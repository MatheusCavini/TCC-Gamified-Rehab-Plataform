#!/usr/bin/env python3
"""ROS 2 serial adapter for a generic single-value pulmonary rehab device.

The device is expected to stream one numeric sample per line over a serial port
(a pressure / flow sensor on an Arduino or ESP32, for example).  This node owns
the port, publishes the samples on the platform's raw-device topic and keeps
advertising the device on /device/info for as long as samples keep arriving.

Platform contract (same as the other device nodes):
  /device/info              std_msgs/String             JSON heartbeat, read by
                                                        hal_device_manager
  /device/<device_id>/raw   std_msgs/Float32MultiArray   [value]; normalised downstream
                                                        by hal_signal_processing
                                                        (min/max calibration)

Nothing here is specific to a sensor: change the unit, baud rate or field index
through parameters.  Use -p simulate:=true to run the whole stack without hardware.
"""

import json
import math
import random
import re
import threading
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray, String

try:
    import serial
except ImportError:  # pyserial is only needed when talking to real hardware
    serial = None

DEVICE_TYPE = 'pulmonary'

_NUMBER_RE = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')


def parse_sample(line, field_index=0):
    """Extract one numeric sample from a serial line.

    Every number found in the line is a candidate, so "512", "512.7",
    "P: 512" and "512,37" all work.  field_index picks which candidate is the
    sample (0 = first, -1 = last).  Returns None when the line has no usable
    number, which is how partial or garbled lines are discarded.
    """
    numbers = _NUMBER_RE.findall(line)
    if not numbers:
        return None
    try:
        value = float(numbers[field_index])
    except IndexError:
        return None
    return value if math.isfinite(value) else None


