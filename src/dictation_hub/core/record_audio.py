import re
import select
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer


def record_audio(output_path: Path, record_format: str, record_type: str) -> None:
    print("  Recording audio... Press [Enter] or [Ctrl+C] to stop.")
    process = subprocess.Popen(
        ["arecord", "-f", record_format, "-t", record_type, str(output_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        while process.poll() is None:
            if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                sys.stdin.readline()
                break
    except KeyboardInterrupt:
        pass
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()


GSM_FREQ_HZ = 217
GSM_BUZZ_THRESHOLD_DB = 5.0
# Narrow notches on the 217 Hz harmonic series of GSM/TDMA phone interference.
GSM_NOTCH = ",".join(
    f"bandreject=f={GSM_FREQ_HZ * k}:width_type=h:w=12" for k in range(1, 25)
)


def _band_mean_volume(file_path: Path, freq: int) -> float:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(file_path),
        "-af", f"bandpass=f={freq}:width_type=h:w=6,volumedetect", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    match = re.search(r"mean_volume:\s+([-\d\.]+)\s+dB", result.stderr)
    return float(match.group(1)) if match else -120.0


def gsm_buzz_level(file_path: Path) -> float:
    """How far (dB) the first 217 Hz harmonics stand above their neighbourhood.

    Phone (GSM/TDMA) interference picked up by the mic shows up as a buzz on
    217 Hz and its harmonics, which can drown speech. Clean recordings score
    around 0 dB or below; a buzzing one scored about +12 dB.
    """
    excess = []
    for k in (1, 2, 3):
        f = GSM_FREQ_HZ * k
        centre = _band_mean_volume(file_path, f)
        around = max(_band_mean_volume(file_path, f - 27), _band_mean_volume(file_path, f + 27))
        excess.append(centre - around)
    return sum(excess) / len(excess)


def get_max_volume(file_path: Path, prefilter: str = "") -> Optional[float]:
    af = f"{prefilter},volumedetect" if prefilter else "volumedetect"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(file_path),
        "-af",
        af,
        "-f",
        "null",
        "/dev/null",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    match = re.search(r"max_volume:\s+([-\d\.]+)\s+dB", result.stderr)
    if match:
        return float(match.group(1))
    return None


def build_ffmpeg_filters(
    normalize: bool = False,
    remove_silence: bool = False,
    highpass_filter: bool = False,
    gain: Optional[float] = None,
    gsm_notch: bool = False,
) -> str:
    filters = []
    if gsm_notch:
        filters.append(GSM_NOTCH)
    if highpass_filter:
        filters.append("highpass=f=80")
    if remove_silence:
        filters.append(
            "silenceremove=stop_periods=-1:stop_duration=1:stop_threshold=-50dB"
        )
    if normalize and gain is not None:
        filters.append(f"volume={gain:.2f}dB")
    return ",".join(filters)


def process_audio(
    input_path: Path,
    output_path: Path,
    sample_rate: int,
    channels: int,
    codec: str,
    filters: str = "",
) -> None:
    print(f"-> Processing audio format ({sample_rate / 1000}kHz, mono)...")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-c:a",
        codec,
    ]
    if filters:
        cmd.extend(["-af", filters])
    cmd.append(str(output_path))
    subprocess.run(cmd, check=True)


def prepare_audio(
    input_path: Path,
    output: Path,
    normalize: bool = True,
    remove_silence: bool = False,
    highpass: bool = False,
    sample_rate: int = 16000,
    channels: int = 1,
    codec: str = "pcm_s16le",
    target_peak: float = -6.0,
) -> None:
    """Convert any ffmpeg-readable recording into the WAV whisper expects."""
    buzz = gsm_buzz_level(input_path)
    gsm_notch = buzz > GSM_BUZZ_THRESHOLD_DB
    if gsm_notch:
        print(
            f"   ⚠️ Phone interference detected (217 Hz buzz, +{buzz:.1f} dB): filtering it out. "
            "Keep phones away from the microphone."
        )
    gain = None
    if normalize:
        print("   -> Analyzing audio for peak normalization...")
        max_vol = get_max_volume(input_path, GSM_NOTCH if gsm_notch else "")
        if max_vol is not None:
            gain = target_peak - max_vol
            print(
                f"   -> Applying static whole-file peak gain adjustment: {gain:.2f}dB (Target: {target_peak}dB)..."
            )
        else:
            print("   ⚠️ Warning: Could not detect audio peak. Skipping normalization.")
    filters = build_ffmpeg_filters(normalize, remove_silence, highpass, gain, gsm_notch)
    process_audio(input_path, output, sample_rate, channels, codec, filters)


def record_audio_app(
    output: Path = typer.Option(
        ..., "--output", "-o", help="Path to the output audio file."
    ),
    normalize: bool = typer.Option(
        False, "--normalize", help="Apply two-pass peak normalization."
    ),
    remove_silence: bool = typer.Option(
        False, "--remove-silence", help="Strip silence from the recording."
    ),
    highpass: bool = typer.Option(
        False, "--highpass", help="Apply a highpass filter (80Hz)."
    ),
    record_format: str = typer.Option(
        "cd", help="Hardware format profile for arecord."
    ),
    record_type: str = typer.Option("wav", help="Audio file type for arecord."),
    sample_rate: int = typer.Option(
        16000, help="Target sample rate for the output file."
    ),
    channels: int = typer.Option(1, help="Target audio channel count (1=mono)."),
    codec: str = typer.Option("pcm_s16le", help="Target audio codec."),
    target_peak: float = typer.Option(
        -6.0, help="Target peak volume in dB (used with --normalize)."
    ),
    raw_output: Optional[Path] = typer.Option(
        None, help="Keep the untouched recording at this path (otherwise it is discarded)."
    ),
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_wav = raw_output or output.with_suffix(".tmp.wav")
    record_audio(temp_wav, record_format, record_type)
    prepare_audio(
        temp_wav, output, normalize, remove_silence, highpass,
        sample_rate, channels, codec, target_peak,
    )
    if raw_output is None:
        temp_wav.unlink(missing_ok=True)
    print(f"✅ Audio saved to {output}")
