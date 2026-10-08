import os
import select
import signal
import subprocess
import sys
import termios
import tty
import wave
from pathlib import Path
from typing import Callable, Iterator, List, Optional

KEY_HELP = "[Space] stop & process   [p] pause/resume   [q] quit (keep audio)   [c] cancel"


def arecord_command(path: Path) -> List[str]:
    """Capture in CD format; prepare_audio converts to 16 kHz mono afterwards.

    Direct 16 kHz mono capture was tried and degraded a live dictation, so the
    pre-M1 format stays until an A/B comparison says otherwise.
    """
    return ["arecord", "-q", "-f", "cd", "-t", "wav", str(path)]


class Recorder:
    """Records into numbered segment files and joins them into ``output`` on stop.

    Each segment runs the capture command in its own session, so a Ctrl-C in
    the terminal never reaches it; it is stopped with SIGINT, which lets
    arecord finalize the WAV header. Pausing closes the current segment and
    resuming opens a new one, so paused time is simply not recorded.
    """

    def __init__(
        self,
        output: Path,
        command_factory: Callable[[Path], List[str]] = arecord_command,
    ):
        self.output = output
        self.command_factory = command_factory
        self.segments: List[Path] = []
        self.process: Optional[subprocess.Popen] = None

    def start(self) -> None:
        segment = self.output.with_name(f"{self.output.stem}.part{len(self.segments):03d}.wav")
        self.segments.append(segment)
        self.process = subprocess.Popen(
            self.command_factory(segment),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def pause(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None

    def resume(self) -> None:
        if self.process is None:
            self.start()

    def stop(self) -> None:
        self.pause()
        self._join_segments()

    def is_alive(self) -> bool:
        return self.process is None or self.process.poll() is None

    def _join_segments(self) -> None:
        params = None
        frames = []
        for segment in self.segments:
            try:
                with wave.open(str(segment)) as w:
                    params = params or w.getparams()
                    frames.append(w.readframes(w.getnframes()))
            except (FileNotFoundError, EOFError, wave.Error):
                continue
        if params is not None:
            with wave.open(str(self.output), "wb") as out:
                out.setparams(params)
                for chunk in frames:
                    out.writeframes(chunk)
        for segment in self.segments:
            segment.unlink(missing_ok=True)


def control_recording(rec, keys: Iterator[Optional[str]]) -> str:
    """Drive a recorder from a key stream.

    Returns ``process`` (Space, or the key stream ended), ``quit`` (q or
    Ctrl-C: keep the audio, skip processing), ``cancel`` (c: discard) or
    ``failed`` (the capture process died). ``None`` keys are idle ticks.
    """
    rec.start()
    paused = False
    outcome = "process"
    try:
        for key in keys:
            if key is None:
                if not rec.is_alive():
                    rec.stop()
                    return "failed"
                continue
            if key == " ":
                break
            if key == "p":
                if paused:
                    rec.resume()
                    print("\r▶  Recording...            ", end="", flush=True)
                else:
                    rec.pause()
                    print("\r⏸  Paused (p to resume)   ", end="", flush=True)
                paused = not paused
            elif key == "q":
                outcome = "quit"
                break
            elif key == "c":
                outcome = "cancel"
                break
    except KeyboardInterrupt:
        outcome = "quit"
    rec.stop()
    print()
    return outcome


def terminal_keys(poll_interval: float = 0.1) -> Iterator[Optional[str]]:
    """Yield single key presses from the terminal, or ``None`` on idle ticks.

    The terminal is put in cbreak mode (no Enter needed, Ctrl-C still raises
    KeyboardInterrupt) and restored afterwards. Without a terminal, each input
    line counts as one key, and the stream ends at EOF.
    """
    stdin = sys.stdin
    if not stdin.isatty():
        for line in stdin:
            yield line[:1] or " "
        return
    fd = stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ready, _, _ = select.select([fd], [], [], poll_interval)
            yield os.read(fd, 1).decode(errors="ignore") if ready else None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def record_from_mic(capture: Path) -> str:
    """Record from the default microphone into ``capture``; returns the outcome."""
    print(f"🎙️  Recording...  {KEY_HELP}")
    return control_recording(Recorder(capture), terminal_keys())
