#!/usr/bin/env python3
"""Plot live controller telemetry received over UDP (default port 8765)."""

import argparse
import json
import socket
import threading
import time
from collections import deque

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0', help='Local interface to listen on')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--window', type=float, default=30.0,
                        help='Visible time window in seconds (default: 30)')
    args = parser.parse_args()

    samples = deque()
    lock = threading.Lock()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(0.5)

    def receive():
        while True:
            try:
                data, _ = sock.recvfrom(65535)
                payload = json.loads(data.decode('utf-8'))
                calibrated_focus = payload.get('calibrated_focus')
                sample = (
                    time.monotonic(),
                    float(calibrated_focus) if calibrated_focus is not None else float('nan'),
                    float(payload['modulation_factor']),
                    float(payload['modulated_control']),
                    float(payload['ideal_control']),
                )
                with lock:
                    samples.append(sample)
            except socket.timeout:
                continue
            except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
                continue

    threading.Thread(target=receive, daemon=True).start()
    fig, ax = plt.subplots()
    lines = [ax.plot([], [], label=label)[0] for label in (
        'Foco calibrado (normalized_value recebido)',
        'Fator de modulação aplicado',
        'Controle após modulação',
        'Controle ideal antes da modulação',
    )]
    ax.set_title('Telemetria do controle em tempo real')
    ax.set_xlabel('Tempo (s)')
    ax.set_ylabel('Valor')
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left')
    started = time.monotonic()

    def update(_):
        now = time.monotonic()
        with lock:
            while samples and now - samples[0][0] > args.window:
                samples.popleft()
            visible = list(samples)
        if not visible:
            ax.set_xlim(max(0, now - started - args.window), now - started + 0.1)
            return lines
        x = [item[0] - started for item in visible]
        for index, line in enumerate(lines, start=1):
            line.set_data(x, [item[index] for item in visible])
        ax.set_xlim(max(0, now - started - args.window), now - started + 0.1)
        values = [item[i] for item in visible for i in (1, 2, 3, 4)
                  if item[i] == item[i] and abs(item[i]) != float('inf')]
        if not values:
            return lines
        low, high = min(values), max(values)
        padding = max((high - low) * 0.1, 0.05)
        ax.set_ylim(low - padding, high + padding)
        return lines

    print(f'Aguardando telemetria UDP em {args.host}:{args.port}...')
    animation = FuncAnimation(fig, update, interval=100, blit=False, cache_frame_data=False)
    # Keep a reference alive while the interactive window is open.
    fig._telemetry_animation = animation
    try:
        plt.show()
    finally:
        sock.close()


if __name__ == '__main__':
    main()
