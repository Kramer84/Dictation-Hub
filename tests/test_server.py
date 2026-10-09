import json
import math
import shutil
import struct
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dictation_hub.core import session
from dictation_hub.server import main

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def tone_wav_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(
            b"".join(struct.pack("<h", int(3000 * math.sin(n / 5))) for n in range(16000))
        )
    return path.read_bytes()


class RecordingPipeline:
    """Stand-in for a profile pipeline: writes the scrubbed text, returns a final text."""

    instances = []

    def __init__(self, workspace_dir, **kwargs):
        self.workspace_dir = workspace_dir
        self.language = json.loads((workspace_dir / "metadata.json").read_text())["language"]
        RecordingPipeline.instances.append(self)

    def execute(self, json_path):
        ts = json.loads((self.workspace_dir / "metadata.json").read_text())["timestamp"]
        (self.workspace_dir / f"{ts}_raw.txt").write_text("bonjour tout le monde")
        return "Bonjour tout le monde."


class FailingPipeline(RecordingPipeline):
    def execute(self, json_path):
        super().execute(json_path)
        raise ConnectionError("Ollama unreachable")


def fake_whisper(input_wav, config, output_base):
    Path(f"{output_base}.json").write_text(
        json.dumps(
            {
                "params": {"language": "auto"},
                "result": {"language": "fr"},
                "transcription": [{"text": " bonjour tout le monde"}],
            }
        )
    )


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(main.static_config.storage, "base_dir", tmp_path / "store")
    monkeypatch.setattr(session, "whisper_transcribe", fake_whisper)
    monkeypatch.setitem(session.PIPELINE_MAP, "standard", RecordingPipeline)
    RecordingPipeline.instances.clear()
    return TestClient(main.app, client=("127.0.0.1", 50000))


def test_detected_language_reaches_the_pipeline(server, tmp_path):
    resp = server.post("/transcribe?profile=standard", content=tone_wav_bytes(tmp_path))
    assert resp.status_code == 200
    assert resp.json()["final_text"] == "Bonjour tout le monde."
    assert RecordingPipeline.instances[-1].language == "fr"


@pytest.mark.parametrize("profile", ["../../escape", "no_such_profile"])
def test_unknown_profile_is_rejected_without_touching_disk(server, tmp_path, profile):
    resp = server.post(f"/transcribe?profile={profile}", content=tone_wav_bytes(tmp_path))
    assert resp.status_code == 400
    assert not (tmp_path / "store").exists()
    assert not (tmp_path / "escape").exists()


def test_llm_failure_falls_back_to_scrubbed_text(server, tmp_path, monkeypatch):
    monkeypatch.setitem(session.PIPELINE_MAP, "standard", FailingPipeline)
    resp = server.post("/transcribe?profile=standard", content=tone_wav_bytes(tmp_path))
    assert resp.status_code == 200
    body = resp.json()
    assert body["raw_text"] == "bonjour tout le monde"
    assert body["final_text"] == "bonjour tout le monde"
    assert "Ollama unreachable" in body["error"]


def test_websocket_rejects_unknown_profile(server):
    with server.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"profile": "../../escape", "language": "auto"})
        assert "error" in ws.receive_json()


def test_dispatcher_cli_reports_missing_metadata(tmp_path, monkeypatch, capsys):
    from dictation_hub.server import n8n_dispatcher

    monkeypatch.setattr("sys.argv", ["n8n_dispatcher", "--workspace", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        n8n_dispatcher.main()
    assert exc.value.code == 1
    assert "No metadata.json" in capsys.readouterr().out


def test_whisper_failure_returns_an_error_instead_of_crashing(server, tmp_path, monkeypatch):
    import typer

    def failing_whisper(**kwargs):
        raise typer.Exit(code=1)

    monkeypatch.setattr(session, "whisper_transcribe", failing_whisper)
    resp = server.post("/transcribe?profile=standard", content=tone_wav_bytes(tmp_path))
    assert resp.status_code == 200
    assert resp.json()["error"] == "Whisper execution failed."


def test_health_endpoint(server):
    resp = server.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_aborted_upload_leaves_no_workspace(tmp_path, monkeypatch):
    """A client that disconnects mid-upload (q/c keys) leaves nothing behind."""
    import asyncio
    from starlette.requests import ClientDisconnect

    monkeypatch.setattr(main.static_config.storage, "base_dir", tmp_path / "store")

    class AbortedRequest:
        query_params = {"profile": "standard"}

        async def stream(self):
            yield b"RIFF" + b"\x00" * 200
            raise ClientDisconnect()

    result = asyncio.run(main.transcribe(AbortedRequest()))
    assert result.status_code == 499
    assert list((tmp_path / "store").iterdir()) == []
