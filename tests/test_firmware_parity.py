"""Compare all exported replay state with compiled production firmware."""
from pathlib import Path
import random
import shutil
import subprocess

import pytest

from pendulum_analysis.pps.firmware_parity import DiscState, FirmwareParity

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("state fast slow applied r_ppm lock_streak unlock_streak transition_streak "
          "holdover_age_ms last_good_slow fast_err_ticks slow_err_ticks applied_err_ticks "
          "fast_err_ppm slow_err_ppm applied_err_ppm slow_mad_ticks applied_mad_ticks "
          "mad_ticks lock_pass_mask unlock_breach_mask").split()


@pytest.fixture(scope="module")
def firmware(tmp_path_factory):
    compiler = shutil.which("c++")
    if not compiler:
        pytest.skip("A host C++ compiler is required")
    directory = tmp_path_factory.mktemp("discipliner")
    (directory / "Arduino.h").write_text("#pragma once\n#include <stdint.h>\n#define LED_BUILTIN 13\n")
    source = ROOT / "Nano.Every/src"
    binary = directory / "replay"
    subprocess.run([compiler, "-std=c++11", "-O2", "-DF_CPU=16000000UL", "-I", str(directory),
                    "-I", str(source), str(ROOT / "tests/firmware/freq_discipliner_replay.cpp"),
                    str(source / "FreqDiscipliner.cpp"), str(source / "TunablesRuntime.cpp"),
                    "-o", str(binary)], check=True)
    return binary


def compare(firmware, events):
    result = subprocess.run([str(firmware)], input="\n".join(" ".join(map(str, e)) for e in events),
                            capture_output=True, text=True, check=True)
    host = FirmwareParity()
    states = []
    for i, (event, output) in enumerate(zip(events, result.stdout.splitlines())):
        reset, cls, valid, ticks, ms, anomaly, fast, slow = event
        host.config.fast_shift, host.config.slow_shift = fast, slow
        if reset:
            host.reset(ticks)
        else:
            host.observe(cls, valid, ticks, ms, anomaly)
        actual = list(map(int, output.split()))
        expected = [int(getattr(host, name)) for name in FIELDS]
        assert actual == expected, (i, event, dict(zip(FIELDS, actual)), dict(zip(FIELDS, expected)))
        states.append(host.state)
    assert len(result.stdout.splitlines()) == len(events)
    return host, states


@pytest.mark.parametrize("error", [-14, -1, 1, 14])
def test_sub_tick_corrections_accumulate(firmware, error):
    events = [(0, 0, 1, 16_000_000 + error, i * 1000, 0, 3, 8) for i in range(3000)]
    host, _ = compare(firmware, events)
    assert host.slow == host.applied == 16_000_000 + error


def test_replay_transitions_timing_wrap_resets_and_shift_changes(firmware):
    events = []
    now = 0xFFFF0000
    def add(count, ticks=16_000_014, valid=1, cls=0, anomaly=0, fast=3, slow=8):
        nonlocal now
        for _ in range(count):
            events.append((0, cls, valid, ticks, now & 0xFFFFFFFF, anomaly, fast, slow))
            now += 1000
    add(100)  # acquisition spans millis rollover
    add(5, anomaly=1)  # unlock on fifth breach
    add(100)  # reacquire
    add(2, valid=0, cls=1)
    add(100)  # short holdover recovery
    add(65, valid=0, cls=1)  # holdover expiration
    add(100, ticks=15_999_986, fast=1, slow=15)
    rng = random.Random(4)
    for _ in range(500):
        add(1, ticks=16_000_000 + rng.randrange(-6000, 6000), anomaly=rng.randrange(10) == 0,
            fast=2, slow=5)
    events = [tuple(int(v) for v in event) for event in events]
    events.append((1, 0, 0, 16_000_000, 0, 0, 3, 8))
    host, states = compare(firmware, events)
    assert set(states) == set(DiscState)
    assert host.fast_q16 == host.slow_q16 == 16_000_000 << 16


@pytest.mark.parametrize("error", [-1, 1])
def test_max_shift_small_errors_and_clamping(firmware, error):
    # Largest legal shift must retain corrections at one tick/second.
    events = [(0, 0, 1, 16_000_000 + error, i * 1000, 0, 0, 255) for i in range(40000)]
    host, _ = compare(firmware, events)
    assert host.fast == host.slow == 16_000_000 + error


def test_supported_signed_error_bounds(firmware):
    events = [(1, 0, 0, 0, 0, 0, 1, 1)]
    events += [(0, 0, 1, 0x7FFFFFFF, i * 1000, 0, 1, 1) for i in range(100)]
    events += [(0, 0, 1, 0, i * 1000, 0, 1, 1) for i in range(100, 200)]
    host, _ = compare(firmware, events)
    assert host.fast == host.slow == 0
