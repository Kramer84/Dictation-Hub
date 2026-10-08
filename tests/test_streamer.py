import subprocess
import time

import pytest

from dictation_hub.client import streamer


@pytest.fixture
def sleepers():
    procs = [subprocess.Popen(["sleep", "30"]) for _ in range(2)]
    yield procs
    for p in procs:
        p.kill()
        p.wait()


def alive(proc):
    time.sleep(0.1)
    return proc.poll() is None


def test_only_the_recorded_stale_capture_is_killed(tmp_path, sleepers):
    stale, unrelated = sleepers
    pid_file = tmp_path / "client_capture.pid"
    streamer.remember_capture(pid_file, stale.pid, "sleep")

    streamer.kill_stale_capture(pid_file)

    assert not alive(stale)
    assert alive(unrelated)
    assert not pid_file.exists()


def test_reused_pid_running_another_program_is_spared(tmp_path, sleepers):
    proc, _ = sleepers
    pid_file = tmp_path / "client_capture.pid"
    streamer.remember_capture(pid_file, proc.pid, "arecord")

    streamer.kill_stale_capture(pid_file)

    assert alive(proc)


def test_missing_pid_file_is_fine(tmp_path):
    streamer.kill_stale_capture(tmp_path / "absent.pid")


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, "http://h:8000/transcribe?profile=standard"),
        ({"language": "fr"}, "http://h:8000/transcribe?profile=standard&language=fr"),
    ],
)
def test_build_url(overrides, expected):
    assert streamer.build_url("http", "h", "8000", "standard", overrides) == expected


def test_file_is_uploaded_instead_of_recording(tmp_path, monkeypatch):
    import json
    from unittest.mock import MagicMock

    wav = tmp_path / "take.wav"
    wav.write_bytes(b"RIFF-take")
    uploaded, clipboard = [], []

    def fake_upload(audio_stream, url, resp_path):
        uploaded.append((audio_stream.read(), url))
        with open(resp_path, "w") as f:
            json.dump({"raw_text": "salut", "final_text": "Salut."}, f)
        return MagicMock(wait=lambda: 0)

    monkeypatch.setattr(streamer, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(streamer, "launch_audio_backend", lambda log: pytest.fail("mic opened"))
    monkeypatch.setattr(streamer, "stream_audio_to_server", fake_upload)
    monkeypatch.setattr(streamer, "copy_to_clipboard", clipboard.append)

    streamer.run_remote_stream("standard", {"language": "fr"}, "http", "h", "8000", audio_file=wav)

    assert uploaded == [(b"RIFF-take", "http://h:8000/transcribe?profile=standard&language=fr")]
    assert clipboard == ["Salut."]
