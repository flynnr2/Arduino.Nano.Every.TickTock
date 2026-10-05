"""Headline and environmental metrics."""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

import numpy as np
import pandas as pd

from .filters import analysis_mask, robust_summary_mask
from .schema import DERIVED, INPUT, METRIC_COLUMNS
from .stats import describe_series


def compute_metrics(data: pd.DataFrame) -> Dict[str, Dict[str, Dict[str, Optional[Union[float, int]]]]]:
    output: Dict[str, Dict[str, Dict[str, Optional[Union[float, int]]]]] = {}
    for label, mask in [("clean_primary", analysis_mask(data)), ("clean_robust", robust_summary_mask(data))]:
        subset = data.loc[mask]
        output[label] = {column: describe_series(subset[column]) for column in METRIC_COLUMNS if column in subset.columns}
    return output


def _correlation(data: pd.DataFrame, left: str, right: str) -> Optional[float]:
    subset = data[[left, right]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(subset) < 3 or subset[left].nunique() < 2 or subset[right].nunique() < 2:
        return None
    value = subset[left].corr(subset[right])
    return None if not np.isfinite(value) else float(value)


def _robust_slope(data: pd.DataFrame, x: str, y: str, max_pairs: int = 300) -> Optional[float]:
    subset = data[[x, y]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(subset) < 3 or subset[x].nunique() < 2:
        return None
    if len(subset) > max_pairs:
        bins = pd.cut(pd.Series(np.arange(len(subset)), index=subset.index), bins=max_pairs, labels=False, duplicates="drop")
        selected = []
        for _, group in subset.groupby(bins, sort=True):
            selected.append(group.index[len(group) // 2])
        subset = subset.loc[selected]
    xs = subset[x].to_numpy()
    ys = subset[y].to_numpy()
    slopes = []
    for i in range(len(xs) - 1):
        dx = xs[i + 1 :] - xs[i]
        valid = dx != 0
        slopes.extend(((ys[i + 1 :][valid] - ys[i]) / dx[valid]).tolist())
    if not slopes:
        return None
    return float(np.median(slopes))


def environmental_diagnostics(data: pd.DataFrame) -> Dict[str, Any]:
    clean = data.loc[robust_summary_mask(data)]
    diagnostics: Dict[str, Any] = {}
    for env_col in [INPUT.temp_C, INPUT.humidity_pct, INPUT.pressure_hPa]:
        if env_col in clean.columns:
            diagnostics[f"corr_rate_ppm_vs_{env_col}"] = _correlation(clean, DERIVED.rate_ppm, env_col)
    if INPUT.temp_C in clean.columns:
        diagnostics["corr_tick_block_vs_temp_C"] = _correlation(clean, INPUT.tick_block, INPUT.temp_C)
        diagnostics["corr_tock_block_vs_temp_C"] = _correlation(clean, INPUT.tock_block, INPUT.temp_C)
        diagnostics["robust_slope_rate_ppm_per_temp_C"] = _robust_slope(clean, INPUT.temp_C, DERIVED.rate_ppm)
    return {key: value for key, value in diagnostics.items() if value is not None}
