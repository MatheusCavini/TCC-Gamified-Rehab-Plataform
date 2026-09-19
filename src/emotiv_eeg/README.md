# EMOTIV EEG node

`emotiv_eeg_node` connects to a locally running EMOTIV Cortex service and
registers `emotiv_eeg` with the platform's device manager only while a headset
session is active. It requests Cortex's `met`, `com`, and `sys` streams; with
`enable_raw_eeg:=true`, it additionally requests licensed `eeg` samples.

Install `websocket-client` (`sudo apt install python3-websocket`) and start
EMOTIV Launcher/Cortex before starting the node. Credentials are ROS parameters,
not source-code constants:

```bash
ros2 run emotiv_eeg emotiv_eeg_node --ros-args \
  -p client_id:=YOUR_CLIENT_ID -p client_secret:=YOUR_CLIENT_SECRET
```

| Topic | Type | Meaning |
| --- | --- | --- |
| `/device/info` | `std_msgs/String` | HAL heartbeat and capabilities |
| `/device/emotiv_eeg/raw` | `Float32MultiArray` | `[focus, engagement, stress]`; inactive values are `NaN` |
| `/device/emotiv_eeg/metrics` | `String` JSON | Canonical and original Cortex performance metrics |
| `/device/emotiv_eeg/mental_command` | `String` JSON | `action`, `power`, and `is_neutral` |
| `/device/emotiv_eeg/calibration/request` | `String` JSON | Cortex mental-command training request |
| `/device/emotiv_eeg/calibration/state` | `String` JSON | Training progress and Cortex system events |
| `/device/emotiv_eeg/eeg` | `Float32MultiArray` | Optional raw EEG samples |

For mental-command calibration, train both `neutral` and the desired action.
Call the central service with `start`, wait for the Cortex system event, then
send `accept` (or `reject`):

```bash
ros2 service call /calibration/toggle_device hal_interfaces/srv/SetString \
  "{data: '{\"device_id\":\"emotiv_eeg\",\"action\":\"neutral\",\"status\":\"start\"}'}"
```

`accept`, `reject`, `reset`, and `erase` are also routed to Cortex. The
signal-processing node remains the single public calibration entry point.
