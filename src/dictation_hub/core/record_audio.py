import re
import select
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

def record_audio(output_path: Path, record_format: str, record_type: str) -> None:
    """
    Record audio from the microphone until Enter or Ctrl+C is pressed.

    Parameters
    ----------
    output_path : Path
        The file path to save the temporary recorded audio.
    record_format : str
        The format flag for `arecord` (e.g., "cd").
    record_type : str
        The file type flag for `arecord` (e.g., "wav").

    Returns
    -------
    None
    """
    print("  Recording audio... Press [Enter] or [Ctrl+C] to stop.")
    
    process = subprocess.Popen(
        ["arecord", "-f", record_format, "-t", record_type, str(output_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    try:
        # Non-blocking loop checks process status and waits for stdin
        while process.poll() is None:
            # select.select waits 0.1s for sys.stdin to become readable
            if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                sys.stdin.readline()  # Consume the Enter key input
                break
    except KeyboardInterrupt:
        # User pressed Ctrl+C
        pass
    finally:
        # Clean up the arecord process safely
        if process.poll() is None:
            process.terminate()
            process.wait()


def get_max_volume(file_path: Path) -> Optional[float]:
    """
    Analyze the audio file using ffmpeg's volumedetect to find the max peak.

    Parameters
    ----------
    file_path : Path
        The path to the audio file to analyze.

    Returns
    -------
    float or None
        The detected maximum volume in dB. Returns None if detection fails.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(file_path),
        "-af", "volumedetect", "-f", "null", "/dev/null"
    ]
    
    # ffmpeg writes all its output, including volumedetect, to stderr
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    match = re.search(r"max_volume:\s+([-\d\.]+)\s+dB", result.stderr)
    if match:
        return float(match.group(1))
    return None


def build_ffmpeg_filters(
    normalize: bool = False,
    remove_silence: bool = False,
    highpass_filter: bool = False,
    gain: Optional[float] = None
) -> str:
    """
    Construct the ffmpeg audio filter chain string.

    Parameters
    ----------
    normalize : bool, optional
        Whether to apply volume normalization.
    remove_silence : bool, optional
        Whether to apply a silence removal filter.
    highpass_filter : bool, optional
        Whether to apply a highpass filter.
    gain : float, optional
        The static gain adjustment required in dB.

    Returns
    -------
    str
        A comma-separated string of ffmpeg audio filters.
    """
    filters = []
    
    # Added a basic highpass filter (80 Hz) since it was unhandled in Bash
    if highpass_filter:
        filters.append("highpass=f=80")
        
    # Added a basic silence removal filter since it was unhandled in Bash
    if remove_silence:
        filters.append("silenceremove=stop_periods=-1:stop_duration=1:stop_threshold=-50dB")
        
    if normalize and gain is not None:
        filters.append(f"volume={gain:.2f}dB")

    return ",".join(filters)


def process_audio(
    input_path: Path,
    output_path: Path,
    sample_rate: int,
    channels: int,
    codec: str,
    filters: str = ""
) -> None:
    """
    Process the recorded audio with ffmpeg, applying conversions and filters.

    Parameters
    ----------
    input_path : Path
        Path to the temporary input audio file.
    output_path : Path
        Path to the final destination audio file.
    sample_rate : int
        Target sample rate (e.g., 16000).
    channels : int
        Target audio channels (e.g., 1 for mono).
    codec : str
        Target audio codec (e.g., "pcm_s16le").
    filters : str, optional
        Comma-separated string of audio filters (`-af`).

    Returns
    -------
    None
    """
    print(f"-> Processing audio format ({sample_rate / 1000}kHz, mono)...")
    
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(input_path),
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-c:a", codec
    ]
    
    if filters:
        cmd.extend(["-af", filters])
        
    cmd.append(str(output_path))
    
    subprocess.run(cmd, check=True)


def record_audio_app(
    output: Path = typer.Option(..., "--output", "-o", help="Path to the output audio file."),
    normalize: bool = typer.Option(False, "--normalize", help="Apply two-pass peak normalization."),
    remove_silence: bool = typer.Option(False, "--remove-silence", help="Strip silence from the recording."),
    highpass: bool = typer.Option(False, "--highpass", help="Apply a highpass filter (80Hz)."),
    record_format: str = typer.Option("cd", help="Hardware format profile for arecord."),
    record_type: str = typer.Option("wav", help="Audio file type for arecord."),
    sample_rate: int = typer.Option(16000, help="Target sample rate for the output file."),
    channels: int = typer.Option(1, help="Target audio channel count (1=mono)."),
    codec: str = typer.Option("pcm_s16le", help="Target audio codec."),
    target_peak: float = typer.Option(-6.0, help="Target peak volume in dB (used with --normalize).")
) -> None:
    """
    Record audio via microphone and process it to specific requirements.
    """
    # 1. Setup paths
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_wav = output.with_suffix(".tmp.wav")

    # 2. Record raw audio
    record_audio(temp_wav, record_format, record_type)

    # 3. Calculate normalization gain
    gain = None
    if normalize:
        print("   -> Analyzing audio for peak normalization...")
        max_vol = get_max_volume(temp_wav)
        
        if max_vol is not None:
            gain = target_peak - max_vol
            print(f"   -> Applying static whole-file peak gain adjustment: {gain:.2f}dB (Target: {target_peak}dB)...")
        else:
            print("   ⚠️ Warning: Could not detect audio peak. Skipping normalization.")

    # 4. Build filters and process audio
    filters = build_ffmpeg_filters(normalize, remove_silence, highpass, gain)
    process_audio(temp_wav, output, sample_rate, channels, codec, filters)

    # 5. Clean up temp files
    temp_wav.unlink(missing_ok=True)
    print(f"✅ Audio saved to {output}")
