import rclpy
from rclpy.node import Node
import json
import os

from std_msgs.msg import String, Float32MultiArray
from std_srvs.srv import Trigger
from hal_interfaces.srv import SetString

# =====================================================
# MOCK EXTERNAL LIBRARIES (Delegation Strategy)
# =====================================================
class DummyEMGLibrary:
    @staticmethod
    def process_emg_signal(raw_data_array):
        # Placeholder: e.g., Rectify and smooth the signal, return an envelope peak
        # Here we just return the absolute maximum value in the array block
        return max([abs(x) for x in raw_data_array])

# =====================================================
# SIGNAL PROCESSOR STRATEGIES
# =====================================================
class SignalProcessor:
    def __init__(self, device_info):
        self.device_info = device_info
        self.mins = None
        self.maxs = None
        self.is_calibrating = False  # Tracked per-device

    def _init_bounds(self, data_len):
        self.mins = [float('inf')] * data_len
        self.maxs = [float('-inf')] * data_len

    def process(self, raw_data):
        """Hook for subclasses to apply third-party processing before normalization."""
        return raw_data

    def normalize(self, raw_data):
        # 1. Apply any specific processing (e.g., extracting index 0 for encoder)
        processed_data = self.process(raw_data)
        
        # 2. Ensure data is a list (vectorized)
        data = list(processed_data) if hasattr(processed_data, '__iter__') else [processed_data]
        
        # 3. Lazy initialization of bounds based on input dimension
        if self.mins is None:
            self._init_bounds(len(data))

        # 4. Check this specific device's calibration state
        if self.is_calibrating:
            for i, val in enumerate(data):
                if val < self.mins[i]: self.mins[i] = val
                if val > self.maxs[i]: self.maxs[i] = val
            return [0.0] * len(data)

        # 5. Vectorized normalization
        norm_data = []
        for i, val in enumerate(data):
            denom = (self.maxs[i] - self.mins[i])
            norm = (val - self.mins[i]) / denom if denom != 0 else 0.5
            norm_data.append(max(0.0, min(1.0, norm)))
        
        return norm_data[0] if len(norm_data) == 1 else norm_data

class EncoderProcessor(SignalProcessor):
    def process(self, raw_data):
        # raw_data[0] is position, raw_data[1] is velocity
        position = raw_data[0] 
        return position

class EMGProcessor(SignalProcessor):
    def process(self, raw_data):
        # Delegate to external biosignal module
        activation_intensity = DummyEMGLibrary.process_emg_signal(raw_data)
        return activation_intensity

