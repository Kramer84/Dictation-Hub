"""Chunked transcription: cut a (growing) recording at pauses, transcribe the
pieces while recording continues, and merge them into one whisper transcript.

Measured on the dataset (optimization/chunking_experiment.py, docs/milestones/
M1b-chunking.md): cutting at the longest pause between 20 and 30 s keeps WER
at the whole-file level and cuts the wait after stop about 4x.
"""
import copy
import os
import re
import struct
import threading
import wave
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

MIN_CHUNK_S = 20.0
MAX_CHUNK_S = 30.0

Pause = Tuple[float, float]  # (middle of the pause, its length) in seconds


def choose_cut(pauses: Sequence[Pause], min_len: float = MIN_CHUNK_S, max_len: float = MAX_CHUNK_S) -> float:
    """Where to end a chunk, in seconds from its start.

    ``pauses`` are relative to the chunk start and cover at least ``max_len``
    seconds of audio. The longest pause between ``min_len`` and ``max_len`` is
    preferred (most likely a sentence end); otherwise the longest pause after
    the first second; otherwise a hard cut at ``max_len``.
    """
    in_window = [p for p in pauses if min_len <= p[0] <= max_len]
    if in_window:
        return max(in_window, key=lambda p: p[1])[0]
    fallback = [p for p in pauses if 1.0 < p[0] <= max_len]
    if fallback:
        return max(fallback, key=lambda p: p[1])[0]
    return max_len


