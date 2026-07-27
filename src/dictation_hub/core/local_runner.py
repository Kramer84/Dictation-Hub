#!/usr/bin/env python3
"""
core/execution_router.py
Execution router orchestrating audio capture, transcription, post-processing, and dispatch.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import typer

# Assuming this script resides in `core/` and the modules are importable
from .core.record_audio import record_audio_app
from .core.whisper_interface import whisper_transcribe

def load_json_config(file_path: Path) -> Dict[str, Any]:
    """
    Load and parse a JSON configuration file.

    Parameters
    ----------
    file_path : Path
        The path to the JSON file.

    Returns
    -------
    dict
        The parsed dictionary from the JSON file.

    Raises
    ------
    FileNotFoundError
        If the configuration file does not exist.
    ValueError
        If the configuration file contains invalid JSON.
    """
    if not file_path.is_file():
        raise FileNotFoundError(f"Configuration file missing: {file_path}")
    
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def verify_virtual_environment(python_exec: Path) -> None:
    """
    Ensure the required Python virtual environment is present and executable.

    Parameters
    ----------
    python_exec : Path
        The path to the Python executable in the virtual environment.

    Raises
    ------
    typer.Exit
        If the executable is not found or lacks execution permissions.
    """
    if not python_exec.is_file() or not os.access(python_exec, os.X_OK):
        typer.secho(
            f"[Router] Error: Python virtual environment not found or not executable.\n"
            f"[Router] Expected at: {python_exec}\n"
            f"[Router] Please run 'bash server/setup_server.sh' to initialize the environment.",
            fg=typer.colors.RED
        )
        raise typer.Exit(code=1)


def create_workspace(base_dir_raw: str, folder_format: str, profile: str) -> Tuple[Path, str]:
    """
    Generate the timestamped workspace directory.

    Parameters
    ----------
    base_dir_raw : str
        The raw base directory string, potentially containing a '~' for home.
    folder_format : str
        The datetime format string to generate the timestamp.
    profile : str
        The selected profile string to append to the folder name.

    Returns
    -------
    tuple[Path, str]
        A tuple containing the resolved workspace Path object and the generated timestamp.
    """
    # Expand user directory equivalent to bash's ${RAW_BASE_DIR/#\~/$HOME}
    base_dir = Path(base_dir_raw).expanduser().resolve()
    
    timestamp = datetime.now().strftime(folder_format)
    workspace_name = f"{timestamp}_{profile}"
    workspace = base_dir / workspace_name
    
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace, timestamp


def generate_temp_env(
    base_env_path: Path, 
    env_overrides: Dict[str, str], 
    cli_overrides: Dict[str, str], 
    valid_args: List[str]
) -> Path:
    """
    Generate a temporary environment file merged with dynamic configuration overrides.

    Parameters
    ----------
    base_env_path : Path
        The path to the base environment configuration file.
    env_overrides : dict
        A dictionary of hard overrides defined in the profile configuration.
    cli_overrides : dict
        A dictionary of dynamic overrides provided via command line.
    valid_args : list[str]
        A list of permissible argument keys that can be overridden.

    Returns
    -------
    Path
        The path to the newly generated temporary environment file.
    """
    if not base_env_path.is_file():
        typer.secho(f"Error: Base environment {base_env_path} not found.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    # Use NamedTemporaryFile with delete=False so external processes can access it safely
    fd, temp_path = tempfile.mkstemp(suffix=".env", text=True)
    
    with open(fd, "w", encoding="utf-8") as out_f:
        # 1. Copy base contents
        with base_env_path.open("r", encoding="utf-8") as in_f:
            out_f.write(in_f.read())
            
        out_f.write("\n# --- DYNAMIC OVERRIDES ---\n")
        
        # 2. Inject profile-specific hard overrides
        for key, val in env_overrides.items():
            out_f.write(f'{key}="{val}"\n')
            
        # 3. Inject validated CLI overrides
        for key, val in cli_overrides.items():
            if key in valid_args:
                out_f.write(f'{key.upper()}="{val}"\n')
            else:
                typer.secho(f"[Router] Warning: Argument '{key}' is not in valid_arguments.", fg=typer.colors.YELLOW)

    return Path(temp_path)


def save_metadata(workspace: Path, file_json: Path, profile: str, timestamp: str) -> None:
    """
    Extract the detected language from the Whisper JSON and save the workspace metadata.

    Parameters
    ----------
    workspace : Path
        The directory path of the active workspace.
    file_json : Path
        The path to the generated Whisper JSON output.
    profile : str
        The active execution profile name.
    timestamp : str
        The current execution timestamp.
    """
    lang_code = "auto"
    if file_json.is_file():
        try:
            with file_json.open("r", encoding="utf-8") as f:
                data = json.load(f)
                lang_code = data.get("result", {}).get("language", "auto")
        except json.JSONDecodeError:
            pass

    metadata_path = workspace / "metadata.json"
    metadata = {
        "profile": profile,
        "language": lang_code,
        "timestamp": timestamp
    }
    
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def copy_to_clipboard(text: str) -> None:
    """
    Copy text to the system clipboard using available display servers (Wayland or X11).

    Parameters
    ----------
    text : str
        The text content to be copied to the clipboard.
    """
    if not text:
        return

    if shutil.which("wl-copy"):
        subprocess.run(["wl-copy"], input=text.encode("utf-8"))
        typer.secho("\n[Router] Copied to Wayland clipboard.", fg=typer.colors.GREEN)
    elif shutil.which("xclip"):
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"))
        typer.secho("\n[Router] Copied to X11 clipboard.", fg=typer.colors.GREEN)
    else:
        typer.secho("\n[Router] Warning: No clipboard utility found (wl-copy/xclip).", fg=typer.colors.YELLOW)


def run_local_pipeline(
    ctx: typer.Context,
    profile: str = typer.Argument("standard", help="The configuration profile to execute."),
    repo_dir: Path = typer.Option(
        Path(__file__).resolve().parent.parent, 
        help="Path to the repository root directory."
    ),
    static_config_name: str = typer.Option("static.json", help="Filename of the static configuration."),
    pipeline_config_name: str = typer.Option("pipeline_config.json", help="Filename of the pipeline configuration.")
) -> None:
    """
    Entry point for the execution router. Orchestrates the workflow from recording 
    to transcription, post-processing, and synchronization.
    """
    config_static_path = repo_dir / "configs" / static_config_name
    config_json_path = repo_dir / "configs" / pipeline_config_name
    python_exec = repo_dir / "server" / "venv" / "bin" / "python"

    # 1. Base Validations
    try:
        static_config = load_json_config(config_static_path)
        pipeline_config = load_json_config(config_json_path)
    except Exception as e:
        typer.secho(f"Error loading configurations: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    verify_virtual_environment(python_exec)

    # 2. Extract CLI Dynamic Overrides (emulating bash shift)
    cli_overrides = {}
    args = ctx.args
    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            # Ensure the next item isn't another flag before assigning it as a value
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                cli_overrides[key] = args[i + 1]
                i += 2
            else:
                cli_overrides[key] = "true"
                i += 1
        else:
            i += 1

    # 3. Profile Setup
    if profile not in pipeline_config.get("profiles", {}):
        typer.secho(f"[Router] Profile '{profile}' not found. Defaulting to standard.", fg=typer.colors.YELLOW)
        profile = "standard"

    profile_data = pipeline_config["profiles"][profile]
    base_env_filename = profile_data.get("env")
    valid_args = pipeline_config.get("valid_arguments", [])
    config_full = repo_dir / "configs" / base_env_filename

    # 4. Initialize Workspace
    base_dir_raw = static_config.get("storage", {}).get("base_dir", "~")
    folder_format = static_config.get("storage", {}).get("folder_format", "%Y%m%d_%H%M%S")
    workspace, timestamp = create_workspace(base_dir_raw, folder_format, profile)

    file_wav = workspace / f"{timestamp}{static_config['suffixes']['audio']}"
    file_json = workspace / f"{timestamp}{static_config['suffixes']['full_json']}"

    typer.secho("========================================================", fg=typer.colors.BLUE)
    typer.secho(f" Workspace Created: {workspace} (Profile: {profile})", fg=typer.colors.CYAN, bold=True)
    typer.secho("========================================================\n", fg=typer.colors.BLUE)

    temp_env_path = generate_temp_env(
        base_env_path=config_full,
        env_overrides=profile_data.get("env_overrides", {}),
        cli_overrides=cli_overrides,
        valid_args=valid_args
    )

    try:
        # 5. Audio Capture Process
        # We explicitly pass all parameters to satisfy typer.Option definitions in record_audio.py
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
            target_peak=-6.0
        )

        if not file_wav.is_file():
            typer.secho("[Router] Error: Audio file was not created. Aborting transcription.", fg=typer.colors.RED)
            raise typer.Exit(code=1)

        typer.secho("[Router] Audio capture finalized. Booting Whisper inference...", fg=typer.colors.MAGENTA)

        # 6. Whisper Inference Process
        whisper_transcribe(
            input_wav=file_wav,
            config=temp_env_path,
            output_base=file_json.with_suffix("") # Whisper logic appends suffix internally
        )

        if not file_json.is_file():
            typer.secho(f"[Router] Error: Output {file_json.name} was not created.", fg=typer.colors.RED)
            raise typer.Exit(code=1)

        # 7. Metadata and Hand-offs
        save_metadata(workspace, file_json, profile, timestamp)
        
        typer.secho("[Router] Handing over to Python Engine...", fg=typer.colors.MAGENTA)
        engine_script = repo_dir / "post_processing" / "engine.py"
        engine_result = subprocess.run([
            str(python_exec), str(engine_script),
            "--profile", profile,
            "--input", str(file_json),
            "--workspace", str(workspace)
        ])

        if engine_result.returncode != 0:
            typer.secho("[Router] Error: Python Engine failed during post-processing.", fg=typer.colors.RED)
            raise typer.Exit(code=1)

        # 8. Dispatch and Completion Setup
        suffix_final = static_config["suffixes"]["final_text"]
        suffix_raw = static_config["suffixes"]["raw_text"]
        
        final_txt_path = workspace / f"{timestamp}{suffix_final}"
        raw_txt_path = workspace / f"{timestamp}{suffix_raw}"

        final_text = final_txt_path.read_text(encoding="utf-8").strip() if final_txt_path.exists() else ""
        raw_text = raw_txt_path.read_text(encoding="utf-8").strip() if raw_txt_path.exists() else ""

        n8n_script = repo_dir / "server" / "n8n_dispatcher.py"
        subprocess.run([str(python_exec), str(n8n_script), "--workspace", str(workspace)])

        (workspace / ".completed").touch()

        # 9. CLI Visualizations and Clipboard actions
        typer.secho("\n=== RAW TEXT ===", bold=True)
        typer.echo(raw_text)

        if raw_text != final_text and final_text:
            typer.secho("\n=== POST-PROCESSED TEXT ===", bold=True)
            typer.echo(final_text)

        text_to_copy = final_text if final_text else raw_text
        copy_to_clipboard(text_to_copy)

    finally:
        # Cleanup temp environment file safely at EOF
        temp_env_path.unlink(missing_ok=True)
