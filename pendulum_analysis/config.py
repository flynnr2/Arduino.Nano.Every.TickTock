"""Runtime configuration for pendulum analysis."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .yamlutil import dump_yaml, load_yaml


@dataclass(frozen=True)
class AnalysisConfig:
    target_period_s: float = 2.0
    phase_mod: int = 0
    clock_profile: str = "generic"
    clock_profile_file: Optional[str] = None
    clock_profile_dir: Optional[str] = None
    project_config: Optional[str] = None
    analysis_profile: str = "auto"
    enabled_analyses: Tuple[str, ...] = ()
    disabled_analyses: Tuple[str, ...] = ()
    force_long_run: bool = False
    standard_min_duration_days: float = 1.0
    medium_min_duration_days: float = 7.0
    long_min_duration_days: float = 21.0
    diurnal_min_duration_days: float = 14.0
    weekly_min_duration_days: float = 21.0
    rolling_allan_min_duration_days: float = 14.0
    env_lag_min_duration_days: float = 14.0
    changepoint_min_duration_days: float = 14.0
    diurnal_harmonic_min_duration_days: float = 14.0
    rolling_window_hours: float = 24.0
    rolling_allan_window_hours: float = 72.0
    env_lag_max_hours: float = 12.0
    nominal_hz: float = 16_000_000.0
    outlier_threshold: float = 6.0
    rolling_window: int = 101
    pps_residual_window: int = 31
    pps_window_seconds: float = 61.0
    max_points_per_plot: int = 20_000
    min_allan_diffs: int = 20
    env_lag_hours: int = 6
    cap16_bins: int = 256
    cap16_min_rows_per_bin: int = 10
    cap16_warn_gap_fraction: float = 0.02
    cap16_fail_gap_fraction: float = 0.05
    cap16_warn_bin_ratio: float = 2.5
    cap16_fail_bin_ratio: float = 5.0
    latency_min_rows: int = 10
    latency_outlier_sigma: float = 8.0
    latency_outlier_median_multiplier: float = 5.0
    fft_expected_periods_s: Tuple[float, ...] = ()
    fft_expected_period_labels: Tuple[str, ...] = ()
    fft_harmonic_base_periods_s: Tuple[float, ...] = ()
    fft_harmonic_max_order: int = 12
    fft_match_tolerance_pct: float = 1.0
    fft_match_tolerance_bin: float = 1.0
    fft_peak_cluster_tolerance_pct: float = 1.0
    disable_profile_fft_expectations: bool = False
    fft_top_n: int = 12
    fft_x_axis: str = "period"

    def validate(self) -> None:
        if self.target_period_s <= 0:
            raise ValueError("--target-period must be positive")
        if self.phase_mod < 0:
            raise ValueError("--phase-mod must be zero or a positive integer")
        if not self.clock_profile:
            raise ValueError("--clock-profile must not be empty")
        if self.analysis_profile not in {"auto", "core", "standard", "medium", "long", "full"}:
            raise ValueError("--analysis-profile must be auto, core, standard, medium, long, or full")
        for name in [
            "standard_min_duration_days",
            "medium_min_duration_days",
            "long_min_duration_days",
            "diurnal_min_duration_days",
            "weekly_min_duration_days",
            "rolling_allan_min_duration_days",
            "env_lag_min_duration_days",
            "changepoint_min_duration_days",
            "diurnal_harmonic_min_duration_days",
            "rolling_window_hours",
            "rolling_allan_window_hours",
            "env_lag_max_hours",
        ]:
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be zero or positive")
        if self.nominal_hz <= 0:
            raise ValueError("--nominal-hz must be positive")
        if self.outlier_threshold <= 0:
            raise ValueError("--outlier-threshold must be positive")
        if not 5 <= self.pps_window_seconds < float("inf"):
            raise ValueError("--pps-window-seconds must be finite and at least 5")
        if self.pps_residual_window <= 1:
            raise ValueError("--pps-residual-window must be greater than 1")
        if self.max_points_per_plot <= 10:
            raise ValueError("--max-points-per-plot must be greater than 10")
        if self.min_allan_diffs <= 0:
            raise ValueError("--min-allan-diffs must be a positive integer")
        if self.env_lag_hours < 0:
            raise ValueError("--env-lag-hours must be zero or positive")
        if self.cap16_bins <= 0:
            raise ValueError("--cap16-bins must be a positive integer")
        if self.cap16_min_rows_per_bin <= 0:
            raise ValueError("--cap16-min-rows-per-bin must be a positive integer")
        if self.cap16_warn_gap_fraction <= 0 or self.cap16_fail_gap_fraction <= 0:
            raise ValueError("cap16 gap thresholds must be positive")
        if self.cap16_warn_bin_ratio <= 0 or self.cap16_fail_bin_ratio <= 0:
            raise ValueError("cap16 bin-ratio thresholds must be positive")
        if self.latency_min_rows <= 0:
            raise ValueError("--latency-min-rows must be a positive integer")
        if self.latency_outlier_sigma <= 0 or self.latency_outlier_median_multiplier <= 0:
            raise ValueError("latency outlier thresholds must be positive")
        if self.fft_harmonic_max_order <= 0:
            raise ValueError("--fft-harmonic-max-order must be a positive integer")
        if self.fft_match_tolerance_pct < 0 or self.fft_match_tolerance_bin < 0:
            raise ValueError("FFT match tolerances must be zero or positive")
        if self.fft_peak_cluster_tolerance_pct < 0:
            raise ValueError("--fft-peak-cluster-tolerance-pct must be zero or positive")
        if self.fft_top_n <= 0:
            raise ValueError("--fft-top-n must be a positive integer")
        if self.fft_x_axis not in {"period", "frequency"}:
            raise ValueError("--fft-x-axis must be period or frequency")
        for period in [*self.fft_expected_periods_s, *self.fft_harmonic_base_periods_s]:
            if period <= 0:
                raise ValueError("FFT expected periods and harmonic base periods must be positive")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def config_hash(self) -> str:
        payload = dump_yaml(self.to_dict()).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


DEFAULTS_PATH = Path(__file__).resolve().parent / "defaults.yaml"


def load_config_layers(
    *,
    profile_config: Optional[Dict[str, Any]] = None,
    project_config_path: Optional[Path] = None,
    cli_overrides: Optional[Dict[str, Any]] = None,
) -> AnalysisConfig:
    """Resolve defaults -> profile config -> project config -> CLI overrides."""

    merged: Dict[str, Any] = {}
    if DEFAULTS_PATH.exists():
        merged.update(load_yaml(DEFAULTS_PATH))
    if profile_config:
        merged.update(profile_config.get("analysis_defaults", {}))
    if project_config_path is not None:
        project = load_yaml(project_config_path)
        merged.update(project.get("analysis", project))
        merged["project_config"] = str(project_config_path)
    if cli_overrides:
        merged.update({key: value for key, value in cli_overrides.items() if value is not None})
    allowed = {field.name for field in fields(AnalysisConfig)}
    unknown = sorted(set(merged) - allowed)
    if unknown:
        raise ValueError(f"unknown analysis config setting(s): {', '.join(unknown)}")
    tuple_fields = {
        "enabled_analyses",
        "disabled_analyses",
        "fft_expected_periods_s",
        "fft_expected_period_labels",
        "fft_harmonic_base_periods_s",
    }
    for name in tuple_fields:
        if name in merged and isinstance(merged[name], list):
            merged[name] = tuple(merged[name])
    config = AnalysisConfig(**merged)
    config.validate()
    return config


def write_resolved_config(config: AnalysisConfig, path: Path) -> None:
    path.write_text(dump_yaml(config.to_dict()), encoding="utf-8")
