"""The shared processing flow: a recorded capture in, a completed workspace out.

Used by the local runner (live recording or --file) and by the server, so the
steps after audio capture exist exactly once.
"""
from pathlib import Path
from typing import Dict, List

import typer

from ..pipeline.engine import PIPELINE_MAP, setup_logging
from .config_manager import REPO_ROOT, generate_temp_env
from .postprocess import PostProcessResult, dispatch_webhook, run_post_processing
from .record_audio import prepare_audio
from .utils import save_metadata
from .whisper_interface import whisper_transcribe


class TranscriptionError(RuntimeError):
    """Whisper did not produce a transcript."""


def process_capture(
    capture: Path,
    workspace: Path,
    timestamp: str,
    profile: str,
    pipeline_config: dict,
    static_config,
    overrides: Dict[str, str],
    valid_args: List[str],
) -> PostProcessResult:
    """Prepare audio, transcribe, post-process and deliver one capture.

    ``profile`` must already be validated against ``pipeline_config``. Raises
    TranscriptionError if whisper fails; post-processing failures degrade to
    the scrubbed text (see run_post_processing).
    """
    suffixes = static_config.suffixes
    profile_data = pipeline_config["profiles"][profile]
    file_wav = workspace / f"{timestamp}{suffixes.audio}"
    file_json = workspace / f"{timestamp}{suffixes.full_json}"

    prepare_audio(capture, file_wav)

    temp_env_path = generate_temp_env(
        base_env_path=REPO_ROOT / "configs" / profile_data.get("env", "standard.env"),
        env_overrides=profile_data.get("env_overrides", {}),
        cli_overrides=overrides,
        valid_args=valid_args,
    )
    try:
        whisper_transcribe(
            input_wav=file_wav,
            config=temp_env_path,
            output_base=file_json.with_suffix(""),
        )
    except typer.Exit as e:
        raise TranscriptionError("Whisper execution failed.") from e
    finally:
        temp_env_path.unlink(missing_ok=True)
    if not file_json.is_file():
        raise TranscriptionError(f"Whisper output {file_json.name} was not created.")

    save_metadata(workspace, file_json, profile, timestamp)
    setup_logging(workspace)
    pipeline_class = PIPELINE_MAP.get(profile, PIPELINE_MAP["standard"])
    pipeline = pipeline_class(
        repo_root=REPO_ROOT,
        static_config=static_config,
        profile_data=profile_data,
        workspace_dir=workspace,
        user_information=pipeline_config.get("user_information", {}),
    )
    result = run_post_processing(pipeline, file_json, workspace, timestamp, suffixes)
    dispatch_webhook(profile_data, result, workspace)
    (workspace / ".completed").touch()
    return result
