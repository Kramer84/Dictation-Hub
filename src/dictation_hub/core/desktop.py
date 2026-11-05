"""Desktop integration for hotkey-driven dictation (M4): paste into the focused
window and notifications.

``activate()`` is called once by the background session that a hotkey starts
(``dictation-hub toggle``); from then on ``recorder.record_from_mic`` reads
signals instead of terminal keys and ``console.deliver`` pastes instead of only
printing. The process is single-purpose, which is why a module-level switch is
used instead of a parameter through every call site (host and client roles).

X11 only for now: ``xdotool`` (required, ``sudo apt install xdotool``) and
``xprop`` (optional, to paste with Ctrl+Shift+V into terminals). Wayland would
use ``wtype`` and is not implemented.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Callable, List, Optional

_active = False

APT_PACKAGES = {"xdotool": "xdotool", "xclip": "xclip"}

TERMINAL_CLASSES = (
    "terminal", "kitty", "alacritty", "konsole", "xterm", "urxvt", "tilix",
    "terminator", "wezterm", "foot", "st-256color", "ptyxis", "guake",
)


def activate() -> None:
    global _active
    _active = True


def is_active() -> bool:
    return _active


def paste_chord(window_class: str) -> str:
    """Terminals paste with Ctrl+Shift+V, everything else with Ctrl+V."""
    name = (window_class or "").lower()
    return "ctrl+shift+v" if any(t in name for t in TERMINAL_CLASSES) else "ctrl+v"


def missing_requirements(which: Callable = shutil.which) -> List[str]:
    """apt packages needed for pasting and not installed."""
    missing = []
    if not which("xdotool"):
        missing.append("xdotool")
    if not (which("xclip") or which("wl-copy")):
        missing.append("xclip")
    return missing


def notify(summary: str, body: str = "", run: Callable = subprocess.run, which: Callable = shutil.which) -> None:
    """Desktop notification; silent when ``notify-send`` is missing or fails."""
    if not which("notify-send"):
        return
    try:
        run(
            ["notify-send", "--app-name=Dictation-Hub", "--expire-time=3000", summary, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _active_window_class(run: Callable, which: Callable) -> str:
    if not which("xprop"):
        return ""
    try:
        win = run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=5)
        if win.returncode != 0 or not win.stdout.strip():
            return ""
        prop = run(["xprop", "-id", win.stdout.strip(), "WM_CLASS"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return " ".join(re.findall(r'"([^"]*)"', prop.stdout or ""))


def paste_text(
    text: str,
    run: Callable = subprocess.run,
    which: Callable = shutil.which,
    copy: Optional[Callable[[str], bool]] = None,
    notify: Callable = notify,
) -> bool:
    """Copy ``text`` and press the paste chord in the focused window. True if pasted.

    The text always stays on the clipboard, so a failed paste can be done by hand.
    """
    if copy is None:
        from .utils import copy_to_clipboard as copy
    if not copy(text):
        return False
    if not which("xdotool"):
        notify("Dictation copied", "Not pasted: install xdotool (sudo apt install xdotool). The text is on the clipboard.")
        return False
    chord = paste_chord(_active_window_class(run, which))
    try:
        # --clearmodifiers: the hotkey's own Ctrl/Alt may still be held down.
        done = run(["xdotool", "key", "--clearmodifiers", chord], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0
