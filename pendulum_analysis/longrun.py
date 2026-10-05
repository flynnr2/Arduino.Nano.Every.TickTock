"""Longer-run timing diagnostics."""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .allan import overlapping_allan_deviation
from .config import AnalysisConfig
from .schema import INPUT


def build_time_of_day_fold(pcsw: pd.DataFrame) -> pd.DataFrame:
    columns = ["hour", "n", "full_resid_us_median", "full_resid_us_iqr", "full_s_median"]
    if not {"t_s", "full_resid_us"}.issubset(pcsw.columns):
        return pd.DataFrame(columns=columns)
    frame = pcsw.copy()
    frame["hour"] = np.floor((pd.to_numeric(frame["t_s"], errors="coerce") / 3600.0) % 24).astype("Int64")
    rows = []
    for hour, group in frame.dropna(subset=["hour"]).groupby("hour"):
        resid = pd.to_numeric(group["full_resid_us"], errors="coerce").dropna()
        full = pd.to_numeric(group.get("full_s"), errors="coerce").dropna() if "full_s" in group else pd.Series(dtype=float)
        rows.append(
            {
                "hour": int(hour),
                "n": int(resid.count()),
                "full_resid_us_median": float(resid.median()) if not resid.empty else np.nan,
                "full_resid_us_iqr": float(resid.quantile(0.75) - resid.quantile(0.25)) if not resid.empty else np.nan,
                "full_s_median": float(full.median()) if not full.empty else np.nan,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_daily_summary(pcsw: pd.DataFrame) -> pd.DataFrame:
    columns = ["day", "n", "full_s_median", "full_resid_us_median", "full_resid_us_robust_sigma", "half_asymmetry_ms_median", "temp_C_median", "humidity_pct_median", "pressure_hPa_median"]
    if "t_s" not in pcsw.columns:
        return pd.DataFrame(columns=columns)
    frame = pcsw.copy()
    frame["day"] = np.floor(pd.to_numeric(frame["t_s"], errors="coerce") / 86400.0).astype("Int64")
    rows = []
    for day, group in frame.dropna(subset=["day"]).groupby("day"):
        resid = pd.to_numeric(group.get("full_resid_us"), errors="coerce").dropna()
        q75 = resid.quantile(0.75) if not resid.empty else np.nan
        q25 = resid.quantile(0.25) if not resid.empty else np.nan
        row: Dict[str, Any] = {"day": int(day), "n": int(len(group))}
        for column in ["full_s", "full_resid_us", "half_asymmetry_ms", INPUT.temp_C, INPUT.humidity_pct, INPUT.pressure_hPa]:
            values = pd.to_numeric(group.get(column), errors="coerce").dropna() if column in group else pd.Series(dtype=float)
            row[f"{column}_median"] = float(values.median()) if not values.empty else np.nan
        row["full_resid_us_robust_sigma"] = float((q75 - q25) / 1.349) if np.isfinite(q75) and np.isfinite(q25) else np.nan
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def build_environmental_lag_correlations(pcsw: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    columns = ["metric", "env_var", "lag_hours", "correlation", "n", "method"]
    if "t_s" not in pcsw.columns:
        return pd.DataFrame(columns=columns)
    env_vars = [column for column in [INPUT.temp_C, INPUT.humidity_pct, INPUT.pressure_hPa, "temperature_C"] if column in pcsw.columns]
    metrics = [column for column in ["full_resid_us", "full_s", "half_asymmetry_ms", "block_A_s", "block_B_s"] if column in pcsw.columns]
    if not env_vars or not metrics:
        return pd.DataFrame(columns=columns)
    max_hours = int(config.env_lag_max_hours)
    rows = []
    t = pd.to_numeric(pcsw["t_s"], errors="coerce")
    for env in env_vars:
        env_frame = pd.DataFrame({"t_s": t, "env": pd.to_numeric(pcsw[env], errors="coerce")}).dropna().sort_values("t_s")
        for metric in metrics:
            metric_frame = pd.DataFrame({"t_s": t, "metric": pd.to_numeric(pcsw[metric], errors="coerce")}).dropna().sort_values("t_s")
            best: Dict[str, Any] | None = None
            for lag_h in range(-max_hours, max_hours + 1):
                shifted = env_frame.copy()
                shifted["t_s"] = shifted["t_s"] + lag_h * 3600.0
                merged = pd.merge_asof(metric_frame, shifted, on="t_s", direction="nearest", tolerance=1800.0)
                subset = merged[["metric", "env"]].dropna()
                corr = float(subset["metric"].corr(subset["env"])) if len(subset) >= 3 and subset["metric"].nunique() > 1 and subset["env"].nunique() > 1 else np.nan
                row = {"metric": metric, "env_var": env, "lag_hours": lag_h, "correlation": corr, "n": int(len(subset)), "method": "lagged Pearson"}
                if np.isfinite(corr) and (best is None or abs(corr) > abs(float(best.get("correlation", 0.0)))):
                    best = row
            if best is not None:
                rows.append(best)
    return pd.DataFrame(rows, columns=columns)


def build_rolling_stability(pcsw: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    columns = ["center_t_hr", "window_hours", "n", "full_resid_us_median", "full_resid_us_iqr", "full_resid_us_robust_sigma"]
    if not {"t_s", "full_resid_us"}.issubset(pcsw.columns):
        return pd.DataFrame(columns=columns)
    frame = pcsw[["t_s", "full_resid_us"]].apply(pd.to_numeric, errors="coerce").dropna().sort_values("t_s")
    if frame.empty:
        return pd.DataFrame(columns=columns)
    window_s = config.rolling_window_hours * 3600.0
    step_s = max(window_s / 4.0, 3600.0)
    rows = []
    for center in np.arange(frame["t_s"].min() + window_s / 2.0, frame["t_s"].max() - window_s / 2.0 + 1.0, step_s):
        subset = frame.loc[(frame["t_s"] >= center - window_s / 2.0) & (frame["t_s"] <= center + window_s / 2.0), "full_resid_us"]
        if len(subset) < 3:
            continue
        iqr = float(subset.quantile(0.75) - subset.quantile(0.25))
        rows.append({"center_t_hr": center / 3600.0, "window_hours": config.rolling_window_hours, "n": int(len(subset)), "full_resid_us_median": float(subset.median()), "full_resid_us_iqr": iqr, "full_resid_us_robust_sigma": iqr / 1.349})
    return pd.DataFrame(rows, columns=columns)


def build_rolling_allan(pcsw: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    columns = ["center_t_hr", "window_hours", "best_tau_s", "min_adev_fractional", "n_tau"]
    if not {"t_s", "full_s"}.issubset(pcsw.columns):
        return pd.DataFrame(columns=columns)
    frame = pcsw[["t_s", "full_s"]].apply(pd.to_numeric, errors="coerce").dropna().sort_values("t_s")
    if len(frame) < config.min_allan_diffs + 2:
        return pd.DataFrame(columns=columns)
    window_s = config.rolling_allan_window_hours * 3600.0
    step_s = max(window_s / 3.0, 3600.0)
    rows = []
    for center in np.arange(frame["t_s"].min() + window_s / 2.0, frame["t_s"].max() - window_s / 2.0 + 1.0, step_s):
        subset = frame.loc[(frame["t_s"] >= center - window_s / 2.0) & (frame["t_s"] <= center + window_s / 2.0), "full_s"]
        if len(subset) < config.min_allan_diffs + 2:
            continue
        y = subset / subset.median() - 1.0
        allan = overlapping_allan_deviation(y.to_numpy(dtype=float), float(subset.median()), min_diffs=config.min_allan_diffs)
        if allan.empty:
            continue
        best = allan.loc[allan["adev_fractional"].idxmin()]
        rows.append({"center_t_hr": center / 3600.0, "window_hours": config.rolling_allan_window_hours, "best_tau_s": best["tau_s"], "min_adev_fractional": best["adev_fractional"], "n_tau": int(len(allan))})
    return pd.DataFrame(rows, columns=columns)


def build_diurnal_harmonic_fit(pcsw: pd.DataFrame) -> pd.DataFrame:
    columns = ["metric", "amplitude", "phase_hours", "r_squared", "n", "terms"]
    if not {"t_s", "full_resid_us"}.issubset(pcsw.columns):
        return pd.DataFrame(columns=columns)
    frame = pcsw[["t_s", "full_resid_us"]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(frame) < 24:
        return pd.DataFrame(columns=columns)
    t = frame["t_s"].to_numpy(dtype=float)
    y = frame["full_resid_us"].to_numpy(dtype=float)
    omega = 2.0 * np.pi / 86400.0
    x = np.column_stack([np.ones(len(t)), np.sin(omega * t), np.cos(omega * t), np.sin(2 * omega * t), np.cos(2 * omega * t)])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    fitted = x @ coef
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    amp = float(np.hypot(coef[1], coef[2]))
    phase = (float(np.arctan2(coef[2], coef[1])) / omega / 3600.0) % 24.0
    return pd.DataFrame([{"metric": "full_resid_us", "amplitude": amp, "phase_hours": phase, "r_squared": 1.0 - ss_res / ss_tot if ss_tot else np.nan, "n": int(len(frame)), "terms": "24 h and 12 h sin/cos"}], columns=columns)


def build_change_point_candidates(pcsw: pd.DataFrame) -> pd.DataFrame:
    columns = ["metric", "elapsed_days", "before_median", "after_median", "magnitude", "score", "method"]
    if "full_resid_us" not in pcsw.columns:
        return pd.DataFrame(columns=columns)
    values = pd.to_numeric(pcsw["full_resid_us"], errors="coerce").dropna()
    if len(values) < 30:
        return pd.DataFrame(columns=columns)
    mid = len(values) // 2
    before = values.iloc[:mid]
    after = values.iloc[mid:]
    magnitude = float(after.median() - before.median())
    spread = float((values.quantile(0.75) - values.quantile(0.25)) / 1.349)
    score = abs(magnitude) / spread if spread > 0 else np.nan
    t_s = pd.to_numeric(pcsw.get("t_s"), errors="coerce")
    elapsed_days = float(t_s.iloc[mid] / 86400.0) if len(t_s) > mid and np.isfinite(t_s.iloc[mid]) else np.nan
    if np.isfinite(score) and score < 3.0:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([{"metric": "full_resid_us", "elapsed_days": elapsed_days, "before_median": float(before.median()), "after_median": float(after.median()), "magnitude": magnitude, "score": score, "method": "single split robust median screen"}], columns=columns)


def build_investigation_next(bundle: Any) -> List[str]:
    findings: List[str] = []
    env = getattr(bundle, "environmental_lag", pd.DataFrame())
    if isinstance(env, pd.DataFrame) and "status" in env.columns and env["status"].eq("not_generated").any():
        env = pd.DataFrame()
    if not env.empty and "correlation" in env.columns:
        ranked = env.copy()
        ranked["abs_corr"] = pd.to_numeric(ranked["correlation"], errors="coerce").abs()
        ranked = ranked.dropna(subset=["abs_corr"]).sort_values("abs_corr", ascending=False)
        if not ranked.empty:
            row = ranked.iloc[0]
            findings.append(f"Review {row.get('metric')} against {row.get('env_var')} near {row.get('lag_hours')} h lag; strongest screened Pearson r is {row.get('correlation'):.3g}.")
    cp = getattr(bundle, "change_points", pd.DataFrame())
    if isinstance(cp, pd.DataFrame) and "status" in cp.columns and cp["status"].eq("not_generated").any():
        cp = pd.DataFrame()
    if not cp.empty:
        row = cp.iloc[0]
        findings.append(f"Review candidate median shift in {row.get('metric')} near day {row.get('elapsed_days'):.2f}; screened magnitude is {row.get('magnitude'):.3g}.")
    harmonic = getattr(bundle, "diurnal_harmonic", pd.DataFrame())
    if isinstance(harmonic, pd.DataFrame) and "status" in harmonic.columns and harmonic["status"].eq("not_generated").any():
        harmonic = pd.DataFrame()
    if not harmonic.empty:
        row = harmonic.iloc[0]
        findings.append(f"Review daily residual structure: 24 h/12 h harmonic fit explains {float(row.get('r_squared')) * 100.0:.1f}% of screened residual variance.")
    if not findings:
        findings.append("No long-run triage finding was generated from the analyses that ran.")
    return findings
