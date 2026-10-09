import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
import typer
from click.shell_completion import CompletionItem

from .client.remote import client_base_url, run_client
from .core.invocation import InvocationError, complete_invocation, parse_invocation
from .core.config_manager import get_config_dir, load_deployment_env, load_json_config
from .core.local_runner import run_local_pipeline
from .pipeline.engine import setup_logging
from .server import service


class DictationRouter(typer.core.TyperGroup):
    """Custom Router that falls back to a hidden dictation command if no subcommand matches."""

    def parse_args(self, ctx, args):
        r"""
        Parse command-line arguments for the CLI application.
        
        This method extends the default argument parsing behavior to handle
        special cases like empty argument lists and non-command arguments.
        
        Parameters
        ----------
        ctx : click.Context
            The Click context object for the current command.
        args : list[str]
            The list of command-line arguments to parse.
        
        Returns
        -------
        click.Context
            The parsed Click context object.
        """
        if ctx.resilient_parsing:
            return super().parse_args(ctx, args)
        special_flags = {"-h", "--help", "--install-completion", "--show-completion"}
        if not args:
            args.insert(0, "run")
        elif args[0] not in self.commands and args[0] not in special_flags:
            args.insert(0, "run")
        return super().parse_args(ctx, args)

    def shell_complete(self, ctx, incomplete):
        r"""
        Generate completion suggestions for the given incomplete input.
        
        Parameters
        ----------
        ctx : click.Context
            The Click context object for the current command.
        incomplete : str
            The incomplete input string to be completed.
        
        Returns
        -------
        List[CompletionItem]
            A list of completion items matching the incomplete input
            string.
        """
        previous = list(getattr(ctx, "_protected_args", [])) + list(ctx.args)
        completions = []
        if not previous:
            completions = [
                c for c in super().shell_complete(ctx, incomplete)
                if c.value in self.commands and not self.commands[c.value].hidden
            ]
        try:
            config = load_json_config("pipeline_config.json")
            profiles = list(config.get("profiles", {}).keys())
        except Exception:
            profiles = ["standard", "technical", "mail_drafting"]
        for value, help_text in complete_invocation(
            previous, incomplete, profiles, installed_models()
        ):
            completions.append(CompletionItem(value, help=help_text))
        return completions


def installed_models() -> list:
    """Whisper models available under WHISPER_DIR/models (test fixtures excluded)."""
    try:
        whisper_dir = load_deployment_env().get("WHISPER_DIR") or "~/whisper.cpp"
    except Exception:
        whisper_dir = "~/whisper.cpp"
    models_dir = Path(os.path.expandvars(whisper_dir)).expanduser() / "models"
    return sorted(
        p.stem[len("ggml-"):]
        for p in models_dir.glob("ggml-*.bin")
        if "silero" not in p.stem
    )


app = typer.Typer(cls=DictationRouter)
server_app = typer.Typer(help="Manage the dictation backend server.")
app.add_typer(server_app, name="server")


def load_machine_role() -> str:
    r"""
    Retrieve the machine role from the deployment environment.
    
    Returns
    -------
    str
        The machine role as defined in the deployment environment.
    
    See Also
    --------
    load_deployment_env :
        Loads the deployment environment configuration.
    """
    config = load_deployment_env()
    return config.get("DICTATION_ROLE", "host")


@app.command(
    "run",
    hidden=True,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def main_run(ctx: typer.Context):
    try:
        invocation = parse_invocation(ctx.args)
    except InvocationError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    profile, overrides = invocation.profile, invocation.overrides
    setup_logging(verbose=invocation.verbose)
    role = load_machine_role()
    if role == "host":
        run_local_pipeline(
            profile, overrides, audio_file=invocation.audio_file, force_local=invocation.force_local
        )
    elif role == "client":
        run_client(profile, overrides, client_base_url(), audio_file=invocation.audio_file)
    else:
        typer.secho("Error: Unknown machine role.", fg=typer.colors.RED)
        raise typer.Exit(code=1)


def get_pid_file(process_name: str) -> Path:
    """Returns the Path to the PID file for a given process."""
    return get_config_dir() / f"{process_name}.pid"


def stop_process(process_name: str):
    """Gracefully terminates a background process using its PID file."""
    pid_file = get_pid_file(process_name)
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text())
            os.kill(pid, signal.SIGTERM)
            typer.secho(
                f"✅ Stopped {process_name} (PID: {pid})", fg=typer.colors.GREEN
            )
        except ProcessLookupError:
            typer.secho(f"⚠️ {process_name} was not running.", fg=typer.colors.YELLOW)
        except ValueError:
            typer.secho(f"⚠️ Invalid PID in {pid_file}", fg=typer.colors.RED)
        finally:
            pid_file.unlink(missing_ok=True)
    else:
        typer.secho(f"ℹ️ No PID file found for {process_name}.", fg=typer.colors.BLUE)


