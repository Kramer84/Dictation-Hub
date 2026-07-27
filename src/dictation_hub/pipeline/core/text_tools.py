import argparse
import json
import logging
import os
import re
import string
import sys

import language_tool_python
import yaml

from core import static_config

# Initialize module-level logger
logger = logging.getLogger(__name__)

LANG_MAP = {"en": "en-US", "fr": "fr-FR", "de": "de-DE"}
BACKCHANNEL_WORDS = {
    "yeah",
    "yeah.",
    "mm-hmm",
    "mm-hmm.",
    "mhm",
    "mhm.",
    "mm",
    "hmm",
    "wow",
    "wow.",
    "nice",
    "nice.",
    "sure",
    "sure.",
    "right",
    "right.",
    "-hmm",
    "-hmm.",
    "uh-huh",
    "uh-huh.",
}
PURE_FILLER_RE = re.compile(
    "^(Mm-hmm|Mhm|Mm|Hmm|-hmm|Yeah|Yep|Wow|Nice|Sure|Right|Okay|Cool|Oh|Uh-huh)[.\\s,!?]*$",
    re.IGNORECASE,
)
FILLER_PATTERN = re.compile(
    "^[\\s.,!?]*(mm[-\\s]?hmm|mhm|mm|hmm|yeah|yep|wow|nice|sure|right|okay|oh|uh[-\\s]?huh|ah|um|i|so)([.\\s,!?]|\\s)*$",
    re.IGNORECASE,
)
MAX_WORD_DURATION_MS = 5000


def parse_whisper_json(filepath):
    logger.debug("Parsing Whisper JSON from: %s", filepath)
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    segments = data.get("transcription", data.get("segments", []))
    logger.debug("Successfully parsed %d segments from JSON.", len(segments))
    return segments


def add_confidence_marker(text, p_value):
    if p_value < 0.3:
        return f"{text} [??]"
    elif p_value < 0.6:
        return f"{text} [?]"
    return text


def format_timestamp(ms):
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"[{h:02d}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def _is_pure_filler(text):
    stripped = PURE_FILLER_RE.sub("", text).strip()
    return not stripped


def _is_fragment(text):
    text = text.strip()
    if FILLER_PATTERN.match(text):
        return True
    if not re.search("[a-zA-Z0-9]", text):
        return True
    return False


def strip_markers_func(text):
    return re.sub("\\s*\\[\\?+\\]|\\s*\\[[-+]+\\]", "", text)


def grammar_checker(
    text, language="en", strip_markers=True, disable_spellchecking=True
):
    lt_lang = LANG_MAP.get(language, "en-US")
    logger.debug("Starting grammar check (Language: %s, Strip markers: %s).", lt_lang, strip_markers)
    
    if strip_markers:
        text = strip_markers_func(text)
    try:
        try:
            tool = language_tool_python.LanguageTool(
                lt_lang, remote_server="http://localhost:8081"
            )
            logger.info("🚀 Connected to remote server daemon.")
        except Exception as e:
            logger.warning("⚠️ [Grammar Checker] Failed to connect to local daemon: %s", e)
            logger.info("-> Falling back to a local, self-hosted LanguageTool instance...")
            tool = language_tool_python.LanguageTool(lt_lang)
            
        if disable_spellchecking:
            tool.disable_spellchecking()
            logger.debug("Spellchecking disabled for grammar checker.")
            
        corrected_text = tool.correct(text)
    except Exception as e:
        logger.error("❌ [Grammar Checker] Both remote daemon and local fallback failed: %s", e)
        logger.warning("-> Bypassing grammar check and returning raw text.")
        corrected_text = text
    finally:
        if "tool" in locals():
            tool.close()
            logger.debug("LanguageTool instance closed.")
            
    logger.info("✅ [Grammar Checker] Truecasing and punctuation restored (Lang: %s).", lt_lang)
    return corrected_text


def build_auto_regex(variations):
    variations_sorted = sorted(variations, key=len, reverse=True)
    escaped_vars = [re.escape(v) for v in variations_sorted]
    return "(?i)\\b(?:" + "|".join(escaped_vars) + ")\\b"


def load_and_compile(yaml_path):
    logger.debug("Loading and compiling regex rules from: %s", yaml_path)
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    rules = {}
    if "auto_generate" in data:
        logger.debug("Compiling 'auto_generate' rules...")
        for target, variations in data["auto_generate"].items():
            pattern = build_auto_regex(variations)
            rules[pattern] = target
            
    if "raw_regex" in data:
        logger.debug("Loading 'raw_regex' rules...")
        for pattern, target in data["raw_regex"].items():
            rules[pattern] = target
            
    logger.debug("Compiled a total of %d regex rules.", len(rules))
    return rules