class PulmonaryDeviceNode(Node):
    """Read a single-value serial sensor and publish it as a HAL raw device."""

    def __init__(self):
        super().__init__('pulmonary_device_node')

        self.declare_parameter('device_id', 'pulmonary_device')
        self.declare_parameter('serial_port', '/dev/ttyUSB0')
        self.declare_parameter('baud_rate', 115200)
        # Which number on the line is the sample (0 = first, -1 = last).
        self.declare_parameter('field_index', 0)
        self.declare_parameter('unit', 'raw_adc')
        self.declare_parameter('info_period_s', 1.0)
        # Stop advertising the device if no sample arrived for this long, so
        # hal_device_manager drops it and the game menu shows it as missing.
        self.declare_parameter('stale_timeout_s', 1.5)
        self.declare_parameter('reconnect_period_s', 2.0)
        # Arduino-style boards reset when the port opens; wait before reading.
        self.declare_parameter('open_settle_s', 2.0)
        # Samples arriving faster than this are averaged, to keep the HAL and
        # the Unity TCP link from being flooded by a kHz-rate sensor.
        self.declare_parameter('max_publish_rate_hz', 50.0)
        # Built-in synthetic breathing, so the graph is testable without hardware.
        self.declare_parameter('simulate', False)
        self.declare_parameter('simulation_breath_period_s', 8.0)

        self.device_id = self.get_parameter('device_id').value
        self.serial_port = self.get_parameter('serial_port').value
        self.baud_rate = int(self.get_parameter('baud_rate').value)
        self.field_index = int(self.get_parameter('field_index').value)
        self.unit = self.get_parameter('unit').value
        self.stale_timeout_s = float(self.get_parameter('stale_timeout_s').value)
        self.reconnect_period_s = float(self.get_parameter('reconnect_period_s').value)
        self.open_settle_s = float(self.get_parameter('open_settle_s').value)
        self.simulate = bool(self.get_parameter('simulate').value)
        self.simulation_breath_period_s = float(
            self.get_parameter('simulation_breath_period_s').value)
        max_rate = max(1.0, float(self.get_parameter('max_publish_rate_hz').value))
        self._min_publish_interval = 1.0 / max_rate

        self.info_pub = self.create_publisher(String, '/device/info', 10)
        self.raw_pub = self.create_publisher(
            Float32MultiArray, f'/device/{self.device_id}/raw', 10)
        self.info_json = json.dumps({
            'device_id': self.device_id,
            'type': DEVICE_TYPE,
            'capabilities': ['intensity'],
            'units': [self.unit],
        })
        self.create_timer(float(self.get_parameter('info_period_s').value),
                          self._publish_info)

        # Written only by the worker thread; read by the heartbeat timer.
        self._last_sample_time = None
        self._acc_sum = 0.0
        self._acc_count = 0
        self._last_publish_time = 0.0

        self.ser = None
        self._stop_event = threading.Event()
        target = self._simulation_loop if self.simulate else self._serial_loop
        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()

        mode = ('synthetic simulation' if self.simulate
                else f'{self.serial_port} @ {self.baud_rate} baud')
        self.get_logger().info(
            f"Pulmonary device node '{self.device_id}' started ({mode}).")

    # ------------------------------------------------------------------
    # ROS side
    # ------------------------------------------------------------------
    def _publish_info(self):
        """Advertise only while samples are actually flowing."""
        last = self._last_sample_time
        if last is None or time.monotonic() - last > self.stale_timeout_s:
            return
        self.info_pub.publish(String(data=self.info_json))

    def _on_sample(self, value):
        now = time.monotonic()
        self._last_sample_time = now
        self._acc_sum += value
        self._acc_count += 1
        if now - self._last_publish_time < self._min_publish_interval:
            return
        mean = self._acc_sum / self._acc_count
        self._acc_sum = 0.0
        self._acc_count = 0
        self._last_publish_time = now
        self.raw_pub.publish(Float32MultiArray(data=[float(mean)]))

    # ------------------------------------------------------------------
    # Serial worker
    # ------------------------------------------------------------------
    def _connect_serial(self):
        if serial is None:
            self.get_logger().error(
                'pyserial is not installed (apt: python3-serial).',
                throttle_duration_sec=10.0)
            return False
        try:
            self.ser = serial.Serial(self.serial_port, self.baud_rate, timeout=1.0)
            self._stop_event.wait(self.open_settle_s)
            self.ser.reset_input_buffer()
            self.ser.readline()  # drop the partial line left after the flush
            self.get_logger().info(f'Connected to {self.serial_port}.')
            return True
        except (serial.SerialException, OSError) as error:
            self.get_logger().warn(
                f'Cannot open {self.serial_port}: {error}',
                throttle_duration_sec=10.0)
            self._close_serial()
            return False

    def _close_serial(self):
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def _serial_loop(self):
        while not self._stop_event.is_set():
            if self.ser is None:
                if not self._connect_serial():
                    self._stop_event.wait(self.reconnect_period_s)
                continue
            try:
                raw_line = self.ser.readline()
            except Exception as error:  # SerialException, or OSError when unplugged
                self.get_logger().warn(f'Serial read error: {error}. Reconnecting...')
                self._close_serial()
                continue
            if not raw_line:
                continue  # read timeout, nothing arrived
            value = parse_sample(
                raw_line.decode('utf-8', errors='ignore'), self.field_index)
            if value is not None:
                self._on_sample(value)

    # ------------------------------------------------------------------
    # Simulation worker
    # ------------------------------------------------------------------
    def _simulation_loop(self):
        period = max(1.0, self.simulation_breath_period_s)
        started = time.monotonic()
        while not self._stop_event.is_set():
            phase = ((time.monotonic() - started) % period) / period
            # Smooth 0 -> 1 -> 0 effort curve (blow, then relax) plus sensor noise.
            envelope = 0.5 * (1.0 - math.cos(2.0 * math.pi * phase))
            self._on_sample(100.0 + 800.0 * envelope + random.gauss(0.0, 8.0))
            self._stop_event.wait(self._min_publish_interval / 2.0)

    # ------------------------------------------------------------------
    # Clean shutdown
    # ------------------------------------------------------------------
    def destroy_node(self):
        self._stop_event.set()
        if self._worker.is_alive():
            self._worker.join(timeout=3.0)
        self._close_serial()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = PulmonaryDeviceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
