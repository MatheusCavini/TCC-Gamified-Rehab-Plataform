#!/usr/bin/env python3
"""
ROS 2 Consolidated Hardware Interface Node for "Guidao" Rehabilitation Device.

Single process owner for the Arduino serial port (/dev/ttyACM0).
Dynamically supports both:
  1. Legacy Firmware (New_controlv5.ino): Read-only streaming (start flag '2'),
     9 CSV fields, no serial command replies required.
  2. Active Controller Firmware (Claude_TCC_Arduino.ino): Bidirectional lockstep
     (start flag '1'), 8 CSV fields, requires continuous signed PWM replies.
"""

import json
import threading
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, Float32MultiArray, String
import serial

ENCODER_DEVICE_ID = "encoder"
LC_PREFIX = "load_cell_guidao_"

# Configuration Defaults
LEGACY_ARDUINO_CODE = False
DESIRED_LOAD_CELLS = 6

# Theta conversion: encoderPos * (180 / 1000) = 0.18 degrees
TICKS_TO_DEG = 0.18

STOP_LINE = b'X\n'


class GuidaoHardwareNode(Node):
    def __init__(self):
        super().__init__('guidao_hardware_node')

        # --- Parameters ---
        self.declare_parameter('serial_port', '/dev/ttyACM0')
        self.declare_parameter('baud_rate', 2000000)
        self.declare_parameter('info_period_s', 1.0)
        self.declare_parameter('command_timeout_s', 0.2)
        self.declare_parameter('output_min', -255.0)
        self.declare_parameter('output_max', 255.0)
        
        # Feature Flags
        self.declare_parameter('num_load_cells', DESIRED_LOAD_CELLS)           # 0 disables load cell topics entirely
        self.declare_parameter('legacy_firmware_mode', LEGACY_ARDUINO_CODE)  # True = read-only, 9 fields, flag '2'

        self.serial_port_name = self.get_parameter('serial_port').value
        self.baud_rate = self.get_parameter('baud_rate').value
        info_period = self.get_parameter('info_period_s').value
        self.command_timeout_s = self.get_parameter('command_timeout_s').value
        self.output_min = self.get_parameter('output_min').value
        self.output_max = self.get_parameter('output_max').value
        
        self.num_load_cells = max(0, min(6, self.get_parameter('num_load_cells').value))
        self.legacy_mode = self.get_parameter('legacy_firmware_mode').value

        # --- Firmware Protocol Configuration ---
        self.start_byte = b'2' if self.legacy_mode else b'1'
        self.expected_fields = 9 if self.legacy_mode else 8
        self.s1_index = 3 if self.legacy_mode else 2

        # --- Device Info Discovery Heartbeats Publisher ---
        self.pub_info = self.create_publisher(String, '/device/info', 10)
        
        # --- 1. Encoder Publisher & Heartbeat ---
        self.pub_encoder_raw = self.create_publisher(
            Float32MultiArray, f'/device/{ENCODER_DEVICE_ID}/raw', 10
        )
        self.encoder_info_json = json.dumps({
            "device_id": ENCODER_DEVICE_ID,
            "type": "encoder",
            "capabilities": ["position", "velocity"],
            "units": ["deg", "deg/s"],
        })

        # --- 2. Dynamic Load Cell Publishers & Heartbeats (0 to 6 channels) ---
        self.lc_device_ids = [f"{LC_PREFIX}{i + 1}" for i in range(self.num_load_cells)]
        self.pub_load_cells = {}
        self.lc_info_json = {}

        for lc_id in self.lc_device_ids:
            self.pub_load_cells[lc_id] = self.create_publisher(
                Float32MultiArray, f'/device/{lc_id}/raw', 10
            )
            self.lc_info_json[lc_id] = json.dumps({
                "device_id": lc_id,
                "type": "load_cell",
                "capabilities": ["intensity"],
                "units": ["raw_adc"],
            })

        self.create_timer(info_period, self._publish_info)

        # --- Subscriber: Control Command (Only enabled if NOT in legacy mode) ---
        self._cmd_lock = threading.Lock()
        self._latest_command = 0.0
        self._latest_command_time = None
        if not self.legacy_mode:
            self.create_subscription(
                Float32, '/device/actuator_command', self._on_command, 10
            )

        # --- Encoder State ---
        self.last_angle = None
        self.last_time = None

        # --- Serial Connection & Threading ---
        self.ser = None
        self._stop_event = threading.Event()
        self._connect_serial()

        self.reader_thread = threading.Thread(target=self._serial_loop, daemon=True)
        self.reader_thread.start()

        mode_str = "Legacy Read-Only" if self.legacy_mode else "Active Lockstep"
        self.get_logger().info(
            f"Guidao Hardware Node running [{mode_str}] on {self.serial_port_name} @ {self.baud_rate} baud. "
            f"Active Load Cells: {self.num_load_cells}"
        )

    # ------------------------------------------------------------------
    # ROS Callbacks
    # ------------------------------------------------------------------
    def _on_command(self, msg: Float32):
        """Store incoming PWM command from controller."""
        value = max(self.output_min, min(self.output_max, float(msg.data)))
        with self._cmd_lock:
            self._latest_command = value
            self._latest_command_time = time.monotonic()

    def _publish_info(self):
        """Publish heartbeats for encoder and active load cell devices."""
        self.pub_info.publish(String(data=self.encoder_info_json))
        for lc_id in self.lc_device_ids:
            self.pub_info.publish(String(data=self.lc_info_json[lc_id]))

    # ------------------------------------------------------------------
    # Serial Communication Loop
    # ------------------------------------------------------------------
    def _connect_serial(self):
        try:
            self.ser = serial.Serial(self.serial_port_name, self.baud_rate, timeout=1.0)
            time.sleep(2.0)  # Allow Arduino reset after opening port
            self.ser.reset_input_buffer()
            # Send flag ('2' for legacy read-only, '1' for active lockstep)
            self.ser.write(self.start_byte)
            self.get_logger().info(f"Connected to Arduino on {self.serial_port_name}.")
        except serial.SerialException as e:
            self.get_logger().error(f"Serial connection error on {self.serial_port_name}: {e}")
            self.ser = None

    def _serial_loop(self):
        while not self._stop_event.is_set():
            if self.ser is None or not self.ser.is_open:
                time.sleep(1.0)
                self._connect_serial()
                continue

            try:
                raw_line = self.ser.readline()
            except serial.SerialException as e:
                self.get_logger().warn(f"Serial read error: {e}. Reconnecting...")
                self._safe_close_serial()
                continue

            line = raw_line.decode('utf-8', errors='ignore').strip()
            if line:
                self._handle_line(line)

            # ONLY send replies back if running in active lockstep mode
            if not self.legacy_mode:
                self._write_command_reply()

    def _handle_line(self, line: str):
        fields = line.split(',')
        if len(fields) != self.expected_fields:
            return  # Filter malformed or mismatched CSV packet formats

        try:
            encoder_ticks = int(float(fields[1]))
            if self.num_load_cells > 0:
                loadcells = [float(x) for x in fields[self.s1_index : self.s1_index + self.num_load_cells]]
        except ValueError:
            return

        # --- 1. Compute & Publish Encoder Dynamics ---
        angle_deg = encoder_ticks * TICKS_TO_DEG
        now = time.monotonic()

        if self.last_angle is None:
            velocity_deg_s = 0.0
        else:
            dt = now - self.last_time
            velocity_deg_s = (angle_deg - self.last_angle) / dt if dt > 0 else 0.0

        self.last_angle = angle_deg
        self.last_time = now

        self.pub_encoder_raw.publish(
            Float32MultiArray(data=[float(angle_deg), float(velocity_deg_s)]))

        # --- 2. Publish Active Load Cell Topics ---
        for i, lc_id in enumerate(self.lc_device_ids):
            out_msg = Float32MultiArray()
            out_msg.data = [loadcells[i]]
            self.pub_load_cells[lc_id].publish(out_msg)

    def _write_command_reply(self):
        """Send command reply to Arduino or 0.0 if command is stale (Active Mode Only)."""
        with self._cmd_lock:
            value = self._latest_command
            cmd_time = self._latest_command_time

        is_stale = (cmd_time is None) or (time.monotonic() - cmd_time > self.command_timeout_s)
        value_to_send = 0.0 if is_stale else value

        try:
            self.ser.write(f"{value_to_send:.3f}\n".encode())
        except serial.SerialException as e:
            self.get_logger().warn(f"Serial write error: {e}. Resetting port...")
            self._safe_close_serial()

    def _safe_close_serial(self):
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    # ------------------------------------------------------------------
    # Clean Shutdown
    # ------------------------------------------------------------------
    def destroy_node(self):
        self._stop_event.set()
        if self.reader_thread.is_alive():
            self.reader_thread.join(timeout=2.0)
        try:
            if self.ser is not None and self.ser.is_open:
                self.ser.write(STOP_LINE)  # Send 'X\n' or reset flag
                time.sleep(0.05)
        except Exception:
            pass
        self._safe_close_serial()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = GuidaoHardwareNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()