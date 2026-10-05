"""Historical capture diagnostics. Current reports use pendulum_analysis.suite."""
from pathlib import Path
from typing import Any, Optional, Union

from .core import PpsConfig, PpsResult, analyze_files, analyze_frame
from .timescale import TimescaleConfig, PpsTimescale, build_timescale


def run_analysis(input_path: Path, output_dir: Path, config: Optional[PpsConfig] = None,
                 export_intervals: bool = False, *,
                 config_overrides: Optional[dict[str, Any]] = None,
                 progress=print) -> Union[PpsResult, dict[str, Any]]:
    """Report on one input or assemble rotated recordings before PPS analysis.

    Single inputs retain the PpsResult return contract. Collections return the
    shared report index; each group resolves its own recorded nominal frequency.
    """
    from ..suite.collection import is_collection, run_collection
    if is_collection(input_path):
        def run_single(source, out, cfg, export, _progress):
            return _run_single(source, out, cfg, export, config_overrides).summary

        return run_collection(input_path, output_dir, config, export_intervals,
                              progress, run_single, required_roles=('pcps',))
    return _run_single(input_path, output_dir, config, export_intervals, config_overrides)


def _run_single(input_path, output_dir, config, export_intervals, config_overrides):
    from .report import write_report
    result = analyze_files(input_path, config, config_overrides=config_overrides)
    write_report(result, Path(output_dir), export_intervals=export_intervals)
    return result


__all__ = ["PpsConfig", "PpsResult", "analyze_files", "analyze_frame", "run_analysis", "TimescaleConfig", "PpsTimescale", "build_timescale"]
