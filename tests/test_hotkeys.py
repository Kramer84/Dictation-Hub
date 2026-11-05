"""Triggers for the toggle command: GNOME keyboard shortcuts and a mouse/key listener (M4).

Real xinput output was captured for the format; gsettings and the clock are fakes.
"""
import pytest

from dictation_hub.core import hotkeys as hk

XI = """\
EVENT type 17 (RawMotion)
    device: 2 (11)
    time:   71233984
    detail: 0
    flags: 
EVENT type 13 (RawKeyPress)
    device: 3 (5)
    time:   71234736
    detail: 50
    flags: 
EVENT type 14 (RawKeyRelease)
    device: 3 (5)
    detail: 50
EVENT type 15 (RawButtonPress)
    device: 2 (11)
    time:   71240000
    detail: 9
    flags: 
EVENT type 16 (RawButtonRelease)
    device: 2 (11)
    detail: 9
""".splitlines()


def test_only_presses_are_read_from_xinput_output():
    assert list(hk.parse_events(XI)) == [("key", 50), ("button", 9)]


def test_trigger_specs():
    assert hk.parse_trigger("button:9") == ("button", 9)
    assert hk.parse_trigger(" key:134 ") == ("key", 134)


@pytest.mark.parametrize("bad", ["", "9", "button", "button:x", "mouse:9", "button:1", "button:3", "button:5", "key:0"])
def test_bad_triggers_are_refused(bad):
    with pytest.raises(ValueError):
        hk.parse_trigger(bad)  # buttons 1-7 are click and scroll: never a trigger


class Clock:
    def __init__(self):
        self.t = 0.0


def test_listen_fires_for_a_configured_button_only():
    fired = []
    hk.listen(XI, [("button", 9)], lambda trig: fired.append(trig), now=lambda: 0.0)
    assert fired == [("button", 9)]


def test_a_key_event_repeated_by_several_devices_fires_once():
    lines = []
    for _ in range(3):  # master + slave + xtest report the same press
        lines += ["EVENT type 13 (RawKeyPress)", "    device: 3 (5)", "    detail: 134"]
    fired, clock = [], Clock()
    hk.listen(lines, [("key", 134)], fired.append, now=lambda: clock.t, debounce=0.5)
    assert fired == [("key", 134)]


def test_presses_apart_fire_again():
    lines = ["EVENT type 15 (RawButtonPress)", "    detail: 9"] * 2
    ticks = iter([0.0, 1.0])
    fired = []
    hk.listen(lines, [("button", 9)], fired.append, now=lambda: next(ticks), debounce=0.5)
    assert len(fired) == 2


def test_detect_reports_each_press_in_trigger_syntax_and_skips_motion_buttons():
    lines = XI + ["EVENT type 15 (RawButtonPress)", "    detail: 4"]  # scroll wheel
    assert list(hk.detect(lines)) == ["key:50", "button:9"]


# --- GNOME keyboard shortcut -------------------------------------------------

class FakeGsettings:
    def __init__(self, listing="@as []"):
        self.values = {"custom-keybindings": listing}
        self.calls = []

    def __call__(self, cmd, **kwargs):
        import subprocess

        self.calls.append(cmd)
        assert cmd[0] == "gsettings"
        if cmd[1] == "get":
            return subprocess.CompletedProcess(cmd, 0, stdout=self.values[cmd[3]] + "\n", stderr="")
        if cmd[1] == "set" and cmd[3] == "custom-keybindings":
            self.values["custom-keybindings"] = cmd[4]
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_shortcut_is_registered_in_the_custom_keybinding_list():
    g = FakeGsettings()
    hk.install_gnome_shortcut("<Ctrl><Alt>d", "dictation-hub toggle", run=g)
    path = hk.GNOME_PATH
    assert g.values["custom-keybindings"] == f"['{path}']"
    flat = [" ".join(c) for c in g.calls]
    assert any("name" in c and "Dictation-Hub" in c for c in flat)
    assert any("command" in c and "dictation-hub toggle" in c for c in flat)
    assert any("binding" in c and "<Ctrl><Alt>d" in c for c in flat)


