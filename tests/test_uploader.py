import json
import shutil
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from dictation_hub.client import uploader
from dictation_hub.core import session
from dictation_hub.server import main

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

PCM_BLOCK = b"\x10\x00\xf0\xff" * 800  # 100 ms of 16 kHz mono


class EchoPipeline:
    def __init__(self, workspace_dir, **kwargs):
        self.workspace_dir = workspace_dir

    def execute(self, json_path):
        ts = json.loads((self.workspace_dir / "metadata.json").read_text())["timestamp"]
        (self.workspace_dir / f"{ts}_raw.txt").write_text("hello there")
        return "Hello there."


def fake_whisper(input_wav, config, output_base):
    Path(f"{output_base}.json").write_text(
        json.dumps({"result": {"language": "en"}, "transcription": [{"text": " hello there"}]})
    )


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    store = tmp_path / "store"
    monkeypatch.setattr(main.static_config.storage, "base_dir", store)
    monkeypatch.setattr(session, "whisper_transcribe", fake_whisper)
    monkeypatch.setattr(session, "setup_logging", lambda ws: None)
    monkeypatch.setitem(session.PIPELINE_MAP, "standard", EchoPipeline)
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", store
    server.should_exit = True
    thread.join(timeout=5)


def test_streamed_recording_is_transcribed(live_server):
    url, store = live_server
    upload = uploader.ServerUpload(f"{url}/transcribe?profile=standard")
    for _ in range(10):
        upload(PCM_BLOCK)
    upload(None)
    result = upload.result(timeout=30)
    assert result["final_text"] == "Hello there."
    (workspace,) = store.iterdir()
    (client_wav,) = workspace.glob("*_client.wav")
    assert client_wav.read_bytes().endswith(PCM_BLOCK * 10)


def test_aborted_upload_leaves_nothing_on_the_server(live_server):
    url, store = live_server
    upload = uploader.ServerUpload(f"{url}/transcribe?profile=standard")
    for _ in range(5):
        upload(PCM_BLOCK)
    time.sleep(0.3)
    upload.abort()
    with pytest.raises(uploader.UploadAborted):
        upload.result(timeout=10)
    deadline = time.time() + 5
    while time.time() < deadline and store.exists() and any(store.iterdir()):
        time.sleep(0.05)
    assert not store.exists() or not any(store.iterdir())


def test_server_health_probe(live_server):
    url, _ = live_server
    assert uploader.server_is_up(url)
    assert not uploader.server_is_up("http://127.0.0.1:9")


def test_upload_is_chunked_while_streaming(live_server, monkeypatch):
    url, store = live_server
    calls = []

    def counting_whisper(input_wav, config, output_base):
        calls.append(time.time())
        fake_whisper(input_wav, config, output_base)

    monkeypatch.setattr(session, "whisper_transcribe", counting_whisper)
    monkeypatch.setattr(session, "speech_pauses", lambda wav, whisper_dir, vad_model: [(24.0, 1.0)])
    upload = uploader.ServerUpload(f"{url}/transcribe?profile=standard")
    for _ in range(700):  # 70 s of audio
        upload(PCM_BLOCK)
    deadline = time.time() + 30
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.1)
    assert len(calls) >= 2  # transcribed before the upload ended
    upload(None)
    result = upload.result(timeout=60)
    assert result["final_text"] == "Hello there."
    assert len(calls) == 3


def test_stalled_connection_is_reported_instead_of_waiting_for_the_timeout():
    # A server that accepts the connection but never reads: sends block once the buffers are full.
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    upload = uploader.ServerUpload(f"http://127.0.0.1:{port}/transcribe", stall_timeout=1.0)
    try:
        for _ in range(8000):  # ~25 MB, far more than the socket buffers hold
            upload(PCM_BLOCK)
        upload(None)
        started = time.time()
        with pytest.raises(uploader.UploadStalled):
            upload.result()
        assert time.time() - started < 10
    finally:
        listener.close()


def test_upload_is_done_once_answered(live_server):
    url, _ = live_server
    upload = uploader.ServerUpload(f"{url}/transcribe?profile=standard")
    assert not upload.done()
    upload(PCM_BLOCK)
    upload(None)
    upload.result(timeout=30)
    assert upload.done()


def test_saved_recording_sent_twice_is_transcribed_once(live_server, tmp_path, monkeypatch):
    """Over real HTTP: the second send gets the stored result by recording id."""
    from dictation_hub.client import remote

    url, store = live_server
    monkeypatch.setattr(remote, "get_config_dir", lambda: tmp_path)
    take = tmp_path / "take.wav"
    take.write_bytes(uploader.streaming_wav_header() + PCM_BLOCK * 10)
    first = remote.send_recording(url, "standard", {}, take)
    workspaces = sorted(p for p in store.iterdir() if not p.name.startswith("."))
    second = remote.send_recording(url, "standard", {}, take)
    assert first["final_text"] == second["final_text"] == "Hello there."
    assert sorted(p for p in store.iterdir() if not p.name.startswith(".")) == workspaces
