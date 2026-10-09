import pytest
import typer

from dictation_hub.client import remote
from dictation_hub.core import console


class FakeUpload:
    """Stand-in for client.uploader.ServerUpload."""

    instances = []
    answer = {"raw_text": "salut", "final_text": "Salut."}

    def __init__(self, url, **kwargs):
        self.url, self.blocks, self.aborted = url, [], False
        FakeUpload.instances.append(self)

    def __call__(self, block):
        self.blocks.append(block)

    def abort(self):
        self.aborted = True

    def result(self, timeout=None):
        if isinstance(FakeUpload.answer, Exception):
            raise FakeUpload.answer
        return FakeUpload.answer


def fake_mic(outcome):
    def record(capture, listeners=()):
        capture.write_bytes(b"RIFF-recording")
        for listener in listeners:
            listener(b"\x00\x00" * 1600)
            if outcome == "process":
                listener(None)
        return outcome

    return record


@pytest.fixture
def client(tmp_path, monkeypatch):
    FakeUpload.instances.clear()
    FakeUpload.answer = {"raw_text": "salut", "final_text": "Salut."}
    clipboard = []
    monkeypatch.setattr(remote, "ServerUpload", FakeUpload)
    monkeypatch.setattr(remote, "server_is_up", lambda base, timeout=2: True)
    monkeypatch.setattr(remote, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(console, "copy_to_clipboard", clipboard.append)

    def run(outcome="process", **kwargs):
        monkeypatch.setattr(remote, "record_from_mic", fake_mic(outcome))
        remote.run_client("standard", {"language": "fr"}, "http://100.71.34.52:8000", **kwargs)
        return clipboard, list((tmp_path / "spool").glob("*.wav"))

    return run


def test_live_recording_is_streamed_and_delivered(client):
    clipboard, spooled = client()
    (upload,) = FakeUpload.instances
    assert upload.url == "http://100.71.34.52:8000/transcribe?profile=standard&language=fr"
    assert upload.blocks[-1] is None and not upload.aborted
    assert clipboard == ["Salut."]
    assert spooled == []  # the server keeps the audio


def test_quit_keeps_the_recording_and_says_how_to_send_it(client, capsys):
    clipboard, spooled = client("quit")
    assert FakeUpload.instances[0].aborted
    assert clipboard == [] and len(spooled) == 1
    assert f"-f {spooled[0]}" in capsys.readouterr().out


def test_cancel_discards_everything(client):
    clipboard, spooled = client("cancel")
    assert FakeUpload.instances[0].aborted
    assert clipboard == [] and spooled == []


def test_server_failure_keeps_the_recording(client, capsys):
    FakeUpload.answer = ConnectionError("server went away")
    with pytest.raises(typer.Exit) as exc:
        client()
    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    spooled = list(remote.get_config_dir().joinpath("spool").glob("*.wav"))
    assert len(spooled) == 1 and f"-f {spooled[0]}" in out


def test_server_answer_without_text_is_a_failure(client):
    FakeUpload.answer = {"raw_text": "", "final_text": "", "error": "Whisper execution failed."}
    with pytest.raises(typer.Exit):
        client()
    assert len(list(remote.get_config_dir().joinpath("spool").glob("*.wav"))) == 1


def test_unreachable_server_fails_before_recording(client, monkeypatch, capsys):
    monkeypatch.setattr(remote, "server_is_up", lambda base, timeout=2: False)
    monkeypatch.setattr(remote, "record_from_mic", lambda *a, **kw: pytest.fail("recorded"))
    with pytest.raises(typer.Exit) as exc:
        remote.run_client("standard", {}, "http://100.71.34.52:8000")
    assert exc.value.exit_code == 1
    assert "100.71.34.52" in capsys.readouterr().out


def test_file_is_uploaded_instead_of_recording(client, tmp_path, monkeypatch):
    wav = tmp_path / "take.wav"
    wav.write_bytes(b"RIFF-take")
    uploaded = []

    def upload_file(url, path):
        uploaded.append((url, path.read_bytes()))
        return {"raw_text": "salut", "final_text": "Salut."}

    monkeypatch.setattr(remote, "upload_file", upload_file)
    monkeypatch.setattr(remote, "record_from_mic", lambda *a, **kw: pytest.fail("recorded"))
    clipboard, _ = client(audio_file=wav)
    assert uploaded == [("http://100.71.34.52:8000/transcribe?profile=standard&language=fr", b"RIFF-take")]
    assert clipboard == ["Salut."]


def test_client_url_comes_from_the_config(monkeypatch):
    monkeypatch.setenv("SERVER_PROTOCOL", "http")
    monkeypatch.setenv("SERVER_IP", "100.71.34.52")
    monkeypatch.setenv("SERVER_PORT", "8000")
    assert remote.client_base_url() == "http://100.71.34.52:8000"
