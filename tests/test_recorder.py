import os
import sys
import time
import wave

import pytest

from dictation_hub.core import recorder

# Stand-in for arecord: writes 16 kHz mono silence until SIGINT, then closes
# the WAV cleanly (like arecord does) so the header is finalized.
FAKE_ARECORD = r"""
import signal, sys, time, wave
stop = False
def handle(*_):
    global stop
    stop = True
signal.signal(signal.SIGINT, handle)
w = wave.open(sys.argv[1], "wb")
w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
while not stop:
    w.writeframes(b"\x00\x00" * 160)  # 10 ms
    time.sleep(0.01)
w.close()
"""


def fake_capture_command(path):
    return [sys.executable, "-c", FAKE_ARECORD, str(path)]


def wav_seconds(path):
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels()) == (16000, 1)
        return w.getnframes() / w.getframerate()


def test_capture_command_records_16k_mono_directly(tmp_path):
    cmd = recorder.arecord_command(tmp_path / "x.wav")
    assert cmd[0] == "arecord"
    assert {"-r", "16000"} <= set(cmd) and "-c" in cmd and cmd[cmd.index("-c") + 1] == "1"


def test_recorder_survives_terminal_signals_and_finalizes_wav(tmp_path):
    rec = recorder.Recorder(tmp_path / "capture.wav", fake_capture_command)
    rec.start()
    time.sleep(0.5)
    assert os.getsid(rec.process.pid) != os.getsid(0)  # own session: no terminal Ctrl-C
    rec.stop()
    assert 0.2 < wav_seconds(tmp_path / "capture.wav") < 1.0
    assert not list(tmp_path.glob("*.part*"))


def test_pause_excludes_audio_and_resume_appends(tmp_path):
    rec = recorder.Recorder(tmp_path / "capture.wav", fake_capture_command)
    rec.start()
    time.sleep(0.5)
    rec.pause()
    time.sleep(1.0)  # not recorded
    rec.resume()
    time.sleep(0.5)
    rec.stop()
    assert 0.5 < wav_seconds(tmp_path / "capture.wav") < 1.4


class FakeRecorder:
    def __init__(self):
        self.calls = []
        self.alive = True

    def start(self):
        self.calls.append("start")

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")

    def stop(self):
        self.calls.append("stop")

    def is_alive(self):
        return self.alive


@pytest.mark.parametrize(
    "keys, outcome, calls",
    [
        ([" "], "process", ["start", "stop"]),
        (["x", None, " "], "process", ["start", "stop"]),
        (["p", "p", " "], "process", ["start", "pause", "resume", "stop"]),
        (["q"], "quit", ["start", "stop"]),
        (["c"], "cancel", ["start", "stop"]),
        ([], "process", ["start", "stop"]),  # key source closed (e.g. stdin EOF)
    ],
)
def test_keys_drive_the_recording(keys, outcome, calls):
    rec = FakeRecorder()
    assert recorder.control_recording(rec, iter(keys)) == outcome
    assert rec.calls == calls


def test_ctrl_c_quits_and_keeps_audio():
    def keys():
        yield None
        raise KeyboardInterrupt

    rec = FakeRecorder()
    assert recorder.control_recording(rec, keys()) == "quit"
    assert rec.calls == ["start", "stop"]


def test_capture_dying_stops_with_error():
    rec = FakeRecorder()
    rec.alive = False
    assert recorder.control_recording(rec, iter([None, None])) == "failed"
