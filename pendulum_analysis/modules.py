"""Plugin-style analysis module declarations used by the planner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .results import AnalysisResult


AnalysisRunner = Callable[..., AnalysisResult]


@dataclass(frozen=True)
class AnalysisModule:
    name: str
    tier: str
    required_roles: List[str]
    optional_roles: List[str]
    required_capabilities: List[str]
    minimum_duration_days: float
    outputs: List[str]
    runner: Optional[AnalysisRunner] = None


MODULES: Sequence[AnalysisModule] = [
    AnalysisModule("input_provenance", "core", [], [], [], 0.0, ["data_quality_summary_v2.json"]),
    AnalysisModule("sanity_checks", "core", [], [], [], 0.0, []),
    AnalysisModule("basic_interval_statistics", "core", ["period"], [], [], 0.0, ["statistics_summary_v2.csv"]),
    AnalysisModule("data_quality", "core", [], [], [], 0.0, ["warnings.txt"]),
    AnalysisModule("pcps_timebase_health", "core", [], ["pps_residual", "latency16", "cap16"], ["supports_timebase_health"], 0.0, ["pcps_timebase_health_summary.csv"]),
    AnalysisModule("basic_residuals", "core", ["residual"], [], [], 0.0, ["derived_pcsw_intervals_sampled_v2.csv"]),
    AnalysisModule("basic_plots", "core", ["period"], [], [], 0.0, ["01_full_cycle_timing_overview.png"]),
    AnalysisModule("allan_deviation", "standard", ["period"], [], [], 1.0, ["allan_deviation_v2_sampled.csv"]),
    AnalysisModule("fft_spectrum", "standard", ["residual"], [], [], 1.0, ["dominant_fft_components_v2.csv"]),
    AnalysisModule("relationship_summary", "standard", ["residual"], [], [], 1.0, []),
    AnalysisModule("environmental_correlations", "standard", ["residual"], ["temperature", "humidity", "pressure"], ["supports_environmental_analysis"], 1.0, ["environmental_correlations_v2.csv"]),
    AnalysisModule("phase_fold", "standard", ["residual"], [], ["supports_phase_fold_analysis"], 1.0, ["phase_fold_summary_v2.csv"]),
    AnalysisModule("tick_tock_diagnostics", "standard", ["tick_interval", "tock_interval"], [], ["supports_tick_tock_analysis"], 1.0, []),
    AnalysisModule("block_interval_diagnostics", "standard", ["block_interval"], [], ["supports_block_analysis"], 1.0, []),
    AnalysisModule("time_of_day_fold", "medium_run", ["elapsed_time", "residual"], [], [], 7.0, ["time_of_day_fold_v2.csv"]),
    AnalysisModule("daily_summary", "medium_run", ["elapsed_time", "period"], [], [], 7.0, ["daily_summary_v2.csv"]),
    AnalysisModule("anomaly_candidates", "medium_run", ["residual"], [], [], 7.0, ["anomaly_candidates_v2.csv"]),
    AnalysisModule("weekly_structure", "long_run", ["elapsed_time", "residual"], [], [], 21.0, []),
    AnalysisModule("environmental_lag_correlations", "long_run", ["elapsed_time", "residual"], ["temperature", "humidity", "pressure"], ["supports_environmental_analysis"], 21.0, ["environmental_lag_correlations_v2.csv"]),
    AnalysisModule("rolling_stability", "long_run", ["elapsed_time", "residual"], [], [], 21.0, ["rolling_stability_v2.csv"]),
    AnalysisModule("rolling_allan_deviation", "long_run", ["elapsed_time", "period"], [], [], 21.0, ["rolling_allan_deviation_v2.csv"]),
    AnalysisModule("diurnal_harmonic_fit", "long_run", ["elapsed_time", "residual"], [], [], 21.0, ["diurnal_harmonic_fit_v2.csv"]),
    AnalysisModule("change_point_detection", "long_run", ["residual"], [], [], 21.0, ["change_point_candidates_v2.csv"]),
    AnalysisModule("what_to_investigate_next", "long_run", [], [], [], 21.0, []),
]


def module_registry() -> Dict[str, AnalysisModule]:
    return {module.name: module for module in MODULES}
