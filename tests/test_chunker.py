import pytest

from dictation_hub.core import chunker


# ------------------------------------------------------------------ choose_cut
@pytest.mark.parametrize(
    "pauses, expected",
    [
        # longest pause between 20 and 30 s wins, not the first one
        ([(21.0, 0.3), (24.5, 1.2), (28.0, 0.5)], 24.5),
        # pauses before 20 s are ignored when a later one exists
        ([(12.0, 2.0), (22.0, 0.3)], 22.0),
        # no pause after 20 s: longest pause available (after 1 s)
        ([(0.5, 3.0), (9.0, 0.4), (15.0, 0.8)], 15.0),
        # no pause at all: hard cut at the cap
        ([], 30.0),
    ],
)
def test_choose_cut(pauses, expected):
    assert chunker.choose_cut(pauses, min_len=20.0, max_len=30.0) == expected


# ------------------------------------------------------------- merge_transcripts
def seg(text, start_ms, end_ms):
    return {
        "offsets": {"from": start_ms, "to": end_ms},
        "text": text,
        "tokens": [
            {"text": "[_BEG_]", "p": 1.0, "offsets": {"from": start_ms, "to": start_ms}},
            *[
                {"text": f" {w}", "p": 0.9, "offsets": {"from": start_ms, "to": end_ms}}
                for w in text.split()
            ],
        ],
    }


def chunk_json(language, *segments):
    return {"result": {"language": language}, "transcription": list(segments)}


def test_merge_shifts_offsets_and_keeps_order():
    merged = chunker.merge_transcripts([
        (0.0, chunk_json("fr", seg("Bonjour à tous.", 0, 2000))),
        (24.5, chunk_json("fr", seg("Ensuite on continue.", 500, 3000))),
    ])
    first, second = merged["transcription"]
    assert second["offsets"] == {"from": 25000, "to": 27500}
    assert second["tokens"][1]["offsets"]["from"] == 25000
    assert merged["result"]["language"] == "fr"


def test_merge_lowercases_continuation_after_a_chunk_without_sentence_end():
    merged = chunker.merge_transcripts([
        (0.0, chunk_json("fr", seg("sur leur poitrine plate,", 0, 2000))),
        (25.0, chunk_json("fr", seg("La tête enveloppée.", 0, 2000))),
    ])
    second = merged["transcription"][1]
    assert second["text"].strip().startswith("la tête")
    assert second["tokens"][1]["text"] == " la"


@pytest.mark.parametrize("previous_end", ["a full stop.", "a question?", "wow!"])
def test_merge_keeps_capital_after_sentence_end(previous_end):
    merged = chunker.merge_transcripts([
        (0.0, chunk_json("en", seg(previous_end, 0, 2000))),
        (25.0, chunk_json("en", seg("The next sentence.", 0, 2000))),
    ])
    assert merged["transcription"][1]["tokens"][1]["text"] == " The"


def test_merge_never_lowercases_english_i():
    merged = chunker.merge_transcripts([
        (0.0, chunk_json("en", seg("and then,", 0, 2000))),
        (25.0, chunk_json("en", seg("I thought so.", 0, 2000))),
    ])
    assert merged["transcription"][1]["tokens"][1]["text"] == " I"


def test_merge_skips_empty_chunks():
    merged = chunker.merge_transcripts([
        (0.0, chunk_json("en", seg("Hello,", 0, 1000))),
        (25.0, chunk_json("en")),
        (50.0, chunk_json("en", seg("World.", 0, 1000))),
    ])
    assert [s["text"] for s in merged["transcription"]] == ["Hello,", "world."]


# ------------------------------------------------------- ChunkedTranscription
import threading
import time
import wave


def growing_wav(path, seconds, rate=16000, realtime_factor=0.0):
    """Write `seconds` of audio into a WAV that grows like a live recording."""
    w = wave.open(str(path), "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    for _ in range(int(seconds)):
        w.writeframes(b"\x01\x00" * rate)
        time.sleep(realtime_factor)
    w.close()


class FakeWhisper:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, chunk_wav, index):
        with wave.open(str(chunk_wav)) as w:
            seconds = w.getnframes() / w.getframerate()
        self.calls.append((index, round(seconds, 2)))
        if index == self.fail_on:
            raise RuntimeError("whisper crashed")
        return chunk_json("en", seg(f"Chunk {index}.", 0, int(seconds * 1000)))


def pause_at_24(window_wav):
    return [(24.0, 1.0)]


def test_chunks_are_transcribed_while_recording_continues(tmp_path):
    source = tmp_path / "capture.wav"
    whisper = FakeWhisper()
    writer = threading.Thread(target=growing_wav, args=(source, 70, 16000, 0.02))
    writer.start()
    job = chunker.ChunkedTranscription(source, tmp_path / "chunks", whisper, pause_at_24, poll_interval=0.05)
    writer.join()
    deadline = time.time() + 5
    while len(whisper.calls) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert [c[0] for c in whisper.calls] == [0, 1]  # done before the recording was finished
    merged = job.finish()
    assert whisper.calls == [(0, 24.0), (1, 24.0), (2, 22.0)]
    assert [s["offsets"]["from"] for s in merged["transcription"]] == [0, 24000, 48000]


def test_short_recording_is_a_single_chunk(tmp_path):
    source = tmp_path / "capture.wav"
    growing_wav(source, 12)
    whisper = FakeWhisper()
    job = chunker.ChunkedTranscription(source, tmp_path / "chunks", whisper, pause_at_24, poll_interval=0.05)
    job.finish()
    assert whisper.calls == [(0, 12.0)]


def test_small_tail_is_kept_with_the_last_chunk(tmp_path):
    source = tmp_path / "capture.wav"
    growing_wav(source, 32)  # 32 s <= 30 s + 3 s tail allowance
    whisper = FakeWhisper()
    chunker.ChunkedTranscription(
        source, tmp_path / "chunks", whisper, pause_at_24, poll_interval=0.05, complete=True
    ).finish()
    assert whisper.calls == [(0, 32.0)]


def test_transcription_errors_surface_on_finish(tmp_path):
    source = tmp_path / "capture.wav"
    growing_wav(source, 70)
    job = chunker.ChunkedTranscription(source, tmp_path / "chunks", FakeWhisper(fail_on=1), pause_at_24, poll_interval=0.05)
    with pytest.raises(RuntimeError, match="whisper crashed"):
        job.finish()


def test_cancel_stops_without_transcribing_the_rest(tmp_path):
    source = tmp_path / "capture.wav"
    growing_wav(source, 10)
    whisper = FakeWhisper()
    job = chunker.ChunkedTranscription(source, tmp_path / "chunks", whisper, pause_at_24, poll_interval=0.05)
    job.cancel()
    assert whisper.calls == []


def test_only_16k_mono_pcm_is_streamable():
    header = lambda rate, ch: (
        b"RIFF\xff\xff\xff\xffWAVEfmt " + __import__("struct").pack("<IHHIIHH", 16, 1, ch, rate, rate * ch * 2, ch * 2, 16) + b"data\xff\xff\xff\xff"
    )
    assert chunker.is_streamable_wav(header(16000, 1))
    assert not chunker.is_streamable_wav(header(44100, 2))
    assert not chunker.is_streamable_wav(b"\x1aE\xdf\xa3webm...")
