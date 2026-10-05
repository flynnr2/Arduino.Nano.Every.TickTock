"""Overlapping Allan deviation."""

from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from .config import AnalysisConfig
from .filters import structure_diagnostic_mask
from .schema import DERIVED, INPUT

ALLAN_COLUMNS = [
    "tau_s",
    "m",
    "adev_fractional",
    "n_averages",
    "n_diffs",
    "mask_name",
    "row_inclusion_policy",
    "deterministic_phase_structure_included",
    "robust_outliers_excluded",
    "gap_policy",
]


def _sample_spacing_s(data: pd.DataFrame, config: AnalysisConfig) -> float:
    if INPUT.timestamp in data.columns:
        timestamps = pd.to_datetime(data[INPUT.timestamp], errors="coerce").dropna().sort_values()
        if len(timestamps) >= 3:
            spacing = timestamps.diff().dt.total_seconds().dropna().median()
            if np.isfinite(spacing) and spacing > 0:
                return float(spacing)
    return config.target_period_s


def overlapping_allan_deviation(
    y: np.ndarray,
    sample_period_s: float,
    min_diffs: int = 20,
    max_m: Optional[int] = None,
) -> pd.DataFrame:
    """Compute overlapping Allan deviation for fractional-frequency samples."""
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)]
    if len(y) < 3 or min_diffs <= 0:
        return pd.DataFrame(columns=ALLAN_COLUMNS)

    if max_m is None:
        max_m = len(y) // 3
    max_m = int(max_m)
    if max_m < 1:
        return pd.DataFrame(columns=ALLAN_COLUMNS)

    ms: List[int] = []
    m = 1
    while m <= max_m:
        ms.append(m)
        m *= 2

    rows: List[dict] = []
    cumsum = np.concatenate([[0.0], np.cumsum(y)])
    for m in ms:
        averages = (cumsum[m:] - cumsum[:-m]) / m
        if len(averages) <= m:
            continue

        # Overlapping Allan deviation compares adjacent m-sample averages
        # separated by one averaging interval, not adjacent moving windows.
        diffs = averages[m:] - averages[:-m]
        n_diffs = len(diffs)
        if n_diffs < min_diffs:
            continue

        adev = np.sqrt(0.5 * np.mean(diffs**2))
        rows.append({"tau_s": float(m * sample_period_s), "m": int(m), "adev_fractional": float(adev), "n_averages": int(len(averages)), "n_diffs": int(n_diffs)})

    base_columns = ["tau_s", "m", "adev_fractional", "n_averages", "n_diffs"]
    return pd.DataFrame(rows, columns=base_columns)


def compute_allan(data: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    mask = structure_diagnostic_mask(data)
    clean = data.loc[mask]
    spacing = _sample_spacing_s(clean, config)
    if "pps_calibration_valid" in data:
        y = pd.to_numeric(data[DERIVED.period_s], errors="coerce").to_numpy()/config.target_period_s-1
        valid = mask.to_numpy() & np.isfinite(y)
        # A boundary invalidates windows crossing it, but not either endpoint.
        boundary = np.zeros(len(data), bool)
        if "seq" in data:
            boundary[1:] |= np.diff(data.seq.to_numpy()) % (2**32) != 1
        if "pps_counter_segment" in data:
            boundary[1:] |= np.diff(data.pps_counter_segment.to_numpy()) != 0
        sums = np.r_[0., np.cumsum(np.where(valid,y,0.))]
        bad = np.r_[0, np.cumsum(~valid)]
        breaks = np.r_[0, np.cumsum(boundary)]
        rows = []
        m = 1
        while m <= len(y)//3:
            starts = np.arange(max(0,len(y)-2*m+1))
            ok = (bad[starts+2*m]-bad[starts] == 0) & (breaks[starts+2*m]-breaks[starts+1] == 0)
            starts = starts[ok]
            if len(starts) >= config.min_allan_diffs:
                differences = (sums[starts+2*m]-2*sums[starts+m]+sums[starts])/m
                rows.append(dict(tau_s=m*spacing,m=m,adev_fractional=float(np.sqrt(np.mean(differences**2)/2)),
                                 n_averages=len(starts)+1,n_diffs=len(starts)))
            m *= 2
        allan = pd.DataFrame(rows)
    else:
        y = pd.to_numeric(clean[DERIVED.period_s], errors="coerce") / config.target_period_s - 1.0
        y = y[np.isfinite(y)].to_numpy(dtype=float)
        allan = overlapping_allan_deviation(y, spacing, min_diffs=config.min_allan_diffs)
    if allan.empty:
        return pd.DataFrame(columns=ALLAN_COLUMNS)
    allan["mask_name"] = "structure_diagnostic_mask"
    allan["row_inclusion_policy"] = "valid locked non-dropped rows; no global robust structural deletion"
    allan["deterministic_phase_structure_included"] = True
    allan["robust_outliers_excluded"] = False
    allan["gap_policy"] = ("exclude windows crossing missing/invalid calibration, sequence gaps or counter segments"
                           if "pps_calibration_valid" in data else "assumes retained rows are regular; gaps reflected in effective n_diffs")
    return allan[ALLAN_COLUMNS]