def test_existing_shortcuts_are_kept_and_not_duplicated():
    other = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"
    g = FakeGsettings(f"['{other}']")
    hk.install_gnome_shortcut("<Super>d", "x", run=g)
    hk.install_gnome_shortcut("<Super>e", "x", run=g)  # reinstall = replace
    assert g.values["custom-keybindings"] == f"['{other}', '{hk.GNOME_PATH}']"


def test_removing_the_shortcut_leaves_the_others():
    other = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"
    g = FakeGsettings(f"['{other}', '{hk.GNOME_PATH}']")
    hk.remove_gnome_shortcut(run=g)
    assert g.values["custom-keybindings"] == f"['{other}']"


def test_listener_unit_runs_the_listen_command():
    unit = hk.render_listener_unit("/home/u/.local/bin/dictation-hub", ["button:9", "key:134"])
    assert "ExecStart=/home/u/.local/bin/dictation-hub hotkeys listen button:9 key:134" in unit
    assert "graphical-session.target" in unit


# --- refusing dangerous or taken key combinations ------------------------------

import subprocess  # noqa: E402


@pytest.mark.parametrize("binding", [
    "<Ctrl><Alt>F9", "<Primary><Alt>F1", "<Control><Alt>F12", "<Alt><Ctrl>f3",
])
def test_virtual_terminal_switch_combinations_are_refused(binding):
    # Ctrl+Alt+F1..F12 is the X server's own VT switch: pressing it froze a machine (2026-11-05).
    with pytest.raises(ValueError, match="virtual terminal"):
        hk.check_binding(binding, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr=""))


def test_a_binding_must_have_a_modifier_and_a_key():
    for bad in ("d", "<Ctrl>", ""):
        with pytest.raises(ValueError):
            hk.check_binding(bad, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr=""))


def test_normalize_treats_the_modifier_spellings_alike():
    assert hk.normalize_binding("<Primary><Alt>d") == hk.normalize_binding("<Alt><Control>D")
    assert hk.normalize_binding("<Ctrl><Alt>d") != hk.normalize_binding("<Ctrl><Shift>d")


class FakeSettings:
    """gsettings answers: {schema: 'schema key value' lines}, custom entries {path: (name, binding)}."""

    def __init__(self, schemas=None, custom=None):
        self.schemas, self.custom = schemas or {}, custom or {}

    def __call__(self, cmd, **kwargs):
        out = ""
        if cmd[:2] == ["gsettings", "list-recursively"]:
            out = "\n".join(self.schemas.get(cmd[2], []))
        elif cmd[:3] == ["gsettings", "get", hk.GNOME_SCHEMA] and cmd[3] == "custom-keybindings":
            out = "[" + ", ".join(f"'{p}'" for p in self.custom) + "]"
        elif cmd[:2] == ["gsettings", "get"] and ":" in cmd[2]:
            path = cmd[2].split(":", 1)[1]
            name, binding = self.custom[path]
            out = f"'{name if cmd[3] == 'name' else binding}'"
        return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")


def test_a_combination_used_by_a_gnome_shortcut_is_reported():
    run = FakeSettings(schemas={
        "org.gnome.desktop.wm.keybindings": [
            "org.gnome.desktop.wm.keybindings show-desktop ['<Primary><Super>d', '<Primary><Alt>d', '<Super>d']",
        ],
    })
    with pytest.raises(hk.BindingTaken) as err:
        hk.check_binding("<Ctrl><Alt>d", run=run)
    assert "show-desktop" in str(err.value)


def test_a_combination_used_by_another_custom_shortcut_is_reported_by_name():
    other = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"
    run = FakeSettings(custom={other: ("testemoji", "<Shift><Control><Alt><Super>space")})
    with pytest.raises(hk.BindingTaken) as err:
        hk.check_binding("<Shift><Ctrl><Alt><Super>space", run=run)
    assert "testemoji" in str(err.value)


def test_our_own_previous_shortcut_is_not_a_conflict():
    run = FakeSettings(custom={hk.GNOME_PATH: ("Dictation-Hub", "<Super>e")})
    hk.check_binding("<Super>e", run=run)  # replacing ourselves is fine


def test_a_free_combination_passes():
    hk.check_binding("<Super><Shift>y", run=FakeSettings())
