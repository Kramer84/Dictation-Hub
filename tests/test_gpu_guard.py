"""GPU pre-flight: whisper must never silently run on the CPU (M2 / D13).

Nothing here needs a GPU: the reading of the card, the clock and the whisper
process are all fakes passed into the functions.
"""
import pytest

from dictation_hub.core import gpu_guard as gg


def state(free, total=8192, util=0):
    return gg.GpuState(free_mib=free, total_mib=total, util_percent=util)


class Clock:
    """A fake clock: sleeping moves time forward and costs nothing."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


# --- reading the card -------------------------------------------------------

def test_parse_smi_reads_free_total_and_utilization():
    assert gg.parse_smi("6173, 8192, 58\n") == state(6173, 8192, 58)


def test_parse_smi_uses_the_first_gpu_and_rejects_garbage():
    assert gg.parse_smi("100, 200, 5\n300, 400, 6\n") == state(100, 200, 5)
    assert gg.parse_smi("") is None
    assert gg.parse_smi("Failed to initialize NVML: Driver/library version mismatch") is None


def test_read_gpu_is_none_when_nvidia_smi_is_missing_or_fails():
    def missing(*a, **k):
        raise FileNotFoundError

    assert gg.read_gpu(run=missing) is None


# --- how much memory a model needs -----------------------------------------

def test_vram_needed_grows_with_the_model_file(tmp_path):
    small, big = tmp_path / "s.bin", tmp_path / "b.bin"
    for path, mib in ((small, 500), (big, 3000)):  # sparse files: no disk used
        with open(path, "wb") as f:
            f.truncate(mib * 1024 * 1024)
    assert gg.vram_needed_mib(big) > gg.vram_needed_mib(small) > 0


def test_vram_needed_has_a_floor_when_the_model_is_missing(tmp_path):
    assert gg.vram_needed_mib(tmp_path / "nope.bin") > 0


# --- waiting for the card --------------------------------------------------

def test_a_free_gpu_is_returned_at_once():
    clock = Clock()
    got = gg.wait_for_gpu(2000, timeout_s=60, read=lambda: state(5000), sleep=clock.sleep, now=clock.now)
    assert got.free_mib == 5000 and clock.sleeps == []


def test_no_card_or_driver_fails_at_once_without_waiting():
    clock = Clock()
    with pytest.raises(gg.GpuUnavailable):
        gg.wait_for_gpu(2000, timeout_s=60, read=lambda: None, sleep=clock.sleep, now=clock.now)
    assert clock.sleeps == []


def test_a_busy_gpu_is_polled_until_memory_frees_up():
    clock = Clock()
    readings = iter([state(500, util=95), state(900, util=90), state(4000, util=10)])
    waits = []
    got = gg.wait_for_gpu(
        2000, timeout_s=60, poll_s=3, read=lambda: next(readings),
        sleep=clock.sleep, now=clock.now, on_wait=waits.append,
    )
    assert got.free_mib == 4000
    assert clock.sleeps == [3, 3]
    assert [w.free_mib for w in waits] == [500, 900]  # progress is reported


def test_a_gpu_that_stays_busy_gives_up_after_the_deadline():
    clock = Clock()
    with pytest.raises(gg.GpuBusy) as err:
        gg.wait_for_gpu(2000, timeout_s=10, poll_s=3, read=lambda: state(300, util=99), sleep=clock.sleep, now=clock.now)
    assert clock.t >= 10
    assert "300" in str(err.value) and "2000" in str(err.value)


def test_the_card_vanishing_while_waiting_is_a_driver_failure():
    clock = Clock()
    readings = iter([state(300), None])
    with pytest.raises(gg.GpuUnavailable):
        gg.wait_for_gpu(2000, timeout_s=60, read=lambda: next(readings), sleep=clock.sleep, now=clock.now)


# --- spotting the CPU fallback in whisper's output --------------------------

@pytest.mark.parametrize("line", [
    "ggml_cuda_init: failed to initialize CUDA: no CUDA-capable device is detected",
    "CUDA error: out of memory",
])
def test_cpu_fallback_lines_are_recognised(line):
    assert gg.is_cpu_fallback(line)
    assert gg.FallbackDetector().feed(line)


@pytest.mark.parametrize("line", [
    "ggml_cuda_init: found 1 CUDA devices:",
    "whisper_backend_init_gpu: using CUDA0 backend",
    "",
])
def test_normal_lines_are_not_fallbacks(line):
    assert not gg.is_cpu_fallback(line)


class FakeProc:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self.returncode = returncode
        self.killed = False

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self):
        return self.returncode


def test_run_guarded_returns_the_output_of_a_normal_run():
    proc = FakeProc(["using CUDA0 backend\n", "done\n"])
    result = gg.run_guarded(["whisper-cli"], popen=lambda *a, **k: proc)
    assert result.returncode == 0
    assert "using CUDA0 backend" in result.stderr and "done" in result.stdout


def test_run_guarded_kills_whisper_as_soon_as_it_falls_back_to_the_cpu():
    proc = FakeProc(["loading model\n", "whisper_backend_init_gpu: no GPU found\n", "never read\n"])
    with pytest.raises(gg.CpuFallback):
        gg.run_guarded(["whisper-cli"], popen=lambda *a, **k: proc)
    assert proc.killed


# --- the whole policy: wait, run, retry -------------------------------------

def test_guard_retries_after_a_fallback_then_succeeds():
    clock = Clock()
    attempts = []

    def run():
        attempts.append(1)
        if len(attempts) < 3:
            raise gg.CpuFallback("no GPU found")
        return "ok"

    out = gg.with_gpu(
        run, need_mib=2000, wait_s=60, retries=3,
        read=lambda: state(5000), sleep=clock.sleep, now=clock.now,
    )
    assert out == "ok" and len(attempts) == 3


def test_guard_gives_up_after_the_retries_with_an_actionable_message():
    clock = Clock()

    def run():
        raise gg.CpuFallback("no GPU found")

    with pytest.raises(gg.GpuUnavailable) as err:
        gg.with_gpu(
            run, need_mib=2000, wait_s=60, retries=2,
            read=lambda: state(5000), sleep=clock.sleep, now=clock.now,
        )
    assert "nvidia-smi" in str(err.value)


def test_guard_does_not_start_whisper_when_the_gpu_never_frees_up():
    clock = Clock()
    started = []
    with pytest.raises(gg.GpuBusy):
        gg.with_gpu(
            lambda: started.append(1), need_mib=2000, wait_s=9, retries=3,
            read=lambda: state(100, util=99), sleep=clock.sleep, now=clock.now,
        )
    assert started == []


# --- VAD prints "no GPU found" for its own CPU-only model (seen 2026-11-05) -----

VAD_RUN = [
    "ggml_cuda_init: found 1 CUDA devices (Total VRAM: 8100 MiB):\n",
    "whisper_model_load:        CUDA0 total size =   573.45 MB\n",
    "whisper_backend_init_gpu: found GPU device 0: CUDA0 (type: 1, cnt: 0)\n",
    "whisper_backend_init_gpu: using CUDA0 backend\n",
    "whisper_vad_init_with_params:          CPU total size =     0.88 MB\n",
    "whisper_backend_init_gpu: no GPU found\n",  # the Silero VAD model, CPU by design
    "whisper_vad_init_context: compute buffer (VAD)   =    1.60 MB\n",
]


def test_vad_s_no_gpu_line_after_the_main_model_is_on_cuda_is_not_a_fallback():
    proc = FakeProc(VAD_RUN)
    result = gg.run_guarded(["whisper-cli"], popen=lambda *a, **k: proc)
    assert not proc.killed and result.returncode == 0


def test_no_gpu_line_before_any_gpu_backend_is_still_a_fallback():
    proc = FakeProc(["whisper_init_with_params_no_state: use gpu = 1\n", "whisper_backend_init_gpu: no GPU found\n"])
    with pytest.raises(gg.CpuFallback):
        gg.run_guarded(["whisper-cli"], popen=lambda *a, **k: proc)
    assert proc.killed


def test_a_cuda_failure_is_a_fallback_even_after_a_gpu_backend_was_seen():
    proc = FakeProc(["whisper_backend_init_gpu: using CUDA0 backend\n", "CUDA error: out of memory\n"])
    with pytest.raises(gg.CpuFallback):
        gg.run_guarded(["whisper-cli"], popen=lambda *a, **k: proc)


def test_the_detector_keeps_its_state_per_run():
    d = gg.FallbackDetector()
    assert not d.feed("whisper_backend_init_gpu: using CUDA0 backend")
    assert not d.feed("whisper_backend_init_gpu: no GPU found")
    assert gg.FallbackDetector().feed("whisper_backend_init_gpu: no GPU found")
