"""Dedicated PCPS/CPS clock-quality diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .allan import compute_allan
from .config import AnalysisConfig
from .schema import CANONICAL_PPS, FLAGS
from .stats import describe_series
from .timeline import delta_u32, duplicate_timestamps, suspicious_reverse_jumps, unwrap_u32


@dataclass(frozen=True)
class PcpsAnalysis:
    intervals: pd.DataFrame
    summary: Dict[str, Any]
    warnings: List[str]


def analyze_pcps(path: Optional[Path], config: AnalysisConfig) -> PcpsAnalysis:
    warnings: List[str] = []
    if path is None:
        return PcpsAnalysis(pd.DataFrame(), {}, ["No PCPS/CPS file was available for dedicated PPS analysis"])

    data = pd.read_csv(path)
    required = [CANONICAL_PPS.seq, CANONICAL_PPS.edge_tcb0]
    missing = [column for column in required if column not in data.columns]
    if missing:
        return PcpsAnalysis(pd.DataFrame(), {}, [f"PCPS/CPS missing required column(s): {', '.join(missing)}"])

    data = data.copy()
    for column in [CANONICAL_PPS.seq, CANONICAL_PPS.edge_tcb0, CANONICAL_PPS.gps_status, CANONICAL_PPS.drop_pps]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    bad_required = data[required].isna().any(axis=1)
    if bad_required.any():
        warnings.append(f"PCPS/CPS has {int(bad_required.sum())} malformed required row(s); they were excluded from interval analysis")
    data = data.loc[~bad_required].reset_index(drop=True)

    data["edge_unwrapped_cycles"] = unwrap_u32(data[CANONICAL_PPS.edge_tcb0])
    data["pps_interval_cycles"] = data["edge_unwrapped_cycles"].diff()
    data["seq_delta"] = data[CANONICAL_PPS.seq].diff()
    data["raw_pps_error_cycles"] = data["pps_interval_cycles"] - config.nominal_hz
    data["raw_pps_error_s"] = data["raw_pps_error_cycles"] / config.nominal_hz
    data["raw_pps_error_ns"] = data["raw_pps_error_s"] * 1e9
    data["raw_pps_error_ppm"] = data["raw_pps_error_cycles"] / config.nominal_hz * 1_000_000.0

    valid = data["pps_interval_cycles"].notna()
    if CANONICAL_PPS.seq in data.columns:
        valid &= data["seq_delta"] == 1
    if CANONICAL_PPS.gps_status in data.columns:
        valid &= data[CANONICAL_PPS.gps_status] == 2
    if CANONICAL_PPS.drop_pps in data.columns:
        valid &= data[CANONICAL_PPS.drop_pps] == 0
    valid &= data["pps_interval_cycles"].between(config.nominal_hz * 0.5, config.nominal_hz * 1.5)
    data["is_valid_pps_interval"] = valid.fillna(False)

    valid_errors = data.loc[data["is_valid_pps_interval"], "raw_pps_error_cycles"]
    expected = valid_errors.shift(1).rolling(window=config.pps_residual_window, min_periods=max(3, config.pps_residual_window // 3)).median()
    data["offline_expected_error_cycles"] = np.nan
    data.loc[valid_errors.index, "offline_expected_error_cycles"] = expected
    data["offline_adjusted_residual_cycles"] = data["raw_pps_error_cycles"] - data["offline_expected_error_cycles"]
    data["offline_adjusted_residual_ns"] = data["offline_adjusted_residual_cycles"] / config.nominal_hz * 1e9
    data["offline_adjusted_residual_ppm"] = data["offline_adjusted_residual_cycles"] / config.nominal_hz * 1_000_000.0

    summary = {
        "path": str(path),
        "rows": int(len(data)),
        "valid_interval_rows": int(data["is_valid_pps_interval"].sum()),
        "nominal_cycles_per_second": config.nominal_hz,
        "duplicate_edge_timestamps": duplicate_timestamps(data[CANONICAL_PPS.edge_tcb0]),
        "suspicious_reverse_edge_jumps": suspicious_reverse_jumps(data[CANONICAL_PPS.edge_tcb0]),
        "gps_status_counts": _counts(data, CANONICAL_PPS.gps_status),
        "drop_pps_counts": _counts(data, CANONICAL_PPS.drop_pps),
        "raw_pps_error_cycles": describe_series(data.loc[data["is_valid_pps_interval"], "raw_pps_error_cycles"]),
        "raw_pps_error_ns": describe_series(data.loc[data["is_valid_pps_interval"], "raw_pps_error_ns"]),
        "raw_pps_error_ppm": describe_series(data.loc[data["is_valid_pps_interval"], "raw_pps_error_ppm"]),
        "offline_adjusted_residual_cycles": describe_series(data.loc[data["is_valid_pps_interval"], "offline_adjusted_residual_cycles"]),
        "offline_adjusted_residual_ns": describe_series(data.loc[data["is_valid_pps_interval"], "offline_adjusted_residual_ns"]),
        "offline_adjusted_residual_ppm": describe_series(data.loc[data["is_valid_pps_interval"], "offline_adjusted_residual_ppm"]),
        "cap16": describe_series(data[CANONICAL_PPS.cap16]) if CANONICAL_PPS.cap16 in data.columns else None,
        "latency16": describe_series(data[CANONICAL_PPS.latency16]) if CANONICAL_PPS.latency16 in data.columns else None,
        "methodology": {
            "raw_pps_error_cycles": "pps_interval_cycles - nominal_cycles_per_second",
            "offline_adjusted_residual": f"current raw PPS error minus previous {config.pps_residual_window} valid-interval trailing median",
            "filters": "seq_delta == 1, gps_status == 2 when present, drop_pps == 0 when present, plausible one-second interval",
        },
    }
    return PcpsAnalysis(data, summary, warnings)


def write_pcps_outputs(analysis: PcpsAnalysis, summary_dir: Path) -> None:
    if analysis.intervals.empty:
        return
    analysis.intervals.to_csv(summary_dir / "pcps_intervals.csv", index=False)
    rows = []
    for metric, values in analysis.summary.items():
        if isinstance(values, dict) and "count" in values:
            for stat, value in values.items():
                rows.append({"metric": metric, "stat": stat, "value": value})
    pd.DataFrame(rows).to_csv(summary_dir / "pcps_summary.csv", index=False)


def _counts(data: pd.DataFrame, column: str) -> Dict[str, int]:
    if column not in data.columns:
        return {}
    return {str(k): int(v) for k, v in data[column].value_counts(dropna=False).sort_index().items()}
