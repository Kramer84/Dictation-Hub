"""What the terminal shows during a dictation: one line per transcribed chunk
and the final text. Everything else goes to the workspace's session.log
(shown on the terminal too with -v/--verbose, see pipeline.engine.setup_logging).
"""
import shutil
import sys
from pathlib import Path


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
