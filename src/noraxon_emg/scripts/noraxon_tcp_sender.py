#!/usr/bin/env python3
"""Forward live Noraxon Acquire transfer batches to the Docker TCP receiver.

This script deliberately has no ROS dependency. Run it in the Windows Python
environment that can import the Noraxon SDK and Windows COM packages.

Lines starting with ``NORAXON_STATUS`` / ``NORAXON_ERROR`` are a stable,
machine-readable channel consumed by scripts/Start-RehabSystem.ps1. Keep them
unchanged when editing the human-readable messages.
"""

import argparse
from collections import deque
import json
import math
import random
import select
import socket
import sys
import time
import traceback


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel-id', action='append', default=[],
                        help='EMG channel ID; repeat once for each channel, in desired order')
    parser.add_argument('--host', default='127.0.0.1',
                        help='Windows host address for the published Docker port')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--connect-timeout-s', type=float, default=30.0,
                        help='how long to keep retrying until the Docker receiver accepts')
    parser.add_argument('--desired-frequency-hz', type=float, default=2000.0)
    parser.add_argument('--transfer-interval-s', type=float, default=0.005)
    parser.add_argument('--dll-path', default=None)
    parser.add_argument('--use-setup', action='store_true',
                        help='let the SDK choose/configure the Acquire profile '
                             '(opens the Noraxon hardware setup dialog)')
    parser.add_argument('--simulate', action='store_true',
                        help='send synthetic TCP frames; does not import the Noraxon SDK')
    parser.add_argument('--simulation-channels', type=int, default=4)
    parser.add_argument('--simulation-rate-hz', type=float, default=1000.0)
    parser.add_argument('--simulation-block-period-s', type=float, default=0.02)
    return parser.parse_args()


def status(code, message):
    """Emit a launcher-readable status line followed by a human message."""
    print(f'NORAXON_STATUS {code} {message}', flush=True)


def send_frame(sock, payload):
    encoded = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode('utf-8')
    sock.sendall(encoded + b'\n')


def peer_closed(sock):
    """Return True when the receiver side has closed the TCP connection.

    The receiver never sends data, so a readable socket means EOF or reset.
    """
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return False
        return sock.recv(1, socket.MSG_PEEK) == b''
    except OSError:
        return True


def connect_to_receiver(host, port, timeout_s):
    """Connect to the Docker receiver, retrying until it really accepts.

    Docker Desktop's port proxy accepts connections on the Windows side even
    while nothing is listening inside the container yet, and then drops them.
    A plain create_connection() therefore "succeeds" too early, so each attempt
    is confirmed by checking that the peer did not immediately close it.
    """
    deadline = time.monotonic() + max(0.0, timeout_s)
    last_error = 'no attempt made'
    while True:
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=5.0)
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            time.sleep(0.5)
            if not peer_closed(sock):
                status('connected', f'Connected to Docker TCP receiver at {host}:{port}.')
                return sock
            last_error = 'connection accepted by Docker but closed by the container'
        except OSError as error:
            last_error = str(error)
        if sock is not None:
            sock.close()
        if time.monotonic() >= deadline:
            raise ConnectionError(
                f'Docker receiver at {host}:{port} is not accepting connections '
                f'({last_error}). Is the noraxon_tcp_receiver container running?')
        time.sleep(1.0)


def run_simulation(sock, args):
    if args.simulation_channels < 1 or args.simulation_rate_hz <= 0:
        raise ValueError('Simulation channel count and sample rate must be positive.')
    channel_count = args.simulation_channels
    sample_rate = args.simulation_rate_hz
    sample_count = max(1, round(sample_rate * args.simulation_block_period_s))
    send_frame(sock, {
        'kind': 'schema',
        'device_id': 'noraxon_emg',
        'channel_names': [f'TCP simulated EMG {i + 1}' for i in range(channel_count)],
        'channel_ids': [f'tcp_simulated_emg_{i + 1}' for i in range(channel_count)],
        'sample_rate_hz': sample_rate,
        'units': 'uV',
    })
    status('streaming', f'Sending synthetic TCP EMG: {channel_count} channels at '
                        f'{sample_rate:g} Hz. Press Ctrl+C to stop.')
    phase = 0.0
    first = True
    while True:
        channels = []
        for channel in range(channel_count):
            batch = []
            for sample in range(sample_count):
                envelope = 25.0 + 100.0 * max(0.0, math.sin(phase + channel * 0.45))
                carrier = math.sin(
                    2.0 * math.pi * (75.0 + channel * 20.0) * sample / sample_rate)
                batch.append(envelope * carrier + random.gauss(0.0, 8.0))
                phase += 2.0 * math.pi * 0.8 / sample_rate
            channels.append(batch)
        send_frame(sock, {'kind': 'raw', 'channels': channels})
        if first:
            status('first_batch', 'First synthetic EMG batch forwarded.')
            first = False
        time.sleep(args.simulation_block_period_s)


