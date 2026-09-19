#!/usr/bin/env python3
"""Expose an EMOTIV headset through the platform's HAL device contract.

The Cortex WebSocket is deliberately owned by one worker thread.  Cortex sends
stream samples and JSON-RPC replies on the same socket, so reading it from ROS
service callbacks would race with the sample receiver.
"""

import json
import math
import queue
import ssl
import threading
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray, String

try:
    import websocket
except ImportError:  # Keep the ROS package importable when the optional client is absent.
    websocket = None


METRIC_FIELDS = ('focus', 'engagement', 'stress')
TRAINING_STATUSES = {'start', 'accept', 'reject', 'reset', 'erase'}


class CortexConnection:
    """Small synchronous Cortex JSON-RPC client, used only by the worker."""

    def __init__(self, url):
        self.url = url
        self.ws = None
        self.request_id = 0

    def connect(self):
        self.ws = websocket.create_connection(
            self.url, sslopt={'cert_reqs': ssl.CERT_NONE}, timeout=3.0)

    def close(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None

    def receive(self, timeout):
        self.ws.settimeout(timeout)
        return json.loads(self.ws.recv())

    def rpc(self, method, params, on_sample):
        self.request_id += 1
        request_id = self.request_id
        self.ws.send(json.dumps({
            'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params,
        }))
        while True:
            message = self.receive(10.0)
            if message.get('id') != request_id:
                on_sample(message)
                continue
            if 'error' in message:
                raise RuntimeError(message['error'])
            return message.get('result', {})


class EmotivEegNode(Node):
    """ROS 2 hardware node for EMOTIV performance metrics and mental commands."""

    def __init__(self):
        super().__init__('emotiv_eeg_node')

        self.declare_parameter('device_id', 'emotiv_eeg')
        self.declare_parameter('cortex_url', 'wss://localhost:6868')
        self.declare_parameter('client_id', '')
        self.declare_parameter('client_secret', '')
        self.declare_parameter('license', '')
        self.declare_parameter('debit', 0)
        self.declare_parameter('headset_id', '')
        self.declare_parameter('enable_raw_eeg', False)
        self.declare_parameter('info_period_s', 1.0)
        self.declare_parameter('reconnect_period_s', 3.0)

        self.device_id = self.get_parameter('device_id').value
        self.cortex_url = self.get_parameter('cortex_url').value
        self.client_id = self.get_parameter('client_id').value
        self.client_secret = self.get_parameter('client_secret').value
        self.license = self.get_parameter('license').value
        self.debit = self.get_parameter('debit').value
        self.headset_id = self.get_parameter('headset_id').value
        self.enable_raw_eeg = self.get_parameter('enable_raw_eeg').value
        self.reconnect_period_s = self.get_parameter('reconnect_period_s').value

        prefix = f'/device/{self.device_id}'
        self.info_pub = self.create_publisher(String, '/device/info', 10)
        # /raw is the standard HAL numeric stream.  Its fixed order is declared below.
        self.raw_pub = self.create_publisher(Float32MultiArray, f'{prefix}/raw', 10)
        self.metrics_pub = self.create_publisher(String, f'{prefix}/metrics', 10)
        self.command_pub = self.create_publisher(String, f'{prefix}/mental_command', 10)
        self.training_pub = self.create_publisher(String, f'{prefix}/calibration/state', 10)
        self.connection_pub = self.create_publisher(String, f'{prefix}/connection_state', 10)
        self.eeg_pub = self.create_publisher(Float32MultiArray, f'{prefix}/eeg', 10)
        self.eeg_schema_pub = self.create_publisher(String, f'{prefix}/eeg_schema', 10)

        self.create_subscription(
            String, f'{prefix}/calibration/request', self._on_training_request, 10)
        self.create_timer(self.get_parameter('info_period_s').value, self._publish_info)

        self._connected = False
        self._stop_event = threading.Event()
        self._commands = queue.Queue()
        self._met_cols = []
        self._com_cols = []
        self._eeg_cols = []
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        self.get_logger().info('EMOTIV EEG node started; waiting for Cortex service.')

    def _publish_info(self):
        # Device Manager treats this heartbeat as availability; do not advertise an
        # unavailable Cortex/headset as a usable input.
        if not self._connected:
            return
        self.info_pub.publish(String(data=json.dumps({
            'device_id': self.device_id,
            'type': 'eeg',
            'capabilities': [
                'performance_metrics', 'focus', 'engagement', 'stress',
                'mental_command', 'mental_command_training',
            ] + (['raw_eeg'] if self.enable_raw_eeg else []),
            'units': ['score_0_to_1', 'score_0_to_1', 'score_0_to_1'],
            'raw_fields': list(METRIC_FIELDS),
            'raw_topic': f'/device/{self.device_id}/raw',
            'mental_command_topic': f'/device/{self.device_id}/mental_command',
            'calibration_request_topic': f'/device/{self.device_id}/calibration/request',
        })))

    def _publish_connection(self, state, message=''):
        self.connection_pub.publish(String(data=json.dumps({
            'device_id': self.device_id, 'connected': state, 'message': message,
        })))

    def _on_training_request(self, msg):
        """Queue Cortex training; request JSON is {"action":"push","status":"start"}."""
        try:
            request = json.loads(msg.data)
            action = request['action']
            status = request['status']
            if not isinstance(action, str) or status not in TRAINING_STATUSES:
                raise ValueError('action must be a string and status must be a supported training status')
        except (json.JSONDecodeError, KeyError, ValueError) as error:
            self._publish_training('error', message=f'Invalid training request: {error}')
            return

        self._commands.put({'action': action, 'status': status})
        self._publish_training('queued', action=action, requested_status=status)

    def _publish_training(self, state, action=None, requested_status=None, message=''):
        payload = {'device_id': self.device_id, 'state': state, 'message': message}
        if action is not None:
            payload['action'] = action
        if requested_status is not None:
            payload['requested_status'] = requested_status
        self.training_pub.publish(String(data=json.dumps(payload)))

    def _run(self):
        if websocket is None:
            self.get_logger().error('Missing websocket-client. Install the python3-websocket package.')
            self._publish_connection(False, 'websocket-client is not installed')
            return

        while not self._stop_event.is_set():
            client = CortexConnection(self.cortex_url)
            token = None
            session_id = None
            try:
                client.connect()
                token, session_id = self._open_session(client)
                self._connected = True
                self._publish_connection(True, 'Cortex session active')
                self.get_logger().info('Connected to Cortex and subscribed to EMOTIV streams.')
                self._receive_loop(client, token, session_id)
            except Exception as error:
                if not self._stop_event.is_set():
                    self.get_logger().warn(f'Cortex connection unavailable: {error}')
                    self._publish_connection(False, str(error))
            finally:
                self._connected = False
                # Closing releases the license's local session quota. Without this,
                # every reconnect leaves the old session dangling on Cortex and
                # silently burns quota until "-32019 Session limit" hits.
                if token and session_id:
                    try:
                        client.rpc('updateSession', {
                            'cortexToken': token, 'session': session_id, 'status': 'close',
                        }, self._on_cortex_message)
                    except Exception as error:
                        self.get_logger().warn(f'Failed to close Cortex session cleanly: {error}')
                client.close()

        self._stop_event.wait(self.reconnect_period_s)
        
    def _open_session(self, client):
        def rpc(method, params):
            return client.rpc(method, params, self._on_cortex_message)

        rpc('requestAccess', {
            'clientId': self.client_id, 'clientSecret': self.client_secret,
        })
        auth = {'clientId': self.client_id, 'clientSecret': self.client_secret}
        if self.license:
            auth['license'] = self.license
        if self.debit:
            auth['debit'] = self.debit
        token = rpc('authorize', auth)['cortexToken']

        headsets = rpc('queryHeadsets', {})
        if self.headset_id:
            headsets = [headset for headset in headsets if headset.get('id') == self.headset_id]
        if not headsets:
            raise RuntimeError('No requested EMOTIV headset was found by Cortex')
        headset = headsets[0]
        headset_id = headset['id']
        if headset.get('status') != 'connected':
            rpc('controlDevice', {'command': 'connect', 'headset': headset_id})
            # Cortex completes connection asynchronously.  Query once more before
            # opening the session, while retaining a useful error if it is not ready.
            time.sleep(1.0)

        session_id = rpc('createSession', {
            'cortexToken': token, 'headset': headset_id, 'status': 'active',
        })['id']
        streams = ['met', 'com', 'sys']
        if self.enable_raw_eeg:
            streams.append('eeg')
        subscription = rpc('subscribe', {
            'cortexToken': token, 'session': session_id, 'streams': streams,
        })
        failures = subscription.get('failure', [])
        if failures:
            self.get_logger().warn(f'Cortex stream subscription failures: {failures}')
        successful = {item['streamName']: item.get('cols', [])
                      for item in subscription.get('success', [])}
        missing = {'met', 'com', 'sys'} - set(successful)
        if missing:
            raise RuntimeError(f'Cortex denied required stream(s): {sorted(missing)}')
        self._met_cols = successful['met']
        self._com_cols = successful['com']
        self._eeg_cols = successful.get('eeg', [])
        return token, session_id

    def _receive_loop(self, client, token, session_id):
        while not self._stop_event.is_set():
            self._drain_training_requests(client, token, session_id)
            try:
                message = client.receive(0.2)
            except Exception as error:
                # websocket-client raises a timeout exception while an otherwise
                # healthy stream is quiet; only reconnect for non-timeout failures.
                if websocket is not None and isinstance(error, websocket.WebSocketTimeoutException):
                    continue
                raise
            self._on_cortex_message(message)

    def _drain_training_requests(self, client, token, session_id):
        while True:
            try:
                request = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                result = client.rpc('training', {
                    'cortexToken': token,
                    'session': session_id,
                    'detection': 'mentalCommand',
                    'action': request['action'],
                    'status': request['status'],
                }, self._on_cortex_message)
                self._publish_training(
                    'accepted', request['action'], request['status'],
                    result.get('message', 'Cortex accepted the training request'),
                )
            except Exception as error:
                self._publish_training('error', request['action'], request['status'], str(error))

    def _on_cortex_message(self, message):
        if 'met' in message:
            self._publish_metrics(message)
        if 'com' in message:
            values = dict(zip(self._com_cols, message['com']))
            action = values.get('act', 'neutral')
            power = self._number(values.get('pow'))
            self.command_pub.publish(String(data=json.dumps({
                'device_id': self.device_id,
                'timestamp': message.get('time'),
                'action': action,
                'power': power,
                'is_neutral': action == 'neutral',
            })))
        if 'sys' in message:
            values = dict(zip(['event', 'message'], message['sys']))
            self._publish_training('event', message=json.dumps(values))
        if self.enable_raw_eeg and 'eeg' in message:
            self._publish_eeg(message)

    def _publish_metrics(self, message):
        native_metrics = dict(zip(self._met_cols, message['met']))
        metrics = {
            'focus': self._number(native_metrics.get('attention', native_metrics.get('focus'))),
            'engagement': self._number(native_metrics.get('eng', native_metrics.get('engagement'))),
            'stress': self._number(native_metrics.get('str', native_metrics.get('stress'))),
        }
        self.metrics_pub.publish(String(data=json.dumps({
            'device_id': self.device_id,
            'timestamp': message.get('time'),
            'metrics': metrics,
            'native_metrics': native_metrics,
        })))
        # NaN distinguishes an inactive/unavailable Cortex metric from a real zero.
        self.raw_pub.publish(Float32MultiArray(data=[
            value if value is not None else math.nan for value in metrics.values()
        ]))

    def _publish_eeg(self, message):
        numeric_pairs = []
        for column, value in zip(self._eeg_cols, message['eeg']):
            numeric = self._number(value)
            if numeric is not None:
                numeric_pairs.append((column, numeric))
        self.eeg_schema_pub.publish(String(data=json.dumps({
            'device_id': self.device_id,
            'timestamp': message.get('time'),
            'fields': [column for column, _ in numeric_pairs],
        })))
        self.eeg_pub.publish(Float32MultiArray(data=[value for _, value in numeric_pairs]))

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or value is None:
            return None
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None

    def destroy_node(self):
        self._stop_event.set()
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = EmotivEegNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
