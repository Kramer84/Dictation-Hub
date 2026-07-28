import os
import signal
import subprocess
from pathlib import Path
from typing import Optional

import click
import typer
from click.shell_completion import CompletionItem

from .client.streamer import run_remote_stream
from .core.config_manager import load_deployment_env, get_config_dir, load_json_config
from .core.local_runner import run_local_pipeline


class DictationRouter(typer.core.TyperGroup):
    """Custom Router that falls back to a hidden dictation command if no subcommand matches."""

    def parse_args(self, ctx, args):
        # Typer's built-in flags that must bypass the custom router
        special_flags = {"-h", "--help", "--install-completion", "--show-completion"}

        if not args:
            args.insert(0, "run")
        elif args[0] not in self.commands and args[0] not in special_flags:
            args.insert(0, "run")

        return super().parse_args(ctx, args)

    def shell_complete(self, ctx, incomplete):
        # 1. Fetch standard subcommand completions (e.g., "server")
        completions = super().shell_complete(ctx, incomplete)

        # 2. Fetch profiles dynamically to merge into top-level autocompletion
        try:
            config = load_json_config("pipeline_config.json")
            profiles = list(config.get("profiles", {}).keys())
        except Exception:
            profiles = ["standard", "technical", "mail_drafting"]

        for profile in profiles:
            if profile.startswith(incomplete):
                completions.append(CompletionItem(profile, help="Dictation profile"))

        return completions


# Bind the custom router class to the root app
app = typer.Typer(cls=DictationRouter)
server_app = typer.Typer(help="Manage the dictation backend server.")
app.add_typer(server_app, name="server")


def load_machine_role() -> str:
    config = load_deployment_env()
    return config.get("DICTATION_ROLE", "host")


# Notice this is now a @command(hidden=True), NOT a callback.
# The custom router silently redirects `dictate standard` here.
@app.command("run", hidden=True)
def main_run(
    ctx: typer.Context,
    profile: str = typer.Argument("standard", help="The dictation profile to use.")
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

# ==============================================================================
# SERVER CLI COMMANDS
# ==============================================================================

@server_app.command("start")
def server_start(
    host: str = typer.Option("0.0.0.0", help="Host IP to bind to (use 'tailscale' for TS IP)"),
    port: int = typer.Option(8000, help="Port to bind to")
):
    role = load_machine_role()
    if role != "host":
        typer.secho("Error: Server can only be started on a machine configured as a 'host'.", fg=typer.colors.RED)
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
            host = subprocess.check_output(["tailscale", "ip", "-4"]).decode("utf-8").strip()
            typer.secho(f"🔒 Locked to Tailscale Interface: {host}", fg=typer.colors.CYAN)
        except Exception:
            typer.secho("❌ Could not determine Tailscale IP. Is Tailscale running?", fg=typer.colors.RED)
            raise typer.Exit(1)

    start_languagetool()

    typer.secho(f"🚀 Booting FastAPI Transcription Server on {host}:{port}...", fg=typer.colors.CYAN)
    log_file = get_config_dir() / "server.log"

    with open(log_file, "w") as f:
        proc = subprocess.Popen(
            ["uvicorn", "dictation_hub.server.main:app", "--host", host, "--port", str(port)],
            stdout=f, stderr=subprocess.STDOUT, preexec_fn=os.setsid
        )

    pid_file.write_text(str(proc.pid))
    typer.secho(f"✅ Server daemonized (PID: {proc.pid})", fg=typer.colors.GREEN)
    typer.secho(f"📄 Real-time logs available at: {log_file}", fg=typer.colors.BLUE)


@server_app.command("stop")
def server_stop():
    typer.secho("Stopping Dictation Hub services...", fg=typer.colors.YELLOW)
    stop_process("languagetool")
    stop_process("server")

    # Aggressive fallback just in case the PID was lost
    subprocess.run(["pkill", "-f", "uvicorn dictation_hub.server.main:app"], stderr=subprocess.DEVNULL)
