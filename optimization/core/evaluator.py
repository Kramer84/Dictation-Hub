#!/usr/bin/env python3
import re
import string

def normalize_text(text):
    """
    Lowercases, removes punctuation, and normalizes whitespace
    to ensure fair WER comparison.
    """
    text = text.lower()
    text = text.translate(str.maketrans('', '', string.punctuation))
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def calculate_wer(reference, hypothesis):
    """
    Calculates the Word Error Rate (WER) using Levenshtein distance.
    WER = (Substitutions + Deletions + Insertions) / Total Reference Words
    """
    ref_words = reference.split()
    hyp_words = hypothesis.split()
    
    r_len = len(ref_words)
    h_len = len(hyp_words)
    
    if r_len == 0:
        return float('inf')
    
    d = [[0] * (h_len + 1) for _ in range(r_len + 1)]
    
    for i in range(r_len + 1):
        d[i][0] = i
    for j in range(h_len + 1):
        d[0][j] = j
        
    for i in range(1, r_len + 1):
        for j in range(1, h_len + 1):
            cost = 0 if ref_words[i-1] == hyp_words[j-1] else 1
            d[i][j] = min(
                d[i-1][j] + 1,      # Deletion
                d[i][j-1] + 1,      # Insertion
                d[i-1][j-1] + cost  # Substitution
            )
            
    wer = d[r_len][h_len] / r_len
    return wer

if __name__ == "__main__":
    # Diagnostic test
    ref = "This is a test of the Whisper transcription tool."
    hyp = "This is test of the whisper transcription tools"
    
    norm_ref = normalize_text(ref)
    norm_hyp = normalize_text(hyp)
    
    wer = calculate_wer(norm_ref, norm_hyp)
    print(f"Reference:  {norm_ref}")
    print(f"Hypothesis: {norm_hyp}")
    print(f"WER Score:  {wer:.2%}")