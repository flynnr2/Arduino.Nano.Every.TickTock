"""Checks for observed capture physics, exclusions and unattended reporting."""
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from pendulum_analysis.pps import PpsConfig, analyze_frame, analyze_files, run_analysis
from pendulum_analysis.pps.core import U16, U32, add_swing_association, discover, reconstruct_timeline


def captures(edges=None, shift=None, latency=None, seq=None):
    physical = np.asarray(edges if edges is not None else [1000 + i * 16_000_016 for i in range(8)], dtype=np.int64)
    n = len(physical)
    shift = np.zeros(n, dtype=int) if shift is None else np.asarray(shift)
    latency = np.full(n, 97) if latency is None else np.asarray(latency)
    edge = (physical + shift) % U32
    return pd.DataFrame({"seq": np.arange(1, n + 1) if seq is None else seq,
        "edge_tcb0": edge, "cap16": (physical + 8) % U16, "latency16": latency,
        "now32": (edge + latency) % U32, "gps_status": 2, "drop_pps": 0, "temperature_C": 24.0})


def swings(path, completions, seq=None):
    completions = np.asarray(completions, dtype=np.int64)
    data = {"seq": np.arange(1, len(completions) + 1) if seq is None else seq}
    for i in range(5):
        data[f"edge{i}_tcb0"] = (completions - (4 - i) * 1000) % U32
    frame = pd.DataFrame(data)
    frame.to_csv(path, index=False)
    return frame


def test_six_cycle_effect_is_an_edge_shift_not_a_latency_rule():
    data = captures(shift=[0, 0, 6, 0, 0, 0, 0, 0], latency=[97, 152, 143, 97, 1268, 97, 97, 97])
    result = analyze_frame(data)
    assert result.summary["six_cycle_shift_records"] == 1
    assert result.summary["rail_shifted_records"] == 0
    assert result.frame.frequency_offset_cycles.iloc[2:4].tolist() == [22, 10]
    assert result.frame.diagnostic_adjusted_offset_cycles.dropna().tolist() == [16] * 7
    assert result.summary["large_latency_projection_shifted_records"] == 0
    assert result.summary["event_counts"]["large_latency"] == 1


def test_wrap_and_multiwrap_gap_are_reconstructed_without_frequency_bridge():
    start = U32 - 200
    data = captures(edges=[start, start + 16_000_016, start + 601 * 16_000_016], seq=[100, 101, 701])
    result = analyze_frame(data)
    assert result.summary["segments"] == 1
    assert result.frame.interval_cycles.iloc[1] == 16_000_016
    assert result.frame.interval_cycles.iloc[2] == 600 * 16_000_016
    assert result.frame.frequency_valid.tolist() == [False, True, False]
    assert result.summary["missing_sequence_records"] == 599


def test_reset_does_not_become_a_wrap_or_join_event_windows():
    result = analyze_frame(captures(edges=[1_000_000, 17_000_016, 100, 16_000_116], seq=[10, 11, 1, 2]))
    assert result.frame.segment.tolist() == [0, 0, 1, 1]
    assert np.isnan(result.frame.interval_cycles.iloc[2])
    windows = result.tables["event_windows"]
    assert windows[windows.event_source_row == 4].segment.eq(1).all()


def test_unsigned_sequence_rollover_is_consecutive():
    result = analyze_frame(captures(seq=[U32 - 4, U32 - 3, U32 - 2, U32 - 1, 0, 1, 2, 3]))
    assert result.summary["segments"] == 1
    assert result.frame.seq_delta.iloc[1:].eq(1).all()


@pytest.mark.parametrize("bad", ["invalid", np.nan, np.inf, -1, U32, 2.5])
def test_malformed_required_fields_are_preserved_and_do_not_create_gap_counts(bad):
    data = captures().astype({"seq": object})
    data.loc[3, "seq"] = bad
    result = analyze_frame(data)
    assert result.summary["malformed_records"] == 1
    assert result.summary["missing_sequence_records"] == 0
    assert not result.frame.frequency_valid.iloc[3:5].any()
    assert result.tables["malformed_records"].source_row.tolist() == [5]


def test_drop_counter_is_cumulative_and_both_endpoints_are_checked():
    data = captures()
    data["drop_pps"] = [0, 0, 1, 1, 1, U32, 0, 0]
    result = analyze_frame(data)
    assert result.frame.frequency_valid.tolist() == [False, True, False, True, True, False, False, True]


def test_lock_recovery_and_extra_capture_pair():
    data = captures(edges=[100, 16_000_100, 24_000_100, 32_000_100, 48_000_100, 64_000_100, 80_000_100])
    data["gps_status"] = [2, 2, 3, 3, 1, 2, 2]
    result = analyze_frame(data)
    assert result.summary["event_counts"]["possible_extra_capture"] == 1
    assert result.summary["event_counts"]["gps_state_change"] == 3
    assert result.frame.frequency_valid.tolist() == [False, True, False, False, False, False, True]


