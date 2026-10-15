"""``dictation-hub answer``: record a spoken answer for another tool and print JSON."""
import contextlib
import json
import sys
from pathlib import Path
from typing import Optional

import typer

from . import api

EXIT_FAILED, EXIT_BAD_INPUT, EXIT_CANCELLED, EXIT_LLM_FAILED = 1, 2, 3, 4
BAD_INPUT = (ImportError, AttributeError, FileNotFoundError, ValueError)


def _emit(document: dict) -> None:
    typer.echo(json.dumps(document, ensure_ascii=False))


def _failure(error: Exception, audio: Optional[str] = None) -> dict:
    return {"ok": False, "raw_text": "", "final_text": "", "data": None,
            "error": f"{type(error).__name__}: {error}", "workspace": None, "audio": audio}


def answer_command(
    file: Optional[Path] = typer.Option(
        None, "--file", "-f", help="Audio file to transcribe instead of recording (retries, tests)."
    ),
    profile_file: Optional[Path] = typer.Option(
        None, "--profile-file", help="JSON file with one profile; its post_processing[0] drives the schema step."
    ),
    schema: Optional[str] = typer.Option(None, "--schema", help="Pydantic model as module:Class."),
):
    """Record a spoken answer (or take --file), then print one JSON document on stdout.

    Progress, the recorder display and logs go to stderr. Exit codes: 0 ok,
    1 recording or transcription failed, 2 bad input, 3 cancelled or quit,
    4 schema step failed (the cleaned text is still in the JSON).
    """
    try:
        # Anything the pipeline prints (log handlers bind stdout at creation) goes to stderr.
        with contextlib.redirect_stdout(sys.stderr):
            result = api.dictate(audio_file=file, profile=profile_file, schema=schema)
    except api.Cancelled as e:
        _emit(_failure(e, audio=e.audio))
        raise typer.Exit(code=EXIT_CANCELLED)
    except BAD_INPUT as e:
        _emit(_failure(e))
        raise typer.Exit(code=EXIT_BAD_INPUT)
    except Exception as e:
        _emit(_failure(e))
        raise typer.Exit(code=EXIT_FAILED)
    _emit(result.to_dict())
    if not result.ok:
        raise typer.Exit(code=EXIT_LLM_FAILED)
