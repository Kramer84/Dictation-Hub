from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from dictation_hub import cli

runner = CliRunner()


@pytest.fixture
def host_run():
    """Invoke the CLI as a host and capture what reaches the local runner."""
    calls = []

    def fake_runner(profile, overrides, audio_file=None, force_local=False):
        calls.append((profile, overrides))
        invoke.audio_file = audio_file
        invoke.force_local = force_local

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


def test_client_mode_forwards_overrides(monkeypatch):
    monkeypatch.setenv("SERVER_IP", "100.71.34.52")
    with patch.object(cli, "load_machine_role", return_value="client"), patch.object(
        cli, "run_client"
    ) as remote:
        result = runner.invoke(cli.app, ["standard", "-l", "fr"])
    assert result.exit_code == 0, result.output
    profile, overrides, base_url = remote.call_args.args
    assert (profile, overrides) == ("standard", {"language": "fr"})
    assert base_url.startswith("http://100.71.34.52:")


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


def test_local_flag(host_run):
    host_run("standard")
    assert host_run.force_local is False
    host_run("--local", "standard", "-l", "fr")
    assert host_run.force_local is True


# ------------------------------------------------------------ server commands
@pytest.fixture
def host_role():
    with patch.object(cli, "load_machine_role", return_value="host"):
        yield


def test_server_run_waits_for_tailscale_and_serves_it(host_role, monkeypatch):
    from dictation_hub.server import service

    monkeypatch.setenv("HOST_BIND_IP", "tailscale")
    resolved, served = [], []
    monkeypatch.setattr(service, "resolve_bind_host", lambda host, wait=0: resolved.append((host, wait)) or "100.71.34.52")
    monkeypatch.setattr(service, "run_server", lambda host, port: served.append((host, port)))
    result = runner.invoke(cli.app, ["server", "run", "--port", "8001"])
    assert result.exit_code == 0, result.output
    assert resolved[0][0] is None and resolved[0][1] > 0
    assert served == [("100.71.34.52", 8001)]


def test_server_run_warns_when_exposed_on_every_network(host_role, monkeypatch):
    from dictation_hub.server import service

    monkeypatch.setattr(service, "run_server", lambda host, port: None)
    result = runner.invoke(cli.app, ["server", "run", "--host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert "every network" in result.output


def test_install_service_uses_this_executable(host_role, monkeypatch):
    from dictation_hub.server import service

    installed = []
    monkeypatch.setattr(service, "install_service", lambda exec_path: installed.append(exec_path))
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/home/u/.local/bin/dictation-hub")
    result = runner.invoke(cli.app, ["server", "install-service"])
    assert result.exit_code == 0, result.output
    assert installed == ["/home/u/.local/bin/dictation-hub"]


def test_server_start_uses_the_service_when_installed(host_role, monkeypatch, tmp_path):
    from dictation_hub.server import service

    unit = tmp_path / "dictation-hub.service"
    unit.write_text("[Unit]\n")
    monkeypatch.setattr(service, "DEFAULT_UNIT_FILE", unit)
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    result = runner.invoke(cli.app, ["server", "start"])
    assert result.exit_code == 0, result.output
    assert calls == [["systemctl", "--user", "start", "dictation-hub.service"]]
