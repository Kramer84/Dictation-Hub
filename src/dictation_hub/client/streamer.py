import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO, Dict, Optional, Tuple
from urllib.parse import urlencode

from ..core.config_manager import get_config_dir



def remember_capture(pid_file: Path, pid: int, program: str) -> None:
    pid_file.write_text(f"{pid} {program}")


def kill_stale_capture(pid_file: Path) -> None:
    """Kill the capture process left behind by a previous crashed run, and nothing else."""
    if not pid_file.exists():
        return
    try:
        pid_str, program = pid_file.read_text().split()
        pid = int(pid_str)
        running = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True
        ).stdout.strip()
        if running == program:
            os.kill(pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError, PermissionError):
        pass
    finally:
        pid_file.unlink(missing_ok=True)


def build_url(protocol: str, ip: str, port: str, profile: str, overrides: Dict[str, str]) -> str:
    query_string = urlencode({"profile": profile, **overrides})
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
    profile: str,
    overrides: Dict[str, str],
    server_protocol: str = "http",
    server_ip: str = "127.0.0.1",
    server_port: str = "8000",
    audio_file: Optional[Path] = None,
):
    target_url = build_url(server_protocol, server_ip, server_port, profile, overrides)
    if audio_file is not None:
        with tempfile.TemporaryDirectory() as temp_dir, open(audio_file, "rb") as audio:
            resp_path = os.path.join(temp_dir, "dictation.resp")
            print(f"-> Uploading {audio_file} (Profile: {profile})...")
            stream_audio_to_server(audio, target_url, resp_path).wait()
            handle_server_response(resp_path)
        return
    capture_pid_file = get_config_dir() / "client_capture.pid"
    kill_stale_capture(capture_pid_file)
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
        remember_capture(capture_pid_file, audio_proc.pid, audio_proc.args[0])
        print(f"✅ Audio Backend Secured: {backend_name} (Profile: {profile})")
        curl_proc = stream_audio_to_server(audio_proc.stdout, target_url, resp_path)
        if audio_proc.stdout:
            audio_proc.stdout.close()
        wait_for_user_stop(audio_proc)
        capture_pid_file.unlink(missing_ok=True)
        print("-> Audio capture stopped. Waiting for server inference...")
        curl_proc.wait()
        handle_server_response(resp_path)
