from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from dictation_hub import cli

runner = CliRunner()


@pytest.fixture
def host_run():
    """Invoke the CLI as a host and capture what reaches the local runner."""
    calls = []

    def fake_runner(profile, overrides, audio_file=None):
        calls.append((profile, overrides))
        invoke.audio_file = audio_file

    with patch.object(cli, "load_machine_role", return_value="host"), patch.object(
        cli, "run_local_pipeline", side_effect=fake_runner
    ):
        def invoke(*argv):
            result = runner.invoke(cli.app, list(argv))
            assert result.exit_code == 0, result.output
            return calls[-1]

        invoke.audio_file = None
        yield invoke


def test_no_arguments_runs_standard_profile(host_run):
    assert host_run() == ("standard", {})


def test_profile_without_options(host_run):
    assert host_run("scheduling") == ("scheduling", {})


@pytest.mark.parametrize(
    "argv",
    [
        ("standard", "-l", "fr"),
        ("standard", "--language", "fr"),
        ("standard", "--language=fr"),
        ("-l", "fr", "standard"),
    ],
)
def test_language_flag_forms(host_run, argv):
    assert host_run(*argv) == ("standard", {"language": "fr"})


def test_several_overrides_and_negative_value(host_run):
    profile, overrides = host_run(
        "technical", "--model", "small", "-t", "8", "--logprob-thold", "-0.5"
    )
    assert profile == "technical"
    assert overrides == {"model": "small", "threads": "8", "logprob_thold": "-0.5"}


def test_bare_flag_is_true(host_run):
    assert host_run("standard", "--no-gpu") == ("standard", {"no_gpu": "true"})


def test_client_mode_forwards_overrides():
    with patch.object(cli, "load_machine_role", return_value="client"), patch.object(
        cli, "run_remote_stream"
    ) as remote:
        result = runner.invoke(cli.app, ["standard", "-l", "fr"])
    assert result.exit_code == 0, result.output
    assert remote.call_args.kwargs["profile"] == "standard"
    assert remote.call_args.kwargs["overrides"] == {"language": "fr"}


@pytest.mark.parametrize(
    "argv",
    [
        ("scheduling", "--file", "{wav}"),
        ("-f", "{wav}", "scheduling"),
        ("scheduling", "--file={wav}", "-l", "fr"),
    ],
)
def test_file_option_selects_reprocessing(host_run, tmp_path, argv):
    wav = tmp_path / "take.wav"
    wav.write_bytes(b"RIFF")
    profile, _ = host_run(*(a.format(wav=wav) for a in argv))
    assert profile == "scheduling"
    assert host_run.audio_file == wav


def test_missing_file_is_an_error(tmp_path):
    with patch.object(cli, "load_machine_role", return_value="host"), patch.object(
        cli, "run_local_pipeline"
    ) as local:
        result = runner.invoke(cli.app, ["standard", "--file", str(tmp_path / "nope.wav")])
    assert result.exit_code == 2
    assert "nope.wav" in result.output
    local.assert_not_called()