def regex_replacer(text, rules_dict_path, strip_markers=False):
    logger.debug("Starting regex replacement (strip_markers=%s).", strip_markers)
    if strip_markers:
        text = strip_markers_func(text)
        
    replacements = load_and_compile(rules_dict_path)
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
        
    logger.debug("✅ [Regex Replacer] Deterministic substitution complete.")
    return text


def compress_repetitions_marked(text, min_phrase_len=2, max_phrase_len=60):
    if not text:
        return ""
        
    logger.debug("Starting repetition compression on text of length %d.", len(text))
    tokens = text.split()

    def clean_token(t):
        t_base = re.sub("\\[[-+_.\\d]+\\]", "", t)
        t_base = re.sub("\\[_EOT_\\]", "", t_base)
        t_base = re.sub("\\[_TT_\\d+\\]|\\[_BEG_\\]", "", t_base)
        return t_base.lower().strip(string.punctuation)

    def ends_sentence(t):
        t_base = re.sub("\\[.*?\\]", "", t)
        return any((t_base.endswith(p) for p in [".", "!", "?"]))

    cleaned_words = []
    valid_mapping = []
    sentence_boundaries = set()
    
    for idx, t in enumerate(tokens):
        c = clean_token(t)
        if c:
            cleaned_words.append(c)
            valid_mapping.append(idx)
            if ends_sentence(t):
                sentence_boundaries.add(len(cleaned_words) - 1)
                
    n = len(cleaned_words)
    output_tokens = []
    i = 0
    last_orig_idx = -1
    
    while i < n:
        best_len = 0
        best_count = 0
        max_coverage = 0
        for L in range(min_phrase_len, max_phrase_len + 1):
            if i + 2 * L > n:
                break
            pat = cleaned_words[i : i + L]
            nxt = cleaned_words[i + L : i + 2 * L]
            has_boundary = any((idx in sentence_boundaries for idx in range(i, i + L)))
            if has_boundary:
                continue
            if pat == nxt:
                count = 1
                curr = i + L
                loop_crossed_boundary = False
                while curr + L <= n:
                    if any(
                        (idx in sentence_boundaries for idx in range(curr, curr + L))
                    ):
                        loop_crossed_boundary = True
                    if cleaned_words[curr : curr + L] == pat and (
                        not loop_crossed_boundary
                    ):
                        count += 1
                        curr += L
                    else:
                        break
                coverage = L * count
                if coverage > max_coverage:
                    max_coverage = coverage
                    best_len = L
                    best_count = count
                    
        if best_len > 0:
            start_idx = valid_mapping[i]
            end_first_idx = valid_mapping[i + best_len - 1]
            end_total_idx = valid_mapping[i + best_len * best_count - 1]
            if start_idx > last_orig_idx + 1:
                output_tokens.extend(tokens[last_orig_idx + 1 : start_idx])
            output_tokens.extend(tokens[start_idx : end_first_idx + 1])
            output_tokens.append(f"[R{best_count}]")
            last_orig_idx = end_total_idx
            i += best_len * best_count
        else:
            orig_idx = valid_mapping[i]
            if orig_idx > last_orig_idx + 1:
                output_tokens.extend(tokens[last_orig_idx + 1 : orig_idx])
            output_tokens.append(tokens[orig_idx])
            last_orig_idx = orig_idx
            i += 1
            
    if last_orig_idx + 1 < len(tokens):
        output_tokens.extend(tokens[last_orig_idx + 1 :])
        
    compressed_text = " ".join(output_tokens)
    logger.debug("Repetition compression complete. Final token count: %d.", len(output_tokens))
    return compressed_text


