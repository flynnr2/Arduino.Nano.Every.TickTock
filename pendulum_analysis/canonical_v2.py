"""Canonical v2 analysis bundle tables and Markdown report."""

from __future__ import annotations

import json
import hashlib
import platform
import re
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from . import __version__
from .artifacts import Artifact, ArtifactRegistry
from .config import AnalysisConfig
from .filters import analysis_mask, dropped_mask, mask_audit_counts, robust_summary_mask, structure_diagnostic_mask, valid_mask
from .longrun import (
    build_change_point_candidates,
    build_daily_summary,
    build_diurnal_harmonic_fit,
    build_environmental_lag_correlations,
    build_investigation_next,
    build_rolling_allan,
    build_rolling_stability,
    build_time_of_day_fold,
)
from .pcps import PcpsAnalysis
from .pcps_health import PcpsHealthResult, analyze_pcps_timebase_health
from .planner import AnalysisPlan, build_analysis_plan, build_dataset_metadata
from .profile import AnalysisCapabilities, ExpectedPeriod, ProfileSelection, derive_capabilities, get_profile, select_clock_profile
from .results import AnalysisResult
from .schema import CANONICAL_PPS, CANONICAL_SWING, DERIVED, FLAGS, INPUT
from .stats import describe_series
from .timeline import unwrap_u32


STAT_COLUMNS = [
    "metric",
    "n",
    "mean",
    "median",
    "std",
    "MAD",
    "robust_sigma",
    "min",
    "max",
    "IQR",
    "p01",
    "p05",
    "p25",
    "p75",
    "p95",
    "p99",
    "unique_values",
    "top_values",
    "nonzero_fraction",
    "spread_note",
]

PHASE_FOLD_COLUMNS = [
    "phase",
    "total_rows",
    "valid_rows",
    "primary_analysis_rows",
    "analysis_rows",
    "robust_summary_rows",
    "robust_outlier_rows",
    "robust_outlier_fraction",
    "dropped_fraction",
    "pps_status_breakdown",
    "n",
    "count",
    "median",
    "mean",
    "std",
    "half_asymmetry_ms_median",
    "modulus",
    "mask_name",
    "status",
    "warning",
]
ENV_CORR_COLUMNS = ["metric", "env_var", "window_name", "lag_s", "method", "correlation", "n"]
FFT_COLUMNS = [
    "series",
    "preprocessing",
    "rank",
    "frequency_hz",
    "frequency_Hz",
    "period_s",
    "amplitude",
    "relative_amplitude_to_max",
    "relative_amplitude",
    "power",
    "relative_power_to_max",
    "power_fraction_if_available",
    "cluster_size",
    "cluster_frequency_span_hz",
    "cluster_period_span_s",
    "representative_frequency_hz",
    "representative_period_s",
    "expected_match",
    "expected_label",
    "expected_family",
    "expected_order",
    "expected_period_s",
    "expected_frequency_hz",
    "match_error_pct",
    "match_error_bins",
    "match_source",
    "interpretation",
    "investigation_priority",
    "structure_period_s",
    "structure_label",
]
SYNCHRONOME_JITTER_SUMMARY_COLUMNS = ["metric", "value", "unit", "note"]
SYNCHRONOME_PHASE15_JITTER_COLUMNS = [
    "phase",
    "n",
    "residual_rms_us",
    "residual_robust_sigma_us",
    "tick_residual_robust_sigma_us",
    "tock_residual_robust_sigma_us",
    "half_cycle_hotspot_ratio",
    "baseline_robust_sigma_us",
    "hotspot_ratio",
]


@dataclass(frozen=True)
class CanonicalV2Bundle:
    pcsw_sampled: pd.DataFrame
    pcps_sampled: pd.DataFrame
    statistics: pd.DataFrame
    phase_fold: pd.DataFrame
    environmental: pd.DataFrame
    fft_components: pd.DataFrame
    fft_expected: pd.DataFrame
    pcps_health: PcpsHealthResult
    allan_sampled: pd.DataFrame
    quality: Dict[str, Any]
    profile_selection: ProfileSelection
    capabilities: AnalysisCapabilities
    analysis_plan: AnalysisPlan
    time_of_day_fold: pd.DataFrame
    daily_summary: pd.DataFrame
    environmental_lag: pd.DataFrame
    rolling_stability: pd.DataFrame
    rolling_allan: pd.DataFrame
    diurnal_harmonic: pd.DataFrame
    change_points: pd.DataFrame
    synchronome_jitter_summary: pd.DataFrame
    synchronome_phase15_jitter: pd.DataFrame
    investigation_next: List[str]
    analysis_results: List[AnalysisResult] | None = None


def build_bundle(
    data: pd.DataFrame,
    pcps: PcpsAnalysis,
    allan: pd.DataFrame,
    discovered: Dict[str, Optional[Path]],
    config: AnalysisConfig,
) -> CanonicalV2Bundle:
    pcsw_full = prepare_pcsw_frame(data, config, pcps.intervals)
    pcps_full = prepare_pcps_frame(pcps.intervals, config)
    inferred = _inferred_period(pcsw_full)
    profile_selection = select_clock_profile(
        config.clock_profile,
        pcsw_full,
        profile_file=Path(config.clock_profile_file) if config.clock_profile_file else None,
        profile_dir=Path(config.clock_profile_dir) if config.clock_profile_dir else None,
    )
    duration_days = max(_duration_hours(pcsw_full, "t_hr") or 0.0, _duration_hours(pcps_full, "pps_t_hr") or 0.0) / 24.0
    capabilities = derive_capabilities(profile_selection.selected, pcsw_full, pcps_full, duration_days)

    statistics = build_statistics_table(pcsw_full, pcps_full)
    phase_moduli = profile_selection.selected.phase.expected_moduli if capabilities.supports_phase_fold_analysis else []
    phase_fold = build_phase_fold_summary(pcsw_full, phase_moduli)
    environmental = build_environmental_correlations(pcsw_full, config)
    expected_fft = _expected_fft_periods(profile_selection, inferred, config)
    depattern_modulus = max(phase_moduli) if phase_moduli else None
    fft_components = build_fft_components(
        pcsw_full,
        inferred,
        config,
        expected_fft,
        capabilities.supports_expected_harmonic_labels,
        depattern_modulus=depattern_modulus,
        count=config.fft_top_n,
    )
    fft_expected = build_expected_fft_table(expected_fft, profile_selection.selected.name)
    pcps_health = analyze_pcps_timebase_health(pcps_full, config)
    allan_sampled = build_allan_sampled(allan, config)
    quality = build_quality_summary_v2(pcsw_full, pcps_full, pcps, discovered, config, inferred)
    quality["pps_timescale"] = pcsw_full.attrs.get("pps_timescale", {"status": "unavailable"})
    quality["PCPS_timebase_health_warnings"] = pcps_health.warnings
    quality["clock_profile"] = profile_selection.to_dict()
    quality["capabilities"] = capabilities.to_dict()
    quality["fft_x_axis"] = config.fft_x_axis
    dataset_metadata = build_dataset_metadata(pcsw_full, pcps_full, quality)
    analysis_plan = build_analysis_plan(dataset_metadata, profile_selection.selected, capabilities, config, config.clock_profile)
    quality["dataset_metadata"] = dataset_metadata.to_dict()
    quality["analysis_plan"] = analysis_plan.to_dict()

    duration_days = float(dataset_metadata.duration_days or 0.0)
    time_of_day = build_time_of_day_fold(pcsw_full) if analysis_plan.should_run("time_of_day_fold") else _not_generated_frame("time_of_day_fold", analysis_plan, duration_days)
    daily = build_daily_summary(pcsw_full) if analysis_plan.should_run("daily_summary") else _not_generated_frame("daily_summary", analysis_plan, duration_days)
    env_lag = build_environmental_lag_correlations(pcsw_full, config) if analysis_plan.should_run("environmental_lag_correlations") else _not_generated_frame("environmental_lag_correlations", analysis_plan, duration_days)
    rolling_stability = build_rolling_stability(pcsw_full, config) if analysis_plan.should_run("rolling_stability") else _not_generated_frame("rolling_stability", analysis_plan, duration_days)
    rolling_allan = build_rolling_allan(pcsw_full, config) if analysis_plan.should_run("rolling_allan_deviation") else _not_generated_frame("rolling_allan_deviation", analysis_plan, duration_days)
    diurnal_harmonic = build_diurnal_harmonic_fit(pcsw_full) if analysis_plan.should_run("diurnal_harmonic_fit") else _not_generated_frame("diurnal_harmonic_fit", analysis_plan, duration_days)
    change_points = build_change_point_candidates(pcsw_full) if analysis_plan.should_run("change_point_detection") else _not_generated_frame("change_point_detection", analysis_plan, duration_days)
    synchronome_summary, synchronome_phase15 = build_synchronome_jitter_localisation(pcsw_full, profile_selection.selected.name == "synchronome")

    bundle = CanonicalV2Bundle(
        pcsw_sampled=_sample_frame(pcsw_full, config.max_points_per_plot, phase_mod=profile_selection.selected.phase.primary_modulus),
        pcps_sampled=_sample_frame(pcps_full, config.max_points_per_plot, phase_mod=profile_selection.selected.phase.primary_modulus),
        statistics=statistics,
        phase_fold=phase_fold,
        environmental=environmental,
        fft_components=fft_components,
        fft_expected=fft_expected,
        pcps_health=pcps_health,
        allan_sampled=allan_sampled,
        quality=quality,
        profile_selection=profile_selection,
        capabilities=capabilities,
        analysis_plan=analysis_plan,
        time_of_day_fold=time_of_day,
        daily_summary=daily,
        environmental_lag=env_lag,
        rolling_stability=rolling_stability,
        rolling_allan=rolling_allan,
        diurnal_harmonic=diurnal_harmonic,
        change_points=change_points,
        synchronome_jitter_summary=synchronome_summary,
        synchronome_phase15_jitter=synchronome_phase15,
        investigation_next=[],
        analysis_results=[],
    )
    object.__setattr__(bundle, "investigation_next", build_investigation_next(bundle) if analysis_plan.should_run("what_to_investigate_next") else [])
    object.__setattr__(bundle, "analysis_results", build_analysis_results(bundle))
    return bundle


def build_analysis_results(bundle: CanonicalV2Bundle) -> List[AnalysisResult]:
    table_by_analysis = {
        "basic_interval_statistics": {"statistics": bundle.statistics},
        "phase_fold": {"phase_fold": bundle.phase_fold},
        "environmental_correlations": {"environmental": bundle.environmental},
        "fft_spectrum": {"fft_components": bundle.fft_components, "fft_expected": bundle.fft_expected},
        "analysis_plan": {"analysis_plan": bundle.analysis_plan.to_frame()},
        "time_of_day_fold": {"time_of_day_fold": bundle.time_of_day_fold},
        "daily_summary": {"daily_summary": bundle.daily_summary},
        "environmental_lag_correlations": {"environmental_lag": bundle.environmental_lag},
        "rolling_stability": {"rolling_stability": bundle.rolling_stability},
        "rolling_allan_deviation": {"rolling_allan": bundle.rolling_allan},
        "diurnal_harmonic_fit": {"diurnal_harmonic": bundle.diurnal_harmonic},
        "change_point_detection": {"change_points": bundle.change_points},
        "pcps_timebase_health": {
            "summary": bundle.pcps_health.summary,
            "allan": bundle.pcps_health.allan,
            "cap16_summary": bundle.pcps_health.cap16_summary,
            "latency_summary": bundle.pcps_health.latency_summary,
        },
        "allan_deviation": {"allan_sampled": bundle.allan_sampled},
    }
    if bundle.profile_selection.selected.name == "synchronome":
        table_by_analysis["synchronome_jitter_localisation"] = {
            "synchronome_jitter_summary": bundle.synchronome_jitter_summary,
            "synchronome_phase15_jitter": bundle.synchronome_phase15_jitter,
        }
    results: List[AnalysisResult] = []
    for planned in bundle.analysis_plan.analyses:
        tables = table_by_analysis.get(planned.name, {})
        table_rows = {name: int(len(frame)) for name, frame in tables.items()}
        warnings = [planned.reason] if planned.status in {"SKIP", "WARN"} else []
        if planned.name == "pcps_timebase_health":
            warnings.extend(bundle.pcps_health.warnings)
        results.append(
            AnalysisResult(
                name=planned.name,
                status="RUN" if planned.status == "FORCED" else planned.status,
                tier=planned.tier,
                summary={"reason": planned.reason, "table_rows": table_rows},
                tables=tables,
                warnings=warnings,
                provenance={
                    "profile": bundle.profile_selection.selected.name,
                    "configuration_hash": bundle.quality.get("configuration_hash"),
                    "required_inputs": planned.required_inputs,
                    "required_capabilities": planned.required_capabilities,
                },
            )
        )
    return results


def _not_generated_frame(name: str, analysis_plan: AnalysisPlan, current_duration_days: float) -> pd.DataFrame:
    reason = "analysis disabled or requirements not met"
    for planned in analysis_plan.analyses:
        if planned.name == name:
            reason = planned.reason
            break
    return pd.DataFrame(
        [
            {
                "status": "not_generated",
                "analysis": name,
                "reason": reason,
                "current_duration_days": current_duration_days,
            }
        ]
    )


