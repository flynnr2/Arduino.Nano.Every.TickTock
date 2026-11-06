"""Check the production CSV integer helpers against standard decimal formatting."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_csv_integer_formatting(tmp_path):
    compiler = shutil.which("c++")
    if not compiler:
        pytest.skip("A host C++ compiler is required for firmware regression tests")
    source = (ROOT / "Nano.Every/src/SerialParser.cpp").read_text()
    helpers = source[source.index("bool appendChar("):
                     source.index("constexpr uint32_t TX_TAIL_BUDGET")]
    (tmp_path / "csv_formatter_under_test.inc").write_text(helpers)
    binary = tmp_path / "csv_formatter_test"
    subprocess.run([
        compiler, "-std=c++11", "-O2", "-I", str(tmp_path),
        str(ROOT / "tests/firmware/csv_formatter_test.cpp"),
        "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)
