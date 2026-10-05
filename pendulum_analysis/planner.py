"""Central adaptive analysis planner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Sequence

import pandas as pd

from .config import AnalysisConfig
from .columns import build_column_map
from .modules import MODULES
from .profile import AnalysisCapabilities, ClockProfile


@dataclass(frozen=True)
class DatasetMetadata:
    duration_s: float
    duration_days: float
    row_counts: Dict[str, int]
    available_columns: List[str]
    sample_cadence_s: float | None
    environmental_available: bool
    pcps_available: bool
    pcsw_available: bool
    available_roles: List[str]
    dropped_missing_summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlannedAnalysis:
    name: str
    tier: str
    status: str
    reason: str
    required_inputs: List[str]
    required_capabilities: List[str]
    output_filenames: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalysisPlan:
    analyses: List[PlannedAnalysis]
    profile: str
    requested_profile: str
    duration_days: float

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([row.to_dict() for row in self.analyses])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "requested_profile": self.requested_profile,
            "duration_days": self.duration_days,
            "analyses": [row.to_dict() for row in self.analyses],
        }

    def status(self, name: str) -> str:
        for row in self.analyses:
            if row.name == name:
                return row.status
        return "SKIP"

    def should_run(self, name: str) -> bool:
        return self.status(name) in {"RUN", "FORCED"}


ANALYSES: Sequence[Dict[str, Any]] = [
    {"name": "input_provenance", "tier": "core", "inputs": ["PCSW"], "caps": [], "outputs": ["data_quality_summary_v2.json"]},
    {"name": "sanity_checks", "tier": "core", "inputs": ["PCSW"], "caps": [], "outputs": []},
    {"name": "basic_interval_statistics", "tier": "core", "inputs": ["full_s"], "caps": [], "outputs": ["statistics_summary_v2.csv"]},
    {"name": "data_quality", "tier": "core", "inputs": ["PCSW"], "caps": [], "outputs": ["warnings.txt"]},
    {"name": "pcps_timebase_health", "tier": "core", "inputs": ["PCPS"], "caps": ["supports_timebase_health"], "outputs": ["pcps_timebase_health_summary.csv"]},
    {"name": "basic_residuals", "tier": "core", "inputs": ["full_resid_us"], "caps": [], "outputs": ["derived_pcsw_intervals_sampled_v2.csv"]},
    {"name": "basic_plots", "tier": "core", "inputs": ["full_s"], "caps": [], "outputs": ["01_full_cycle_timing_overview.png"]},
    {"name": "allan_deviation", "tier": "standard", "inputs": ["full_s"], "caps": [], "outputs": ["allan_deviation_v2_sampled.csv"]},
    {"name": "fft_spectrum", "tier": "standard", "inputs": ["full_resid_us"], "caps": [], "outputs": ["dominant_fft_components_v2.csv", "residual_fft_raw_peaks.csv", "residual_fft_phase_depatterned_peaks.csv"]},
    {"name": "relationship_summary", "tier": "standard", "inputs": ["full_resid_us"], "caps": [], "outputs": []},
    {"name": "environmental_correlations", "tier": "standard", "inputs": ["env"], "caps": ["supports_environmental_analysis"], "outputs": ["environmental_correlations_v2.csv"]},
    {"name": "phase_fold", "tier": "standard", "inputs": ["full_resid_us"], "caps": ["supports_phase_fold_analysis"], "outputs": ["phase_fold_summary_v2.csv"]},
    {"name": "tick_tock_diagnostics", "tier": "standard", "inputs": ["half_A_s", "half_B_s"], "caps": ["supports_tick_tock_analysis"], "outputs": []},
    {"name": "block_interval_diagnostics", "tier": "standard", "inputs": ["block_A_s", "block_B_s"], "caps": ["supports_block_analysis"], "outputs": []},
    {"name": "time_of_day_fold", "tier": "medium_run", "inputs": ["t_s", "full_resid_us"], "caps": [], "outputs": ["time_of_day_fold_v2.csv"]},
    {"name": "daily_summary", "tier": "medium_run", "inputs": ["t_s", "full_s"], "caps": [], "outputs": ["daily_summary_v2.csv"]},
    {"name": "anomaly_candidates", "tier": "medium_run", "inputs": ["full_resid_us"], "caps": [], "outputs": ["anomaly_candidates_v2.csv"]},
    {"name": "weekly_structure", "tier": "long_run", "inputs": ["t_s", "full_resid_us"], "caps": [], "outputs": []},
    {"name": "environmental_lag_correlations", "tier": "long_run", "inputs": ["env", "t_s"], "caps": ["supports_environmental_analysis"], "outputs": ["environmental_lag_correlations_v2.csv"]},
    {"name": "rolling_stability", "tier": "long_run", "inputs": ["t_s", "full_resid_us"], "caps": [], "outputs": ["rolling_stability_v2.csv"]},
    {"name": "rolling_allan_deviation", "tier": "long_run", "inputs": ["t_s", "full_s"], "caps": [], "outputs": ["rolling_allan_deviation_v2.csv"]},
    {"name": "diurnal_harmonic_fit", "tier": "long_run", "inputs": ["t_s", "full_resid_us"], "caps": [], "outputs": ["diurnal_harmonic_fit_v2.csv"]},
    {"name": "change_point_detection", "tier": "long_run", "inputs": ["full_resid_us"], "caps": [], "outputs": ["change_point_candidates_v2.csv"]},
    {"name": "what_to_investigate_next", "tier": "long_run", "inputs": [], "caps": [], "outputs": []},
]

TIER_ORDER = {"core": 0, "standard": 1, "medium_run": 2, "long_run": 3}
PROFILE_TIER = {"core": 0, "standard": 1, "medium": 2, "long": 3, "full": 3}
ENV_INPUTS = {"env"}


def build_dataset_metadata(pcsw: pd.DataFrame, pcps: pd.DataFrame, quality: Dict[str, Any]) -> DatasetMetadata:
    duration_hours = quality.get("duration_hours", {}) if isinstance(quality, dict) else {}
    duration_h = max(float(duration_hours.get("PCSW") or 0.0), float(duration_hours.get("PCPS") or 0.0))
    columns = sorted(set(pcsw.columns) | {f"PCPS.{column}" for column in pcps.columns})
    pcps_role_frame = pcps.rename(columns={column: column for column in pcps.columns})
    role_map = build_column_map(pd.concat([pcsw.reset_index(drop=True), pcps_role_frame.reset_index(drop=True)], axis=1))
    t_s = pd.to_numeric(pcsw.get("t_s"), errors="coerce") if "t_s" in pcsw.columns else pd.Series(dtype=float)
    cadence = float(t_s.diff().dropna().median()) if t_s.notna().sum() >= 3 else None
    return DatasetMetadata(
        duration_s=duration_h * 3600.0,
        duration_days=duration_h / 24.0,
        row_counts=quality.get("rows", {}) if isinstance(quality.get("rows"), dict) else {},
        available_columns=columns,
        sample_cadence_s=cadence,
        environmental_available=any(column in pcsw.columns for column in ["temp_C", "temperature_C", "humidity_pct", "pressure_hPa"]),
        pcps_available=not pcps.empty,
        pcsw_available=not pcsw.empty,
        available_roles=sorted(role_map.roles),
        dropped_missing_summary={
            "PCSW_sequence": quality.get("PCSW_sequence", {}),
            "PCPS_sequence": quality.get("PCPS_sequence", {}),
            "PCSW_drop_counts": quality.get("PCSW_drop_counts", {}),
        },
    )


def build_analysis_plan(
    dataset_metadata: DatasetMetadata,
    profile: ClockProfile,
    capabilities: AnalysisCapabilities,
    config: AnalysisConfig,
    requested_profile: str,
) -> AnalysisPlan:
    rows: List[PlannedAnalysis] = []
    enabled = set(config.enabled_analyses)
    disabled = set(config.disabled_analyses)
    max_tier = _max_tier(dataset_metadata.duration_days, config)
    available_roles = set(dataset_metadata.available_roles)

    for module in MODULES:
        name = module.name
        tier = module.tier
        required_inputs = list(module.required_roles)
        required_caps = list(module.required_capabilities)
        outputs = list(module.outputs)
        should_by_tier = TIER_ORDER[tier] <= max_tier
        status = "RUN" if should_by_tier else "SKIP"
        reason = f"{tier} analysis enabled for duration {dataset_metadata.duration_days:.2f} days"

        missing_inputs = _missing_inputs(required_inputs, available_roles, dataset_metadata)
        missing_caps = [cap for cap in required_caps if not bool(getattr(capabilities, cap))]
        if missing_inputs:
            status = "SKIP"
            reason = f"missing required input(s): {', '.join(missing_inputs)}"
        elif missing_caps:
            status = "SKIP"
            reason = "; ".join(capabilities.reasons.get(cap, f"missing capability {cap}") for cap in missing_caps)
        elif not should_by_tier:
            reason = _duration_skip_reason(tier, dataset_metadata.duration_days, config)

        if name == "change_point_detection" and status == "RUN" and not _changepoint_dependency_available():
            status = "SKIP"
            reason = "optional dependency ruptures not installed"

        if name in enabled and status == "SKIP":
            if missing_inputs:
                status = "WARN"
                reason = f"forced but missing required input(s): {', '.join(missing_inputs)}"
            elif missing_caps:
                status = "WARN"
                reason = f"forced but capability unavailable: {'; '.join(missing_caps)}"
            else:
                status = "FORCED"
                reason = "enabled by user override"
        elif name in enabled:
            status = "FORCED"
            reason = "enabled by user override"

        if name in disabled:
            status = "SKIP"
            reason = "disabled by user override"

        rows.append(PlannedAnalysis(name, tier, status, reason, required_inputs, required_caps, outputs))

    return AnalysisPlan(rows, profile.name, requested_profile, dataset_metadata.duration_days)


def list_analysis_names() -> List[str]:
    return [module.name for module in MODULES]


def _max_tier(duration_days: float, config: AnalysisConfig) -> int:
    if config.force_long_run or config.analysis_profile == "full":
        return TIER_ORDER["long_run"]
    if config.analysis_profile != "auto":
        return PROFILE_TIER[config.analysis_profile]
    if duration_days >= config.long_min_duration_days:
        return TIER_ORDER["long_run"]
    if duration_days >= config.medium_min_duration_days:
        return TIER_ORDER["medium_run"]
    if duration_days >= config.standard_min_duration_days:
        return TIER_ORDER["standard"]
    return TIER_ORDER["core"]


def _duration_skip_reason(tier: str, duration_days: float, config: AnalysisConfig) -> str:
    thresholds = {
        "standard": config.standard_min_duration_days,
        "medium_run": config.medium_min_duration_days,
        "long_run": config.long_min_duration_days,
    }
    return f"duration {duration_days:.2f} days below {tier} threshold {thresholds.get(tier, 0.0):g} days"


def _missing_inputs(required: Iterable[str], available: set[str], metadata: DatasetMetadata) -> List[str]:
    missing = []
    for item in required:
        if item in ENV_INPUTS:
            if not metadata.environmental_available:
                missing.append("environmental columns")
        elif item == "PCPS":
            if not metadata.pcps_available:
                missing.append("PCPS")
        elif item == "PCSW":
            if not metadata.pcsw_available:
                missing.append("PCSW")
        elif item not in available:
            missing.append(item)
    return missing


def _changepoint_dependency_available() -> bool:
    try:
        __import__("ruptures")
    except ImportError:
        return False
    return True
