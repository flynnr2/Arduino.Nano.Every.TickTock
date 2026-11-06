from pathlib import Path
import importlib.util
import json
import re

import numpy as np
import pandas as pd

from pendulum_analysis.artifacts import ArtifactRegistry
from pendulum_analysis.allan import ALLAN_COLUMNS, compute_allan, overlapping_allan_deviation
from pendulum_analysis.columns import build_column_map
from pendulum_analysis.canonical_v2 import (
    build_bundle,
    build_environmental_correlations,
    build_fft_components,
    build_health_scorecard,
    build_phase_fold_summary,
    build_relationship_summary,
    build_report_provenance,
    build_sanity_checks,
    build_statistics_table,
    compact_stats_for_report,
    detect_pps_adjustment_status,
    PCPS_HEALTH_FIGURES,
    _fmt_report_value,
    _frame_table,
    _mapping_table,
    _markdown_table,
    _sample_frame,
    prepare_pcps_frame,
    prepare_pcsw_frame,
    sequence_diagnostics,
)
from pendulum_analysis.historical_cli import main
from pendulum_analysis.config import AnalysisConfig, load_config_layers
from pendulum_analysis.derive import add_derived_fields
from pendulum_analysis.filters import add_filter_flags, mask_audit_counts, robust_outlier_mask
from pendulum_analysis.load import load_csv
from pendulum_analysis.pcps import analyze_pcps
from pendulum_analysis.pcps_health import analyze_pcps_timebase_health, first_available_column, has_column
from pendulum_analysis.pcps_health import analyze_cap16_phase_coverage, analyze_latency16, largest_empty_cap16_gap
from pendulum_analysis.phasefold import add_phase_index
from pendulum_analysis.planner import build_analysis_plan, build_dataset_metadata
from pendulum_analysis.modules import module_registry
from pendulum_analysis.profile import ExpectedPeriod, derive_capabilities, load_profile, select_clock_profile
from pendulum_analysis.quality import count_locked_transitions
from pendulum_analysis.schema import DERIVED, FLAGS, INPUT
from pendulum_analysis.timeline import elapsed_u32, unwrap_u32


def base_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            INPUT.tick_block: [20_000, 20_010, 20_020, 20_030, 20_040, 20_050],
            INPUT.tick: [7_980_000, 7_979_990, 7_979_980, 7_979_970, 7_979_960, 8_300_000],
            INPUT.tock_block: [20_000, 19_990, 19_980, 19_970, 19_960, 19_950],
            INPUT.tock: [7_980_000, 7_980_010, 7_980_020, 7_980_030, 7_980_040, 7_980_050],
            INPUT.dropped: [0, 0, 1, 0, 0, 0],
            INPUT.gps_status: [2, 2, 2, 1, 2, 2],
            INPUT.row_index: [0, 1, 2, 3, 4, 5],
        }
    )


def test_schema_validation_succeeds_and_warns(tmp_path: Path) -> None:
    run = write_canonical_run(tmp_path)
    loaded = load_csv(run / "PCSW.CSV")
    assert loaded.validation["ok"] is True
    assert INPUT.row_index in loaded.data.columns
    assert loaded.warnings


def test_schema_validation_requires_capture_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    base_frame().to_csv(path, index=False)
    import pytest
    with pytest.raises(ValueError, match="required PCSW/CSW column"):
        load_csv(path)


