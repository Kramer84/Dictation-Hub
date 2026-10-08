import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

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
