#include <Arduino.h>
#include <Wire.h>
#include <micro_ros_arduino.h> // Ensure you are using PlatformIO
#include <rcl/rcl.h>
#include <rcl/error_handling.h>
#include <rclc/rclc.h>
#include <rclc/executor.h>
#include <std_msgs/msg/float32_multi_array.h>
#include <std_msgs/msg/string.h>

#define SDA_PIN 21
#define SCL_PIN 22
#define AS5600_ADDR 0x36
#define LED_PIN 2

// micro-ROS objects
rcl_publisher_t pub_raw;
rcl_publisher_t pub_info;
std_msgs__msg__Float32MultiArray msg_raw;
std_msgs__msg__String msg_info;
rclc_executor_t executor;
rclc_support_t support;
rcl_allocator_t allocator;
rcl_node_t node;
rcl_timer_t timer_fast;
rcl_timer_t timer_slow;

// State variables for velocity computation
float last_angle = 0.0f;
unsigned long last_time = 0;

// Device Info JSON payload
const char* device_info_json = "{\"device_id\":\"as5600_encoder\",\"type\":\"encoder\",\"capabilities\":[\"position\",\"velocity\"],\"units\":[\"deg\",\"deg/s\"]}";

// Error Handling Macros
#define RCCHECK(fn) { rcl_ret_t rc = fn; if (rc != RCL_RET_OK) { error_loop(); } }
#define RCSOFTCHECK(fn) { rcl_ret_t rc = fn; if (rc != RCL_RET_OK) {} }

void error_loop() {
  while (1) {
    digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    delay(100);
  }
}

float readAngleDegrees() {
  Wire.beginTransmission(AS5600_ADDR);
  Wire.write(0x0E); // RAW ANGLE high byte register
  Wire.endTransmission(false);
  Wire.requestFrom(AS5600_ADDR, 2);
  if (Wire.available() == 2) {
    uint16_t raw = (Wire.read() << 8) | Wire.read();
    raw &= 0x0FFF; // 12-bit value
    return (raw / 4096.0f) * 360.0f;
  }
  return -1.0f;
}

// Fast Timer Callback: Reads sensor, computes velocity, publishes raw data (50 Hz)
void timer_fast_callback(rcl_timer_t* timer, int64_t last_call_time) {
  RCLC_UNUSED(last_call_time);
  if (timer != NULL) {
    float current_angle = readAngleDegrees();
    
    if (current_angle < 0.0f) return; // Sensor read failed

    unsigned long current_time = millis();
    float delta_time = (current_time - last_time) / 1000.0f; // convert to seconds
    float delta_angle = current_angle - last_angle;

    // Handle 0-360 degree wrap-around (shortest path calculation)
    if (delta_angle > 180.0f) delta_angle -= 360.0f;
    if (delta_angle < -180.0f) delta_angle += 360.0f;

    float velocity = 0.0f;
    if (delta_time > 0.0f) {
      velocity = delta_angle / delta_time;
    }

    // Populate MultiArray message [0] = Position, [1] = Velocity
    msg_raw.data.data[0] = current_angle;
    msg_raw.data.data[1] = velocity;

    RCSOFTCHECK(rcl_publish(&pub_raw, &msg_raw, NULL));

    last_angle = current_angle;
    last_time = current_time;
  }
}

// Slow Timer Callback: Publishes device capabilities heartbeat (1 Hz)
void timer_slow_callback(rcl_timer_t* timer, int64_t last_call_time) {
  RCLC_UNUSED(last_call_time);
  if (timer != NULL) {
    RCSOFTCHECK(rcl_publish(&pub_info, &msg_info, NULL));
    digitalWrite(LED_PIN, !digitalRead(LED_PIN)); // Blink to show alive
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000); // Set I2C to fast mode for lower latency

  // 1. Initialize Serial Transport
  Serial.begin(115200);
  set_microros_transports();
  delay(2000);

  allocator = rcl_get_default_allocator();
  RCCHECK(rclc_support_init(&support, 0, NULL, &allocator));
  RCCHECK(rclc_node_init_default(&node, "as5600_encoder_node", "", &support));

  // 2. Initialize Publishers
  RCCHECK(rclc_publisher_init_default(
    &pub_raw, &node, 
    ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, Float32MultiArray), 
    "/device/as5600_encoder/raw"
  ));

  RCCHECK(rclc_publisher_init_default(
    &pub_info, &node, 
    ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, String), 
    "/device/info"
  ));

  // 3. Allocate Memory for Messages
  msg_raw.data.capacity = 2;
  msg_raw.data.size = 2;
  msg_raw.data.data = (float*) malloc(msg_raw.data.capacity * sizeof(float));

  msg_info.data.capacity = strlen(device_info_json) + 1;
  msg_info.data.size = strlen(device_info_json);
  msg_info.data.data = (char*) malloc(msg_info.data.capacity * sizeof(char));
  strcpy(msg_info.data.data, device_info_json);

  // 4. Initialize Timers
  // Fast timer for sensor data: 20ms = 50Hz (Standard for reactive rehab games)
  RCCHECK(rclc_timer_init_default(&timer_fast, &support, RCL_MS_TO_NS(20), timer_fast_callback));
  // Slow timer for heartbeat: 1000ms = 1Hz
  RCCHECK(rclc_timer_init_default(&timer_slow, &support, RCL_MS_TO_NS(1000), timer_slow_callback));

  // 5. Initialize Executor
  RCCHECK(rclc_executor_init(&executor, &support.context, 2, &allocator)); // 2 handles for 2 timers
  RCCHECK(rclc_executor_add_timer(&executor, &timer_fast));
  RCCHECK(rclc_executor_add_timer(&executor, &timer_slow));

  // Init state
  last_angle = readAngleDegrees();
  last_time = millis();
}

void loop() {
  // Spin executor to handle timers and transmissions
  RCSOFTCHECK(rclc_executor_spin_some(&executor, RCL_MS_TO_NS(10)));
}