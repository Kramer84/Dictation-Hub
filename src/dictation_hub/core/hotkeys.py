"""Triggers that run ``dictation-hub toggle`` (M4).

* Keyboard combinations (Ctrl+Alt+D...) are registered as a GNOME custom
  shortcut with ``gsettings``: GNOME grabs the keys, so they do not reach the
  focused application.
* A mouse button (the MX Master thumb button) or a single key (an emoji key)
  cannot be bound in GNOME's settings. ``listen`` watches them through
  ``xinput test-xi2 --root`` (X11, no extra group needed). This only
  *observes*: the button or key still does its normal job as well, so remap it
  first if that is unwanted (``xinput set-button-map``, solaar, input-remapper).
  ``detect`` prints the spec of whatever you press, to find the numbers.
"""
from __future__ import annotations

import re
import subprocess
import time
from typing import Callable, Iterable, Iterator, List, Optional, Tuple

Trigger = Tuple[str, int]

GNOME_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
GNOME_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation-hub/"
LISTENER_UNIT = "dictation-hub-hotkeys.service"

_EVENT = re.compile(r"EVENT type \d+ \((RawKeyPress|RawButtonPress)\)")
_DETAIL = re.compile(r"\s*detail:\s*(\d+)")
_KINDS = {"RawKeyPress": "key", "RawButtonPress": "button"}


def parse_events(lines: Iterable[str]) -> Iterator[Trigger]:
    """(kind, number) for each key or button *press* in ``xinput test-xi2 --root`` output."""
    kind: Optional[str] = None
    for line in lines:
        head = _EVENT.search(line)
        if head:
            kind = _KINDS[head.group(1)]
        elif line.startswith("EVENT"):
            kind = None
        elif kind:
            detail = _DETAIL.match(line)
            if detail:
                yield kind, int(detail.group(1))
                kind = None


def parse_trigger(spec: str) -> Trigger:
    """``button:9`` or ``key:134``. Buttons 1-7 (click, scroll) are refused."""
    kind, _, number = (spec or "").strip().partition(":")
    if kind not in ("button", "key") or not number.isdigit():
        raise ValueError(f"Trigger '{spec}': use button:<n> or key:<keycode> (see `hotkeys detect`).")
    n = int(number)
    if kind == "button" and n <= 7:
        raise ValueError(f"Trigger '{spec}': buttons 1-7 are click and scroll; use a side button (8 and up).")
    if kind == "key" and n < 8:
        raise ValueError(f"Trigger '{spec}': not a valid keycode.")
    return kind, n


def detect(lines: Iterable[str]) -> Iterator[str]:
    """Specs of the presses seen, skipping buttons that cannot be triggers."""
    for kind, n in parse_events(lines):
        if kind == "button" and n <= 7:
            continue
        yield f"{kind}:{n}"


def listen(
    lines: Iterable[str],
    triggers: List[Trigger],
    fire: Callable[[Trigger], None],
    now: Callable[[], float] = time.monotonic,
    debounce: float = 0.5,
) -> None:
    """Call ``fire(trigger)`` for each configured press; one physical press is
    reported several times (master and slave devices), so repeats within
    ``debounce`` seconds count once."""
    last = {}
    for event in parse_events(lines):
        if event not in triggers:
            continue
        t = now()
        if event in last and t - last[event] < debounce:
            continue
        last[event] = t
        fire(event)


def xinput_lines() -> Iterator[str]:
    proc = subprocess.Popen(["xinput", "test-xi2", "--root"], stdout=subprocess.PIPE, text=True)
    try:
        yield from proc.stdout
    finally:
        proc.kill()


# --- checking a combination before it is installed ---------------------------------

class BindingTaken(ValueError):
    """The combination already belongs to another shortcut."""


_MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "primary": "ctrl", "alt": "alt", "shift": "shift",
              "super": "super", "mod4": "super"}
