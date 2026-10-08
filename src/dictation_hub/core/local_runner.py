import shutil
from pathlib import Path
from typing import Dict, Optional

import typer

from ..pipeline.core.static_config import WhisperPipelineConfig
from ..pipeline.engine import PIPELINE_MAP, setup_logging
from .config_manager import (
    REPO_ROOT,
    create_workspace,
    generate_temp_env,
    get_config_dir,
    load_json_config,
)
from .invocation import WHISPER_OPTIONS
from .postprocess import dispatch_webhook, run_post_processing
from .record_audio import prepare_audio, record_audio_app
from .utils import copy_to_clipboard, save_metadata
from .whisper_interface import whisper_transcribe


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
    profile_data = pipeline_config["profiles"][profile]
    base_env_filename = profile_data.get("env")
    valid_args = sorted(WHISPER_OPTIONS)
    config_full = REPO_ROOT / "configs" / base_env_filename
    base_dir_raw = str(static_config.storage.base_dir)
    folder_format = static_config.storage.folder_format
    workspace, timestamp = create_workspace(base_dir_raw, folder_format, profile)
    file_wav = workspace / f"{timestamp}{static_config.suffixes.audio}"
    file_json = workspace / f"{timestamp}{static_config.suffixes.full_json}"
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
    temp_env_path = generate_temp_env(
        base_env_path=config_full,
        env_overrides=profile_data.get("env_overrides", {}),
        cli_overrides=cli_overrides,
        valid_args=valid_args,
    )
    try:
        if audio_file is not None:
            capture = workspace / f"{timestamp}_capture{audio_file.suffix}"
            shutil.copy2(audio_file, capture)
            typer.secho(f"[Router] Reprocessing {audio_file}", fg=typer.colors.MAGENTA)
            prepare_audio(capture, file_wav)
        else:
            record_audio_app(
                output=file_wav,
                normalize=True,
                remove_silence=False,
                highpass=False,
                record_format="cd",
                record_type="wav",
                sample_rate=16000,
                channels=1,
                codec="pcm_s16le",
                target_peak=-6.0,
                raw_output=workspace / f"{timestamp}_capture.wav",
            )
        if not file_wav.is_file():
            typer.secho(
                "[Router] Error: Audio file was not created. Aborting.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        typer.secho(
            "[Router] Audio capture finalized. Booting Whisper inference...",
            fg=typer.colors.MAGENTA,
        )
        whisper_transcribe(
            input_wav=file_wav,
            config=temp_env_path,
            output_base=file_json.with_suffix(""),
        )
        if not file_json.is_file():
            typer.secho(
                f"[Router] Error: Output {file_json.name} was not created.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        save_metadata(workspace, file_json, profile, timestamp)
        typer.secho(
            "[Router] Handing over to Pipeline Engine...", fg=typer.colors.MAGENTA
        )
        setup_logging(workspace)
        user_info = pipeline_config.get("user_information", {})
        pipeline_class = PIPELINE_MAP.get(profile, PIPELINE_MAP["standard"])
        pipeline = pipeline_class(
            repo_root=REPO_ROOT,
            static_config=static_config,
            profile_data=profile_data,
            workspace_dir=workspace,
            user_information=user_info,
        )
        result = run_post_processing(
            pipeline, file_json, workspace, timestamp, static_config.suffixes
        )
        if result.error:
            typer.secho(
                f"[Router] Post-processing failed ({result.error}). Falling back to scrubbed text.",
                fg=typer.colors.YELLOW,
            )
        raw_text, final_text = result.raw_text, result.final_text
        if profile_data.get("webhook_url"):
            typer.secho(
                f"[Router] Routing '{profile}' payload to n8n...", fg=typer.colors.CYAN
            )
        dispatch_webhook(profile_data, result, workspace)
        (workspace / ".completed").touch()
        typer.secho("\n=== RAW TEXT ===", bold=True)
        typer.echo(raw_text)
        if raw_text != final_text and final_text:
            typer.secho("\n=== POST-PROCESSED TEXT ===", bold=True)
            typer.echo(final_text)
        text_to_copy = final_text if final_text else raw_text
        copy_to_clipboard(text_to_copy)
    finally:
        temp_env_path.unlink(missing_ok=True)
