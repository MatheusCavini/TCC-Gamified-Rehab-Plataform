import math
from types import SimpleNamespace

from hal_signal_processing.emg_preprocessor import EMGPreprocessor


def _message(channel_blocks):
    samples = len(channel_blocks[0])
    return SimpleNamespace(
        data=[value for block in channel_blocks for value in block],
        layout=SimpleNamespace(dim=[
            SimpleNamespace(size=len(channel_blocks)),
            SimpleNamespace(size=samples),
        ]),
    )


def test_channel_major_blocks_produce_one_rms_per_channel():
    processor = EMGPreprocessor(
        sample_rate_hz=1000.0,
        highpass_hz=20.0,
        lowpass_hz=450.0,
        rms_window_ms=100.0,
    )
    samples = list(range(200))
    message = _message([
        [100.0 * math.sin(2.0 * math.pi * 100.0 * index / 1000.0) for index in samples],
        [50.0 * math.sin(2.0 * math.pi * 150.0 * index / 1000.0) for index in samples],
    ])

    rms = processor.process(message)

    assert len(rms) == 2
    assert rms[0] > rms[1] > 0.0
    # The pass-band signal remains close to its expected A / sqrt(2) RMS,
    # allowing for the causal filter's initial transient.
    assert 50.0 < rms[0] < 80.0
