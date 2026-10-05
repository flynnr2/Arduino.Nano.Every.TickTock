"""Archived capture-investigation CLI, retained for historical regression only."""
import argparse
from dataclasses import fields
import json
from pathlib import Path
import sys

from . import PpsConfig, run_analysis


def main(argv=None):
    parser = argparse.ArgumentParser(description="Analyze PPS captures and write an unattended local report")
    parser.add_argument("input", type=Path, help="Run/segment directory, recording collection, catalogue.json or PCPS.CSV[.gz]")
    parser.add_argument("--out", type=Path, help="Output directory (default: RUN/pps_analysis)")
    parser.add_argument("--config", type=Path, help="Optional JSON settings; unknown keys are errors")
    parser.add_argument("--nominal-hz", type=float, help="Override STS clock frequency (default: STS nhz or 16000000)")
    parser.add_argument("--export-intervals", action="store_true", help="Also write the full derived interval table as gzip CSV")
    args = parser.parse_args(argv)
    try:
        values = json.loads(args.config.read_text()) if args.config else {}
        if not isinstance(values, dict):
            raise ValueError("config must be a JSON object")
        unknown = set(values) - {f.name for f in fields(PpsConfig)}
        if unknown:
            raise ValueError("Unknown settings: " + ", ".join(sorted(unknown)))
        if args.nominal_hz is not None:
            values["nominal_hz"] = args.nominal_hz
        out = args.out or (args.input if args.input.is_dir() else args.input.parent) / "pps_analysis"
        print(f"Analyzing PPS captures in {args.input} ...", flush=True)
        result = run_analysis(args.input, out, export_intervals=args.export_intervals,
                              config_overrides=values,
                              progress=lambda message: print(message, flush=True))
        if isinstance(result, dict):
            records = sum(report['records']['pcps'] for report in result['reports'])
            print(f"Analyzed {records:,} records in {len(result['reports'])} reports. Report: {(out / 'report.html').resolve()}")
        else:
            for warning in result.warnings:
                print("note: " + warning, file=sys.stderr)
            print(f"Analyzed {result.summary['records']:,} records. Report: {(out / 'report.html').resolve()}")
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"PPS analysis failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
