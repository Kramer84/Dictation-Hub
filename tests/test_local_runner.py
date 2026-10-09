import json
import wave
import shutil
from pathlib import Path

import pytest

from dictation_hub.core import local_runner, session


def write_tone(path, rate=16000, channels=1, seconds=1):
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x10\x00\xf0\xff" * (rate * channels * seconds // 2))


def fake_mic(outcome):
    def record(capture, listeners=()):
        write_tone(capture)
        for listener in listeners:
            listener(b"\x00\x00" * 1600)
            if outcome == "process":
                listener(None)
        return outcome

    return record


class FakeUpload:
    """Stand-in for client.uploader.ServerUpload."""

    instances = []
    answer = {"raw_text": "hello", "final_text": "Hello."}

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
        session,
        "whisper_transcribe",
        lambda input_wav, config, output_base: (transcribed.append(input_wav), fake_whisper(input_wav, config, output_base)),
    )
    monkeypatch.setattr(session, "setup_logging", lambda ws: None)
    monkeypatch.setattr(local_runner, "copy_to_clipboard", clipboard.append)
    monkeypatch.setitem(session.PIPELINE_MAP, "standard", FailingPipeline)
    monkeypatch.setattr(local_runner, "server_is_up", lambda base: False)  # never the real server
    shutil.copy(Path(__file__).resolve().parents[1] / "configs" / "static.json", tmp_path)
    monkeypatch.setattr(local_runner, "get_config_dir", lambda: tmp_path)

    def invoke(outcome="process", **kwargs):
        monkeypatch.setattr(local_runner, "record_from_mic", fake_mic(outcome))
        local_runner.run_local_pipeline("standard", {}, **kwargs)
        return workspace, clipboard, transcribed

    return invoke


def test_llm_failure_still_delivers_scrubbed_text(run):
    workspace, clipboard, _ = run()
    assert clipboard == ["salut"]
    assert (workspace / "20261008_120000_final.txt").read_text() == "salut"


def test_terminal_shows_chunks_and_the_final_text_only(run, capsys):
    run()
    out = capsys.readouterr().out
    assert "✓ chunk 1  salut" in out
    assert out.count("salut") == 2  # the chunk line and the delivered text
    assert "RAW TEXT" not in out and "Executing" not in out


def test_raw_capture_is_kept_in_workspace(run):
    workspace, _, transcribed = run()
    assert (workspace / "20261008_120000_capture.wav").is_file()
    assert [p.parent for p in transcribed] == [workspace / "chunks"]


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

    monkeypatch.setattr(local_runner, "record_from_mic", lambda capture, listeners=(): pytest.fail("recorded"))
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
    monkeypatch.setattr(local_runner, "record_from_mic", lambda capture, listeners=(): pytest.fail("recorded"))
    def whisper_noting_format(input_wav, config, output_base):
        with wave.open(str(input_wav)) as w:
            transcribed.append((w.getframerate(), w.getnchannels()))
        fake_whisper(input_wav, config, output_base)

    monkeypatch.setattr(session, "whisper_transcribe", whisper_noting_format)
    monkeypatch.setattr(session, "setup_logging", lambda ws: None)
    monkeypatch.setattr(local_runner, "copy_to_clipboard", clipboard.append)
    monkeypatch.setitem(session.PIPELINE_MAP, "standard", FailingPipeline)

    local_runner.run_local_pipeline("standard", {}, audio_file=source)

    assert (workspace / "20261008_130000_capture.wav").read_bytes() == source.read_bytes()
    assert transcribed == [(16000, 1)]
    assert clipboard == ["salut"]


@pytest.fixture
def server_up(run, monkeypatch):
    FakeUpload.instances.clear()
    FakeUpload.answer = {"raw_text": "hello", "final_text": "Hello."}
    monkeypatch.setattr(local_runner, "server_is_up", lambda base: True)
    monkeypatch.setattr(local_runner, "ServerUpload", FakeUpload)
    monkeypatch.setenv("SERVER_IP", "100.1.2.3")  # the client role's setting, not used on the host
    monkeypatch.setenv("HOST_BIND_PORT", "8123")
    return run


def test_running_server_receives_the_live_recording(server_up, tmp_path):
    workspace, clipboard, transcribed = server_up()
    (upload,) = FakeUpload.instances
    assert upload.url.startswith("http://127.0.0.1:8123/transcribe?profile=standard")
    assert upload.blocks[-1] is None and not upload.aborted
    assert clipboard == ["Hello."] and transcribed == []
    assert not list((tmp_path / "spool").glob("*.wav"))  # server keeps the audio


def test_quit_with_server_aborts_upload_and_keeps_audio_locally(server_up, tmp_path):
    workspace, clipboard, transcribed = server_up("quit")
    assert FakeUpload.instances[0].aborted
    assert (workspace / "20261008_120000_capture.wav").is_file()
    assert clipboard == [] and transcribed == []


def test_cancel_with_server_aborts_and_leaves_nothing(server_up, tmp_path):
    workspace, clipboard, _ = server_up("cancel")
    assert FakeUpload.instances[0].aborted
    assert not any(workspace.iterdir())  # no local workspace content was created
    assert not list((tmp_path / "spool").glob("*.wav"))


def test_server_failure_falls_back_to_local_processing(server_up):
    FakeUpload.answer = ConnectionError("server went away")
    workspace, clipboard, transcribed = server_up()
    assert [p.parent for p in transcribed] == [workspace / "chunks"]
    assert clipboard == ["salut"]


def test_local_flag_skips_the_server(server_up):
    workspace, clipboard, transcribed = server_up(force_local=True)
    assert FakeUpload.instances == []
    assert transcribed and clipboard == ["salut"]


def test_overrides_the_server_does_not_accept_are_processed_locally(server_up, monkeypatch):
    monkeypatch.setattr(local_runner, "record_from_mic", fake_mic("process"))
    local_runner.run_local_pipeline("standard", {"threads": "8"})
    assert FakeUpload.instances == []


def test_live_local_recording_feeds_the_chunker(run, monkeypatch):
    started = []
    original = session.start_transcription

    def spy(*args, **kwargs):
        job = original(*args, **kwargs)
        started.append(kwargs.get("live"))
        return job

    monkeypatch.setattr(local_runner, "start_transcription", spy)
    workspace, clipboard, transcribed = run()
    assert started == [True]
    assert clipboard == ["salut"]


def test_quit_cancels_the_live_chunker(run, monkeypatch):
    jobs = []
    original = session.start_transcription

    def spy(*args, **kwargs):
        job = original(*args, **kwargs)
        jobs.append(job)
        return job

    monkeypatch.setattr(local_runner, "start_transcription", spy)
    run("quit")
    assert jobs and jobs[0]._cancelled.is_set()
