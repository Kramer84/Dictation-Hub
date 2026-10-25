import re

import pytest
import requests
import typer

from dictation_hub.client import remote, uploader
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

    def done(self):
        return False  # a live upload that failed never answers later here


class FakeServer:
    """What the client sees of the server after the live upload: the status
    of the recording (``GET /result/<id>``) and resends of the saved file."""

    def __init__(self):
        self.statuses = [None]  # one per poll, the last one repeats; None = unreachable
        self.uploads = []  # what the resend answers, one per attempt
        self.sent = []

    def fetch_status(self, base_url, recording_id):
        self.status_ids = (base_url, recording_id)
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def upload_file(self, url, path, timeout=None):
        self.sent.append((url, path.read_bytes()))
        answer = self.uploads.pop(0) if self.uploads else requests.ConnectionError("link down")
        if isinstance(answer, Exception):
            raise answer
        return answer

    def resend(self, url, path):
        return FakeResend(self, url, path)


SENDING = object()  # a resend that is still on its way


class FakeResend:
    """Stand-in for remote.BackgroundUpload: done at once, unless the answer is SENDING."""

    def __init__(self, server, url, path):
        self.sending = bool(server.uploads) and server.uploads[0] is SENDING
        if self.sending:
            server.sent.append((url, path.read_bytes()))
            server.uploads.pop(0)
            self.sent = 0
        else:
            try:
                self.answer = server.upload_file(url, path)
            except Exception as e:
                self.answer = e

    def done(self):
        return not self.sending

    def progress(self):
        if self.sending and getattr(self, "growing", False):
            self.sent += 1000
        return self.sent if self.sending else 0

    def result(self, timeout=None):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


DONE = {"state": "done", "result": {"raw_text": "salut", "final_text": "Salut."}}


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
    monkeypatch.setattr(remote, "RETRY_DELAY", 0)
    monkeypatch.setattr(remote, "POLL_INTERVAL", 0)
    monkeypatch.setattr(remote, "STALL_TIMEOUT", 0)
    monkeypatch.setattr(remote, "machine_name", lambda: "laptop")
    server = FakeServer()
    monkeypatch.setattr(remote, "fetch_status", server.fetch_status)
    monkeypatch.setattr(remote, "upload_file", server.upload_file)
    monkeypatch.setattr(remote, "BackgroundUpload", server.resend)
    monkeypatch.setattr(console, "copy_to_clipboard", clipboard.append)

    def run(outcome="process", **kwargs):
        monkeypatch.setattr(remote, "record_from_mic", fake_mic(outcome))
        remote.run_client("standard", {"language": "fr"}, "http://100.71.34.52:8000", **kwargs)
        return clipboard, list((tmp_path / "spool").glob("*.wav"))

    run.server = server
    return run


def test_live_recording_is_streamed_and_delivered(client):
    clipboard, spooled = client()
    (upload,) = FakeUpload.instances
    assert re.fullmatch(
        r"http://100\.71\.34\.52:8000/transcribe\?profile=standard&language=fr&recording_id=laptop-\d{8}_\d{6}_standard",
        upload.url,
    )
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
    client.server.statuses = [{"state": "unknown"}]
    client.server.uploads = [{"raw_text": "salut", "final_text": "Salut."}]
    monkeypatch.setattr(remote, "record_from_mic", lambda *a, **kw: pytest.fail("recorded"))
    clipboard, _ = client(audio_file=wav)
    ((url, body),) = client.server.sent
    assert re.fullmatch(
        r"http://100\.71\.34\.52:8000/transcribe\?profile=standard&language=fr&recording_id=file-[0-9a-f]{16}", url
    )
    assert body == b"RIFF-take"
    assert clipboard == ["Salut."]


def test_client_url_comes_from_the_config(monkeypatch):
    monkeypatch.setenv("SERVER_PROTOCOL", "http")
    monkeypatch.setenv("SERVER_IP", "100.71.34.52")
    monkeypatch.setenv("SERVER_PORT", "8000")
    assert remote.client_base_url() == "http://100.71.34.52:8000"


def test_lost_answer_is_fetched_from_the_server_without_resending(client):
    """The live upload's connection died after the server got everything."""
    FakeUpload.answer = requests.ReadTimeout("Read timed out.")
    client.server.statuses = [DONE]
    clipboard, spooled = client()
    assert client.server.sent == []
    assert client.server.status_ids[1] == FakeUpload.instances[0].url.rsplit("recording_id=", 1)[1]
    assert clipboard == ["Salut."] and spooled == []


