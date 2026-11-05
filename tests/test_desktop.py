"""Pasting the result into the focused window and desktop notifications (M4).

xdotool, the clipboard and notify-send are all fakes: no window is touched.
"""
import subprocess

from dictation_hub.core import desktop


class Recorder:
    """Collects the commands that would have been run."""

    def __init__(self, active_class="firefox", fail=()):
        self.calls, self.active_class, self.fail = [], active_class, fail

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] in self.fail:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        out = ""
        if cmd[:2] == ["xdotool", "getactivewindow"]:
            out = "98566154\n"
        elif cmd[0] == "xprop":  # xdotool 3.2016 has no getwindowclassname
            out = f'WM_CLASS(STRING) = "{self.active_class}", "{self.active_class.title()}"\n'
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def have(*tools):
    return lambda name: f"/usr/bin/{name}" if name in tools else None


def test_normal_windows_get_ctrl_v():
    assert desktop.paste_chord("firefox") == "ctrl+v"


def test_terminals_get_ctrl_shift_v():
    for name in ("gnome-terminal-server", "Gnome-terminal", "kitty", "Alacritty", "konsole"):
        assert desktop.paste_chord(name) == "ctrl+shift+v", name


def test_text_is_copied_then_pasted_with_the_window_s_chord():
    run, copied = Recorder("firefox"), []
    ok = desktop.paste_text("Hello.", run=run, which=have("xdotool", "xprop"), copy=lambda t: copied.append(t) or True)
    assert ok and copied == ["Hello."]
    assert ["xdotool", "key", "--clearmodifiers", "ctrl+v"] in run.calls


def test_a_terminal_window_is_pasted_with_ctrl_shift_v():
    run = Recorder("gnome-terminal-server")
    desktop.paste_text("ls", run=run, which=have("xdotool", "xprop"), copy=lambda t: True)
    assert ["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"] in run.calls


def test_without_xdotool_the_text_stays_on_the_clipboard_and_the_user_is_told():
    run, notes = Recorder(), []
    ok = desktop.paste_text("Hello.", run=run, which=have(), copy=lambda t: True, notify=lambda *a: notes.append(a))
    assert not ok and run.calls == []
    assert "apt install xdotool" in notes[0][1]


def test_nothing_is_pasted_when_the_copy_failed():
    run = Recorder()
    ok = desktop.paste_text("Hello.", run=run, which=have("xdotool", "xprop"), copy=lambda t: False)
    assert not ok and not any(c[:2] == ["xdotool", "key"] for c in run.calls)


def test_a_failing_xdotool_reports_false_instead_of_raising():
    run = Recorder(fail=("xdotool",))
    assert not desktop.paste_text("x", run=run, which=have("xdotool", "xprop"), copy=lambda t: True)


def test_notify_uses_notify_send_and_is_silent_without_it():
    run = Recorder()
    desktop.notify("Dictation", "Recording", run=run, which=have("notify-send"))
    assert run.calls[0][0] == "notify-send" and "Recording" in run.calls[0]
    run2 = Recorder()
    desktop.notify("Dictation", "Recording", run=run2, which=have())
    assert run2.calls == []


def test_missing_requirements_names_the_apt_package():
    assert desktop.missing_requirements(which=have("notify-send", "xclip")) == ["xdotool"]
    assert desktop.missing_requirements(which=have("xdotool", "notify-send", "xclip")) == []


def test_without_xprop_the_plain_chord_is_used():
    run = Recorder("gnome-terminal-server")
    desktop.paste_text("x", run=run, which=have("xdotool"), copy=lambda t: True)
    assert ["xdotool", "key", "--clearmodifiers", "ctrl+v"] in run.calls
