"""Reusable filter and outlier flag logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from .config import AnalysisConfig
from .schema import DERIVED, FLAGS, INPUT


@dataclass(frozen=True)
class OutlierResult:
    mask: pd.Series
    method: str
    warning: str = ""


def robust_outlier_mask(values: pd.Series, threshold: float) -> OutlierResult:
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric[np.isfinite(numeric)]
    mask = pd.Series(False, index=values.index)

    if len(finite) < 4:
        return OutlierResult(mask=mask, method="skipped", warning="Too few finite values for robust outlier detection")

    median = float(finite.median())
    mad = float((finite - median).abs().median())
    if mad > 0:
        robust_z = 0.67448975 * (numeric - median).abs() / mad
        return OutlierResult(mask=(robust_z > threshold).fillna(False), method="mad")

    q1 = float(finite.quantile(0.25))
    q3 = float(finite.quantile(0.75))
    iqr = q3 - q1
    if iqr > 0:
        lower = q1 - threshold * iqr
        upper = q3 + threshold * iqr
        return OutlierResult(mask=((numeric < lower) | (numeric > upper)).fillna(False), method="iqr", warning="MAD was zero; used IQR outlier fallback")

    return OutlierResult(mask=mask, method="skipped", warning="MAD and IQR were zero; skipped outlier detection")


def valid_mask(data: pd.DataFrame) -> pd.Series:
    """Rows with parseable finite timing fields before lock/drop policy."""
    mask = pd.Series(True, index=data.index)
    for column in [DERIVED.period_s]:
        if column in data.columns:
            numeric = pd.to_numeric(data[column], errors="coerce")
            mask &= np.isfinite(numeric)
    return mask.fillna(False)


def locked_mask(data: pd.DataFrame) -> pd.Series:
    """Rows where PPS/GPS status is locked according to the current policy."""
    if "pps_calibration_valid" in data:
        return data.pps_calibration_valid.fillna(False).astype(bool)
    if INPUT.gps_status not in data.columns:
        return pd.Series(False, index=data.index)
    return (pd.to_numeric(data[INPUT.gps_status], errors="coerce") == 2).fillna(False)


def dropped_mask(data: pd.DataFrame) -> pd.Series:
    """Rows flagged as dropped by the input stream."""
    if INPUT.dropped not in data.columns:
        return pd.Series(False, index=data.index)
    return (pd.to_numeric(data[INPUT.dropped], errors="coerce").fillna(0) != 0).fillna(False)


def analysis_mask(data: pd.DataFrame) -> pd.Series:
    """Structure-preserving analysis rows: valid, locked, and not dropped."""
    if FLAGS.is_analysis in data.columns:
        return data[FLAGS.is_analysis].astype(bool)
    if FLAGS.is_clean_primary in data.columns:
        return data[FLAGS.is_clean_primary].astype(bool)
    mask = valid_mask(data)
    if INPUT.gps_status in data.columns:
        mask &= locked_mask(data)
    if INPUT.dropped in data.columns:
        mask &= ~dropped_mask(data)
    return mask


def primary_analysis_mask(data: pd.DataFrame) -> pd.Series:
    """Primary analysis rows before any robust or structural outlier exclusion."""
    return analysis_mask(data)


def structure_diagnostic_mask(data: pd.DataFrame) -> pd.Series:
    """Rows for structure-discovery diagnostics; never applies global robust outlier filters."""
    return primary_analysis_mask(data)


def robust_summary_mask(data: pd.DataFrame) -> pd.Series:
    """Denoised summary rows: analysis rows after robust period/asymmetry exclusions."""
    if FLAGS.is_robust_summary in data.columns:
        return data[FLAGS.is_robust_summary].astype(bool)
    if FLAGS.is_clean_robust in data.columns:
        return data[FLAGS.is_clean_robust].astype(bool)
    return analysis_mask(data)


def mask_audit_counts(data: pd.DataFrame) -> Dict[str, int]:
    """Return reproducible row counts for the named masks used by analysis tooling."""
    counts = {"total_rows": int(len(data))}
    masks = {
        "valid_mask_rows": data[FLAGS.is_valid].astype(bool) if FLAGS.is_valid in data.columns else valid_mask(data),
        "locked_mask_rows": data[FLAGS.is_locked].astype(bool) if FLAGS.is_locked in data.columns else locked_mask(data),
        "dropped_mask_rows": data[FLAGS.is_dropped].astype(bool) if FLAGS.is_dropped in data.columns else dropped_mask(data),
        "analysis_mask_rows": analysis_mask(data),
        "robust_summary_mask_rows": robust_summary_mask(data),
    }
    counts.update({name: int(mask.sum()) for name, mask in masks.items()})
    for column in [FLAGS.is_period_outlier, FLAGS.is_asymmetry_outlier]:
        if column in data.columns:
            counts[f"{column}_rows"] = int(data[column].astype(bool).sum())
    return counts


def add_filter_flags(data: pd.DataFrame, config: AnalysisConfig) -> Tuple[pd.DataFrame, List[str]]:
    result = data.copy()
    warnings: List[str] = []

    result[FLAGS.is_valid] = valid_mask(result)
    result[FLAGS.is_locked] = locked_mask(result)
    result[FLAGS.is_dropped] = dropped_mask(result)
    result[FLAGS.is_analysis] = result[FLAGS.is_valid] & result[FLAGS.is_locked] & ~result[FLAGS.is_dropped]
    result[FLAGS.is_clean_primary] = result[FLAGS.is_analysis]

    period_outliers = robust_outlier_mask(result.loc[result[FLAGS.is_analysis], DERIVED.period_s], config.outlier_threshold)
    asymmetry_source = result.loc[result[FLAGS.is_analysis], DERIVED.A_half_ms] if DERIVED.A_half_ms in result.columns else pd.Series(dtype=float)
    asymmetry_outliers = robust_outlier_mask(asymmetry_source, config.outlier_threshold)

    result[FLAGS.is_period_outlier] = False
    result.loc[period_outliers.mask.index, FLAGS.is_period_outlier] = period_outliers.mask
    result[FLAGS.is_asymmetry_outlier] = False
    result.loc[asymmetry_outliers.mask.index, FLAGS.is_asymmetry_outlier] = asymmetry_outliers.mask
    result[FLAGS.is_robust_summary] = (
        result[FLAGS.is_analysis]
        & ~result[FLAGS.is_period_outlier]
        & ~result[FLAGS.is_asymmetry_outlier]
    )
    result[FLAGS.is_clean_robust] = result[FLAGS.is_robust_summary]

    for label, outliers in [("period", period_outliers), ("asymmetry", asymmetry_outliers)]:
        if outliers.warning:
            warnings.append(f"{label} outliers: {outliers.warning}")

    return result, warnings
