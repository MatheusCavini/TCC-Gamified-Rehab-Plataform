# Guidao controller modes

The controller supports two modes and starts in `assisted` mode to preserve
the existing behavior.

- `assisted`: the existing PID drives the handlebar toward the computed
  setpoint.
- `resistive`: a bounded torque proportional to the angular error drives
  away from the setpoint. The setpoint and Stanley trajectory are unchanged.

## Selecting a mode

Publish a `std_msgs/msg/String` on `/control/mode` with either `assisted` or
`resistive`. The game can publish this when the player chooses a mode. The
controller also accepts an optional `control_mode` field in its existing
`/game/state` JSON message, for example:

```json
{"lateral_error": 0.0, "heading_error_deg": 0.0, "speed": 1.0, "control_mode": "resistive"}
```

The selected mode is included in `/control/command` as `control_mode`. Invalid
mode values are ignored; valid values are `assisted` and `resistive`.

## Resistive settings

ROS parameters allow tuning the resistance after conservative hardware
calibration:

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `resistive_gain` | `30.0` | Output units per radian beyond the deadband |
| `resistive_output_limit` | `35.0` | Absolute output cap in resistive mode |
| `resistive_deadband_rad` | `0.01` | Error around the setpoint with no resistive torque |
| `resistive_slew_rate` | `100.0` | Maximum output change per second |

Resistance is clipped to both `resistive_output_limit` and the existing global
output limits, then receives the existing EEG/EMG modulation if enabled. The
current output cap is intentionally below the assisted mode's global cap;
calibrate the sign and magnitude against the physical device before use with a
participant.
