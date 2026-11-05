"""One press starts a dictation, the next one stops it (M4).

``press`` is what a keyboard shortcut or mouse button runs. A background
session (``dictation-hub session``) records; its pid and phase live in a small
state file under ``$XDG_RUNTIME_DIR``. A second press sends SIGUSR1, which the
session reads as the Space key (stop and process); SIGUSR2 reads as ``c``
(cancel and discard). While the session is already processing, presses are
ignored so that two sessions never compete for the GPU.
"""
from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from typing import Callable, Iterator, Optional


def default_state_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/dictation-hub-{os.getuid()}"
    return Path(runtime) / "dictation-hub"


def write_state(state_dir: Path, pid: int, phase: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "session.json").write_text(json.dumps({"pid": pid, "phase": phase}))


def read_state(state_dir: Path) -> Optional[dict]:
    try:
        data = json.loads((state_dir / "session.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "pid" in data else None


def set_phase(state_dir: Path, phase: str) -> None:
    state = read_state(state_dir)
    if state:
        write_state(state_dir, state["pid"], phase)


def clear_state(state_dir: Path) -> None:
    (state_dir / "session.json").unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def send_signal(pid: int, sig: int) -> None:
    os.kill(pid, sig)


def press(
    state_dir: Path,
    spawn: Callable[[], None],
    is_alive: Optional[Callable[[int], bool]] = None,
    send: Optional[Callable[[int, int], None]] = None,
    cancel: bool = False,
) -> str:
    """Handle one key or button press; returns what happened.

    ``started`` / ``stopping`` / ``busy`` (processing) for a normal press,
    ``cancelling`` / ``idle`` for a cancel press.
    """
    is_alive, send = is_alive or pid_alive, send or send_signal
    state = read_state(state_dir)
    live = state if state and is_alive(state["pid"]) else None
    if live is None:
        if state:
            clear_state(state_dir)  # left behind by a crashed session
        if cancel:
            return "idle"
        spawn()
        return "started"
    if live.get("phase") != "recording":
        return "busy"
    send(live["pid"], signal.SIGUSR2 if cancel else signal.SIGUSR1)
    return "cancelling" if cancel else "stopping"


def signal_keys(poll_interval: float = 0.1) -> Iterator[Optional[str]]:
    """Key stream for ``control_recording`` driven by signals instead of a terminal.

    Yields ``None`` idle ticks; SIGUSR1 gives " " (stop and process), SIGUSR2
    gives "c" (cancel), SIGTERM/SIGINT give "q" (keep the audio, skip
    processing). The previous handlers are restored when the stream ends.
    """
    pending: list = []
    mapping = {signal.SIGUSR1: " ", signal.SIGUSR2: "c", signal.SIGTERM: "q", signal.SIGINT: "q"}
    previous = {sig: signal.signal(sig, lambda s, f, key=key: pending.append(key)) for sig, key in mapping.items()}
    try:
        while True:
            if pending:
                yield pending.pop(0)
                return
            yield None
            time.sleep(poll_interval)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
