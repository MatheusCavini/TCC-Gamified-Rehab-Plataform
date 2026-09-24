"""Streaming EMG band-pass filtering and RMS-envelope extraction."""

from collections import deque
import math


class EMGPreprocessor:
    """Stateful 20--450 Hz band-pass filter followed by rolling RMS.

    Input blocks are channel-major, matching ``noraxon_emg_node``.  Filter
    state and RMS windows survive from one ROS message to the next, which is
    required when the acquisition SDK delivers arbitrary transfer sizes.
    """

    def __init__(self, sample_rate_hz=1000.0, highpass_hz=20.0,
                 lowpass_hz=450.0, rms_window_ms=100.0):
        self.sample_rate_hz = None
        self.highpass_hz = None
        self.lowpass_hz = None
        self.rms_window_ms = None
        self._filters = []
        self._rms_windows = []
        self.configure(sample_rate_hz, highpass_hz, lowpass_hz, rms_window_ms)

    def configure(self, sample_rate_hz, highpass_hz, lowpass_hz, rms_window_ms):
        sample_rate_hz = float(sample_rate_hz)
        highpass_hz = float(highpass_hz)
        lowpass_hz = float(lowpass_hz)
        rms_window_ms = float(rms_window_ms)
        if sample_rate_hz <= 0 or rms_window_ms <= 0:
            raise ValueError('sample rate and RMS window must be positive')
        nyquist = sample_rate_hz / 2.0
        if not 0 < highpass_hz < lowpass_hz < nyquist:
            raise ValueError(
                f'EMG filter requires 0 < highpass < lowpass < Nyquist ({nyquist:g} Hz)')
        changed = (sample_rate_hz != self.sample_rate_hz or
                   highpass_hz != self.highpass_hz or
                   lowpass_hz != self.lowpass_hz or
                   rms_window_ms != self.rms_window_ms)
        self.sample_rate_hz = sample_rate_hz
        self.highpass_hz = highpass_hz
        self.lowpass_hz = lowpass_hz
        self.rms_window_ms = rms_window_ms
        self._coefficients = (
            self._biquad_coefficients('highpass', highpass_hz),
            self._biquad_coefficients('lowpass', lowpass_hz),
        )
        if changed:
            self._filters = []
            self._rms_windows = []

    def _biquad_coefficients(self, kind, cutoff_hz):
        omega = 2.0 * math.pi * cutoff_hz / self.sample_rate_hz
        alpha = math.sin(omega) / (2.0 * math.sqrt(0.5))  # Butterworth Q.
        cosine = math.cos(omega)
        if kind == 'highpass':
            b0, b1, b2 = (1.0 + cosine) / 2.0, -(1.0 + cosine), (1.0 + cosine) / 2.0
        else:
            b0, b1, b2 = (1.0 - cosine) / 2.0, 1.0 - cosine, (1.0 - cosine) / 2.0
        a0, a1, a2 = 1.0 + alpha, -2.0 * cosine, 1.0 - alpha
        return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    def _ensure_channels(self, count):
        if count == len(self._filters):
            return
        window_samples = max(1, round(self.sample_rate_hz * self.rms_window_ms / 1000.0))
        self._filters = [[[0.0, 0.0, 0.0, 0.0] for _ in self._coefficients]
                         for _ in range(count)]
        self._rms_windows = [deque(maxlen=window_samples) for _ in range(count)]

    @staticmethod
    def _apply_biquad(value, coefficients, state):
        b0, b1, b2, a1, a2 = coefficients
        x1, x2, y1, y2 = state
        result = b0 * value + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        state[:] = [value, x1, result, y1]
        return result

    @staticmethod
    def _decode_channel_major(msg):
        data = list(msg.data)
        dims = msg.layout.dim
        if len(dims) >= 2:
            channels, samples = int(dims[0].size), int(dims[1].size)
            if channels > 0 and samples > 0 and channels * samples <= len(data):
                return [data[index * samples:(index + 1) * samples]
                        for index in range(channels)]
        # A layout-less topic is treated as one sample for each channel. This
        # keeps legacy producers usable while Noraxon uses the schema above.
        return [[value] for value in data]

    def process(self, msg):
        """Return one RMS value per channel after filtering its input block."""
        blocks = self._decode_channel_major(msg)
        if not blocks:
            return []
        self._ensure_channels(len(blocks))
        rms_values = []
        for index, samples in enumerate(blocks):
            window = self._rms_windows[index]
            highpass_state, lowpass_state = self._filters[index]
            for raw_value in samples:
                value = float(raw_value)
                if not math.isfinite(value):
                    continue
                value = self._apply_biquad(value, self._coefficients[0], highpass_state)
                value = self._apply_biquad(value, self._coefficients[1], lowpass_state)
                window.append(value)
            rms_values.append(math.sqrt(sum(value * value for value in window) / len(window))
                              if window else 0.0)
        return rms_values
