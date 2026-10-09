import os
import select
import shlex
import signal
import subprocess
import sys
import termios
import threading
import tty
import wave
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional

KEY_HELP = "[Space] stop & process   [p] pause/resume   [q] quit (keep audio)   [c] cancel"


SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
BLOCK_BYTES = SAMPLE_RATE * SAMPLE_WIDTH // 10  # 100 ms


def arecord_command() -> List[str]:
    """16 kHz mono PCM on stdout: whisper's native input.

    An A/B against CD-format capture (optimization/ab_capture.sh) gave
    identical results on this machine.

    DICTATION_CAPTURE_COMMAND replaces it (any command writing 16 kHz mono
    s16le PCM to stdout), e.g. to replay a recording in real time for testing:
    ffmpeg -re -i take.wav -f s16le -ar 16000 -ac 1 -
    """
    override = os.environ.get("DICTATION_CAPTURE_COMMAND")
    if override:
        return shlex.split(override)
    return ["arecord", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"]


class Recorder:
    """Streams microphone PCM into a WAV file and to listeners as it arrives.

    The capture command runs in its own session, so a Ctrl-C in the terminal
    never reaches it; it is stopped with SIGINT. The WAV header is patched on
    every write, so the file on disk is always a valid recording (raw audio is
    never only in memory). While paused, captured audio is discarded.
    Listeners get each kept PCM block, then ``None`` once recording stops.
    """

    def __init__(
        self,
        output: Path,
        command_factory: Callable[[], List[str]] = arecord_command,
        listeners: Iterable[Callable[[Optional[bytes]], None]] = (),
    ):
        self.output = output
        self.command_factory = command_factory
        self.listeners = list(listeners)
        self.process: Optional[subprocess.Popen] = None
        self.paused = False
        self._reader: Optional[threading.Thread] = None
        self._wav = None

    def start(self) -> None:
        self._wav = wave.open(str(self.output), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(SAMPLE_WIDTH)
        self._wav.setframerate(SAMPLE_RATE)
        self.process = subprocess.Popen(
            self.command_factory(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        stream = self.process.stdout
        pending = b""
        while True:
            data = stream.read1(BLOCK_BYTES) if hasattr(stream, "read1") else stream.read(BLOCK_BYTES)
            if not data:
                break
            # Keep whole samples only: a pause between the two bytes of a
            # sample would shift all later audio by one byte.
            data = pending + data
            cut = len(data) - len(data) % SAMPLE_WIDTH
            block, pending = data[:cut], data[cut:]
            if not block or self.paused:
                continue
            self._wav.writeframes(block)
            for listener in self.listeners:
                listener(block)

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def stop(self, send_end: bool = True) -> None:
        """Stop capturing. ``send_end`` tells listeners the recording is
        complete (None); without it they are left open, e.g. to be aborted."""
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._reader.join(timeout=5)
        self._wav.close()
        if send_end:
            for listener in self.listeners:
                listener(None)
        self.process = None

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


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
                    rec.stop(send_end=False)
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
    rec.stop(send_end=outcome == "process")
    # End a pause/resume status line, if one is showing, without a blank line.
    print("\r\033[K" if sys.stdout.isatty() else "", end="", flush=True)
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


def record_from_mic(capture: Path, listeners: Iterable[Callable[[Optional[bytes]], None]] = ()) -> str:
    """Record from the default microphone into ``capture`` (and to ``listeners``).

    Returns the outcome; listeners only get the end-of-stream marker when the
    outcome is ``process``.
    """
    print(f"🎙️  Recording...  {KEY_HELP}")
    return control_recording(Recorder(capture, listeners=listeners), terminal_keys())
