"""Dictate through the dictation server: the client role on a remote machine,
and the host CLI when its own server is running.

The recording is streamed to the server while it is made, and kept in a local
spool file until the server has answered, so a lost connection loses no audio.
Each recording carries an id: when the answer does not come back on the upload's
own connection, the client asks the server for it (``GET /result/<id>``) and
resends the spool file only when the server does not have it or stopped
receiving it.
"""
import hashlib
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlencode

import requests
import typer

from ..core.config_manager import get_config_dir
from ..core.console import deliver
from ..core.recorder import record_from_mic
from . import net
from .uploader import ServerUpload, UploadStalled, server_is_up


RESEND_ATTEMPTS = 3
RETRY_DELAY = 3.0  # seconds, multiplied by the attempt number
ANSWER_WAIT = 30.0  # seconds to wait for the live answer before asking the server
STALL_TIMEOUT = 30.0  # seconds without progress on the server before resending
POLL_INTERVAL = 2.0  # seconds between status checks
RECOVERY_TIMEOUT = 900.0  # seconds before giving up on a recording


class ServerFailed(RuntimeError):
    """The server did not deliver a transcript."""


def client_base_url() -> str:
    protocol = os.environ.get("SERVER_PROTOCOL", "http")
    ip = os.environ.get("SERVER_IP", "127.0.0.1")
    port = os.environ.get("SERVER_PORT", "8000")
    return f"{protocol}://{ip}:{port}"


def transcribe_url(
    base_url: str, profile: str, overrides: Dict[str, str], recording_id: Optional[str] = None
) -> str:
    params = {"profile": profile, **overrides}
    if recording_id is not None:
        params["recording_id"] = recording_id
    return f"{base_url}/transcribe?{urlencode(params)}"


def machine_name() -> str:
    return socket.gethostname()


