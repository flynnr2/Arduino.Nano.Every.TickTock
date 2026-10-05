"""Numbered Tufte-like canonical v2 figures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "pendulum_analysis_matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .artifacts import ArtifactRegistry
from .canonical_v2 import CanonicalV2Bundle, PCPS_HEALTH_PLOT_SPECS, PLOT_SPECS


INK = "0.12"
MUTED = "0.45"
LIGHT = "0.88"
ACCENT = "#9f3b2f"
BLUE = "#345f75"
GREEN = "#4f6f45"


def create_canonical_v2_plots(bundle: CanonicalV2Bundle, registry: ArtifactRegistry) -> None:
    pcsw = bundle.pcsw_sampled
    paths = _register_plot_paths(registry, PLOT_SPECS + PCPS_HEALTH_PLOT_SPECS)
    if bundle.profile_selection.selected.name == "synchronome":
        artifact = registry.register_plot(
            namespace="pcsw",
            slug="synchronome_phase15_jitter_polar",
            title="Synchronome phase-15 jitter localisation",
            section="Synchronome impulse/jitter localisation",
            source_files=["PCSW.csv"],
            analysis_name="synchronome_jitter_localisation",
        )
        paths["synchronome_phase15_jitter_polar"] = registry.path(artifact)

    _series_plot(pcsw, "t_hr", "full_s", "full_med31_s", "Full-cycle timing overview", "full period (s)", paths["full_cycle_timing_overview"])
    _multi_series_plot(
        pcsw,
        "t_hr",
        [("half_A_s", BLUE), ("half_B_s", GREEN)],
        [("half_A_s_med31", BLUE), ("half_B_s_med31", GREEN)],
        "Half-cycle comparison",
        "half cycle (s)",
        paths["half_cycle_comparison"],
    )
    _multi_series_plot(
        pcsw,
        "t_hr",
        [("open_A_s", BLUE), ("block_A_s", ACCENT), ("open_B_s", GREEN), ("block_B_s", MUTED)],
        [("open_A_s_med31", BLUE), ("block_A_s_med31", ACCENT), ("open_B_s_med31", GREEN), ("block_B_s_med31", MUTED)],
        "Component decomposition",
        "interval (s)",
        paths["component_decomposition"],
    )
    _histogram(pcsw.get("full_resid_us"), "Residual histogram", "full residual (us)", paths["residual_histogram"])
    _autocorrelation(pcsw.get("full_resid_us"), paths["residual_autocorrelation"])
    _fft_plot(bundle.fft_components, bundle.fft_expected, paths["residual_fft"], str(bundle.quality.get("fft_x_axis", "period")))
    phase_moduli = list(bundle.profile_selection.selected.phase.expected_moduli)
    primary_modulus = phase_moduli[0] if phase_moduli else None
    secondary_modulus = phase_moduli[1] if len(phase_moduli) > 1 else None
    _phase_plot(bundle.phase_fold, primary_modulus, paths["phase_fold_primary"], "primary")
    _phase_plot(bundle.phase_fold, secondary_modulus, paths["phase_fold_secondary"], "secondary")
    _phase_outlier_fraction_plot(bundle.phase_fold, primary_modulus, paths["phase_fold_robust_outlier_fraction_primary"], "primary")
    _env_plot(pcsw, "humidity_pct", paths["environment_relationships_humidity_pct"])
    _env_plot(pcsw, "pressure_hPa", paths["environment_relationships_pressure_hpa"])
    _env_plot(pcsw, "temp_C" if "temp_C" in pcsw.columns else "temperature_C", paths["environment_relationships_temperature_c"])
    _allan_plot(bundle.allan_sampled, paths["allan_deviation"])
    if "synchronome_phase15_jitter_polar" in paths:
        _synchronome_phase15_jitter_polar(bundle.synchronome_phase15_jitter, paths["synchronome_phase15_jitter_polar"])


def _register_plot_paths(registry: ArtifactRegistry, specs: list[tuple[str, str, str, str, str]]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for namespace, slug, title, section, analysis_name in specs:
        artifact = registry.register_plot(
            namespace=namespace,
            slug=slug,
            title=title,
            section=section,
            source_files=["PCSW.csv"] if namespace == "pcsw" else ["PCPS.csv"] if namespace == "pcps" else ["PCSW.csv", "PCPS.csv"],
            analysis_name=analysis_name,
        )
        paths[slug] = registry.path(artifact)
    return paths


def _setup(title: str, ylabel: str) -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(8.6, 4.0))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    ax.set_ylabel(ylabel, fontsize=9, color=INK)
    ax.tick_params(axis="both", labelsize=8, colors=INK, length=3, width=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.6)
    ax.spines["bottom"].set_linewidth(0.6)
    ax.grid(axis="y", color=LIGHT, linewidth=0.45)
    return fig, ax


def _save(fig: plt.Figure, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def _empty(path: Path, title: str, message: str = "No sufficient data") -> None:
    fig, ax = _setup(title, "")
    ax.text(0.02, 0.55, message, transform=ax.transAxes, fontsize=10, color=MUTED)
    ax.set_xticks([])
    ax.set_yticks([])
    _save(fig, path)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _first_series(frame: pd.DataFrame, columns: list[str]) -> Optional[pd.Series]:
    for column in columns:
        if column in frame.columns:
            return frame[column]
    return None


def _series_plot(frame: pd.DataFrame, x_col: str, y_col: str, smooth_col: str | None, title: str, ylabel: str, path: Path) -> None:
    x = _numeric(frame, x_col)
    y = _numeric(frame, y_col)
    valid = x.notna() & y.notna()
    if not valid.any():
        _empty(path, title)
        return
    fig, ax = _setup(title, ylabel)
    ax.scatter(x[valid], y[valid], s=4, color=MUTED, alpha=0.5, linewidths=0)
    if smooth_col:
        smooth = _numeric(frame, smooth_col)
        smooth_valid = x.notna() & smooth.notna()
        if smooth_valid.any():
            ax.plot(x[smooth_valid], smooth[smooth_valid], color=ACCENT, linewidth=0.9)
    ax.set_xlabel("elapsed hours", fontsize=9, color=INK)
    _save(fig, path)


def _multi_series_plot(
    frame: pd.DataFrame,
    x_col: str,
    scatter_cols: list[tuple[str, str]],
    smooth_cols: list[tuple[str, str]],
    title: str,
    ylabel: str,
    path: Path,
) -> None:
    x = _numeric(frame, x_col)
    if frame.empty or x.empty:
        _empty(path, title)
        return
    fig, ax = _setup(title, ylabel)
    plotted = False
    for column, color in scatter_cols:
        y = _numeric(frame, column)
        valid = x.notna() & y.notna()
        if valid.any():
            ax.scatter(x[valid], y[valid], s=3, color=color, alpha=0.28, linewidths=0)
            plotted = True
    for column, color in smooth_cols:
        y = _numeric(frame, column)
        valid = x.notna() & y.notna()
        if valid.any():
            ax.plot(x[valid], y[valid], color=color, linewidth=0.8, label=column.replace("_med31", ""))
            plotted = True
    if not plotted:
        plt.close(fig)
        _empty(path, title)
        return
    ax.set_xlabel("elapsed hours", fontsize=9, color=INK)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, frameon=False, fontsize=7, loc="best")
    _save(fig, path)


def _histogram(values: Optional[pd.Series], title: str, xlabel: str, path: Path) -> None:
    if values is None:
        _empty(path, title)
        return
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        _empty(path, title)
        return
    fig, ax = _setup(title, "count")
    unique = numeric.nunique()
    if unique <= 40 and np.allclose(numeric, np.round(numeric)):
        counts = numeric.value_counts().sort_index()
        ax.bar(counts.index.astype(float), counts.values, color=MUTED, width=0.8, linewidth=0)
    else:
        ax.hist(numeric, bins=80, color=MUTED, edgecolor="white", linewidth=0.2)
        ax.axvline(numeric.median(), color=ACCENT, linewidth=0.9)
    ax.set_xlabel(xlabel, fontsize=9, color=INK)
    _save(fig, path)


def _autocorrelation(values: Optional[pd.Series], path: Path, max_lag: int = 300) -> None:
    if values is None:
        _empty(path, "Residual autocorrelation")
        return
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if len(numeric) < 8:
        _empty(path, "Residual autocorrelation")
        return
    arr = numeric.to_numpy(dtype=float)
    arr = arr - np.mean(arr)
    max_lag = min(max_lag, max(1, len(arr) // 4))
    corr = np.correlate(arr, arr, mode="full")[len(arr) - 1 : len(arr) + max_lag]
    if corr[0] != 0:
        corr = corr / corr[0]
    fig, ax = _setup("Residual autocorrelation", "autocorrelation")
    ax.plot(np.arange(len(corr)), corr, color=INK, linewidth=0.75)
    ax.set_xlabel("lag (rows)", fontsize=9, color=INK)
    _save(fig, path)


def _fft_plot(components: pd.DataFrame, expected_lines: pd.DataFrame, path: Path, x_axis: str = "period") -> None:
    if components.empty:
        _empty(path, "Residual FFT")
        return
    fig, ax = _setup("Residual FFT", "relative amplitude to strongest peak")
    colors = {"full_resid_us": INK, "full_resid_us_phase_depatterned": BLUE}
    x_col = "period_s" if x_axis == "period" and "period_s" in components.columns else "frequency_hz" if "frequency_hz" in components.columns else "frequency_Hz"
    y_col = "relative_amplitude_to_max" if "relative_amplitude_to_max" in components.columns else "relative_amplitude"
    expected_x_col = "period_s" if x_col == "period_s" else "frequency_hz"
    label_candidates: list[tuple[float, float, str, str]] = []
    expected_reference = expected_lines.copy() if expected_lines is not None else pd.DataFrame()
    if not expected_reference.empty and expected_x_col in expected_reference.columns:
        expected_reference[expected_x_col] = pd.to_numeric(expected_reference[expected_x_col], errors="coerce")
        expected_reference = expected_reference.dropna(subset=[expected_x_col]).sort_values(expected_x_col)
        for _, row in expected_reference.head(8).iterrows():
            ax.axvline(row[expected_x_col], color=LIGHT, linewidth=0.7, zorder=0)
    for series, group in components.groupby("series") if "series" in components.columns else [("full_resid_us", components)]:
        group = group.copy()
        group[x_col] = pd.to_numeric(group[x_col], errors="coerce")
        group[y_col] = pd.to_numeric(group[y_col], errors="coerce")
        group = group.dropna(subset=[x_col, y_col])
        if group.empty:
            continue
        color = colors.get(str(series), INK)
        expected = group["expected_match"].astype(bool) if "expected_match" in group.columns else pd.Series(False, index=group.index)
        ax.vlines(group[x_col], 0, group[y_col], color=color, linewidth=0.6, alpha=0.45)
        ax.scatter(group.loc[expected, x_col], group.loc[expected, y_col], s=16, color=color, marker="o", linewidths=0, label=f"{series} expected")
        ax.scatter(group.loc[~expected, x_col], group.loc[~expected, y_col], s=22, facecolors="none", edgecolors=ACCENT, marker="o", linewidths=0.9, label=f"{series} candidate")
        for _, row in _fft_label_rows(group, x_col, y_col).iterrows():
            label_candidates.append((float(row[x_col]), float(row[y_col]), _fft_label_text(row, x_col), color))
    if x_col == "period_s":
        ax.set_xlabel("period (s)", fontsize=9, color=INK)
        if components["period_s"].max() / max(float(components["period_s"].min()), 1e-9) > 10:
            ax.set_xscale("log")
        ax.invert_xaxis()
    else:
        ax.set_xlabel("frequency (Hz)", fontsize=9, color=INK)
    handles, labels = ax.get_legend_handles_labels()
    dedup = dict(zip(labels, handles))
    if dedup:
        ax.legend(dedup.values(), dedup.keys(), fontsize=7, frameon=False)
    for index, (x_value, y_value, text, color) in enumerate(_dedupe_fft_labels(label_candidates)):
        ax.annotate(
            text,
            (x_value, y_value),
            xytext=(10, 12 + 11 * (index % 3)),
            textcoords="offset points",
            fontsize=6.5,
            color=MUTED,
            arrowprops={"arrowstyle": "-", "color": color, "linewidth": 0.45, "alpha": 0.7},
            ha="left",
            va="bottom",
        )
    _save(fig, path)


def _fft_label_rows(group: pd.DataFrame, x_col: str, y_col: str) -> pd.DataFrame:
    if group.empty:
        return group
    working = group.copy()
    expected = working["expected_match"].astype(bool) if "expected_match" in working.columns else pd.Series(False, index=working.index)
    strong = pd.to_numeric(working[y_col], errors="coerce") >= 0.25
    selected = working.loc[expected & strong].sort_values(y_col, ascending=False).head(3)
    if len(selected) < 3:
        filler = working.loc[strong & ~working.index.isin(selected.index)].sort_values(y_col, ascending=False).head(3 - len(selected))
        selected = pd.concat([selected, filler])
    return selected.head(3)


def _dedupe_fft_labels(candidates: list[tuple[float, float, str, str]]) -> list[tuple[float, float, str, str]]:
    result: list[tuple[float, float, str, str]] = []
    seen: set[str] = set()
    for item in sorted(candidates, key=lambda value: value[1], reverse=True):
        key = item[2]
        if key in seen:
            continue
        result.append(item)
        seen.add(key)
        if len(result) >= 5:
            break
    return result


def _fft_label_text(row: pd.Series, x_col: str) -> str:
    family = str(row.get("expected_family", ""))
    order = row.get("expected_order")
    period = row.get("expected_period_s", row.get("period_s"))
    if pd.isna(period):
        period = row.get("period_s")
    if family == "impulse_cycle" and pd.notna(order):
        order_int = int(order)
        if order_int == 1:
            return "Impulse period (~30 s)"
        return f"{order_int}× impulse"
    if pd.notna(period):
        period_value = float(period)
        if 82_000 <= period_value <= 91_000:
            return "Daily"
        if period_value >= 3_600:
            return "Thermal / environmental"
    label = str(row.get("expected_label") or "")
    if label and label != "nan":
        return label
    return f"{float(row[x_col]):.3g} s" if x_col == "period_s" else f"{float(row[x_col]):.3g} Hz"


def _phase_plot(phase_fold: pd.DataFrame, modulus: Optional[int], path: Path, label: str = "profile") -> None:
    if modulus is None:
        _empty(path, f"Phase fold {label} modulus", "Not applicable: no profile phase modulus configured")
        return
    subset = phase_fold.loc[phase_fold["modulus"] == modulus] if "modulus" in phase_fold.columns else pd.DataFrame()
    if subset.empty:
        _empty(path, f"Phase fold mod {modulus}")
        return
    fig, ax = _setup(f"Phase fold mod {modulus} - structure_diagnostic_mask", "median full residual (us)")
    subset = subset.sort_values("phase")
    ok = subset["status"].eq("ok") if "status" in subset.columns else subset["median"].notna()
    ax.plot(subset["phase"], subset["median"], marker="o", markersize=3, color=INK, linewidth=0.8)
    if "warning" in subset.columns:
        removed = subset["warning"].astype(str).str.len().gt(0)
        if removed.any():
            ax.scatter(subset.loc[removed, "phase"], subset.loc[removed, "median"], marker="s", s=28, facecolors="none", edgecolors=ACCENT, linewidths=0.9, label="robust mask would remove bin")
    if (~ok).any():
        ymin, ymax = ax.get_ylim()
        marker_y = ymin + (ymax - ymin) * 0.04
        ax.scatter(subset.loc[~ok, "phase"], [marker_y] * int((~ok).sum()), marker="x", s=18, color=MUTED, linewidths=0.8)
    ax.axhline(0, color=LIGHT, linewidth=0.6)
    ax.set_xlim(-0.5, modulus - 0.5)
    ax.set_xticks(np.arange(0, modulus, max(1, modulus // 15)))
    ax.set_xlabel("phase", fontsize=9, color=INK)
    ax.text(0.01, 0.98, "Rows: structure_diagnostic_mask; robust outliers are not removed", transform=ax.transAxes, va="top", fontsize=7, color=MUTED)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, frameon=False, fontsize=7, loc="best")
    _save(fig, path)


def _phase_outlier_fraction_plot(phase_fold: pd.DataFrame, modulus: Optional[int], path: Path, label: str = "profile") -> None:
    if modulus is None:
        _empty(path, f"Phase fold robust outlier fraction {label} modulus", "Not applicable: no profile phase modulus configured")
        return
    subset = phase_fold.loc[phase_fold["modulus"] == modulus] if "modulus" in phase_fold.columns else pd.DataFrame()
    if subset.empty or "robust_outlier_fraction" not in subset.columns:
        _empty(path, f"Phase fold robust outlier fraction mod {modulus}")
        return
    subset = subset.sort_values("phase")
    fraction = pd.to_numeric(subset["robust_outlier_fraction"], errors="coerce")
    if fraction.dropna().empty:
        _empty(path, f"Phase fold robust outlier fraction mod {modulus}")
        return
    fig, ax = _setup(f"Phase fold robust outlier fraction mod {modulus}", "fraction")
    ax.bar(subset["phase"], fraction.fillna(0.0), color=MUTED, width=0.75, linewidth=0)
    ax.set_ylim(0, min(1.0, max(0.05, float(fraction.max()) * 1.15)))
    ax.set_xlim(-0.5, modulus - 0.5)
    ax.set_xticks(np.arange(0, modulus, max(1, modulus // 15)))
    ax.set_xlabel("phase", fontsize=9, color=INK)
    ax.text(0.01, 0.98, "Diagnostic only: robust_summary_mask count divided by structure_diagnostic_mask rows", transform=ax.transAxes, va="top", fontsize=7, color=MUTED)
    _save(fig, path)


def _synchronome_phase15_jitter_polar(frame: pd.DataFrame, path: Path) -> None:
    if frame.empty or "phase" not in frame.columns or "residual_robust_sigma_us" not in frame.columns:
        _empty(path, "Synchronome phase-15 jitter localisation")
        return
    work = frame.copy()
    work["phase"] = pd.to_numeric(work["phase"], errors="coerce")
    work["sigma"] = pd.to_numeric(work["residual_robust_sigma_us"], errors="coerce")
    work = work.dropna(subset=["phase", "sigma"]).sort_values("phase")
    if work.empty:
        _empty(path, "Synchronome phase-15 jitter localisation")
        return
    phases = work["phase"].astype(int).to_numpy()
    sigma = work["sigma"].to_numpy(dtype=float)
    theta = phases / 15.0 * 2.0 * np.pi
    width = 2.0 * np.pi / 15.0 * 0.78

    fig = plt.figure(figsize=(6.2, 5.2))
    fig.patch.set_facecolor("white")
    ax = fig.add_subplot(111, projection="polar")
    ax.set_title("Synchronome phase-15 jitter localisation", loc="left", fontsize=11, color=INK, pad=14)
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    hotspot_index = int(np.nanargmax(sigma))
    colors = [MUTED] * len(sigma)
    colors[hotspot_index] = ACCENT
    ax.bar(theta, sigma, width=width, bottom=0.0, color=colors, alpha=0.82, linewidth=0)
    baseline = pd.to_numeric(work.get("baseline_robust_sigma_us"), errors="coerce")
    if baseline.notna().any():
        base = float(baseline.dropna().iloc[0])
        angles = np.linspace(0, 2.0 * np.pi, 240)
        ax.plot(angles, np.full_like(angles, base), color=BLUE, linewidth=0.9, alpha=0.8, label="baseline")
    ax.set_xticks(np.arange(15) / 15.0 * 2.0 * np.pi)
    ax.set_xticklabels([str(index) for index in range(15)], fontsize=8, color=INK)
    ax.tick_params(axis="y", labelsize=7, colors=MUTED)
    ax.grid(color=LIGHT, linewidth=0.55)
    ax.spines["polar"].set_color(LIGHT)
    ax.annotate(
        f"phase {int(phases[hotspot_index])}",
        (theta[hotspot_index], sigma[hotspot_index]),
        xytext=(-28, -8),
        textcoords="offset points",
        fontsize=8,
        color=ACCENT,
        arrowprops={"arrowstyle": "-", "color": ACCENT, "linewidth": 0.5, "alpha": 0.8},
        ha="right",
        va="center",
    )
    if ax.get_legend_handles_labels()[0]:
        ax.legend(frameon=False, fontsize=7, loc="upper right", bbox_to_anchor=(1.16, 1.08))
    _save(fig, path)


def _env_plot(frame: pd.DataFrame, env_col: str, path: Path) -> None:
    title = f"Residual vs {env_col}"
    if frame.empty or env_col not in frame.columns or "full_resid_us" not in frame.columns:
        _empty(path, title)
        return
    x = pd.to_numeric(frame[env_col], errors="coerce")
    y = pd.to_numeric(frame["full_resid_us"], errors="coerce")
    valid = x.notna() & y.notna()
    if valid.sum() < 3:
        _empty(path, title)
        return
    fig, ax = _setup(title, "full residual (us)")
    ax.scatter(x[valid], y[valid], s=5, color=MUTED, alpha=0.45, linewidths=0)
    ax.set_xlabel(env_col, fontsize=9, color=INK)
    _save(fig, path)


def _allan_plot(allan: pd.DataFrame, path: Path, title: str = "Allan deviation") -> None:
    if allan.empty:
        _empty(path, title)
        return
    fig, ax = _setup(title, "ADEV fractional")
    ax.loglog(allan["tau_s"], allan["adev_fractional"], marker="o", markersize=3, color=INK, linewidth=0.8)
    ax.set_xlabel("tau (s)", fontsize=9, color=INK)
    _save(fig, path)


def _pcps_lock_plot(frame: pd.DataFrame, path: Path) -> None:
    if frame.empty or "gps_status" not in frame.columns:
        _empty(path, "PCPS GPS lock state")
        return
    _series_plot(frame, "pps_t_hr", "gps_status", None, "PCPS GPS lock state", "gps_status", path)


def _pcps_r_ppm_plot(frame: pd.DataFrame, path: Path) -> None:
    column = "r_ppm" if "r_ppm" in frame.columns else "pps_raw_error_ppm" if "pps_raw_error_ppm" in frame.columns else "offline_pps_adjusted_residual_ppm"
    if frame.empty or column not in frame.columns:
        _empty(path, "PCPS oscillator correction", "No r_ppm or residual ppm column")
        return
    _series_plot(frame, "pps_t_hr", column, None, "PCPS oscillator correction / residual ppm", column, path)


def _pcps_j_ticks_plot(frame: pd.DataFrame, path: Path) -> None:
    if frame.empty or "j_ticks" not in frame.columns:
        _empty(path, "PCPS PPS jitter", "No j_ticks column")
        return
    _series_plot(frame, "pps_t_hr", "j_ticks", None, "PCPS PPS jitter", "j_ticks", path)


def _pcps_latency_plot(frame: pd.DataFrame, outliers: pd.DataFrame, path: Path) -> None:
    if frame.empty or "latency16" not in frame.columns:
        _empty(path, "PCPS ISR/capture latency", "No latency16 column")
        return
    x = _numeric(frame, "pps_t_hr")
    y = _numeric(frame, "latency16")
    valid = x.notna() & y.notna()
    if not valid.any():
        _empty(path, "PCPS ISR/capture latency")
        return
    ordinary_limit = y[valid].quantile(0.995)
    fig, ax = _setup("PCPS ISR/capture latency", "latency cycles")
    ordinary = valid & (y <= ordinary_limit)
    ax.scatter(x[ordinary], y[ordinary], s=4, color=MUTED, alpha=0.5, linewidths=0)
    if (~ordinary & valid).any():
        ax.scatter(x[~ordinary & valid], np.minimum(y[~ordinary & valid], ordinary_limit), s=12, color=ACCENT, alpha=0.8, linewidths=0, label="clipped high latency")
    if not outliers.empty and {"pps_t_hr", "latency_cycles"}.issubset(outliers.columns):
        ox = pd.to_numeric(outliers["pps_t_hr"], errors="coerce")
        oy = pd.to_numeric(outliers["latency_cycles"], errors="coerce")
        outlier_valid = ox.notna() & oy.notna()
        if outlier_valid.any():
            ax.scatter(ox[outlier_valid], np.minimum(oy[outlier_valid], ordinary_limit), marker="x", s=24, color=ACCENT, linewidths=0.9, label="detected outlier")
    ax.set_ylim(bottom=max(0, float(y[ordinary].min()) - 2.0) if ordinary.any() else None, top=float(ordinary_limit) * 1.05 if np.isfinite(ordinary_limit) else None)
    ax.set_xlabel("elapsed hours", fontsize=9, color=INK)
    ax.text(0.01, 0.98, "high values clipped to keep ordinary latency structure visible", transform=ax.transAxes, va="top", fontsize=7, color=MUTED)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, frameon=False, fontsize=7, loc="best")
    _save(fig, path)


def _pcps_cap16_plot(bins: pd.DataFrame, path: Path) -> None:
    if bins.empty or "count" not in bins.columns:
        _empty(path, "PCPS cap16 phase coverage", "No cap16 histogram bins")
        return
    counts = pd.to_numeric(bins["count"], errors="coerce")
    expected = pd.to_numeric(bins.get("expected_count"), errors="coerce")
    if counts.dropna().empty:
        _empty(path, "PCPS cap16 phase coverage")
        return
    x = pd.to_numeric(bins["bin_index"], errors="coerce") if "bin_index" in bins.columns else pd.Series(np.arange(len(bins)))
    fig, ax = _setup("PCPS cap16 phase coverage sanity check", "count")
    ax.bar(x, counts, width=1.0, color=MUTED, linewidth=0)
    if expected.notna().any():
        ax.axhline(float(expected.dropna().iloc[0]), color=ACCENT, linewidth=0.8, label="expected per bin")
        ax.legend(frameon=False, fontsize=7, loc="best")
    ax.set_xlabel("cap16 bin across 0..65535", fontsize=9, color=INK)
    ax.text(0.01, 0.98, "approximate uniformity is expected for asynchronous PPS captures", transform=ax.transAxes, va="top", fontsize=7, color=MUTED)
    _save(fig, path)
