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


def copy_to_clipboard(text: str) -> bool:
    """Copy ``text`` with the first clipboard tool found; True if it worked."""
    if not text:
        return False
    for tool, cmd, name in (
        ("wl-copy", ["wl-copy"], "Wayland"),
        ("xclip", ["xclip", "-selection", "clipboard"], "X11"),
        ("pbcopy", ["pbcopy"], "macOS"),
    ):
        if shutil.which(tool):
            # Both tools stay in the background to serve the selection: they
            # must not keep our stdout open, or a pipe reading it never ends.
            copied = subprocess.run(
                cmd, input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if copied.returncode == 0:
                typer.secho(f"📋 Copied to clipboard ({name})", fg=typer.colors.GREEN)
                return True
            typer.secho(f"⚠️ Copying to the clipboard with {tool} failed.", fg=typer.colors.YELLOW)
            return False
    typer.secho("⚠️ No clipboard tool found (wl-copy/xclip/pbcopy).", fg=typer.colors.YELLOW)
    return False