def test_consistency_failure_excludes_both_touching_intervals():
    data = captures()
    data.loc[3, "now32"] += 1
    result = analyze_frame(data)
    assert result.summary["event_counts"]["reconstruction_mismatch"] == 1
    assert not result.frame.frequency_valid.iloc[3:5].any()


def test_missing_optional_inputs_produce_unavailable_statistics_not_successful_zero():
    data = captures().drop(columns=["gps_status", "drop_pps", "temperature_C"])
    result = analyze_frame(data)
    assert result.summary["frequency_valid_intervals"] == 0
    assert result.summary["raw_frequency_offset_cycles"]["mean"] is None
    assert len(result.tables["state_runs"]) == 1
    assert result.warnings


def test_swing_alignment_wrap_and_missing_swing_exposure(tmp_path):
    path = tmp_path / "PCSW.CSV"
    start = U32 - 100_000
    swings(path, [start, start + 32_000_000, start + 96_000_000, start + 128_000_000], seq=[1, 2, 4, 5])
    result = analyze_frame(captures(edges=[start + x for x in (12_000, 16_012_000, 32_012_000, 48_012_000, 64_012_000, 80_012_000, 96_012_000)],
                                      latency=[1000, 97, 97, 97, 97, 97, 1000]))
    add_swing_association(result, path)
    assert result.summary["swing_association"]["available"]
    assert result.frame.since_edge4_us.iloc[0] == 750
    assert result.frame.since_edge4_us.iloc[2:6].isna().all()
    assert result.summary["swing_association"]["associated_large_latency_records"] == 2
    bins = result.tables["swing_latency_bins"]
    assert bins.records.sum() == result.summary["swing_association"]["associated_pps_records"]


def test_invalid_swing_edge_order_disables_association(tmp_path):
    path = tmp_path / "PCSW.CSV"
    data = swings(path, [100, 32_000_100, 64_000_100])
    data.loc[1, "edge1_tcb0"] = data.loc[1, "edge4_tcb0"] + 1
    data.to_csv(path, index=False)
    result = analyze_frame(captures())
    add_swing_association(result, path)
    assert not result.summary["swing_association"]["available"]


def test_case_insensitive_discovery_ignores_uno(tmp_path):
    captures().to_csv(tmp_path / "pCpS.cSv", index=False)
    (tmp_path / "UNO.CSV").write_text("not a valid input; never read this")
    assert set(discover(tmp_path)) == {"pcps"}


def test_config_rejects_bad_units_and_options():
    with pytest.raises(ValueError):
        PpsConfig(nominal_hz=0)
    with pytest.raises(ValueError):
        PpsConfig(nominal_hz=float("nan"))
    with pytest.raises(ValueError):
        PpsConfig(event_window_records=1000)


