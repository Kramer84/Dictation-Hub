import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
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


class CaptureUnavailable(RuntimeError):
    """No tool to record from the microphone could be found."""


def alsa_has_capture_card() -> bool:
    """True when ALSA lists at least one capture card (False on WSL2/WSLg, where
    the microphone is only a PulseAudio source and ``arecord`` finds no device)."""
    try:
        listing = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "card " in listing


def capture_command() -> List[str]:
    """A command writing 16 kHz mono s16le PCM (whisper's native input) to stdout.

    DICTATION_CAPTURE_COMMAND replaces the choice (any such command), e.g. to
    replay a recording in real time for testing:
    ffmpeg -re -i take.wav -f s16le -ar 16000 -ac 1 -

    Otherwise the first backend that applies wins:
    1. arecord, when ALSA sees a sound card (native Linux);
    2. parec (PulseAudio / PipeWire-pulse; WSL2 with WSLg, where ALSA has no card);
    3. ffmpeg: avfoundation on macOS, pulse elsewhere.
    """
    override = os.environ.get("DICTATION_CAPTURE_COMMAND")
    if override:
        return shlex.split(override)
    rate = str(SAMPLE_RATE)
    if shutil.which("arecord") and alsa_has_capture_card():
        return ["arecord", "-q", "-f", "S16_LE", "-r", rate, "-c", "1", "-t", "raw"]
    if shutil.which("parec"):
        return ["parec", "--format=s16le", f"--rate={rate}", "--channels=1", "--raw"]
    if shutil.which("ffmpeg"):
        source = ["-f", "avfoundation", "-i", ":default"] if sys.platform == "darwin" else ["-f", "pulse", "-i", "default"]
        return ["ffmpeg", "-loglevel", "error", "-nostdin", *source, "-f", "s16le", "-ar", rate, "-ac", "1", "-"]
    raise CaptureUnavailable(
        "no audio capture tool found: install pulseaudio-utils (parec), alsa-utils (arecord) or ffmpeg, "
        "or set DICTATION_CAPTURE_COMMAND to a command writing 16 kHz mono s16le PCM to stdout"
    )


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
        command_factory: Callable[[], List[str]] = capture_command,
        listeners: Iterable[Callable[[Optional[bytes]], None]] = (),
    ):
        self.output = output
        self.command_factory = command_factory
        self.listeners = list(listeners)
        self.process: Optional[subprocess.Popen] = None
        self.paused = False
        self._reader: Optional[threading.Thread] = None
        self._wav = None
        self._stderr = None

    def start(self) -> None:
        command = self.command_factory()
        self._wav = wave.open(str(self.output), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(SAMPLE_WIDTH)
        self._wav.setframerate(SAMPLE_RATE)
        self._stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
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

    def error_text(self) -> str:
        """What the capture command wrote to stderr (why it died, if it did)."""
        if self._stderr is None:
            return ""
        self._stderr.seek(0)
        return self._stderr.read().decode(errors="replace").strip()

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


def control_recording(rec, keys: Iterator[Optional[str]]) -> str:
    """Drive a recorder from a key stream.

    Returns ``process`` (Space, or the key stream ended), ``quit`` (q or
    Ctrl-C: keep the audio, skip processing), ``cancel`` (c: discard) or
    ``failed`` (the capture process died). ``None`` keys are idle ticks.
    """
    try:
        rec.start()
    except (CaptureUnavailable, OSError) as exc:
        print(f"\r\033[K⚠️  Cannot start audio capture: {exc}", file=sys.stderr)
        return "failed"
    paused = False
    outcome = "process"
    try:
        for key in keys:
            if key is None:
                if not rec.is_alive():
                    rec.stop(send_end=False)
                    if rec.error_text():
                        print(f"\r\033[K⚠️  Audio capture stopped: {rec.error_text()[-500:]}", file=sys.stderr)
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