def write_canonical_run(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    pcps = pd.DataFrame(
        {
            "seq": [1, 2, 3, 4, 5],
            "edge_tcb0": [100, 16_000_100, 32_000_100, 48_000_105, 64_000_095],
            "gps_status": [2, 2, 2, 2, 2],
            "holdover_age_ms": [0, 0, 0, 0, 0],
            "cap16": [10, 11, 12, 13, 14],
            "latency16": [2, 2, 3, 2, 2],
            "now32": [102, 16_000_102, 32_000_103, 48_000_107, 64_000_097],
            "drop_pps": [0, 0, 0, 0, 0],
        }
    )
    pcps.to_csv(tmp_path / "PCPS.CSV", index=False)
    pcsw = pd.DataFrame(
        {
            "seq": [10, 11],
            "edge0_tcb0": [150, 350],
            "edge1_tcb0": [170, 370],
            "edge2_tcb0": [180, 380],
            "edge3_tcb0": [220, 420],
            "edge4_tcb0": [230, 430],
            "drop_ir": [0, 0],
            "drop_pps": [0, 0],
            "drop_swing": [0, 0],
            "temperature_C": [20.1, 20.2],
            "humidity_pct": [40.0, 40.1],
            "pressure_hPa": [1010.0, 1010.1],
        }
    )
    pcsw.to_csv(tmp_path / "PCSW.CSV", index=False)
    pd.DataFrame({"ts_ms": [1], "raw": ["cfg nhz=16000000"]}).to_csv(tmp_path / "STS.CSV", index=False)
    pd.DataFrame({"ts_ms": [1], "category": ["wifi"], "key": ["rssi"], "value": [-60]}).to_csv(tmp_path / "UNO.CSV", index=False)
    return tmp_path


def test_canonical_pcsw_loads_with_sibling_pcps(tmp_path: Path) -> None:
    run_dir = write_canonical_run(tmp_path)
    loaded = load_csv(run_dir / "PCSW.CSV")

    assert loaded.validation["input_format"] == "canonical_swing"
    assert loaded.data.loc[0, DERIVED.open_A] == 20
    assert loaded.data.loc[0, DERIVED.block_A] == 10
    assert loaded.data.loc[0, DERIVED.open_B] == 40
    assert loaded.data.loc[0, DERIVED.block_B] == 10
    assert loaded.data.loc[0, INPUT.tick] == 20
    assert loaded.data.loc[0, INPUT.gps_status] == 2
    assert loaded.data.loc[0, INPUT.temp_C] == 20.1


def test_canonical_pcsw_wrap_elapsed(tmp_path: Path) -> None:
    pcsw = pd.DataFrame(
        {
            "seq": [1],
            "edge0_tcb0": [2**32 - 5],
            "edge1_tcb0": [3],
            "edge2_tcb0": [8],
            "edge3_tcb0": [13],
            "edge4_tcb0": [18],
            "drop_ir": [0],
            "drop_pps": [0],
            "drop_swing": [0],
        }
    )
    path = tmp_path / "PCSW.CSV"
    pcsw.to_csv(path, index=False)

    loaded = load_csv(path)

    assert loaded.data.loc[0, DERIVED.open_A] == 8
    assert loaded.data.loc[0, DERIVED.block_A] == 5
    assert loaded.data.loc[0, INPUT.gps_status] == 0


def test_wrap_helpers_handle_multiple_wraps() -> None:
    values = pd.Series([2**32 - 3, 2, 10, 2**32 - 1, 4])
    unwrapped = unwrap_u32(values)
    assert unwrapped.iloc[1] == 2**32 + 2
    assert elapsed_u32(pd.Series([2**32 - 3]), pd.Series([2])).iloc[0] == 5


def test_pcps_analysis_computes_raw_and_residual(tmp_path: Path) -> None:
    run_dir = write_canonical_run(tmp_path)
    analysis = analyze_pcps(run_dir / "PCPS.CSV", AnalysisConfig(nominal_hz=16_000_000, pps_residual_window=3))
    assert analysis.summary["valid_interval_rows"] == 4
    assert "raw_pps_error_ppm" in analysis.summary
    assert "offline_adjusted_residual_cycles" in analysis.intervals.columns


def test_derived_fields_are_numerically_correct() -> None:
    config = AnalysisConfig()
    data = add_derived_fields(base_frame().iloc[:1], config)
    assert data.loc[0, DERIVED.tick_half_cycles] == 8_000_000
    assert data.loc[0, DERIVED.tock_half_cycles] == 8_000_000
    assert data.loc[0, DERIVED.period_cycles] == 16_000_000
    assert data.loc[0, DERIVED.period_s] == 1.0
    assert data.loc[0, DERIVED.rate_ppm] == -500_000.0


def test_raw_durations_use_nominal_counter_rate() -> None:
    config = AnalysisConfig(nominal_hz=10.0)
    data = add_derived_fields(base_frame(), config)
    assert (data[DERIVED.f_used_hz] == 10.0).all()


def test_filter_flags_and_robust_outlier_behavior() -> None:
    config = AnalysisConfig(target_period_s=1.0, outlier_threshold=3.0)
    data = add_derived_fields(base_frame(), config)
    flagged, warnings = add_filter_flags(data, config)
    assert bool(flagged.loc[0, FLAGS.is_locked]) is True
    assert bool(flagged.loc[2, FLAGS.is_dropped]) is True
    assert bool(flagged.loc[3, FLAGS.is_clean_primary]) is False
    assert bool(flagged.loc[0, FLAGS.is_analysis]) is True
    assert bool(flagged.loc[0, FLAGS.is_robust_summary]) is bool(flagged.loc[0, FLAGS.is_clean_robust])
    assert flagged[FLAGS.is_period_outlier].sum() >= 1
    audit = mask_audit_counts(flagged)
    assert audit["analysis_mask_rows"] == int(flagged[FLAGS.is_analysis].sum())
    assert audit["robust_summary_mask_rows"] == int(flagged[FLAGS.is_robust_summary].sum())
    assert warnings == [] or all(isinstance(w, str) for w in warnings)


def test_robust_outlier_fallback_or_skip() -> None:
    result = robust_outlier_mask(pd.Series([1, 1, 1, 1, 10]), threshold=3.0)
    assert result.method in {"iqr", "skipped"}


def test_locked_to_unlocked_transition_count() -> None:
    config = AnalysisConfig()
    data = add_derived_fields(base_frame(), config)
    flagged, _ = add_filter_flags(data, config)
    assert count_locked_transitions(flagged) == 2


def test_phase_fold_uses_swing_index_and_row_index_fallback() -> None:
    config = AnalysisConfig(phase_mod=3)
    data = base_frame()
    data[INPUT.swing_index] = [10, 11, None, 13, 14, 15]
    phased = add_phase_index(data, config)
    assert phased.loc[0, DERIVED.phase_index] == 1
    assert phased.loc[2, DERIVED.phase_index] == 2


def test_allan_skips_gracefully_on_short_data() -> None:
    config = AnalysisConfig()
    data = add_derived_fields(base_frame().iloc[:3], config)
    flagged, _ = add_filter_flags(data, config)
    allan = compute_allan(flagged, config)
    assert allan.empty
    assert list(allan.columns) == ALLAN_COLUMNS


def test_allan_reports_structure_preserving_policy() -> None:
    rows = 80
    frame = pd.DataFrame(
        {
            INPUT.tick_block: [20_000] * rows,
            INPUT.tick: [7_980_000] * rows,
            INPUT.tock_block: [20_000] * rows,
            INPUT.tock: [7_980_000] * rows,
            INPUT.dropped: [0] * rows,
            INPUT.gps_status: [2] * rows,
            INPUT.row_index: np.arange(rows),
        }
    )
    config = AnalysisConfig(min_allan_diffs=2)
    data = add_derived_fields(frame, config)
    flagged, _ = add_filter_flags(data, config)
    allan = compute_allan(flagged, config)

    assert not allan.empty
    assert set(["mask_name", "robust_outliers_excluded", "gap_policy"]).issubset(allan.columns)
    assert set(allan["mask_name"]) == {"structure_diagnostic_mask"}
    assert allan["robust_outliers_excluded"].eq(False).all()


def test_overlapping_allan_uses_m_separated_average_differences() -> None:
    y = np.array([0.0, 0.0, 4.0, 4.0, 8.0, 8.0, 12.0, 12.0])
    allan = overlapping_allan_deviation(y, sample_period_s=1.0, min_diffs=1, max_m=2)

    m2 = allan.loc[allan["m"] == 2].iloc[0]
    assert m2["n_averages"] == 7
    assert m2["n_diffs"] == 5
    assert m2["adev_fractional"] == np.sqrt(8.0)
    adjacent_bug_adev = np.sqrt(2.0)
    assert m2["adev_fractional"] != adjacent_bug_adev


def test_allan_uses_powers_of_two_only_without_max_endpoint() -> None:
    y = np.arange(100, dtype=float)
    allan = overlapping_allan_deviation(y, sample_period_s=2.0, min_diffs=20)
    assert set(allan["m"]) == {1, 2, 4, 8, 16, 32}
    assert 33 not in set(allan["m"])


def test_phase_fold_outputs_empty_bins_when_phase_missing() -> None:
    frame = pd.DataFrame(
        {
            "seq": [idx for idx in range(30) if idx % 15 != 12],
            "full_resid_us": np.arange(28, dtype=float),
            FLAGS.is_clean_robust: True,
        }
    )
    summary = build_phase_fold_summary(frame, [15])

    assert set(summary["phase"]) == set(range(15))
    missing = summary.loc[summary["phase"] == 12].iloc[0]
    assert missing["n"] == 0
    assert missing["status"] == "empty"
    assert pd.isna(missing["median"])


def test_phase_fold_keeps_phase_when_robust_mask_removes_all_rows() -> None:
    seq = np.arange(45)
    frame = pd.DataFrame(
        {
            "seq": seq,
            "full_resid_us": np.where(seq % 15 == 12, 250.0, 0.0),
            "half_asymmetry_ms": np.where(seq % 15 == 12, 5.0, 0.0),
            FLAGS.is_analysis: True,
            FLAGS.is_clean_primary: True,
            FLAGS.is_robust_summary: seq % 15 != 12,
            FLAGS.is_clean_robust: seq % 15 != 12,
            FLAGS.is_period_outlier: seq % 15 == 12,
        }
    )

    summary = build_phase_fold_summary(frame, [15])
    phase12 = summary.loc[summary["phase"] == 12].iloc[0]

    assert phase12["status"] == "ok"
    assert phase12["analysis_rows"] == 3
    assert phase12["valid_rows"] == 3
    assert phase12["primary_analysis_rows"] == 3
    assert phase12["robust_summary_rows"] == 0
    assert phase12["median"] == 250.0
    assert phase12["robust_outlier_fraction"] == 1.0
    assert "excluded by robust_summary_mask" in phase12["warning"]


def test_sample_frame_is_phase_time_balanced_not_fixed_stride() -> None:
    seq = np.arange(60_000)
    frame = pd.DataFrame({"seq": seq, "t_s": seq.astype(float), "full_resid_us": np.sin(seq / 15.0)})
    sampled = _sample_frame(frame, 1_500, phase_mod=15)

    assert len(sampled) <= 1_500
    assert sampled["sample_method"].iloc[0] == "phase_time_balanced_mod15"
    counts = (sampled["seq"] % 15).value_counts()
    assert set(counts.index) == set(range(15))
    assert counts.max() - counts.min() <= 1


def test_no_fixed_stride_sampling_in_canonical_sampler() -> None:
    source = Path("pendulum_analysis/canonical_v2.py").read_text(encoding="utf-8")
    sample_body = source[source.index("def _sample_frame") : source.index("def _inferred_period")]
    assert "iloc[::" not in sample_body


def test_phase_fold_refuses_stride_sampled_phase_collapse() -> None:
    frame = pd.DataFrame(
        {
            "seq": np.arange(0, 900, 15),
            "full_resid_us": np.arange(60, dtype=float),
            FLAGS.is_clean_robust: True,
        }
    )
    try:
        build_phase_fold_summary(frame, [15])
    except ValueError as exc:
        assert "phase coverage" in str(exc)
    else:
        raise AssertionError("phase fold should reject collapsed stride-sampled phase coverage")

    sampled = _sample_frame(pd.DataFrame({"seq": np.arange(100), "full_resid_us": np.arange(100, dtype=float)}), 10)
    assert sampled["sampled"].any()
    assert set(["sample_stride", "sample_note"]).issubset(sampled.columns)


def test_pps_adjustment_status_detects_missing_noop_and_available() -> None:
    assert detect_pps_adjustment_status(pd.DataFrame({"full_s": [1.0]}))[0] == "unavailable"
    noop = pd.DataFrame({"full_s": [1.0, 1.1], "full_pps_adj_s": [1.0, 1.1], "scale_prev31": [1.0, 1.0]})
    assert detect_pps_adjustment_status(noop)[0] == "no_op"
    adjusted = pd.DataFrame({"full_s": [1.0, 1.1], "full_pps_adj_s": [0.99, 1.09], "scale_prev31": [0.99, 0.99]})
    assert detect_pps_adjustment_status(adjusted)[0] == "available"


def test_environmental_correlations_include_lagged_association_rows() -> None:
    t_s = np.arange(20, dtype=float) * 3600.0
    frame = pd.DataFrame(
        {
            "t_s": t_s,
            "temp_C": np.linspace(20.0, 25.0, len(t_s)),
            "full_resid_us": np.linspace(0.0, 10.0, len(t_s)),
            FLAGS.is_clean_robust: True,
        }
    )
    correlations = build_environmental_correlations(frame, AnalysisConfig(env_lag_hours=2))

    assert {"metric", "env_var", "window_name", "lag_s", "method", "correlation", "n"}.issubset(correlations.columns)
    assert set(correlations["lag_s"]) >= {-7200, -3600, 0, 3600, 7200}
    same_row = correlations.loc[(correlations["env_var"] == "temp_C") & (correlations["lag_s"] == 0)]
    assert "global same-row Pearson" in set(same_row["method"])


def test_fft_components_include_phase_depatterned_series_and_structure_labels() -> None:
    seq = np.arange(300)
    residual = np.sin(2 * np.pi * seq / 15.0)
    frame = pd.DataFrame({"seq": seq, "full_resid_us": residual})
    peaks = build_fft_components(frame, inferred_period_s=2.0, config=AnalysisConfig(clock_profile="synchronome"), count=6)

    assert {"full_resid_us", "full_resid_us_phase_depatterned"}.issubset(set(peaks["series"]))
    assert (peaks["structure_period_s"] == 30.0).any()
    matched = peaks.loc[peaks["expected_period_s"].eq(30.0)]
    assert matched["expected_match"].any()
    assert "Impulse cycle" in set(matched["expected_label"])


def test_fft_matches_synchronome_seventh_harmonic() -> None:
    seq = np.arange(840)
    residual = np.sin(2 * np.pi * seq / (15.0 / 7.0))
    frame = pd.DataFrame({"seq": seq, "full_resid_us": residual})
    peaks = build_fft_components(frame, inferred_period_s=2.0, config=AnalysisConfig(clock_profile="synchronome"), count=8)

    raw = peaks.loc[peaks["series"].eq("full_resid_us")]
    assert (raw["expected_order"] == 7).any()
    assert np.isclose(raw.loc[raw["expected_order"].eq(7), "expected_period_s"].iloc[0], 30.0 / 7.0)


def test_fft_generic_profile_does_not_mark_synchronome_harmonics_expected() -> None:
    seq = np.arange(300)
    residual = np.sin(2 * np.pi * seq / 15.0)
    frame = pd.DataFrame({"seq": seq, "full_resid_us": residual})
    peaks = build_fft_components(frame, inferred_period_s=2.0, config=AnalysisConfig(clock_profile="generic"), count=4)

    raw = peaks.loc[peaks["series"].eq("full_resid_us")]
    assert not raw["expected_match"].any()
    assert "Synchronome" not in " ".join(raw["interpretation"].astype(str))


def test_fft_user_configured_expected_period_matches_generic_profile() -> None:
    seq = np.arange(300)
    residual = np.sin(2 * np.pi * seq / 20.0)
    frame = pd.DataFrame({"seq": seq, "full_resid_us": residual})
    config = AnalysisConfig(clock_profile="generic", fft_expected_periods_s=(40.0,), fft_expected_period_labels=("Configured 40 s",))
    peaks = build_fft_components(frame, inferred_period_s=2.0, config=config, count=4)

    raw = peaks.loc[peaks["series"].eq("full_resid_us")]
    assert raw["expected_match"].any()
    assert "Configured 40 s" in set(raw["expected_label"])


def test_fft_clusters_adjacent_peaks_deterministically() -> None:
    seq = np.arange(600)
    residual = np.sin(2 * np.pi * seq / 15.0) + 0.9 * np.sin(2 * np.pi * seq / (600.0 / 41.0))
    frame = pd.DataFrame({"seq": seq, "full_resid_us": residual})
    expected = [ExpectedPeriod.from_period(30.0, "Impulse cycle", family="impulse_cycle", order=1, source="clock_profile")]
    config = AnalysisConfig(fft_peak_cluster_tolerance_pct=5.0, fft_match_tolerance_pct=5.0)

    first = build_fft_components(frame, inferred_period_s=2.0, config=config, expected_structure_periods_s=expected, count=4)
    second = build_fft_components(frame, inferred_period_s=2.0, config=config, expected_structure_periods_s=expected, count=4)
    raw = first.loc[first["series"].eq("full_resid_us")]

    assert raw.iloc[0]["cluster_size"] >= 2
    assert raw["period_s"].between(28.0, 31.0).sum() == 1
    pd.testing.assert_frame_equal(first, second)


def test_discrete_statistics_show_counts_when_robust_spread_is_zero() -> None:
    pcsw = pd.DataFrame({"full_s": [2.0] * 20 + [2.1], "full_resid_us": [0.0] * 20 + [100.0]})
    pcps = pd.DataFrame({"valid_pps_interval": [True] * 21, "latency16": [2] * 20 + [9], "cap16": list(range(21))})
    stats = build_statistics_table(pcsw, pcps)
    latency = stats.loc[stats["metric"] == "PCPS_latency16_valid"].iloc[0]

    assert latency["robust_sigma"] == 0
    assert latency["unique_values"] == 2
    assert "2" in latency["top_values"]
    assert "robust spread is zero" in latency["spread_note"]


def test_report_value_formatting_reduces_false_precision() -> None:
    assert _fmt_report_value(1.9996865625, "inferred_full_period_s") == "1.999687"
    assert _fmt_report_value(0.123456, "correlation") == "0.123"
    assert _fmt_report_value(12.34567, "full_ppm_vs_inferred") == "12.3"
    assert _fmt_report_value(12345, "n") == "12,345"
    assert _fmt_report_value(1.23456, "half_asymmetry_ms") == "1.235"


def test_markdown_tables_are_padded_and_escape_cell_pipes() -> None:
    table = _markdown_table(["metric", "value"], [["short", "1"], ["longer_metric", "a|b"]])
    lines = table.splitlines()

    assert lines[0] == "| metric        | value |"
    assert lines[1] == "| ------------- | ----- |"
    assert lines[3] == r"| longer_metric | a\|b  |"

    frame_table = _frame_table(pd.DataFrame({"metric": ["full_s"], "mean": [1.9996865625]}), max_rows=1)
    assert "| metric |    mean |" in frame_table
    assert "| ------ | ------: |" in frame_table
    assert "| full_s | 1.99969 |" in frame_table


def test_mapping_table_expands_dicts_without_json_blob() -> None:
    table = _mapping_table({"b": 2, "a": 1}, "item", "rows")

    assert '"a"' not in table
    assert "{'" not in table
    assert "| ---- | ---: |" in table
    assert "| a    |    1 |" in table


def test_compact_stats_for_report_keeps_csv_stats_exhaustive() -> None:
    pcsw = pd.DataFrame({"full_s": [2.0, 2.0, 2.1], "full_resid_us": [0.0, 10.0, 20.0]})
    stats = build_statistics_table(pcsw, pd.DataFrame({"valid_pps_interval": [False, False, False]}))
    compact = compact_stats_for_report(stats)

    assert list(stats.columns) == [
        "metric",
        "n",
        "mean",
        "median",
        "std",
        "MAD",
        "robust_sigma",
        "min",
        "max",
        "IQR",
        "p01",
        "p05",
        "p25",
        "p75",
        "p95",
        "p99",
        "unique_values",
        "top_values",
        "nonzero_fraction",
        "spread_note",
    ]
    assert {"metric", "n", "median", "robust_spread", "p05", "p95", "note"}.issubset(compact.columns)
    assert "mean" not in compact.columns


def pcps_health_frame(rows: int = 64) -> pd.DataFrame:
    seq = np.arange(rows)
    return pd.DataFrame(
        {
            "seq": seq,
            "edge_tcb0": seq * 16_000_000,
            "pps_t_s": seq.astype(float),
            "pps_t_hr": seq.astype(float) / 3600.0,
            "gps_status": 2,
            "holdover_age_ms": 0,
            "drop_pps": 0,
            "r_ppm": np.linspace(0.1, 0.2, rows),
            "j_ticks": np.arange(rows) % 4,
            "en": np.zeros(rows),
            "ef": np.ones(rows),
            "es": np.arange(rows) % 3,
            "eh": np.sin(np.arange(rows) / 5.0) * 1e-8,
            "latency16": np.full(rows, 97),
            "cap16": (seq * 4096) % 65536,
        }
    )


def test_pcps_timebase_health_with_expected_columns() -> None:
    result = analyze_pcps_timebase_health(pcps_health_frame(), AnalysisConfig(min_allan_diffs=4))

    assert has_column(result.timeseries, "r_ppm")
    assert first_available_column(result.timeseries, ["missing", "j_ticks"]) == "j_ticks"
    assert {"gps_lock", "oscillator_correction", "pps_jitter_ticks", "timebase_allan"}.issubset(set(result.summary["section"]))
    assert not result.allan.empty
    assert set(["tau_s", "m", "adev_fractional", "n_diffs", "source_column"]).issubset(result.allan.columns)


def test_pcps_timebase_health_missing_optional_columns_warns_without_crashing() -> None:
    frame = pcps_health_frame().drop(columns=["r_ppm", "j_ticks", "en", "ef", "es", "eh"])
    result = analyze_pcps_timebase_health(frame, AnalysisConfig(min_allan_diffs=4))

    assert result.summary["section"].eq("gps_lock").any()
    assert any("missing column for pps_jitter_ticks" in warning for warning in result.warnings)


def test_pcps_timebase_health_warns_on_no_gps_lock_and_holdover() -> None:
    frame = pcps_health_frame()
    frame["gps_status"] = 1
    frame.loc[10:20, "holdover_age_ms"] = 5000
    result = analyze_pcps_timebase_health(frame, AnalysisConfig(min_allan_diffs=4))

    assert any("no GPS lock" in warning for warning in result.warnings)
    assert any("holdover interval present" in warning for warning in result.warnings)
    holdover = result.summary.loc[(result.summary["section"] == "gps_lock") & (result.summary["metric"] == "holdover_rows")]
    assert int(holdover.iloc[0]["value"]) == 11


def test_pcps_timebase_health_quantized_residuals_get_discrete_summary() -> None:
    frame = pcps_health_frame()
    frame["eh"] = [0.0] * 60 + [1.0, -1.0, 0.0, 0.0]
    result = analyze_pcps_timebase_health(frame, AnalysisConfig(min_allan_diffs=4))

    rows = result.summary.loc[(result.summary["section"] == "pps_residuals") & (result.summary["metric"] == "eh")]
    assert "unique_values" in set(rows["stat"])
    assert "top_values" in set(rows["stat"])


def test_pcps_timebase_health_cap16_coverage_summary() -> None:
    result = analyze_pcps_timebase_health(pcps_health_frame(), AnalysisConfig(min_allan_diffs=4))
    cap = result.cap16_summary.iloc[0]

    assert cap["unique_cap16_count"] > 0
    assert cap["bin_count"] == 256
    assert not result.cap16_bins.empty


def test_cap16_uniform_clustered_gap_and_insufficient_assessments() -> None:
    config = AnalysisConfig(cap16_bins=16, cap16_min_rows_per_bin=2)
    uniform = pd.DataFrame({"cap16": np.tile(np.arange(0, 65536, 256), 2)})
    clustered = pd.DataFrame({"cap16": np.repeat(np.arange(100, 132), 4)})
    insufficient = pd.DataFrame({"cap16": [1, 2, 3]})

    uniform_summary, uniform_bins = analyze_cap16_phase_coverage(uniform, config)
    clustered_summary, _ = analyze_cap16_phase_coverage(clustered, config)
    insufficient_summary, _ = analyze_cap16_phase_coverage(insufficient, config)

    assert uniform_summary.iloc[0]["uniformity_assessment"] == "PASS"
    assert len(uniform_bins) == 16
    assert clustered_summary.iloc[0]["uniformity_assessment"] in {"WARN", "FAIL"}
    assert insufficient_summary.iloc[0]["uniformity_assessment"] == "INSUFFICIENT_DATA"


def test_largest_empty_cap16_gap_handles_wraparound() -> None:
    assert largest_empty_cap16_gap(pd.Series([65534, 65535, 0, 1])) == 65532
    assert largest_empty_cap16_gap(pd.Series([10])) == 65535


def test_latency16_detects_quantized_extreme_outlier_and_context() -> None:
    frame = pcps_health_frame(32)
    frame["latency16"] = [97] * 31 + [1254]
    frame["gps_status"] = 2
    frame["drop_pps"] = 0
    summary, outliers = analyze_latency16(frame, AnalysisConfig(latency_min_rows=10))

    row = summary.iloc[0]
    assert row["latency_assessment"] == "WARN"
    assert row["outlier_count"] == 1
    assert row["unique_value_count"] == 2
    assert not outliers.empty
    assert bool(outliers.iloc[0]["isolated"]) is True
    assert "gps_status" in outliers.columns


def test_latency16_no_outliers_and_insufficient_rows() -> None:
    ok_summary, ok_outliers = analyze_latency16(pd.DataFrame({"latency16": [97] * 20}), AnalysisConfig(latency_min_rows=10))
    short_summary, _ = analyze_latency16(pd.DataFrame({"latency16": [97, 98]}), AnalysisConfig(latency_min_rows=10))

    assert ok_summary.iloc[0]["latency_assessment"] == "PASS"
    assert ok_outliers.empty
    assert short_summary.iloc[0]["latency_assessment"] == "INSUFFICIENT_DATA"


def test_pcps_timebase_allan_uses_corrected_overlapping_implementation() -> None:
    frame = pcps_health_frame(80)
    frame["eh"] = np.repeat(np.arange(40, dtype=float) * 4.0, 2)
    result = analyze_pcps_timebase_health(frame, AnalysisConfig(min_allan_diffs=1))

    m2 = result.allan.loc[result.allan["m"] == 2].iloc[0]
    assert m2["source_column"] == "eh"
    assert m2["adev_fractional"] == np.sqrt(8.0)
    assert m2["adev_fractional"] != np.sqrt(2.0)


def test_sequence_diagnostics_counts_gaps_and_nonpositive_jumps() -> None:
    diag = sequence_diagnostics(pd.Series([1, 2, 5, 5, 4, 8]))
    assert diag["non_unit_jumps"] == 4
    assert diag["total_missing_when_positive"] == 5
    assert diag["max_positive_jump"] == 4
    assert diag["nonpositive_jumps"] == 2


def test_v2_sampled_export_headers_and_filter_counts(tmp_path: Path) -> None:
    run_dir = write_canonical_run(tmp_path / "run")
    config = AnalysisConfig(pps_residual_window=3, clock_profile="synchronome")
    loaded = load_csv(run_dir / "PCSW.CSV")
    data = add_derived_fields(loaded.data, config)
    data, _ = add_filter_flags(data, config)
    pcsw = prepare_pcsw_frame(data, config)
    pcps = prepare_pcps_frame(analyze_pcps(run_dir / "PCPS.CSV", config).intervals, config)

    for column in ["open_A_s", "full_s", "t_s", "t_hr", "full_resid_us", "full_ppm_vs_inferred"]:
        assert column in pcsw.columns
    for column in ["pps_raw_error_cycles", "pps_raw_error_ns", "valid_pps_interval", "expected_error_cycles_prev31"]:
        assert column in pcps.columns
    assert int(pcps["valid_pps_interval"].sum()) == 4


def test_bundle_contains_phase_moduli_and_nominal_source(tmp_path: Path) -> None:
    run_dir = write_canonical_run(tmp_path / "run")
    config = AnalysisConfig(pps_residual_window=3, clock_profile="synchronome")
    loaded = load_csv(run_dir / "PCSW.CSV")
    data = add_derived_fields(loaded.data, config)
    data, _ = add_filter_flags(data, config)
    data = add_phase_index(data, config)
    bundle = build_bundle(data, analyze_pcps(run_dir / "PCPS.CSV", config), compute_allan(data, config), {
        "pcsw": run_dir / "PCSW.CSV",
        "pcps": run_dir / "PCPS.CSV",
        "sts": run_dir / "STS.CSV",
        "uno": run_dir / "UNO.CSV",
        "cfg": None,
        "hdr": None,
    }, config)

    assert set(bundle.phase_fold["modulus"]) == {15, 30}
    assert bundle.quality["nominal_source"] == "STS cfg nhz=16000000"
    assert bundle.quality["PCPS_filter_counts"]["valid_filtered_intervals"] == 4


def test_report_polish_helpers_handle_bundle_and_missing_metadata(tmp_path: Path) -> None:
    run_dir = write_canonical_run(tmp_path / "run")
    config = AnalysisConfig(pps_residual_window=3, min_allan_diffs=1)
    loaded = load_csv(run_dir / "PCSW.CSV")
    data = add_derived_fields(loaded.data, config)
    data, _ = add_filter_flags(data, config)
    data = add_phase_index(data, config)
    bundle = build_bundle(
        data,
        analyze_pcps(run_dir / "PCPS.CSV", config),
        compute_allan(data, config),
        {
            "pcsw": run_dir / "PCSW.CSV",
            "pcps": run_dir / "PCPS.CSV",
            "sts": None,
            "uno": None,
            "cfg": None,
            "hdr": None,
        },
        config,
    )

    sanity = build_sanity_checks(bundle)
    scorecard = build_health_scorecard(bundle)
    relationships = build_relationship_summary(bundle)
    provenance = build_report_provenance(bundle, "pendulum-analysis run", str(run_dir))

    assert {"check", "status", "detail"}.issubset(sanity.columns)
    assert set(scorecard["dimension"]) >= {"mean period", "repeatability", "timebase/GPS"}
    assert {"relationship", "method", "r_or_effect", "n", "note"}.issubset(relationships.columns)
    assert "configuration_hash" in set(provenance["item"])
    assert provenance.loc[provenance["item"] == "input_PCSW_sha256", "value"].iloc[0] != "n/a"


def test_cli_smoke_writes_canonical_v2_outputs(tmp_path: Path) -> None:
    input_path = Path("examples/synthetic/PCSW.CSV")
    outdir = tmp_path / "analysis"
    exit_code = main([str(input_path), "--out", str(outdir)])
    assert exit_code == 0
    assert (outdir / "report" / "canonical_report.md").exists()
    assert (outdir / "report" / "manifest.json").exists()
    assert (outdir / "combined" / "summary" / "01_data_quality_summary_v2.json").exists()
    assert (outdir / "combined" / "csv" / "01_statistics_summary_v2.csv").exists()
    assert (outdir / "pcsw" / "csv" / "02_phase_fold_summary_v2.csv").exists()
    assert (outdir / "pcsw" / "csv" / "01_derived_intervals_sampled_v2.csv").exists()
    assert (outdir / "pcsw" / "plots" / "01_full_cycle_timing_overview.png").exists()
    assert (outdir / "metadata" / "config_resolved.yaml").exists()
    assert (outdir / "metadata" / "provenance.json").exists()
    assert (outdir / "metadata" / "hashes.json").exists()
    assert (outdir / "metadata" / "analysis_lineage.csv").exists()
    assert (outdir / "metadata" / "analysis_lineage.md").exists()
    assert (outdir / "metadata" / "mask_audit.csv").exists()
    assert (outdir / "metadata" / "mask_audit.md").exists()
    lineage = pd.read_csv(outdir / "metadata" / "analysis_lineage.csv")
    assert {"output_filename", "mask_used", "sampling_method", "robust_outliers_excluded"}.issubset(lineage.columns)
    assert (lineage["mask_used"] == "structure_diagnostic_mask").any()
    mask_audit = pd.read_csv(outdir / "metadata" / "mask_audit.csv")
    assert "analysis_mask_rows" in set(mask_audit["mask_or_count"])
    manifest = json.loads((outdir / "report" / "manifest.json").read_text(encoding="utf-8"))
    manifest_paths = {artifact["relative_path"] for artifact in manifest["artifacts"]}
    assert "report/canonical_report.md" in manifest_paths
    assert "metadata/config_resolved.yaml" in manifest_paths
    assert "metadata/provenance.json" in manifest_paths
    assert "metadata/hashes.json" in manifest_paths
    assert "pcsw/plots/01_full_cycle_timing_overview.png" in manifest_paths
    assert not any(path.startswith("pcps/") for path in manifest_paths)
    assert not (outdir / "CANONICAL_TIMING_ANALYSIS_REPORT_V2_PCPS_INTEGRATED.md").exists()
    assert not (outdir / "statistics_summary_v2.csv").exists()
    assert not (outdir / "plots").exists()
    assert not (outdir / "figures").exists()
    assert not (outdir / "summary.md").exists()
    assert not (outdir / "summary.json").exists()


def test_cli_directory_smoke_writes_report_and_v2_outputs(tmp_path: Path) -> None:
    run_dir = write_canonical_run(tmp_path / "run")
    outdir = tmp_path / "analysis"
    exit_code = main([str(run_dir), "--out", str(outdir)])
    assert exit_code == 0
    assert (outdir / "report" / "manifest.json").exists()
    assert (outdir / "metadata" / "warnings.txt").exists()
    assert (outdir / "report" / "canonical_report.md").exists()
    assert (outdir / "pcps" / "report.html").exists()
    assert (outdir / "pcps" / "report.md").exists()
    assert (outdir / "pcps" / "summary.json").exists()
    assert (outdir / "pcps" / "plots").is_dir()
    assert (outdir / "pcps" / "csv").is_dir()
    assert not (outdir / "pcps" / "csv" / "01_derived_pps_sampled_v2.csv").exists()
    assert (outdir / "pcsw" / "plots" / "09_allan_deviation.png").exists()
    assert not (outdir / "plots").exists()
    assert not (outdir / "summary_tables").exists()

    quality = json.loads((outdir / "combined" / "summary" / "01_data_quality_summary_v2.json").read_text(encoding="utf-8"))
    assert quality["nominal_source"] == "STS cfg nhz=16000000"
    report_path = outdir / "report" / "canonical_report.md"
    report = report_path.read_text(encoding="utf-8")
    assert "## Executive summary" in report
    assert "## PPS clock analysis" in report
    assert "[PPS clock analysis report](../pcps/report.html)" in report
    assert "authoritative for PPS" in report
    assert "## Dedicated PCPS/CPS clock-quality analysis" not in report
    assert "## Timebase Health / PCPS Diagnostics" not in report
    assert "## PCPS cap16 phase coverage sanity check" not in report
    assert "## PCPS latency16 capture/ISR service diagnostic" not in report
    assert "Figure PCSW-01" in report
    assert "![Figure PCSW-01: Full-cycle timing overview](../pcsw/plots/01_full_cycle_timing_overview.png)" in report
    assert "(pcps_lock_state_timeline.png)" not in report
    for title in PCPS_HEALTH_FIGURES:
        assert title not in report
    assert "## Open questions / caveats" in report
    for image_path in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", report):
        assert (report_path.parent / image_path).resolve().exists()
    assert not re.search(r"\]\([0-9]{2}_[^)]+\.png\)", report)


def test_artifact_registry_numbering_paths_manifest_and_duplicates(tmp_path: Path) -> None:
    registry = ArtifactRegistry(tmp_path)
    pcps = registry.register_plot(namespace="pcps", slug="lock state", title="Lock state", section="PCPS", source_files=["PCPS.csv"])
    pcsw = registry.register_plot(namespace="pcsw", slug="allan_deviation", title="Allan deviation", section="Long-term stability", source_files=["PCSW.csv"])
    combined = registry.register_plot(namespace="combined", slug="environment relationships", title="Environment relationships", section="Environment", source_files=["PCSW.csv", "PCPS.csv"])
    csv_artifact = registry.register_csv(namespace="pcsw", slug="sampled intervals", title="Sampled intervals", section="PCSW", source_files=["PCSW.csv"])

    assert pcps.id == "PCPS-01"
    assert pcsw.id == "PCSW-01"
    assert combined.id == "COMB-01"
    assert csv_artifact.id == "PCSW-CSV-01"
    assert pcps.relative_path == "pcps/plots/01_lock_state.png"
    assert pcsw.relative_path == "pcsw/plots/01_allan_deviation.png"
    assert combined.relative_path == "combined/plots/01_environment_relationships.png"
    assert csv_artifact.relative_path == "pcsw/csv/01_sampled_intervals.csv"
    assert [item["id"] for item in registry.to_manifest()["artifacts"]] == ["PCPS-01", "PCSW-01", "COMB-01", "PCSW-CSV-01"]

    try:
        registry.register_plot(namespace="pcsw", slug="allan deviation", title="Duplicate", section="Long-term stability")
    except ValueError as exc:
        assert "Duplicate artifact registration" in str(exc)
    else:
        raise AssertionError("duplicate namespace/type/slug should fail")


def test_cli_writes_only_hierarchical_outputs(tmp_path: Path) -> None:
    outdir = tmp_path / "analysis"
    exit_code = main(["examples/synthetic/PCSW.CSV", "--out", str(outdir)])
    assert exit_code == 0
    assert (outdir / "report" / "canonical_report.md").exists()
    assert (outdir / "combined" / "csv" / "01_statistics_summary_v2.csv").exists()
    assert (outdir / "pcsw" / "plots" / "01_full_cycle_timing_overview.png").exists()
    assert not (outdir / "CANONICAL_TIMING_ANALYSIS_REPORT_V2_PCPS_INTEGRATED.md").exists()
    assert not (outdir / "statistics_summary_v2.csv").exists()
    assert not (outdir / "01_full_cycle_timing_overview.png").exists()


def synthetic_long_pcsw(days: int = 30, period_s: float = 2.0) -> pd.DataFrame:
    rows = int(days * 24 * 60 * 60 / period_s)
    seq = np.arange(rows)
    t_s = seq * period_s
    residual = 25.0 * np.sin(2 * np.pi * t_s / 86400.0) + 0.01 * seq / rows
    return pd.DataFrame(
        {
            "seq": seq,
            "t_s": t_s,
            "t_hr": t_s / 3600.0,
            "full_s": period_s + residual * 1e-6,
            "full_resid_us": residual,
            "full_ppm_vs_inferred": residual / period_s,
            "half_A_s": period_s / 2,
            "half_B_s": period_s / 2,
            "half_asymmetry_ms": 0.0,
            "temp_C": 20.0 + np.sin(2 * np.pi * t_s / 86400.0),
            FLAGS.is_clean_robust: True,
        }
    )


def test_clock_profiles_and_capabilities_generic_vs_synchronome() -> None:
    pcsw = synthetic_long_pcsw(days=1)
    pcps = pd.DataFrame({"pps_t_s": [0.0, 1.0], "valid_pps_interval": [True, True]})

    generic_selection = select_clock_profile("generic", pcsw)
    generic_caps = derive_capabilities(generic_selection.selected, pcsw, pcps, 1.0)
    sync_selection = select_clock_profile("synchronome", pcsw)
    sync_caps = derive_capabilities(sync_selection.selected, pcsw, pcps, 1.0)

    assert generic_selection.selected.name == "generic"
    assert np.isclose(generic_selection.inferred_nominal_period_s, 2.0)
    assert generic_caps.supports_tick_tock_analysis is False
    assert generic_caps.supports_impulse_cycle_interpretation is False
    assert sync_caps.supports_phase_fold_analysis is True
    assert sync_selection.selected.expected_phase_moduli == [15, 30]


def test_auto_profile_infers_without_asserting_clock_family() -> None:
    pcsw = synthetic_long_pcsw(days=1)
    pcsw["full_resid_us"] = np.sin(2 * np.pi * pcsw["seq"] / 15.0)

    selection = select_clock_profile("auto", pcsw)

    assert selection.selected.name == "generic"
    assert np.isclose(selection.inferred_nominal_period_s, 2.0)
    assert "generic analysis" in selection.reason
    assert "Synchronome" not in selection.reason


def test_yaml_profiles_and_layered_config_are_resolved(tmp_path: Path) -> None:
    profile = load_profile("synchronome")
    assert profile.source_path and profile.source_path.endswith("synchronome.yaml")
    assert profile.expected_phase_moduli == [15, 30]
    assert any(period.order == 7 for period in profile.expected_structure_periods_s)

    project_config = tmp_path / "analysis.yaml"
    project_config.write_text("analysis:\n  clock_profile: generic\n  max_points_per_plot: 123\n", encoding="utf-8")
    resolved = load_config_layers(project_config_path=project_config, cli_overrides={"max_points_per_plot": 456})

    assert resolved.clock_profile == "generic"
    assert resolved.max_points_per_plot == 456
    assert resolved.project_config == str(project_config)
    assert len(resolved.config_hash()) == 64


def test_semantic_columns_and_module_registry_use_roles() -> None:
    frame = pd.read_csv("tests/golden/perfect_clock.csv")
    roles = build_column_map(frame)
    modules = module_registry()

    assert roles.require("period") == "full_s"
    assert roles.require("residual") == "full_resid_us"
    assert roles.require("temperature") == "temp_C"
    assert modules["fft_spectrum"].required_roles == ["residual"]
    assert "supports_phase_fold_analysis" in modules["phase_fold"].required_capabilities


def test_golden_synthetic_datasets_cover_named_scenarios() -> None:
    golden = Path("tests/golden")
    expected = {
        "perfect_clock.csv",
        "tick_tock_asymmetry.csv",
        "phase15_impulse.csv",
        "temperature_lag.csv",
        "pps_latency_spike.csv",
        "missing_phases.csv",
    }
    assert expected == {path.name for path in golden.glob("*.csv")}

    perfect = pd.read_csv(golden / "perfect_clock.csv")
    assert perfect["full_resid_us"].abs().sum() == 0

    asymmetry = pd.read_csv(golden / "tick_tock_asymmetry.csv")
    assert (asymmetry["half_A_s"] > asymmetry["half_B_s"]).all()

    impulse = pd.read_csv(golden / "phase15_impulse.csv")
    assert set(impulse.loc[impulse["full_resid_us"] > 0, "seq"] % 15) == {0}

    latency = pd.read_csv(golden / "pps_latency_spike.csv")
    assert latency["latency16"].max() > latency["latency16"].median() * 10

    missing = pd.read_csv(golden / "missing_phases.csv")
    assert 12 not in set(missing["seq"] % 15)


def test_planner_duration_thresholds_and_overrides() -> None:
    config = AnalysisConfig(clock_profile="generic")
    pcps = pd.DataFrame()
    durations = [0.5, 2.0, 8.0, 30.0]
    expected = [
        ("SKIP", "SKIP", "SKIP"),
        ("RUN", "SKIP", "SKIP"),
        ("RUN", "RUN", "SKIP"),
        ("RUN", "RUN", "RUN"),
    ]
    for days, (standard, medium, long_run) in zip(durations, expected):
        pcsw = synthetic_long_pcsw(days=1)
        quality = {"rows": {"PCSW": len(pcsw)}, "duration_hours": {"PCSW": days * 24.0, "PCPS": 0.0}}
        metadata = build_dataset_metadata(pcsw, pcps, quality)
        selection = select_clock_profile("generic", pcsw)
        caps = derive_capabilities(selection.selected, pcsw, pcps, days)
        plan = build_analysis_plan(metadata, selection.selected, caps, config, "generic")

        assert plan.status("allan_deviation") == standard
        assert plan.status("time_of_day_fold") == medium
        assert plan.status("rolling_stability") == long_run

    force_config = AnalysisConfig(clock_profile="generic", enabled_analyses=("time_of_day_fold",), force_long_run=True)
    quality = {"rows": {"PCSW": 10}, "duration_hours": {"PCSW": 0.5 * 24.0, "PCPS": 0.0}}
    pcsw = synthetic_long_pcsw(days=1)
    metadata = build_dataset_metadata(pcsw, pcps, quality)
    selection = select_clock_profile("generic", pcsw)
    caps = derive_capabilities(selection.selected, pcsw, pcps, 0.5)
    plan = build_analysis_plan(metadata, selection.selected, caps, force_config, "generic")

    assert plan.status("time_of_day_fold") in {"RUN", "FORCED"}
    change_point = next(row for row in plan.analyses if row.name == "change_point_detection")
    if importlib.util.find_spec("ruptures") is None:
        assert change_point.status == "SKIP"
        assert "optional dependency" in change_point.reason
    else:
        assert change_point.status == "RUN"


def test_report_sections_and_generic_output_avoid_synchronome_wording(tmp_path: Path) -> None:
    input_path = Path("examples/synthetic/PCSW.CSV")
    outdir = tmp_path / "generic"
    exit_code = main([str(input_path), "--out", str(outdir), "--clock-profile", "generic"])

    assert exit_code == 0
    report = (outdir / "report" / "canonical_report.md").read_text(encoding="utf-8")
    assert "## Clock profile and capabilities" in report
    assert "## Analysis plan" in report
    assert "selected profile" in report
    assert "generic" in report
    assert "Synchronome" not in report
    assert "Skipped - no configured or detected cycle model" in report
    assert (outdir / "combined" / "csv" / "03_analysis_plan_v2.csv").exists()


def test_long_run_modules_enabled_on_synthetic_30_day_data(tmp_path: Path) -> None:
    config = AnalysisConfig(clock_profile="generic", analysis_profile="long", min_allan_diffs=2)
    pcsw = synthetic_long_pcsw(days=30, period_s=60.0)
    pcps = pd.DataFrame()
    pcps_analysis = analyze_pcps(None, config)
    bundle = build_bundle(
        pcsw,
        pcps_analysis,
        pd.DataFrame(columns=ALLAN_COLUMNS),
        {"pcsw": None, "pcps": None, "sts": None, "uno": None, "cfg": None, "hdr": None},
        config,
    )

    assert bundle.analysis_plan.status("time_of_day_fold") == "RUN"
    assert bundle.analysis_plan.status("daily_summary") == "RUN"
    assert bundle.analysis_plan.status("rolling_stability") == "RUN"
    assert not bundle.time_of_day_fold.empty
    assert not bundle.daily_summary.empty
    assert not bundle.rolling_stability.empty
