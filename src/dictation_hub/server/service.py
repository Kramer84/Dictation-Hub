"""Run the dictation server in the foreground and manage its systemd --user unit."""
import ctypes
import getpass
import ipaddress
import logging
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

UNIT_NAME = "dictation-hub.service"
DEFAULT_UNIT_FILE = Path.home() / ".config" / "systemd" / "user" / UNIT_NAME
TAILSCALE_WAIT_S = 300

logger = logging.getLogger(__name__)


class ServiceError(RuntimeError):
    pass


def resolve_bind_host(host: Optional[str], wait: float = 0, sleep: Callable[[float], None] = time.sleep) -> str:
    """Explicit host, else HOST_BIND_IP from config.env, else the Tailscale IPv4.

    With ``wait``, keep asking Tailscale for up to that many seconds: at boot
    the server can start before Tailscale is up.
    """
    host = host or os.environ.get("HOST_BIND_IP") or "tailscale"
    if host.lower() != "tailscale":
        return host
    waited = 0.0
    while True:
        try:
            return subprocess.check_output(
                ["tailscale", "ip", "-4"], stderr=subprocess.DEVNULL
            ).decode().strip().splitlines()[0]
        except Exception as e:
            if waited >= wait:
                raise ServiceError(f"Could not determine the Tailscale IP. Is Tailscale running? ({e})") from e
        if waited == 0:
            logger.warning("Waiting for Tailscale to come up (up to %d s)...", wait)
        sleep(5)
        waited += 5


def bind_hosts(host: str) -> List[str]:
    """Where to listen: ``host``, plus localhost so the host's own CLI always
    reaches the server, even while Tailscale is down."""
    if exposes_all_interfaces(host) or ipaddress.ip_address(host).is_loopback:
        return [host]
    return [host, "127.0.0.1"]


def open_sockets(hosts: List[str], port: int) -> List[socket.socket]:
    sockets = []
    for host in hosts:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(128)
        sockets.append(sock)
    return sockets


def resolve_bind_port(port: Optional[int]) -> int:
    return port or int(os.environ.get("HOST_BIND_PORT", "8000"))


def exposes_all_interfaces(host: str) -> bool:
    return host in ("0.0.0.0", "::")


def render_unit(exec_path: str) -> str:
    # No After=network-online.target: a user manager cannot order on system
    # targets. `server run` waits for Tailscale itself; if it gives up, the
    # start fails and is retried, forever (StartLimitIntervalSec=0).
    return f"""[Unit]
Description=Dictation Hub transcription server
StartLimitIntervalSec=0

[Service]
ExecStart={exec_path} server run
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def install_service(
    exec_path: str,
    unit_file: Optional[Path] = None,
    run: Callable = subprocess.run,
    user: Optional[str] = None,
) -> None:
    """Install and start the unit. Lingering makes the user's systemd start at
    boot, so the server runs without anyone logged in."""
    unit_file = unit_file or DEFAULT_UNIT_FILE
    unit_file.parent.mkdir(parents=True, exist_ok=True)
    unit_file.write_text(render_unit(exec_path))
    run(["loginctl", "enable-linger", user or getpass.getuser()], check=True)
    run(["systemctl", "--user", "daemon-reload"], check=True)
    run(["systemctl", "--user", "enable", "--now", UNIT_NAME], check=True)


def uninstall_service(unit_file: Optional[Path] = None, run: Callable = subprocess.run) -> None:
    """Remove the unit. Lingering is left on: other user services may need it."""
    unit_file = unit_file or DEFAULT_UNIT_FILE
    run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
    unit_file.unlink(missing_ok=True)
    run(["systemctl", "--user", "daemon-reload"], check=True)


def languagetool_command() -> Optional[List[str]]:
    jars = list((Path.home() / ".cache" / "language_tool_python").rglob("languagetool-server.jar"))
    if not jars:
        return None
    return ["java", "-cp", str(jars[0]), "org.languagetool.server.HTTPServer",
            "--port", "8081", "--allow-origin", "*"]


LANGUAGETOOL_URL = "http://127.0.0.1:8081"
WARM_UP_TEXT = {"en-US": "this is a short warm up sentence", "fr-FR": "ceci est une courte phrase d'essai"}


def warm_up_languagetool(
    languages=("en-US", "fr-FR"),
    wait: float = 120,
    get: Optional[Callable] = None,
    post: Optional[Callable] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait for the LanguageTool daemon, then check one sentence per language.

    Its first request per language takes about 4 s (rules load, JVM warms
    up), later ones about 0.15 s: this moves the slow one out of the first
    dictation after boot. Failures are only logged.
    """
    import requests

    get, post = get or requests.get, post or requests.post
    waited = 0.0
    while True:
        try:
            get(f"{LANGUAGETOOL_URL}/v2/languages", timeout=2)
            break
        except Exception:
            if waited >= wait:
                logger.warning("LanguageTool did not come up within %d s; no warm-up.", wait)
                return
            sleep(1)
            waited += 1
    for language in languages:
        try:
            text = WARM_UP_TEXT.get(language, WARM_UP_TEXT["en-US"])
            post(f"{LANGUAGETOOL_URL}/v2/check", data={"text": text, "language": language}, timeout=30)
        except Exception as e:
            logger.warning("LanguageTool warm-up for %s failed: %s", language, e)
    logger.info("LanguageTool warmed up (%s).", ", ".join(languages))


def serve(hosts: List[str], port: int) -> None:
    import uvicorn

    sockets = open_sockets(hosts, port)
    logger.warning("Dictation server listening on %s, port %d", ", ".join(hosts), port)
    config = uvicorn.Config("dictation_hub.server.main:app", port=port)
    uvicorn.Server(config).run(sockets=sockets)


def _die_with_parent() -> None:
    """In the child: the kernel sends SIGTERM when the server dies, however it
    dies (uvicorn re-raises SIGTERM after shutdown, so no `finally` runs)."""
    PR_SET_PDEATHSIG = 1
    ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


def run_server(host: str, port: int) -> None:
    """Foreground server on ``host`` (resolved, see resolve_bind_host) and
    localhost, with LanguageTool as a child that stops with it, warmed up in
    the background."""
    lt_cmd = languagetool_command()
    lt = subprocess.Popen(
        lt_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=_die_with_parent
    ) if lt_cmd else None
    if lt is not None:
        threading.Thread(target=lambda: warm_up_languagetool(), daemon=True).start()
    try:
        serve(bind_hosts(host), port)
    finally:
        if lt is not None and lt.poll() is None:
            lt.terminate()
            try:
                lt.wait(timeout=10)
            except subprocess.TimeoutExpired:
                lt.kill()
