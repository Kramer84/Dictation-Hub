"""One key or button starts a recording, the next one stops it (M4).

Processes, signals and the clock are fakes except for one real in-process signal.
"""
import os
import signal

import pytest

from dictation_hub.core import toggle


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / "state"


def fakes(alive=()):
    log = {"spawned": 0, "signals": []}
    return log, dict(
        spawn=lambda: log.__setitem__("spawned", log["spawned"] + 1),
        is_alive=lambda pid: pid in alive,
        send=lambda pid, sig: log["signals"].append((pid, sig)),
    )


def test_first_press_starts_a_session(state_dir):
    log, f = fakes()
    assert toggle.press(state_dir, **f) == "started"
    assert log["spawned"] == 1 and log["signals"] == []


def test_press_during_recording_asks_it_to_stop(state_dir):
    toggle.write_state(state_dir, pid=4242, phase="recording")
    log, f = fakes(alive=(4242,))
    assert toggle.press(state_dir, **f) == "stopping"
    assert log["signals"] == [(4242, signal.SIGUSR1)] and log["spawned"] == 0


def test_press_while_processing_does_nothing_and_never_starts_a_second_session(state_dir):
    toggle.write_state(state_dir, pid=4242, phase="processing")
    log, f = fakes(alive=(4242,))
    assert toggle.press(state_dir, **f) == "busy"
    assert log["signals"] == [] and log["spawned"] == 0


def test_a_dead_session_s_state_is_ignored(state_dir):
    toggle.write_state(state_dir, pid=4242, phase="recording")  # crashed: pid gone
    log, f = fakes(alive=())
    assert toggle.press(state_dir, **f) == "started"
    assert log["spawned"] == 1


def test_cancel_signals_a_recording_and_is_a_noop_otherwise(state_dir):
    log, f = fakes()
    assert toggle.press(state_dir, cancel=True, **f) == "idle"
    assert log["spawned"] == 0
    toggle.write_state(state_dir, pid=7, phase="recording")
    log, f = fakes(alive=(7,))
    assert toggle.press(state_dir, cancel=True, **f) == "cancelling"
    assert log["signals"] == [(7, signal.SIGUSR2)]


def test_phase_update_and_clear(state_dir):
    toggle.write_state(state_dir, pid=1, phase="recording")
    toggle.set_phase(state_dir, "processing")
    assert toggle.read_state(state_dir)["phase"] == "processing"
    toggle.clear_state(state_dir)
    assert toggle.read_state(state_dir) is None


def test_a_corrupt_state_file_counts_as_no_session(state_dir):
    state_dir.mkdir(parents=True)
    (state_dir / "session.json").write_text("{not json")
    assert toggle.read_state(state_dir) is None


def test_signal_keys_turns_sigusr1_into_a_space_and_restores_handlers():
    before = signal.getsignal(signal.SIGUSR1)
    keys = toggle.signal_keys(poll_interval=0.01)
    assert next(keys) is None  # idle tick
    os.kill(os.getpid(), signal.SIGUSR1)
    got = [k for k in keys if k is not None]
    assert got == [" "]
    assert signal.getsignal(signal.SIGUSR1) == before


def test_signal_keys_turns_sigusr2_into_cancel():
    keys = toggle.signal_keys(poll_interval=0.01)
    next(keys)
    os.kill(os.getpid(), signal.SIGUSR2)
    assert [k for k in keys if k is not None] == ["c"]
