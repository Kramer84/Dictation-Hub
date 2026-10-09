import subprocess
from pathlib import Path

import pytest

from dictation_hub.core import whisper_interface as wi


@pytest.fixture
def fake_whisper(tmp_path, monkeypatch):
    """A whisper.cpp tree whose binary is never run: subprocess.run is recorded."""
    whisper_dir = tmp_path / "whisper.cpp"
    (whisper_dir / "build" / "bin").mkdir(parents=True)
    (whisper_dir / "build" / "bin" / "whisper-cli").write_text("")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        stderr = "whisper_full_with_state: auto-detected language: fr (p = 0.997400)\n" if "-dl" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=stderr)

    monkeypatch.setattr(wi.subprocess, "run", run)

    def transcribe(**kwargs):
        wi.whisper_transcribe(input_wav=wav, whisper_dir=whisper_dir, output_base=tmp_path / "out", **kwargs)
        return calls

    return transcribe


def option(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def test_known_language_gets_its_style_prompt_without_detection(fake_whisper):
    calls = fake_whisper(language="fr")
    assert len(calls) == 1
    assert option(calls[0], "--prompt") == wi.STYLE_PROMPTS["fr"]


def test_auto_language_is_detected_then_locked(fake_whisper):
    calls = fake_whisper(language="auto")
    detect, main = calls
    assert "-dl" in detect and "-np" not in detect
    assert option(main, "-l") == "fr"
    assert option(main, "--prompt") == wi.STYLE_PROMPTS["fr"]


def test_profile_vocabulary_is_kept_after_the_style_sentence(fake_whisper):
    calls = fake_whisper(language="en", initial_prompt="Ollama, PyTorch, SiYuan.")
    assert option(calls[0], "--prompt") == wi.STYLE_PROMPTS["en"] + " Ollama, PyTorch, SiYuan."


def test_style_prompt_can_be_disabled(fake_whisper):
    calls = fake_whisper(language="auto", no_style_prompt=True)
    assert len(calls) == 1
    assert option(calls[0], "-l") == "auto" and "--prompt" not in calls[0]


def test_language_without_style_sentence_gets_no_prompt(fake_whisper):
    calls = fake_whisper(language="ja")
    assert "--prompt" not in calls[0]


def test_whisper_output_goes_to_the_log_not_the_terminal(fake_whisper, monkeypatch, caplog, capsys):
    def run(cmd, **kwargs):
        assert kwargs.get("capture_output")
        return subprocess.CompletedProcess(
            cmd, 0, stdout="[00:00:00.000 --> 00:00:01.000]   Hello there.\n",
            stderr="read_audio_data: reading audio data\n",
        )

    monkeypatch.setattr(wi.subprocess, "run", run)
    with caplog.at_level("DEBUG"):
        fake_whisper(language="en")
    assert "Hello there." in caplog.text and "read_audio_data" in caplog.text
    assert capsys.readouterr().out == ""


def test_whisper_failure_shows_its_error(fake_whisper, monkeypatch, caplog):
    monkeypatch.setattr(
        wi.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, stdout="", stderr="error: failed to load model\n"),
    )
    with caplog.at_level("ERROR"), pytest.raises(wi.typer.Exit):
        fake_whisper(language="en")
    assert "failed to load model" in caplog.text


def test_failed_detection_falls_back_to_auto_without_style(fake_whisper, monkeypatch):
    monkeypatch.setattr(wi, "probe_language", lambda *a: None)
    calls = fake_whisper(language="auto")
    assert option(calls[-1], "-l") == "auto" and "--prompt" not in calls[-1]
