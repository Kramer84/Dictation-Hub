"""The hotkey session: how `toggle`, `session`, recording and delivery fit together (M4)."""
import os
import signal
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from dictation_hub import cli
from dictation_hub.core import console, desktop, recorder, toggle

runner = CliRunner()


@pytest.fixture(autouse=True)
def desktop_off(monkeypatch):
    monkeypatch.setattr(desktop, "_active", False)


@pytest.fixture
def notes(monkeypatch):
    seen = []
    monkeypatch.setattr(desktop, "notify", lambda summary, body="", **k: seen.append((summary, body)))
    return seen


# --- delivery ---------------------------------------------------------------

def test_delivery_only_prints_and_copies_in_a_terminal_session(monkeypatch):
    pasted = []
    monkeypatch.setattr(console, "copy_to_clipboard", lambda t: True)
    monkeypatch.setattr(desktop, "paste_text", lambda t, **k: pasted.append(t) or True)
    console.deliver("raw", "Final.", None)
    assert pasted == []


def test_delivery_pastes_in_a_hotkey_session_and_says_so(monkeypatch, notes):
    pasted = []
    monkeypatch.setattr(console, "copy_to_clipboard", lambda t: True)
    monkeypatch.setattr(desktop, "paste_text", lambda t, **k: pasted.append(t) or True)
    desktop.activate()
    console.deliver("raw", "Final.", None)
    assert pasted == ["Final."]
    assert notes[-1][0] == "Dictation pasted"


def test_a_failed_paste_is_reported_as_copied(monkeypatch, notes):
    monkeypatch.setattr(console, "copy_to_clipboard", lambda t: True)
    monkeypatch.setattr(desktop, "paste_text", lambda t, **k: False)
    desktop.activate()
    console.deliver("raw", "Final.", None)
    assert notes[-1][0] == "Dictation copied"


def test_empty_text_is_not_pasted(monkeypatch, notes):
    pasted = []
    monkeypatch.setattr(console, "copy_to_clipboard", lambda t: True)
    monkeypatch.setattr(desktop, "paste_text", lambda t, **k: pasted.append(t) or True)
    desktop.activate()
    console.deliver("", "", None)
    assert pasted == [] and "Nothing" in notes[-1][1]


# --- recording --------------------------------------------------------------

def test_terminal_session_reads_the_terminal(monkeypatch):
    got = {}
    monkeypatch.setattr(recorder, "control_recording", lambda rec, keys: got.setdefault("keys", keys) and "process")
    monkeypatch.setattr(recorder, "terminal_keys", lambda: "TERMINAL")
    recorder.record_from_mic(Path("/tmp/x.wav"))
    assert got["keys"] == "TERMINAL"


def test_hotkey_session_reads_signals_and_reports_phases(monkeypatch, notes, tmp_path):
    got = {}
    monkeypatch.setattr(recorder, "control_recording", lambda rec, keys: got.setdefault("keys", keys) and "process")
    monkeypatch.setattr(toggle, "signal_keys", lambda: "SIGNALS")
    monkeypatch.setattr(toggle, "default_state_dir", lambda: tmp_path)
    toggle.write_state(tmp_path, os.getpid(), "recording")
    desktop.activate()
    recorder.record_from_mic(Path("/tmp/x.wav"))
    assert got["keys"] == "SIGNALS"
    assert toggle.read_state(tmp_path)["phase"] == "processing"
    assert [n[0] for n in notes] == ["Dictation recording", "Dictation transcribing"]


def test_a_cancelled_hotkey_recording_does_not_say_transcribing(monkeypatch, notes, tmp_path):
    monkeypatch.setattr(recorder, "control_recording", lambda rec, keys: "cancel")
    monkeypatch.setattr(toggle, "signal_keys", lambda: iter(()))
    monkeypatch.setattr(toggle, "default_state_dir", lambda: tmp_path)
    toggle.write_state(tmp_path, os.getpid(), "recording")
    desktop.activate()
    recorder.record_from_mic(Path("/tmp/x.wav"))
    assert [n[0] for n in notes] == ["Dictation recording", "Dictation cancelled"]


# --- commands ---------------------------------------------------------------

