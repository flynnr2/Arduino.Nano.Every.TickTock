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


def run(input_path,out,cfg=None,export_intervals=False,progress=print):
    from .collection import is_collection, run_collection
    if is_collection(input_path):
        return run_collection(input_path, out, cfg or Settings(), export_intervals, progress, _run_single)
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
    status=[]
    if "sts" in inputs:
        status=pd.read_csv(inputs["sts"]).to_dict("records")
    summary=dict(schema="pendulum-clock-swing-suite.v1",settings=asdict(cfg),clock=clock.summary,
        swings=swing.summary if swing else None,status_records=status,complete=False,
        provenance=dict(inputs={k:fingerprint(p) for k,p in inputs.items()},
            implementation={str(p.relative_to(Path(__file__).parent.parent)):fingerprint(p)["sha256"] for p in list(Path(__file__).parent.glob("*.py")) + list((Path(__file__).parent.parent/"pps").glob("*.py"))},
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
    parser.add_argument("--stale-seconds",type=int,default=3600)
    parser.add_argument("--max-blocked-fraction",type=float,default=.20,help="Synchronome: maximum blocked share of either half")
    parser.add_argument("--half-period-tolerance",type=float,default=.25,help="Synchronome: allowed fractional departure from nominal half period")
    parser.add_argument("--export-intervals",action="store_true",help="Export complete derived frames as gzip CSV")
    parser.add_argument("--diagnostics",action="store_true",help="Add raw comparisons, distribution plots and detailed PPS/environment tables")
    parser.add_argument("--detail-start-hours",type=float,help="Start of an optional calibrated swing detail window, in observed elapsed hours")
    parser.add_argument("--detail-duration-seconds",type=float,default=90.,help="Detail window duration (default 90 seconds)")
    parser.add_argument("--autocorrelation",action="store_true",help="Add gap-aware autocorrelation of complete 15-swing averages")
    args=parser.parse_args(argv)
    try:
        cfg=Settings(pps_window_seconds=args.pps_window_seconds,profile=args.profile,nominal_hz=args.nominal_hz,swing_period_s=args.swing_period,phase_origin=args.phase_origin,stale_seconds=args.stale_seconds,
                     max_blocked_fraction=args.max_blocked_fraction,half_period_tolerance=args.half_period_tolerance,
                     diagnostics=args.diagnostics,detail_start_hours=args.detail_start_hours,
                     detail_duration_seconds=args.detail_duration_seconds,autocorrelation=args.autocorrelation)
        directory=args.input if args.input.is_dir() else args.input.parent
        run(args.input,args.out or directory/"analysis_suite",cfg,args.export_intervals,progress=lambda message:print(message,flush=True))
    except (OSError,ValueError,TypeError) as exc:
        parser.exit(2,f"Analysis failed: {exc}\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
