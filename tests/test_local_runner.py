import json
from pathlib import Path

import pytest

from dictation_hub.core import local_runner


def fake_record(output, raw_output, **kwargs):
    raw_output.write_bytes(b"RIFF-capture")
    output.write_bytes(b"RIFF-processed")


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


@pytest.fixture
def run(tmp_path, monkeypatch):
    clipboard = []
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(local_runner, "create_workspace", lambda *a: (workspace, "20261008_120000"))
    monkeypatch.setattr(local_runner, "record_audio_app", fake_record)
    monkeypatch.setattr(local_runner, "whisper_transcribe", fake_whisper)
    monkeypatch.setattr(local_runner, "setup_logging", lambda ws: None)
    monkeypatch.setattr(local_runner, "copy_to_clipboard", clipboard.append)
    monkeypatch.setitem(local_runner.PIPELINE_MAP, "standard", FailingPipeline)

    def invoke():
        local_runner.run_local_pipeline("standard", {})
        return workspace, clipboard

    return invoke


def test_llm_failure_still_delivers_scrubbed_text(run):
    workspace, clipboard = run()
    assert clipboard == ["salut"]
    assert (workspace / "20261008_120000_final.txt").read_text() == "salut"


def test_raw_capture_is_kept_in_workspace(run):
    workspace, _ = run()
    assert (workspace / "20261008_120000_capture.wav").read_bytes() == b"RIFF-capture"


def test_unknown_profile_fails_before_recording(monkeypatch):
    import typer

    monkeypatch.setattr(local_runner, "record_audio_app", lambda **kw: pytest.fail("recorded"))
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
    with wave.open(str(source), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x10\x00\xf0\xff" * 44100)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    transcribed = []
    clipboard = []
    monkeypatch.setattr(local_runner, "create_workspace", lambda *a: (workspace, "20261008_130000"))
    monkeypatch.setattr(local_runner, "record_audio_app", lambda **kw: pytest.fail("recorded"))
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
