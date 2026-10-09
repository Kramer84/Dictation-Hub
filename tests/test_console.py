import logging
import subprocess

from dictation_hub.core import console, utils
from dictation_hub.core.invocation import parse_invocation
from dictation_hub.pipeline.engine import setup_logging


def test_details_go_to_the_session_log_not_the_terminal(tmp_path, capsys):
    setup_logging(tmp_path, verbose=False)
    logging.getLogger("dictation_hub.test").info("gain +2.7 dB")
    logging.getLogger("dictation_hub.test").warning("phone interference")
    out = capsys.readouterr().out
    assert "gain" not in out and "phone interference" in out
    log = (tmp_path / "session.log").read_text()
    assert "gain +2.7 dB" in log and "phone interference" in log


def test_verbose_shows_details_on_the_terminal(tmp_path, capsys):
    setup_logging(tmp_path, verbose=True)
    logging.getLogger("dictation_hub.test").info("gain +2.7 dB")
    assert "gain +2.7 dB" in capsys.readouterr().out


def test_verbose_choice_survives_the_workspace_setup(tmp_path, capsys):
    setup_logging(verbose=True)
    setup_logging(tmp_path)
    logging.getLogger("dictation_hub.test").info("kept verbose")
    assert "kept verbose" in capsys.readouterr().out
    setup_logging(verbose=False)


def test_verbose_flag_is_parsed():
    assert parse_invocation(["-v"]).verbose
    assert parse_invocation(["standard", "--verbose", "-l", "fr"]).verbose
    assert not parse_invocation(["standard"]).verbose


def test_chunk_line_fits_the_terminal_width():
    line = console.chunk_line(0, "I think if it's possible to start a service at boot", width=30)
    assert line.startswith("   ✓ chunk 1  I think")
    assert len(line) == 30 and line.endswith("…")
    assert console.chunk_line(2, "So...", width=80) == "   ✓ chunk 3  So..."


def test_empty_chunk_is_shown_as_silence():
    assert console.chunk_line(7, "  ", width=80) == "   ✓ chunk 8  (silence)"


def test_clipboard_reports_a_failed_copy(monkeypatch, capsys):
    monkeypatch.setattr(utils.shutil, "which", lambda name: "/usr/bin/xclip" if name == "xclip" else None)
    monkeypatch.setattr(
        utils.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1)
    )
    assert utils.copy_to_clipboard("hello") is False
    assert "Copied" not in capsys.readouterr().out


def test_clipboard_tool_does_not_hold_our_output(monkeypatch):
    seen = {}
    monkeypatch.setattr(utils.shutil, "which", lambda name: "/usr/bin/xclip" if name == "xclip" else None)

    def run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(utils.subprocess, "run", run)
    assert utils.copy_to_clipboard("hello") is True
    assert seen["stdout"] is subprocess.DEVNULL and seen["stderr"] is subprocess.DEVNULL
