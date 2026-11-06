"""Phase-fold summaries."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import AnalysisConfig
from .filters import robust_summary_mask, structure_diagnostic_mask
from .schema import DERIVED, INPUT, PHASEFOLD_COLUMNS
from .stats import mad


def add_phase_index(data: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    result = data.copy()
    if config.phase_mod <= 0:
        return result
    source = result[INPUT.row_index]
    if INPUT.swing_index in result.columns:
        swing = pd.to_numeric(result[INPUT.swing_index], errors="coerce")
        if swing.notna().any():
            source = swing.where(swing.notna(), result[INPUT.row_index])
    result[DERIVED.phase_index] = (source.astype(int) % config.phase_mod).astype(int)
    return result


def compute_phasefold(data: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    with_phase = add_phase_index(data, config)
    mask = structure_diagnostic_mask(with_phase)
    robust_mask = robust_summary_mask(with_phase)
    clean = with_phase.loc[mask]
    rows = []
    grouped = clean.groupby(DERIVED.phase_index, sort=True)
    robust_grouped = robust_mask.groupby(with_phase[DERIVED.phase_index]).sum() if DERIVED.phase_index in with_phase.columns else pd.Series(dtype=int)
    for phase, group in grouped:
        robust_n = int(robust_grouped.get(phase, 0))
        row = {
            DERIVED.phase_index: int(phase),
            "mask_name": "structure_diagnostic_mask",
            "analysis_rows": int(len(group)),
            "robust_summary_rows": robust_n,
            "robust_outlier_fraction": float((len(group) - robust_n) / len(group)) if len(group) else np.nan,
        }
        for column in PHASEFOLD_COLUMNS:
            numeric = pd.to_numeric(group[column], errors="coerce")
            numeric = numeric[np.isfinite(numeric)]
            row[f"{column}_count"] = int(numeric.count())
            row[f"{column}_median"] = float(numeric.median()) if not numeric.empty else np.nan
            row[f"{column}_MAD"] = mad(numeric) if not numeric.empty else np.nan
            row[f"{column}_p05"] = float(numeric.quantile(0.05)) if not numeric.empty else np.nan
            row[f"{column}_p95"] = float(numeric.quantile(0.95)) if not numeric.empty else np.nan
        rows.append(row)
    columns = [DERIVED.phase_index]
    columns.extend(["mask_name", "analysis_rows", "robust_summary_rows", "robust_outlier_fraction"])
    for column in PHASEFOLD_COLUMNS:
        columns.extend([f"{column}_count", f"{column}_median", f"{column}_MAD", f"{column}_p05", f"{column}_p95"])
    return pd.DataFrame(rows, columns=columns)