# =====================================================
# MAIN ROS2 NODE
# =====================================================
class SignalProcessingNode(Node):
    def __init__(self):
        super().__init__('signal_processing_node')
        
        self.processors = {}
        self.profile_path = os.path.expanduser('~/thesis_ws/calibration_profile.json')

        # Unified subscription to /devices/available for registry
        self.create_subscription(String, '/devices/available', self.registry_callback, 10)
        
        # Dictionary to hold dynamic subscribers
        self.subs = {}

        self.state_pub = self.create_publisher(String, '/hal/device_state', 10)
        self.calib_state_pub = self.create_publisher(String, '/calibration/state', 10)
        self.calib_profile_pub = self.create_publisher(String, '/calibration/profile', 10)

        # --- Services ---
        # Toggle uses SetString to specify which device_id to calibrate
        self.create_service(SetString, '/calibration/toggle_device', self.handle_device_calibration)
        self.create_service(Trigger, '/calibration/save_profile', self.handle_calib_save)
        self.create_service(Trigger, '/calibration/load_profile', self.handle_calib_load)

        # --- Timers ---
        self.create_timer(1.0, self.publish_calibration_state)

        self.get_logger().info("HAL Signal Processing Node Initialized.")

    # --------------------------------------------------
    # CALLBACKS: Registry & Topics
    # --------------------------------------------------
    def registry_callback(self, msg):
        devices = json.loads(msg.data)
        current_ids = {d['device_id'] for d in devices}

        # 1. Add new devices dynamically
        for dev in devices:
            d_id = dev['device_id']
            if d_id not in self.processors:
                # Instantiate Processor based on type
                self.processors[d_id] = self._create_processor(dev)
                
                # Dynamically create subscription to topic: /device/{device_id}/raw
                topic = f"/device/{d_id}/raw"
                self.subs[d_id] = self.create_subscription(
                    Float32MultiArray, topic, 
                    lambda msg, d=d_id: self.generic_callback(d, msg), 10
                )
                self.get_logger().info(f"Subscribed to dynamic topic: {topic}")

        # 2. Cleanup removed devices
        for d_id in list(self.processors.keys()):
            if d_id not in current_ids:
                self.destroy_subscription(self.subs.pop(d_id))
                del self.processors[d_id]

    def _create_processor(self, dev_info):
        """Factory to create processors."""
        if dev_info['type'] == 'encoder': return EncoderProcessor(dev_info)
        if dev_info['type'] == 'emg': return EMGProcessor(dev_info)
        return SignalProcessor(dev_info)

    def generic_callback(self, device_id, msg):
        """Single callback for all devices."""
        processor = self.processors.get(device_id)
        if not processor: return

        # Normalization handles internal calibration logic autonomously
        norm_val = processor.normalize(msg.data)
        
        # Only broadcast to game layer if this specific device is NOT calibrating
        if not processor.is_calibrating:
            state_msg = {
                "device_id": device_id,
                "normalized_value": norm_val,
                "unit": "normalized_intensity", 
                "type": processor.device_info.get('type') 
            }
            self.state_pub.publish(String(data=json.dumps(state_msg)))

    # --------------------------------------------------
    # SERVICES: Calibration Management
    # --------------------------------------------------
    def handle_device_calibration(self, request, response):
        """Toggles calibration for a SPECIFIC device_id passed in the request."""
        target_device_id = request.data
        
        if target_device_id not in self.processors:
            response.success = False
            response.message = f"Device '{target_device_id}' is not active or registered."
            return response
            
        processor = self.processors[target_device_id]
        processor.is_calibrating = not processor.is_calibrating
        
        if processor.is_calibrating:
            # Reset only this device's bounds for fresh recording
            if processor.mins is not None:
                processor._init_bounds(len(processor.mins))
            response.message = f"Calibration STARTED for device: {target_device_id}."
        else:
            response.message = f"Calibration STOPPED for device: {target_device_id}. Limits locked."
            
        response.success = True
        self.get_logger().info(response.message)
        return response

    def handle_calib_save(self, request, response):
        """Saves current memory limits (arrays) to a persistent JSON file."""
        profile = {}
        for dev_id, proc in self.processors.items():
            # Only save if the device has received data and generated bounds
            if proc.mins is not None:
                profile[dev_id] = {
                    "mins": proc.mins, 
                    "maxs": proc.maxs,
                    "type": proc.device_info.get('type')
                }
            
        try:
            with open(self.profile_path, 'w') as f:
                json.dump(profile, f)
            
            self.calib_profile_pub.publish(String(data=json.dumps(profile)))
            response.success = True
            response.message = f"Profile saved for {len(profile)} devices."
        except Exception as e:
            response.success = False
            response.message = f"Failed to save profile: {str(e)}"
        return response

    def handle_calib_load(self, request, response):
        """Loads array limits from JSON file into the active processors."""
        if not os.path.exists(self.profile_path):
            response.success = False
            response.message = "No profile found to load."
            return response
            
        try:
            with open(self.profile_path, 'r') as f:
                profile = json.load(f)
                
            loaded_count = 0
            for dev_id, limits in profile.items():
                if dev_id in self.processors:
                    self.processors[dev_id].mins = limits["mins"]
                    self.processors[dev_id].maxs = limits["maxs"]
                    loaded_count += 1
                    
            response.success = True
            response.message = f"Calibration profile loaded for {loaded_count} active devices."
            
            self.calib_profile_pub.publish(String(data=json.dumps(profile)))
        except Exception as e:
            response.success = False
            response.message = f"Failed to load profile: {str(e)}"
            
        return response

    def publish_calibration_state(self):
        """Publishes a dictionary mapping device_ids to their boolean calibration state."""
        state_msg = {dev_id: proc.is_calibrating for dev_id, proc in self.processors.items()}
        self.calib_state_pub.publish(String(data=json.dumps(state_msg)))

def main(args=None):
    rclpy.init(args=args)
    node = SignalProcessingNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()