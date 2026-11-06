"""Run the complete local clock/swing suite without legacy configuration."""
from dataclasses import asdict
from pathlib import Path
import argparse
import json
import platform
import shutil
import tempfile
import time

from .common import Settings,discover,read_numeric,fingerprint,write_json
from .clock import REQUIRED as CLOCK_REQUIRED,analyze_clock
from .swings import REQUIRED as SWING_REQUIRED,analyze_swings


def run(input_path,out,cfg=None,export_intervals=False,progress=print,*,collection_mode='separate'):
    if collection_mode not in {'separate', 'combined'}:
        raise ValueError('collection_mode must be separate or combined')
    from .collection import is_collection, run_collection
    if is_collection(input_path):
        return run_collection(input_path, out, cfg or Settings(), export_intervals, progress, _run_single,
                              collection_mode=collection_mode)
    return _run_single(input_path, out, cfg, export_intervals, progress)


def _run_single(input_path,out,cfg=None,export_intervals=False,progress=print):
    import numpy as np
    import pandas as pd
    inputs=discover(input_path)
    cfg=cfg or Settings()
    out=Path(out).resolve()
    if out in inputs.values() or out==inputs["pcps"].parent:
        raise ValueError("Output must be a separate analysis directory, not the input directory/file")
    started=time.monotonic()
    progress("Reading PCPS and calculating clock validity, frequency, phase and stability …")
    source=read_numeric(inputs["pcps"],CLOCK_REQUIRED)
    clock=analyze_clock(source,cfg)
    del source
    swing=None
    if "pcsw" in inputs:
        progress("Reading PCSW, assigning sequence phases and calibrating complete swings …")
        swing=analyze_swings(read_numeric(inputs["pcsw"],SWING_REQUIRED),clock,cfg)
        for warning in swing.summary.get("impulse_alignment", {}).get("warnings", []):
            progress("WARNING: " + warning)
    status=[]
    if "sts" in inputs:
        status=pd.read_csv(inputs["sts"]).to_dict("records")
    summary=dict(schema="pendulum-clock-swing-suite.v1",settings=asdict(cfg),clock=clock.summary,
        swings=swing.summary if swing else None,status_records=status,complete=False,
        provenance=dict(inputs={k:fingerprint(p) for k,p in inputs.items()},
            implementation={str(p.relative_to(Path(__file__).parent.parent)):fingerprint(p)["sha256"] for p in list(Path(__file__).parent.glob("*.py")) + [Path(__file__).parent.parent/'phase_colors.py'] + list((Path(__file__).parent.parent/"pps").glob("*.py")) + [p for p in (Path(__file__).parent.parent/'swing_components').rglob('*') if p.suffix in ('.py','.js','.html')]},
            python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__),
        input_contract="PCPS required; PCSW optional. STS is metadata. Nominal frequency is an explicit setting (default 16 MHz).")
    progress("Writing time series, phase evolution, tables and the report …")
    from .report import write_report
    out.parent.mkdir(parents=True,exist_ok=True)
    # A plotting/export failure leaves an earlier completed report intact.
    with tempfile.TemporaryDirectory(prefix=".clock-swing-build-",dir=out.parent) as temporary:
        stage=Path(temporary)
        plots=write_report(stage,clock,swing,cfg,summary)
        if export_intervals:
            clock.frame.to_csv(stage/"csv"/"clock_intervals.csv.gz",index=False)
            if swing:swing.frame.to_csv(stage/"csv"/"swing_intervals.csv.gz",index=False)
            if swing and swing.source_frame is not None:
                swing.source_frame.to_csv(stage/"csv"/"swing_source_intervals.csv.gz",index=False)
        summary.update(complete=True,plots=plots,elapsed_seconds=time.monotonic()-started)
        write_json(stage/"summary.json",summary)
        shutil.copytree(stage,out,dirs_exist_ok=True)
    progress(f"Complete: {out/'report.html'}")
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description="Analyze PCPS clock and PCSW swing timing with sequence-correct phase bins")
    parser.add_argument("input",type=Path,help="Run/segment directory, recording collection, catalogue.json or PCPS.CSV[.gz]")
    parser.add_argument("--out",type=Path,help="Default: RUN/analysis_suite")
    parser.add_argument("--nominal-hz",type=float,default=16_000_000)
    parser.add_argument("--pps-window-seconds",type=float,default=61.,help="Centered local quadratic PPS phase fit window")
    parser.add_argument("--profile",choices=("synchronome","generic"),default="synchronome")
    parser.add_argument("--swing-period",type=float,default=2.)
    parser.add_argument("--phase-origin",type=int,default=0,help="Fixed sequence origin for phase zero; never a row index")
    parser.add_argument("--impulse-alignment",action="store_true",help="Synchronome: align a clear, stable adjacent half-period impulse signature to phase 0; warn and retain sequence phases otherwise")
    parser.add_argument("--stale-seconds",type=int,default=3600)
    parser.add_argument("--max-blocked-fraction",type=float,default=.20,help="Synchronome: maximum blocked share of either half")
    parser.add_argument("--half-period-tolerance",type=float,default=.25,help="Synchronome: allowed fractional departure from nominal half period")
    parser.add_argument("--export-intervals",action="store_true",help="Export complete derived frames as gzip CSV")
    parser.add_argument("--collection-mode",choices=("separate","combined"),default="separate",help="Collection presentation: separate detail index or combined gap-aware statistics and plots; calibration always stays independent")
    parser.add_argument("--diagnostics",action="store_true",help="Add detailed PPS tables and optional supporting diagnostics")
    parser.add_argument("--raw-phase-charts",action="store_true",help="Request raw phase charts; excluded by default")
    parser.add_argument("--phase-polars",action="store_true",help="Request polar phase charts; excluded by default")
    parser.add_argument("--swing-pca",action="store_true",help="Include optional dataset-local phase PCA")
    parser.add_argument("--swing-spectra",action="store_true",help="Include optional physical-power swing spectra")
    parser.add_argument("--no-swing-components",action="store_true",help="Omit the four swing analysis components")
    parser.add_argument("--optical-path-mm",type=float,default=3.79,help="Provisional effective blocked path for passage speed (default 3.79 mm)")
    parser.add_argument("--pivot-to-beam-mm",type=float,default=1242.,help="Pivot-to-beam radius for estimated semi-amplitude (default 1242 mm)")
    parser.add_argument("--detail-start-hours",type=float,help="Start of an optional calibrated swing detail window, in observed elapsed hours")
    parser.add_argument("--detail-duration-seconds",type=float,default=90.,help="Detail window duration (default 90 seconds)")
    parser.add_argument("--autocorrelation",action="store_true",help="Add gap-aware autocorrelation of complete 15-swing averages")
    args=parser.parse_args(argv)
    try:
        cfg=Settings(pps_window_seconds=args.pps_window_seconds,profile=args.profile,nominal_hz=args.nominal_hz,swing_period_s=args.swing_period,phase_origin=args.phase_origin,impulse_alignment=args.impulse_alignment,stale_seconds=args.stale_seconds,
                     max_blocked_fraction=args.max_blocked_fraction,half_period_tolerance=args.half_period_tolerance,
                     diagnostics=args.diagnostics,raw_phase_charts=args.raw_phase_charts,phase_polars=args.phase_polars,
                     swing_components=not args.no_swing_components,swing_pca=args.swing_pca,swing_spectra=args.swing_spectra,
                     optical_path_mm=args.optical_path_mm,pivot_to_beam_mm=args.pivot_to_beam_mm,
                     detail_start_hours=args.detail_start_hours,
                     detail_duration_seconds=args.detail_duration_seconds,autocorrelation=args.autocorrelation)
        directory=args.input if args.input.is_dir() else args.input.parent
        run(args.input,args.out or directory/"analysis_suite",cfg,args.export_intervals,progress=lambda message:print(message,flush=True),collection_mode=args.collection_mode)
    except (OSError,ValueError,TypeError) as exc:
        parser.exit(2,f"Analysis failed: {exc}\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