def test_unattended_cli_eventless_report_and_provenance(tmp_path):
    source = tmp_path / "PCPS.CSV"
    captures().to_csv(source, index=False)
    out = tmp_path / "report"
    completed = subprocess.run([sys.executable, "-m", "pendulum_analysis.pps.historical_main", str(source), "--out", str(out)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    summary = json.loads((out / "summary.json").read_text())
    assert summary["complete"]
    assert summary["event_counts"]["large_latency"] == 0
    assert summary["provenance"]["inputs"]["pcps"]["sha256"]
    assert len(list((out / "plots").glob("*.png"))) == 3
    assert pd.read_csv(out / "csv/events.csv").empty
    table = pd.read_csv(out / "csv/pps_characteristics.csv")
    assert len(table) == 8
    assert table.iloc[0]["n"] == 7
    assert table.iloc[1]["median"] == 1000
    assert table.iloc[3]["n"] == 0
    import base64
    import re
    html = (out / "report.html").read_text()
    images = re.findall(r"src='data:image/png;base64,([^']+)'", html)
    assert images
    assert all(base64.b64decode(data).startswith(b"\x89PNG\r\n\x1a\n") for data in images)
    assert "src='plots/" not in html
    for filename in ("report.html", "report.md"):
        report = (out / filename).read_text()
        assert "PPS timing characteristics" in report
        assert "PCPS_offline_pps_adjusted_residual_ns" in report
        assert "GPS lock and holdover" in report
        assert "broader filter" in report
        assert "unavailable" in report


def test_cli_bad_schema_fails_noninteractively(tmp_path):
    path = tmp_path / "PCPS.CSV"
    path.write_text("seq,edge_tcb0\n1,100\n")
    completed = subprocess.run([sys.executable, "-m", "pendulum_analysis.pps.historical_main", str(path)], capture_output=True, text=True)
    assert completed.returncode == 2
    assert "missing required columns" in completed.stderr


def test_all_malformed_records_still_produce_an_explanatory_report(tmp_path):
    data = captures().astype({"seq": object})
    data["seq"] = "broken"
    path = tmp_path / "PCPS.CSV"
    data.to_csv(path, index=False)
    result = run_analysis(path, tmp_path / "report")
    assert result.summary["malformed_records"] == len(data)
    assert result.summary["raw_frequency_offset_cycles"]["mean"] is None
    assert (tmp_path / "report/report.html").exists()
    table = result.tables["pps_characteristics"]
    assert table.n.eq(0).all()
    assert table["median"].isna().all()


def test_characteristics_use_frequency_population_and_configured_units():
    hz = 8_000_000
    d = captures(edges=1000 + np.arange(60)*(hz+16))
    d.loc[20, "gps_status"] = 3
    c = analyze_frame(d, PpsConfig(nominal_hz=hz))
    t = c.tables["pps_characteristics"].set_index("metric")
    assert t.loc["PCPS_pps_raw_error_cycles", "n"] == 57
    assert t.loc["PCPS_latency16_valid", "n"] == 57
    assert t.loc["PCPS_cap16_valid", "n"] == 57
    assert t.loc["PCPS_pps_raw_error_ns", "median"] == 2000
    assert t.loc["PCPS_pps_raw_error_ppm", "median"] == 2
    assert t.loc["PCPS_offline_pps_adjusted_residual_cycles", "n"] == 37
    assert "robust spread is zero" in t.loc["PCPS_latency16_valid", "note"]
    counts = c.tables["pps_discrete_counts"]
    assert counts[counts.metric == "PCPS_offline_pps_adjusted_residual_cycles"].records.sum() == 37
    assert c.tables["pps_cap16_bins"].records.sum() == 60
    health = c.tables["pps_timebase_health"].set_index("metric")
    assert pd.isna(health.loc["Maximum holdover age", "value"])


def test_offline_residual_is_causal_and_restarts_after_excluded_intervals():
    offsets = np.r_[np.full(40, 16), np.full(40, 80)]
    d = captures(edges=1000 + np.r_[0, np.cumsum(16_000_000+offsets)])
    d.loc[40, "gps_status"] = 3
    c = analyze_frame(d)
    f = c.frame
    assert f.offline_pps_adjusted_residual_cycles.iloc[:11].isna().all()
    assert f.offline_pps_adjusted_residual_cycles.iloc[11:40].eq(0).all()
    assert f.offline_pps_adjusted_residual_cycles.iloc[40:52].isna().all()
    assert f.offline_expected_error_cycles_prev31.iloc[52] == 80
    assert f.offline_pps_adjusted_residual_cycles.iloc[52:].eq(0).all()
    # Independent full-window check includes a changing signal and excludes current value.
    offsets = np.arange(60)
    d = captures(edges=1000 + np.r_[0, np.cumsum(16_000_000+offsets)])
    c = analyze_frame(d)
    assert c.frame.offline_expected_error_cycles_prev31.iloc[50] == np.median(offsets[18:49])


def test_offline_residual_does_not_apply_timer_projection_normalization():
    shift = np.zeros(50, dtype=int)
    shift[35] = 6
    c = analyze_frame(captures(edges=1000+np.arange(50)*16_000_016, shift=shift))
    assert c.frame.diagnostic_adjusted_offset_cycles.iloc[35:37].tolist() == [16, 16]
    assert c.frame.offline_pps_adjusted_residual_cycles.iloc[35:37].tolist() == [6, -6]


@pytest.mark.skipif(not os.environ.get("PPS_67DAYS_DIR"), reason="Set PPS_67DAYS_DIR for the full historical regression")
def test_historical_67days_baseline():
    result = analyze_files(Path(os.environ["PPS_67DAYS_DIR"]))
    s = result.summary
    assert s["records"] == 5_820_076
    assert s["six_cycle_shift_records"] == 6_181
    assert s["rail_records"] == 5_181
    assert s["rail_shifted_records"] == 5_068
    assert s["event_counts"]["large_latency"] == 132
    assert s["event_counts"]["sequence_gap"] == 2
    assert s["missing_sequence_records"] == 112
    assert s["event_counts"]["possible_extra_capture"] == 1
    assert s["swing_association"]["associated_large_latency_records"] == 132
    assert s["swing_association"]["large_latency_since_edge4_us"]["min"] == pytest.approx(697, abs=1)
    assert s["swing_association"]["large_latency_since_edge4_us"]["max"] == pytest.approx(990, abs=1)
    assert s["temperature_fit"]["slope_ppm_per_C"] == pytest.approx(.0217, abs=.0001)
