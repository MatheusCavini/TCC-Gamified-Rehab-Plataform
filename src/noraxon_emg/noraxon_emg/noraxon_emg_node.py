#!/usr/bin/env python3
"""Publish live Noraxon EMG blocks using the platform hardware contract.

The Noraxon Acquire SDK is a Windows COM API.  COM objects are created and
used only inside the worker thread, which also owns activation/deactivation.
This is important: moving individual SDK calls into ROS callbacks causes COM
thread-affinity errors and can leave the Noraxon device active on shutdown.
"""

import json
import math
import random
import threading
from collections import deque

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray, MultiArrayDimension, String


DEFAULT_CHANNEL_IDS = [
    'line.1;type.input.analog.emg;device.player.player.record;',
    'line.2;type.input.analog.emg;device.player.player.record;',
    'line.3;type.input.analog.emg;device.player.player.record;',
    'line.4;type.input.analog.emg;device.player.player.record;',
]


class NoraxonEmgNode(Node):
    """Read Noraxon Acquire transfer blocks and publish channel-major EMG."""

    def __init__(self):
        super().__init__('noraxon_emg_node')

        self.declare_parameter('device_id', 'noraxon_emg')
        self.declare_parameter('channel_ids', DEFAULT_CHANNEL_IDS)
        self.declare_parameter('desired_frequency_hz', 2000.0)
        self.declare_parameter('transfer_interval_s', 0.005)
        self.declare_parameter('dll_path', '')
        self.declare_parameter('use_setup', False)
        self.declare_parameter('reconnect_period_s', 3.0)
        self.declare_parameter('info_period_s', 1.0)
        self.declare_parameter('qos_depth', 100)
        # This is deliberately built in, so the whole ROS graph is testable
        # without Windows COM, Noraxon Acquire, or physical sensors.
        self.declare_parameter('simulate', False)
        self.declare_parameter('simulation_channel_count', 4)
        self.declare_parameter('simulation_sample_rate_hz', 1000.0)
        self.declare_parameter('simulation_block_period_s', 0.02)
        self.declare_parameter('publish_biosignals_alias', True)

        self.device_id = self.get_parameter('device_id').value
        self.channel_ids = list(self.get_parameter('channel_ids').value)
        self.desired_frequency_hz = float(
            self.get_parameter('desired_frequency_hz').value)
        self.transfer_interval_s = float(
            self.get_parameter('transfer_interval_s').value)
        self.dll_path = self.get_parameter('dll_path').value or None
        self.use_setup = bool(self.get_parameter('use_setup').value)
        self.reconnect_period_s = float(
            self.get_parameter('reconnect_period_s').value)
        self.simulate = bool(self.get_parameter('simulate').value)
        self.simulation_channel_count = int(
            self.get_parameter('simulation_channel_count').value)
        self.simulation_sample_rate_hz = float(
            self.get_parameter('simulation_sample_rate_hz').value)
        self.simulation_block_period_s = float(
            self.get_parameter('simulation_block_period_s').value)

        qos_depth = int(self.get_parameter('qos_depth').value)
        prefix = f'/device/{self.device_id}'
        self.info_pub = self.create_publisher(String, '/device/info', 10)
        self.raw_pub = self.create_publisher(Float32MultiArray, f'{prefix}/raw', qos_depth)
        self.schema_pub = self.create_publisher(String, f'{prefix}/raw_schema', 10)
        self.connection_pub = self.create_publisher(String, f'{prefix}/connection_state', 10)
        self.raw_alias_pub = None
        if self.get_parameter('publish_biosignals_alias').value:
            self.raw_alias_pub = self.create_publisher(
                Float32MultiArray, '/biosignals/emg/raw', qos_depth)

        self._connected = False
        self._stop_event = threading.Event()
        self._metadata_lock = threading.Lock()
        self._channel_names = []
        self._channel_ids = []
        self._sample_rate_hz = None
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        self.create_timer(self.get_parameter('info_period_s').value, self._publish_info)
        mode = 'synthetic simulation' if self.simulate else 'Noraxon Acquire COM'
        self.get_logger().info(f'Noraxon EMG node started in {mode} mode.')

    def _publish_info(self):
        """Advertise only after a stream is actually available to the HAL."""
        if not self._connected:
            return
        with self._metadata_lock:
            channel_names = list(self._channel_names)
            channel_ids = list(self._channel_ids)
            sample_rate_hz = self._sample_rate_hz
        self.info_pub.publish(String(data=json.dumps({
            'device_id': self.device_id,
            'type': 'emg',
            'capabilities': ['raw_emg', 'rms_emg'],
            'units': ['uV'],
            'raw_topic': f'/device/{self.device_id}/raw',
            'rms_topic': f'/device/{self.device_id}/rms',
            'raw_format': 'channel_major_blocks',
            'channel_names': channel_names,
            'channel_ids': channel_ids,
            'sample_rate_hz': sample_rate_hz,
        })))

    def _publish_connection(self, connected, message=''):
        self.connection_pub.publish(String(data=json.dumps({
            'device_id': self.device_id,
            'connected': connected,
            'message': message,
        })))

    def _set_stream_metadata(self, channel_names, channel_ids, sample_rate_hz):
        with self._metadata_lock:
            self._channel_names = list(channel_names)
            self._channel_ids = list(channel_ids)
            self._sample_rate_hz = float(sample_rate_hz)
        self.schema_pub.publish(String(data=json.dumps({
            'device_id': self.device_id,
            'format': 'Float32MultiArray channel-major blocks',
            'dimensions': ['channel', 'sample'],
            'channel_names': list(channel_names),
            'channel_ids': list(channel_ids),
            'sample_rate_hz': float(sample_rate_hz),
            'units': 'uV',
        })))

    def _publish_block(self, channel_batches):
        """Publish equal-length channel batches as one channel-major message."""
        if not channel_batches:
            return
        sample_count = len(channel_batches[0])
        if sample_count == 0 or any(len(batch) != sample_count for batch in channel_batches):
            return
        message = Float32MultiArray()
        message.layout.dim = [
            MultiArrayDimension(
                label='channel', size=len(channel_batches),
                stride=len(channel_batches) * sample_count),
            MultiArrayDimension(label='sample', size=sample_count, stride=sample_count),
        ]
        message.data = [float(value) for batch in channel_batches for value in batch]
        self.raw_pub.publish(message)
        if self.raw_alias_pub is not None:
            self.raw_alias_pub.publish(message)

    def _run(self):
        while not self._stop_event.is_set():
            try:
                if self.simulate:
                    self._run_simulation()
                else:
                    self._run_noraxon()
            except Exception as error:  # Reconnect makes USB/profile restarts survivable.
                if not self._stop_event.is_set():
                    self.get_logger().warn(f'Noraxon stream unavailable: {error}')
                    self._publish_connection(False, str(error))
            finally:
                self._connected = False
            self._stop_event.wait(self.reconnect_period_s)

    def _run_simulation(self):
        if self.simulation_channel_count < 1 or self.simulation_sample_rate_hz <= 0:
            raise ValueError(
                'simulation_channel_count and simulation_sample_rate_hz must be positive')
        names = [f'Simulated EMG {index + 1}' for index in range(self.simulation_channel_count)]
        ids = [f'simulated_emg_{index + 1}' for index in range(self.simulation_channel_count)]
        self._set_stream_metadata(names, ids, self.simulation_sample_rate_hz)
        self._connected = True
        self._publish_connection(True, 'Synthetic EMG stream active')
        phase = 0.0
        while not self._stop_event.is_set():
            samples = max(
                1, round(self.simulation_sample_rate_hz * self.simulation_block_period_s))
            batches = []
            for channel in range(self.simulation_channel_count):
                # Noise plus a slow activation envelope is intentionally EMG-like,
                # and gives a visible, testable RMS response without a device.
                batch = []
                for sample in range(samples):
                    envelope = 25.0 + 100.0 * max(0.0, math.sin(phase + channel * 0.45))
                    carrier = math.sin(2.0 * math.pi * (75.0 + channel * 20.0) * sample /
                                       self.simulation_sample_rate_hz)
                    batch.append(envelope * carrier + random.gauss(0.0, 8.0))
                    phase += 2.0 * math.pi * 0.8 / self.simulation_sample_rate_hz
                batches.append(batch)
            self._publish_block(batches)
            self._stop_event.wait(self.simulation_block_period_s)

    def _run_noraxon(self):
        """Direct, cancellable version of the SDK's acquisition transfer loop."""
        try:
            import pythoncom
            from noraxon_sdk.core.batches import read_channel_batch
            from noraxon_sdk.core.others import select_and_configure_channels
            from noraxon_sdk.core.probing import probe_active_channels
            from noraxon_sdk.core.setup import activated_device, setup_sdk
        except ImportError as error:
            raise RuntimeError(
                'Noraxon dependencies are unavailable. Install the local noraxon-sdk '
                'environment (including pywin32/comtypes) into the Python used by ROS 2, '
                'or start with -p simulate:=true.') from error

        if not self.channel_ids:
            raise ValueError('channel_ids must contain at least one EMG channel ID')
        pythoncom.CoInitialize()
        try:
            sdk, device_manager, device = setup_sdk(
                use_setup=self.use_setup, dll_path=self.dll_path)
            channels = select_and_configure_channels(
                device=device, sdk=sdk, channel_ids=self.channel_ids,
                desired_frequency=self.desired_frequency_hz)
            if any(channel.component_type != 'analog' or 'analog.emg' not in channel.id
                   for channel in channels):
                raise ValueError('Every channel_ids entry must be an analog EMG channel.')

            with activated_device(device=device, device_manager=device_manager):
                probe_active_channels(channels=channels)
                sample_rates = {
                    float(channel.fs) for channel in channels if channel.fs is not None
                }
                if len(sample_rates) != 1:
                    raise RuntimeError(
                        f'EMG channels have incompatible sampling rates: {sample_rates}')
                sample_rate_hz = sample_rates.pop()
                self._set_stream_metadata(
                    [channel.name for channel in channels],
                    [channel.id for channel in channels], sample_rate_hz)
                self._connected = True
                self._publish_connection(True, 'Noraxon Acquire stream active')
                device.Record()
                pending = [deque() for _ in channels]
                try:
                    while not self._stop_event.is_set():
                        state = device.Transfer()
                        if state in (1, 3):
                            for index, channel in enumerate(channels):
                                batch = read_channel_batch(channel)
                                if batch is not None:
                                    pending[index].extend(float(value) for value in batch)
                            count = min((len(values) for values in pending), default=0)
                            if count:
                                self._publish_block([
                                    [pending[index].popleft() for _ in range(count)]
                                    for index in range(len(pending))
                                ])
                        elif state not in (0, 2):
                            raise RuntimeError(f'Unexpected Noraxon Transfer() state: {state}')
                        if state in (0, 2):
                            self._stop_event.wait(self.transfer_interval_s)
                finally:
                    device.Stop()
        finally:
            pythoncom.CoUninitialize()

    def destroy_node(self):
        self._stop_event.set()
        if self._worker.is_alive():
            self._worker.join(timeout=3.0)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = NoraxonEmgNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