def write_bundle(bundle: CanonicalV2Bundle, registry: ArtifactRegistry) -> None:
    _write_csv(
        bundle.pcsw_sampled,
        registry,
        namespace="pcsw",
        slug="derived_intervals_sampled_v2",
        title="Derived PCSW intervals sampled v2",
        section="PCSW pendulum timing summary",
        source_files=["PCSW.csv"],
        analysis_name="basic_residuals",
    )
    for frame, namespace, slug, title, section, analysis_name in [
        (_swing_statistics(bundle.statistics), "combined", "statistics_summary_v2", "Statistics summary v2", "PCSW pendulum timing summary", "basic_interval_statistics"),
        (bundle.phase_fold, "pcsw", "phase_fold_summary_v2", "Phase fold summary v2", "Residual/spectral analysis", "phase_fold"),
        (bundle.environmental, "combined", "environmental_correlations_v2", "Environmental correlations v2", "Environmental analysis", "environmental_correlations"),
        (bundle.fft_components, "pcsw", "dominant_fft_components_v2", "Dominant FFT components v2", "Residual/spectral analysis", "fft_spectrum"),
        (bundle.fft_expected, "pcsw", "expected_fft_lines_v2", "Expected FFT reference lines v2", "Residual/spectral analysis", "fft_spectrum"),
        (_fft_series(bundle.fft_components, "full_resid_us"), "pcsw", "residual_fft_raw_peaks", "Raw residual FFT peaks", "Residual/spectral analysis", "fft_spectrum"),
        (_fft_series(bundle.fft_components, "full_resid_us_phase_depatterned"), "pcsw", "residual_fft_phase_depatterned_peaks", "Phase-depatterned residual FFT peaks", "Residual/spectral analysis", "fft_spectrum"),
        (bundle.analysis_plan.to_frame(), "combined", "analysis_plan_v2", "Analysis plan v2", "Analysis plan", "analysis_plan"),
        (bundle.time_of_day_fold, "pcsw", "time_of_day_fold_v2", "Time of day fold v2", "Long-run triage", "time_of_day_fold"),
        (bundle.daily_summary, "pcsw", "daily_summary_v2", "Daily summary v2", "Long-run triage", "daily_summary"),
        (bundle.environmental_lag, "combined", "environmental_lag_correlations_v2", "Environmental lag correlations v2", "Environmental analysis", "environmental_lag_correlations"),
        (bundle.rolling_stability, "pcsw", "rolling_stability_v2", "Rolling stability v2", "Long-run triage", "rolling_stability"),
        (bundle.rolling_allan, "pcsw", "rolling_allan_deviation_v2", "Rolling Allan deviation v2", "Long-run triage", "rolling_allan_deviation"),
        (bundle.diurnal_harmonic, "pcsw", "diurnal_harmonic_fit_v2", "Diurnal harmonic fit v2", "Long-run triage", "diurnal_harmonic_fit"),
        (bundle.change_points, "pcsw", "change_point_candidates_v2", "Change point candidates v2", "Long-run triage", "change_point_detection"),
        (bundle.allan_sampled, "pcsw", "allan_deviation_sampled", "Allan deviation sampled", "Long-term stability", "allan_deviation"),
    ]:
        _write_csv(
            frame,
            registry,
            namespace=namespace,
            slug=slug,
            title=title,
            section=section,
            source_files=["PCSW.csv"] if namespace == "pcsw" else ["PCPS.csv"] if namespace == "pcps" else ["PCSW.csv", "PCPS.csv"],
            analysis_name=analysis_name,
        )
    if bundle.profile_selection.selected.name == "synchronome":
        for frame, slug, title in [
            (bundle.synchronome_jitter_summary, "synchronome_jitter_summary", "Synchronome jitter localisation summary"),
            (bundle.synchronome_phase15_jitter, "synchronome_phase15_jitter", "Synchronome phase-15 jitter localisation"),
        ]:
            _write_csv(
                frame,
                registry,
                namespace="pcsw",
                slug=slug,
                title=title,
                section="Synchronome impulse/jitter localisation",
                source_files=["PCSW.csv"],
                analysis_name="synchronome_jitter_localisation",
            )
    quality_artifact = registry.register_summary(
        namespace="combined",
        slug="data_quality_summary_v2",
        title="Data quality summary v2",
        section="Data quality details",
        source_files=["PCSW.csv", "PCPS.csv"],
        analysis_name="data_quality",
    )
    registry.path(quality_artifact).write_text(json.dumps(_swing_quality(bundle.quality), indent=2, sort_keys=True), encoding="utf-8")
    results_artifact = registry.register_summary(
        namespace="combined",
        slug="analysis_results_v2",
        title="Analysis results v2",
        section="Analysis plan",
        source_files=["PCSW.csv", "PCPS.csv"],
        analysis_name="analysis_results",
    )
    registry.path(results_artifact).write_text(
        json.dumps(
            [result.to_manifest_entry() for result in (bundle.analysis_results or []) if result.name != "pcps_timebase_health"],
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _swing_statistics(frame: pd.DataFrame) -> pd.DataFrame:
    """Exclude legacy PPS statistics from canonical swing-facing artifacts."""
    if frame.empty or "metric" not in frame.columns:
        return frame
    return frame.loc[~frame["metric"].astype(str).str.startswith("PCPS_")].reset_index(drop=True)


def _swing_quality(quality: Dict[str, Any]) -> Dict[str, Any]:
    excluded = {
        "GPS_status_counts",
        "PCPS_drop_pps_sum",
        "PCPS_filter_counts",
        "PCPS_sequence",
        "PCPS_summary",
        "PCPS_timebase_health_warnings",
    }
    return {key: value for key, value in quality.items() if key not in excluded}


def prepare_pcsw_frame(data: pd.DataFrame, config: AnalysisConfig, pcps: pd.DataFrame | None = None) -> pd.DataFrame:
    result = data.copy()
    if "pps_period_s" not in result and all(f"edge{i}_tcb0" in result for i in range(5)):
        from .derive import add_pps_calibration
        result = add_pps_calibration(result, pd.DataFrame() if pcps is None else pcps, config)
    f_source = (pd.Series(config.nominal_hz, index=result.index) if CANONICAL_SWING.edge0_tcb0 in result
                else result[DERIVED.f_used_hz] if DERIVED.f_used_hz in result.columns else pd.Series(config.nominal_hz, index=result.index))
    f = pd.to_numeric(f_source, errors="coerce").replace(0, np.nan)
    for column in [DERIVED.open_A, DERIVED.block_A, DERIVED.open_B, DERIVED.block_B, DERIVED.half_A, DERIVED.half_B, DERIVED.full]:
        if column in result.columns:
            result[f"{column}_cycles"] = pd.to_numeric(result[column], errors="coerce")
            result[f"{column}_s"] = result[f"{column}_cycles"] / f

    if DERIVED.half_asymmetry in result.columns:
        result["half_asymmetry_ms"] = pd.to_numeric(result[DERIVED.half_asymmetry], errors="coerce") / f * 1000.0
    elif DERIVED.A_half_ms in result.columns:
        result["half_asymmetry_ms"] = result[DERIVED.A_half_ms]

    if CANONICAL_SWING.edge0_tcb0 in result.columns:
        edge0 = unwrap_u32(pd.to_numeric(result[CANONICAL_SWING.edge0_tcb0], errors="coerce"))
        result["row_delta_cycles"] = edge0.diff()
        first = edge0.iloc[0] if len(edge0) else 0.0
        result["t_s"] = (edge0 - first) / config.nominal_hz
    else:
        full_s = pd.to_numeric(result.get("full_s", result.get(DERIVED.period_s, np.nan)), errors="coerce").fillna(0.0)
        result["row_delta_cycles"] = pd.to_numeric(result.get(DERIVED.period_cycles, np.nan), errors="coerce")
        result["t_s"] = full_s.cumsum() - full_s.iloc[0] if len(full_s) else pd.Series(dtype=float)
    result["t_hr"] = result["t_s"] / 3600.0

    full_s = pd.to_numeric(result.get("full_s", result.get(DERIVED.period_s, np.nan)), errors="coerce")
    inferred = _inferred_period(result)
    result["full_med31_s"] = full_s.rolling(config.pps_residual_window, min_periods=max(3, config.pps_residual_window // 3), center=True).median()
    result["full_resid_us"] = (full_s - inferred) * 1e6
    for column in ["half_A_s", "half_B_s", "open_A_s", "block_A_s", "open_B_s", "block_B_s", "half_asymmetry_ms"]:
        if column in result.columns:
            result[f"{column}_med31"] = pd.to_numeric(result[column], errors="coerce").rolling(
                config.pps_residual_window,
                min_periods=max(3, config.pps_residual_window // 3),
                center=True,
            ).median()
    result["full_ppm_vs_inferred"] = (full_s / inferred - 1.0) * 1_000_000.0 if inferred and np.isfinite(inferred) else np.nan
    result["scale_prev31"] = 1.0
    result["full_pps_adj_s"] = full_s
    result["full_pps_adj_med31_s"] = result["full_med31_s"]
    result["full_pps_adj_ppm_vs_inferred"] = result["full_ppm_vs_inferred"]
    if "pps_period_s" in result:
        result["full_pps_adj_s"] = result.pps_period_s
        result["scale_prev31"] = result.pps_period_s / full_s
        result["full_pps_adj_med31_s"] = result.pps_period_s.rolling(
            config.pps_residual_window, min_periods=3, center=True).median()
        result["full_pps_adj_ppm_vs_inferred"] = (result.pps_period_s / inferred - 1)*1e6
        for name, a, b in [("open_A",0,1),("block_A",1,2),("half_A",0,2),
                           ("open_B",2,3),("block_B",3,4),("half_B",2,4)]:
            result[name+"_pps_s"] = result[f"pps_edge{b}_s"]-result[f"pps_edge{a}_s"]
    status, reason = detect_pps_adjustment_status(result)
    result["pps_adjustment_status"] = status
    result["pps_adjustment_reason"] = reason
    return result


def detect_pps_adjustment_status(pcsw: pd.DataFrame, tolerance_s: float = 1e-12) -> tuple[str, str]:
    scale = pd.to_numeric(pcsw.get("scale_prev31"), errors="coerce") if "scale_prev31" in pcsw.columns else pd.Series(dtype=float)
    raw = pd.to_numeric(pcsw.get("full_s"), errors="coerce") if "full_s" in pcsw.columns else pd.Series(dtype=float)
    adjusted = pd.to_numeric(pcsw.get("full_pps_adj_s"), errors="coerce") if "full_pps_adj_s" in pcsw.columns else pd.Series(dtype=float)
    if scale.empty or adjusted.empty:
        return "unavailable", "required PPS correction inputs missing"
    scale_finite = scale[np.isfinite(scale)]
    if scale_finite.empty:
        return "unavailable", "required PPS correction inputs missing"
    if np.allclose(scale_finite.to_numpy(dtype=float), 1.0, rtol=0.0, atol=1e-12):
        return "no_op", "scale_prev31 is 1.0 for all finite rows"
    paired = pd.concat([raw, adjusted], axis=1).dropna()
    if not paired.empty and np.allclose(paired.iloc[:, 0], paired.iloc[:, 1], rtol=0.0, atol=tolerance_s):
        return "no_op", "adjusted full-cycle intervals equal raw full_s within tolerance"
    return "available", "PPS adjustment differs from raw full_s"


def prepare_pcps_frame(pcps: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    columns = [
        CANONICAL_PPS.seq,
        CANONICAL_PPS.edge_tcb0,
        CANONICAL_PPS.gps_status,
        CANONICAL_PPS.holdover_age_ms,
        CANONICAL_PPS.cap16,
        CANONICAL_PPS.latency16,
        CANONICAL_PPS.now32,
        CANONICAL_PPS.drop_pps,
        CANONICAL_PPS.temperature_C,
        INPUT.humidity_pct,
        INPUT.pressure_hPa,
        "pps_interval_cycles",
        "seq_delta",
        "consecutive_seq",
        "pps_raw_error_cycles",
        "pps_raw_error_ns",
        "pps_raw_error_ppm",
        "pps_delta_cycles_for_time",
        "pps_t_s",
        "pps_t_hr",
        "valid_pps_interval",
        "expected_error_cycles_prev31",
        "offline_pps_adjusted_residual_cycles",
        "offline_pps_adjusted_residual_ns",
        "offline_pps_adjusted_residual_ppm",
    ]
    if pcps.empty:
        return pd.DataFrame(columns=columns)

    result = pcps.copy()
    result["consecutive_seq"] = result["seq_delta"] == 1
    result["pps_raw_error_cycles"] = result.get("raw_pps_error_cycles")
    result["pps_raw_error_ns"] = result.get("raw_pps_error_ns")
    result["pps_raw_error_ppm"] = result.get("raw_pps_error_ppm")
    result["offline_pps_adjusted_residual_cycles"] = result.get("offline_adjusted_residual_cycles")
    result["offline_pps_adjusted_residual_ns"] = result.get("offline_adjusted_residual_ns")
    result["offline_pps_adjusted_residual_ppm"] = result.get("offline_adjusted_residual_ppm")
    if CANONICAL_PPS.edge_tcb0 in result.columns:
        edge = unwrap_u32(pd.to_numeric(result[CANONICAL_PPS.edge_tcb0], errors="coerce"))
        result["pps_delta_cycles_for_time"] = edge.diff()
        first = edge.iloc[0] if len(edge) else 0.0
        result["pps_t_s"] = (edge - first) / config.nominal_hz
        result["pps_t_hr"] = result["pps_t_s"] / 3600.0
    else:
        result["pps_delta_cycles_for_time"] = result["pps_interval_cycles"]
        result["pps_t_s"] = pd.to_numeric(result["pps_interval_cycles"], errors="coerce").fillna(0.0).cumsum() / config.nominal_hz
        result["pps_t_hr"] = result["pps_t_s"] / 3600.0
    result["valid_pps_interval"] = result.get("is_valid_pps_interval", False)
    result["expected_error_cycles_prev31"] = result.get("offline_expected_error_cycles")
    for column in columns:
        if column not in result.columns:
            result[column] = np.nan
    optional_health_columns = [column for column in ["r_ppm", "j_ticks", "en", "ef", "es", "eh", "latency_cycles"] if column in result.columns and column not in columns]
    return result[columns + optional_health_columns]


def build_quality_summary_v2(
    pcsw: pd.DataFrame,
    pcps_frame: pd.DataFrame,
    pcps: PcpsAnalysis,
    discovered: Dict[str, Optional[Path]],
    config: AnalysisConfig,
    inferred_full_period_s: float,
) -> Dict[str, Any]:
    nominal_source = _nominal_source(discovered, config)
    rows = {}
    input_paths = {}
    input_sha256 = {}
    for key in ["pcsw", "pcps", "sts", "uno"]:
        path = discovered.get(key)
        rows[key.upper()] = _count_csv_rows(path) if path is not None else 0
        if path is not None:
            input_paths[key.upper()] = str(path)
            input_sha256[key.upper()] = _sha256_file(path)
    pcps_filters = _pcps_filter_counts(pcps_frame, config)
    pps_status = _first_non_null(pcsw.get("pps_adjustment_status"))
    pps_reason = _first_non_null(pcsw.get("pps_adjustment_reason"))
    return {
        "nominal_hz": config.nominal_hz,
        "nominal_source": nominal_source,
        "rows": rows,
        "duration_hours": {
            "PCSW": _duration_hours(pcsw, "t_hr"),
            "PCPS": _duration_hours(pcps_frame, "pps_t_hr"),
        },
        "inferred_full_period_s": inferred_full_period_s,
        "swing_31s_window_rows": int(round(31.0 / inferred_full_period_s)) if inferred_full_period_s else None,
        "PCSW_sequence": sequence_diagnostics(pcsw.get(CANONICAL_SWING.seq)),
        "PCPS_sequence": sequence_diagnostics(pcps_frame.get(CANONICAL_PPS.seq)),
        "PCSW_drop_counts": _drop_counts(pcsw, [CANONICAL_SWING.drop_ir, CANONICAL_SWING.drop_pps, CANONICAL_SWING.drop_swing]),
        "PCPS_filter_counts": pcps_filters,
        "GPS_status_counts": _value_counts(pcps_frame.get(CANONICAL_PPS.gps_status)),
        "PCPS_drop_pps_sum": _sum_column(pcps_frame, CANONICAL_PPS.drop_pps),
        "PCPS_summary": pcps.summary,
        "PCSW_mask_audit": mask_audit_counts(pcsw),
        "pps_adjustment_status": pps_status,
        "pps_adjustment_reason": pps_reason,
        "environmental_lag_hours": config.env_lag_hours,
        "input_paths": input_paths,
        "input_sha256": input_sha256,
        "configuration_hash": _configuration_hash(config),
    }


def build_statistics_table(pcsw: pd.DataFrame, pcps: pd.DataFrame) -> pd.DataFrame:
    valid_pcps = pcps["valid_pps_interval"].astype(bool) if "valid_pps_interval" in pcps.columns else pd.Series(False, index=pcps.index)
    series = {
        "open_A_s": pcsw.get("open_A_s"),
        "block_A_s": pcsw.get("block_A_s"),
        "open_B_s": pcsw.get("open_B_s"),
        "block_B_s": pcsw.get("block_B_s"),
        "half_A_s": pcsw.get("half_A_s"),
        "half_B_s": pcsw.get("half_B_s"),
        "full_s": pcsw.get("full_s", pcsw.get(DERIVED.period_s)),
        "full_pps_adj_s": pcsw.get("full_pps_adj_s"),
        "half_asymmetry_ms": pcsw.get("half_asymmetry_ms"),
        "full_resid_us": pcsw.get("full_resid_us"),
        "full_ppm_vs_inferred": pcsw.get("full_ppm_vs_inferred"),
        "PCPS_pps_raw_error_cycles": pcps.loc[valid_pcps, "pps_raw_error_cycles"] if "pps_raw_error_cycles" in pcps.columns else None,
        "PCPS_pps_raw_error_ns": pcps.loc[valid_pcps, "pps_raw_error_ns"] if "pps_raw_error_ns" in pcps.columns else None,
        "PCPS_pps_raw_error_ppm": pcps.loc[valid_pcps, "pps_raw_error_ppm"] if "pps_raw_error_ppm" in pcps.columns else None,
        "PCPS_offline_pps_adjusted_residual_cycles": pcps.loc[valid_pcps, "offline_pps_adjusted_residual_cycles"]
        if "offline_pps_adjusted_residual_cycles" in pcps.columns
        else None,
        "PCPS_offline_pps_adjusted_residual_ns": pcps.loc[valid_pcps, "offline_pps_adjusted_residual_ns"]
        if "offline_pps_adjusted_residual_ns" in pcps.columns
        else None,
        "PCPS_offline_pps_adjusted_residual_ppm": pcps.loc[valid_pcps, "offline_pps_adjusted_residual_ppm"]
        if "offline_pps_adjusted_residual_ppm" in pcps.columns
        else None,
        "PCPS_latency16_valid": pcps.loc[valid_pcps, CANONICAL_PPS.latency16]
        if CANONICAL_PPS.latency16 in pcps.columns
        else None,
        "PCPS_cap16_valid": pcps.loc[valid_pcps, CANONICAL_PPS.cap16]
        if CANONICAL_PPS.cap16 in pcps.columns
        else None,
    }
    if _first_non_null(pcsw.get("pps_adjustment_status")) == "available":
        series["full_pps_adj_ppm_vs_inferred"] = pcsw.get("full_pps_adj_ppm_vs_inferred")
    rows = []
    for metric, values in series.items():
        if values is None:
            continue
        stats = describe_series(values)
        row = {"metric": metric, "n": stats.pop("count")}
        row.update(stats)
        _add_discrete_summary(row, values)
        rows.append(row)
    return pd.DataFrame(rows, columns=STAT_COLUMNS)


def build_phase_fold_summary(pcsw: pd.DataFrame, moduli: Iterable[int], diagnostic_only: bool = False) -> pd.DataFrame:
    rows = []
    moduli = list(moduli)
    if not moduli:
        return pd.DataFrame(
            [
                {
                    "phase": np.nan,
                    "total_rows": int(len(pcsw)),
                    "valid_rows": int(valid_mask(pcsw).sum()) if len(pcsw) else 0,
                    "primary_analysis_rows": 0,
                    "analysis_rows": 0,
                    "robust_summary_rows": 0,
                    "robust_outlier_rows": 0,
                    "robust_outlier_fraction": np.nan,
                    "dropped_fraction": np.nan,
                    "pps_status_breakdown": "",
                    "n": 0,
                    "count": 0,
                    "median": np.nan,
                    "mean": np.nan,
                    "std": np.nan,
                    "half_asymmetry_ms_median": np.nan,
                    "modulus": np.nan,
                    "mask_name": "not_applicable",
                    "status": "not_applicable",
                    "warning": "phase-fold skipped: no profile phase modulus configured",
                }
            ],
            columns=PHASE_FOLD_COLUMNS,
        )
    if "full_resid_us" not in pcsw.columns:
        return pd.DataFrame(columns=PHASE_FOLD_COLUMNS)
    source = pcsw[CANONICAL_SWING.seq] if CANONICAL_SWING.seq in pcsw.columns else pcsw.get(INPUT.row_index, pd.Series(range(len(pcsw))))
    values = pd.to_numeric(pcsw["full_resid_us"], errors="coerce")
    base_mask = structure_diagnostic_mask(pcsw) & values.notna()
    valid = valid_mask(pcsw) & values.notna()
    dropped = dropped_mask(pcsw)
    robust_mask = robust_summary_mask(pcsw) & values.notna()
    asymmetry = pd.to_numeric(pcsw.get("half_asymmetry_ms"), errors="coerce") if "half_asymmetry_ms" in pcsw.columns else pd.Series(np.nan, index=pcsw.index)
    for modulus in moduli:
        _validate_phase_fold_input(pcsw, source, modulus, diagnostic_only=diagnostic_only)
        phase = pd.to_numeric(source, errors="coerce").fillna(0).astype(int) % modulus
        for phase_index in range(int(modulus)):
            phase_mask = phase.eq(phase_index)
            total_rows = int((phase_mask & values.notna()).sum())
            valid_rows = int((phase_mask & valid).sum())
            analysis_rows = int((phase_mask & base_mask).sum())
            robust_rows = int((phase_mask & robust_mask).sum())
            dropped_rows = int((phase_mask & dropped).sum())
            group = values.loc[phase_mask & base_mask]
            finite = group[np.isfinite(group)]
            n = int(finite.count())
            warning = ""
            if analysis_rows > 0 and robust_rows == 0:
                warning = "all analysis rows in this phase are excluded by robust_summary_mask"
            asym_finite = asymmetry.loc[phase_mask & base_mask]
            asym_finite = asym_finite[np.isfinite(asym_finite)]
            rows.append(
                {
                    "phase": int(phase_index),
                    "total_rows": total_rows,
                    "valid_rows": valid_rows,
                    "primary_analysis_rows": analysis_rows,
                    "analysis_rows": analysis_rows,
                    "robust_summary_rows": robust_rows,
                    "robust_outlier_rows": max(0, analysis_rows - robust_rows),
                    "robust_outlier_fraction": float((analysis_rows - robust_rows) / analysis_rows) if analysis_rows else np.nan,
                    "dropped_fraction": float(dropped_rows / total_rows) if total_rows else np.nan,
                    "pps_status_breakdown": _phase_status_breakdown(pcsw, phase_mask),
                    "n": n,
                    "count": n,
                    "median": float(finite.median()) if not finite.empty else np.nan,
                    "mean": float(finite.mean()) if not finite.empty else np.nan,
                    "std": float(finite.std(ddof=1)) if len(finite) > 1 else np.nan,
                    "half_asymmetry_ms_median": float(asym_finite.median()) if not asym_finite.empty else np.nan,
                    "modulus": int(modulus),
                    "mask_name": "structure_diagnostic_mask",
                    "status": "ok" if n > 0 else "empty" if total_rows == 0 else "no_analysis_rows",
                    "warning": warning,
                }
            )
    return pd.DataFrame(rows, columns=PHASE_FOLD_COLUMNS)


def build_synchronome_jitter_localisation(pcsw: pd.DataFrame, enabled: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not enabled:
        return pd.DataFrame(columns=SYNCHRONOME_JITTER_SUMMARY_COLUMNS), pd.DataFrame(columns=SYNCHRONOME_PHASE15_JITTER_COLUMNS)
    if pcsw.empty or "full_resid_us" not in pcsw.columns:
        return pd.DataFrame(columns=SYNCHRONOME_JITTER_SUMMARY_COLUMNS), pd.DataFrame(columns=SYNCHRONOME_PHASE15_JITTER_COLUMNS)

    source = pcsw[CANONICAL_SWING.seq] if CANONICAL_SWING.seq in pcsw.columns else pcsw.get(INPUT.row_index, pd.Series(range(len(pcsw))))
    phase = pd.to_numeric(source, errors="coerce").fillna(0).astype(int) % 15
    residual = pd.to_numeric(pcsw["full_resid_us"], errors="coerce")
    base_mask = structure_diagnostic_mask(pcsw) & residual.notna()
    if int(base_mask.sum()) < 15:
        return pd.DataFrame(columns=SYNCHRONOME_JITTER_SUMMARY_COLUMNS), pd.DataFrame(columns=SYNCHRONOME_PHASE15_JITTER_COLUMNS)

    phase_template = residual.loc[base_mask].groupby(phase.loc[base_mask]).transform("median")
    depatterned = residual.loc[base_mask] - phase_template
    slow = depatterned.rolling(101, min_periods=15, center=True).median()
    depatterned_slow = (depatterned - slow).dropna()
    if depatterned_slow.empty:
        depatterned_slow = depatterned.dropna()

    phase_rows: List[Dict[str, Any]] = []
    phase_for_resid = phase.loc[depatterned_slow.index]
    tick_resid = _half_cycle_residual_us(pcsw, "half_A_s", base_mask)
    tock_resid = _half_cycle_residual_us(pcsw, "half_B_s", base_mask)
    for phase_index in range(15):
        values = depatterned_slow.loc[phase_for_resid.eq(phase_index)]
        finite = values[np.isfinite(values)]
        tick_values = tick_resid.loc[tick_resid.index.intersection(values.index)]
        tock_values = tock_resid.loc[tock_resid.index.intersection(values.index)]
        tick_sigma = _robust_sigma(tick_values)
        tock_sigma = _robust_sigma(tock_values)
        half_sigmas = [value for value in [tick_sigma, tock_sigma] if np.isfinite(value)]
        phase_rows.append(
            {
                "phase": int(phase_index),
                "n": int(finite.count()),
                "residual_rms_us": _rms(finite),
                "residual_robust_sigma_us": _robust_sigma(finite),
                "tick_residual_robust_sigma_us": tick_sigma,
                "tock_residual_robust_sigma_us": tock_sigma,
                "half_cycle_hotspot_ratio": float(max(half_sigmas) / min(half_sigmas)) if len(half_sigmas) == 2 and min(half_sigmas) > 0 else np.nan,
            }
        )
    phase_frame = pd.DataFrame(phase_rows, columns=SYNCHRONOME_PHASE15_JITTER_COLUMNS[:-2])
    sigma = pd.to_numeric(phase_frame["residual_robust_sigma_us"], errors="coerce")
    hotspot_phase = int(phase_frame.loc[sigma.idxmax(), "phase"]) if sigma.notna().any() else -1
    baseline = float(sigma.drop(index=sigma.idxmax()).median()) if sigma.notna().sum() > 1 else np.nan
    hotspot = float(sigma.max()) if sigma.notna().any() else np.nan
    ratio = float(hotspot / baseline) if np.isfinite(hotspot) and np.isfinite(baseline) and baseline > 0 else np.nan
    phase_frame["baseline_robust_sigma_us"] = baseline
    phase_frame["hotspot_ratio"] = pd.to_numeric(phase_frame["residual_robust_sigma_us"], errors="coerce") / baseline if np.isfinite(baseline) and baseline > 0 else np.nan
    phase_frame = phase_frame[SYNCHRONOME_PHASE15_JITTER_COLUMNS]

    hotspot_row = phase_frame.loc[phase_frame["phase"].eq(hotspot_phase)].iloc[0] if hotspot_phase >= 0 else pd.Series(dtype=object)
    tick_hot = _finite_or_nan(hotspot_row.get("tick_residual_robust_sigma_us"))
    tock_hot = _finite_or_nan(hotspot_row.get("tock_residual_robust_sigma_us"))
    half_visible = "yes" if any(np.isfinite(value) and np.isfinite(baseline) and baseline > 0 and value / baseline >= 1.5 for value in [tick_hot, tock_hot]) else "no"
    summary_rows = [
        {"metric": "raw residual jitter", "value": _robust_sigma(residual.loc[base_mask]), "unit": "us", "note": "robust sigma before phase removal"},
        {"metric": "after removing 15-phase template", "value": _robust_sigma(depatterned), "unit": "us", "note": "phase median removed"},
        {"metric": "after removing slow drift", "value": _robust_sigma(depatterned_slow), "unit": "us", "note": "phase template plus rolling median removed"},
        {"metric": "robust residual jitter", "value": _robust_sigma(depatterned_slow), "unit": "us", "note": "reported per-phase metric basis"},
        {"metric": "hottest phase", "value": hotspot_phase if hotspot_phase >= 0 else np.nan, "unit": "phase", "note": "phase with largest robust residual sigma"},
        {"metric": "hotspot/baseline ratio", "value": ratio, "unit": "x", "note": "hotspot sigma divided by median of other phases"},
        {"metric": "tick/tock visible", "value": half_visible, "unit": "", "note": "whether half-cycle residuals show the hotspot above baseline"},
    ]
    return pd.DataFrame(summary_rows, columns=SYNCHRONOME_JITTER_SUMMARY_COLUMNS), phase_frame


def _half_cycle_residual_us(pcsw: pd.DataFrame, column: str, mask: pd.Series) -> pd.Series:
    if column not in pcsw.columns:
        return pd.Series(dtype=float)
    values = pd.to_numeric(pcsw[column], errors="coerce").loc[mask].dropna()
    if values.empty:
        return pd.Series(dtype=float)
    residual = (values - values.median()) * 1e6
    slow = residual.rolling(101, min_periods=15, center=True).median()
    return (residual - slow).dropna()


def _rms(values: pd.Series) -> float:
    finite = pd.to_numeric(values, errors="coerce").dropna()
    if finite.empty:
        return np.nan
    arr = finite.to_numpy(dtype=float)
    return float(np.sqrt(np.mean(arr * arr)))


def _robust_sigma(values: pd.Series) -> float:
    finite = pd.to_numeric(values, errors="coerce").dropna()
    if finite.empty:
        return np.nan
    median = float(finite.median())
    mad = float((finite - median).abs().median())
    return float(mad * 1.4826)


def build_environmental_correlations(pcsw: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    rows = []
    lag_grid_s = [0] + [sign * hour * 3600 for hour in range(1, config.env_lag_hours + 1) for sign in (-1, 1)]
    for column in [INPUT.temp_C, INPUT.humidity_pct, INPUT.pressure_hPa, CANONICAL_SWING.temperature_C]:
        if column not in pcsw.columns or column == CANONICAL_SWING.temperature_C and INPUT.temp_C in pcsw.columns:
            continue
        for window_name, frame in _environmental_windows(pcsw).items():
            subset = frame[[column, "full_resid_us"]].apply(pd.to_numeric, errors="coerce")
            for lag_s in lag_grid_s:
                corr, n = _lagged_pearson(subset[column], subset["full_resid_us"], frame.get("t_s"), lag_s)
                rows.append(
                    {
                        "metric": "full_resid_us",
                        "env_var": column,
                        "window_name": window_name,
                        "lag_s": int(lag_s),
                        "method": "global same-row Pearson" if lag_s == 0 else "lagged Pearson",
                        "correlation": corr,
                        "n": n,
                    }
                )
    return pd.DataFrame(rows, columns=ENV_CORR_COLUMNS)


def _phase_status_breakdown(pcsw: pd.DataFrame, phase_mask: pd.Series) -> str:
    if INPUT.gps_status not in pcsw.columns:
        return ""
    status = pd.to_numeric(pcsw.loc[phase_mask, INPUT.gps_status], errors="coerce")
    counts = status.value_counts(dropna=False).sort_index()
    return ";".join(f"{key}:{int(value)}" for key, value in counts.items())


def build_fft_components(
    pcsw: pd.DataFrame,
    inferred_period_s: float,
    config: AnalysisConfig,
    expected_structure_periods_s: Optional[Iterable[Any]] = None,
    expected_labels: bool = True,
    depattern_modulus: Optional[int] = None,
    count: int = 12,
) -> pd.DataFrame:
    sample_period = inferred_period_s if inferred_period_s and np.isfinite(inferred_period_s) else config.target_period_s
    rows = []
    raw = pd.to_numeric(pcsw.get("full_resid_us"), errors="coerce")
    expected = _coerce_expected_periods(expected_structure_periods_s)
    if expected_structure_periods_s is None:
        try:
            expected = _expected_fft_periods(
                ProfileSelection(
                    "",
                    get_profile(
                        config.clock_profile if config.clock_profile != "auto" else "generic",
                        profile_file=Path(config.clock_profile_file) if config.clock_profile_file else None,
                        profile_dir=Path(config.clock_profile_dir) if config.clock_profile_dir else None,
                    ),
                    "",
                    inferred_period_s,
                    None,
                    "",
                    "",
                ),
                inferred_period_s,
                config,
            )
        except ValueError:
            expected = []
    rows.extend(_fft_peak_rows(raw, sample_period, "full_resid_us", "mean-subtracted raw residuals", count, expected, config))
    if not expected_labels:
        depattern_modulus = None
    depatterned = _phase_depatterned_residuals(pcsw, depattern_modulus)
    rows.extend(
        _fft_peak_rows(
            depatterned,
            sample_period,
            "full_resid_us_phase_depatterned",
            "phase-median depatterned residuals" if depattern_modulus else "mean-subtracted residuals; no configured phase depattern",
            count,
            expected,
            config,
        )
    )
    return pd.DataFrame(rows, columns=FFT_COLUMNS)


def build_expected_fft_table(expected: Iterable[ExpectedPeriod], profile_id: str) -> pd.DataFrame:
    rows = []
    for item in expected:
        rows.append(
            {
                "profile_id": profile_id,
                "label": item.label,
                "family": item.family,
                "order": item.order,
                "period_s": item.period_s,
                "frequency_hz": item.frequency_hz,
                "source": item.source,
                "confidence": item.confidence,
            }
        )
    return pd.DataFrame(rows, columns=["profile_id", "label", "family", "order", "period_s", "frequency_hz", "source", "confidence"])


def _expected_fft_periods(selection: ProfileSelection, inferred_period_s: Optional[float], config: AnalysisConfig) -> List[ExpectedPeriod]:
    expected: List[ExpectedPeriod] = []
    min_period_s = max(2.0 * float(inferred_period_s or config.target_period_s), 0.0)
    if not config.disable_profile_fft_expectations:
        expected.extend(period for period in selection.selected.expected_structure_periods_s if period.period_s >= min_period_s)
        for annotation in selection.selected.annotations_fft:
            if annotation.period_s is not None and annotation.period_s >= min_period_s:
                expected.append(
                    ExpectedPeriod.from_period(
                        annotation.period_s,
                        annotation.label,
                        family=annotation.id,
                        source="clock_profile_annotation",
                    )
                )
    for index, period in enumerate(config.fft_expected_periods_s):
        label = config.fft_expected_period_labels[index] if index < len(config.fft_expected_period_labels) else "Configured expected period"
        expected.append(ExpectedPeriod.from_period(period, label, family="configured", source="user_config"))
    for base in config.fft_harmonic_base_periods_s:
        expected.extend(
            selection.selected.expected_harmonic_family(
                base,
                config.fft_harmonic_max_order,
                min_period_s=min_period_s,
                family="configured",
                source="user_config",
            )
        )
    if selection.selected.name == "generic" and selection.detected_cycle_events and inferred_period_s:
        base = float(selection.detected_cycle_events) * float(inferred_period_s)
        expected.extend(
            [
                ExpectedPeriod.from_period(base, "Detected cycle", family="detected_cycle", order=1, source="auto_detected", confidence=selection.confidence),
                ExpectedPeriod.from_period(base / 2.0, "Detected cycle harmonic", family="detected_cycle", order=2, source="auto_detected", confidence=selection.confidence),
            ]
        )
    return expected


def _coerce_expected_periods(values: Optional[Iterable[Any]]) -> List[ExpectedPeriod]:
    expected: List[ExpectedPeriod] = []
    for value in values or []:
        if isinstance(value, ExpectedPeriod):
            expected.append(value)
        else:
            period = float(value)
            expected.append(ExpectedPeriod.from_period(period, f"Expected {period:g} s period", family="configured", source="user_config"))
    return expected


def _fft_series(frame: pd.DataFrame, series: str) -> pd.DataFrame:
    if frame.empty or "series" not in frame.columns:
        return pd.DataFrame(columns=FFT_COLUMNS)
    return frame.loc[frame["series"].eq(series)].copy()


def build_allan_sampled(allan: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    if allan.empty:
        return pd.DataFrame(columns=["tau_rows", "tau_s", "m", "adev_fractional", "n_averages", "n_diffs", "series", "mask_name", "row_inclusion_policy", "deterministic_phase_structure_included", "robust_outliers_excluded", "gap_policy"])
    result = pd.DataFrame(
        {
            "tau_rows": np.maximum(1, np.rint(pd.to_numeric(allan["tau_s"], errors="coerce") / config.target_period_s)).astype(int),
            "tau_s": allan["tau_s"],
            "m": allan["m"],
            "adev_fractional": allan["adev_fractional"],
            "n_averages": allan["n_averages"],
            "n_diffs": allan["n_diffs"],
            "series": "full_s",
            "mask_name": allan.get("mask_name", "structure_diagnostic_mask"),
            "row_inclusion_policy": allan.get("row_inclusion_policy", "valid locked non-dropped rows"),
            "deterministic_phase_structure_included": allan.get("deterministic_phase_structure_included", True),
            "robust_outliers_excluded": allan.get("robust_outliers_excluded", False),
            "gap_policy": allan.get("gap_policy", "assumes retained rows are regular"),
        }
    )
    return result


def sequence_diagnostics(values: Optional[pd.Series]) -> Dict[str, int]:
    if values is None:
        return {"non_unit_jumps": 0, "total_missing_when_positive": 0, "max_positive_jump": 0, "nonpositive_jumps": 0}
    seq = pd.to_numeric(values, errors="coerce").dropna()
    delta = seq.diff().dropna()
    positive = delta[delta > 0]
    non_unit = positive[positive != 1]
    return {
        "non_unit_jumps": int((delta != 1).sum()),
        "total_missing_when_positive": int((positive[positive > 1] - 1).sum()) if not positive.empty else 0,
        "max_positive_jump": int(positive.max()) if not positive.empty else 0,
        "nonpositive_jumps": int((delta <= 0).sum()),
    }


def write_report(
    bundle: CanonicalV2Bundle,
    registry: ArtifactRegistry,
    exact_command: str,
    input_label: str,
    *,
    pps_report_available: bool = False,
) -> None:
    quality = bundle.quality
    stats = bundle.statistics
    compact_stats = compact_stats_for_report(stats)
    sanity = build_sanity_checks(bundle)
    sanity = sanity.loc[~sanity["check"].astype(str).str.startswith("PCPS")].reset_index(drop=True)
    scorecard = build_health_scorecard(bundle)
    scorecard = scorecard.loc[scorecard["dimension"] != "timebase/GPS"].reset_index(drop=True)
    relationships = build_relationship_summary(bundle)
    provenance = build_report_provenance(bundle, exact_command, input_label)
    plan_frame = bundle.analysis_plan.to_frame()
    plan_frame = plan_frame.loc[plan_frame["name"] != "pcps_timebase_health"].reset_index(drop=True)
    lines = [
        "# CANONICAL Timing Analysis Report - v2 PCPS Integrated",
        "",
        "## Executive summary",
        "",
        *build_executive_summary(bundle),
        "",
        "## Clock profile and capabilities",
        "",
        _profile_capability_section(bundle),
        "",
        "## Analysis plan",
        "",
        _frame_table(plan_frame[["name", "tier", "status", "reason"]], max_rows=40),
        "",
        "## Health scorecard",
        "",
        _frame_table(scorecard, max_rows=20),
        "",
        "## Sanity checks",
        "",
        _frame_table(sanity, max_rows=24),
        "",
        "## Measurement and derived quantity provenance",
        "",
        measurement_provenance_section(),
        "",
        "## Data quality details",
        "",
        "Input row counts:",
        "",
        _mapping_table(quality.get("rows", {}), "file", "rows"),
        "",
        "Sequence and drop checks:",
        "",
        _key_value_sections(
            {
                "PCSW sequence": quality.get("PCSW_sequence", {}),
                "PCSW drops": quality.get("PCSW_drop_counts", {}),
                "PCSW masks": quality.get("PCSW_mask_audit", {}),
            }
        ),
        "",
        "## PPS clock analysis",
        "",
        _pps_report_note(pps_report_available),
        "",
        "## PCSW pendulum timing summary",
        "",
        _pendulum_interpretation(bundle),
        "",
        _frame_table(_swing_statistics(compact_stats), max_rows=18),
        "",
        "## Visualizations",
        "",
    ]
    for artifact in _report_artifacts(registry, NUMBERED_PLOT_SLUGS):
        lines.append(f"Figure {artifact.id}: {artifact.title}")
        lines.append("")
        lines.append(f"![Figure {artifact.id}: {artifact.title}]({registry.markdown_path(artifact)})")
        lines.append("")
    lines.extend(
        [
            "## Residual/spectral analysis",
            "",
            _spectral_note(bundle),
            "",
            _frame_table(_compact_fft_table(bundle.fft_components), max_rows=12),
            "",
            _synchronome_jitter_report_section(bundle, registry),
            "",
            "## Environmental analysis",
            "",
            f"Environmental rows are associations, not effects. The lag grid is 0 and +/-1..{quality.get('environmental_lag_hours', 'n/a')} hours where sufficient timestamps exist; same-row entries are labelled as global same-row Pearson.",
            "",
            _frame_table(bundle.environmental, max_rows=8),
            "",
            "## Relationships",
            "",
            _relationship_note(relationships),
            "",
            _frame_table(relationships, max_rows=10),
            "",
            "## Long-run triage",
            "",
            _long_run_triage_section(bundle),
            "",
            "## Interpretation layer",
            "",
            "Observed facts:",
            "",
            "- The CANONICAL edge decomposition is evaluated as neutral open/block and half-cycle intervals.",
            "- PPS clock quality is presented in the standalone PPS report linked above.",
            "- Swing sequence discontinuities and firmware drop flags are reported explicitly in the data quality table.",
            "",
            "Derived/inferred interpretation:",
            "",
            "- Full-cycle residuals, phase folds, FFT components, and environmental correlations are host-derived diagnostics.",
            "",
            "## Key findings",
            "",
            "1. The output bundle is sufficient for repeatable canonical v2 timing review.",
            "2. Swing mechanics and PPS clock quality are reported separately, with the standalone PPS report authoritative for PPS findings.",
            "3. The CSV files preserve the useful derived series needed for external checks.",
            "",
            "## Open questions / caveats",
            "",
            "- PPS-calibrated swing intervals require valid captured PPS coverage.",
            "- Environmental correlations are association diagnostics and should not be read as causal models.",
            "- Long-tau Allan results depend on run duration and clean-row continuity.",
            "",
            "## Reproducibility",
            "",
            _frame_table(provenance, max_rows=30),
            "",
            "```sh",
            exact_command,
            "```",
            "",
        ]
    )
    report_artifact = registry.register_report(
        slug="canonical_report",
        title="Canonical timing analysis report",
        source_files=["PCSW.csv", "PCPS.csv"],
    )
    registry.path(report_artifact).write_text("\n".join(lines), encoding="utf-8")


def _pps_report_note(available: bool) -> str:
    if available:
        return (
            "The standalone [PPS clock analysis report](../pcps/report.html) is authoritative for PPS "
            "quality, diagnostics, plots, and exported tables. A [Markdown version](../pcps/report.md) "
            "is also available."
        )
    return "No PCPS/CPS input was discovered, so no standalone PPS clock analysis report was generated."


def write_audit_artifacts(bundle: CanonicalV2Bundle, registry: ArtifactRegistry) -> None:
    mask_md = registry.register_metadata(
        slug="mask_audit",
        title="Mask audit notes",
        section="Analysis audit",
        source_files=["PCSW.csv"],
        analysis_name="analysis_audit",
        description="Human-readable mask semantics and row counts.",
        extension=".md",
    )
    lineage_md = registry.register_metadata(
        slug="analysis_lineage",
        title="Analysis lineage notes",
        section="Analysis audit",
        source_files=["PCSW.csv", "PCPS.csv"],
        analysis_name="analysis_audit",
        description="Human-readable per-artifact data lineage.",
        extension=".md",
    )

    mask_frame = build_mask_audit_frame(bundle)
    lineage = build_lineage_frame(bundle, registry)
    mask_frame.to_csv(registry.root / "metadata" / "mask_audit.csv", index=False)
    registry.path(mask_md).write_text(_mask_audit_markdown(mask_frame), encoding="utf-8")
    lineage.to_csv(registry.root / "metadata" / "analysis_lineage.csv", index=False)
    registry.path(lineage_md).write_text(_lineage_markdown(lineage), encoding="utf-8")


def build_mask_audit_frame(bundle: CanonicalV2Bundle) -> pd.DataFrame:
    audit = bundle.quality.get("PCSW_mask_audit", {})
    semantics = {
        "total_rows": "All PCSW rows after parsing.",
        "valid_mask_rows": "Rows with finite derived timing fields.",
        "locked_mask_rows": "Rows whose PPS/GPS lock policy is acceptable.",
        "dropped_mask_rows": "Rows flagged as dropped.",
        "analysis_mask_rows": "Valid locked non-dropped rows; no robust outlier exclusion.",
        "robust_summary_mask_rows": "Analysis rows after robust period/asymmetry outlier exclusion.",
        "is_period_outlier_rows": "Rows flagged by the global robust period outlier detector.",
        "is_asymmetry_outlier_rows": "Rows flagged by the global robust asymmetry outlier detector.",
    }
    rows = []
    total = float(audit.get("total_rows", 0) or 0)
    for name, description in semantics.items():
        count = int(audit.get(name, 0) or 0)
        rows.append({"mask_or_count": name, "rows": count, "fraction_of_total": count / total if total else np.nan, "description": description})
    return pd.DataFrame(rows, columns=["mask_or_count", "rows", "fraction_of_total", "description"])


def build_lineage_frame(bundle: CanonicalV2Bundle, registry: ArtifactRegistry) -> pd.DataFrame:
    rows = []
    for artifact in registry.artifacts:
        rows.append(_lineage_row(bundle, artifact))
    return pd.DataFrame(
        rows,
        columns=[
            "output_filename",
            "artifact_id",
            "artifact_type",
            "source_dataframe",
            "source_csv",
            "required_columns",
            "mask_used",
            "row_count_before_mask",
            "row_count_after_mask",
            "sampling_method",
            "aggregation_method",
            "deterministic_phase_structure_preserved",
            "robust_outliers_excluded",
            "selected_profile_id",
            "profile_display_name",
            "capabilities_used",
            "analysis_type",
            "profile_fields_used",
            "reason_skipped",
            "rationale",
        ],
    )


def _lineage_row(bundle: CanonicalV2Bundle, artifact: Artifact) -> Dict[str, Any]:
    slug = artifact.slug
    source_dataframe = "pcsw_full"
    before = int(bundle.quality.get("PCSW_mask_audit", {}).get("total_rows", 0) or 0)
    after = int(bundle.quality.get("PCSW_mask_audit", {}).get("analysis_mask_rows", 0) or 0)
    mask = "analysis_mask"
    sampling = "none"
    aggregation = "none"
    phase_preserved = True
    robust_excluded = False
    required = ""
    rationale = "Default PCSW descriptive diagnostic."
    planned = _planned_analysis(bundle, artifact.analysis_name)
    required_caps = planned.required_capabilities if planned is not None else []
    capabilities_used = [cap for cap in required_caps if bool(getattr(bundle.capabilities, cap, False))]
    analysis_type = "profile-specific" if required_caps or artifact.analysis_name in {"phase_fold", "tick_tock_diagnostics", "block_interval_diagnostics"} else "core"
    profile_fields_used = ""
    reason_skipped = planned.reason if planned is not None and planned.status == "SKIP" else ""

    if artifact.namespace == "pcps":
        source_dataframe = "pcps_full"
        before = int(bundle.quality.get("rows", {}).get("PCPS", 0) or 0)
        filters = bundle.quality.get("PCPS_filter_counts", {})
        after = int(filters.get("valid_filtered_intervals", before) if isinstance(filters, dict) else before)
        mask = "valid_pps_interval"
        phase_preserved = False
        required = "PCPS timing columns"
        rationale = "Dedicated PPS/timebase diagnostic."
    elif artifact.namespace == "combined":
        source_dataframe = "pcsw_full + pcps_full"
        required = "varies by table"

    if "sampled" in slug or slug in {"full_cycle_timing_overview", "half_cycle_comparison", "component_decomposition"}:
        sampling = _sample_method_for(bundle.pcsw_sampled if artifact.namespace != "pcps" else bundle.pcps_sampled)
        rationale = "Plot/export uses deterministic profile-phase/time-balanced sampling when a phase model is configured."
    if slug.startswith("phase_fold"):
        mask = "structure_diagnostic_mask"
        after = int(pd.to_numeric(bundle.phase_fold.get("analysis_rows", pd.Series(dtype=float)), errors="coerce").sum()) if not bundle.phase_fold.empty and "analysis_rows" in bundle.phase_fold.columns else after
        aggregation = "group by seq phase; median/mean/std full_resid_us; robust counts retained"
        required = "seq or row_index, full_resid_us"
        profile_fields_used = "phase.expected_moduli"
        rationale = "Structure-discovery diagnostic; robust outliers are annotated, not removed."
    elif "environment" in slug:
        mask = "all_rows_descriptive + analysis_mask + robust_summary_mask_labelled"
        aggregation = "Pearson correlations by labelled window and lag"
        required = "full_resid_us, environmental column, optional t_s"
        robust_excluded = "robust_summary_mask_labelled" in mask
        rationale = "Environmental associations are emitted for explicit labelled windows."
    elif "allan" in slug:
        mask = "structure_diagnostic_mask"
        aggregation = "overlapping Allan deviation"
        required = "period_s or full_s"
        robust_excluded = False
        rationale = "Swing Allan includes deterministic phase structure; row policy is recorded in table columns."
    elif "statistics" in slug:
        aggregation = "describe_series statistics"
        required = "derived timing columns"
        rationale = "Descriptive summary table; robust spread statistics do not imply row deletion."
    elif "histogram" in slug or slug.endswith("_hist"):
        aggregation = "histogram/bin counts"
        rationale = "Distribution plot uses available plotted values; clipping is labelled where used."
    elif artifact.artifact_type in {"metadata", "report"}:
        source_dataframe = "artifact registry / bundle metadata"
        before = 0
        after = 0
        mask = "not_applicable"
        phase_preserved = False
        required = "artifact registry"
        rationale = "Metadata/report artifact."
    elif "fft" in slug:
        profile_fields_used = "harmonic_families; annotations.fft" if bundle.fft_expected is not None and not bundle.fft_expected.empty else ""

    return {
        "output_filename": artifact.relative_path,
        "artifact_id": artifact.id,
        "artifact_type": artifact.artifact_type,
        "source_dataframe": source_dataframe,
        "source_csv": ";".join(artifact.source_files),
        "required_columns": required,
        "mask_used": mask,
        "row_count_before_mask": before,
        "row_count_after_mask": after,
        "sampling_method": sampling,
        "aggregation_method": aggregation,
        "deterministic_phase_structure_preserved": bool(phase_preserved),
        "robust_outliers_excluded": bool(robust_excluded),
        "selected_profile_id": bundle.profile_selection.selected.name,
        "profile_display_name": bundle.profile_selection.selected.display_name,
        "capabilities_used": ";".join(capabilities_used),
        "analysis_type": analysis_type,
        "profile_fields_used": profile_fields_used,
        "reason_skipped": reason_skipped,
        "rationale": rationale,
    }


def _planned_analysis(bundle: CanonicalV2Bundle, name: str):
    for planned in bundle.analysis_plan.analyses:
        if planned.name == name:
            return planned
    return None


def _sample_method_for(frame: pd.DataFrame) -> str:
    if "sample_method" in frame.columns:
        values = frame["sample_method"].dropna().astype(str)
        if not values.empty:
            return values.iloc[0]
    if "sampled" in frame.columns and not frame["sampled"].astype(bool).any():
        return "none_full_resolution"
    return "none"


def _mask_audit_markdown(frame: pd.DataFrame) -> str:
    lines = ["# Mask Audit", "", "Named masks are centrally constructed in `pendulum_analysis.filters`.", ""]
    lines.append(_frame_as_markdown(frame))
    lines.append("")
    lines.append("`analysis_mask` / `structure_diagnostic_mask` preserve deterministic configured phase structure. `robust_summary_mask` is reserved for explicitly labelled denoised summaries.")
    return "\n".join(lines)


def _lineage_markdown(frame: pd.DataFrame) -> str:
    compact = frame[["output_filename", "analysis_type", "selected_profile_id", "capabilities_used", "profile_fields_used", "reason_skipped", "mask_used", "row_count_after_mask", "sampling_method", "robust_outliers_excluded", "rationale"]].copy()
    lines = ["# Analysis Lineage", "", "Each generated artifact records its data source, mask, sampling, aggregation, and outlier policy.", ""]
    lines.append(_frame_as_markdown(compact))
    return "\n".join(lines)


def _frame_as_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    return _markdown_table(list(frame.columns), frame.astype(object).where(pd.notna(frame), "").values.tolist())


PLOT_SPECS = [
    ("pcsw", "full_cycle_timing_overview", "Full-cycle timing overview", "PCSW pendulum timing summary", "basic_plots"),
    ("pcsw", "half_cycle_comparison", "Half-cycle comparison", "PCSW pendulum timing summary", "basic_plots"),
    ("pcsw", "component_decomposition", "Component decomposition", "PCSW pendulum timing summary", "basic_plots"),
    ("pcsw", "residual_histogram", "Residual histogram", "Residual/spectral analysis", "basic_residuals"),
    ("pcsw", "residual_autocorrelation", "Residual autocorrelation", "Residual/spectral analysis", "basic_residuals"),
    ("pcsw", "residual_fft", "Residual FFT", "Residual/spectral analysis", "fft_spectrum"),
    ("pcsw", "phase_fold_primary", "Phase fold primary profile modulus", "Residual/spectral analysis", "phase_fold"),
    ("pcsw", "phase_fold_secondary", "Phase fold secondary profile modulus", "Residual/spectral analysis", "phase_fold"),
    ("combined", "environment_relationships_humidity_pct", "Environmental relationships: humidity", "Environmental analysis", "environmental_correlations"),
    ("combined", "environment_relationships_pressure_hpa", "Environmental relationships: pressure", "Environmental analysis", "environmental_correlations"),
    ("combined", "environment_relationships_temperature_c", "Environmental relationships: temperature", "Environmental analysis", "environmental_correlations"),
    ("pcsw", "allan_deviation", "Allan deviation", "Long-term stability", "allan_deviation"),
    ("pcsw", "phase_fold_robust_outlier_fraction_primary", "Phase fold robust outlier fraction primary modulus", "Residual/spectral analysis", "phase_fold"),
]

PCPS_HEALTH_PLOT_SPECS: list[tuple[str, str, str, str, str]] = []

NUMBERED_PLOT_SLUGS = [slug for _, slug, *_ in PLOT_SPECS]
PCPS_HEALTH_PLOT_SLUGS = [slug for _, slug, *_ in PCPS_HEALTH_PLOT_SPECS]
PCPS_HEALTH_FIGURES = [title for _, _, title, *_ in PCPS_HEALTH_PLOT_SPECS]


def _write_csv(
    frame: pd.DataFrame,
    registry: ArtifactRegistry,
    *,
    namespace: str,
    slug: str,
    title: str,
    section: str,
    source_files: Iterable[str],
    analysis_name: str,
) -> Artifact:
    artifact = registry.register_csv(
        namespace=namespace,
        slug=slug,
        title=title,
        section=section,
        source_files=source_files,
        analysis_name=analysis_name,
    )
    frame.to_csv(registry.path(artifact), index=False)
    return artifact


def _report_artifacts(registry: ArtifactRegistry, slugs: Iterable[str]) -> List[Artifact]:
    artifacts: List[Artifact] = []
    for slug in slugs:
        for namespace in ["pcsw", "pcps", "combined"]:
            try:
                artifacts.append(registry.get(namespace, "plot", slug))
                break
            except KeyError:
                continue
        else:
            raise KeyError(f"Report references unregistered plot artifact: {slug}")
    return artifacts


def _sample_frame(data: pd.DataFrame, max_points: int, phase_mod: Optional[int] = None) -> pd.DataFrame:
    note = "phase/time-balanced sampled export; not valid for phase-fold diagnostics" if phase_mod else "time-balanced sampled export; no phase model configured"
    if len(data) <= max_points:
        sampled = data.copy()
        sampled["sampled"] = False
        sampled["sample_method"] = "none_full_resolution"
        sampled["sample_stride"] = 1
        sampled["sample_note"] = note
        return sampled

    source = _sampling_sequence(data)
    if phase_mod:
        phase = (pd.to_numeric(source, errors="coerce").fillna(0).astype(int) % max(1, int(phase_mod))).rename("_sample_phase")
    else:
        phase = pd.Series(0, index=data.index, name="_sample_phase")
    time = _sampling_time(data).rename("_sample_time")
    working = data.copy()
    working["_sample_phase"] = phase
    working["_sample_time"] = time

    phases = sorted(working["_sample_phase"].dropna().unique().tolist()) or [0]
    quota = max(1, int(np.floor(max_points / max(1, len(phases)))))
    selected: list[Any] = []
    for _, phase_group in working.sort_values("_sample_time").groupby("_sample_phase", sort=True):
        selected.extend(_time_balanced_indices(phase_group, quota))

    if len(selected) > max_points:
        selected = sorted(selected, key=lambda index: (working.at[index, "_sample_time"], working.at[index, "_sample_phase"]))[:max_points]
    sampled = working.loc[selected].sort_values("_sample_time").drop(columns=["_sample_phase", "_sample_time"]).copy()
    sampled["sampled"] = True
    sampled["sample_method"] = f"phase_time_balanced_mod{int(phase_mod)}" if phase_mod else "time_balanced"
    sampled["sample_stride"] = np.nan
    sampled["sample_note"] = note
    return sampled


def _sampling_sequence(data: pd.DataFrame) -> pd.Series:
    if CANONICAL_SWING.seq in data.columns:
        return data[CANONICAL_SWING.seq]
    if CANONICAL_PPS.seq in data.columns:
        return data[CANONICAL_PPS.seq]
    if INPUT.row_index in data.columns:
        return data[INPUT.row_index]
    return pd.Series(np.arange(len(data)), index=data.index)


def _sampling_time(data: pd.DataFrame) -> pd.Series:
    for column in ["t_s", "pps_t_s", "t_hr", "pps_t_hr"]:
        if column in data.columns:
            values = pd.to_numeric(data[column], errors="coerce")
            if values.notna().any():
                return values.ffill().bfill().fillna(0.0)
    return pd.Series(np.arange(len(data)), index=data.index, dtype=float)


def _time_balanced_indices(group: pd.DataFrame, quota: int) -> list[Any]:
    if len(group) <= quota:
        return list(group.index)
    time = pd.to_numeric(group["_sample_time"], errors="coerce")
    if time.nunique(dropna=True) <= 1:
        ranks = pd.Series(np.arange(len(group)), index=group.index, dtype=float)
        time = ranks
    bins = min(max(1, int(quota)), len(group))
    edges = pd.cut(time, bins=bins, labels=False, duplicates="drop")
    selected: list[Any] = []
    for _, bin_group in group.groupby(edges, sort=True):
        center = float(pd.to_numeric(bin_group["_sample_time"], errors="coerce").median())
        distance = (pd.to_numeric(bin_group["_sample_time"], errors="coerce") - center).abs()
        selected.append(distance.sort_values(kind="mergesort").index[0])
    return selected


def _inferred_period(pcsw: pd.DataFrame) -> float:
    source = pd.to_numeric(pcsw.get("full_s", pcsw.get(DERIVED.period_s)), errors="coerce")
    if FLAGS.is_robust_summary in pcsw.columns or FLAGS.is_clean_robust in pcsw.columns:
        source = source.loc[robust_summary_mask(pcsw)]
    source = source[np.isfinite(source)]
    return float(source.median()) if not source.empty else float("nan")


def _duration_hours(data: pd.DataFrame, column: str) -> Optional[float]:
    if column not in data.columns or data.empty:
        return None
    values = pd.to_numeric(data[column], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.max() - values.min())


def _first_non_null(values: Optional[pd.Series]) -> Optional[Any]:
    if values is None:
        return None
    present = values.dropna()
    return present.iloc[0] if not present.empty else None


def _add_discrete_summary(row: Dict[str, Any], values: pd.Series) -> None:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[np.isfinite(numeric)]
    row["unique_values"] = int(numeric.nunique()) if not numeric.empty else None
    if numeric.empty:
        row["top_values"] = None
        row["nonzero_fraction"] = None
        row["spread_note"] = None
        return
    counts = numeric.value_counts().head(5)
    row["top_values"] = "; ".join(f"{_fmt_report_value(value, 'value')}:{int(count)}" for value, count in counts.items())
    row["nonzero_fraction"] = float((numeric != 0).mean())
    if row.get("robust_sigma") == 0 or row.get("IQR") == 0:
        row["spread_note"] = "robust spread is zero; see discrete counts and quantiles"
    else:
        row["spread_note"] = None


def _validate_phase_fold_input(pcsw: pd.DataFrame, source: pd.Series, modulus: int, diagnostic_only: bool = False) -> None:
    if diagnostic_only or len(pcsw) < max(30, int(modulus) * 2):
        return
    sampled = pcsw.get("sampled")
    if sampled is not None and sampled.astype(bool).any():
        raise ValueError("phase-fold diagnostics require full-resolution rows, not sampled exports")
    phase = pd.to_numeric(source, errors="coerce").dropna().astype(int) % int(modulus)
    if phase.empty:
        return
    counts = phase.value_counts()
    max_fraction = float(counts.max() / counts.sum())
    if max_fraction >= 0.8:
        raise ValueError("phase-fold diagnostics refused input with implausibly collapsed phase coverage")


def _environmental_windows(pcsw: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    windows = {"all_rows_descriptive": pcsw}
    analysis = analysis_mask(pcsw)
    robust = robust_summary_mask(pcsw)
    if analysis.any():
        windows["analysis_mask"] = pcsw.loc[analysis]
    if robust.any():
        windows["robust_summary_mask_labelled"] = pcsw.loc[robust]
    t_s = pd.to_numeric(pcsw.get("t_s"), errors="coerce")
    if t_s.notna().sum() >= 3:
        startup_cutoff = t_s.min() + 3600.0
        stable = analysis & (t_s >= startup_cutoff)
        if stable.sum() >= 3:
            windows["analysis_after_first_hour"] = pcsw.loc[stable]
    return windows


def _lagged_pearson(env: pd.Series, residual: pd.Series, t_s: Optional[pd.Series], lag_s: int) -> tuple[float, int]:
    env_numeric = pd.to_numeric(env, errors="coerce")
    residual_numeric = pd.to_numeric(residual, errors="coerce")
    if lag_s == 0 or t_s is None:
        subset = pd.concat([env_numeric, residual_numeric], axis=1).dropna()
    else:
        t_numeric = pd.to_numeric(t_s, errors="coerce")
        env_frame = pd.DataFrame({"t_s": t_numeric + float(lag_s), "env": env_numeric}).dropna().sort_values("t_s")
        residual_frame = pd.DataFrame({"t_s": t_numeric, "residual": residual_numeric}).dropna().sort_values("t_s")
        if env_frame.empty or residual_frame.empty:
            return np.nan, 0
        merged = pd.merge_asof(residual_frame, env_frame, on="t_s", direction="nearest", tolerance=max(1.0, abs(float(lag_s)) / 2.0))
        subset = merged[["env", "residual"]].dropna()
    n = int(len(subset))
    if n < 3 or subset.iloc[:, 0].nunique() <= 1 or subset.iloc[:, 1].nunique() <= 1:
        return np.nan, n
    return float(subset.iloc[:, 0].corr(subset.iloc[:, 1])), n


def _phase_depatterned_residuals(pcsw: pd.DataFrame, modulus: Optional[int] = 30) -> pd.Series:
    residual = pd.to_numeric(pcsw.get("full_resid_us"), errors="coerce")
    if residual.empty or not modulus:
        return residual
    source = pcsw[CANONICAL_SWING.seq] if CANONICAL_SWING.seq in pcsw.columns else pcsw.get(INPUT.row_index, pd.Series(range(len(pcsw))))
    phase = pd.to_numeric(source, errors="coerce").fillna(0).astype(int) % int(modulus)
    medians = residual.groupby(phase).transform("median")
    return residual - medians


def _fft_peak_rows(values: pd.Series, sample_period_s: float, series: str, preprocessing: str, count: int, expected_periods: Iterable[ExpectedPeriod], config: AnalysisConfig) -> List[Dict[str, Any]]:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if len(numeric) < 8:
        return []
    arr = numeric.to_numpy(dtype=float)
    arr = arr - np.mean(arr)
    window = np.hanning(len(arr))
    spectrum = np.abs(np.fft.rfft(arr * window))
    freq = np.fft.rfftfreq(len(arr), d=sample_period_s)
    if len(spectrum) <= 1:
        return []
    bin_width = float(freq[1] - freq[0]) if len(freq) > 1 else 0.0
    candidates = _fft_peak_candidates(spectrum, count)
    clusters = _cluster_fft_peaks(candidates, freq, spectrum, bin_width, config.fft_peak_cluster_tolerance_pct)
    clusters = sorted(clusters, key=lambda cluster: (-float(spectrum[cluster["representative_index"]]), float(freq[cluster["representative_index"]]), int(cluster["representative_index"])))[:count]
    max_amplitude = float(max([spectrum[cluster["representative_index"]] for cluster in clusters], default=1.0)) or 1.0
    power = spectrum * spectrum
    max_power = float(max([power[cluster["representative_index"]] for cluster in clusters], default=1.0)) or 1.0
    total_power = float(np.sum(power[1:])) or np.nan
    rows: List[Dict[str, Any]] = []
    for rank, cluster in enumerate(clusters, start=1):
        index = int(cluster["representative_index"])
        if freq[index] <= 0:
            continue
        period = float(1.0 / freq[index])
        match = _match_expected_frequency(float(freq[index]), expected_periods, bin_width, config)
        label = match["expected_label"] if match else ""
        interpretation = _fft_interpretation(match)
        amp = float(spectrum[index])
        pwr = float(power[index])
        rows.append(
            {
                "series": series,
                "preprocessing": preprocessing,
                "rank": rank,
                "frequency_hz": float(freq[index]),
                "frequency_Hz": float(freq[index]),
                "period_s": period,
                "amplitude": amp,
                "relative_amplitude_to_max": float(amp / max_amplitude),
                "relative_amplitude": float(amp / max_amplitude),
                "power": pwr,
                "relative_power_to_max": float(pwr / max_power),
                "power_fraction_if_available": float(pwr / total_power) if np.isfinite(total_power) and total_power > 0 else np.nan,
                "cluster_size": int(cluster["cluster_size"]),
                "cluster_frequency_span_hz": float(cluster["cluster_frequency_span_hz"]),
                "cluster_period_span_s": float(cluster["cluster_period_span_s"]),
                "representative_frequency_hz": float(freq[index]),
                "representative_period_s": period,
                "expected_match": bool(match),
                "expected_label": label,
                "expected_family": match["expected_family"] if match else "",
                "expected_order": match["expected_order"] if match else np.nan,
                "expected_period_s": match["expected_period_s"] if match else np.nan,
                "expected_frequency_hz": match["expected_frequency_hz"] if match else np.nan,
                "match_error_pct": match["match_error_pct"] if match else np.nan,
                "match_error_bins": match["match_error_bins"] if match else np.nan,
                "match_source": match["match_source"] if match else "",
                "interpretation": interpretation,
                "investigation_priority": "low" if match else "candidate",
                "structure_period_s": match["expected_period_s"] if match else np.nan,
                "structure_label": label,
            }
        )
    return rows


def _local_fft_maxima(spectrum: np.ndarray) -> List[int]:
    if len(spectrum) <= 2:
        return []
    peaks = [idx for idx in range(1, len(spectrum) - 1) if spectrum[idx] >= spectrum[idx - 1] and spectrum[idx] >= spectrum[idx + 1]]
    if not peaks and len(spectrum) > 1:
        peaks = list(range(1, len(spectrum)))
    return peaks


def _fft_peak_candidates(spectrum: np.ndarray, count: int) -> List[int]:
    peaks = set(_local_fft_maxima(spectrum))
    strongest = np.argsort(spectrum[1:])[::-1][: max(int(count) * 6, int(count))] + 1
    peaks.update(int(index) for index in strongest)
    return sorted(peaks)


def _cluster_fft_peaks(indices: List[int], freq: np.ndarray, spectrum: np.ndarray, bin_width_hz: float, tolerance_pct: float) -> List[Dict[str, Any]]:
    clusters: List[List[int]] = []
    for index in sorted(indices, key=lambda idx: float(freq[idx])):
        if not clusters:
            clusters.append([index])
            continue
        previous = clusters[-1]
        prev_freq = float(freq[previous[-1]])
        threshold = max(float(bin_width_hz), prev_freq * float(tolerance_pct) / 100.0)
        if abs(float(freq[index]) - prev_freq) <= threshold:
            previous.append(index)
        else:
            clusters.append([index])
    result: List[Dict[str, Any]] = []
    for cluster in clusters:
        representative = max(cluster, key=lambda idx: (float(spectrum[idx]), -idx))
        cluster_freq = [float(freq[idx]) for idx in cluster if freq[idx] > 0]
        cluster_period = [float(1.0 / freq[idx]) for idx in cluster if freq[idx] > 0]
        result.append(
            {
                "representative_index": int(representative),
                "cluster_size": int(len(cluster)),
                "cluster_frequency_span_hz": float(max(cluster_freq) - min(cluster_freq)) if cluster_freq else 0.0,
                "cluster_period_span_s": float(max(cluster_period) - min(cluster_period)) if cluster_period else 0.0,
            }
        )
    return result


def _match_expected_frequency(frequency_hz: float, expected_periods: Iterable[ExpectedPeriod], bin_width_hz: float, config: AnalysisConfig) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    for expected in expected_periods:
        expected_freq = float(expected.frequency_hz)
        error_hz = abs(float(frequency_hz) - expected_freq)
        tolerance_hz = max(expected_freq * float(config.fft_match_tolerance_pct) / 100.0, float(config.fft_match_tolerance_bin) * float(bin_width_hz))
        if error_hz > tolerance_hz:
            continue
        normalized = error_hz / tolerance_hz if tolerance_hz > 0 else 0.0
        match = {
            "expected_label": expected.label,
            "expected_family": expected.family,
            "expected_order": expected.order if expected.order is not None else np.nan,
            "expected_period_s": expected.period_s,
            "expected_frequency_hz": expected.frequency_hz,
            "match_error_pct": float(error_hz / expected_freq * 100.0) if expected_freq else np.nan,
            "match_error_bins": float(error_hz / bin_width_hz) if bin_width_hz else np.nan,
            "match_source": expected.source,
            "normalized_error": normalized,
        }
        if best is None or (match["normalized_error"], match["match_error_pct"]) < (best["normalized_error"], best["match_error_pct"]):
            best = match
    if best is not None:
        best.pop("normalized_error", None)
    return best


def _fft_interpretation(match: Optional[Dict[str, Any]]) -> str:
    if not match:
        return "Unmatched periodic structure; investigate if persistent after phase/environmental correction"
    if match["expected_family"] == "impulse_cycle" and match.get("expected_order") == 1:
        return "Expected profile impulse-cycle fundamental"
    if match["expected_family"] == "impulse_cycle" and pd.notna(match.get("expected_order")):
        order = int(match["expected_order"])
        return f"Expected {_ordinal(order)} harmonic of {match['expected_period_s'] * order:g} s impulse cycle"
    if match["match_source"] == "user_config":
        return "Configured expected period"
    if match["expected_family"] == "detected_cycle":
        return "Expected auto-detected cycle structure"
    return f"Expected {match['expected_label']}"


def _ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def compact_stats_for_report(stats: pd.DataFrame) -> pd.DataFrame:
    """Condense exhaustive statistics into the fields useful in the report."""
    columns = ["metric", "n", "median", "robust_spread", "p05", "p95", "min", "max", "note"]
    if stats.empty:
        return pd.DataFrame(columns=columns)
    preferred = [
        "full_s",
        "full_resid_us",
        "full_ppm_vs_inferred",
        "half_A_s",
        "half_B_s",
        "half_asymmetry_ms",
        "open_A_s",
        "block_A_s",
        "open_B_s",
        "block_B_s",
        "PCPS_pps_raw_error_cycles",
        "PCPS_pps_raw_error_ns",
        "PCPS_pps_raw_error_ppm",
        "PCPS_offline_pps_adjusted_residual_cycles",
        "PCPS_offline_pps_adjusted_residual_ns",
        "PCPS_offline_pps_adjusted_residual_ppm",
        "PCPS_latency16_valid",
        "PCPS_cap16_valid",
    ]
    available = {str(row["metric"]): row for _, row in stats.iterrows()}
    rows: List[Dict[str, Any]] = []
    for metric in preferred:
        if metric not in available:
            continue
        source = available[metric]
        spread = _finite_or_nan(source.get("robust_sigma"))
        spread_name = "robust sigma"
        if not np.isfinite(spread) or spread == 0:
            iqr = _finite_or_nan(source.get("IQR"))
            if np.isfinite(iqr) and iqr != 0:
                spread = iqr
                spread_name = "IQR"
        note = spread_name
        spread_note = source.get("spread_note")
        if isinstance(spread_note, str) and spread_note:
            note = f"{note}; {spread_note}"
        rows.append(
            {
                "metric": metric,
                "n": source.get("n"),
                "median": source.get("median"),
                "robust_spread": spread,
                "p05": source.get("p05"),
                "p95": source.get("p95"),
                "min": source.get("min"),
                "max": source.get("max"),
                "note": note,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_sanity_checks(bundle: CanonicalV2Bundle) -> pd.DataFrame:
    rows: List[Dict[str, str]] = []
    quality = bundle.quality
    counts = quality.get("rows", {})
    _append_check(rows, "PCSW input rows", int(counts.get("PCSW", 0)) > 0, f"{int(counts.get('PCSW', 0)):,} PCSW rows")
    pcps_rows = int(counts.get("PCPS", 0))
    _append_check(rows, "PCPS input rows", pcps_rows > 0, f"{pcps_rows:,} PCPS rows", warn_when_false=True)

    for label in ["PCSW", "PCPS"]:
        diag = quality.get(f"{label}_sequence", {})
        jumps = int(diag.get("non_unit_jumps", 0))
        missing = int(diag.get("total_missing_when_positive", 0))
        nonpositive = int(diag.get("nonpositive_jumps", 0))
        status = "PASS" if jumps == 0 else "WARN"
        rows.append({"check": f"{label} sequence continuity", "status": status, "detail": f"{jumps:,} non-unit jumps; {missing:,} missing forward rows; {nonpositive:,} nonpositive jumps"})

    drop_counts = quality.get("PCSW_drop_counts", {})
    total_drops = sum(int(value) for value in drop_counts.values()) if isinstance(drop_counts, dict) else 0
    rows.append({"check": "PCSW dropout flags", "status": "PASS" if total_drops == 0 else "WARN", "detail": f"{total_drops:,} flagged drops"})

    filters = quality.get("PCPS_filter_counts", {})
    valid_pps = int(filters.get("valid_filtered_intervals", 0)) if isinstance(filters, dict) else 0
    rows.append({"check": "PCPS valid PPS intervals", "status": "PASS" if valid_pps > 0 else "WARN", "detail": f"{valid_pps:,} valid filtered intervals"})

    pps_status = quality.get("pps_adjustment_status") or "unavailable"
    rows.append({"check": "Offline PPS-calibrated swing interval", "status": "PASS" if pps_status == "available" else "WARN", "detail": f"{pps_status}: {quality.get('pps_adjustment_reason', 'n/a')}"})

    phase_empty = int((bundle.phase_fold.get("status", pd.Series(dtype=str)) == "empty").sum()) if not bundle.phase_fold.empty else 0
    rows.append({"check": "Phase-fold bin coverage", "status": "PASS" if phase_empty == 0 else "WARN", "detail": f"{phase_empty:,} empty bins across reported moduli"})

    allan = bundle.allan_sampled
    if allan.empty:
        rows.append({"check": "Swing Allan deviation support", "status": "WARN", "detail": "no Allan tau rows survived filtering"})
    else:
        min_diffs = int(pd.to_numeric(allan.get("n_diffs"), errors="coerce").min())
        rows.append({"check": "Swing Allan deviation support", "status": "PASS", "detail": f"{len(allan):,} tau rows; minimum {min_diffs:,} differences"})

    env_status = "PASS" if not bundle.environmental.empty and pd.to_numeric(bundle.environmental.get("n"), errors="coerce").max() >= 3 else "WARN"
    rows.append({"check": "Environmental association inputs", "status": env_status, "detail": f"{len(bundle.environmental):,} correlation rows"})

    health = bundle.pcps_health
    if health.timeseries.empty:
        rows.append({"check": "PCPS timebase health diagnostics", "status": "WARN", "detail": "no PCPS timebase timeseries available"})
    else:
        status = "PASS" if not health.warnings else "WARN"
        detail = "no warnings" if not health.warnings else "; ".join(health.warnings[:3])
        rows.append({"check": "PCPS timebase health diagnostics", "status": status, "detail": detail})
    return pd.DataFrame(rows, columns=["check", "status", "detail"])


def build_health_scorecard(bundle: CanonicalV2Bundle) -> pd.DataFrame:
    stats = bundle.statistics
    rows = [
        _score_mean_period(stats, bundle.quality),
        _score_repeatability(stats),
        _score_stability(bundle.allan_sampled),
        _score_half_cycle_asymmetry(stats),
        _score_dropouts(bundle.quality),
        _score_timebase(bundle),
        _score_environment(bundle.environmental),
        _score_phase_impulse(bundle),
    ]
    return pd.DataFrame(rows, columns=["dimension", "assessment", "basis"])


def build_relationship_summary(bundle: CanonicalV2Bundle) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    pcsw = bundle.pcsw_sampled
    internal_pairs = [
        ("half_asymmetry_ms vs full_resid_us", "half_asymmetry_ms", "full_resid_us"),
        ("full_ppm_vs_inferred vs full_resid_us", "full_ppm_vs_inferred", "full_resid_us"),
    ]
    for label, left, right in internal_pairs:
        if left in pcsw.columns and right in pcsw.columns:
            corr, n = _series_corr(pcsw[left], pcsw[right])
            rows.append({"relationship": label, "method": "same-row Pearson", "r_or_effect": corr, "n": n, "note": "internal derived timing association"})

    if not bundle.environmental.empty:
        env = bundle.environmental.copy()
        env["abs_corr"] = pd.to_numeric(env["correlation"], errors="coerce").abs()
        env = env.sort_values("abs_corr", ascending=False).head(5)
        for _, row in env.iterrows():
            rows.append(
                {
                    "relationship": f"{row.get('env_var')} vs {row.get('metric')}",
                    "method": row.get("method"),
                    "r_or_effect": row.get("correlation"),
                    "n": row.get("n"),
                    "note": f"window={row.get('window_name')}; lag_s={row.get('lag_s')}",
                }
            )

    if not bundle.phase_fold.empty:
        phase = bundle.phase_fold.loc[bundle.phase_fold["status"] == "ok"].copy()
        if not phase.empty:
            phase["median"] = pd.to_numeric(phase["median"], errors="coerce")
            for modulus, group in phase.groupby("modulus"):
                finite = group["median"].dropna()
                if not finite.empty:
                    rows.append(
                        {
                            "relationship": f"phase-fold residual structure mod {int(modulus)}",
                            "method": "phase median range",
                            "r_or_effect": float(finite.max() - finite.min()),
                            "n": int(group["n"].sum()),
                            "note": "effect in microseconds; phase structure, not causal attribution",
                        }
                    )

    if not bundle.fft_components.empty:
        fft = bundle.fft_components.copy()
        fft["relative_amplitude"] = pd.to_numeric(fft["relative_amplitude"], errors="coerce")
        strongest = fft.sort_values("relative_amplitude", ascending=False).head(3)
        for _, row in strongest.iterrows():
            rows.append(
                {
                    "relationship": f"{row.get('series')} spectral component",
                    "method": row.get("preprocessing"),
                    "r_or_effect": row.get("relative_amplitude"),
                    "n": np.nan,
                    "note": f"period_s={_fmt_report_value(row.get('period_s'), 'period_s')}; {row.get('structure_label', '')}",
                }
            )

    result = pd.DataFrame(rows, columns=["relationship", "method", "r_or_effect", "n", "note"])
    if result.empty:
        return result
    result["_rank"] = pd.to_numeric(result["r_or_effect"], errors="coerce").abs()
    return result.sort_values("_rank", ascending=False).drop(columns=["_rank"]).reset_index(drop=True)


def build_report_provenance(bundle: CanonicalV2Bundle, exact_command: str, input_label: str) -> pd.DataFrame:
    quality = bundle.quality
    rows: List[Dict[str, Any]] = [
        {"item": "analysis_version", "value": __version__},
        {"item": "generated_utc", "value": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        {"item": "input_label", "value": input_label},
        {"item": "exact_command", "value": exact_command},
        {"item": "git_commit", "value": _git_output(["git", "rev-parse", "HEAD"])},
        {"item": "git_dirty", "value": "yes" if _git_output(["git", "status", "--porcelain"]) else "no"},
        {"item": "configuration_hash", "value": quality.get("configuration_hash", "n/a")},
        {"item": "python", "value": platform.python_version()},
    ]
    for package in ["numpy", "pandas", "matplotlib", "scipy"]:
        rows.append({"item": f"{package}_version", "value": _package_version(package)})
    input_paths = quality.get("input_paths", {})
    input_hashes = quality.get("input_sha256", {})
    if isinstance(input_paths, dict):
        for key in sorted(input_paths):
            rows.append({"item": f"input_{key}_path", "value": input_paths[key]})
            rows.append({"item": f"input_{key}_sha256", "value": input_hashes.get(key, "n/a") if isinstance(input_hashes, dict) else "n/a"})
    return pd.DataFrame(rows, columns=["item", "value"])


def build_executive_summary(bundle: CanonicalV2Bundle) -> List[str]:
    quality = bundle.quality
    stats = bundle.statistics
    duration = quality.get("duration_hours", {})
    rows = quality.get("rows", {})
    full_period = _stat_value(stats, "full_pps_adj_s", "median")
    residual_spread = _stat_value(stats, "full_resid_us", "robust_sigma")
    allan = bundle.allan_sampled
    if allan.empty:
        allan_text = "No swing Allan tau row survived the configured confidence threshold."
    else:
        best = allan.loc[pd.to_numeric(allan["adev_fractional"], errors="coerce").idxmin()]
        allan_text = f"The sampled Allan curve reaches its lowest reported point near tau={_fmt_report_value(best.get('tau_s'), 'tau_s')} s with {int(best.get('n_diffs')):,} differences."
    return [
        f"- Dataset: {int(rows.get('PCSW', 0)):,} PCSW rows over {_fmt_report_value(duration.get('PCSW'), 'duration_hours')} hours and {int(rows.get('PCPS', 0)):,} PCPS rows over {_fmt_report_value(duration.get('PCPS'), 'duration_hours')} hours.",
        f"- Primary timing result: PPS-calibrated median full-cycle period is {_fmt_report_value(full_period, 'full_s')} s; robust residual spread is {_fmt_report_value(residual_spread, 'full_resid_us')} us.",
        f"- Stability view: {allan_text}",
        f"- Data quality: swing PPS adjustment status is {quality.get('pps_adjustment_status', 'unavailable')}; standalone PPS diagnostics are linked below when PCPS/CPS input is available.",
        "- Interpretation: mechanics, environmental associations, and phase structure are reported here; timebase health is reported by the standalone PPS analysis.",
    ]


def measurement_provenance_section() -> str:
    measured = _markdown_table(
        ["quantity", "source"],
        [
            ["PCSW edge timestamps", "firmware capture rows in PCSW.CSV"],
            ["PCPS PPS timestamps/status", "firmware PPS rows in PCPS.CSV"],
            ["temperature/humidity/pressure", "logged columns when present in input files"],
            ["drop and GPS status flags", "firmware-provided status fields"],
        ],
    )
    derived = _markdown_table(
        ["quantity", "derivation"],
        [
            ["open/block/half/full seconds", "edge-cycle intervals divided by nominal or configured clock rate"],
            ["full residuals and ppm", "full_s relative to inferred median full period"],
            ["phase folds and FFT components", "host-side residual diagnostics"],
            ["Allan deviation", "host-side overlapping Allan deviation with configured minimum difference count"],
            ["environmental correlations", "host-side Pearson association scans over reported lag grid"],
        ],
    )
    return f"Measured inputs:\n\n{measured}\n\nDerived diagnostics:\n\n{derived}"


def _profile_capability_section(bundle: CanonicalV2Bundle) -> str:
    selection = bundle.profile_selection
    profile = selection.selected
    inferred = selection.inferred_nominal_period_s
    capability_rows = []
    for key, value in bundle.capabilities.to_dict().items():
        if not key.startswith("supports_"):
            continue
        reason = "enabled" if value else bundle.capabilities.reasons.get(key, "not enabled")
        capability_rows.append([key.replace("supports_", ""), "yes" if value else "no", reason])
    skipped = []
    if not bundle.capabilities.supports_phase_fold_analysis:
        skipped.append(["phase_fold", "Skipped - no configured or detected cycle model."])
    if not bundle.capabilities.supports_tick_tock_analysis:
        skipped.append(["tick_tock_diagnostics", "Skipped - alternating tick/tock fields unavailable."])
    if not bundle.capabilities.supports_block_analysis:
        skipped.append(["block_interval_diagnostics", "Skipped - block interval columns unavailable or not defined by profile."])
    rows = [
        ["selected profile", profile.name],
        ["display name", profile.display_name],
        ["selection mode", selection.mode],
        ["requested profile", selection.requested],
        ["inferred nominal period", _fmt_report_value(inferred, "full_s")],
        ["cycle suggestion", selection.reason],
        ["profile notes", profile.notes],
    ]
    text = _markdown_table(["item", "value"], rows)
    text += "\n\nCapabilities:\n\n" + _markdown_table(["capability", "enabled", "reason"], capability_rows)
    if skipped:
        text += "\n\nSkipped profile-specific analyses:\n\n" + _markdown_table(["analysis", "reason"], skipped)
    return text


def _spectral_note(bundle: CanonicalV2Bundle) -> str:
    caption = "Expected structures are supplied by the active clock profile and/or FFT config. Unmatched peaks are candidates for further investigation."
    if bundle.profile_selection.selected.supports("impulse_cycle"):
        return f"Raw residual spectra may show profile-defined impulse-cycle harmonics; phase-depatterned spectra ask what remains after removing repeatable cycle structure. {caption}"
    return f"Generic/core mode reports configured or auto-detected cycle harmonics only; clock-family interpretation is suppressed unless a supporting profile is explicitly selected. {caption}"


def _compact_fft_table(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = pd.DataFrame(
        {
            "series": frame.get("series", ""),
            "rank": frame.get("rank", ""),
            "period": frame.get("period_s", ""),
            "frequency": frame.get("frequency_hz", frame.get("frequency_Hz", "")),
            "relative amplitude": pd.to_numeric(frame.get("relative_amplitude_to_max", frame.get("relative_amplitude", pd.Series(dtype=float))), errors="coerce") * 100.0,
            "expected?": frame.get("expected_match", False).map(lambda value: "yes" if bool(value) else "no") if "expected_match" in frame.columns else "",
            "match / label": frame.get("expected_label", frame.get("structure_label", "")),
            "note": frame.get("interpretation", ""),
        }
    )
    result["period"] = pd.to_numeric(result["period"], errors="coerce").map(lambda value: f"{value:.3f} s" if pd.notna(value) else "")
    result["frequency"] = pd.to_numeric(result["frequency"], errors="coerce").map(lambda value: f"{value:.5g} Hz" if pd.notna(value) else "")
    result["relative amplitude"] = pd.to_numeric(result["relative amplitude"], errors="coerce").map(lambda value: f"{value:.1f}%" if pd.notna(value) else "")
    return result


def _synchronome_jitter_report_section(bundle: CanonicalV2Bundle, registry: ArtifactRegistry) -> str:
    if bundle.profile_selection.selected.name != "synchronome":
        return ""
    summary = bundle.synchronome_jitter_summary
    phase = bundle.synchronome_phase15_jitter
    if summary.empty or phase.empty:
        return "## Synchronome impulse/jitter localisation\n\n_No sufficient data._"
    hottest = _summary_metric(summary, "hottest phase")
    ratio = _summary_metric(summary, "hotspot/baseline ratio")
    visible = _summary_metric(summary, "tick/tock visible")
    ratio_value = _finite_or_nan(ratio)
    concentration = "yes" if np.isfinite(ratio_value) and ratio_value >= 1.5 else "weak/no"
    ratio_text = f"{_fmt_report_value(ratio, 'ratio')}x" if np.isfinite(ratio_value) else "n/a"
    figure = ""
    try:
        artifact = registry.get("pcsw", "plot", "synchronome_phase15_jitter_polar")
        figure = f"\n\nFigure {artifact.id}: {artifact.title}\n\n![Figure {artifact.id}: {artifact.title}]({registry.markdown_path(artifact)})"
    except KeyError:
        figure = ""
    display = summary.loc[summary["metric"].isin(
        [
            "raw residual jitter",
            "after removing 15-phase template",
            "after removing slow drift",
            "robust residual jitter",
            "hottest phase",
            "hotspot/baseline ratio",
        ]
    )].copy()
    return (
        "## Synchronome impulse/jitter localisation\n\n"
        f"Residual timing noise concentration by 15-beat phase: {concentration}. "
        f"Hotspot phase: {_fmt_report_value(hottest, 'phase')}; hotspot/baseline: {ratio_text}. "
        f"Tick/tock/half-cycle residual visibility: {visible}.\n\n"
        f"{_frame_table(display, max_rows=8)}"
        f"{figure}"
    )


def _summary_metric(summary: pd.DataFrame, metric: str) -> Any:
    rows = summary.loc[summary["metric"].eq(metric), "value"] if "metric" in summary.columns and "value" in summary.columns else pd.Series(dtype=object)
    return rows.iloc[0] if not rows.empty else np.nan


def _long_run_triage_section(bundle: CanonicalV2Bundle) -> str:
    lines: List[str] = []
    planned = {row.name: row for row in bundle.analysis_plan.analyses}
    for name, frame in [
        ("time_of_day_fold", bundle.time_of_day_fold),
        ("daily_summary", bundle.daily_summary),
        ("environmental_lag_correlations", bundle.environmental_lag),
        ("rolling_stability", bundle.rolling_stability),
        ("rolling_allan_deviation", bundle.rolling_allan),
        ("diurnal_harmonic_fit", bundle.diurnal_harmonic),
        ("change_point_detection", bundle.change_points),
    ]:
        plan = planned.get(name)
        status = plan.status if plan else "SKIP"
        reason = plan.reason if plan else "not planned"
        rows = len(frame) if isinstance(frame, pd.DataFrame) else 0
        lines.append(f"- {name}: {status}; {rows:,} row(s). {reason}")
    if bundle.investigation_next:
        lines.extend(["", "What to investigate next:"])
        lines.extend([f"- {item}" for item in bundle.investigation_next])
    else:
        lines.extend(["", "What to investigate next was skipped because long-run triage did not run."])
    return "\n".join(lines)


def _mapping_table(mapping: Dict[str, Any], key_label: str, value_label: str) -> str:
    if not mapping:
        return "_No sufficient data._"
    rows = []
    for key, value in sorted(mapping.items(), key=lambda item: str(item[0])):
        format_column = str(key) if value_label == "value" else value_label
        if value_label == "value" and isinstance(value, (np.integer, int)) and not isinstance(value, bool):
            format_column = "count"
        rows.append([key, _fmt_report_value(value, format_column)])
    return _markdown_table([key_label, value_label], rows)


def _key_value_sections(sections: Dict[str, Dict[str, Any]]) -> str:
    parts: List[str] = []
    for title, mapping in sections.items():
        parts.extend([f"### {title}", "", _mapping_table(mapping or {}, "item", "value"), ""])
    return "\n".join(parts).rstrip()


def _pendulum_interpretation(bundle: CanonicalV2Bundle) -> str:
    stats = bundle.statistics
    asym = _stat_value(stats, "half_asymmetry_ms", "median")
    full = _stat_value(stats, "full_s", "median")
    resid = _stat_value(stats, "full_resid_us", "robust_sigma")
    return (
        f"The pendulum summary below is compact by design: the CSV contains the full percentile/statistical export. "
        f"The report focuses on the median full-cycle period ({_fmt_report_value(full, 'full_s')} s), robust full residual spread "
        f"({_fmt_report_value(resid, 'full_resid_us')} us), and half-cycle asymmetry ({_fmt_report_value(asym, 'half_asymmetry_ms')} ms). "
        "These are derived diagnostics from the captured edge timings, not new measured channels."
    )


def _relationship_note(relationships: pd.DataFrame) -> str:
    if relationships.empty:
        return "No relationship rows had enough data to report. This section is intentionally left empty rather than filling missing associations."
    strongest = relationships.iloc[0]
    return (
        "Rows are ranked by absolute correlation or effect size. They are screening diagnostics only; a large value points to a structure worth reviewing, "
        f"not to a demonstrated cause. Strongest reported row: {strongest.get('relationship')} via {strongest.get('method')}."
    )


def _score_mean_period(stats: pd.DataFrame, quality: Dict[str, Any]) -> Dict[str, str]:
    median = _stat_value(stats, "full_s", "median")
    target = quality.get("inferred_full_period_s", np.nan)
    if not np.isfinite(median):
        return {"dimension": "mean period", "assessment": "Insufficient data", "basis": "full_s median unavailable"}
    delta_ppm = abs((median / target - 1.0) * 1_000_000.0) if np.isfinite(target) and target else 0.0
    assessment = "Excellent" if delta_ppm < 1 else "Good" if delta_ppm < 10 else "Watch"
    return {"dimension": "mean period", "assessment": assessment, "basis": f"median full_s={_fmt_report_value(median, 'full_s')} s; inferred reference delta={_fmt_report_value(delta_ppm, 'ppm')} ppm"}


def _score_repeatability(stats: pd.DataFrame) -> Dict[str, str]:
    spread = _stat_value(stats, "full_resid_us", "robust_sigma")
    if not np.isfinite(spread):
        return {"dimension": "repeatability", "assessment": "Insufficient data", "basis": "full_resid_us robust sigma unavailable"}
    assessment = "Excellent" if abs(spread) < 50 else "Good" if abs(spread) < 250 else "Watch"
    return {"dimension": "repeatability", "assessment": assessment, "basis": f"robust residual spread={_fmt_report_value(spread, 'full_resid_us')} us"}


def _score_stability(allan: pd.DataFrame) -> Dict[str, str]:
    if allan.empty:
        return {"dimension": "stability", "assessment": "Insufficient data", "basis": "no Allan rows after confidence filtering"}
    best = float(pd.to_numeric(allan["adev_fractional"], errors="coerce").min())
    assessment = "Excellent" if best < 1e-6 else "Good" if best < 1e-5 else "Watch"
    return {"dimension": "stability", "assessment": assessment, "basis": f"best reported Allan deviation={_fmt_report_value(best, 'adev_fractional')}"}


def _score_half_cycle_asymmetry(stats: pd.DataFrame) -> Dict[str, str]:
    asym = _stat_value(stats, "half_asymmetry_ms", "median")
    if not np.isfinite(asym):
        return {"dimension": "half-cycle asymmetry", "assessment": "Insufficient data", "basis": "half_asymmetry_ms unavailable"}
    assessment = "Excellent" if abs(asym) < 1 else "Good" if abs(asym) < 10 else "Watch"
    return {"dimension": "half-cycle asymmetry", "assessment": assessment, "basis": f"median asymmetry={_fmt_report_value(asym, 'half_asymmetry_ms')} ms"}


def _score_dropouts(quality: Dict[str, Any]) -> Dict[str, str]:
    drops = quality.get("PCSW_drop_counts", {})
    seq = quality.get("PCSW_sequence", {})
    total = sum(int(value) for value in drops.values()) if isinstance(drops, dict) else 0
    jumps = int(seq.get("non_unit_jumps", 0)) if isinstance(seq, dict) else 0
    assessment = "Excellent" if total == 0 and jumps == 0 else "Good" if total == 0 else "Watch"
    return {"dimension": "dropouts", "assessment": assessment, "basis": f"{total:,} PCSW drop flags; {jumps:,} sequence jumps"}


def _score_timebase(bundle: CanonicalV2Bundle) -> Dict[str, str]:
    filters = bundle.quality.get("PCPS_filter_counts", {})
    valid = int(filters.get("valid_filtered_intervals", 0)) if isinstance(filters, dict) else 0
    if bundle.pcps_health.timeseries.empty:
        return {"dimension": "timebase/GPS", "assessment": "Insufficient data", "basis": "PCPS health timeseries unavailable"}
    assessment = "Good" if valid > 0 and not bundle.pcps_health.warnings else "Watch"
    return {"dimension": "timebase/GPS", "assessment": assessment, "basis": f"{valid:,} valid PPS intervals; warnings={len(bundle.pcps_health.warnings):,}"}


def _score_environment(environmental: pd.DataFrame) -> Dict[str, str]:
    if environmental.empty:
        return {"dimension": "environmental coupling", "assessment": "Insufficient data", "basis": "no environmental association rows"}
    strongest = float(pd.to_numeric(environmental["correlation"], errors="coerce").abs().max())
    assessment = "Excellent" if strongest < 0.1 else "Good" if strongest < 0.3 else "Watch"
    return {"dimension": "environmental coupling", "assessment": assessment, "basis": f"strongest absolute Pearson r={_fmt_report_value(strongest, 'correlation')}"}


def _score_phase_impulse(bundle: CanonicalV2Bundle) -> Dict[str, str]:
    if bundle.phase_fold.empty and bundle.fft_components.empty:
        return {"dimension": "phase/impulse", "assessment": "Insufficient data", "basis": "phase and FFT diagnostics unavailable"}
    empty_bins = int((bundle.phase_fold.get("status", pd.Series(dtype=str)) == "empty").sum()) if not bundle.phase_fold.empty else 0
    structured = bool(not bundle.fft_components.empty and bundle.fft_components.get("structure_label", pd.Series(dtype=str)).astype(str).str.len().gt(0).any())
    assessment = "Watch" if empty_bins or structured else "Good"
    detail = f"{empty_bins:,} empty phase bins"
    if structured:
        detail += "; spectral rows near expected mechanical structure"
    return {"dimension": "phase/impulse", "assessment": assessment, "basis": detail}


def _append_check(rows: List[Dict[str, str]], check: str, passed: bool, detail: str, warn_when_false: bool = False) -> None:
    rows.append({"check": check, "status": "PASS" if passed else ("WARN" if warn_when_false else "FAIL"), "detail": detail})


def _series_corr(left: pd.Series, right: pd.Series) -> tuple[float, int]:
    frame = pd.concat([pd.to_numeric(left, errors="coerce"), pd.to_numeric(right, errors="coerce")], axis=1).dropna()
    n = int(len(frame))
    if n < 3 or frame.iloc[:, 0].nunique() <= 1 or frame.iloc[:, 1].nunique() <= 1:
        return np.nan, n
    return float(frame.iloc[:, 0].corr(frame.iloc[:, 1])), n


def _stat_value(stats: pd.DataFrame, metric: str, column: str) -> float:
    if stats.empty or column not in stats.columns:
        return float("nan")
    rows = stats.loc[stats["metric"] == metric, column]
    if rows.empty:
        return float("nan")
    return _finite_or_nan(rows.iloc[0])


def _finite_or_nan(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if np.isfinite(result) else float("nan")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return "unavailable"
    return digest.hexdigest()


def _configuration_hash(config: AnalysisConfig) -> str:
    return config.config_hash()


def _git_output(args: List[str]) -> str:
    try:
        return subprocess.check_output(args, cwd=Path(__file__).resolve().parents[1], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _package_version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unavailable"


def _pps_adjustment_report_note(quality: Dict[str, Any]) -> str:
    status = quality.get("pps_adjustment_status")
    reason = quality.get("pps_adjustment_reason") or "no usable PPS adjustment provenance"
    if status == "available":
        return "- PPS-adjusted swing intervals are available and differ from raw full_s."
    return f"- PPS-adjusted swing intervals unavailable/no-op for this dataset; raw full_s used ({reason})."


def _pcps_health_note(health: PcpsHealthResult, quality: Dict[str, Any]) -> str:
    if health.timeseries.empty:
        return "PCPS timebase health could not be assessed because no PCPS rows were available. PCSW pendulum diagnostics continue, but clock trust must be established elsewhere."
    warnings = health.warnings
    valid = int(quality.get("PCPS_filter_counts", {}).get("valid_filtered_intervals", 0))
    rows = int(len(health.timeseries))
    suitability = "appears suitable for interpreting PCSW" if valid > 0 and not any("no GPS lock" in warning for warning in warnings) else "needs caution before interpreting PCSW"
    lines = [
        f"PCPS is treated here as the GPS/PPS timebase health record, separate from PCSW pendulum timing. It has {rows:,} rows and {valid:,} valid filtered PPS intervals, so the timebase {suitability}.",
        "Warnings: " + ("; ".join(warnings) if warnings else "none"),
    ]
    return "\n\n".join(lines)


def _cap16_report_note(summary: pd.DataFrame) -> str:
    if summary.empty:
        return "cap16 phase coverage could not be assessed because no summary rows were produced."
    row = summary.iloc[0]
    assessment = row.get("uniformity_assessment", "INSUFFICIENT_DATA")
    reason = row.get("assessment_reason", "no reason available")
    gap = _fmt_report_value(row.get("largest_empty_gap_ticks"), "count")
    max_ratio = _fmt_report_value(row.get("max_bin_over_expected"), "max_bin_over_expected")
    return (
        f"Assessment: {assessment}. Reason: {reason}. Largest circular empty gap is {gap} ticks; "
        f"maximum bin/expected ratio is {max_ratio}. This is a capture-system sanity check, not a pendulum-performance metric."
    )


def _latency_report_note(summary: pd.DataFrame, outliers: pd.DataFrame) -> str:
    if summary.empty:
        return "latency16 could not be assessed because no summary rows were produced."
    row = summary.iloc[0]
    assessment = row.get("latency_assessment", "INSUFFICIENT_DATA")
    reason = row.get("assessment_reason", "no reason available")
    median = _fmt_report_value(row.get("median_cycles"), "median_cycles")
    p99 = _fmt_report_value(row.get("p99_cycles"), "p99_cycles")
    threshold = _fmt_report_value(row.get("outlier_threshold_cycles"), "outlier_threshold_cycles")
    text = (
        "latency16 describes capture/ISR service latency. Isolated spikes are measurement-system events unless accompanied by PPS residuals, "
        "GPS unlock, holdover, drops, or sustained jitter. "
        f"Assessment: {assessment}. Reason: {reason}. Median={median} cycles, p99={p99} cycles, threshold={threshold} cycles."
    )
    if outliers.empty:
        return text + " No latency outlier context rows were emitted."
    strongest = outliers.sort_values("latency_cycles", ascending=False).iloc[0]
    elapsed = _fmt_report_value(strongest.get("pps_t_hr"), "duration_hours") if "pps_t_hr" in strongest else "n/a"
    isolated = strongest.get("isolated", "n/a")
    gps = strongest.get("gps_status", "n/a")
    drop = strongest.get("drop_pps", "n/a")
    return (
        text
        + f" Largest outlier occurs at elapsed {elapsed} h with latency {_fmt_report_value(strongest.get('latency_cycles'), 'latency_cycles')} cycles; "
        + f"isolated={isolated}, gps_status={gps}, drop_pps={drop}, disturbed_window_overlap={strongest.get('disturbed_window_overlap', 'not_configured')}."
    )


def _count_csv_rows(path: Optional[Path]) -> int:
    if path is None or not path.exists():
        return 0
    with path.open("rb") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def _drop_counts(data: pd.DataFrame, columns: List[str]) -> Dict[str, int]:
    return {column: _sum_column(data, column) for column in columns if column in data.columns}


def _sum_column(data: pd.DataFrame, column: str) -> int:
    if column not in data.columns:
        return 0
    return int(pd.to_numeric(data[column], errors="coerce").fillna(0).sum())


def _value_counts(values: Optional[pd.Series]) -> Dict[str, int]:
    if values is None:
        return {}
    return {str(k): int(v) for k, v in pd.to_numeric(values, errors="coerce").value_counts(dropna=False).sort_index().items()}


def _pcps_filter_counts(pcps: pd.DataFrame, config: AnalysisConfig) -> Dict[str, int]:
    if pcps.empty:
        return {
            "candidate_intervals_after_first_row": 0,
            "consecutive_seq": 0,
            "plausible_0p75_to_1p25_sec": 0,
            "gps_status_2_rows": 0,
            "drop_pps_zero_rows": 0,
            "valid_filtered_intervals": 0,
            "offline_residual_rows_prev31_available": 0,
        }
    interval = pd.to_numeric(pcps["pps_interval_cycles"], errors="coerce")
    return {
        "candidate_intervals_after_first_row": int(interval.notna().sum()),
        "consecutive_seq": int(pcps.get("consecutive_seq", False).fillna(False).sum()),
        "plausible_0p75_to_1p25_sec": int(interval.between(config.nominal_hz * 0.75, config.nominal_hz * 1.25).sum()),
        "gps_status_2_rows": int((pd.to_numeric(pcps.get(CANONICAL_PPS.gps_status), errors="coerce") == 2).sum()) if CANONICAL_PPS.gps_status in pcps.columns else 0,
        "drop_pps_zero_rows": int((pd.to_numeric(pcps.get(CANONICAL_PPS.drop_pps), errors="coerce").fillna(0) == 0).sum()) if CANONICAL_PPS.drop_pps in pcps.columns else 0,
        "valid_filtered_intervals": int(pcps.get("valid_pps_interval", False).fillna(False).sum()),
        "offline_residual_rows_prev31_available": int(pd.to_numeric(pcps.get("offline_pps_adjusted_residual_cycles"), errors="coerce").notna().sum())
        if "offline_pps_adjusted_residual_cycles" in pcps.columns
        else 0,
    }


def _nominal_source(discovered: Dict[str, Optional[Path]], config: AnalysisConfig) -> str:
    for key in ["sts", "cfg"]:
        path = discovered.get(key)
        if path is None or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in [r"\bnhz[=,](\d+(?:\.\d+)?)", r"\bnominal_hz[=,](\d+(?:\.\d+)?)"]:
            match = re.search(pattern, text)
            if match:
                return f"{key.upper()} cfg nhz={match.group(1)}"
        try:
            frame = pd.read_csv(path)
        except Exception:
            continue
        if {"key", "value"}.issubset(frame.columns):
            keys = frame["key"].astype(str).str.lower()
            hits = frame.loc[keys.isin(["nhz", "nominal_hz"]), "value"]
            if not hits.empty:
                return f"{key.upper()} cfg nhz={hits.iloc[0]}"
    return f"CLI/default nominal_hz={config.nominal_hz:g}"


def _fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if not np.isfinite(value):
            return "n/a"
        return f"{value:.{digits}g}"
    return str(value)


def _fmt_report_value(value: Any, column: str) -> str:
    if value is None:
        return "n/a"
    col = column.lower()
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return f"{int(value):,}" if _looks_like_count_column(col) else str(int(value))
    if isinstance(value, (np.floating, float)):
        if not np.isfinite(value):
            return "n/a"
        if _looks_like_count_column(col):
            return f"{int(round(float(value))):,}"
        if "corr" in col or column == "correlation":
            return f"{float(value):.3f}"
        if "ppm" in col:
            return f"{float(value):.3g}"
        if col in {"phase", "hottest phase"}:
            return str(int(round(float(value))))
        if "ratio" in col:
            return f"{float(value):.2f}"
        if "adev" in col or "allan" in col:
            return f"{float(value):.3g}"
        if "ms" in col:
            return f"{float(value):.3f}"
        if "percent" in col or col.endswith("_pct") or "percentage" in col:
            return f"{float(value):.2f}"
        if "fraction" in col:
            return f"{float(value) * 100.0:.2f}%"
        if col.endswith("_s") or "period" in col or "duration" in col or "seconds" in col:
            return f"{float(value):.6f}".rstrip("0").rstrip(".")
        if "pvalue" in col or "p_value" in col:
            return f"{float(value):.3e}"
        return f"{float(value):.6g}"
    return str(value)


def _looks_like_count_column(column: str) -> bool:
    return (
        column in {"n", "count", "rows", "unique_values", "sample_stride", "n_averages", "n_diffs"}
        or column.endswith("_rows")
        or column.endswith("_count")
        or column.endswith("_counts")
        or any(token in column for token in ["intervals", "jumps", "missing", "transitions"])
    )


def _dict_table(data: Dict[str, Any], keys: List[str]) -> str:
    rows = []
    for key in keys:
        value = data.get(key)
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, sort_keys=True)
        else:
            value_text = _fmt_report_value(value, key)
        rows.append([key, value_text])
    return _markdown_table(["item", "value"], rows)


def _frame_table(frame: pd.DataFrame, max_rows: int) -> str:
    if frame.empty:
        return "_No sufficient data._"
    shown = frame.head(max_rows).copy()
    columns = list(shown.columns)
    rows = []
    for _, row in shown.iterrows():
        values: List[str] = []
        for column in columns:
            value = row[column]
            format_column = str(column)
            if format_column == "value":
                candidate = row.get("stat", row.get("metric", "value"))
                candidate_text = str(candidate)
                format_column = str(row.get("metric", "value")) if candidate_text == "value" or candidate_text.replace(".", "", 1).isdigit() else candidate_text
            values.append(_fmt_report_value(value, format_column))
        rows.append(values)
    return _markdown_table([str(column) for column in columns], rows)


def _markdown_table(headers: List[str], rows: List[List[Any]]) -> str:
    text_rows = [[_markdown_cell(cell) for cell in row] for row in rows]
    text_headers = [_markdown_cell(header) for header in headers]
    widths = [
        max(len(text_headers[index]), *(len(row[index]) for row in text_rows)) if text_rows else len(text_headers[index])
        for index in range(len(text_headers))
    ]
    aligns = [_markdown_column_alignment(index, text_rows) for index in range(len(text_headers))]

    def fmt_row(values: List[str]) -> str:
        cells = []
        for index, value in enumerate(values):
            cells.append(value.rjust(widths[index]) if aligns[index] == "right" else value.ljust(widths[index]))
        return "| " + " | ".join(cells) + " |"

    separator_cells = []
    for index, width in enumerate(widths):
        marker = "-" * max(3, width)
        if aligns[index] == "right":
            marker = marker[:-1] + ":"
        separator_cells.append(marker)
    separator = "| " + " | ".join(separator_cells) + " |"
    return "\n".join([fmt_row(text_headers), separator, *(fmt_row(row) for row in text_rows)])


def _markdown_column_alignment(index: int, rows: List[List[str]]) -> str:
    values = [row[index].strip() for row in rows if row[index].strip() and row[index].strip() != "n/a"]
    if not values:
        return "left"
    return "right" if all(_looks_markdown_numeric(value) for value in values) else "left"


def _looks_markdown_numeric(value: str) -> bool:
    text = value.replace(",", "").strip()
    for suffix in ["%", "x", " s", " Hz", " us", " ms", " ppm"]:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    try:
        float(text)
    except ValueError:
        return False
    return True


def _markdown_cell(value: Any) -> str:
    if value is None:
        text = "n/a"
    else:
        text = str(value)
    return text.replace("\n", " ").replace("|", r"\|")
