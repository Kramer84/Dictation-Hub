import os
import subprocess
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from dotenv import dotenv_values


def load_env_config(config_path: Path) -> dict[str, str]:
    if not config_path.is_file():
        return {}

    # dotenv natively strips inline comments, quotes, and whitespace safely
    parsed_env = dotenv_values(config_path)

    # Ensure all values are returned as strings (dotenv can sometimes return None)
    return {k: str(v) for k, v in parsed_env.items() if v is not None}


def apply_config_overrides(
    config_path: Path, current_args: dict[str, Any]
) -> dict[str, Any]:

    env_vars = load_env_config(config_path)
    updated_args = current_args.copy()

    for key, val in env_vars.items():
        lower_key = key.lower()
        if lower_key in updated_args:
            original_value = updated_args[lower_key]

            if isinstance(original_value, bool):
                updated_args[lower_key] = val.lower() in ("true", "1", "yes")
            elif isinstance(original_value, int):
                updated_args[lower_key] = int(val)
            elif isinstance(original_value, float):
                updated_args[lower_key] = float(val)
            elif isinstance(original_value, Path) or original_value is None:
                updated_args[lower_key] = Path(os.path.expandvars(val)).expanduser()
            else:
                updated_args[lower_key] = val

    return updated_args


def resolve_paths(
    input_wav: Path, whisper_dir: Path, model: str, output_base: Optional[Path]
) -> tuple[Path, Path, Path]:

    if not input_wav.is_file():
        raise FileNotFoundError(f"A valid input audio file must be passed: {input_wav}")

    cli_exec = whisper_dir / "build" / "bin" / "whisper-cli"
    if not cli_exec.is_file() and not os.access(cli_exec, os.X_OK):
        raise FileNotFoundError(
            f"whisper-cli executable not found or not executable at {cli_exec}"
        )

    model_path = whisper_dir / "models" / f"ggml-{model}.bin"

    if output_base:
        resolved_output_base = output_base.with_suffix("")
        resolved_output_base.parent.mkdir(parents=True, exist_ok=True)
    else:
        resolved_output_base = input_wav.with_suffix("")

    return cli_exec, model_path, resolved_output_base


def build_whisper_command(args: dict[str, Any]) -> list[str]:

    cmd = [
        str(args["cli_exec"]),
        "-f",
        str(args["input_wav"]),
        "-m",
        str(args["model_path"]),
        "-t",
        str(args["threads"]),
        "-p",
        str(args["processors"]),
        "-bs",
        str(args["beam_size"]),
        "-ac",
        str(args["audio_ctx"]),
        "-mc",
        str(args["max_context"]),
        "-et",
        str(args["entropy_thold"]),
        "-lpt",
        str(args["logprob_thold"]),
        "-wt",
        str(args["word_thold"]),
        "-l",
        str(args["language"]),
        "-ot",
        str(args["offset_t"]),
        "-on",
        str(args["offset_n"]),
        "-d",
        str(args["duration"]),
        "-ml",
        str(args["max_len"]),
        "-bo",
        str(args["best_of"]),
        "-nth",
        str(args["no_speech_thold"]),
        "--temperature",
        str(args["temperature"]),
        "--temperature-inc",
        str(args["temperature_inc"]),
        "--ov-e-device",
        str(args["ov_e_device"]),
        "--grammar-penalty",
        str(args["grammar_penalty"]),
    ]

    flags_map = {
        "split_on_word": "-sow",
        "debug_mode": "-debug",
        "translate": "-tr",
        "diarize": "-di",
        "tinydiarize": "-tdrz",
        "no_fallback": "-nf",
        "no_prints": "-np",
        "print_special": "-ps",
        "print_colors": "-pc",
        "print_progress": "-pp",
        "no_timestamps": "-nt",
        "detect_language": "-dl",
        "log_score": "-ls",
        "no_gpu": "-ng",
        "flash_attn": "-fa",
        "suppress_nst": "-sns",
        "output_txt": "-otxt",
        "output_vtt": "-ovtt",
        "output_srt": "-osrt",
        "output_lrc": "-olrc",
        "output_words": "-owts",
        "output_csv": "-ocsv",
        "output_json": "-oj",
        "output_json_full": "-ojf",
    }

    for key, flag in flags_map.items():
        if args.get(key):
            cmd.append(flag)

    if args.get("font_path"):
        cmd.extend(["-fp", str(args["font_path"])])
    if args.get("initial_prompt"):
        cmd.extend(["--prompt", str(args["initial_prompt"])])
    if args.get("dtw_model"):
        cmd.extend(["-dtw", str(args["dtw_model"])])
    if args.get("suppress_regex"):
        cmd.extend(["--suppress-regex", str(args["suppress_regex"])])
    if args.get("grammar"):
        cmd.extend(["--grammar", str(args["grammar"])])
    if args.get("grammar_rule"):
        cmd.extend(["--grammar-rule", str(args["grammar_rule"])])

    if args.get("use_vad"):
        vad_model_path = (
            args["whisper_dir"] / "models" / f"ggml-{args['vad_model']}.bin"
        )
        if vad_model_path.is_file():
            cmd.extend(
                [
                    "--vad",
                    "--vad-model",
                    str(vad_model_path),
                    "--vad-threshold",
                    str(args["vad_thold"]),
                    "--vad-min-speech-duration-ms",
                    str(args["vad_min_speech"]),
                    "--vad-min-silence-duration-ms",
                    str(args["vad_min_silence"]),
                    "--vad-max-speech-duration-s",
                    str(args["vad_max_speech"]),
                    "--vad-speech-pad-ms",
                    str(args["vad_pad"]),
                    "--vad-samples-overlap",
                    str(args["vad_overlap"]),
                ]
            )

    cmd.extend(["-of", str(args["resolved_output_base"])])

    return cmd


