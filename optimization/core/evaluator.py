#!/usr/bin/env python3
import re
import string
import jiwer

def normalize_text(text, lang='en'):
    text = text.lower()
    if lang == 'fr':
        text = text.replace("'", " ").replace("’", " ")
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def get_error_metrics(reference, hypothesis):
    """
    Returns a dictionary of detailed error metrics.
    """
    # process_words returns a WordOutput object
    measures = jiwer.process_words(reference, hypothesis)
    return {
        "wer": measures.wer,
        "insertions": measures.insertions,
        "deletions": measures.deletions,
        "substitutions": measures.substitutions
    }