def _require_host() -> None:
    if load_machine_role() != "host":
        typer.secho(
            "Error: The server can only run on a machine configured as a 'host'.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)


def _service_installed() -> bool:
    return service.DEFAULT_UNIT_FILE.is_file()


def _executable() -> str:
    return shutil.which("dictation-hub") or str(Path(sys.argv[0]).resolve())


HOST_HELP = (
    "Address to listen on (localhost is always served too). Default: HOST_BIND_IP "
    "from config.env, else 'tailscale' (this machine's Tailscale IPv4)."
)


@server_app.command("run")
def server_run(
    host: Optional[str] = typer.Option(None, help=HOST_HELP),
    port: Optional[int] = typer.Option(None, help="Port. Default: HOST_BIND_PORT, else 8000."),
):
    """Run the server in the foreground (what the systemd service runs)."""
    _require_host()
    setup_logging(verbose=True)
    try:
        bind = service.resolve_bind_host(host, wait=service.TAILSCALE_WAIT_S)
    except service.ServiceError as e:
        typer.secho(f"❌ {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if service.exposes_all_interfaces(bind):
        typer.secho(
            f"⚠️ Listening on every network ({bind}): Wi-Fi, LAN and Docker too, not only "
            "Tailscale. Only Tailscale and localhost clients are accepted (HOST_ALLOWED_NETWORKS).",
            fg=typer.colors.YELLOW,
        )
    service.run_server(bind, service.resolve_bind_port(port))


@server_app.command("install-service")
def server_install_service():
    """Install the systemd user service: starts at boot and restarts on failure."""
    _require_host()
    try:
        service.install_service(_executable())
    except subprocess.CalledProcessError as e:
        typer.secho(f"❌ {' '.join(e.cmd)} failed (exit code {e.returncode}).", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho(
        f"✅ {service.UNIT_NAME} installed and started; it starts at boot.\n"
        "   Logs: dictation-hub server logs",
        fg=typer.colors.GREEN,
    )


@server_app.command("uninstall-service")
def server_uninstall_service():
    """Stop and remove the systemd user service."""
    service.uninstall_service()
    typer.secho(f"✅ {service.UNIT_NAME} removed.", fg=typer.colors.GREEN)


@server_app.command("start")
def server_start(
    host: Optional[str] = typer.Option(None, help=HOST_HELP),
    port: Optional[int] = typer.Option(None, help="Port. Default: HOST_BIND_PORT, else 8000."),
):
    """Start the server in the background (through the service when installed)."""
    _require_host()
    if _service_installed():
        subprocess.run(["systemctl", "--user", "start", service.UNIT_NAME])
        return
    pid_file = get_pid_file("server")
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), 0)
            typer.secho("⚠️ Server is already running.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)
        except (OSError, ValueError):
            pid_file.unlink()
    cmd = [_executable(), "server", "run"]
    if host:
        cmd += ["--host", host]
    if port:
        cmd += ["--port", str(port)]
    log_file = get_config_dir() / "server.log"
    with open(log_file, "w") as f:
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    pid_file.write_text(str(proc.pid))
    typer.secho(f"✅ Server started (PID: {proc.pid}). Logs: {log_file}", fg=typer.colors.GREEN)


@server_app.command("stop")
def server_stop():
    """Stop the server (and its LanguageTool)."""
    if _service_installed():
        subprocess.run(["systemctl", "--user", "stop", service.UNIT_NAME])
        return
    stop_process("server")
    if get_pid_file("languagetool").exists():  # left over from older versions
        stop_process("languagetool")


@server_app.command("status")
def server_status():
    if _service_installed():
        subprocess.run(["systemctl", "--user", "--no-pager", "status", service.UNIT_NAME])
        return
    pid_file = get_pid_file("server")
    try:
        os.kill(int(pid_file.read_text()), 0)
        typer.secho(f"✅ Server running (PID: {pid_file.read_text()})", fg=typer.colors.GREEN)
    except (OSError, ValueError):
        typer.secho("Server is not running.", fg=typer.colors.YELLOW)


@server_app.command("logs")
def server_logs(lines: int = typer.Option(200, "--lines", "-n")):
    if _service_installed():
        subprocess.run(["journalctl", "--user", "-u", service.UNIT_NAME, "-n", str(lines), "--no-pager"])
        return
    log_file = get_config_dir() / "server.log"
    if log_file.is_file():
        print("\n".join(log_file.read_text().splitlines()[-lines:]))
    else:
        typer.secho("Log file not found.", fg=typer.colors.RED)
