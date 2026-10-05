"""Check receiver output against real archived files and the existing analysis.

The historical diagnostic columns are intentionally absent on the current wire;
the Pi retains their positions in PCSW without inventing values for them.
"""
from __future__ import annotations

import csv
from itertools import islice
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Raspberry.Pi"))

from pendulum_pi.config import Settings
from pendulum_pi.protocol import CSW_FIELDS, CPS_FIELDS, decode
from pendulum_pi.recording import DIAG_FIELDS, PCPS_FIELDS, PCSW_FIELDS, STS_FIELDS, Recorder

ARCHIVE = Path(__file__).parent / "fixtures" / "csv-reference"


def archived_rows(name, count=20):
    with (ARCHIVE / name).open(newline="") as stream:
        return list(islice(csv.DictReader(stream), count))


@pytest.mark.parametrize("filename,fields", [("PCSW.CSV", PCSW_FIELDS), ("PCPS.CSV", PCPS_FIELDS)])
def test_headers_match_archived_measurement_files(filename, fields):
    with (ARCHIVE / filename).open(newline="") as stream:
        assert next(csv.reader(stream)) == list(fields)


def test_status_and_host_diagnostics_keep_existing_shapes():
    assert list(STS_FIELDS) == ["ts_ms", "raw"]
    assert list(DIAG_FIELDS) == ["ts_ms", "epoch", "category", "key", "value", "msg"]


@pytest.fixture
def recorded_archive(tmp_path):
    settings = Settings(data_dir=tmp_path / "logs", runtime_dir=tmp_path / "runtime", min_free_mb=0)
    recorder = Recorder(settings)
    recorder.start("compatibility-test")
    rows_by_tag = {}
    try:
        for tag, filename, fields in [("CPS", "PCPS.CSV", CPS_FIELDS), ("CSW", "PCSW.CSV", CSW_FIELDS)]:
            rows_by_tag[tag] = archived_rows(filename)
            for index, source in enumerate(rows_by_tag[tag]):
                raw = (tag + "," + ",".join(source[name] for name in fields) + "\n").encode("ascii")
                record = decode(raw)
                environment = {key: float(source[key]) for key in ("temperature_C", "humidity_pct", "pressure_hPa")}
                recorder.capture(tag, record.values, environment, 100.0 + index, 1790424000.0)
    finally:
        recorder.close()
    segments = list(settings.data_dir.glob("*/segment-*/PCSW.CSV"))
    assert len(segments) == 1
    return segments[0].parent, rows_by_tag


@pytest.mark.parametrize("tag,filename,fields", [("CSW", "PCSW.CSV", CSW_FIELDS), ("CPS", "PCPS.CSV", CPS_FIELDS)])
def test_archived_capture_fields_round_trip_without_retiming(recorded_archive, tag, filename, fields):
    directory, rows_by_tag = recorded_archive
    with (directory / filename).open(newline="") as stream:
        actual = list(csv.DictReader(stream))
    expected = rows_by_tag[tag]
    assert len(actual) == len(expected)
    for written, source in zip(actual, expected):
        assert {key: written[key] for key in fields} == {key: source[key] for key in fields}
        for key in ("temperature_C", "humidity_pct", "pressure_hPa"):
            if source[key].lower() == "nan":
                assert written[key] in {"", "nan"}
            else:
                assert float(written[key]) == pytest.approx(float(source[key]))
        if tag == "CSW":
            assert written["adj_diag"] == ""
            assert written["adj_comp_diag"] == ""


def test_receiver_csvs_load_in_existing_analysis(recorded_archive):
    pytest.importorskip("numpy", reason="offline analysis compatibility needs the root analysis environment")
    pytest.importorskip("pandas")
    from pendulum_analysis.load import load_csv

    directory, rows_by_tag = recorded_archive
    loaded = load_csv(directory / "PCSW.CSV")
    assert loaded.validation["ok"]
    assert loaded.validation["original_columns"] == list(PCSW_FIELDS)
    assert len(loaded.data) == len(rows_by_tag["CSW"])
    assert any("aligned gps_status from sibling PCPS.CSV" in warning for warning in loaded.warnings)
