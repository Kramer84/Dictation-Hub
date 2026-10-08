import shutil
from pathlib import Path
from typing import Dict, Optional

import typer

from ..pipeline.core.static_config import WhisperPipelineConfig
from .config_manager import create_workspace, get_config_dir, load_json_config
from .invocation import WHISPER_OPTIONS
from .recorder import record_from_mic
from .session import TranscriptionError, process_capture
from .utils import copy_to_clipboard


def run_local_pipeline(
    profile: str, cli_overrides: Dict[str, str], audio_file: Optional[Path] = None
) -> None:
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
    base_dir_raw = str(static_config.storage.base_dir)
    folder_format = static_config.storage.folder_format
    workspace, timestamp = create_workspace(base_dir_raw, folder_format, profile)
    typer.secho(
        "========================================================", fg=typer.colors.BLUE
    )
    typer.secho(
        f" Workspace Created: {workspace} (Profile: {profile})",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.secho(
        "========================================================\n",
        fg=typer.colors.BLUE,
    )
    if audio_file is not None:
        capture = workspace / f"{timestamp}_capture{audio_file.suffix}"
        shutil.copy2(audio_file, capture)
        typer.secho(f"[Router] Reprocessing {audio_file}", fg=typer.colors.MAGENTA)
    else:
        capture = workspace / f"{timestamp}_capture.wav"
        outcome = record_from_mic(capture)
        if outcome == "cancel":
            shutil.rmtree(workspace, ignore_errors=True)
            typer.secho("[Router] Recording cancelled and discarded.", fg=typer.colors.YELLOW)
            return
        if outcome == "quit":
            typer.secho(
                f"[Router] Recording kept without processing. Reprocess with:\n"
                f"  dictation-hub {profile} -f {capture}",
                fg=typer.colors.YELLOW,
            )
            return
        if outcome == "failed" or not capture.is_file():
            typer.secho(
                "[Router] Error: audio capture failed (is the microphone available?).",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
    try:
        result = process_capture(
            capture=capture,
            workspace=workspace,
            timestamp=timestamp,
            profile=profile,
            pipeline_config=pipeline_config,
            static_config=static_config,
            overrides=cli_overrides,
            valid_args=sorted(WHISPER_OPTIONS),
        )
    except TranscriptionError as e:
        typer.secho(f"[Router] Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if result.error:
        typer.secho(
            f"[Router] Post-processing failed ({result.error}). Falling back to scrubbed text.",
            fg=typer.colors.YELLOW,
        )
    typer.secho("\n=== RAW TEXT ===", bold=True)
    typer.echo(result.raw_text)
    if result.raw_text != result.final_text and result.final_text:
        typer.secho("\n=== POST-PROCESSED TEXT ===", bold=True)
        typer.echo(result.final_text)
    copy_to_clipboard(result.final_text or result.raw_text)
