import math
import shutil
import struct
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from dictation_hub.core import record_audio

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def fake_mic(output_path: Path, record_format: str, record_type: str) -> None:
    """Stand-in for arecord: 1 s of a quiet 440 Hz tone, 44.1 kHz stereo (arecord -f cd)."""
    rate = 44100
    with wave.open(str(output_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        for n in range(rate):
            sample = int(3000 * math.sin(2 * math.pi * 440 * n / rate))
            w.writeframes(struct.pack("<hh", sample, sample))


def test_capture_is_kept_untouched_next_to_processed_audio(tmp_path):
    processed = tmp_path / "ts_raw.wav"
    capture = tmp_path / "ts_capture.wav"
    reference = tmp_path / "reference.wav"
    fake_mic(reference, "cd", "wav")

    with patch.object(record_audio, "record_audio", fake_mic):
        record_audio.record_audio_app(
            output=processed,
            raw_output=capture,
            normalize=True,
            remove_silence=False,
            highpass=False,
            record_format="cd",
            record_type="wav",
            sample_rate=16000,
            channels=1,
            codec="pcm_s16le",
            target_peak=-6.0,
        )

    assert capture.read_bytes() == reference.read_bytes()
    with wave.open(str(processed)) as w:
        assert (w.getframerate(), w.getnchannels()) == (16000, 1)
