import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import IO, Optional, Tuple
from urllib.parse import urlencode

import typer


def kill_existing_audio_processes() -> None:
    targets = ["arecord", "parec", "parecord", "rec", "ffmpeg"]
    for target in targets:
        subprocess.run(
            ["killall", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


def build_url(protocol: str, ip: str, port: str, profile: str, extra_args: list) -> str:
    query_params = {"profile": profile}
    i = 0
    while i < len(extra_args):
        if extra_args[i].startswith("--"):
            key = extra_args[i].lstrip("-")
            val = (
                extra_args[i + 1]
                if i + 1 < len(extra_args) and not extra_args[i + 1].startswith("--")
                else ""
            )
            query_params[key] = val
            i += 2 if val else 1
        else:
            i += 1
    query_string = urlencode(query_params)
    return f"{protocol}://{ip}:{port}/transcribe?{query_string}"


def launch_audio_backend(err_log: IO[str]) -> Tuple[Optional[subprocess.Popen], str]:
    forced_backend = os.environ.get("AUDIO_BACKEND")
    backends = [
        (
            "ffmpeg (pulse)",
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-f",
                "pulse",
                "-i",
                "default",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-f",
                "wav",
                "-",
            ],
        ),
        ("arecord (pulse)", ["arecord", "-D", "pulse", "-f", "cd", "-t", "wav"]),
        ("arecord (default)", ["arecord", "-D", "default", "-f", "cd", "-t", "wav"]),
        (
            "rec (SoX)",
            ["rec", "-q", "-r", "44100", "-b", "16", "-c", "1", "-t", "wav", "-"],
        ),
        ("parecord", ["parecord", "--file-format=wav"]),
    ]
    for name, cmd in backends:
        if forced_backend and cmd[0] != forced_backend:
            continue
        if shutil.which(cmd[0]):
            err_log.write(f"=== Attempting {name} Backend ===\n")
            err_log.flush()
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_log)
            time.sleep(0.3)
            if proc.poll() is None:
                return proc, name
    return None, ""


def stream_audio_to_server(
    audio_stream: IO[bytes], url: str, resp_path: str
) -> subprocess.Popen:
    cmd = [
        "curl",
        "-sS",
        "-X",
        "POST",
        "-H",
        "Transfer-Encoding: chunked",
        "-H",
        "Expect:",
        "--data-binary",
        "@-",
        url,
    ]
    resp_file = open(resp_path, "wb")
    return subprocess.Popen(
        cmd, stdin=audio_stream, stdout=resp_file, stderr=sys.stderr
    )


def wait_for_user_stop(audio_proc: subprocess.Popen) -> None:
    print("🎙️ Recording and streaming... Press [Enter] or [Ctrl+C] to stop.")
    try:
        if os.name == "nt":
            import msvcrt

            while audio_proc.poll() is None:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key in (b"\r", b"\n", b"\x03"):
                        break
                time.sleep(0.1)
        else:
            import select

            while select.select([sys.stdin], [], [], 0.0)[0]:
                sys.stdin.read(1)
            while audio_proc.poll() is None:
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    sys.stdin.readline()
                    break
    except KeyboardInterrupt:
        pass
    finally:
        if audio_proc.poll() is None:
            audio_proc.terminate()
            audio_proc.wait()


def copy_to_clipboard(text: str) -> None:
    clipboards = [
        ("wl-copy", ["wl-copy"], "Wayland"),
        ("xclip", ["xclip", "-selection", "clipboard"], "X11"),
        ("pbcopy", ["pbcopy"], "macOS"),
    ]
    for tool, cmd, env_name in clipboards:
        if shutil.which(tool):
            subprocess.run(cmd, input=text.encode("utf-8"), check=True)
            print(f"\n✅ Copied to {env_name} clipboard.")
            return
    print("\n⚠️ No clipboard utility found (tried wl-copy, xclip, pbcopy).")


def handle_server_response(resp_path: str) -> None:
    try:
        with open(resp_path, "r") as f:
            content = f.read().strip()
    except FileNotFoundError:
        print("❌ Error: No response file generated.")
        sys.exit(1)
    if not content or "empty audio stream" in content:
        print("❌ Server rejected stream (empty audio stream).")
        sys.exit(1)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        print("❌ Error parsing server response:")
        print(content)
        sys.exit(1)
    raw_text = data.get("raw_text")
    final_text = data.get("final_text")
    if not raw_text or raw_text == "null":
        print("❌ Invalid server response data:")
        print(content)
        sys.exit(1)
    print("\n=== RAW TEXT ===")
    print(raw_text)
    if final_text and final_text != raw_text:
        print("\n=== POST-PROCESSED TEXT ===")
        print(final_text)
    text_to_copy = final_text if final_text else raw_text
    copy_to_clipboard(text_to_copy)


def run_remote_stream(
    ctx: typer.Context,
    profile: str = typer.Argument(
        "standard", help="The dictation profile (e.g. standard, fast, medical)"
    ),
    server_protocol: str = typer.Option(
        "http", "--protocol", help="Protocol of the transcription server"
    ),
    server_ip: str = typer.Option("127.0.0.1", "--ip", help="IP address of the server"),
    server_port: str = typer.Option("8000", "--port", help="Port of the server"),
):
    kill_existing_audio_processes()
    target_url = build_url(server_protocol, server_ip, server_port, profile, ctx.args)
    with tempfile.TemporaryDirectory() as temp_dir:
        err_path = os.path.join(temp_dir, "dictation.err")
        resp_path = os.path.join(temp_dir, "dictation.resp")
        with open(err_path, "w") as err_log:
            audio_proc, backend_name = launch_audio_backend(err_log)
        if not audio_proc:
            print("❌ Audio capture died instantly. Hardware Error Log:")
            with open(err_path, "r") as f:
                print(f.read())
            sys.exit(1)
        print(f"✅ Audio Backend Secured: {backend_name} (Profile: {profile})")
        curl_proc = stream_audio_to_server(audio_proc.stdout, target_url, resp_path)
        if audio_proc.stdout:
            audio_proc.stdout.close()
        wait_for_user_stop(audio_proc)
        print("-> Audio capture stopped. Waiting for server inference...")
        curl_proc.wait()
        handle_server_response(resp_path)
