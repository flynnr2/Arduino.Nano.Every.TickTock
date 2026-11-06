"""Exercise firmware capture assembly and its complete host wire contract."""
import csv
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "Nano.Every/src"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


logger = module("capture_test_logger", "tools/nano_capture.py")


def extract(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def compile_test(tmp_path, filename, sources=()):
    compiler = shutil.which("c++")
    if not compiler:
        pytest.skip("A host C++ compiler is required")
    (tmp_path / "Arduino.h").write_text("#pragma once\n#include <stdint.h>\n#define LED_BUILTIN 13\n")
    (tmp_path / "util").mkdir(exist_ok=True)
    (tmp_path / "util/atomic.h").write_text(
        "#pragma once\n#define ATOMIC_RESTORESTATE 0\n"
        "#define ATOMIC_BLOCK(x) for (bool once = true; once; once = false)\n")
    binary = tmp_path / filename
    subprocess.run([compiler, "-std=c++11", "-O2", "-Werror=format", "-DF_CPU=16000000UL",
                    "-I", str(tmp_path), "-I", str(SRC),
                    str(ROOT / "tests/firmware" / (filename + ".cpp")),
                    *(str(SRC / s) for s in sources), "-o", str(binary)], check=True)
    return subprocess.run([str(binary)], check=True, text=True, capture_output=True).stdout


def test_python_capture_schemas_match_shared_header():
    header = (SRC / "PendulumProtocol.h").read_text()
    for family, fields in (("SWING", logger.CSW_FIELDS), ("PPS", logger.CPS_FIELDS)):
        declared = re.search(rf'CANONICAL_{family}_SCHEMA\[\] PROGMEM =\s*"([^"]+)"', header)[1]
        assert declared.split(",") == fields
    assert logger.PROTOCOL_VERSION == re.search(r"PROTOCOL_VERSION = (\d+)", header)[1]
    assert logger.CANONICAL_SWING_SCHEMA_ID == re.search(r'CANONICAL_SWING_SCHEMA_ID\[\] = "([^"]+)"', header)[1]
    assert logger.CANONICAL_PPS_SCHEMA_ID == re.search(r'CANONICAL_PPS_SCHEMA_ID\[\] = "([^"]+)"', header)[1]


def test_firmware_serialization_round_trips_through_host(tmp_path):
    serial = (SRC / "SerialParser.cpp").read_text()
    status = (SRC / "StatusTelemetry.cpp").read_text()
    helpers = serial[serial.index("bool appendChar("):serial.index("constexpr uint32_t TX_TAIL_BUDGET")]
    functions = [extract(serial, f) for f in (
        "void printCsvHeader()", "bool sendCanonicalSwingSample(", "bool sendCanonicalPpsSample(")]
    functions += [extract(status, f) for f in ("void emitSchemaHeader()", "void emitStatusSampleConfig()")]
    (tmp_path / "capture_wire_under_test.inc").write_text(helpers + "\n" + "\n".join(functions))
    wire = compile_test(tmp_path, "capture_wire_test")
    assert "schema,sts=5,eeprom=1,css=canonical_swing_v2,cps=canonical_pps_v1" in wire
    cfg = next(line for line in wire.splitlines() if line.startswith("CFG,"))
    assert "cps=canonical_pps_v1,fw=0.0.0-dev" in cfg
    assert "STS,PROGRESS_UPDATE,cfg," + cfg[4:] in wire
    out = tmp_path / "capture"
    with logger.Capture(out) as capture:
        capture.feed(wire.encode())
        assert capture.malformed == 0
        assert capture.ready
    files = {p.name for p in out.iterdir()}
    assert set(files) == {"PCSW.CSV", "PCPS.CSV", "CFG.CSV", "HDR.CSV", "STS.CSV", "wire_raw.log"}
    with (out / "PCSW.CSV").open() as stream:
        row = list(csv.DictReader(stream))[0]
    assert row["seq"] == str(2**32 - 1)
    assert row["edge2_tcb0"] == "0"
    assert row["drop_swing"] == str(2**32 - 1)
    assert len(row) == 9  # Only original firmware fields; no fabricated environment.
    with (out / "PCPS.CSV").open() as stream:
        row = list(csv.DictReader(stream))[0]
    assert row["cap16"] == str(2**16 - 1)
    assert row["now32"] == str(2**32 - 1)


def test_swing_capture_wrap_overlap_and_backpressure(tmp_path):
    compile_test(tmp_path, "swing_capture_test", ("SwingAssembler.cpp",))



def test_strict_fixture_oracle(tmp_path):
    """Production capture tolerates bad rows; regression fixtures must remain clean."""
    fixtures = ROOT / "tests/fixtures/nano_capture"
    with logger.Capture(tmp_path / "valid") as capture:
        capture.feed((fixtures / "valid_canonical_serial.txt").read_bytes())
        assert capture.ready and capture.malformed == capture.before_ready == 0
        assert capture.counts["CSW"] == capture.counts["CPS"] == 1
    with logger.Capture(tmp_path / "malformed") as capture:
        capture.feed((fixtures / "malformed_row_serial.txt").read_bytes())
        assert capture.malformed > 0
    with logger.Capture(tmp_path / "conflict") as capture:
        with pytest.raises(logger.CaptureError):
            capture.feed((fixtures / "conflicting_schema_serial.txt").read_bytes())