def dedup_and_filter_hallucinations(segments, mark_confidence=False):
    logger.debug("Starting deduplication and hallucination filtering on %d segments (mark_confidence=%s).", len(segments), mark_confidence)
    cleaned_segments = []
    
    for seg in segments:
        offsets = seg.get("offsets", {})
        start_ms = offsets.get(
            "from", seg.get("start", 0) * 1000 if "start" in seg else 0
        )
        end_ms = offsets.get("to", seg.get("end", 0) * 1000 if "end" in seg else 0)
        
        if isinstance(start_ms, float):
            start_ms = int(start_ms)
        if isinstance(end_ms, float):
            end_ms = int(end_ms)
            
        duration = end_ms - start_ms
        tokens = seg.get("tokens", [])
        
        if not tokens:
            raw_words = seg.get("text", "").split()
            tokens = [{"text": f" {w}", "p": 1.0} for w in raw_words]
            
        if duration > MAX_WORD_DURATION_MS and len(tokens) <= 3:
            logger.debug("Filtering suspected hallucination: duration=%dms, tokens=%d", duration, len(tokens))
            continue
            
        cleaned_tokens = []
        streak = 1
        for i, token_obj in enumerate(tokens):
            if isinstance(token_obj, dict):
                raw_text = token_obj.get("text", "")
                p_val = token_obj.get("p", 1.0)
            else:
                raw_text = str(token_obj)
                p_val = 1.0
                
            curr_text = raw_text.strip().lower()
            if i > 0:
                prev_text_raw = (
                    tokens[i - 1].get("text", "")
                    if isinstance(tokens[i - 1], dict)
                    else str(tokens[i - 1])
                )
                prev_text = prev_text_raw.strip().lower()
                if curr_text == prev_text and curr_text != "":
                    streak += 1
                    limit = 2 if curr_text in BACKCHANNEL_WORDS else 1
                    if streak > limit:
                        continue
                else:
                    streak = 1
                    
            final_text = raw_text
            if mark_confidence:
                final_text = add_confidence_marker(raw_text, p_val)
            cleaned_tokens.append(final_text)
            
        if cleaned_tokens:
            reconstructed_text = "".join(cleaned_tokens)
            reconstructed_text = re.sub("\\[_EOT_\\]", "", reconstructed_text)
            reconstructed_text = re.sub("\\[_TT_\\d+\\]", "", reconstructed_text)
            reconstructed_text = re.sub("\\[_BEG_\\]", "", reconstructed_text)
            cleaned_segments.append(
                {"start_ms": start_ms, "end_ms": end_ms, "text": reconstructed_text}
            )
            
    logger.debug("Deduplication complete. Retained %d cleaned segments.", len(cleaned_segments))
    return cleaned_segments


def phrase_level_cleanup(entries, gap_threshold_ms=3000, apply_compression=False):
    logger.debug("Starting phrase-level cleanup on %d entries (gap_threshold=%dms, compression=%s).", len(entries), gap_threshold_ms, apply_compression)
    
    no_fillers = [e for e in entries if not _is_pure_filler(e["text"].strip())]
    logger.debug("Filtered out pure fillers. Remaining segments: %d", len(no_fillers))
    
    no_fragments = [e for e in no_fillers if not _is_fragment(e["text"])]
    logger.debug("Filtered out fragments. Remaining segments: %d", len(no_fragments))
    
    final_out = []
    cur = None
    for e in no_fragments:
        if cur is None:
            cur = dict(e)
        elif e["start_ms"] - cur["end_ms"] < gap_threshold_ms:
            cur["end_ms"] = e["end_ms"]
            text_to_add = e["text"] if e["text"].startswith(" ") else " " + e["text"]
            cur["text"] += text_to_add
        else:
            if apply_compression:
                cur["text"] = compress_repetitions_marked(cur["text"])
            final_out.append(cur)
            cur = dict(e)
            
    if cur:
        if apply_compression:
            cur["text"] = compress_repetitions_marked(cur["text"])
        final_out.append(cur)
        
    logger.debug("Finished phrase-level cleanup. Final aggregated phrases: %d", len(final_out))
    return final_out


def whisper_json_output_pre_treatment(
    transcription_json_path,
    static_config,
    mark_confidence=False,
    compress_repetitions=False,
):
    logger.debug("Initiating Whisper JSON output pre-treatment for: %s", transcription_json_path)
    
    if not os.path.exists(transcription_json_path):
        logger.critical("Error: %s not found.", transcription_json_path)
        sys.exit(1)
        
    options_string = "with "
    if not mark_confidence and (not compress_repetitions):
        options_string += "no flags"
    if mark_confidence and (not compress_repetitions):
        options_string += "confidence marking"
    if compress_repetitions and (not mark_confidence):
        options_string += "repetition compression"
    if mark_confidence and compress_repetitions:
        options_string += "confidence marking and repetition compression"
        
    logger.info("-> Running deterministic pre-processing on %s %s...", transcription_json_path, options_string)
    
    raw_segments = parse_whisper_json(transcription_json_path)
    deduped_entries = dedup_and_filter_hallucinations(
        raw_segments, mark_confidence=mark_confidence
    )
    final_cleaned_entries = phrase_level_cleanup(
        deduped_entries, apply_compression=compress_repetitions
    )
    
    base_name = str(transcription_json_path).replace(
        static_config.suffixes.full_json, ""
    )
    out_json = f"{base_name}{static_config.suffixes.cleaned_json}"
    out_md = f"{base_name}{static_config.suffixes.cleaned_md}"
    
    logger.debug("Writing cleaned data to %s and %s", out_json, out_md)
    
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"segments": final_cleaned_entries}, f, indent=2)
        
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("# Cleaned Transcription\n\n")
        for e in final_cleaned_entries:
            ts = format_timestamp(e["start_ms"])
            f.write(f"**{ts}** {e['text'].strip()}\n\n")
            
    logger.info("✅ Scrubbed output successfully saved to %s and %s", out_json, out_md)