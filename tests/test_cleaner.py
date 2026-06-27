import pytest
import sys
import os

# Ensure the parent directory is in the path to import the module
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Adjust this import to match the actual name of your script file (e.g., cleaner.py)
from post_processing.deterministic_cleaner import (
    add_confidence_marker,
    compress_repetitions_marked,
    dedup_and_filter_hallucinations,
    _is_pure_filler,
    _is_fragment,
    phrase_level_cleanup,
    format_timestamp
)

# --- Unit Tests: Confidence Markers ---

@pytest.mark.parametrize("text, p_val, expected", [
    ("Terrible", 0.35, "Terrible[---]"),
    ("low", 0.55, "low[--]"),
    ("confidence", 0.75, "confidence[-]"),
    ("normal", 0.85, "normal"),
    ("perfect", 0.995, "perfect[+]"),
])
def test_add_confidence_marker(text, p_val, expected):
    assert add_confidence_marker(text, p_val) == expected

# --- Unit Tests: Phrase Compression ---

def test_compress_repetitions_marked_phrase():
    # Phrase length is 2 ("this phrase")
    text = "I like this phrase this phrase very much"
    expected = "I like this phrase [R2] very much"
    assert compress_repetitions_marked(text) == expected

def test_compress_repetitions_marked_single_word_ignored():
    # Single word repetitions should NOT be compressed by this function due to min_phrase_len=2
    text = "I have a repeated repeated word"
    assert compress_repetitions_marked(text) == text

def test_compress_repetitions_marked_nested():
    # L2x4 beats L4x2
    text = "go back go back go back go back"
    expected = "go back [R4]"
    assert compress_repetitions_marked(text) == expected

# --- Unit Tests: Deduplication and Hallucination Filtering ---

def test_dedup_single_words():
    mock_segments = [{
        "start": 0, "end": 2,
        "tokens": [
            {"text": " This", "p": 0.9},
            {"text": " is", "p": 0.9},
            {"text": " a", "p": 0.9},
            {"text": " repeated", "p": 0.9},
            {"text": " repeated", "p": 0.9},
            {"text": " word", "p": 0.9}
        ]
    }]
    result = dedup_and_filter_hallucinations(mock_segments)
    assert len(result) == 1
    # The consecutive single word is stripped out by dedup, NOT compression
    assert result[0]["text"] == " This is a repeated word"

def test_dedup_backchannels_allowed_streaks():
    mock_segments = [{
        "start": 0, "end": 2,
        "tokens": [
            {"text": " yeah", "p": 0.9},
            {"text": " yeah", "p": 0.9},
            {"text": " yeah", "p": 0.9},  # Streak 3: should be dropped because limit is 2
            {"text": " okay", "p": 0.9}
        ]
    }]
    result = dedup_and_filter_hallucinations(mock_segments)
    assert result[0]["text"] == " yeah yeah okay"

# --- Unit Tests: Filler and Fragment Identification ---

@pytest.mark.parametrize("text, expected", [
    ("Mm-hmm.", True),
    ("Yeah, sure.", True),
    ("Uh-huh!", True),
    ("This is real text", False),
    ("Okay, let's go", False),
])
def test_is_pure_filler(text, expected):
    assert _is_pure_filler(text) == expected

@pytest.mark.parametrize("text, expected", [
    ("mhm", True),
    ("yeah ", True),
    ("...", True),   # Completely non-alphanumeric noise
    ("I think so", False),
])
def test_is_fragment(text, expected):
    assert _is_fragment(text) == expected

# --- Integration Tests: Phrase Level Cleanup ---

def test_phrase_level_cleanup_merging():
    entries = [
        {"start_ms": 0, "end_ms": 2000, "text": "Hello world."},
        {"start_ms": 2500, "end_ms": 4000, "text": " This should merge."}
    ]
    # Gap is 500ms, which is < 3000ms threshold
    result = phrase_level_cleanup(entries, gap_threshold_ms=3000)
    assert len(result) == 1
    assert result[0]["end_ms"] == 4000
    assert result[0]["text"] == "Hello world. This should merge."

def test_phrase_level_cleanup_no_merging():
    entries = [
        {"start_ms": 0, "end_ms": 2000, "text": "Hello world."},
        {"start_ms": 6000, "end_ms": 8000, "text": " This should NOT merge."}
    ]
    # Gap is 4000ms, which is > 3000ms threshold
    result = phrase_level_cleanup(entries, gap_threshold_ms=3000)
    assert len(result) == 2
    assert result[0]["text"] == "Hello world."
    assert result[1]["text"] == " This should NOT merge."

def test_phrase_level_cleanup_drops_fillers():
    entries = [
        {"start_ms": 0, "end_ms": 1000, "text": "Yeah."},
        {"start_ms": 1500, "end_ms": 3000, "text": "Actual dictation."}
    ]
    result = phrase_level_cleanup(entries)
    assert len(result) == 1
    assert result[0]["text"] == "Actual dictation."

# --- Unit Tests: Utility ---

@pytest.mark.parametrize("ms, expected", [
    (15000, "[00:15]"),
    (65000, "[01:05]"),
    (3665000, "[01:01:05]"),
])
def test_format_timestamp(ms, expected):
    assert format_timestamp(ms) == expected