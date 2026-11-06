"""Numerical summary helpers used by reports and plots."""

from __future__ import annotations

from typing import Dict, Optional, Union

import numpy as np
import pandas as pd


def mad(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[np.isfinite(numeric)]
    if numeric.empty:
        return float("nan")
    median = float(numeric.median())
    return float((numeric - median).abs().median())


def iqr(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[np.isfinite(numeric)]
    if numeric.empty:
        return float("nan")
    return float(numeric.quantile(0.75) - numeric.quantile(0.25))


def describe_series(values: pd.Series) -> Dict[str, Optional[Union[float, int]]]:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[np.isfinite(numeric)]
    if numeric.empty:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "std": None,
            "MAD": None,
            "robust_sigma": None,
        "IQR": None,
        "min": None,
        "max": None,
        "p01": None,
        "p05": None,
        "p25": None,
        "p75": None,
        "p95": None,
        "p99": None,
        }
    return {
        "count": int(numeric.count()),
        "mean": float(numeric.mean()),
        "median": float(numeric.median()),
        "std": float(numeric.std(ddof=1)) if len(numeric) > 1 else 0.0,
        "MAD": mad(numeric),
        "robust_sigma": 1.4826 * mad(numeric),
        "IQR": iqr(numeric),
        "min": float(numeric.min()),
        "max": float(numeric.max()),
        "p01": float(numeric.quantile(0.01)),
        "p05": float(numeric.quantile(0.05)),
        "p25": float(numeric.quantile(0.25)),
        "p75": float(numeric.quantile(0.75)),
        "p95": float(numeric.quantile(0.95)),
        "p99": float(numeric.quantile(0.99)),
    }
