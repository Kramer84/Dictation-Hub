"""Run the dictation server in the foreground and manage its systemd --user unit."""
import os
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

UNIT_NAME = "dictation-hub.service"
DEFAULT_UNIT_FILE = Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


class ServiceError(RuntimeError):
    pass


def resolve_bind_host(host: Optional[str]) -> str:
    """Explicit host, else HOST_BIND_IP from config.env, else the Tailscale IPv4."""
    host = host or os.environ.get("HOST_BIND_IP") or "tailscale"
    if host.lower() != "tailscale":
        return host
    try:
        return subprocess.check_output(["tailscale", "ip", "-4"]).decode().strip().splitlines()[0]
    except Exception as e:
        raise ServiceError(f"Could not determine the Tailscale IP. Is Tailscale running? ({e})") from e


def resolve_bind_port(port: Optional[int]) -> int:
    return port or int(os.environ.get("HOST_BIND_PORT", "8000"))


def exposes_all_interfaces(host: str) -> bool:
    return host in ("0.0.0.0", "::")


def render_unit(exec_path: str) -> str:
    # No After=network-online.target: a user manager cannot order on system
    # targets. If Tailscale is not up yet, the start fails and is retried.
    return f"""[Unit]
Description=Dictation Hub transcription server

[Service]
ExecStart={exec_path} server run
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def install_service(exec_path: str, unit_file: Path = DEFAULT_UNIT_FILE, run: Callable = subprocess.run) -> None:
    unit_file.parent.mkdir(parents=True, exist_ok=True)
    unit_file.write_text(render_unit(exec_path))
    run(["systemctl", "--user", "daemon-reload"], check=True)
    run(["systemctl", "--user", "enable", "--now", UNIT_NAME], check=True)


def uninstall_service(unit_file: Path = DEFAULT_UNIT_FILE, run: Callable = subprocess.run) -> None:
    run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
    unit_file.unlink(missing_ok=True)
    run(["systemctl", "--user", "daemon-reload"], check=True)


def languagetool_command() -> Optional[List[str]]:
    jars = list((Path.home() / ".cache" / "language_tool_python").rglob("languagetool-server.jar"))
    if not jars:
        return None
    return ["java", "-cp", str(jars[0]), "org.languagetool.server.HTTPServer",
            "--port", "8081", "--allow-origin", "*"]


def serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("dictation_hub.server.main:app", host=host, port=port)


def run_server(host: str, port: int) -> None:
    """Foreground server with LanguageTool as a child that stops with it."""
    lt_cmd = languagetool_command()
    lt = subprocess.Popen(lt_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) if lt_cmd else None
    try:
        serve(host, port)
    finally:
        if lt is not None and lt.poll() is None:
            lt.terminate()
            try:
                lt.wait(timeout=10)
            except subprocess.TimeoutExpired:
                lt.kill()