def test_toggle_command_starts_a_session_with_the_given_arguments(monkeypatch, notes, tmp_path):
    spawned = []
    monkeypatch.setattr(toggle, "default_state_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "spawn_session", lambda args, log_dir: spawned.append(list(args)))
    result = runner.invoke(cli.app, ["toggle", "standard", "-l", "fr"])
    assert result.exit_code == 0, result.output
    assert spawned == [["standard", "-l", "fr"]]


def test_toggle_command_asks_a_running_session_to_stop(monkeypatch, notes, tmp_path):
    sent = []
    monkeypatch.setattr(toggle, "default_state_dir", lambda: tmp_path)
    monkeypatch.setattr(toggle, "send_signal", lambda pid, sig: sent.append((pid, sig)))
    toggle.write_state(tmp_path, os.getpid(), "recording")  # this very process counts as the session
    result = runner.invoke(cli.app, ["toggle"])
    assert result.exit_code == 0, result.output
    assert sent == [(os.getpid(), signal.SIGUSR1)]


def test_toggle_refuses_without_xdotool(monkeypatch, notes, tmp_path):
    monkeypatch.setattr(toggle, "default_state_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "missing_requirements", lambda: ["xdotool"])
    monkeypatch.setattr(cli, "spawn_session", lambda *a: pytest.fail("must not start"))
    result = runner.invoke(cli.app, ["toggle"])
    assert result.exit_code == 1
    assert "apt install xdotool" in notes[-1][1]


def test_session_activates_desktop_mode_and_cleans_up_its_state(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(toggle, "default_state_dir", lambda: tmp_path)

    def fake_dispatch(args):
        seen["active"] = desktop.is_active()
        seen["state"] = toggle.read_state(tmp_path)
        seen["args"] = list(args)

    monkeypatch.setattr(cli, "dispatch", fake_dispatch)
    result = runner.invoke(cli.app, ["session", "standard"])
    assert result.exit_code == 0, result.output
    assert seen["active"] and seen["state"]["phase"] == "recording" and seen["args"] == ["standard"]
    assert toggle.read_state(tmp_path) is None


def test_session_failure_is_notified_and_state_cleared(monkeypatch, notes, tmp_path):
    monkeypatch.setattr(toggle, "default_state_dir", lambda: tmp_path)

    def boom(args):
        raise typer.Exit(code=1)

    monkeypatch.setattr(cli, "dispatch", boom)
    result = runner.invoke(cli.app, ["session"])
    assert result.exit_code == 1
    assert notes[-1][0] == "Dictation failed"
    assert toggle.read_state(tmp_path) is None


# --- hotkeys command group ------------------------------------------------------

from dictation_hub.core import hotkeys  # noqa: E402


def test_install_keyboard_registers_an_absolute_toggle_command(monkeypatch):
    got = {}
    monkeypatch.setattr(hotkeys, "install_gnome_shortcut", lambda b, c, **k: got.update(binding=b, command=c))
    monkeypatch.setattr(hotkeys, "check_binding", lambda b, **k: None)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/home/u/.local/bin/dictation-hub")
    result = runner.invoke(cli.app, ["hotkeys", "install-keyboard", "<Ctrl><Alt>d"])
    assert result.exit_code == 0, result.output
    assert got == {"binding": "<Ctrl><Alt>d", "command": "/home/u/.local/bin/dictation-hub toggle"}


def test_listen_runs_toggle_for_each_trigger_and_rejects_a_scroll_button(monkeypatch):
    fired = []
    monkeypatch.setattr(cli, "do_toggle", lambda args, cancel=False: fired.append(cancel))
    monkeypatch.setattr(hotkeys, "xinput_lines", lambda: iter(["EVENT type 15 (RawButtonPress)", "    detail: 9"]))
    ok = runner.invoke(cli.app, ["hotkeys", "listen", "button:9"])
    assert ok.exit_code == 0 and fired == [False]
    bad = runner.invoke(cli.app, ["hotkeys", "listen", "button:4"])
    assert bad.exit_code == 2


def test_detect_prints_what_was_pressed(monkeypatch):
    monkeypatch.setattr(hotkeys, "xinput_lines", lambda: iter(["EVENT type 15 (RawButtonPress)", "    detail: 9"]))
    result = runner.invoke(cli.app, ["hotkeys", "detect"])
    assert "button:9" in result.output


def test_install_keyboard_refuses_a_dangerous_or_taken_combination_without_touching_settings(monkeypatch):
    monkeypatch.setattr(hotkeys, "install_gnome_shortcut", lambda *a, **k: pytest.fail("must not install"))
    result = runner.invoke(cli.app, ["hotkeys", "install-keyboard", "<Ctrl><Alt>F9"])
    assert result.exit_code == 2 and "virtual terminal" in result.output
