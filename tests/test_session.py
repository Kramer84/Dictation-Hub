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


def write_tone(path, seconds=1, rate=16000, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x10\x00\xf0\xff" * (rate * channels * seconds // 2))


class EchoPipeline:
    def __init__(self, workspace_dir, static_config, **kwargs):
        self.workspace_dir = workspace_dir
        self.language = json.loads((workspace_dir / "metadata.json").read_text())["language"]

    def execute(self, json_path):
        (self.workspace_dir / f"{TS}_raw.txt").write_text("bonjour")
        return f"Bonjour ({self.language})."


@pytest.fixture
def setup(tmp_path, monkeypatch):
    seen = {"inputs": [], "envs": []}

    def fake_whisper(input_wav, config, output_base):
        seen["input"] = input_wav
        seen["inputs"].append(input_wav)
        with wave.open(str(input_wav)) as w:
            seen.setdefault("formats", []).append((w.getframerate(), w.getnchannels()))
        seen["envs"].append(Path(config).read_text())
        seen["env"] = seen["envs"][-1]
        n = len(seen["inputs"])
        Path(f"{output_base}.json").write_text(json.dumps({
            "result": {"language": "fr"},
            "transcription": [{"offsets": {"from": 0, "to": 1000}, "text": f" bonjour {n},"}],
        }))

    monkeypatch.setattr(session, "whisper_transcribe", fake_whisper)
    monkeypatch.setattr(session, "speech_pauses", lambda wav, whisper_dir, vad_model: [(24.0, 1.0)])
    monkeypatch.setattr(session, "setup_logging", lambda ws: None)
    monkeypatch.setitem(session.PIPELINE_MAP, "standard", EchoPipeline)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    capture = workspace / f"{TS}_capture.wav"
    write_tone(capture)
    static = WhisperPipelineConfig.load_from_file(REPO / "configs" / "static.json")
    pipeline_config = {"profiles": {"standard": {"env": "standard.env"}}, "user_information": {}}

    def run(overrides=None, valid_args=("language",), seconds=1, **kwargs):
        write_tone(capture, seconds)
        return session.process_capture(
            capture=capture,
            workspace=workspace,
            timestamp=TS,
            profile="standard",
            pipeline_config=pipeline_config,
            static_config=static,
            overrides=overrides or {},
            valid_args=list(valid_args),
            **kwargs,
        )

    return run, workspace, seen


def test_capture_becomes_a_completed_workspace(setup):
    run, workspace, seen = setup
    result = run()
    assert result.final_text == "Bonjour (fr)."
    assert result.raw_text == "bonjour"
    assert seen["inputs"][0].parent == workspace / "chunks"
    names = {p.name for p in workspace.iterdir()}
    assert {f"{TS}_full.json", f"{TS}_final.txt", "metadata.json", ".completed"} <= names


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


def test_long_capture_is_transcribed_in_chunks_and_merged(setup):
    run, workspace, seen = setup
    run(seconds=70)
    assert len(seen["inputs"]) == 3  # cuts at 24 s and 48 s
    full = json.loads((workspace / f"{TS}_full.json").read_text())
    assert [s["offsets"]["from"] for s in full["transcription"]] == [0, 24000, 48000]
    assert [s["text"] for s in full["transcription"]] == [" bonjour 1,", " bonjour 2,", " bonjour 3,"]


def test_each_transcribed_chunk_is_reported(setup):
    run, _, _ = setup
    reported = []
    run(seconds=70, on_chunk=lambda index, text: reported.append((index, text)))
    assert reported == [(0, "bonjour 1,"), (1, "bonjour 2,"), (2, "bonjour 3,")]


def test_language_found_on_the_first_chunk_is_locked_for_the_next(setup):
    run, _, seen = setup
    run(seconds=70)
    assert 'LANGUAGE="fr"' not in seen["envs"][0]
    assert all('LANGUAGE="fr"' in env for env in seen["envs"][1:])


def test_non_16k_capture_is_converted_first(setup, tmp_path):
    run, workspace, seen = setup
    capture = workspace / f"{TS}_capture.wav"
    write_tone(capture, seconds=2, rate=44100, channels=2)
    session.process_capture(
        capture=capture, workspace=workspace, timestamp=TS, profile="standard",
        pipeline_config={"profiles": {"standard": {"env": "standard.env"}}, "user_information": {}},
        static_config=WhisperPipelineConfig.load_from_file(REPO / "configs" / "static.json"),
        overrides={}, valid_args=["language"],
    )
    assert seen["formats"][0] == (16000, 1)


def test_live_recording_is_chunked_while_it_grows(setup):
    import threading
    import time

    run, workspace, seen = setup
    capture = workspace / f"{TS}_live.wav"
    pipeline_config = {"profiles": {"standard": {"env": "standard.env"}}, "user_information": {}}
    static = WhisperPipelineConfig.load_from_file(REPO / "configs" / "static.json")
    job = session.start_transcription(
        capture, workspace, TS, "standard", pipeline_config, overrides={}, valid_args=["language"], live=True
    )
    w = wave.open(str(capture), "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    for _ in range(60):
        w.writeframes(b"\x10\x00\xf0\xff" * 8000)
        job(b"")
        time.sleep(0.02)
    deadline = time.time() + 10
    while len(seen["inputs"]) < 1 and time.time() < deadline:
        time.sleep(0.05)
    assert len(seen["inputs"]) >= 1  # first chunk done before the recording ended
    w.close()
    result = session.complete_session(job, workspace, TS, "standard", pipeline_config, static)
    assert result.final_text == "Bonjour (fr)."
    assert (workspace / ".completed").exists()
