import os
import subprocess
import sys

import pytest

from dictation_hub.core.invocation import complete_invocation

PROFILES = ["standard", "scheduling", "technical"]
MODELS = ["large-v3-turbo-q5_0", "small"]


def values(previous, incomplete):
    return [v for v, _ in complete_invocation(previous, incomplete, PROFILES, MODELS)]


def test_profiles_offered_first():
    assert values([], "s") == ["standard", "scheduling"]


def test_profiles_not_offered_again_once_chosen():
    assert values(["standard"], "") == []


def test_dash_offers_short_and_long_options():
    offered = values(["standard"], "-")
    assert {"-f", "-l", "-m", "-t", "--file", "--language", "--threads", "--beam-size"} <= set(offered)


def test_long_prefix_filters_options():
    assert values(["standard"], "--lan") == ["--language"]


def test_file_value_falls_back_to_shell_path_completion():
    assert values(["standard", "-f"], "") == []
    assert values(["--file"], "rec") == []


def test_language_values():
    assert "fr" in values(["standard", "-l"], "")
    assert values(["--language"], "f") == ["fr"]


def test_model_values_come_from_installed_models():
    assert values(["standard", "-m"], "") == MODELS


def test_other_option_values_offer_nothing():
    assert values(["standard", "--beam-size"], "") == []


def test_profile_still_offered_after_options_with_values():
    assert values(["-l", "fr"], "sch") == ["scheduling"]


def test_bool_flag_does_not_swallow_the_profile_position():
    assert values(["--no-gpu"], "tech") == ["technical"]


def shell_complete(words, cword):
    env = dict(os.environ, COMP_WORDS=words, COMP_CWORD=str(cword), _DICTATION_HUB_COMPLETE="complete_bash")
    code = "from dictation_hub.cli import app; app(prog_name='dictation-hub')"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    return out.stdout.split()


def test_shell_offers_file_option():
    assert "--file" in shell_complete("dictation-hub standard --fi", 2)


def test_shell_still_offers_server_subcommand():
    assert "server" in shell_complete("dictation-hub ", 1)