def spool_id(spool: Path) -> str:
    """The id the server files a live recording under (also before it exists)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", f"{machine_name()}-{spool.stem}")[:100]


def recording_id(path: Path) -> str:
    """The id of a saved recording sent with ``-f``.

    A spool file keeps the id of its live recording, so resending it finds the
    result of the first try. Any other file is named by its content, so two
    different files called ``take.wav`` never share a result.
    """
    if path.parent == get_config_dir() / "spool":
        return spool_id(path)
    return f"file-{hashlib.sha256(path.read_bytes()).hexdigest()[:16]}"


def new_spool(profile: str) -> Path:
    spool_dir = get_config_dir() / "spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    return spool_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{profile}.wav"


def check_answer(answer: dict) -> dict:
    """An answer without any text (e.g. whisper failed on the server) is a failure."""
    if not (answer.get("final_text") or answer.get("raw_text")):
        raise ServerFailed(answer.get("error") or "the server returned no text")
    return answer


def record_via_server(
    base_url: str,
    profile: str,
    overrides: Dict[str, str],
    spool: Path,
    record: Optional[Callable] = None,
) -> Tuple[str, Optional[dict]]:
    """Record into ``spool`` while streaming to the server.

    Returns the recording outcome and, for ``process``, the server's answer.
    Any other outcome aborts the upload (the server drops its partial copy).
    Raises ServerFailed when the server does not deliver; ``spool`` is kept.
    """
    rec_id = spool_id(spool)
    url = transcribe_url(base_url, profile, overrides, rec_id)
    upload = ServerUpload(url)
    outcome = (record or record_from_mic)(spool, listeners=[upload])
    if outcome != "process":
        upload.abort()
        return outcome, None
    typer.secho("⏹  Stopped — waiting for the server…", fg=typer.colors.CYAN)
    try:
        try:
            answer = upload.result(timeout=ANSWER_WAIT)
        except Exception as e:  # stalled, timed out, connection lost
            typer.secho(f"⚠️  No answer yet ({e}); asking the server…", fg=typer.colors.YELLOW)
            answer = recover(base_url, url, rec_id, spool, live=upload)
        return outcome, check_answer(answer)
    except Exception as e:
        raise ServerFailed(str(e)) from e


def fetch_status(base_url: str, rec_id: str) -> Optional[dict]:
    """What the server knows of a recording, or None when it cannot be reached."""
    try:
        return net.session().get(f"{base_url}/result/{rec_id}", timeout=(5, 10)).json()
    except (requests.RequestException, ValueError):
        return None


def upload_file(url: str, path: Path, timeout: float = 600, on_progress: Optional[Callable[[int], None]] = None) -> dict:
    def blocks(f):
        while block := f.read(64 * 1024):
            on_progress(len(block))
            yield block

    with open(path, "rb") as f:
        resp = net.session().post(url, data=blocks(f) if on_progress else f, timeout=(5, timeout))
    resp.raise_for_status()
    return resp.json()


class BackgroundUpload:
    """Sends a saved recording from a background thread, so the client can keep
    asking the server about it meanwhile. ``progress()`` is the number of bytes
    handed to the network so far."""

    def __init__(self, url: str, path: Path):
        self._sent = 0
        self._answer: Optional[dict] = None
        self._error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._run, args=(url, path), daemon=True)
        self._thread.start()

    def _count(self, n: int) -> None:
        self._sent += n

    def _run(self, url: str, path: Path) -> None:
        try:
            self._answer = upload_file(url, path, on_progress=self._count)
        except BaseException as e:  # reported by result()
            self._error = e

    def done(self) -> bool:
        return not self._thread.is_alive()

    def progress(self) -> int:
        return self._sent

    def result(self, timeout: Optional[float] = None) -> dict:
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError("the upload is still running")
        if self._error is not None:
            raise self._error
        return self._answer


def recover(base_url: str, url: str, rec_id: str, path: Path, live=None) -> dict:
    """Get the answer for a recording whose upload did not answer.

    Polls the server: ``done`` is the answer; ``processing`` and a growing
    ``receiving`` count are waited for, and so is a resend whose own bytes are
    still moving (status checks can fail on a bad link while the upload works).
    ``unknown`` with no upload running, or no progress (or no server) for
    STALL_TIMEOUT, resends ``path`` in the background, at most RESEND_ATTEMPTS
    times. ``live`` is the original upload, whose answer is still taken if it
    arrives.
    """
    deadline = time.monotonic() + RECOVERY_TIMEOUT
    progress_at, received, sent, resends = time.monotonic(), -1, -1, 0
    while time.monotonic() < deadline:
        if live is not None and live.done():
            try:
                return live.result(timeout=0)
            except Exception as e:
                if resends:
                    typer.secho(f"⚠️  Upload failed ({e}).", fg=typer.colors.YELLOW)
                live = None
        status = fetch_status(base_url, rec_id) or {}
        state = status.get("state")
        if state == "done":
            return status["result"]
        live_sent = getattr(live, "progress", lambda: -1)() if live is not None else -1
        if (
            state == "processing"
            or (state == "receiving" and status.get("received", 0) > received)
            or live_sent > sent
        ):
            received = status.get("received", received)
            sent = live_sent
            progress_at = time.monotonic()
        elif (state == "unknown" and live is None) or time.monotonic() - progress_at > STALL_TIMEOUT:
            if resends == RESEND_ATTEMPTS:
                break
            if resends:
                time.sleep(RETRY_DELAY * resends)
            resends += 1
            typer.secho(f"   Sending the saved recording ({resends}/{RESEND_ATTEMPTS})…", fg=typer.colors.YELLOW)
            live = BackgroundUpload(url, path)
            progress_at, received, sent = time.monotonic(), -1, -1
            continue
        time.sleep(POLL_INTERVAL)
    raise ServerFailed(f"no answer after {resends} resend(s)")


def send_recording(base_url: str, profile: str, overrides: Dict[str, str], path: Path) -> dict:
    """Upload a saved recording, or fetch its result if the server already has it."""
    rec_id = recording_id(path)
    return recover(base_url, transcribe_url(base_url, profile, overrides, rec_id), rec_id, path)


def run_client(
    profile: str, overrides: Dict[str, str], base_url: str, audio_file: Optional[Path] = None
) -> None:
    """The client role: everything is processed by the server at ``base_url``."""
    typer.secho(f"[Client] {profile} · dictation server {base_url}", fg=typer.colors.BLUE)
    if not server_is_up(base_url, timeout=2):
        typer.secho(
            f"❌ The dictation server at {base_url} is not reachable. Is Tailscale up, and is "
            "the server running on the host (`dictation-hub server status`)?",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    resend = f"dictation-hub {profile} -f"
    if audio_file is not None:
        typer.secho(f"   Uploading {audio_file}", fg=typer.colors.BLUE)
        try:
            answer = check_answer(send_recording(base_url, profile, overrides, audio_file))
        except Exception as e:
            typer.secho(f"❌ The server did not deliver ({e}).", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        deliver(answer.get("raw_text", ""), answer.get("final_text", ""), answer.get("error"))
        return

    spool = new_spool(profile)
    try:
        outcome, answer = record_via_server(base_url, profile, overrides, spool)
    except ServerFailed as e:
        typer.secho(
            f"❌ The server did not deliver ({e}). The recording is kept; send it again with:\n"
            f"  {resend} {spool}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    if outcome == "process":
        spool.unlink(missing_ok=True)  # the server keeps its own copy in its workspace
        deliver(answer.get("raw_text", ""), answer.get("final_text", ""), answer.get("error"))
    elif outcome == "cancel":
        spool.unlink(missing_ok=True)
        typer.secho("Recording cancelled and discarded.", fg=typer.colors.YELLOW)
    elif outcome == "quit":
        typer.secho(
            f"Recording kept without processing. Send it with:\n  {resend} {spool}",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho("❌ Audio capture failed (is the microphone available?).", fg=typer.colors.RED)
        raise typer.Exit(code=1)
