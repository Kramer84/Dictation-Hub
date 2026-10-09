"""What the terminal shows during a dictation: one line per transcribed chunk
and the final text. Everything else goes to the workspace's session.log
(shown on the terminal too with -v/--verbose, see pipeline.engine.setup_logging).
"""
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer

from .utils import copy_to_clipboard


def chunk_line(index: int, text: str, width: int) -> str:
    """One terminal line for a transcribed chunk, cut to ``width`` columns."""
    line = f"   ✓ chunk {index + 1}  {' '.join(text.split()) or '(silence)'}"
    return line if len(line) <= width else line[: width - 1] + "…"


def show_chunk(index: int, text: str) -> None:
    """Print a chunk line. It comes from the transcription thread while the
    recorder may have left a status line (pause) without a newline: clear it."""
    width = shutil.get_terminal_size((100, 20)).columns
    prefix = "\r\033[K" if sys.stdout.isatty() else ""
    print(prefix + chunk_line(index, text, width), flush=True)


def rule() -> str:
    return "─" * min(shutil.get_terminal_size((100, 20)).columns, 60)


def short_path(path: Path) -> str:
    """``path`` with the home directory written as ~."""
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return str(path)


def deliver(raw_text: str, final_text: str, error: Optional[str]) -> None:
    """Show the delivered text once and copy it. The scrubbed raw text stays in
    the workspace (<ts>_raw.txt) and is what gets delivered if the LLM failed."""
    if error:
        typer.secho(
            f"⚠️ Post-processing failed ({error}); delivering the scrubbed text.",
            fg=typer.colors.YELLOW,
        )
    text = final_text or raw_text
    typer.echo(rule())
    typer.echo(text)
    typer.echo(rule())
    copy_to_clipboard(text)
