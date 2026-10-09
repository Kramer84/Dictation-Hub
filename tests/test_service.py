import subprocess
import sys
import time

import pytest

from dictation_hub.server import service


# ------------------------------------------------------------ bind address
def test_explicit_host_is_used_as_is(monkeypatch):
    monkeypatch.setenv("HOST_BIND_IP", "100.9.9.9")
    assert service.resolve_bind_host("127.0.0.1") == "127.0.0.1"


def test_config_host_is_the_default(monkeypatch):
    monkeypatch.setenv("HOST_BIND_IP", "127.0.0.1")
    assert service.resolve_bind_host(None) == "127.0.0.1"


def test_tailscale_is_the_default_and_is_resolved(monkeypatch):
    monkeypatch.delenv("HOST_BIND_IP", raising=False)
    monkeypatch.setattr(service.subprocess, "check_output", lambda cmd, **kw: b"100.71.34.52\n")
    assert service.resolve_bind_host(None) == "100.71.34.52"


def test_missing_tailscale_is_a_clear_error(monkeypatch):
    def fail(cmd, **kw):
        raise FileNotFoundError("tailscale")

    monkeypatch.setattr(service.subprocess, "check_output", fail)
    with pytest.raises(service.ServiceError, match="Tailscale"):
        service.resolve_bind_host("tailscale")


def test_binding_all_interfaces_is_flagged():
    assert service.exposes_all_interfaces("0.0.0.0")
    assert not service.exposes_all_interfaces("100.71.34.52")


def test_port_default_comes_from_config(monkeypatch):
    monkeypatch.setenv("HOST_BIND_PORT", "8123")
    assert service.resolve_bind_port(None) == 8123
    assert service.resolve_bind_port(9000) == 9000


# ------------------------------------------------------------ systemd unit
def test_unit_runs_the_server_in_the_foreground_and_restarts():
    unit = service.render_unit("/home/u/.local/bin/dictation-hub")
    assert "ExecStart=/home/u/.local/bin/dictation-hub server run" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit


def test_install_writes_unit_and_enables_it(tmp_path):
    calls = []
    unit_file = tmp_path / "systemd" / "user" / "dictation-hub.service"
    service.install_service("/bin/dictation-hub", unit_file, run=lambda cmd, **kw: calls.append(cmd))
    assert "server run" in unit_file.read_text()
    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "dictation-hub.service"],
    ]


def test_uninstall_disables_and_removes_unit(tmp_path):
    calls = []
    unit_file = tmp_path / "dictation-hub.service"
    unit_file.write_text("[Unit]\n")
    service.uninstall_service(unit_file, run=lambda cmd, **kw: calls.append(cmd))
    assert not unit_file.exists()
    assert calls == [
        ["systemctl", "--user", "disable", "--now", "dictation-hub.service"],
        ["systemctl", "--user", "daemon-reload"],
    ]


# ------------------------------------------------------------ foreground run
def test_run_server_stops_languagetool_with_it(monkeypatch):
    children = []
    real_popen = subprocess.Popen

    def popen(cmd, **kw):
        proc = real_popen([sys.executable, "-c", "import time; time.sleep(30)"])
        children.append(proc)
        return proc

    served = []
    monkeypatch.setattr(service, "languagetool_command", lambda: ["languagetool"])
    monkeypatch.setattr(service.subprocess, "Popen", popen)
    monkeypatch.setattr(service, "serve", lambda host, port: served.append((host, port)))

    service.run_server("127.0.0.1", 8000)

    assert served == [("127.0.0.1", 8000)]
    time.sleep(0.2)
    assert children and children[0].poll() is not None
