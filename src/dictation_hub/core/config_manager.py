# src/dictation_hub/core/config_manager.py

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import typer
import yaml
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = REPO_ROOT / "configs"


USER_CONFIG_DIR = Path.home() / ".config" / "dictation_hub"

FILE_MAPPING = {
    "config.env.template": "config.env",
    "static.json": "static.json",
    "pipeline_config.json": "pipeline_config.json",
    "hallucinations_dict.yaml": "hallucinations_dict.yaml",
}


def is_config_ready() -> bool:

    if not USER_CONFIG_DIR.is_dir():
        return False

    for target_name in FILE_MAPPING.values():
        expected_file = USER_CONFIG_DIR / target_name
        if not expected_file.is_file():
            return False

    return True


def initialize_user_configs(force: bool = False) -> None:

    if is_config_ready() and not force:
        return

    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    for template_name, target_name in FILE_MAPPING.items():
        src_file = TEMPLATE_DIR / template_name
        dest_file = USER_CONFIG_DIR / target_name

        if src_file.exists():
            if not dest_file.exists() or force:
                shutil.copy2(src_file, dest_file)
        else:
            typer.secho(
                f"Warning: Template {src_file} missing from repository.",
                fg=typer.colors.YELLOW,
            )

    typer.secho(
        f"Initialized configuration at {USER_CONFIG_DIR}", fg=typer.colors.GREEN
    )


def get_config_dir() -> Path:

    if not is_config_ready():
        initialize_user_configs()
    return USER_CONFIG_DIR


def load_deployment_env() -> Dict[str, str]:

    env_path = get_config_dir() / "config.env"
    if not env_path.exists():
        raise FileNotFoundError(f"Missing required config file: {env_path}")
    return dotenv_values(env_path)


def load_json_config(filename: str) -> Dict[str, Any]:

    file_path = get_config_dir() / filename
    if not file_path.is_file():
        raise FileNotFoundError(f"Configuration file missing: {file_path}")
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def create_workspace(
    base_dir_raw: str, folder_format: str, profile: str
) -> Tuple[Path, str]:

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
    valid_args: List[str],
) -> Path:

    if not base_env_path.is_file():
        typer.secho(
            f"Error: Base environment {base_env_path} not found.", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)

    fd, temp_path = tempfile.mkstemp(suffix=".env", text=True)

    with open(fd, "w", encoding="utf-8") as out_f:
        with base_env_path.open("r", encoding="utf-8") as in_f:
            out_f.write(in_f.read())

        out_f.write("\n# --- DYNAMIC OVERRIDES ---\n")

        for key, val in env_overrides.items():
            out_f.write(f'{key}="{val}"\n')

        for key, val in cli_overrides.items():
            if key in valid_args:
                out_f.write(f'{key.upper()}="{val}"\n')
            else:
                typer.secho(
                    f"[Router] Warning: Argument '{key}' is not in valid_arguments.",
                    fg=typer.colors.YELLOW,
                )

    return Path(temp_path)
