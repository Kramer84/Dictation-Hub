import os
import shutil
from pathlib import Path
from typing import Dict, Optional

import typer

from ..client.remote import ServerFailed, new_spool, record_via_server
from ..client.uploader import server_is_up
from ..pipeline.core.static_config import WhisperPipelineConfig
from .config_manager import create_workspace, get_config_dir, load_json_config
from .console import deliver, short_path, show_chunk
from .invocation import WHISPER_OPTIONS
from .recorder import record_from_mic
from .session import TranscriptionError, complete_session, process_capture, start_transcription


def server_base_url() -> str:
    """The host's own dictation server: it always listens on localhost too,
    so this works even while Tailscale is down."""
    return f"http://127.0.0.1:{os.environ.get('HOST_BIND_PORT', '8000')}"


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
        deliver(result.raw_text, result.final_text, result.error)
        return
    _process_locally(capture, workspace, timestamp, profile, pipeline_config, static_config, cli_overrides)


def _run_via_server(base_url, profile, overrides, pipeline_config, static_config) -> None:
    spool = new_spool(profile)
    typer.secho(f"[Host] {profile} · dictation server {base_url}", fg=typer.colors.BLUE)
    try:
        outcome, answer = record_via_server(base_url, profile, overrides, spool, record=record_from_mic)
    except ServerFailed as e:
        typer.secho(
            f"[Router] Server processing failed ({e}); processing locally instead.",
            fg=typer.colors.YELLOW,
        )
        workspace, timestamp = _new_workspace(static_config, profile)
        capture = workspace / f"{timestamp}_capture.wav"
        shutil.move(str(spool), capture)
        _process_locally(capture, workspace, timestamp, profile, pipeline_config, static_config, overrides)
        return
    if outcome != "process":
        if outcome == "cancel":
            spool.unlink(missing_ok=True)
            typer.secho("[Router] Recording cancelled and discarded.", fg=typer.colors.YELLOW)
            return
        workspace, timestamp = _new_workspace(static_config, profile)
        capture = workspace / f"{timestamp}_capture.wav"
        shutil.move(str(spool), capture)
        _handle_unprocessed(outcome, capture, workspace, profile)
        return
    spool.unlink(missing_ok=True)  # the server keeps its own copy in its workspace
    deliver(answer.get("raw_text", ""), answer.get("final_text", ""), answer.get("error"))


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
    deliver(result.raw_text, result.final_text, result.error)