# Where GNOME keeps the shortcuts that could collide with ours.
_SHORTCUT_SCHEMAS = (
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.shell.keybindings",
    "org.gnome.mutter.keybindings",
    "org.gnome.settings-daemon.plugins.media-keys",
)


def normalize_binding(binding: str):
    """(modifiers, key) with Ctrl/Control/Primary alike and the key lower-cased."""
    mods = frozenset(_MODIFIERS.get(m.lower(), m.lower()) for m in re.findall(r"<(\w+)>", binding or ""))
    return mods, re.sub(r"<\w+>", "", binding or "").strip().lower()


def check_binding(binding: str, run: Callable = subprocess.run) -> None:
    """Refuse a combination that is unusable, dangerous or already taken.

    ValueError: no modifier or no key; Ctrl+Alt+F1..F12 (the X server's virtual
    terminal switch, which froze a machine on 2026-11-05). BindingTaken: used by
    a GNOME shortcut or another custom shortcut (our own entry does not count).
    """
    mods, key = normalize_binding(binding)
    if not mods or not key:
        raise ValueError(f"'{binding}': a shortcut needs at least one modifier and a key, e.g. '<Super><Shift>y'.")
    if {"ctrl", "alt"} <= mods and re.fullmatch(r"f([1-9]|1[0-2])", key):
        raise ValueError(f"'{binding}' switches virtual terminals (Ctrl+Alt+F1..F12) and can freeze the display. Pick another.")
    wanted = (mods, key)
    for schema in _SHORTCUT_SCHEMAS:
        listing = run(["gsettings", "list-recursively", schema], capture_output=True, text=True).stdout
        for line in listing.splitlines():
            _, _, rest = line.partition(" ")
            name, _, value = rest.partition(" ")
            if any(normalize_binding(v) == wanted for v in re.findall(r"'([^']*)'", value)):
                raise BindingTaken(f"'{binding}' is already used by the GNOME shortcut {schema} {name}.")
    for path in _current_paths(run):
        if path == GNOME_PATH:
            continue
        entry = f"{GNOME_SCHEMA}.custom-keybinding:{path}"
        got = lambda k: re.sub(r"^'|'$", "", run(["gsettings", "get", entry, k], capture_output=True, text=True).stdout.strip())  # noqa: E731
        if normalize_binding(got("binding")) == wanted:
            raise BindingTaken(f"'{binding}' is already used by your custom shortcut '{got('name')}'.")


# --- GNOME custom shortcut ---------------------------------------------------

def _current_paths(run: Callable) -> List[str]:
    out = run(["gsettings", "get", GNOME_SCHEMA, "custom-keybindings"], capture_output=True, text=True).stdout
    return re.findall(r"'([^']+)'", out)


def _set_paths(paths: List[str], run: Callable) -> None:
    value = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
    run(["gsettings", "set", GNOME_SCHEMA, "custom-keybindings", value], check=True)


def install_gnome_shortcut(binding: str, command: str, run: Callable = subprocess.run) -> None:
    """Register (or replace) the Dictation-Hub shortcut, e.g. ``<Ctrl><Alt>d``."""
    schema = f"{GNOME_SCHEMA}.custom-keybinding:{GNOME_PATH}"
    for key, value in (("name", "Dictation-Hub"), ("command", command), ("binding", binding)):
        run(["gsettings", "set", schema, key, value], check=True)
    paths = _current_paths(run)
    if GNOME_PATH not in paths:
        _set_paths(paths + [GNOME_PATH], run)


def remove_gnome_shortcut(run: Callable = subprocess.run) -> None:
    paths = [p for p in _current_paths(run) if p != GNOME_PATH]
    _set_paths(paths, run)
    run(["gsettings", "reset-recursively", f"{GNOME_SCHEMA}.custom-keybinding:{GNOME_PATH}"], check=False)


# --- listener service -----------------------------------------------------------

def render_listener_unit(exec_path: str, triggers: List[str]) -> str:
    return f"""[Unit]
Description=Dictation Hub mouse button / key listener
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart={exec_path} hotkeys listen {' '.join(triggers)}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
"""
