"""Filesystem helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

LEGACY_PLOT_FILES = {
    "allan_deviation.png",
    "block_time_proxies.png",
    "component_decomposition.png",
    "environment_relationships.png",
    "half_cycle_asymmetry.png",
    "period_over_time.png",
    "rate_ppm_over_time.png",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_output_dirs(outdir: Path) -> Path:
    figures_dir = outdir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    (outdir / "summary_tables").mkdir(parents=True, exist_ok=True)
    return figures_dir


def remove_legacy_plot_outputs(outdir: Path) -> list[str]:
    plots_dir = outdir / "plots"
    removed = []
    if not plots_dir.exists():
        return removed
    for path in plots_dir.glob("*.png"):
        if path.name not in LEGACY_PLOT_FILES and not path.name.startswith("phase_fold_"):
            continue
        path.unlink()
        removed.append(str(path))
    try:
        plots_dir.rmdir()
    except OSError:
        pass
    return removed
