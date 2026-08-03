import json
import shutil
import subprocess
from pathlib import Path

import typer


def save_metadata(
    workspace: Path, file_json: Path, profile: str, timestamp: str
) -> None:
    lang_code = "auto"
    if file_json.is_file():
        try:
            with file_json.open("r", encoding="utf-8") as f:
                data = json.load(f)
                lang_code = data.get("result", {}).get("language", "auto")
        except json.JSONDecodeError:
            pass
    metadata_path = workspace / "metadata.json"
    metadata = {"profile": profile, "language": lang_code, "timestamp": timestamp}
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def copy_to_clipboard(text: str) -> None:
    if not text:
        return
    if shutil.which("wl-copy"):
        subprocess.run(["wl-copy"], input=text.encode("utf-8"))
        typer.secho("\n[Router] Copied to Wayland clipboard.", fg=typer.colors.GREEN)
    elif shutil.which("xclip"):
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"))
        typer.secho("\n[Router] Copied to X11 clipboard.", fg=typer.colors.GREEN)
    else:
        typer.secho(
            "\n[Router] Warning: No clipboard utility found (wl-copy/xclip).",
            fg=typer.colors.YELLOW,
        )
