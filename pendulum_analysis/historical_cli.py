"""Archived canonical-v2 regression harness; not an installed user command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional

from .allan import compute_allan
from .artifacts import ArtifactRegistry
from .canonical_v2 import build_bundle, build_report_provenance, write_audit_artifacts, write_bundle, write_report
from .config import load_config_layers, write_resolved_config
from .derive import add_derived_fields, add_pps_calibration
from .filters import add_filter_flags
from .load import load_csv
from .manifest import build_manifest, discover_run_files, select_primary_csv
from .pcps import analyze_pcps
from .phasefold import add_phase_index
from .planner import list_analysis_names
from .plots import create_canonical_v2_plots
from .pps import PpsConfig, run_analysis as run_pps_analysis
from .profile import load_profile
from .report import write_warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze canonical pendulum CSV or run directory output")
    parser.add_argument("input_csv", type=Path, nargs="?", help="Input pendulum CSV or run directory")
    parser.add_argument("--out", type=Path, help="Output directory")
    parser.add_argument("--target-period", type=float, default=2.0, help="Target full period in seconds")
    parser.add_argument("--phase-mod", type=int, default=0, help="Legacy generic phase index modulus; profile phase settings drive canonical v2 phase folds")
    parser.add_argument("--clock-profile", default="generic", help="Clock profile id; use synchronome for Synchronome-specific interpretation")
    parser.add_argument("--clock-profile-file", type=Path, help="Explicit YAML clock profile file; takes precedence over profile directories")
    parser.add_argument("--clock-profile-dir", type=Path, help="Directory containing clock profile YAML files")
    parser.add_argument("--config", type=Path, help="Project analysis configuration YAML")
    parser.add_argument("--analysis-profile", choices=["auto", "core", "standard", "medium", "long", "full"], default="auto", help="Analysis tier selection")
    parser.add_argument("--enable", action="append", default=[], metavar="ANALYSIS", help="Force an analysis by name; may be repeated")
    parser.add_argument("--disable", action="append", default=[], metavar="ANALYSIS", help="Disable an analysis by name; may be repeated")
    parser.add_argument("--min-duration-diurnal-days", type=float, default=14.0, help="Minimum duration for diurnal harmonic analysis")
    parser.add_argument("--min-duration-weekly-days", type=float, default=21.0, help="Minimum duration for weekly structure analysis")
    parser.add_argument("--rolling-window-hours", type=float, default=24.0, help="Rolling stability window in hours")
    parser.add_argument("--rolling-allan-window-hours", type=float, default=72.0, help="Rolling Allan window in hours")
    parser.add_argument("--env-lag-max-hours", type=float, default=12.0, help="Maximum environmental lag grid in hours for long-run screening")
    parser.add_argument("--force-long-run", action="store_true", help="Plan long-run analyses regardless of duration")
    parser.add_argument("--list-analyses", action="store_true", help="List available planner analysis names and exit")
    parser.add_argument("--nominal-hz", type=float, default=16_000_000.0, help="Nominal clock frequency")
    parser.add_argument("--outlier-threshold", type=float, default=6.0, help="Robust outlier threshold")
    parser.add_argument("--pps-window-seconds", type=float, default=61., help="Centered local quadratic PPS phase fit window")
    parser.add_argument("--pps-residual-window", type=int, default=31, help="Trailing valid PPS intervals for offline residual median")
    parser.add_argument("--max-points-per-plot", type=int, default=20_000, help="Deterministic plot and CSV sampling limit")
    parser.add_argument("--min-allan-diffs", type=int, default=20, help="Minimum Allan differences required for each tau")
    parser.add_argument("--env-lag-hours", type=int, default=6, help="Maximum environmental lag grid in hours")
    parser.add_argument("--cap16-bins", type=int, default=256, help="Bins for PCPS cap16 phase coverage diagnostic")
    parser.add_argument("--cap16-min-rows-per-bin", type=int, default=10, help="Minimum rows per cap16 bin before assessing uniformity")
    parser.add_argument("--latency-min-rows", type=int, default=10, help="Minimum latency rows before assessing outliers")
    parser.add_argument("--fft-expected-period", type=float, action="append", default=[], help="Expected FFT period in seconds; may be repeated")
    parser.add_argument("--fft-expected-period-label", action="append", default=[], help="Label for a configured expected FFT period; may be repeated")
    parser.add_argument("--fft-harmonic-base-period", type=float, action="append", default=[], help="Base period for an expected harmonic family; may be repeated")
    parser.add_argument("--fft-harmonic-max-order", type=int, default=12, help="Maximum harmonic order for configured FFT harmonic families")
    parser.add_argument("--fft-match-tolerance-pct", type=float, default=1.0, help="FFT expected-match tolerance as percent of expected frequency")
    parser.add_argument("--fft-match-tolerance-bin", type=float, default=1.0, help="FFT expected-match tolerance in FFT bins")
    parser.add_argument("--fft-peak-cluster-tolerance-pct", type=float, default=1.0, help="Cluster detected FFT peaks within this frequency percentage")
    parser.add_argument("--disable-profile-fft-expectations", action="store_true", help="Disable expected FFT structures supplied by the active clock profile")
    parser.add_argument("--fft-top-n", type=int, default=12, help="Number of clustered FFT peaks to report per spectrum")
    parser.add_argument("--fft-x-axis", choices=["period", "frequency"], default="period", help="Residual FFT plot x-axis")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.list_analyses:
        for name in list_analysis_names():
            print(name)
        return 0
    if args.input_csv is None or args.out is None:
        raise ValueError("input_csv and --out are required unless --list-analyses is used")
    cli_overrides = getattr(args, "_cli_overrides", None) or _all_arg_overrides(args)
    profile_name = str(cli_overrides.get("clock_profile", args.clock_profile))
    profile_file = cli_overrides.get("clock_profile_file", args.clock_profile_file)
    profile_dir = cli_overrides.get("clock_profile_dir", args.clock_profile_dir)
    profile = load_profile(
        profile_name if profile_name != "auto" else "generic",
        profile_file=Path(profile_file) if profile_file else None,
        profile_dir=Path(profile_dir) if profile_dir else None,
    )
    config = load_config_layers(profile_config=profile.raw or {}, project_config_path=args.config, cli_overrides=cli_overrides)
    args.out.mkdir(parents=True, exist_ok=True)
    registry = ArtifactRegistry(args.out)

    warnings: List[str] = []
    discovered = discover_run_files(args.input_csv)
    primary_csv = select_primary_csv(args.input_csv, discovered)
    manifest = build_manifest(args.input_csv, args.out, config.to_dict(), discovered, warnings)
    pcps_path = discovered.get("pcps")
    if pcps_path is not None:
        run_pps_analysis(pcps_path, args.out / "pcps", PpsConfig(nominal_hz=config.nominal_hz))
    loaded = load_csv(primary_csv)
    warnings.extend(loaded.warnings)

    pcps = analyze_pcps(discovered.get("pcps"), config)
    data = add_derived_fields(loaded.data, config)
    data = add_pps_calibration(data, pcps.intervals, config)
    data, filter_warnings = add_filter_flags(data, config)
    warnings.extend(filter_warnings)
    data = add_phase_index(data, config)

    allan = compute_allan(data, config)
    warnings.extend(pcps.warnings)

    manifest["warnings"] = warnings
    config_artifact = registry.register_metadata(
        slug="config_resolved",
        title="Analysis configuration",
        analysis_name="input_provenance",
        description="Resolved analysis configuration used for this run.",
        extension=".yaml",
    )
    write_resolved_config(config, registry.path(config_artifact))
    provenance_artifact = registry.register_metadata(
        slug="provenance",
        title="Input and run provenance",
        analysis_name="input_provenance",
        description="Input file manifest and command provenance.",
    )
    write_warnings(warnings, args.out / "metadata" / "warnings.txt")

    bundle = build_bundle(data, pcps, allan, discovered, config)
    exact_command = " ".join(sys.argv)
    provenance = {
        "schema": "pendulum_analysis.provenance.v1",
        "input_manifest": manifest,
        "report_provenance": build_report_provenance(bundle, exact_command, str(primary_csv)).to_dict(orient="records"),
    }
    registry.path(provenance_artifact).write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")
    write_bundle(bundle, registry)
    create_canonical_v2_plots(bundle, registry)
    write_report(bundle, registry, exact_command, str(primary_csv), pps_report_available=pcps_path is not None)
    write_audit_artifacts(bundle, registry)
    hashes_artifact = registry.register_metadata(
        slug="hashes",
        title="Artifact and input hashes",
        analysis_name="input_provenance",
        description="SHA256 hashes for materialized artifacts and source inputs.",
    )
    registry.path(hashes_artifact).write_text(
        json.dumps(_build_hashes(registry, bundle), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    registry.write_manifest()

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"Wrote canonical v2 analysis outputs to {args.out}")
    return 0


def _build_hashes(registry: ArtifactRegistry, bundle: Any) -> dict:
    from .io import sha256_file

    artifact_hashes = {}
    for artifact in registry.artifacts:
        path = registry.path(artifact)
        if path.exists():
            artifact_hashes[artifact.relative_path] = sha256_file(path)
    return {
        "schema": "pendulum_analysis.hashes.v1",
        "input_sha256": bundle.quality.get("input_sha256", {}),
        "artifacts": artifact_hashes,
    }


ARG_TO_CONFIG = {
    "target_period": "target_period_s",
    "phase_mod": "phase_mod",
    "clock_profile": "clock_profile",
    "clock_profile_file": "clock_profile_file",
    "clock_profile_dir": "clock_profile_dir",
    "analysis_profile": "analysis_profile",
    "enable": "enabled_analyses",
    "disable": "disabled_analyses",
    "force_long_run": "force_long_run",
    "min_duration_diurnal_days": "diurnal_min_duration_days",
    "min_duration_weekly_days": "weekly_min_duration_days",
    "rolling_window_hours": "rolling_window_hours",
    "rolling_allan_window_hours": "rolling_allan_window_hours",
    "env_lag_max_hours": "env_lag_max_hours",
    "nominal_hz": "nominal_hz",
    "outlier_threshold": "outlier_threshold",
    "pps_residual_window": "pps_residual_window",
    "pps_window_seconds": "pps_window_seconds",
    "max_points_per_plot": "max_points_per_plot",
    "min_allan_diffs": "min_allan_diffs",
    "env_lag_hours": "env_lag_hours",
    "cap16_bins": "cap16_bins",
    "cap16_min_rows_per_bin": "cap16_min_rows_per_bin",
    "latency_min_rows": "latency_min_rows",
    "fft_expected_period": "fft_expected_periods_s",
    "fft_expected_period_label": "fft_expected_period_labels",
    "fft_harmonic_base_period": "fft_harmonic_base_periods_s",
    "fft_harmonic_max_order": "fft_harmonic_max_order",
    "fft_match_tolerance_pct": "fft_match_tolerance_pct",
    "fft_match_tolerance_bin": "fft_match_tolerance_bin",
    "fft_peak_cluster_tolerance_pct": "fft_peak_cluster_tolerance_pct",
    "disable_profile_fft_expectations": "disable_profile_fft_expectations",
    "fft_top_n": "fft_top_n",
    "fft_x_axis": "fft_x_axis",
}

FLAG_TO_ATTR = {
    "--target-period": "target_period",
    "--phase-mod": "phase_mod",
    "--clock-profile": "clock_profile",
    "--clock-profile-file": "clock_profile_file",
    "--clock-profile-dir": "clock_profile_dir",
    "--analysis-profile": "analysis_profile",
    "--enable": "enable",
    "--disable": "disable",
    "--force-long-run": "force_long_run",
    "--min-duration-diurnal-days": "min_duration_diurnal_days",
    "--min-duration-weekly-days": "min_duration_weekly_days",
    "--rolling-window-hours": "rolling_window_hours",
    "--rolling-allan-window-hours": "rolling_allan_window_hours",
    "--env-lag-max-hours": "env_lag_max_hours",
    "--nominal-hz": "nominal_hz",
    "--outlier-threshold": "outlier_threshold",
    "--pps-residual-window": "pps_residual_window",
    "--pps-window-seconds": "pps_window_seconds",
    "--max-points-per-plot": "max_points_per_plot",
    "--min-allan-diffs": "min_allan_diffs",
    "--env-lag-hours": "env_lag_hours",
    "--cap16-bins": "cap16_bins",
    "--cap16-min-rows-per-bin": "cap16_min_rows_per_bin",
    "--latency-min-rows": "latency_min_rows",
    "--fft-expected-period": "fft_expected_period",
    "--fft-expected-period-label": "fft_expected_period_label",
    "--fft-harmonic-base-period": "fft_harmonic_base_period",
    "--fft-harmonic-max-order": "fft_harmonic_max_order",
    "--fft-match-tolerance-pct": "fft_match_tolerance_pct",
    "--fft-match-tolerance-bin": "fft_match_tolerance_bin",
    "--fft-peak-cluster-tolerance-pct": "fft_peak_cluster_tolerance_pct",
    "--disable-profile-fft-expectations": "disable_profile_fft_expectations",
    "--fft-top-n": "fft_top_n",
    "--fft-x-axis": "fft_x_axis",
}


def _normalize_override_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return tuple(value)
    return value


def _all_arg_overrides(args: argparse.Namespace) -> dict:
    return {key: _normalize_override_value(getattr(args, attr, None)) for attr, key in ARG_TO_CONFIG.items()}


def _specified_cli_overrides(argv: List[str], args: argparse.Namespace) -> dict:
    seen_attrs = {attr for flag, attr in FLAG_TO_ATTR.items() if flag in argv}
    return {ARG_TO_CONFIG[attr]: _normalize_override_value(getattr(args, attr)) for attr in seen_attrs}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    argv_list = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv_list)
    args._cli_overrides = _specified_cli_overrides(argv_list, args)
    try:
        return run(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
