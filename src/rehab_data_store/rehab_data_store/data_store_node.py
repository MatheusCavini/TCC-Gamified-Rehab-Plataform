"""Write session biosignals to local CSV files and metadata to PostgreSQL."""

import json
import math
import os
import queue
import threading
import time
import uuid

import psycopg2
import rclpy
from hal_interfaces.srv import SetString
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray, String
from std_srvs.srv import Trigger


class RehabDataStoreNode(Node):
    """Persist raw/filtered streams and processed numeric metrics."""

    def __init__(self):
        super().__init__('rehab_data_store')
        self.declare_parameter('data_dir', '/data/sessions')
        self.declare_parameter('db_host', os.getenv('DB_HOST', '127.0.0.1'))
        self.declare_parameter('db_port', int(os.getenv('DB_PORT', '5432')))
        self.declare_parameter('db_name', os.getenv('POSTGRES_DB', 'rehab'))
        self.declare_parameter('db_user', os.getenv('POSTGRES_USER', 'rehab'))
        self.declare_parameter('db_password', os.getenv('POSTGRES_PASSWORD', ''))
        self.data_dir = os.path.abspath(self.get_parameter('data_dir').value)
        os.makedirs(self.data_dir, exist_ok=True)

        self.events = queue.Queue(maxsize=2000)
        self.recording_lock = threading.Lock()
        self.session_id = None
        self.game_session_metadata = {}
        self.device_metadata = {}
        self.subscriptions = {}
        self.warned_missing_sample_rate = set()
        self.worker_stop = threading.Event()
        self.worker = threading.Thread(target=self._writer_loop, daemon=True)
        self.worker.start()

        self.create_subscription(String, '/devices/available', self._on_devices, 10)
        self.create_subscription(String, '/hal/device_state', self._on_processed_state, 100)
        self.create_subscription(String, '/game/performance_metrics', self._on_game_metrics, 100)
        self.create_service(SetString, '/recording/start', self._start_session)
        self.create_service(Trigger, '/recording/stop', self._stop_session)
        self.get_logger().info('Data store ready; awaiting /recording/start.')

    def _db_connect(self):
        return psycopg2.connect(
            host=self.get_parameter('db_host').value,
            port=self.get_parameter('db_port').value,
            dbname=self.get_parameter('db_name').value,
            user=self.get_parameter('db_user').value,
            password=self.get_parameter('db_password').value,
            connect_timeout=5,
        )

    def _enqueue_and_wait(self, event, timeout=10):
        done = threading.Event()
        event['done'] = done
        self.events.put(event, timeout=timeout)
        if not done.wait(timeout):
            raise TimeoutError('Persistence worker did not complete the request in time.')
        if event.get('error'):
            raise RuntimeError(event['error'])
        return event.get('result')

    def _start_session(self, request, response):
        try:
            raw_request = (request.data or '').strip()
            payload = json.loads(raw_request) if raw_request.startswith('{') else {}
            patient_id = str(uuid.UUID(payload.get('patient_id', raw_request)))
            with self.recording_lock:
                if self.session_id:
                    raise ValueError('A recording session is already active.')
                session_id = self._enqueue_and_wait({
                    'kind': 'start', 'patient_id': patient_id,
                    'protocol': str(payload.get('protocol', '')),
                    'device': str(payload.get('device', '')),
                })
                self.session_id = session_id
                self.game_session_metadata = {}
            response.success = True
            response.message = session_id
        except Exception as error:
            response.success = False
            response.message = str(error)
        return response

    def _stop_session(self, request, response):
        del request
        try:
            with self.recording_lock:
                if not self.session_id:
                    raise ValueError('There is no active recording session.')
                session_id = self.session_id
                self.session_id = None
            try:
                self._enqueue_and_wait({'kind': 'stop', 'session_id': session_id})
            except Exception:
                with self.recording_lock:
                    if not self.session_id:
                        self.session_id = session_id
                raise
            response.success = True
            response.message = session_id
        except Exception as error:
            response.success = False
            response.message = str(error)
        return response

    def _on_devices(self, message):
        try:
            devices = json.loads(message.data)
        except (json.JSONDecodeError, TypeError):
            self.get_logger().warning('Ignoring malformed /devices/available payload.')
            return
        for device in devices:
            device_id = str(device.get('device_id', '')).strip()
            if not device_id:
                continue
            self.device_metadata[device_id] = device
            if device.get('type') == 'eeg':
                # The HAL raw topic for this device carries derived Cortex
                # metrics. Subscribe to the actual EEG stream only when enabled.
                if 'raw_eeg' in device.get('capabilities', []):
                    topic = device.get('eeg_topic', f'/device/{device_id}/eeg')
                    key = (device_id, 'raw')
                    if key not in self.subscriptions:
                        self.subscriptions[key] = self.create_subscription(
                            Float32MultiArray, topic,
                            lambda msg, d=device_id: self._on_signal(d, 'raw', msg), 100,
                        )
                    schema_topic = device.get(
                        'eeg_schema_topic', f'/device/{device_id}/eeg_schema')
                    schema_key = (device_id, 'schema')
                    if schema_key not in self.subscriptions:
                        self.subscriptions[schema_key] = self.create_subscription(
                            String, schema_topic,
                            lambda msg, d=device_id: self._on_eeg_schema(d, msg), 10,
                        )
                continue
            for signal_type, topic in (
                ('raw', device.get('raw_topic', f'/device/{device_id}/raw')),
                ('filtered', device.get('filtered_topic', f'/device/{device_id}/filtered')),
            ):
                key = (device_id, signal_type)
                if key not in self.subscriptions:
                    self.subscriptions[key] = self.create_subscription(
                        Float32MultiArray, topic,
                        lambda msg, d=device_id, s=signal_type: self._on_signal(d, s, msg),
                        100,
                    )

    def _on_eeg_schema(self, device_id, message):
        try:
            schema = json.loads(message.data)
            metadata = self.device_metadata.setdefault(device_id, {})
            metadata['channel_names'] = schema.get('fields', [])
            metadata['sample_rate_hz'] = schema.get('sample_rate_hz')
            metadata['units'] = [schema.get('unit', 'score_0_to_1')]
        except (json.JSONDecodeError, TypeError):
            self.get_logger().warning(f'Ignoring malformed EEG schema for {device_id}.')

    def _active_session(self):
        with self.recording_lock:
            return self.session_id

    def _queue_data(self, event):
        if not self._active_session():
            return
        self._queue_event(event)

    def _queue_event(self, event):
        try:
            self.events.put_nowait(event)
            return True
        except queue.Full:
            self.get_logger().error('Persistence queue is full; a data block was dropped.')
            return False

    def _on_signal(self, device_id, signal_type, message):
        session_id = self._active_session()
        if not session_id:
            return
        metadata = self.device_metadata.get(device_id, {})
        sample_rate_hz = metadata.get('sample_rate_hz')
        if not isinstance(sample_rate_hz, (int, float)) or sample_rate_hz <= 0:
            if device_id not in self.warned_missing_sample_rate:
                self.get_logger().warning(
                    f'Not recording {device_id}: sampling rate is missing or invalid.')
                self.warned_missing_sample_rate.add(device_id)
            return
        dims = message.layout.dim
        channels = int(dims[0].size) if len(dims) >= 2 else max(1, len(message.data))
        samples = int(dims[1].size) if len(dims) >= 2 else 1
        if channels <= 0 or samples <= 0 or channels * samples > len(message.data):
            return
        names = metadata.get('channel_names') or metadata.get('raw_fields') or []
        units = metadata.get('units') or []
        self._queue_data({
            'kind': 'signal', 'session_id': session_id, 'device_id': device_id,
            'signal_type': signal_type, 'received_at': time.time(),
            'channels': channels, 'samples': samples,
            'sampling_rate_hz': float(sample_rate_hz),
            'channel_names': names, 'unit': units[0] if units else None,
            'values': list(message.data[:channels * samples]),
        })

    def _on_processed_state(self, message):
        try:
            payload = json.loads(message.data)
            device_id = str(payload.get('device_id', 'unknown'))
            unit = payload.get('unit')
            for key in ('rms', 'normalized_value', 'metrics'):
                values = payload.get(key)
                if isinstance(values, dict):
                    for name, value in values.items():
                        self._flatten_metric(device_id, f'{key}.{name}', value, unit)
                elif isinstance(values, (int, float, list)):
                    self._flatten_metric(device_id, key, values, unit)
        except (json.JSONDecodeError, TypeError, ValueError):
            self.get_logger().warning('Ignoring malformed /hal/device_state payload.')

    def _on_game_metrics(self, message):
        """Persist Unity game measurements and keep game setup on the session."""
        try:
            payload = json.loads(message.data)
            if not isinstance(payload, dict):
                raise ValueError('Expected a JSON object.')
            session_id = self._active_session()
            if not session_id:
                return

            game_id = str(payload.get('game_id', '')).strip()
            session_metadata = {'source': 'unity'}
            if game_id:
                session_metadata['game_id'] = game_id
            if isinstance(payload.get('finished'), bool):
                session_metadata['finished'] = payload['finished']

            # Exercise setup is stored once per change on sessions.metadata,
            # rather than repeated in the performance time series.
            config_fields = (
                'total_cycles', 'inflate_seconds', 'deflate_seconds',
                'inflate_target',
            )
            for field in config_fields:
                value = payload.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if math.isfinite(float(value)):
                        session_metadata[field] = value

            with self.recording_lock:
                if self.session_id != session_id:
                    return
                if session_metadata != self.game_session_metadata:
                    enqueued = self._queue_event({
                        'kind': 'session_metadata', 'session_id': session_id,
                        'metadata': session_metadata,
                    })
                    if enqueued:
                        self.game_session_metadata = session_metadata

            excluded = {'game_id', 'finished', *config_fields}
            for name, value in payload.items():
                if name in excluded:
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if math.isfinite(float(value)):
                        self._queue_metric(
                            None, f'game.{name}', float(value), None,
                            metadata={'source': 'unity', 'game_id': game_id or None},
                            session_id=session_id,
                        )
        except (json.JSONDecodeError, TypeError, ValueError):
            self.get_logger().warning(
                'Ignoring malformed /game/performance_metrics payload.')

    def _flatten_metric(self, device_id, name, value, unit):
        if isinstance(value, list):
            for index, item in enumerate(value):
                self._flatten_metric(device_id, f'{name}.{index + 1}', item, unit)
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            self._queue_metric(device_id, name, float(value), unit)

    def _queue_metric(self, device_id, name, value, unit, metadata=None,
                      session_id=None):
        session_id = session_id or self._active_session()
        if session_id:
            self._queue_data({
                'kind': 'metric', 'session_id': session_id,
                'device_id': device_id, 'name': name,
                'value': float(value), 'unit': unit,
                'metadata': metadata or {}, 'recorded_at': time.time(),
            })

    def _writer_loop(self):
        connection = None
        active_id = None
        files = {}
        pending_metrics = []

        def flush_metrics():
            if not pending_metrics:
                return
            with connection.cursor() as cursor:
                cursor.executemany(
                    'INSERT INTO performance_metrics '
                    '(session_id, name, value, unit, metadata, recorded_at) '
                    'VALUES (%s, %s, %s, %s, %s, to_timestamp(%s))',
                    pending_metrics,
                )
            connection.commit()
            pending_metrics.clear()

        while not self.worker_stop.is_set() or not self.events.empty():
            try:
                event = self.events.get(timeout=0.2)
            except queue.Empty:
                try:
                    flush_metrics()
                except Exception as error:
                    if connection:
                        connection.rollback()
                    self.get_logger().error(f'Could not flush pending metrics: {error}')
                continue
            try:
                kind = event['kind']
                if kind in ('start', 'signal', 'metric', 'session_metadata', 'stop') and connection is None:
                    connection = self._db_connect()
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "UPDATE sessions SET status = 'interrupted', ended_at = now() "
                            "WHERE status = 'in_progress'"
                        )
                    connection.commit()
                if kind == 'start':
                    if active_id:
                        raise RuntimeError('Writer already has an active session.')
                    session_id = str(uuid.uuid4())
                    with connection.cursor() as cursor:
                        cursor.execute(
                            'INSERT INTO patients (id) VALUES (%s) ON CONFLICT (id) DO NOTHING',
                            (event['patient_id'],),
                        )
                        cursor.execute(
                            "INSERT INTO sessions (id, patient_id, started_at, protocol, device, status) "
                            "VALUES (%s, %s, now(), %s, %s, 'in_progress')",
                            (session_id, event['patient_id'], event['protocol'], event['device']),
                        )
                    connection.commit()
                    active_id = session_id
                    event['result'] = session_id
                elif kind == 'signal':
                    if event['session_id'] != active_id:
                        continue
                    session_id = event['session_id']
                    folder = os.path.join(self.data_dir, session_id)
                    os.makedirs(folder, exist_ok=True)
                    key = (event['device_id'], event['signal_type'])
                    if key not in files:
                        safe_device = ''.join(c if c.isalnum() or c in '-_' else '_' for c in event['device_id'])
                        filename = f'{safe_device}_{event["signal_type"]}.csv'
                        relative_path = os.path.join(session_id, filename).replace('\\', '/')
                        handle = open(os.path.join(folder, filename), 'w', encoding='utf-8', newline='')
                        handle.write('timestamp_unix,channel,sample_index,value\n')
                        files[key] = (handle, relative_path)
                        with connection.cursor() as cursor:
                            cursor.execute(
                                'INSERT INTO signal_files (session_id, signal_type, file_path, format, '
                                'sampling_rate_hz, channels, unit, algorithm_version) '
                                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                                (session_id, event['signal_type'], relative_path, 'csv',
                                 event['sampling_rate_hz'], json.dumps(event['channel_names']), event['unit'],
                                 'emg_biquad_bandpass_v1' if event['signal_type'] == 'filtered' else None),
                            )
                        connection.commit()
                    handle = files[key][0]
                    sample_rate = event['sampling_rate_hz']
                    block_start = event['received_at'] - event['samples'] / sample_rate
                    values = event['values']
                    for channel in range(event['channels']):
                        for sample in range(event['samples']):
                            index = channel * event['samples'] + sample
                            value = float(values[index])
                            if math.isfinite(value):
                                stamp = block_start + sample / sample_rate
                                handle.write(f'{stamp:.6f},{channel},{sample},{value:.9g}\n')
                    handle.flush()
                elif kind == 'metric':
                    if event['session_id'] != active_id:
                        continue
                    metadata = dict(event.get('metadata') or {})
                    if event.get('device_id'):
                        metadata['device_id'] = event['device_id']
                    pending_metrics.append((
                        event['session_id'], event['name'], event['value'], event['unit'],
                        json.dumps(metadata), event['recorded_at'],
                    ))
                    if len(pending_metrics) >= 128:
                        flush_metrics()
                elif kind == 'session_metadata':
                    if event['session_id'] != active_id:
                        continue
                    with connection.cursor() as cursor:
                        cursor.execute(
                            'UPDATE sessions SET metadata = metadata || %s::jsonb WHERE id = %s',
                            (json.dumps(event['metadata']), event['session_id']),
                        )
                    connection.commit()
                elif kind == 'stop':
                    if event['session_id'] != active_id:
                        raise ValueError('Requested session is not active in the writer.')
                    flush_metrics()
                    for handle, _ in files.values():
                        handle.flush()
                        handle.close()
                    files.clear()
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "UPDATE sessions SET ended_at = now(), status = %s WHERE id = %s",
                            (event.get('status', 'completed'), active_id),
                        )
                    connection.commit()
                    active_id = None
                elif kind == 'shutdown':
                    break
            except Exception as error:
                if connection:
                    connection.rollback()
                event['error'] = str(error)
                if event.get('kind') != 'metric':
                    self.get_logger().error(f'Persistence error ({event.get("kind")}): {error}')
            finally:
                if event.get('done'):
                    event['done'].set()
                self.events.task_done()
        for handle, _ in files.values():
            handle.close()
        if connection:
            connection.close()

    def destroy_node(self):
        with self.recording_lock:
            if self.session_id:
                try:
                    self._enqueue_and_wait({
                        'kind': 'stop', 'session_id': self.session_id,
                        'status': 'interrupted',
                    })
                except Exception as error:
                    self.get_logger().error(f'Could not close recording session: {error}')
                self.session_id = None
        self.worker_stop.set()
        self.worker.join(timeout=5)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = RehabDataStoreNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
