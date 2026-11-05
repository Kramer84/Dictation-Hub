"""GPU pre-flight for whisper.cpp (M2, defect D13).

whisper-cli silently runs on the CPU when CUDA cannot start, which turns a few
seconds into minutes. This module makes that impossible to miss:

1. ``wait_for_gpu`` checks the card through ``nvidia-smi`` and, when other
   programs hold the memory, polls until enough is free (or gives up).
2. ``run_guarded`` reads whisper's output as it appears and kills the process
   the moment it reports a CPU fallback.
3. ``with_gpu`` ties both together: wait, run, and on a fallback wait again and
   retry a few times, then fail with a message saying what to check.

The reading of the card, the clock and the process are parameters so that tests
need no GPU.
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Heuristic, not measured on every model: weights stay in VRAM, plus compute buffers.
VRAM_OVERHEAD_MIB = 700
VRAM_MODEL_FACTOR = 1.3
MIN_VRAM_MIB = 1000

# Always a failure, wherever it appears.
_FALLBACK_PATTERN = re.compile(
    r"failed to initialize CUDA|CUDA error|cudaMalloc failed|out of memory",
    re.IGNORECASE,
)
# "no GPU found" is a failure only before the main model reports a GPU backend:
# with --vad, whisper prints it again for the Silero VAD model, which runs on the CPU by design.
_NO_GPU = re.compile(r"no GPU found", re.IGNORECASE)
_GPU_BACKEND_UP = re.compile(r"using \w+ backend|found GPU device", re.IGNORECASE)


class GpuError(RuntimeError):
    """Base class of the GPU pre-flight failures."""


class GpuUnavailable(GpuError):
    """No usable GPU: driver or CUDA missing, or whisper keeps falling back to the CPU."""


class GpuBusy(GpuError):
    """The GPU is there but other programs keep its memory past the deadline."""


class CpuFallback(GpuError):
    """Whisper reported that it is running (or about to run) on the CPU."""


@dataclass(frozen=True)
class GpuState:
    free_mib: int
    total_mib: int
    util_percent: int


def parse_smi(text: str) -> Optional[GpuState]:
    """First GPU of ``nvidia-smi --query-gpu=memory.free,memory.total,utilization.gpu`` (csv, no header)."""
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            return GpuState(int(parts[0]), int(parts[1]), int(parts[2]))
    return None


def read_gpu(run: Callable = subprocess.run) -> Optional[GpuState]:
    """Current state of the first GPU, or None when ``nvidia-smi`` is missing or fails."""
    try:
        out = run(
            ["nvidia-smi", "--query-gpu=memory.free,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return parse_smi(out.stdout)


def vram_needed_mib(model_path: Path) -> int:
    """Estimated VRAM to load ``model_path`` (file size x factor + overhead)."""
    try:
        size_mib = Path(model_path).stat().st_size / (1024 * 1024)
    except OSError:
        return MIN_VRAM_MIB
    return max(MIN_VRAM_MIB, int(size_mib * VRAM_MODEL_FACTOR) + VRAM_OVERHEAD_MIB)


def wait_for_gpu(
    need_mib: int,
    timeout_s: float,
    poll_s: float = 3.0,
    *,
    read: Optional[Callable[[], Optional[GpuState]]] = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    on_wait: Optional[Callable[[GpuState], None]] = None,
) -> GpuState:
    """Return once the GPU has ``need_mib`` free; poll every ``poll_s`` while it is busy.

    Raises GpuUnavailable at once when no card answers (a missing driver does
    not fix itself), and GpuBusy when the memory is still taken at the deadline.
    """
    read = read or read_gpu
    deadline = now() + timeout_s
    while True:
        gpu = read()
        if gpu is None:
            raise GpuUnavailable(
                "No NVIDIA GPU answered `nvidia-smi` (driver not loaded or card lost). "
                "Check `nvidia-smi`; a suspend/resume or a driver update often needs a reboot "
                "or `sudo modprobe nvidia`."
            )
        if gpu.free_mib >= need_mib:
            return gpu
        if now() >= deadline:
            raise GpuBusy(
                f"GPU memory stayed taken for {timeout_s:.0f} s: {gpu.free_mib} MiB free, "
                f"{need_mib} MiB needed (utilization {gpu.util_percent} %). "
                "See which program holds it with `nvidia-smi`."
            )
        if on_wait is not None:
            on_wait(gpu)
        sleep(poll_s)


def is_cpu_fallback(line: str) -> bool:
    """A line that is a failure on its own (see FallbackDetector for "no GPU found")."""
    return bool(_FALLBACK_PATTERN.search(line or ""))


class FallbackDetector:
    """Reads whisper's output line by line (one instance per run)."""

    def __init__(self) -> None:
        self.gpu_up = False

    def feed(self, line: str) -> bool:
        """True if ``line`` shows that whisper runs, or is about to run, on the CPU."""
        if is_cpu_fallback(line):
            return True
        if _GPU_BACKEND_UP.search(line or ""):
            self.gpu_up = True
            return False
        return bool(_NO_GPU.search(line or "")) and not self.gpu_up


def run_guarded(cmd: list[str], popen: Callable = subprocess.Popen) -> subprocess.CompletedProcess:
    """Run whisper, stdout and stderr merged; kill it and raise CpuFallback on a fallback line.

    The merged text is returned as both ``stdout`` and ``stderr`` (callers read
    whichever they used before).
    """
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: list[str] = []
    detector = FallbackDetector()
    for line in proc.stdout:
        lines.append(line)
        if detector.feed(line):
            proc.kill()
            proc.wait()
            raise CpuFallback(line.strip())
    proc.wait()
    text = "".join(lines)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=text, stderr=text)


def with_gpu(
    run: Callable[[], T],
    need_mib: int,
    wait_s: float,
    retries: int,
    *,
    read: Optional[Callable[[], Optional[GpuState]]] = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> T:
    """Wait for GPU memory, call ``run``; on CpuFallback wait again and retry up to ``retries`` times."""

    def waiting(gpu: GpuState) -> None:
        logger.warning(
            "GPU busy (%d MiB free, %d needed, %d %% used): waiting.",
            gpu.free_mib, need_mib, gpu.util_percent,
        )

    last: Optional[CpuFallback] = None
    for attempt in range(1, max(1, retries) + 1):
        wait_for_gpu(need_mib, wait_s, read=read, sleep=sleep, now=now, on_wait=waiting)
        try:
            return run()
        except CpuFallback as e:
            last = e
            logger.warning("Whisper fell back to the CPU (attempt %d/%d): %s", attempt, retries, e)
            sleep(2.0)
    raise GpuUnavailable(
        f"Whisper kept falling back to the CPU ({last}). Not running on the CPU: it would take "
        "minutes. Check `nvidia-smi` and the CUDA build of whisper.cpp, or set NO_GPU=true to accept the CPU."
    )
