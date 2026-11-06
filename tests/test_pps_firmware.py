"""Exercise production PPS processing with host-side capture/serial substitutes."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "Nano.Every" / "src"


def extract_function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


@pytest.mark.parametrize("baseline_enabled", [0, 1])
def test_production_pps_processing(tmp_path, baseline_enabled):
    compiler = shutil.which("c++")
    if not compiler:
        pytest.skip("A host C++ compiler is required for firmware regression tests")
    source = (SRC / "PendulumCore.cpp").read_text()
    globals_ = source[source.index("static_assert(sizeof(uint16_t)"):
                      source.index("void resetRuntimeStateAfterTunablesChange()")]
    (tmp_path / "pps_runtime_under_test.inc").write_text(
        globals_ + "\n" + extract_function(source, "void resetRuntimeStateAfterTunablesChange()")
        + "\n" + extract_function(source, "static void process_pps(uint8_t budget_remaining)")
    )
    (tmp_path / "Arduino.h").write_text(
        "#pragma once\n#include <stdint.h>\n#define LED_BUILTIN 13\n"
    )
    binary = tmp_path / "pps_runtime_test"
    command = [compiler, "-std=c++11", "-O2", "-DF_CPU=16000000UL",
               "-DENABLE_PROFILING=0", f"-DENABLE_PPS_BASELINE_TELEMETRY={baseline_enabled}",
               "-DPPS_TUNING_TELEMETRY=0", "-DENABLE_RESTART_BREADCRUMBS=0",
               "-DENABLE_TCB_LATENCY_DIAG=0", "-DENABLE_PERIODIC_SERIAL_DIAG_STS=0",
               "-I", str(tmp_path), "-I", str(SRC),
               str(ROOT / "tests" / "firmware" / "pps_runtime_test.cpp")]
    command += [str(SRC / name) for name in (
        "PpsValidator.cpp", "FreqDiscipliner.cpp", "DisciplinedTime.cpp",
        "TunablesRuntime.cpp")]
    subprocess.run(command + ["-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


@pytest.mark.parametrize("tuning_enabled", [0, 1])
def test_nano_settings_and_eeprom_recovery(tmp_path, tuning_enabled):
    compiler = shutil.which("c++")
    if not compiler:
        pytest.skip("A host C++ compiler is required")
    (tmp_path / "startup_replay_under_test.inc").write_text(extract_function(
        (SRC / "PendulumCore.cpp").read_text(), "void emitStartupNow()"))
    binary = tmp_path / "nano_config_test"
    command = [compiler, "-std=c++11", "-O2", "-DF_CPU=16000000UL",
               f"-DPPS_TUNING_TELEMETRY={tuning_enabled}",
               "-I", str(tmp_path), "-I", str(ROOT / "tests/firmware/config_stubs"), "-I", str(SRC),
               str(ROOT / "tests/firmware/eeprom_config_test.cpp")]
    command += [str(SRC / name) for name in (
        "EEPROMConfig.cpp", "TunablesRuntime.cpp", "TunableRegistry.cpp", "TunableCommands.cpp")]
    subprocess.run(command + ["-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