def run_acquire(args):
    try:
        import pythoncom
        from noraxon_sdk.core.batches import read_channel_batch
        from noraxon_sdk.core.others import select_and_configure_channels
        from noraxon_sdk.core.probing import probe_active_channels
        from noraxon_sdk.core.setup import activated_device, setup_sdk
    except ImportError as error:
        raise SystemExit(
            'Noraxon SDK dependencies are unavailable in this Python. Install/import '
            'noraxon_sdk, pywin32 and comtypes in the selected Windows environment. '
            f'Import error: {error}') from error

    pythoncom.CoInitialize()
    sock = None
    try:
        # The SDK is configured BEFORE opening the TCP connection. With
        # --use-setup this may block on the Noraxon hardware setup dialog for
        # as long as the operator takes to confirm it; an idle socket opened
        # earlier could be dropped in the meantime.
        if args.use_setup:
            status('setup_waiting',
                   'Waiting for the Noraxon hardware setup dialog to be confirmed...')
        else:
            status('setup', 'Initializing the Noraxon SDK with the current Acquire device...')
        sdk, device_manager, device = setup_sdk(
            use_setup=args.use_setup, dll_path=args.dll_path)
        channels = select_and_configure_channels(
            device=device, sdk=sdk, channel_ids=args.channel_id,
            desired_frequency=args.desired_frequency_hz)
        if not channels:
            raise RuntimeError(
                'The SDK did not configure any requested channels. Check that every '
                '--channel-id matches the IDs listed by list_device_channels exactly.')
        if any(channel.component_type != 'analog' or 'analog.emg' not in channel.id
               for channel in channels):
            raise ValueError('Every --channel-id must identify an analog EMG channel.')
        status('configured', f'{len(channels)} EMG channel(s) configured.')

        with activated_device(device=device, device_manager=device_manager):
            probe_active_channels(channels=channels)
            sample_rates = {
                float(channel.fs) for channel in channels if channel.fs is not None
            }
            if len(sample_rates) != 1:
                raise RuntimeError(
                    f'EMG channels have incompatible sampling rates: {sample_rates}')
            sample_rate_hz = sample_rates.pop()

            sock = connect_to_receiver(args.host, args.port, args.connect_timeout_s)
            send_frame(sock, {
                'kind': 'schema',
                'device_id': 'noraxon_emg',
                'channel_names': [channel.name for channel in channels],
                'channel_ids': [channel.id for channel in channels],
                'sample_rate_hz': sample_rate_hz,
                'units': 'uV',
            })
            status('streaming', f'Acquire stream active: {len(channels)} EMG channels at '
                                f'{sample_rate_hz:g} Hz. Sending batches; press Ctrl+C to stop.')
            device.Record()
            pending = [deque() for _ in channels]
            first_batch = True
            try:
                while True:
                    state = device.Transfer()
                    if state in (1, 3):
                        for index, channel in enumerate(channels):
                            batch = read_channel_batch(channel)
                            if batch is not None:
                                pending[index].extend(float(value) for value in batch)
                        count = min((len(values) for values in pending), default=0)
                        if count:
                            channel_batches = [
                                [pending[index].popleft() for _ in range(count)]
                                for index in range(len(pending))
                            ]
                            try:
                                send_frame(sock, {'kind': 'raw', 'channels': channel_batches})
                            except OSError as error:
                                raise ConnectionError(
                                    f'Docker receiver closed the connection: {error}') from error
                            if first_batch:
                                status('first_batch', 'First EMG batch forwarded to Docker.')
                                first_batch = False
                    elif state not in (0, 2):
                        raise RuntimeError(f'Unexpected Noraxon Transfer() state: {state}')
                    if state in (0, 2):
                        # Without data flowing a closed receiver (e.g. after
                        # `docker compose down`) would never be noticed; check
                        # explicitly so the device is always stopped cleanly.
                        if peer_closed(sock):
                            raise ConnectionError('Docker receiver closed the connection.')
                        time.sleep(args.transfer_interval_s)
            finally:
                device.Stop()
    finally:
        if sock is not None:
            sock.close()
        pythoncom.CoUninitialize()


def main():
    args = parse_args()
    if not args.simulate and not args.channel_id:
        raise SystemExit('Pass at least one --channel-id, or use --simulate to test TCP.')
    if args.simulate:
        sock = connect_to_receiver(args.host, args.port, args.connect_timeout_s)
        with sock:
            run_simulation(sock, args)
        return
    run_acquire(args)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped Noraxon TCP sender.', file=sys.stderr)
    except SystemExit as error:
        if error.code not in (None, 0):
            print(f'NORAXON_ERROR {error.code}', flush=True)
        raise
    except Exception as error:  # noqa: BLE001 - report every failure to the launcher
        traceback.print_exc()
        print(f'NORAXON_ERROR {type(error).__name__}: {error}', flush=True)
        sys.exit(1)