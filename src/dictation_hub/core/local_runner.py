import os
import shutil
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlencode

import typer

from ..client.uploader import ServerUpload, server_is_up
from ..pipeline.core.static_config import WhisperPipelineConfig
from .config_manager import create_workspace, get_config_dir, load_json_config
from .console import rule, short_path, show_chunk
from .invocation import WHISPER_OPTIONS
from .recorder import record_from_mic
from .session import TranscriptionError, complete_session, process_capture, start_transcription
from .utils import copy_to_clipboard


def server_base_url() -> str:
    protocol = os.environ.get("SERVER_PROTOCOL", "http")
    ip = os.environ.get("SERVER_IP", "127.0.0.1")
    port = os.environ.get("SERVER_PORT", "8000")
    return f"{protocol}://{ip}:{port}"


def run_local_pipeline(
    profile: str,
    cli_overrides: Dict[str, str],
    audio_file: Optional[Path] = None,
    force_local: bool = False,
) -> None:
    """Record (or take ``audio_file``), transcribe and deliver.

    A live recording is streamed to the dictation server when it is running,
    so it is processed there; otherwise (or with ``force_local``) everything
    runs in this process.
    """
    try:
        pipeline_config = load_json_config("pipeline_config.json")
        static_json_path = get_config_dir() / "static.json"
        static_config = WhisperPipelineConfig.load_from_file(static_json_path)
    except Exception as e:
        typer.secho(f"Error loading configurations: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if profile not in pipeline_config.get("profiles", {}):
        known = ", ".join(sorted(pipeline_config.get("profiles", {})))
        typer.secho(
            f"Error: Unknown profile '{profile}'. Known profiles: {known}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    if audio_file is None and not force_local:
        base_url = server_base_url()
        rejected = set(cli_overrides) - set(pipeline_config.get("valid_arguments", []))
        if server_is_up(base_url):
            if not rejected:
                _run_via_server(base_url, profile, cli_overrides, pipeline_config, static_config)
                return
            typer.secho(
                f"[Router] The server does not accept {', '.join(sorted(rejected))}; processing locally.",
                fg=typer.colors.YELLOW,
            )

    workspace, timestamp = _new_workspace(static_config, profile)
    typer.secho(f"[Host] {profile} · local GPU · {short_path(workspace)}", fg=typer.colors.MAGENTA)
    if audio_file is not None:
        capture = workspace / f"{timestamp}_capture{audio_file.suffix}"
        shutil.copy2(audio_file, capture)
        typer.secho(f"   Reprocessing {audio_file}", fg=typer.colors.MAGENTA)
    else:
        # Live: chunks are transcribed while recording, only the last one remains at stop.
        capture = workspace / f"{timestamp}_capture.wav"
        job = start_transcription(
            capture, workspace, timestamp, profile, pipeline_config,
            overrides=cli_overrides, valid_args=sorted(WHISPER_OPTIONS), live=True,
            on_chunk=show_chunk,
        )
        outcome = record_from_mic(capture, listeners=[job])
        if outcome != "process":
            job.cancel()
        if not _handle_unprocessed(outcome, capture, workspace, profile):
            return
        typer.secho("⏹  Stopped — finishing the last chunk, then cleanup and grammar…", fg=typer.colors.CYAN)
        try:
            result = complete_session(job, workspace, timestamp, profile, pipeline_config, static_config)
        except TranscriptionError as e:
            typer.secho(f"[Router] Error: {e}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        _deliver(result.raw_text, result.final_text, result.error)
        return
    _process_locally(capture, workspace, timestamp, profile, pipeline_config, static_config, cli_overrides)


def _run_via_server(base_url, profile, overrides, pipeline_config, static_config) -> None:
    spool_dir = get_config_dir() / "spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    spool = spool_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{profile}.wav"
    upload = ServerUpload(f"{base_url}/transcribe?{urlencode({'profile': profile, **overrides})}")
    typer.secho(f"[Host] {profile} · dictation server {base_url}", fg=typer.colors.BLUE)

    outcome = record_from_mic(spool, listeners=[upload])
    if outcome != "process":
        upload.abort()
        if outcome == "cancel":
            spool.unlink(missing_ok=True)
            typer.secho("[Router] Recording cancelled and discarded.", fg=typer.colors.YELLOW)
            return
        workspace, timestamp = _new_workspace(static_config, profile)
        capture = workspace / f"{timestamp}_capture.wav"
        shutil.move(str(spool), capture)
        _handle_unprocessed(outcome, capture, workspace, profile)
        return

    typer.secho("⏹  Stopped — waiting for the server…", fg=typer.colors.CYAN)
    try:
        answer = upload.result()
    except Exception as e:
        typer.secho(
            f"[Router] Server processing failed ({e}); processing locally instead.",
            fg=typer.colors.YELLOW,
        )
        workspace, timestamp = _new_workspace(static_config, profile)
        capture = workspace / f"{timestamp}_capture.wav"
        shutil.move(str(spool), capture)
        _process_locally(capture, workspace, timestamp, profile, pipeline_config, static_config, overrides)
        return
    spool.unlink(missing_ok=True)  # the server keeps its own copy in its workspace
    _deliver(answer.get("raw_text", ""), answer.get("final_text", ""), answer.get("error"))


def _new_workspace(static_config, profile):
    return create_workspace(
        str(static_config.storage.base_dir), static_config.storage.folder_format, profile
    )


def _handle_unprocessed(outcome: str, capture: Path, workspace: Path, profile: str) -> bool:
    """Apply a non-processing recording outcome; True means: go on and process."""
    if outcome == "cancel":
        shutil.rmtree(workspace, ignore_errors=True)
        typer.secho("[Router] Recording cancelled and discarded.", fg=typer.colors.YELLOW)
        return False
    if outcome == "quit":
        typer.secho(
            f"[Router] Recording kept without processing. Reprocess with:\n"
            f"  dictation-hub {profile} -f {capture}",
            fg=typer.colors.YELLOW,
        )
        return False
    if outcome == "failed" or not capture.is_file():
        typer.secho(
            "[Router] Error: audio capture failed (is the microphone available?).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    return True


def _process_locally(capture, workspace, timestamp, profile, pipeline_config, static_config, overrides):
    try:
        result = process_capture(
            capture=capture,
            workspace=workspace,
            timestamp=timestamp,
            profile=profile,
            pipeline_config=pipeline_config,
            static_config=static_config,
            overrides=overrides,
            valid_args=sorted(WHISPER_OPTIONS),
            on_chunk=show_chunk,
        )
    except TranscriptionError as e:
        typer.secho(f"[Router] Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    _deliver(result.raw_text, result.final_text, result.error)


def _deliver(raw_text: str, final_text: str, error: Optional[str]) -> None:
    """Show the delivered text once and copy it. The scrubbed raw text stays in
    the workspace (<ts>_raw.txt) and is what gets delivered if the LLM failed."""
    if error:
        typer.secho(
            f"⚠️ Post-processing failed ({error}); delivering the scrubbed text.",
            fg=typer.colors.YELLOW,
        )
    text = final_text or raw_text
    typer.echo(rule())
    typer.echo(text)
    typer.echo(rule())
    copy_to_clipboard(text)
