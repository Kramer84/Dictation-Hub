# optimization/search_space.py

def get_search_space(trial):
    """
    Define all optimizable inference parameters (Decoder Stage).
    VAD_THOLD and LANGUAGE are intentionally omitted as they are handled 
    statically via standard.env and metadata.json respectively.
    """
    return {
        "BEAM_SIZE": trial.suggest_int("BEAM_SIZE", 4, 8),
        "ENTROPY_THOLD": trial.suggest_float("ENTROPY_THOLD", 2.0, 3.0),
        "LOGPROB_THOLD": trial.suggest_float("LOGPROB_THOLD", -1.6, -0.5),
        "NO_SPEECH_THOLD": trial.suggest_float("NO_SPEECH_THOLD", 0.3, 0.8)
    }