def test_recording_the_server_never_got_is_resent(client):
    """The case of 2026-10-25: a read timeout, and the server knows nothing of it."""
    FakeUpload.answer = requests.ReadTimeout("Read timed out.")
    client.server.statuses = [{"state": "unknown"}]
    client.server.uploads = [{"raw_text": "salut", "final_text": "Salut."}]
    clipboard, spooled = client()
    ((url, body),) = client.server.sent
    assert url == FakeUpload.instances[0].url and body == b"RIFF-recording"
    assert clipboard == ["Salut."] and spooled == []


def test_slow_processing_is_waited_for_not_resent(client):
    FakeUpload.answer = TimeoutError("No answer from the dictation server.")
    client.server.statuses = [{"state": "processing"}, {"state": "processing"}, DONE]
    clipboard, _ = client()
    assert client.server.sent == [] and clipboard == ["Salut."]


def test_upload_still_arriving_is_waited_for_not_resent(client):
    FakeUpload.answer = TimeoutError("No answer from the dictation server.")
    client.server.statuses = [
        {"state": "receiving", "received": 1000},
        {"state": "receiving", "received": 2000},
        {"state": "processing"},
        DONE,
    ]
    clipboard, _ = client()
    assert client.server.sent == [] and clipboard == ["Salut."]


def test_upload_stuck_on_the_server_is_resent(client):
    FakeUpload.answer = uploader.UploadStalled("no progress")
    client.server.statuses = [{"state": "receiving", "received": 1000}, {"state": "receiving", "received": 1000}]
    client.server.uploads = [{"raw_text": "salut", "final_text": "Salut."}]
    clipboard, _ = client()
    assert len(client.server.sent) == 1 and clipboard == ["Salut."]


def test_resend_that_times_out_while_the_server_works_is_not_sent_again(client):
    FakeUpload.answer = requests.ReadTimeout("Read timed out.")
    client.server.statuses = [{"state": "unknown"}, {"state": "processing"}, DONE]
    client.server.uploads = [requests.ReadTimeout("Read timed out.")]
    clipboard, _ = client()
    assert len(client.server.sent) == 1 and clipboard == ["Salut."]


def test_resend_is_retried_and_the_recording_kept_when_it_keeps_failing(client, capsys):
    FakeUpload.answer = uploader.UploadStalled("no progress")
    with pytest.raises(typer.Exit):
        client()
    assert len(client.server.sent) == 3
    spooled = list(remote.get_config_dir().joinpath("spool").glob("*.wav"))
    assert len(spooled) == 1 and f"-f {spooled[0]}" in capsys.readouterr().out


def test_resending_a_spool_file_reuses_its_recording_id(client, tmp_path, monkeypatch):
    """`dictation-hub standard -f <spool>` gets the stored result if the server has it."""
    spool = tmp_path / "spool" / "20261025_192125_standard.wav"
    spool.parent.mkdir()
    spool.write_bytes(b"RIFF-recording")
    client.server.statuses = [DONE]
    monkeypatch.setattr(remote, "record_from_mic", lambda *a, **kw: pytest.fail("recorded"))
    clipboard, _ = client(audio_file=spool)
    assert client.server.status_ids[1] == "laptop-20261025_192125_standard"
    assert client.server.sent == [] and clipboard == ["Salut."]


def test_file_upload_succeeds_after_a_transient_failure(client, tmp_path):
    wav = tmp_path / "take.wav"
    wav.write_bytes(b"RIFF-take")
    client.server.statuses = [{"state": "unknown"}]
    client.server.uploads = [requests.ConnectionError("blip"), {"raw_text": "salut", "final_text": "Salut."}]
    clipboard, _ = client(audio_file=wav)
    assert len(client.server.sent) == 2 and clipboard == ["Salut."]


def test_resend_still_on_its_way_is_not_sent_again(client):
    """A resend over a slow link keeps going while the server reports progress."""
    FakeUpload.answer = requests.ReadTimeout("Read timed out.")
    client.server.statuses = [
        {"state": "unknown"},
        {"state": "receiving", "received": 1000},
        {"state": "receiving", "received": 2000},
        {"state": "processing"},
        DONE,
    ]
    client.server.uploads = [SENDING]
    clipboard, _ = client()
    assert len(client.server.sent) == 1 and clipboard == ["Salut."]


def test_resend_still_sending_counts_as_progress_when_the_server_cannot_be_asked(client, monkeypatch):
    """Status checks fail on a bad link, but the resend's own bytes keep moving."""
    FakeUpload.answer = requests.ReadTimeout("Read timed out.")
    monkeypatch.setattr(FakeResend, "growing", True, raising=False)
    client.server.statuses = [{"state": "unknown"}, None, None, None, DONE]
    client.server.uploads = [SENDING]
    clipboard, _ = client()
    assert len(client.server.sent) == 1 and clipboard == ["Salut."]
