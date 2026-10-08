import json
from pathlib import Path

import pytest

from dictation_hub.core import local_runner


def write_tone(path, rate=16000, channels=1, seconds=1):
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x10\x00\xf0\xff" * (rate * channels * seconds // 2))


def fake_mic(outcome):
    def record(capture):
        write_tone(capture)
        return outcome

    return record


def fake_whisper(input_wav, config, output_base):
    Path(f"{output_base}.json").write_text(
        json.dumps({"result": {"language": "fr"}, "transcription": [{"text": " salut"}]})
    )


class FailingPipeline:
    def __init__(self, workspace_dir, **kwargs):
        self.workspace_dir = workspace_dir

    def execute(self, json_path):
        ts = json.loads((self.workspace_dir / "metadata.json").read_text())["timestamp"]
        (self.workspace_dir / f"{ts}_raw.txt").write_text("salut")
        raise TimeoutError("LLM timed out")


pytestmark = pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None, reason="ffmpeg not installed"
)


@pytest.fixture
def run(tmp_path, monkeypatch):
    clipboard = []
    transcribed = []
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(local_runner, "create_workspace", lambda *a: (workspace, "20261008_120000"))
    monkeypatch.setattr(
        local_runner,
        "whisper_transcribe",
        lambda input_wav, config, output_base: (transcribed.append(input_wav), fake_whisper(input_wav, config, output_base)),
    )
    monkeypatch.setattr(local_runner, "setup_logging", lambda ws: None)
    monkeypatch.setattr(local_runner, "copy_to_clipboard", clipboard.append)
    monkeypatch.setitem(local_runner.PIPELINE_MAP, "standard", FailingPipeline)

    def invoke(outcome="process"):
        monkeypatch.setattr(local_runner, "record_from_mic", fake_mic(outcome))
        local_runner.run_local_pipeline("standard", {})
        return workspace, clipboard, transcribed

    return invoke


def test_llm_failure_still_delivers_scrubbed_text(run):
    workspace, clipboard, _ = run()
    assert clipboard == ["salut"]
    assert (workspace / "20261008_120000_final.txt").read_text() == "salut"


def test_raw_capture_is_kept_in_workspace(run):
    workspace, _, transcribed = run()
    assert (workspace / "20261008_120000_capture.wav").is_file()
    assert transcribed == [workspace / "20261008_120000_raw.wav"]


def test_quit_keeps_audio_and_skips_processing(run, capsys):
    workspace, clipboard, transcribed = run("quit")
    capture = workspace / "20261008_120000_capture.wav"
    assert capture.is_file()
    assert transcribed == [] and clipboard == []
    assert f"-f {capture}" in capsys.readouterr().out


def test_cancel_removes_the_workspace(run):
    workspace, clipboard, transcribed = run("cancel")
    assert not workspace.exists()
    assert transcribed == [] and clipboard == []


def test_dead_capture_exits_with_error(run):
    import typer

    with pytest.raises(typer.Exit) as exc:
        run("failed")
    assert exc.value.exit_code == 1


def test_unknown_profile_fails_before_recording(monkeypatch):
    import typer

    monkeypatch.setattr(local_runner, "record_from_mic", lambda capture: pytest.fail("recorded"))
    monkeypatch.setattr(local_runner, "create_workspace", lambda *a: pytest.fail("workspace created"))
    with pytest.raises(typer.Exit) as exc:
        local_runner.run_local_pipeline("standrad", {})
    assert exc.value.exit_code == 2


def test_existing_file_is_reprocessed_without_recording(tmp_path, monkeypatch):
    import shutil
    import wave

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    source = tmp_path / "old_take.wav"
    write_tone(source, rate=44100, channels=2)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    transcribed = []
    clipboard = []
    monkeypatch.setattr(local_runner, "create_workspace", lambda *a: (workspace, "20261008_130000"))
    monkeypatch.setattr(local_runner, "record_from_mic", lambda capture: pytest.fail("recorded"))
    monkeypatch.setattr(
        local_runner,
        "whisper_transcribe",
        lambda input_wav, config, output_base: (transcribed.append(input_wav), fake_whisper(input_wav, config, output_base)),
    )
    monkeypatch.setattr(local_runner, "setup_logging", lambda ws: None)
    monkeypatch.setattr(local_runner, "copy_to_clipboard", clipboard.append)
    monkeypatch.setitem(local_runner.PIPELINE_MAP, "standard", FailingPipeline)

    local_runner.run_local_pipeline("standard", {}, audio_file=source)

    assert (workspace / "20261008_130000_capture.wav").read_bytes() == source.read_bytes()
    with wave.open(str(transcribed[0])) as w:
        assert (w.getframerate(), w.getnchannels()) == (16000, 1)
    assert clipboard == ["salut"]