def _format_ms(ms: int) -> str:
    s, ms = divmod(int(ms), 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _shift(item: dict, offset_ms: int) -> None:
    if "offsets" in item:
        item["offsets"] = {k: v + offset_ms for k, v in item["offsets"].items()}
        if "timestamps" in item:
            item["timestamps"] = {k: _format_ms(v) for k, v in item["offsets"].items()}


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.?!…]['\"»”’)]*\s*$", text))


def _lowercase_first_word(segment: dict) -> None:
    for token in segment.get("tokens", []):
        text = token.get("text", "")
        if text.startswith("[_") or not text.strip():
            continue
        word = text.strip()
        if word == "I" or word.startswith("I'") or word.isupper():
            return
        lowered = re.sub(r"\S", lambda m: m.group(0).lower(), text, count=1)
        token["text"] = lowered
        segment["text"] = segment.get("text", "").replace(word, lowered.strip(), 1)
        return


def merge_transcripts(chunks: Iterable[Tuple[float, dict]]) -> dict:
    """Join per-chunk whisper ``-ojf`` outputs into one transcript.

    ``chunks`` are (start offset in seconds, parsed JSON) in order. Offsets are
    shifted to the full recording. When a chunk ends without a sentence end,
    the next chunk's first word is lowercased: whisper capitalizes every
    chunk start, even mid-sentence.
    """
    merged = None
    segments: List[dict] = []
    for offset_s, data in chunks:
        if merged is None:
            merged = {k: copy.deepcopy(v) for k, v in data.items() if k != "transcription"}
        new = [copy.deepcopy(s) for s in data.get("transcription", []) if s.get("text", "").strip()]
        if not new:
            continue
        offset_ms = int(round(offset_s * 1000))
        for segment in new:
            _shift(segment, offset_ms)
            for token in segment.get("tokens", []):
                _shift(token, offset_ms)
        if segments and not _ends_sentence(segments[-1]["text"]):
            _lowercase_first_word(new[0])
        segments.extend(new)
    merged = merged or {"result": {}}
    merged["transcription"] = segments
    return merged


# ------------------------------------------------------------------ streaming
SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit mono
MIN_TAIL_S = 3.0


def _wav_layout(header: bytes) -> Optional[Tuple[int, int, int, int]]:
    """(channels, rate, bits, data offset) from a WAV header, or None."""
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        return None
    pos, fmt = 12, None
    while pos + 8 <= len(header):
        chunk_id, size = header[pos:pos + 4], struct.unpack("<I", header[pos + 4:pos + 8])[0]
        if chunk_id == b"fmt " and pos + 24 <= len(header):
            _, channels, rate, _, _, bits = struct.unpack("<HHIIHH", header[pos + 8:pos + 24])
            fmt = (channels, rate, bits)
        elif chunk_id == b"data":
            return (*fmt, pos + 8) if fmt else None
        pos += 8 + size + (size % 2)
    return None


def is_streamable_wav(header: bytes) -> bool:
    """True for 16 kHz mono 16-bit PCM WAV, which can be chunked while it grows."""
    layout = _wav_layout(header)
    return layout is not None and layout[:3] == (1, SAMPLE_RATE, 16)


class ChunkedTranscription:
    """Transcribe a growing 16 kHz mono WAV in pause-cut chunks.

    A worker thread watches ``source`` (the capture file, valid on disk while
    it grows). Once ``max_len`` seconds past the current chunk start exist, it
    asks ``find_pauses`` about that window, cuts with ``choose_cut`` and hands
    the chunk to ``transcribe_chunk(chunk_wav, index) -> whisper JSON dict``.
    ``finish()`` (or the listener call with ``None``) marks the recording as
    complete; the rest is transcribed and the merged transcript returned.
    """

    def __init__(
        self,
        source: Path,
        chunk_dir: Path,
        transcribe_chunk: Callable[[Path, int], dict],
        find_pauses: Callable[[Path], Sequence[Pause]],
        min_len: float = MIN_CHUNK_S,
        max_len: float = MAX_CHUNK_S,
        poll_interval: float = 0.5,
        complete: bool = False,
    ):
        self.source = source
        self.chunk_dir = chunk_dir
        self.transcribe_chunk = transcribe_chunk
        self.find_pauses = find_pauses
        self.min_len, self.max_len = min_len, max_len
        self.poll_interval = poll_interval
        self.results: List[Tuple[float, dict]] = []
        self._complete = threading.Event()
        if complete:  # an existing file rather than a live recording
            self._complete.set()
        self._cancelled = threading.Event()
        self._wake = threading.Event()
        self._error: Optional[BaseException] = None
        self._data_offset: Optional[int] = None
        chunk_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # recorder / upload listener: any block wakes the worker, None ends the recording
    def __call__(self, block: Optional[bytes]) -> None:
        if block is None:
            self._complete.set()
        self._wake.set()

    def finish(self) -> dict:
        self._complete.set()
        self._wake.set()
        self._thread.join()
        if self._error is not None:
            raise self._error
        return merge_transcripts(self.results)

    def cancel(self) -> None:
        self._cancelled.set()
        self._wake.set()
        self._thread.join()

    def _available(self) -> float:
        try:
            size = os.path.getsize(self.source)
        except OSError:
            return 0.0
        if self._data_offset is None:
            with open(self.source, "rb") as f:
                header = f.read(4096)
            layout = _wav_layout(header)
            if layout is None:
                return 0.0
            if layout[:3] != (1, SAMPLE_RATE, 16):
                raise ValueError(f"{self.source.name} is not 16 kHz mono 16-bit PCM")
            self._data_offset = layout[3]
        return max(0.0, (size - self._data_offset) / BYTES_PER_SECOND)

    def _extract(self, start: float, end: float, path: Path) -> Path:
        first = int(start * SAMPLE_RATE) * 2
        count = int(end * SAMPLE_RATE) * 2 - first
        with open(self.source, "rb") as f:
            f.seek(self._data_offset + first)
            frames = f.read(count)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(frames)
        return path

    def _run(self) -> None:
        try:
            start, index = 0.0, 0
            while not self._cancelled.is_set():
                complete = self._complete.is_set()
                available = self._available()
                remaining = available - start
                if complete and remaining <= self.max_len + MIN_TAIL_S:
                    if remaining > 0.3:
                        self._transcribe(start, available, index)
                    return
                if not complete and remaining < self.max_len:
                    self._wake.wait(self.poll_interval)
                    self._wake.clear()
                    continue
                window = self._extract(start, start + self.max_len, self.chunk_dir / "window.wav")
                cut = choose_cut(self.find_pauses(window), self.min_len, self.max_len)
                self._transcribe(start, start + cut, index)
                start, index = start + cut, index + 1
        except BaseException as e:
            self._error = e

    def _transcribe(self, start: float, end: float, index: int) -> None:
        if self._cancelled.is_set():
            return
        chunk = self._extract(start, end, self.chunk_dir / f"{index:03d}.wav")
        self.results.append((start, self.transcribe_chunk(chunk, index)))
