"""Data quality summaries."""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from .schema import DERIVED, FLAGS, INPUT


def count_locked_transitions(data: pd.DataFrame) -> int:
    locked = data[FLAGS.is_locked].astype(bool)
    if len(locked) < 2:
        return 0
    return int((locked != locked.shift()).iloc[1:].sum())


def longest_true_segment(mask: pd.Series) -> int:
    best = 0
    current = 0
    for value in mask.astype(bool):
        if value:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def estimate_run_duration(data: pd.DataFrame) -> Optional[float]:
    if INPUT.timestamp in data.columns:
        timestamps = pd.to_datetime(data[INPUT.timestamp], errors="coerce")
        if timestamps.notna().sum() >= 2:
            return float((timestamps.max() - timestamps.min()).total_seconds())
    if DERIVED.period_s in data.columns and data[DERIVED.period_s].notna().any():
        return float(pd.to_numeric(data[DERIVED.period_s], errors="coerce").sum())
    return None


def build_quality_report(data: pd.DataFrame) -> Dict[str, Any]:
    return {
        "total_rows": int(len(data)),
        "gps_status_counts": {str(k): int(v) for k, v in data[INPUT.gps_status].value_counts(dropna=False).sort_index().items()},
        "dropped_counts": {str(k): int(v) for k, v in data[INPUT.dropped].value_counts(dropna=False).sort_index().items()},
        "locked_row_count": int(data[FLAGS.is_locked].sum()),
        "clean_primary_row_count": int(data[FLAGS.is_clean_primary].sum()),
        "clean_robust_row_count": int(data[FLAGS.is_clean_robust].sum()),
        "locked_to_unlocked_transition_count": count_locked_transitions(data),
        "longest_contiguous_locked_segment": longest_true_segment(data[FLAGS.is_locked]),
        "missing_counts_by_column": {column: int(count) for column, count in data.isna().sum().items() if int(count) > 0},
        "period_outlier_count": int(data[FLAGS.is_period_outlier].sum()),
        "asymmetry_outlier_count": int(data[FLAGS.is_asymmetry_outlier].sum()),
        "estimated_run_duration_s": estimate_run_duration(data),
    }


def quality_report_frame(report: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    for key, value in report.items():
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                rows.append({"metric": key, "name": subkey, "value": subvalue})
        else:
            rows.append({"metric": key, "name": "", "value": value})
    return pd.DataFrame(rows)