def whisper_transcribe(
    input_wav: Annotated[
        Path, typer.Option("--input", help="A valid input audio file")
    ],
    config: Annotated[
        Optional[Path],
        typer.Option("--config", help="Environment config file to override defaults"),
    ] = None,
    output_base: Annotated[
        Optional[Path], typer.Option("--output", help="Output base filepath")
    ] = None,
    whisper_dir: Annotated[
        Path, typer.Option(help="Directory containing whisper.cpp base")
    ] = Path.home() / "whisper.cpp",
    model: Annotated[str, typer.Option(help="Whisper model to use")] = "large-v3",
    threads: Annotated[int, typer.Option(help="Number of threads")] = 4,
    processors: Annotated[int, typer.Option(help="Number of processors")] = 1,
    offset_t: Annotated[int, typer.Option(help="Time offset in milliseconds")] = 0,
    offset_n: Annotated[int, typer.Option(help="Segment index offset")] = 0,
    duration: Annotated[
        int, typer.Option(help="Duration of audio to process in ms (0=all)")
    ] = 0,
    max_context: Annotated[int, typer.Option(help="Maximum context length")] = -1,
    max_len: Annotated[
        int, typer.Option(help="Maximum segment length in characters")
    ] = 0,
    split_on_word: Annotated[
        bool, typer.Option(help="Split on word rather than on token")
    ] = False,
    best_of: Annotated[int, typer.Option(help="Number of best candidates to keep")] = 5,
    beam_size: Annotated[int, typer.Option(help="Beam size for beam search")] = 5,
    audio_ctx: Annotated[int, typer.Option(help="Audio context size (0=all)")] = 0,
    word_thold: Annotated[
        float, typer.Option(help="Word timestamp probability threshold")
    ] = 0.01,
    entropy_thold: Annotated[
        float, typer.Option(help="Entropy threshold for decoder fail")
    ] = 2.40,
    logprob_thold: Annotated[
        float, typer.Option(help="Log probability threshold for decoder fail")
    ] = -1.00,
    no_speech_thold: Annotated[
        float, typer.Option(help="No-speech probability threshold")
    ] = 0.60,
    temperature: Annotated[
        float, typer.Option(help="Initial decoding temperature")
    ] = 0.00,
    temperature_inc: Annotated[
        float, typer.Option(help="Temperature increment step")
    ] = 0.20,
    debug_mode: Annotated[bool, typer.Option(help="Enable debug mode")] = False,
    translate: Annotated[
        bool, typer.Option(help="Translate from source language to English")
    ] = False,
    diarize: Annotated[bool, typer.Option(help="Stereo audio diarization")] = False,
    tinydiarize: Annotated[bool, typer.Option(help="Enable tinydiarize")] = False,
    no_fallback: Annotated[
        bool, typer.Option(help="Do not use temperature fallback")
    ] = False,
    output_txt: Annotated[bool, typer.Option(help="Output TXT file")] = False,
    output_vtt: Annotated[bool, typer.Option(help="Output VTT file")] = False,
    output_srt: Annotated[bool, typer.Option(help="Output SRT file")] = False,
    output_lrc: Annotated[bool, typer.Option(help="Output LRC file")] = False,
    output_words: Annotated[
        bool, typer.Option(help="Output per-word timestamps")
    ] = False,
    font_path: Annotated[str, typer.Option(help="Path to font for rendering")] = "",
    output_csv: Annotated[bool, typer.Option(help="Output CSV file")] = False,
    output_json: Annotated[bool, typer.Option(help="Output JSON file")] = False,
    output_json_full: Annotated[
        bool, typer.Option(help="Output full JSON file")
    ] = True,
    no_prints: Annotated[
        bool, typer.Option(help="Do not print transcription progress")
    ] = True,
    print_special: Annotated[bool, typer.Option(help="Print special tokens")] = False,
    print_colors: Annotated[
        bool, typer.Option(help="Print colors for transcription")
    ] = False,
    print_progress: Annotated[bool, typer.Option(help="Print progress")] = False,
    no_timestamps: Annotated[
        bool, typer.Option(help="Do not print timestamps")
    ] = False,
    language: Annotated[
        str, typer.Option(help="Language code (e.g., 'en', 'auto')")
    ] = "auto",
    detect_language: Annotated[
        bool, typer.Option(help="Automatically detect language")
    ] = False,
    initial_prompt: Annotated[
        str, typer.Option(help="Initial prompt for the model")
    ] = "",
    ov_e_device: Annotated[str, typer.Option(help="OpenVINO execution device")] = "CPU",
    dtw_model: Annotated[str, typer.Option(help="DTW Model path")] = "",
    log_score: Annotated[bool, typer.Option(help="Log best decoder scores")] = False,
    no_gpu: Annotated[bool, typer.Option(help="Disable GPU computation")] = False,
    flash_attn: Annotated[bool, typer.Option(help="Use flash attention")] = False,
    suppress_nst: Annotated[
        bool, typer.Option(help="Suppress non-speech tokens")
    ] = True,
    suppress_regex: Annotated[
        str, typer.Option(help="Regex expression to suppress")
    ] = "",
    grammar: Annotated[str, typer.Option(help="Grammar path")] = "",
    grammar_rule: Annotated[str, typer.Option(help="Grammar rule name")] = "",
    grammar_penalty: Annotated[float, typer.Option(help="Grammar penalty")] = 100.0,
    use_vad: Annotated[bool, typer.Option(help="Use Silero VAD")] = False,
    vad_model: Annotated[
        str, typer.Option(help="Silero VAD model name")
    ] = "silero-v6.2.0",
    vad_thold: Annotated[float, typer.Option(help="VAD threshold")] = 0.60,
    vad_min_speech: Annotated[
        int, typer.Option(help="VAD min speech duration ms")
    ] = 100,
    vad_min_silence: Annotated[
        int, typer.Option(help="VAD min silence duration ms")
    ] = 100,
    vad_max_speech: Annotated[int, typer.Option(help="VAD max speech duration s")] = 30,
    vad_pad: Annotated[int, typer.Option(help="VAD speech pad ms")] = 50,
    vad_overlap: Annotated[float, typer.Option(help="VAD samples overlap")] = 0.10,
):

    current_args = locals().copy()

    if config:
        current_args = apply_config_overrides(config, current_args)

    try:
        cli_exec, model_path, resolved_output_base = resolve_paths(
            input_wav=current_args["input_wav"],
            whisper_dir=current_args["whisper_dir"],
            model=current_args["model"],
            output_base=current_args["output_base"],
        )

        current_args.update(
            {
                "cli_exec": cli_exec,
                "model_path": model_path,
                "resolved_output_base": resolved_output_base,
            }
        )

    except FileNotFoundError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    cmd = build_whisper_command(current_args)

    typer.secho("-> Executing Whisper Inference...", fg=typer.colors.CYAN)

    result = subprocess.run(cmd)

    if result.returncode == 0:
        typer.secho(
            f"✅ Transcription process complete using target: {resolved_output_base}",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho("❌ Whisper execution failed.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
