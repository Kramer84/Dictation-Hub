import json
from pathlib import Path
from typing import List

import typer

from ..pipeline.core.static_config import WhisperPipelineConfig
from ..pipeline.engine import PIPELINE_MAP, setup_logging
from ..server.n8n_dispatcher import push_to_n8n
from .config_manager import (
    REPO_ROOT,
    create_workspace,
    generate_temp_env,
    get_config_dir,
    load_json_config,
)
from .record_audio import record_audio_app
from .utils import copy_to_clipboard, save_metadata
from .whisper_interface import whisper_transcribe


def run_local_pipeline(profile: str, args: List[str]) -> None:

    try:
        pipeline_config = load_json_config("pipeline_config.json")
        static_json_path = get_config_dir() / "static.json"

        static_config = WhisperPipelineConfig.load_from_file(static_json_path)
    except Exception as e:
        typer.secho(f"Error loading configurations: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    cli_overrides = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                cli_overrides[key] = args[i + 1]
                i += 2
            else:
                cli_overrides[key] = "true"
                i += 1
        else:
            i += 1

    if profile not in pipeline_config.get("profiles", {}):
        typer.secho(
            f"[Router] Profile '{profile}' not found. Defaulting to standard.",
            fg=typer.colors.YELLOW,
        )
        profile = "standard"

    profile_data = pipeline_config["profiles"][profile]
    base_env_filename = profile_data.get("env")
    valid_args = pipeline_config.get("valid_arguments", [])
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

        try:
            pipeline = pipeline_class(
                repo_root=REPO_ROOT,
                static_config=static_config,
                profile_data=profile_data,
                workspace_dir=workspace,
                user_information=user_info,
            )
            final_text = pipeline.execute(file_json)
        except Exception as e:
            typer.secho(
                f"[Router] Error during Python Pipeline execution: {e}",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)

        final_txt_path = workspace / f"{timestamp}{static_config.suffixes.final_text}"
        final_txt_path.write_text(final_text, encoding="utf-8")

        raw_txt_path = workspace / f"{timestamp}{static_config.suffixes.raw_text}"
        raw_text = (
            raw_txt_path.read_text(encoding="utf-8").strip()
            if raw_txt_path.exists()
            else ""
        )

        webhook_url = profile_data.get("webhook_url")
        if webhook_url:
            typer.secho(
                f"[Router] Routing '{profile}' payload to n8n...", fg=typer.colors.CYAN
            )
            try:
                payload = json.loads(final_text)
            except json.JSONDecodeError:
                payload = {"text_content": final_text}

            push_to_n8n(webhook_url, payload, workspace.name)

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
