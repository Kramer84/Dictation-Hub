import os
import sys
import time
import wave

import pytest

from dictation_hub.core import recorder

# Stand-in for arecord: writes 16 kHz mono silence (raw PCM) to stdout until
# SIGINT, like `arecord -t raw`.
FAKE_ARECORD = r"""
import signal, sys, time
stop = False
def handle(*_):
    global stop
    stop = True
signal.signal(signal.SIGINT, handle)
out = sys.stdout.buffer
while not stop:
    out.write(b"\x01\x00" * 160)  # 10 ms
    out.flush()
    time.sleep(0.01)
"""


def fake_capture_command():
    return [sys.executable, "-c", FAKE_ARECORD]


def wav_seconds(path):
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels()) == (16000, 1)
        return w.getnframes() / w.getframerate()


def test_capture_command_streams_16k_mono_pcm():
    cmd = recorder.arecord_command()
    assert cmd[0] == "arecord"
    assert cmd[cmd.index("-r") + 1] == "16000" and cmd[cmd.index("-c") + 1] == "1"
    assert cmd[cmd.index("-t") + 1] == "raw"


def test_recorder_survives_terminal_signals_and_finalizes_wav(tmp_path):
    rec = recorder.Recorder(tmp_path / "capture.wav", fake_capture_command)
    rec.start()
    time.sleep(0.5)
    assert os.getsid(rec.process.pid) != os.getsid(0)  # own session: no terminal Ctrl-C
    rec.stop()
    assert 0.2 < wav_seconds(tmp_path / "capture.wav") < 1.0


def test_capture_file_is_readable_while_recording(tmp_path):
    rec = recorder.Recorder(tmp_path / "capture.wav", fake_capture_command)
    rec.start()
    time.sleep(0.5)
    assert wav_seconds(tmp_path / "capture.wav") > 0.2  # raw audio is on disk already
    rec.stop()


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


def test_listeners_receive_exactly_the_recorded_audio_then_end(tmp_path):
    received = []
    rec = recorder.Recorder(tmp_path / "capture.wav", fake_capture_command, listeners=[received.append])
    rec.start()
    time.sleep(0.4)
    rec.pause()
    time.sleep(0.4)
    rec.stop()
    assert received[-1] is None  # end of stream
    with wave.open(str(tmp_path / "capture.wav")) as w:
        assert b"".join(received[:-1]) == w.readframes(w.getnframes())


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

    def stop(self, send_end=True):
        self.calls.append("stop" if send_end else "stop-no-end")

    def is_alive(self):
        return self.alive


@pytest.mark.parametrize(
    "keys, outcome, calls",
    [
        ([" "], "process", ["start", "stop"]),
        (["x", None, " "], "process", ["start", "stop"]),
        (["p", "p", " "], "process", ["start", "pause", "resume", "stop"]),
        (["q"], "quit", ["start", "stop-no-end"]),
        (["c"], "cancel", ["start", "stop-no-end"]),
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
    assert rec.calls == ["start", "stop-no-end"]


def test_capture_dying_stops_with_error():
    rec = FakeRecorder()
    rec.alive = False
    assert recorder.control_recording(rec, iter([None, None])) == "failed"
    assert rec.calls == ["start", "stop-no-end"]


def test_stop_without_end_does_not_signal_listeners(tmp_path):
    received = []
    rec = recorder.Recorder(tmp_path / "capture.wav", fake_capture_command, listeners=[received.append])
    rec.start()
    time.sleep(0.3)
    rec.stop(send_end=False)
    assert None not in received
    assert wav_seconds(tmp_path / "capture.wav") > 0.1  # audio still kept locally


ODD_WRITER = r"""
import signal, sys, time
stop = False
def handle(*_):
    global stop
    stop = True
signal.signal(signal.SIGINT, handle)
out = sys.stdout.buffer
n = 0
while not stop:
    out.write(bytes([n % 256]) * 3)  # odd-sized writes split samples
    out.flush()
    n += 1
    time.sleep(0.003)
"""


def test_blocks_stay_sample_aligned(tmp_path):
    received = []
    rec = recorder.Recorder(
        tmp_path / "capture.wav", lambda: [sys.executable, "-c", ODD_WRITER], listeners=[received.append]
    )
    rec.start()
    time.sleep(0.3)
    rec.pause()
    time.sleep(0.05)
    rec.resume()
    time.sleep(0.3)
    rec.stop()
    assert all(len(b) % 2 == 0 for b in received[:-1])


def test_capture_command_can_be_overridden_for_testing(monkeypatch):
    monkeypatch.setenv("DICTATION_CAPTURE_COMMAND", "ffmpeg -re -i 'my take.wav' -f s16le -")
    assert recorder.arecord_command() == ["ffmpeg", "-re", "-i", "my take.wav", "-f", "s16le", "-"]
