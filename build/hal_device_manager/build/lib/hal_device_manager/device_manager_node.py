import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import json
import time

class DeviceManagerNode(Node):
    def __init__(self):
        super().__init__('device_manager_node')
        
        # Subscriptions & Publishers
        self.info_sub = self.create_subscription(String, '/device/info', self.info_callback, 10)
        self.avail_pub = self.create_publisher(String, '/devices/available', 10)
        
        # State Dictionary to track connected hardware
        self.active_devices = {}
        self.timeout_seconds = 3.0 # If no heartbeat in 3s, consider device disconnected
        
        # Timer to publish the available devices list and prune dead ones (2 Hz)
        self.timer = self.create_timer(0.5, self.publish_available_devices)
        self.get_logger().info("Device Manager Node Started. Listening for hardware heartbeats...")

    def info_callback(self, msg):
        try:
            device_info = json.loads(msg.data)
            device_id = device_info.get('device_id')
            
            if device_id:
                # Update the last_seen timestamp
                device_info['last_seen'] = time.time()
                
                # Log if it's a newly discovered device
                if device_id not in self.active_devices:
                    self.get_logger().info(f"New device discovered & registered: {device_id}")
                    
                self.active_devices[device_id] = device_info
                
        except json.JSONDecodeError:
            self.get_logger().warn("Received malformed JSON on /device/info")

    def publish_available_devices(self):
        current_time = time.time()
        active_list = []
        keys_to_remove = []
        
        # Check for timeouts
        for dev_id, info in self.active_devices.items():
            if current_time - info['last_seen'] > self.timeout_seconds:
                keys_to_remove.append(dev_id)
            else:
                # Strip 'last_seen' before publishing to keep the payload clean
                clean_info = {k: v for k, v in info.items() if k != 'last_seen'}
                active_list.append(clean_info)
                
        # Prune disconnected devices
        for dev_id in keys_to_remove:
            del self.active_devices[dev_id]
            self.get_logger().warn(f"Device disconnected & deregistered: {dev_id}")
            
        # Publish the current state as a JSON array
        msg = String()
        msg.data = json.dumps(active_list)
        self.avail_pub.publish(msg)

def main(args=None):
    rclpy.init(args=args)
    node = DeviceManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()