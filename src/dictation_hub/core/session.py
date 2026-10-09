"""The shared processing flow: a recorded capture in, a completed workspace out.

Used by the local runner (live recording or --file) and by the server, so the
steps after audio capture exist exactly once. Audio is transcribed in
pause-cut chunks (core/chunker.py): during a live recording the chunks are
transcribed while the user keeps talking, so only the last one is left when
recording stops.
"""
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional

import typer

from ..pipeline.engine import PIPELINE_MAP, setup_logging
from .chunker import ChunkedTranscription, is_streamable_wav
from .config_manager import REPO_ROOT, generate_temp_env
from .postprocess import PostProcessResult, dispatch_webhook, run_post_processing
from .record_audio import prepare_audio
from .utils import save_metadata
from .whisper_interface import load_env_config, speech_pauses, whisper_transcribe

logger = logging.getLogger(__name__)

ChunkReporter = Callable[[int, str], None]


class TranscriptionError(RuntimeError):
    """Whisper did not produce a transcript."""


def _base_env(pipeline_config: dict, profile: str) -> Path:
    return REPO_ROOT / "configs" / pipeline_config["profiles"][profile].get("env", "standard.env")


def _chunk_transcriber(
    pipeline_config: dict,
    profile: str,
    overrides: Dict[str, str],
    valid_args: List[str],
    on_chunk: Optional[ChunkReporter] = None,
) -> Callable[[Path, int], dict]:
    """Per chunk: buzz filter and gain, then whisper. The language found on the
    first chunk (when not given) is locked for the following ones. Each chunk's
    text is logged and passed to ``on_chunk(index, text)``."""
    profile_data = pipeline_config["profiles"][profile]
    state = {"language": overrides.get("language")}

    def transcribe(chunk_wav: Path, index: int) -> dict:
        prepared = chunk_wav.with_name(f"{chunk_wav.stem}_prepared.wav")
        prepare_audio(chunk_wav, prepared)
        chunk_overrides = dict(overrides)
        if state["language"] and state["language"] != "auto":
            chunk_overrides["language"] = state["language"]
        temp_env = generate_temp_env(
            base_env_path=_base_env(pipeline_config, profile),
            env_overrides=profile_data.get("env_overrides", {}),
            cli_overrides=chunk_overrides,
            valid_args=list(valid_args) + ["language"],
        )
        out_base = chunk_wav.with_suffix("")
        try:
            whisper_transcribe(input_wav=prepared, config=temp_env, output_base=out_base)
        except typer.Exit as e:
            raise TranscriptionError("Whisper execution failed.") from e
        finally:
            temp_env.unlink(missing_ok=True)
        out_json = Path(f"{out_base}.json")
        if not out_json.is_file():
            raise TranscriptionError(f"Whisper output for chunk {index} was not created.")
        data = json.loads(out_json.read_text(encoding="utf-8"))
        detected = data.get("result", {}).get("language")
        if (not state["language"] or state["language"] == "auto") and detected and detected != "auto":
            state["language"] = detected
        text = " ".join(s.get("text", "").strip() for s in data.get("transcription", [])).strip()
        logger.info("Chunk %d: %s", index + 1, text)
        if on_chunk is not None:
            on_chunk(index, text)
        return data

    return transcribe


def start_transcription(
    capture: Path,
    workspace: Path,
    timestamp: str,
    profile: str,
    pipeline_config: dict,
    overrides: Dict[str, str],
    valid_args: List[str],
    live: bool = True,
    on_chunk: Optional[ChunkReporter] = None,
) -> ChunkedTranscription:
    """Begin chunked transcription of ``capture`` (16 kHz mono WAV).

    With ``live`` the file is still being recorded: feed the job as a recorder
    or upload listener (``None`` ends the recording) and call
    ``complete_session`` afterwards.
    """
    setup_logging(workspace)
    env = load_env_config(_base_env(pipeline_config, profile))
    whisper_dir = env.get("WHISPER_DIR", "~/whisper.cpp")
    vad_model = env.get("VAD_MODEL", "silero-v6.2.0")
    return ChunkedTranscription(
        capture,
        workspace / "chunks",
        _chunk_transcriber(pipeline_config, profile, overrides, valid_args, on_chunk),
        lambda window: speech_pauses(window, whisper_dir, vad_model),
        complete=not live,
    )


def complete_session(
    job: ChunkedTranscription,
    workspace: Path,
    timestamp: str,
    profile: str,
    pipeline_config: dict,
    static_config,
) -> PostProcessResult:
    """Wait for the last chunk, then metadata, post-processing and delivery."""
    try:
        transcript = job.finish()
    except TranscriptionError:
        raise
    except Exception as e:
        raise TranscriptionError(f"Chunked transcription failed: {e}") from e
    suffixes = static_config.suffixes
    profile_data = pipeline_config["profiles"][profile]
    file_json = workspace / f"{timestamp}{suffixes.full_json}"
    file_json.write_text(json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")
    for chunk_audio in (workspace / "chunks").glob("*.wav"):
        chunk_audio.unlink()  # the capture holds the audio; keep the per-chunk JSON

    save_metadata(workspace, file_json, profile, timestamp)
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


def to_16k_mono(source: Path, target: Path) -> Path:
    """Convert any ffmpeg-readable audio to the 16 kHz mono WAV the chunker reads."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(target)],
        check=True,
    )
    return target


def process_capture(
    capture: Path,
    workspace: Path,
    timestamp: str,
    profile: str,
    pipeline_config: dict,
    static_config,
    overrides: Dict[str, str],
    valid_args: List[str],
    on_chunk: Optional[ChunkReporter] = None,
) -> PostProcessResult:
    """Transcribe and deliver a complete recording (``--file``, uploads that
    could not be chunked live, web UI audio).

    ``profile`` must already be validated against ``pipeline_config``. Raises
    TranscriptionError if whisper fails; post-processing failures degrade to
    the scrubbed text (see run_post_processing).
    """
    with open(capture, "rb") as f:
        header = f.read(4096)
    source = capture
    if not is_streamable_wav(header):
        source = to_16k_mono(capture, workspace / f"{timestamp}_16k.wav")
    job = start_transcription(
        source, workspace, timestamp, profile, pipeline_config, overrides, valid_args,
        live=False, on_chunk=on_chunk,
    )
    return complete_session(job, workspace, timestamp, profile, pipeline_config, static_config)
