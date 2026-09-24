# Noraxon EMG ROS 2 adapter

`noraxon_emg_node` owns the Noraxon Acquire COM connection and publishes raw,
channel-major EMG blocks.  It follows the platform device-discovery contract:

- `/device/noraxon_emg/raw` (`std_msgs/Float32MultiArray`): raw EMG in uV;
  dimensions are `[channel, sample]` and data are channel-major.
- `/biosignals/emg/raw`: optional compatibility mirror of the raw topic.
- `/device/noraxon_emg/raw_schema` (`std_msgs/String`): channel order, exact
  Noraxon IDs, granted sample rate, and units.
- `/device/info`: heartbeat consumed by `hal_device_manager`.
- `/device/noraxon_emg/rms` (`std_msgs/Float32MultiArray`): one filtered,
  100-ms RMS value per channel, published by `hal_signal_processing`.

The signal-processing node applies a streaming second-order Butterworth
20--450 Hz band-pass filter followed by a 100-ms rolling RMS.  The filter
preserves state across raw blocks; it does not reduce each block to a peak.
The RMS values are also included in the EMG entry sent to `/hal/device_state`.

## Native Noraxon prerequisites

Run this node in a **native Windows ROS 2 environment**.  The Noraxon SDK uses
Windows COM and therefore cannot communicate with Noraxon Acquire from the
Linux Docker/WSL stack in this repository.

1. Install Noraxon Acquire and make a device/profile available.  Start it and
   configure the EMG sensors/profile before launching ROS.
2. Install the local SDK at `..\\noraxon-sdk` and its COM dependencies in the
   same Python environment used by `ros2` (`comtypes`, `pywin32`, and
   `noraxon_sdk` must import from that interpreter).  The SDK project's current
   `pyproject.toml` requests Python 3.13, so the ROS 2 distribution must use a
   compatible Python, or the SDK project must be made compatible with ROS's
   Python before installing it.
3. Discover the exact EMG channel IDs exposed by the selected Acquire profile:

   ```powershell
   cd ..\noraxon-sdk
   uv run python -c "from noraxon_sdk.api import list_device_channels; list_device_channels(dll_path=None, use_setup=True, print_table=True, post_skip=True)"
   ```

   Copy only IDs containing `type.input.analog.emg` into the node's
   `channel_ids` parameter.  `use_setup:=true` may also be passed to the node
   to choose the Acquire profile interactively.

## Build and run

Build the three participating packages, then source the generated ROS setup
script appropriate to the terminal/shell in use:

```powershell
colcon build --packages-select noraxon_emg hal_device_manager hal_signal_processing
.\install\local_setup.ps1
```

Run each of these in a separate sourced terminal:

```powershell
ros2 run hal_device_manager device_manager_node
ros2 run hal_signal_processing signal_processing_node
ros2 run noraxon_emg noraxon_emg_node --ros-args -p use_setup:=true -p channel_ids:="['line.1;type.input.analog.emg;device.player.player.record;']"
```

The last command is only an example ID; replace it with IDs discovered on the
actual machine.  `desired_frequency_hz` is a request.  The node advertises the
actual rate granted by Acquire in `/device/noraxon_emg/raw_schema` and uses it
for filtering.

The signal processor accepts `emg_highpass_hz` (20), `emg_lowpass_hz` (450),
and `emg_rms_window_ms` (100) ROS parameters.  For example, start it with
`--ros-args -p emg_rms_window_ms:=200` for a smoother 200-ms envelope.

## Test without hardware

The node has an independent synthetic mode, so this test needs no Noraxon
installation or COM registration:

```powershell
ros2 run hal_device_manager device_manager_node
ros2 run hal_signal_processing signal_processing_node
ros2 run noraxon_emg noraxon_emg_node --ros-args -p simulate:=true
```

In another sourced terminal, verify the complete chain:

```powershell
ros2 topic echo /devices/available
ros2 topic echo /device/noraxon_emg/raw_schema --once
ros2 topic echo /device/noraxon_emg/rms
ros2 topic echo /hal/device_state
```

Synthetic EMG is noise with a changing muscle-activation envelope; RMS should
vary over time.  If Noraxon Acquire's own emulated device/profile is installed,
use it instead by leaving `simulate:=false`, set `use_setup:=true`, and select
its EMG channels exactly as in the discovery step.  The local SDK notes that
this emulator can take roughly half a second after activation before samples
appear.
