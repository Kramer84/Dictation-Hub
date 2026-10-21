"""Dictate through the dictation server: the client role on a remote machine,
and the host CLI when its own server is running.

The recording is streamed to the server while it is made, and kept in a local
spool file until the server has answered, so a lost connection loses no audio.
"""
import os
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlencode

import requests
import typer

from ..core.config_manager import get_config_dir
from ..core.console import deliver
from ..core.recorder import record_from_mic
from .uploader import ServerUpload, UploadStalled, server_is_up


RESEND_ATTEMPTS = 3
RETRY_DELAY = 3.0  # seconds, multiplied by the attempt number


class ServerFailed(RuntimeError):
    """The server did not deliver a transcript."""


def client_base_url() -> str:
    protocol = os.environ.get("SERVER_PROTOCOL", "http")
    ip = os.environ.get("SERVER_IP", "127.0.0.1")
    port = os.environ.get("SERVER_PORT", "8000")
    return f"{protocol}://{ip}:{port}"


def transcribe_url(base_url: str, profile: str, overrides: Dict[str, str]) -> str:
    return f"{base_url}/transcribe?{urlencode({'profile': profile, **overrides})}"


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
    upload = ServerUpload(transcribe_url(base_url, profile, overrides))
    outcome = (record or record_from_mic)(spool, listeners=[upload])
    if outcome != "process":
        upload.abort()
        return outcome, None
    typer.secho("⏹  Stopped — waiting for the server…", fg=typer.colors.CYAN)
    url = transcribe_url(base_url, profile, overrides)
    try:
        try:
            answer = upload.result()
        except UploadStalled as e:
            typer.secho(f"⚠️  Live upload stalled ({e}); resending the saved recording…", fg=typer.colors.YELLOW)
            answer = send_recording(url, spool)
        return outcome, check_answer(answer)
    except Exception as e:
        raise ServerFailed(str(e)) from e


def upload_file(url: str, path: Path) -> dict:
    with open(path, "rb") as f:
        resp = requests.post(url, data=f, timeout=(5, 600))
    resp.raise_for_status()
    return resp.json()


def send_recording(url: str, path: Path) -> dict:
    """Upload a saved recording, retrying when the network drops or times out."""
    for attempt in range(1, RESEND_ATTEMPTS + 1):
        try:
            return upload_file(url, path)
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == RESEND_ATTEMPTS:
                raise
            typer.secho(f"⚠️  Upload failed ({e}); retrying ({attempt}/{RESEND_ATTEMPTS - 1})…", fg=typer.colors.YELLOW)
            time.sleep(RETRY_DELAY * attempt)


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
            answer = check_answer(send_recording(transcribe_url(base_url, profile, overrides), audio_file))
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
