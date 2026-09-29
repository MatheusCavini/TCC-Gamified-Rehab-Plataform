#!/usr/bin/env python3
"""Receive Noraxon batches over TCP and publish them into the ROS graph."""

import json
import queue
import socket
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray, MultiArrayDimension, String


class NoraxonTcpReceiver(Node):
    """TCP ingress for a Windows Noraxon SDK sender."""

    def __init__(self):
        super().__init__('noraxon_tcp_receiver')
        self.declare_parameter('bind_address', '0.0.0.0')
        self.declare_parameter('port', 8765)
        self.declare_parameter('device_id', 'noraxon_emg')
        self.declare_parameter('info_period_s', 1.0)

        self.bind_address = str(self.get_parameter('bind_address').value)
        self.port = int(self.get_parameter('port').value)
        self.device_id = str(self.get_parameter('device_id').value)
        self.events = queue.Queue(maxsize=100)
        self.stop_event = threading.Event()
        self.metadata = None
        self.connected = False

        prefix = f'/device/{self.device_id}'
        self.info_pub = self.create_publisher(String, '/device/info', 10)
        self.raw_pub = self.create_publisher(Float32MultiArray, f'{prefix}/raw', 100)
        self.schema_pub = self.create_publisher(String, f'{prefix}/raw_schema', 10)
        self.connection_pub = self.create_publisher(String, f'{prefix}/connection_state', 10)
        self.alias_pub = self.create_publisher(Float32MultiArray, '/biosignals/emg/raw', 100)
        self.create_timer(0.002, self._drain_events)
        self.create_timer(
            float(self.get_parameter('info_period_s').value), self._publish_info)

        self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
        self.server_thread.start()
        self.get_logger().info(
            f'Noraxon TCP receiver listening on {self.bind_address}:{self.port}')

    def _enqueue(self, event):
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # Raw samples must not silently accumulate without bound. If ROS
            # cannot keep up, discard the oldest queued event and preserve the
            # newest incoming data.
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            try:
                self.events.put_nowait(event)
            except queue.Full:
                pass

    def _server_loop(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((self.bind_address, self.port))
            server.listen(1)
            server.settimeout(1.0)
            while not self.stop_event.is_set():
                try:
                    conn, address = server.accept()
                except socket.timeout:
                    continue
                self.get_logger().info(f'Noraxon TCP sender connected from {address}')
                self._enqueue({'kind': 'connection', 'connected': True, 'message': str(address)})
                with conn:
                    conn.settimeout(1.0)
                    buffer = b''
                    while not self.stop_event.is_set():
                        try:
                            chunk = conn.recv(65536)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buffer += chunk
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            if not line:
                                continue
                            try:
                                event = json.loads(line.decode('utf-8'))
                                self._enqueue(event)
                            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                                self.get_logger().warning(f'Ignoring malformed TCP frame: {error}')
                self._enqueue({
                    'kind': 'connection', 'connected': False,
                    'message': 'Windows sender disconnected',
                })
        except OSError as error:
            self._enqueue({
                'kind': 'connection', 'connected': False,
                'message': f'TCP receiver error: {error}',
            })
        finally:
            server.close()

    def _drain_events(self):
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            kind = event.get('kind')
            if kind == 'schema':
                if event.get('device_id') != self.device_id:
                    continue
                self.metadata = event
                self.schema_pub.publish(String(data=json.dumps({
                    'device_id': self.device_id,
                    'format': 'Float32MultiArray channel-major blocks',
                    'dimensions': ['channel', 'sample'],
                    'channel_names': event['channel_names'],
                    'channel_ids': event['channel_ids'],
                    'sample_rate_hz': float(event['sample_rate_hz']),
                    'units': event.get('units', 'uV'),
                })))
                self.get_logger().info(
                    f"Noraxon stream metadata received: {len(event['channel_ids'])} channels "
                    f"at {float(event['sample_rate_hz']):g} Hz")
            elif kind == 'raw':
                if self.metadata is None:
                    continue
                channels = event.get('channels', [])
                if not channels or any(not isinstance(ch, list) for ch in channels):
                    continue
                sample_count = len(channels[0])
                if sample_count == 0 or any(len(ch) != sample_count for ch in channels):
                    continue
                if len(channels) != len(self.metadata['channel_ids']):
                    self.get_logger().warning('Dropping block with unexpected channel count')
                    continue
                msg = Float32MultiArray()
                msg.layout.dim = [
                    MultiArrayDimension(
                        label='channel', size=len(channels),
                        stride=len(channels) * sample_count),
                    MultiArrayDimension(
                        label='sample', size=sample_count, stride=sample_count),
                ]
                msg.data = [float(value) for channel in channels for value in channel]
                self.raw_pub.publish(msg)
                self.alias_pub.publish(msg)
            elif kind == 'connection':
                self.connected = bool(event.get('connected'))
                message = str(event.get('message', ''))
                self.connection_pub.publish(String(data=json.dumps({
                    'device_id': self.device_id,
                    'connected': self.connected,
                    'message': message,
                })))
                if self.connected:
                    self.get_logger().info(f'Noraxon TCP stream active: {message}')
                else:
                    self.get_logger().warning(f'Noraxon TCP stream inactive: {message}')

    def _publish_info(self):
        if not self.connected or self.metadata is None:
            return
        self.info_pub.publish(String(data=json.dumps({
            'device_id': self.device_id,
            'type': 'emg',
            'capabilities': ['raw_emg', 'rms_emg'],
            'units': [self.metadata.get('units', 'uV')],
            'raw_topic': f'/device/{self.device_id}/raw',
            'rms_topic': f'/device/{self.device_id}/rms',
            'raw_format': 'channel_major_blocks',
            'channel_names': self.metadata['channel_names'],
            'channel_ids': self.metadata['channel_ids'],
            'sample_rate_hz': float(self.metadata['sample_rate_hz']),
        })))

    def destroy_node(self):
        self.stop_event.set()
        if self.server_thread.is_alive():
            self.server_thread.join(timeout=2.0)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = NoraxonTcpReceiver()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
