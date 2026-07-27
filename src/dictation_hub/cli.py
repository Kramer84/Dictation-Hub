import typer
import os
from typing import Optional

# Import your underlying logic modules
from .core.local_runner import run_local_pipeline
from .client.streamer import run_remote_stream 

app = typer.Typer()
server_app = typer.Typer(help="Manage the dictation backend server.")
app.add_typer(server_app, name="server")

def load_machine_role() -> str:
    """Read the local deployment config to determine if this is a client or host."""
    # Logic to read ~/.config/dictation_hub/deployment.toml
    # Returning 'client' for demonstration
    return "client" 

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context, 
    profile: str = typer.Argument("standard", help="The dictation profile to use.")
):
    """
    Dictation Hub: Speak to transcribe.
    """
    # If the user typed `dictate server start`, let the sub-app handle it
    if ctx.invoked_subcommand is not None:
        return

    # Check local configuration to determine execution path
    role = load_machine_role()

    if role == "host":
        typer.secho(f"[Host] Executing '{profile}' locally via GPU...", fg=typer.colors.MAGENTA)
        # run_local_pipeline(profile, ctx.args)
    elif role == "client":
        typer.secho(f"[Client] Streaming '{profile}' audio to server...", fg=typer.colors.BLUE)
        # run_remote_stream(profile, ctx.args)
    else:
        typer.secho("Error: Unknown machine role.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

# --- Server Management Commands ---

@server_app.command("start")
def server_start(port: int = 8000):
    """Boot the FastAPI transcription server."""
    role = load_machine_role()
    if role != "host":
        typer.secho("Error: Server can only be started on a machine configured as a 'host'.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
        
    typer.secho(f"Starting server on port {port}...", fg=typer.colors.GREEN)
    # import uvicorn; uvicorn.run(...)

@server_app.command("stop")
def server_stop():
    """Kill the background server process."""
    typer.secho("Stopping server...", fg=typer.colors.YELLOW)
    # Logic to kill PID