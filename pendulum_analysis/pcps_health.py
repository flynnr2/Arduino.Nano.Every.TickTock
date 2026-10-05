"""PCPS timebase health diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .allan import overlapping_allan_deviation
from .config import AnalysisConfig
from .schema import CANONICAL_PPS
from .stats import describe_series, iqr, mad


@dataclass(frozen=True)
class PcpsHealthResult:
    summary: pd.DataFrame
    timeseries: pd.DataFrame
    allan: pd.DataFrame
    cap16_summary: pd.DataFrame
    cap16_bins: pd.DataFrame
    latency_summary: pd.DataFrame
    latency_outliers: pd.DataFrame
    warnings: List[str]


def has_column(df: pd.DataFrame, name: str) -> bool:
    return name in df.columns


def first_available_column(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    for candidate in candidates:
        if has_column(df, candidate):
            return candidate
    return None


def analyze_pcps_timebase_health(pcps: pd.DataFrame, config: AnalysisConfig) -> PcpsHealthResult:
    warnings: List[str] = []
    summary_rows: List[Dict[str, Any]] = []
    if pcps.empty:
        return PcpsHealthResult(
            pd.DataFrame(columns=["section", "metric", "stat", "value", "status", "note"]),
            pd.DataFrame(),
            _empty_allan(),
            _empty_cap16_summary(),
            _empty_cap16_bins(),
            _empty_latency_summary(),
            _empty_latency_outliers(),
            ["No PCPS/CPS rows were available for timebase health diagnostics"],
        )

    ts = _build_timeseries(pcps, config)
    _summarize_lock_state(ts, summary_rows, warnings)
    _summarize_numeric(ts, first_available_column(ts, ["r_ppm", "pps_raw_error_ppm", "offline_pps_adjusted_residual_ppm"]), "oscillator_correction", summary_rows, warnings)
    _summarize_numeric(ts, first_available_column(ts, ["j_ticks"]), "pps_jitter_ticks", summary_rows, warnings)
    _summarize_residuals(ts, summary_rows, warnings)
    latency_summary, latency_outliers = analyze_latency16(ts, config)
    _append_latency_summary_rows(latency_summary, summary_rows, warnings)
    _summarize_drops(ts, summary_rows, warnings)
    cap16_summary, cap16_bins = analyze_cap16_phase_coverage(ts, config)
    _append_cap16_summary_rows(cap16_summary, summary_rows, warnings)
    allan = _timebase_allan(ts, config, summary_rows, warnings)
    return PcpsHealthResult(pd.DataFrame(summary_rows), ts, allan, cap16_summary, cap16_bins, latency_summary, latency_outliers, warnings)


def analyze_cap16_phase_coverage(pcps: pd.DataFrame, config: AnalysisConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    columns = [
        "n",
        "unique_cap16_count",
        "unique_cap16_fraction",
        "observed_min",
        "observed_max",
        "largest_empty_gap_ticks",
        "largest_empty_gap_fraction",
        "bin_count",
        "expected_count_per_bin",
        "max_bin_count",
        "min_bin_count",
        "max_bin_over_expected",
        "min_bin_over_expected",
        "chi_square_statistic",
        "chi_square_p_value",
        "uniformity_assessment",
        "assessment_reason",
    ]
    if CANONICAL_PPS.cap16 not in pcps.columns:
        return pd.DataFrame([{column: np.nan for column in columns} | {"uniformity_assessment": "INSUFFICIENT_DATA", "assessment_reason": "missing cap16 column"}], columns=columns), _empty_cap16_bins()

    cap = pd.to_numeric(pcps[CANONICAL_PPS.cap16], errors="coerce")
    cap = cap[np.isfinite(cap)].round().astype(int)
    cap = cap[(cap >= 0) & (cap <= 65535)]
    n = int(len(cap))
    bin_count = int(config.cap16_bins)
    hist, edges = np.histogram(cap.to_numpy(dtype=float), bins=bin_count, range=(0, 65536)) if n else (np.zeros(bin_count, dtype=int), np.linspace(0, 65536, bin_count + 1))
    expected = float(n / bin_count) if bin_count else np.nan
    unique_count = int(cap.nunique()) if n else 0
    largest_gap = largest_empty_cap16_gap(cap)
    chi2 = float((((hist - expected) ** 2) / expected).sum()) if expected > 0 else np.nan
    p_value = _chi_square_p_value(chi2, bin_count - 1) if np.isfinite(chi2) and bin_count > 1 else np.nan
    max_bin = int(hist.max()) if len(hist) else 0
    min_bin = int(hist.min()) if len(hist) else 0
    max_ratio = float(max_bin / expected) if expected > 0 else np.nan
    min_ratio = float(min_bin / expected) if expected > 0 else np.nan
    assessment, reason = _assess_cap16(n, largest_gap / 65536.0 if n else np.nan, max_ratio, config)

    summary = pd.DataFrame(
        [
            {
                "n": n,
                "unique_cap16_count": unique_count,
                "unique_cap16_fraction": float(unique_count / 65536.0),
                "observed_min": int(cap.min()) if n else np.nan,
                "observed_max": int(cap.max()) if n else np.nan,
                "largest_empty_gap_ticks": int(largest_gap) if n else np.nan,
                "largest_empty_gap_fraction": float(largest_gap / 65536.0) if n else np.nan,
                "bin_count": bin_count,
                "expected_count_per_bin": expected,
                "max_bin_count": max_bin,
                "min_bin_count": min_bin,
                "max_bin_over_expected": max_ratio,
                "min_bin_over_expected": min_ratio,
                "chi_square_statistic": chi2,
                "chi_square_p_value": p_value,
                "uniformity_assessment": assessment,
                "assessment_reason": reason,
            }
        ],
        columns=columns,
    )
    bins = pd.DataFrame(
        {
            "bin_index": np.arange(bin_count, dtype=int),
            "bin_start": edges[:-1].astype(int),
            "bin_end_exclusive": edges[1:].astype(int),
            "count": hist.astype(int),
            "expected_count": expected,
            "count_over_expected": hist / expected if expected > 0 else np.nan,
        }
    )
    return summary, bins


def analyze_latency16(pcps: pd.DataFrame, config: AnalysisConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    column = first_available_column(pcps, [CANONICAL_PPS.latency16, "latency_cycles"])
    if column is None:
        return _latency_summary_row("INSUFFICIENT_DATA", "missing latency16/latency_cycles column"), _empty_latency_outliers()

    numeric = pd.to_numeric(pcps[column], errors="coerce")
    finite = numeric[np.isfinite(numeric)]
    n = int(len(finite))
    if n == 0:
        return _latency_summary_row("INSUFFICIENT_DATA", "no finite latency values"), _empty_latency_outliers()

    median = float(finite.median())
    p95 = float(finite.quantile(0.95))
    p99 = float(finite.quantile(0.99))
    minimum = float(finite.min())
    maximum = float(finite.max())
    robust_sigma = 1.4826 * mad(finite)
    threshold = _latency_outlier_threshold(median, p99, robust_sigma, config)
    outlier_mask = numeric > threshold
    outlier_count = int(outlier_mask.fillna(False).sum())
    outlier_fraction = float(outlier_count / n) if n else np.nan
    runs = _boolean_run_lengths(outlier_mask.fillna(False).to_numpy(dtype=bool))
    assessment, reason = _assess_latency(n, outlier_count, outlier_fraction, config)
    summary = pd.DataFrame(
        [
            {
                "n": n,
                "median_cycles": median,
                "p95_cycles": p95,
                "p99_cycles": p99,
                "max_cycles": maximum,
                "min_cycles": minimum,
                "robust_sigma_cycles": robust_sigma,
                "unique_value_count": int(finite.nunique()),
                "top_value_counts": _top_value_counts(finite),
                "outlier_threshold_cycles": threshold,
                "outlier_count": outlier_count,
                "outlier_fraction": outlier_fraction,
                "max_outlier_cycles": float(numeric.loc[outlier_mask].max()) if outlier_count else np.nan,
                "max_outlier_elapsed_h": _max_outlier_elapsed_h(pcps, outlier_mask),
                "consecutive_outlier_runs": int(len(runs)),
                "longest_outlier_run": int(max(runs)) if runs else 0,
                "latency_assessment": assessment,
                "assessment_reason": reason,
            }
        ],
        columns=list(_empty_latency_summary().columns),
    )
    outliers = _latency_outlier_context(pcps, column, numeric, outlier_mask, threshold, median, p99)
    return summary, outliers


def largest_empty_cap16_gap(values: pd.Series) -> int:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[np.isfinite(numeric)].round().astype(int)
    numeric = np.sort(np.unique(numeric[(numeric >= 0) & (numeric <= 65535)]))
    if len(numeric) == 0:
        return 65536
    if len(numeric) == 1:
        return 65535
    forward = np.diff(numeric) - 1
    wrap = int(numeric[0] + 65536 - numeric[-1] - 1)
    return int(max(wrap, int(forward.max(initial=0))))


def _build_timeseries(pcps: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    ts = pcps.copy()
    if "pps_t_s" not in ts.columns:
        if CANONICAL_PPS.edge_tcb0 in ts.columns:
            edge = pd.to_numeric(ts[CANONICAL_PPS.edge_tcb0], errors="coerce")
            first = edge.dropna().iloc[0] if edge.notna().any() else 0.0
            ts["pps_t_s"] = (edge - first) / config.nominal_hz
        else:
            ts["pps_t_s"] = np.arange(len(ts), dtype=float)
    ts["pps_t_hr"] = pd.to_numeric(ts.get("pps_t_hr", ts["pps_t_s"] / 3600.0), errors="coerce")
    valid_column = first_available_column(ts, ["is_valid_pps_interval", "valid_pps_interval"])
    if valid_column is not None:
        valid = ts[valid_column].astype(bool)
        for column in [
            "pps_raw_error_cycles",
            "pps_raw_error_ns",
            "pps_raw_error_ppm",
            "offline_pps_adjusted_residual_cycles",
            "offline_pps_adjusted_residual_ns",
            "offline_pps_adjusted_residual_ppm",
        ]:
            if column in ts.columns:
                ts.loc[~valid, column] = np.nan
    return ts


def _summarize_lock_state(ts: pd.DataFrame, rows: List[Dict[str, Any]], warnings: List[str]) -> None:
    if CANONICAL_PPS.gps_status not in ts.columns:
        warnings.append("PCPS timebase health: missing gps_status; lock-state timeline skipped")
        _row(rows, "gps_lock", "gps_status", "available", False, "missing gps_status")
        return
    status = pd.to_numeric(ts[CANONICAL_PPS.gps_status], errors="coerce")
    counts = status.value_counts(dropna=False).sort_index()
    for state, count in counts.items():
        _row(rows, "gps_lock", "gps_status_count", str(state), int(count), "")
    locked = status == 2
    _row(rows, "gps_lock", "locked_fraction", "value", float(locked.mean()) if len(locked) else np.nan, "gps_status == 2")
    transitions = int((locked.astype(int).diff().fillna(0) != 0).sum())
    _row(rows, "gps_lock", "lock_unlock_transitions", "count", transitions, "")
    if not locked.any():
        warnings.append("PCPS timebase health: no GPS lock achieved")
    holdover = pd.to_numeric(ts.get(CANONICAL_PPS.holdover_age_ms), errors="coerce") if CANONICAL_PPS.holdover_age_ms in ts.columns else pd.Series(dtype=float)
    if not holdover.empty:
        active = holdover.fillna(0) > 0
        _row(rows, "gps_lock", "holdover_rows", "count", int(active.sum()), "holdover_age_ms > 0")
        _row(rows, "gps_lock", "max_holdover_age_ms", "max", float(holdover.max()) if holdover.notna().any() else np.nan, "")
        if active.any():
            warnings.append("PCPS timebase health: holdover interval present")
    else:
        warnings.append("PCPS timebase health: missing holdover_age_ms; holdover summary skipped")


def _summarize_numeric(
    ts: pd.DataFrame,
    column: Optional[str],
    metric: str,
    rows: List[Dict[str, Any]],
    warnings: List[str],
) -> None:
    if column is None:
        warnings.append(f"PCPS timebase health: missing column for {metric}")
        _row(rows, metric, "available", "value", False, "missing optional column")
        return
    stats = describe_series(ts[column])
    for stat in ["count", "median", "p05", "p95", "min", "max"]:
        _row(rows, metric, column, stat, stats.get(stat), "")
    _add_discrete_rows(rows, metric, column, ts[column])
    if metric == "oscillator_correction":
        p05, p95 = stats.get("p05"), stats.get("p95")
        if p05 is not None and p95 is not None:
            _row(rows, metric, column, "p95_minus_p05", float(p95) - float(p05), "drift/stability spread")
    if metric == "capture_latency_cycles" and stats.get("p95") is not None and stats.get("max") is not None:
        if float(stats["max"]) > max(float(stats["p95"]) * 4.0, float(stats["p95"]) + 100.0):
            warnings.append("PCPS timebase health: suspicious latency excursions detected")


def _summarize_residuals(ts: pd.DataFrame, rows: List[Dict[str, Any]], warnings: List[str]) -> None:
    found = False
    for column in ["en", "ef", "es", "eh", "pps_raw_error_cycles", "offline_pps_adjusted_residual_cycles", "offline_pps_adjusted_residual_ns"]:
        if column not in ts.columns:
            continue
        found = True
        stats = describe_series(ts[column])
        for stat in ["count", "median", "MAD", "IQR", "min", "max", "p01", "p50", "p99"]:
            value = stats.get("median") if stat == "p50" else stats.get(stat)
            _row(rows, "pps_residuals", column, stat, value, "")
        _add_discrete_rows(rows, "pps_residuals", column, ts[column])
    if not found:
        warnings.append("PCPS timebase health: no PPS residual columns found")


def _summarize_drops(ts: pd.DataFrame, rows: List[Dict[str, Any]], warnings: List[str]) -> None:
    if CANONICAL_PPS.drop_pps not in ts.columns:
        warnings.append("PCPS timebase health: missing drop_pps; dropped-capture summary skipped")
        return
    drops = pd.to_numeric(ts[CANONICAL_PPS.drop_pps], errors="coerce").fillna(0)
    _row(rows, "dropped_pps", "drop_pps", "sum", int(drops.sum()), "")
    _row(rows, "dropped_pps", "drop_pps", "nonzero_rows", int((drops != 0).sum()), "")


def _summarize_cap16(ts: pd.DataFrame, rows: List[Dict[str, Any]], warnings: List[str]) -> None:
    if CANONICAL_PPS.cap16 not in ts.columns:
        warnings.append("PCPS timebase health: missing cap16; phase coverage sanity check skipped")
        return
    cap = pd.to_numeric(ts[CANONICAL_PPS.cap16], errors="coerce").dropna()
    if cap.empty:
        _row(rows, "cap16_phase_coverage", "cap16", "available", False, "no finite cap16 values")
        return
    bins = np.histogram(cap.to_numpy(dtype=float), bins=16, range=(0, 65536))[0]
    expected = bins.mean() if len(bins) else np.nan
    chi2_ratio = float((((bins - expected) ** 2) / expected).sum() / max(1, len(bins) - 1)) if expected else np.nan
    _row(rows, "cap16_phase_coverage", "cap16", "unique_values", int(cap.nunique()), "sanity check, not precision metric")
    _row(rows, "cap16_phase_coverage", "cap16", "bin_count_min", int(bins.min()), "")
    _row(rows, "cap16_phase_coverage", "cap16", "bin_count_max", int(bins.max()), "")
    _row(rows, "cap16_phase_coverage", "cap16", "reduced_chi2_16_bins", chi2_ratio, "large values may indicate non-uniform coverage")
    if np.isfinite(chi2_ratio) and chi2_ratio > 10.0:
        warnings.append("PCPS timebase health: strong cap16 non-uniformity; check for aliasing, missed captures, or coupling")


def _append_cap16_summary_rows(summary: pd.DataFrame, rows: List[Dict[str, Any]], warnings: List[str]) -> None:
    if summary.empty:
        return
    row = summary.iloc[0]
    for stat in [
        "n",
        "unique_cap16_count",
        "unique_cap16_fraction",
        "observed_min",
        "observed_max",
        "largest_empty_gap_ticks",
        "largest_empty_gap_fraction",
        "expected_count_per_bin",
        "max_bin_count",
        "min_bin_count",
        "max_bin_over_expected",
        "min_bin_over_expected",
        "chi_square_statistic",
        "chi_square_p_value",
        "uniformity_assessment",
        "assessment_reason",
    ]:
        _row(rows, "cap16_phase_coverage", "cap16", stat, row.get(stat), "capture-system sanity check, not pendulum metric")
    assessment = str(row.get("uniformity_assessment", "INSUFFICIENT_DATA"))
    if assessment in {"WARN", "FAIL"}:
        warnings.append(f"PCPS timebase health: cap16 phase coverage {assessment.lower()}: {row.get('assessment_reason')}")


def _append_latency_summary_rows(summary: pd.DataFrame, rows: List[Dict[str, Any]], warnings: List[str]) -> None:
    if summary.empty:
        return
    row = summary.iloc[0]
    for stat in [
        "n",
        "median_cycles",
        "p95_cycles",
        "p99_cycles",
        "max_cycles",
        "min_cycles",
        "robust_sigma_cycles",
        "unique_value_count",
        "top_value_counts",
        "outlier_threshold_cycles",
        "outlier_count",
        "outlier_fraction",
        "max_outlier_cycles",
        "max_outlier_elapsed_h",
        "consecutive_outlier_runs",
        "longest_outlier_run",
        "latency_assessment",
        "assessment_reason",
    ]:
        _row(rows, "capture_latency_cycles", "latency16", stat, row.get(stat), "capture/ISR service latency diagnostic")
    assessment = str(row.get("latency_assessment", "INSUFFICIENT_DATA"))
    if assessment in {"WARN", "FAIL"}:
        warnings.append(f"PCPS timebase health: latency16 {assessment.lower()}: {row.get('assessment_reason')}")


def _assess_cap16(n: int, largest_gap_fraction: float, max_ratio: float, config: AnalysisConfig) -> tuple[str, str]:
    required = int(config.cap16_bins * config.cap16_min_rows_per_bin)
    if n < required:
        return "INSUFFICIENT_DATA", f"n={n} below {required} rows required for {config.cap16_bins} bins"
    if np.isfinite(largest_gap_fraction) and largest_gap_fraction >= config.cap16_fail_gap_fraction:
        return "FAIL", f"largest circular empty gap fraction {largest_gap_fraction:.4g} exceeds fail threshold {config.cap16_fail_gap_fraction:g}"
    if np.isfinite(max_ratio) and max_ratio >= config.cap16_fail_bin_ratio:
        return "FAIL", f"max bin count is {max_ratio:.3g} times expected"
    if np.isfinite(largest_gap_fraction) and largest_gap_fraction >= config.cap16_warn_gap_fraction:
        return "WARN", f"largest circular empty gap fraction {largest_gap_fraction:.4g} exceeds warn threshold {config.cap16_warn_gap_fraction:g}"
    if np.isfinite(max_ratio) and max_ratio >= config.cap16_warn_bin_ratio:
        return "WARN", f"max bin count is {max_ratio:.3g} times expected"
    return "PASS", "cap16 coverage is broadly distributed at configured binning"


def _assess_latency(n: int, outlier_count: int, outlier_fraction: float, config: AnalysisConfig) -> tuple[str, str]:
    if n < config.latency_min_rows:
        return "INSUFFICIENT_DATA", f"n={n} below configured minimum {config.latency_min_rows}"
    if outlier_count == 0:
        return "PASS", "no latency rows exceed robust outlier threshold"
    if outlier_count > 1 and outlier_fraction >= 0.01:
        return "FAIL", f"{outlier_count} latency outliers ({outlier_fraction:.2%}) exceed robust threshold"
    return "WARN", f"{outlier_count} isolated latency outlier(s) exceed robust threshold"


def _latency_outlier_threshold(median: float, p99: float, robust_sigma: float, config: AnalysisConfig) -> float:
    if np.isfinite(robust_sigma) and robust_sigma > 0:
        sigma_threshold = median + config.latency_outlier_sigma * robust_sigma
    else:
        sigma_threshold = p99 + 1.0 if np.isfinite(p99) else median + 1.0
    return float(max(sigma_threshold, median * config.latency_outlier_median_multiplier, p99 + 1.0 if np.isfinite(p99) else sigma_threshold))


def _latency_summary_row(assessment: str, reason: str) -> pd.DataFrame:
    row = {column: np.nan for column in _empty_latency_summary().columns}
    row["latency_assessment"] = assessment
    row["assessment_reason"] = reason
    return pd.DataFrame([row], columns=list(_empty_latency_summary().columns))


def _latency_outlier_context(
    pcps: pd.DataFrame,
    column: str,
    numeric: pd.Series,
    outlier_mask: pd.Series,
    threshold: float,
    median: float,
    p99: float,
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    context_columns = [
        "pps_t_hr",
        CANONICAL_PPS.gps_status,
        CANONICAL_PPS.holdover_age_ms,
        CANONICAL_PPS.drop_pps,
        "en",
        "ef",
        "es",
        "eh",
        "r_ppm",
        "j_ticks",
        "pps_raw_error_cycles",
        "offline_pps_adjusted_residual_cycles",
    ]
    for index in pcps.index[outlier_mask.fillna(False)]:
        loc = pcps.index.get_loc(index)
        previous_value = numeric.iloc[loc - 1] if loc > 0 else np.nan
        next_value = numeric.iloc[loc + 1] if loc + 1 < len(numeric) else np.nan
        row: Dict[str, Any] = {
            "row_index": int(loc),
            "source_index": index,
            "latency_column": column,
            "latency_cycles": numeric.loc[index],
            "threshold_cycles": threshold,
            "median_cycles": median,
            "p99_cycles": p99,
            "multiple_of_median": float(numeric.loc[index] / median) if median else np.nan,
            "previous_latency_cycles": previous_value,
            "next_latency_cycles": next_value,
            "isolated": not (pd.notna(previous_value) and previous_value > threshold) and not (pd.notna(next_value) and next_value > threshold),
            "disturbed_window_overlap": "not_configured",
        }
        for context in context_columns:
            if context in pcps.columns:
                row[context] = pcps.loc[index, context]
        rows.append(row)
    return pd.DataFrame(rows, columns=list(_empty_latency_outliers().columns) + [column for column in context_columns if column in pcps.columns])


def _boolean_run_lengths(values: np.ndarray) -> List[int]:
    runs: List[int] = []
    current = 0
    for value in values:
        if value:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def _max_outlier_elapsed_h(pcps: pd.DataFrame, outlier_mask: pd.Series) -> float:
    if "pps_t_hr" not in pcps.columns or not outlier_mask.fillna(False).any():
        return np.nan
    elapsed = pd.to_numeric(pcps.loc[outlier_mask.fillna(False), "pps_t_hr"], errors="coerce")
    return float(elapsed.max()) if elapsed.notna().any() else np.nan


def _top_value_counts(values: pd.Series, count: int = 5) -> str:
    counts = values.value_counts().head(count)
    return "; ".join(f"{value:g}:{int(total)}" for value, total in counts.items())


def _chi_square_p_value(chi2: float, dof: int) -> float:
    try:
        from scipy.stats import chi2 as chi2_distribution
    except Exception:
        return np.nan
    return float(chi2_distribution.sf(chi2, dof))


def _timebase_allan(ts: pd.DataFrame, config: AnalysisConfig, rows: List[Dict[str, Any]], warnings: List[str]) -> pd.DataFrame:
    column = first_available_column(ts, ["eh", "offline_pps_adjusted_residual_ppm", "pps_raw_error_ppm", "r_ppm"])
    if column is None:
        warnings.append("PCPS timebase health: no residual/correction column available for timebase Allan deviation")
        return _empty_allan()
    y = pd.to_numeric(ts[column], errors="coerce")
    if "ppm" in column or column == "r_ppm":
        y = y / 1_000_000.0
    allan = overlapping_allan_deviation(y.to_numpy(dtype=float), sample_period_s=1.0, min_diffs=config.min_allan_diffs)
    if allan.empty:
        warnings.append("PCPS timebase health: low-confidence timebase Allan points suppressed")
    allan = allan.copy()
    allan["source_column"] = column
    _row(rows, "timebase_allan", column, "points", int(len(allan)), "overlapping Allan deviation on PCPS residual/correction")
    return allan


def _add_discrete_rows(rows: List[Dict[str, Any]], section: str, metric: str, values: pd.Series) -> None:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[np.isfinite(numeric)]
    if numeric.empty:
        return
    robust_mad = mad(numeric)
    robust_iqr = iqr(numeric)
    if robust_mad != 0 and robust_iqr != 0:
        return
    counts = numeric.value_counts().head(5)
    top = "; ".join(f"{value:g}:{int(count)}" for value, count in counts.items())
    _row(rows, section, metric, "unique_values", int(numeric.nunique()), "robust spread is zero or quantized")
    _row(rows, section, metric, "top_values", top, "robust spread is zero or quantized")
    _row(rows, section, metric, "p01", float(numeric.quantile(0.01)), "discrete summary")
    _row(rows, section, metric, "p50", float(numeric.quantile(0.50)), "discrete summary")
    _row(rows, section, metric, "p99", float(numeric.quantile(0.99)), "discrete summary")


def _row(rows: List[Dict[str, Any]], section: str, metric: str, stat: str, value: Any, note: str) -> None:
    rows.append({"section": section, "metric": metric, "stat": stat, "value": value, "status": "ok", "note": note})


def _empty_allan() -> pd.DataFrame:
    return pd.DataFrame(columns=["tau_s", "m", "adev_fractional", "n_averages", "n_diffs", "source_column"])


def _empty_cap16_summary() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "n",
            "unique_cap16_count",
            "unique_cap16_fraction",
            "observed_min",
            "observed_max",
            "largest_empty_gap_ticks",
            "largest_empty_gap_fraction",
            "bin_count",
            "expected_count_per_bin",
            "max_bin_count",
            "min_bin_count",
            "max_bin_over_expected",
            "min_bin_over_expected",
            "chi_square_statistic",
            "chi_square_p_value",
            "uniformity_assessment",
            "assessment_reason",
        ]
    )


def _empty_cap16_bins() -> pd.DataFrame:
    return pd.DataFrame(columns=["bin_index", "bin_start", "bin_end_exclusive", "count", "expected_count", "count_over_expected"])


def _empty_latency_summary() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "n",
            "median_cycles",
            "p95_cycles",
            "p99_cycles",
            "max_cycles",
            "min_cycles",
            "robust_sigma_cycles",
            "unique_value_count",
            "top_value_counts",
            "outlier_threshold_cycles",
            "outlier_count",
            "outlier_fraction",
            "max_outlier_cycles",
            "max_outlier_elapsed_h",
            "consecutive_outlier_runs",
            "longest_outlier_run",
            "latency_assessment",
            "assessment_reason",
        ]
    )


def _empty_latency_outliers() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "row_index",
            "source_index",
            "latency_column",
            "latency_cycles",
            "threshold_cycles",
            "median_cycles",
            "p99_cycles",
            "multiple_of_median",
            "previous_latency_cycles",
            "next_latency_cycles",
            "isolated",
            "disturbed_window_overlap",
        ]
    )
