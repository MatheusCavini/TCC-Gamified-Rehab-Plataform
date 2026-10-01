# Noraxon EMG ROS 2 adapter

This package publishes live Noraxon EMG data using the platform device contract:

- `/device/noraxon_emg/raw` (`std_msgs/Float32MultiArray`): raw EMG blocks in
  microvolts, channel-major, with dimensions `[channel, sample]`.
- `/biosignals/emg/raw`: compatibility mirror of the raw topic.
- `/device/noraxon_emg/raw_schema` (`std_msgs/String`): channel order, exact
  Noraxon IDs, granted sample rate, and units.
- `/device/info`: heartbeat consumed by `hal_device_manager`.
- `/device/noraxon_emg/rms` (`std_msgs/Float32MultiArray`): filtered 100-ms
  RMS values, published by `hal_signal_processing`.

The signal processor applies a streaming second-order Butterworth 20--450 Hz
band-pass filter followed by rolling RMS.  Filter state is preserved across raw
blocks.  RMS values are also included in the EMG entry sent to
`/hal/device_state`.

## Run the Noraxon Acquire stream through Docker on Windows

Noraxon Acquire and its COM SDK stay on Windows.  A lightweight sender reads
SDK transfer batches and sends newline-delimited JSON over TCP to a receiver
inside Docker.  The receiver publishes into the existing ROS graph, so this
approach needs neither native Windows ROS nor DDS discovery across Windows and
Docker.

1. Start Acquire and configure its EMG profile or emulated device.
2. Discover the exact EMG channel IDs exposed by the selected profile, using
   the local SDK Python environment:

   ```powershell
   cd ..\repo_EMG_noraxon\noraxon-sdk
   uv run python -c "from noraxon_sdk.api import list_device_channels; list_device_channels(dll_path=None, use_setup=True, print_table=True, post_skip=True)"
   ```

   Use only IDs containing `type.input.analog.emg`.  The SDK project's current
   `pyproject.toml` requests Python 3.13. Run the sender with the Python
   environment that can import `noraxon_sdk`, `pythoncom`, `comtypes`, and
   `pywin32`.

3. Rebuild the Docker image after adding/updating this receiver and its Compose
   service. From the repository root, stop an old stack, then build once:

   ```powershell
   docker compose -f docker-compose.windows.yml down
   docker build -t rehab-platform:humble .
   ```

4. Start the ROS stack. It includes `noraxon_tcp_receiver`, listening inside
   Docker on port 8765 and published on Windows loopback only:

   ```powershell
   docker compose -f docker-compose.windows.yml up
   ```

5. First test the Windows-to-Docker TCP link without Acquire or the SDK. In a
   Windows PowerShell terminal, run the sender's synthetic mode:

   ```powershell
   python src\noraxon_emg\scripts\noraxon_tcp_sender.py --simulate --host 127.0.0.1 --port 8765
   ```

   Leave it running and verify changing RMS values using the commands below.
   This exercises Windows → Docker TCP → ROS without involving COM.

6. Stop the synthetic sender with Ctrl+C. To send the Acquire stream instead,
   run the SDK sender from the repository root. Replace the example channel ID
   with the actual ID from Acquire. Repeat `--channel-id` once for each
   selected EMG channel:

   ```powershell
   uv run --project ..\repo_EMG_noraxon\noraxon-sdk python src\noraxon_emg\scripts\noraxon_tcp_sender.py --use-setup --channel-id "line.1;type.input.analog.emg;device.player.player.record;" --host 127.0.0.1 --port 8765
   ```

   The sender reports when Acquire is active and batches are being forwarded.
   The Docker receiver publishes the ROS topics listed above. The actual
   sample rate granted by Acquire is included in `raw_schema` and passed to
   the signal processor through the device heartbeat.

The receiver's TCP port is bound to `127.0.0.1` on the Windows host. The sender
and receiver currently have no authentication, so keep this loopback binding
and do not publish the port on external interfaces.

## Verify the Docker path

While the sender and Compose stack are running, use another PowerShell terminal
to inspect the outputs. An interactive `docker compose exec` shell needs the
ROS setup sourced explicitly:

```powershell
docker compose -f docker-compose.windows.yml exec signal_processing bash -lc "source /opt/ros/humble/setup.bash && source /ros2_ws/install/local_setup.bash && ros2 topic echo /device/noraxon_emg/raw --once"
docker compose -f docker-compose.windows.yml exec signal_processing bash -lc "source /opt/ros/humble/setup.bash && source /ros2_ws/install/local_setup.bash && ros2 topic echo /device/noraxon_emg/rms"
docker compose -f docker-compose.windows.yml exec signal_processing bash -lc "source /opt/ros/humble/setup.bash && source /ros2_ws/install/local_setup.bash && ros2 topic echo /hal/device_state"
```

`raw_schema` is published when the Windows SDK establishes the stream. Start
its subscriber before launching the sender if you want to capture that startup
message. To test device registration, inspect `/devices/available` for the
`noraxon_emg` entry.

The signal processor accepts `emg_highpass_hz` (20), `emg_lowpass_hz` (450),
and `emg_rms_window_ms` (100) ROS parameters. For example, start it with
`--ros-args -p emg_rms_window_ms:=200` for a smoother 200-ms envelope.

## Test the ROS path without Acquire

The Docker adapter has an independent synthetic mode. It tests the ROS graph
and processing pipeline without Noraxon hardware, Windows COM, or the TCP
bridge. With the usual stack running, start this in another PowerShell window:

```powershell
docker compose -f docker-compose.windows.yml run --rm --no-deps signal_processing ros2 run noraxon_emg noraxon_emg_node --ros-args -p simulate:=true
```

Synthetic EMG is noise with a changing activation envelope, so the RMS values
should vary over time. The simulator is distinct from Acquire's emulated
device/profile; that uses the TCP sender workflow above.
