import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
import typer
from click.shell_completion import CompletionItem

from .client.streamer import run_remote_stream
from .core.config_manager import get_config_dir, load_deployment_env, load_json_config
from .core.local_runner import run_local_pipeline


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
        completions = super().shell_complete(ctx, incomplete)
        try:
            config = load_json_config("pipeline_config.json")
            profiles = list(config.get("profiles", {}).keys())
        except Exception:
            profiles = ["standard", "technical", "mail_drafting"]
        for profile in profiles:
            if profile.startswith(incomplete):
                completions.append(CompletionItem(profile, help="Dictation profile"))
        return completions


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


@app.command("run", hidden=True)
def main_run(
    ctx: typer.Context,
    profile: str = typer.Argument("standard", help="The dictation profile to use."),
):
    role = load_machine_role()
    if role == "host":
        typer.secho(
            f"[Host] Executing '{profile}' locally via GPU...", fg=typer.colors.MAGENTA
        )
        run_local_pipeline(profile, ctx.args)
    elif role == "client":
        typer.secho(
            f"[Client] Streaming '{profile}' audio to server...", fg=typer.colors.BLUE
        )
        run_remote_stream(ctx, profile)
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


def start_languagetool():
    """Locates the cached LanguageTool JAR and boots it as a background daemon."""
    pid_file = get_pid_file("languagetool")
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), 0)
            typer.secho("✅ LanguageTool is already running.", fg=typer.colors.GREEN)
            return
        except OSError:
            pid_file.unlink(missing_ok=True)
    lt_cache_dir = Path.home() / ".cache" / "language_tool_python"
    try:
        lt_jars = list(lt_cache_dir.rglob("languagetool-server.jar"))
        if not lt_jars:
            typer.secho(
                "⚠️ LanguageTool offline server not found. It will be downloaded dynamically on your first grammar check.",
                fg=typer.colors.YELLOW,
            )
            return
        lt_jar = lt_jars[0]
        typer.secho(
            "🚀 Booting Local LanguageTool Server on port 8081...", fg=typer.colors.CYAN
        )
        log_file = get_config_dir() / "languagetool.log"
        with open(log_file, "w") as f:
            proc = subprocess.Popen(
                [
                    "java",
                    "-cp",
                    str(lt_jar),
                    "org.languagetool.server.HTTPServer",
                    "--port",
                    "8081",
                    "--allow-origin",
                    "*",
                ],
                stdout=f,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
        pid_file.write_text(str(proc.pid))
        typer.secho(
            f"✅ LanguageTool daemonized (PID: {proc.pid})", fg=typer.colors.GREEN
        )
    except Exception as e:
        typer.secho(f"❌ Failed to start LanguageTool: {e}", fg=typer.colors.RED)


@server_app.command("start")
def server_start(
    host: str = typer.Option(
        "0.0.0.0", help="Host IP to bind to (use 'tailscale' for TS IP)"
    ),
    port: int = typer.Option(8000, help="Port to bind to"),
):
    role = load_machine_role()
    if role != "host":
        typer.secho(
            "Error: Server can only be started on a machine configured as a 'host'.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    pid_file = get_pid_file("server")
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), 0)
            typer.secho("⚠️ Server is already running.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)
        except OSError:
            pid_file.unlink()
    if host.lower() == "tailscale":
        try:
            host = (
                subprocess.check_output(["tailscale", "ip", "-4"])
                .decode("utf-8")
                .strip()
            )
            typer.secho(
                f"🔒 Locked to Tailscale Interface: {host}", fg=typer.colors.CYAN
            )
        except Exception:
            typer.secho(
                "❌ Could not determine Tailscale IP. Is Tailscale running?",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
    start_languagetool()
    typer.secho(
        f"🚀 Booting FastAPI Transcription Server on {host}:{port}...",
        fg=typer.colors.CYAN,
    )
    log_file = get_config_dir() / "server.log"
    with open(log_file, "w") as f:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-u","-m",
                "uvicorn",
                "dictation_hub.server.main:app",
                "--host", host,
                "--port", str(port),
            ],
            stdout=f,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
    pid_file.write_text(str(proc.pid))
    typer.secho(f"✅ Server daemonized (PID: {proc.pid})", fg=typer.colors.GREEN)
    typer.secho(f"📄 Real-time logs available at: {log_file}", fg=typer.colors.BLUE)


@server_app.command("stop")
def server_stop():
    typer.secho("Stopping Dictation Hub services...", fg=typer.colors.YELLOW)
    stop_process("languagetool")
    stop_process("server")
    subprocess.run(
        ["pkill", "-f", "uvicorn dictation_hub.server.main:app"],
        stderr=subprocess.DEVNULL,
    )


@server_app.command("logs")
def server_logs():
    typer.secho("Printing logs in terminal:", fg=typer.colors.YELLOW)
    log_file = get_config_dir() / "server.log"
    if log_file.is_file():
        print(log_file.read_text())
    else:
        typer.secho("Log file not found.", fg=typer.colors.RED)
