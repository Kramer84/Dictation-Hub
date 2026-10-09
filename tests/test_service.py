import os
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


def test_tailscale_waits_until_tailscale_is_up(monkeypatch):
    answers = [FileNotFoundError("down"), subprocess.CalledProcessError(1, "tailscale"), b"100.71.34.52\n"]
    slept = []

    def check_output(cmd, **kw):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(service.subprocess, "check_output", check_output)
    assert service.resolve_bind_host("tailscale", wait=60, sleep=slept.append) == "100.71.34.52"
    assert len(slept) == 2


def test_tailscale_wait_gives_up(monkeypatch):
    def fail(cmd, **kw):
        raise FileNotFoundError("tailscale")

    monkeypatch.setattr(service.subprocess, "check_output", fail)
    with pytest.raises(service.ServiceError, match="Tailscale"):
        service.resolve_bind_host("tailscale", wait=20, sleep=lambda s: None)


def test_localhost_is_always_served_too():
    assert service.bind_hosts("100.71.34.52") == ["100.71.34.52", "127.0.0.1"]
    assert service.bind_hosts("127.0.0.1") == ["127.0.0.1"]
    assert service.bind_hosts("0.0.0.0") == ["0.0.0.0"]


def test_sockets_are_opened_on_every_host():
    sockets = service.open_sockets(["127.0.0.1"], 0)
    try:
        assert [s.getsockname()[0] for s in sockets] == ["127.0.0.1"]
    finally:
        for s in sockets:
            s.close()


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
    assert "StartLimitIntervalSec=0" in unit  # keep retrying until Tailscale is up


def test_install_writes_unit_and_enables_it(tmp_path):
    calls = []
    unit_file = tmp_path / "systemd" / "user" / "dictation-hub.service"
    service.install_service("/bin/dictation-hub", unit_file, run=lambda cmd, **kw: calls.append(cmd), user="alice")
    assert "server run" in unit_file.read_text()
    assert calls == [
        ["loginctl", "enable-linger", "alice"],  # start at boot, without a login
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
    monkeypatch.setattr(service, "serve", lambda hosts, port: served.append((hosts, port)))

    service.run_server("100.71.34.52", 8000)

    assert served == [(["100.71.34.52", "127.0.0.1"], 8000)]
    time.sleep(0.2)
    assert children and children[0].poll() is not None


def test_languagetool_dies_even_when_the_server_is_killed(tmp_path):
    """uvicorn re-raises SIGTERM after shutdown, so no `finally` runs: the
    child must be tied to the server's life by the kernel."""
    pid_file = tmp_path / "child.pid"
    script = f"""
import os, signal, subprocess, sys
from dictation_hub.server import service
service.languagetool_command = lambda: [sys.executable, "-c", "import time; time.sleep(30)"]
real_popen = subprocess.Popen
def popen(*a, **kw):
    proc = real_popen(*a, **kw)
    open({str(pid_file)!r}, "w").write(str(proc.pid))
    return proc
service.subprocess.Popen = popen
def serve(hosts, port):
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)
service.serve = serve
service.run_server("127.0.0.1", 0)
"""
    subprocess.run([sys.executable, "-c", script], timeout=30)
    child = int(pid_file.read_text())
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(child, 9)
    pytest.fail("LanguageTool outlived the server")
