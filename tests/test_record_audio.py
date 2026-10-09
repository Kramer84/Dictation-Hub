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


def write_signal(path, buzz: bool, seconds=3, rate=16000):
    """Noise standing in for speech, optionally plus a GSM-style 217 Hz pulse train."""
    import random

    rnd = random.Random(0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for n in range(rate * seconds):
            sample = rnd.gauss(0, 1500)
            if buzz and (n * 217 // rate) % 2 == 0 and (n * 217 / rate) % 1 < 0.125:
                sample += 6000  # TDMA burst: 1/8 duty cycle at 217 Hz
            frames += struct.pack("<h", max(-32768, min(32767, int(sample))))
        w.writeframes(bytes(frames))


def test_gsm_buzz_is_detected_only_when_present(tmp_path):
    write_signal(tmp_path / "clean.wav", buzz=False)
    write_signal(tmp_path / "buzz.wav", buzz=True)
    assert record_audio.gsm_buzz_level(tmp_path / "clean.wav") < 3
    assert record_audio.gsm_buzz_level(tmp_path / "buzz.wav") > 6


def test_prepare_audio_removes_detected_buzz(tmp_path, caplog):
    write_signal(tmp_path / "buzz.wav", buzz=True)
    record_audio.prepare_audio(tmp_path / "buzz.wav", tmp_path / "out.wav")
    assert record_audio.gsm_buzz_level(tmp_path / "out.wav") < 3
    warnings = [r.getMessage().lower() for r in caplog.records if r.levelname == "WARNING"]
    assert any("phone" in w for w in warnings)


def test_prepare_audio_leaves_clean_audio_unfiltered(tmp_path, caplog):
    write_signal(tmp_path / "clean.wav", buzz=False)
    record_audio.prepare_audio(tmp_path / "clean.wav", tmp_path / "out.wav")
    assert "phone" not in caplog.text.lower()


def test_prepare_audio_prints_nothing(tmp_path, capsys):
    write_signal(tmp_path / "clean.wav", buzz=False)
    record_audio.prepare_audio(tmp_path / "clean.wav", tmp_path / "out.wav")
    assert capsys.readouterr().out == ""
