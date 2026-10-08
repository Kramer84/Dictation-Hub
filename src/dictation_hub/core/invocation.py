import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .whisper_interface import whisper_transcribe

SHORT_FLAGS = {"-l": "language", "-m": "model", "-t": "threads"}

_PARAMS = inspect.signature(whisper_transcribe).parameters
WHISPER_OPTIONS = set(_PARAMS) - {"input_wav", "config", "output_base"}
BOOL_OPTIONS = {name for name in WHISPER_OPTIONS if isinstance(_PARAMS[name].default, bool)}


class InvocationError(ValueError):
    pass


@dataclass
class Invocation:
    profile: str
    overrides: Dict[str, str] = field(default_factory=dict)
    audio_file: Optional[Path] = None


def parse_invocation(args: List[str], default_profile: str = "standard") -> Invocation:
    """Split raw CLI tokens into a profile, whisper overrides and an optional input file.

    Accepts ``-l fr``, ``--language fr``, ``--language=fr`` and bare boolean
    flags (``--no-gpu``). Non-boolean options always consume the next token,
    so negative numbers (``--logprob-thold -0.5``) work.
    """
    positionals: List[str] = []
    overrides: Dict[str, str] = {}
    audio_file: Optional[Path] = None
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("-f", "--file") or token.startswith("--file="):
            if "=" in token:
                path = token.split("=", 1)[1]
            elif i + 1 < len(args):
                i += 1
                path = args[i]
            else:
                raise InvocationError(f"Option '{token}' requires a path.")
            audio_file = Path(path).expanduser()
            if not audio_file.is_file():
                raise InvocationError(f"Audio file not found: {audio_file}")
        elif token in SHORT_FLAGS or token.startswith("--"):
            if token in SHORT_FLAGS:
                key, value = SHORT_FLAGS[token], None
            else:
                key, _, inline = token[2:].partition("=")
                key, value = key.replace("-", "_"), (inline if "=" in token else None)
            if key not in WHISPER_OPTIONS:
                raise InvocationError(f"Unknown option '{token}'.")
            if value is None:
                if key in BOOL_OPTIONS:
                    value = "true"
                elif i + 1 < len(args):
                    i += 1
                    value = args[i]
                else:
                    raise InvocationError(f"Option '{token}' requires a value.")
            overrides[key] = value
        elif token.startswith("-") and len(token) > 1:
            raise InvocationError(f"Unknown option '{token}'.")
        else:
            positionals.append(token)
        i += 1
    if len(positionals) > 1:
        raise InvocationError(f"Expected a single profile, got: {' '.join(positionals)}")
    return Invocation(positionals[0] if positionals else default_profile, overrides, audio_file)


LANGUAGES = ["auto", "en", "fr", "de", "es", "it", "nl", "pt"]
FILE_FLAGS = ("-f", "--file")


def _option_help(name: str) -> str:
    for meta in getattr(_PARAMS[name].annotation, "__metadata__", ()):
        if getattr(meta, "help", None):
            return meta.help
    return ""


def _all_options() -> List[Tuple[str, str]]:
    short = [
        ("-f", "Reprocess an existing audio file"),
        ("-l", "Language code (e.g. fr, en, auto)"),
        ("-m", "Whisper model"),
        ("-t", "Number of threads"),
    ]
    long = [("--file", "Reprocess an existing audio file")] + sorted(
        ("--" + name.replace("_", "-"), _option_help(name)) for name in WHISPER_OPTIONS
    )
    return short + long


def _takes_value(token: str) -> bool:
    if token in FILE_FLAGS or token in SHORT_FLAGS:
        return True
    if token.startswith("--") and "=" not in token:
        key = token[2:].replace("-", "_")
        return key in WHISPER_OPTIONS and key not in BOOL_OPTIONS
    return False


def _value_key(token: str) -> str:
    if token in FILE_FLAGS:
        return "file"
    return SHORT_FLAGS.get(token) or token[2:].replace("-", "_")


def _scan(previous: List[str]) -> Tuple[List[str], Optional[str]]:
    """Return the positional tokens and the option still waiting for its value."""
    positionals: List[str] = []
    pending: Optional[str] = None
    for token in previous:
        if pending is not None:
            pending = None
        elif _takes_value(token):
            pending = token
        elif not token.startswith("-"):
            positionals.append(token)
    return positionals, pending


def complete_invocation(
    previous: List[str], incomplete: str, profiles: List[str], models: List[str]
) -> List[Tuple[str, str]]:
    """Completion candidates as (value, help) for the dictation command line.

    An empty result for a file value lets the shell fall back to its own path
    completion.
    """
    positionals, pending = _scan(previous)
    if pending is not None:
        key = _value_key(pending)
        if key == "language":
            choices = [(c, "") for c in LANGUAGES]
        elif key == "model":
            choices = [(m, "Installed whisper model") for m in models]
        else:
            return []
        return [(v, h) for v, h in choices if v.startswith(incomplete)]
    if incomplete.startswith("-"):
        return [(v, h) for v, h in _all_options() if v.startswith(incomplete)]
    if positionals:
        return []
    return [(p, "Dictation profile") for p in profiles if p.startswith(incomplete)]
