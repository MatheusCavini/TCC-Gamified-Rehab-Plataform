# Pulmonary device ROS 2 adapter

Reads a generic **single-value** serial sensor (pressure / flow / effort) and
publishes it using the platform device contract:

- `/device/pulmonary_device/raw` (`std_msgs/Float32MultiArray`): `[value]`.
- `/device/info` (`std_msgs/String`): JSON heartbeat consumed by
  `hal_device_manager`:
  `{"device_id": "pulmonary_device", "type": "pulmonary", "capabilities": ["intensity"], "units": ["raw_adc"]}`.
  The heartbeat is only sent while samples are arriving, so unplugging the
  sensor removes it from `/devices/available` after the manager's 3 s timeout.

`hal_signal_processing` needs no changes: it subscribes to
`/device/<id>/raw` for any unknown `type`, applies the same min/max
calibration as every other device and publishes
`{"device_id", "normalized_value", "unit", "type"}` on `/hal/device_state`.

## Serial protocol

One sample per line, newline terminated.  The parser takes the first number it
finds, so `512`, `512.7` and `P: 512` all work (`-p field_index:=N` picks
another number on the line, `-1` = last).  Lines without a number are ignored.
Samples that arrive faster than `max_publish_rate_hz` (default 50) are averaged.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `device_id` | `pulmonary_device` | Id used in topics and by Unity |
| `serial_port` | `/dev/ttyUSB0` | Serial port |
| `baud_rate` | `115200` | Baud rate |
| `field_index` | `0` | Which number on the line is the sample |
| `unit` | `raw_adc` | Unit advertised in the heartbeat |
| `max_publish_rate_hz` | `50.0` | Publish rate cap (averages in between) |
| `stale_timeout_s` | `1.5` | Stop advertising after this long without samples |
| `open_settle_s` | `2.0` | Wait after opening the port (boards that reset) |
| `simulate` | `false` | Publish a synthetic breathing waveform, no hardware |
| `simulation_breath_period_s` | `8.0` | Period of the simulated breath |

## Run

```bash
ros2 run pulmonary_device pulmonary_device_node --ros-args -p serial_port:=/dev/ttyUSB0 -p baud_rate:=115200
# no hardware:
ros2 run pulmonary_device pulmonary_device_node --ros-args -p simulate:=true
```

## Calibration

Normalisation is min/max, as for every device.  Until the device has been
calibrated (Calibrate button in the Unity menu, or *Load Calibration Profile*),
the HAL has no bounds and the normalised value is stuck at 1.0.  Calibrate by
pressing **Calibrate**, doing one full-effort blow and one full relaxation,
then pressing **Stop (Lock ROM)**.
