"""JSON and Markdown report generation."""

from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import AnalysisConfig
from .schema import DERIVED


def build_summary(
    input_path: Path,
    input_sha256: str,
    args: Dict[str, Any],
    validation: Dict[str, Any],
    quality: Dict[str, Any],
    metrics: Dict[str, Any],
    environmental: Dict[str, Any],
    allan: Any,
    warnings: List[str],
    manifest: Optional[Dict[str, Any]] = None,
    pcps_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    summary = {
        "input_path": str(input_path),
        "input_sha256": input_sha256,
        "tool_version": __version__,
        "python_version": platform.python_version(),
        "command_args": args,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "schema_validation_result": validation,
        "quality_report": quality,
        "headline_metrics": metrics,
        "environmental_diagnostics": environmental,
        "warnings": warnings,
    }
    if manifest is not None:
        summary["manifest"] = manifest
    if pcps_summary is not None:
        summary["pcps_summary"] = pcps_summary
    if allan is not None and not allan.empty:
        summary["allan_summary"] = {
            "min_tau_s": float(allan["tau_s"].min()),
            "max_tau_s": float(allan["tau_s"].max()),
            "min_allan_deviation": float(allan["adev_fractional"].min()),
        }
    else:
        summary["allan_summary"] = None
    return summary


def write_summary_json(summary: Dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def _fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def write_summary_md(summary: Dict[str, Any], path: Path, exact_command: str) -> None:
    robust = summary["headline_metrics"].get("clean_robust", {})
    primary = summary["headline_metrics"].get("clean_primary", {})
    period = robust.get(DERIVED.period_s, {})
    rate = robust.get(DERIVED.rate_ppm, {})
    sec_day = robust.get(DERIVED.sec_per_day, {})
    asym = robust.get(DERIVED.A_half_ms, {})
    phase_text = "See phasefold.csv for per-phase medians and spread."
    env = summary.get("environmental_diagnostics") or {}
    allan = summary.get("allan_summary")
    warnings = summary.get("warnings") or []
    pcps = summary.get("pcps_summary") or {}
    manifest = summary.get("manifest") or {}

    lines = [
        "# Pendulum Analysis Summary",
        "",
        "## Run identity",
        f"- Input: `{summary['input_path']}`",
        f"- SHA256: `{summary['input_sha256']}`",
        f"- Tool version: `{summary['tool_version']}`",
        f"- Generated: `{summary['timestamp']}`",
        f"- Input format: `{summary['schema_validation_result'].get('input_format', 'canonical_swing')}`",
        "",
        "## Detected input files",
    ]
    files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
    if files:
        for label, info in files.items():
            if not info.get("present"):
                lines.append(f"- {label.upper()}: not present")
            elif "error" in info:
                lines.append(f"- {label.upper()}: present but unreadable: {info['error']}")
            else:
                lines.append(f"- {label.upper()}: {info.get('rows', 'n/a')} rows, `{info.get('path', '')}`")
    else:
        lines.append("- Single-file analysis input.")
    lines.extend([
        "",
        "## Row counts and data health",
        f"- Total rows: {_fmt(summary['quality_report'].get('total_rows'))}",
        f"- Clean primary rows: {_fmt(summary['quality_report'].get('clean_primary_row_count'))}",
        f"- Clean robust rows: {_fmt(summary['quality_report'].get('clean_robust_row_count'))}",
        f"- Period outliers: {_fmt(summary['quality_report'].get('period_outlier_count'))}",
        f"- Asymmetry outliers: {_fmt(summary['quality_report'].get('asymmetry_outlier_count'))}",
        "",
        "## Primary timing result",
        f"- Median period: {_fmt(period.get('median'))} s",
        f"- Mean period: {_fmt(period.get('mean'))} s",
        f"- Median rate error: {_fmt(rate.get('median'))} ppm",
        f"- Median daily error: {_fmt(sec_day.get('median'))} sec/day",
        "",
        "## Half-cycle asymmetry",
        f"- Median tick-minus-tock asymmetry: {_fmt(asym.get('median'))} ms",
        f"- Asymmetry MAD: {_fmt(asym.get('MAD'))} ms",
        "",
        "## Phase-fold observations",
        f"- {phase_text}",
        "",
        "## PCPS clock diagnostics",
    ])
    if pcps:
        raw_ppm = pcps.get("raw_pps_error_ppm", {})
        residual_ns = pcps.get("offline_adjusted_residual_ns", {})
        lines.extend([
            f"- Valid PPS intervals: {_fmt(pcps.get('valid_interval_rows'))}",
            f"- Median raw PPS interval error: {_fmt(raw_ppm.get('median'))} ppm",
            f"- Offline adjusted residual robust sigma: {_fmt(residual_ns.get('robust_sigma'))} ns",
            "- Offline residual method: previous valid PPS intervals trailing median.",
        ])
    else:
        lines.append("- No sufficient PCPS/CPS data were available.")
    lines.extend([
        "",
        "## Environmental observations",
    ])
    if env:
        lines.extend([f"- {key}: {_fmt(value)}" for key, value in env.items()])
    else:
        lines.append("- No sufficient environmental diagnostics were available.")
    lines.extend(["", "## Allan summary"])
    if allan:
        lines.extend([
            f"- Tau range: {_fmt(allan['min_tau_s'])} s to {_fmt(allan['max_tau_s'])} s",
            f"- Minimum Allan deviation: {_fmt(allan['min_allan_deviation'])}",
        ])
    else:
        lines.append("- Allan deviation was skipped because too few clean robust rows were available.")
    lines.extend(["", "## Warnings"])
    lines.extend([f"- {warning}" for warning in warnings] if warnings else ["- None."])
    lines.extend([
        "",
        "## Figures",
        "- [PCSW period timeseries](figures/pcsw_period_timeseries.png)",
        "- [PCSW rate timeseries](figures/pcsw_rate_ppm_timeseries.png)",
        "- [PCSW edge intervals](figures/pcsw_edge_intervals.png)",
        "- [PCSW environmental relationships](figures/pcsw_environment_relationships.png)",
        "- [PCSW phase fold](figures/pcsw_phase_fold.png)",
        "- [PCSW Allan deviation](figures/pcsw_allan_deviation.png)",
        "- [PCPS PPS error](figures/pcps_pps_error_cycles.png)",
        "- [PCPS residual histogram](figures/pcps_residual_histogram.png)",
    ])
    lines.extend(["", "## Reproducibility", f"```sh\n{exact_command}\n```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_warnings(warnings: List[str], path: Path) -> None:
    path.write_text("\n".join(warnings) + ("\n" if warnings else ""), encoding="utf-8")
