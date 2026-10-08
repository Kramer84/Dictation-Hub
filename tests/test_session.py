import json
import shutil
import wave
from pathlib import Path

import pytest

from dictation_hub.core import session
from dictation_hub.pipeline.core.static_config import WhisperPipelineConfig

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

REPO = Path(__file__).resolve().parents[1]
TS = "20261008_140000"


def write_tone(path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x10\x00\xf0\xff" * 8000)


class EchoPipeline:
    def __init__(self, workspace_dir, static_config, **kwargs):
        self.workspace_dir = workspace_dir
        self.language = json.loads((workspace_dir / "metadata.json").read_text())["language"]

    def execute(self, json_path):
        (self.workspace_dir / f"{TS}_raw.txt").write_text("bonjour")
        return f"Bonjour ({self.language})."


@pytest.fixture
def setup(tmp_path, monkeypatch):
    seen = {}

    def fake_whisper(input_wav, config, output_base):
        seen["input"] = input_wav
        seen["env"] = Path(config).read_text()
        Path(f"{output_base}.json").write_text(
            json.dumps({"result": {"language": "fr"}, "transcription": [{"text": " bonjour"}]})
        )

    monkeypatch.setattr(session, "whisper_transcribe", fake_whisper)
    monkeypatch.setattr(session, "setup_logging", lambda ws: None)
    monkeypatch.setitem(session.PIPELINE_MAP, "standard", EchoPipeline)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    capture = workspace / f"{TS}_capture.wav"
    write_tone(capture)
    static = WhisperPipelineConfig.load_from_file(REPO / "configs" / "static.json")
    pipeline_config = {"profiles": {"standard": {"env": "standard.env"}}, "user_information": {}}

    def run(overrides=None, valid_args=("language",)):
        return session.process_capture(
            capture=capture,
            workspace=workspace,
            timestamp=TS,
            profile="standard",
            pipeline_config=pipeline_config,
            static_config=static,
            overrides=overrides or {},
            valid_args=list(valid_args),
        )

    return run, workspace, seen


def test_capture_becomes_a_completed_workspace(setup):
    run, workspace, seen = setup
    result = run()
    assert result.final_text == "Bonjour (fr)."
    assert result.raw_text == "bonjour"
    assert seen["input"] == workspace / f"{TS}_raw.wav"
    names = {p.name for p in workspace.iterdir()}
    assert {f"{TS}_raw.wav", f"{TS}_final.txt", "metadata.json", ".completed"} <= names


def test_overrides_reach_whisper_and_temp_env_is_removed(setup, tmp_path):
    run, _, seen = setup
    run({"language": "fr"})
    assert 'LANGUAGE="fr"' in seen["env"]


def test_missing_transcript_raises_transcription_error(setup, monkeypatch):
    run, workspace, _ = setup
    monkeypatch.setattr(session, "whisper_transcribe", lambda **kw: None)
    with pytest.raises(session.TranscriptionError):
        run()
    assert not (workspace / ".completed").exists()
