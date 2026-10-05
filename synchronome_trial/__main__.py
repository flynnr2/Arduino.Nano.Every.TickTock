"""Run the experimental package directly from the repository checkout."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import tempfile
import numpy as np
import pandas as pd

from pendulum_analysis.suite.common import finite_json, fingerprint
from . import __version__
from .config import TrialConfig
from .data import load, METRICS
from .phase import first_templates, flag_profile, flag_evolution
from .events import select_events, event_analysis
from .periods import alternative_periods, period_profiles
from .modulation import investigate
from .environment import hourly, models


def run(input_path, out, config=None, event_hours=(), progress=print):
    cfg = config or TrialConfig()
    out = Path(out).resolve()
    directory = Path(input_path).resolve()
    directory = directory if directory.is_dir() else directory.parent
    if out == directory or out.suffix.casefold() in (".csv", ".gz"):
        raise ValueError("Choose a separate analysis output directory")
    progress("Reading the selected recording and reusing calibrated edge metrology…")
    f, c, stale, metrology, inputs, hashes = load(input_path, cfg)
    code = list(Path(__file__).parent.glob("*.py"))+list((Path(__file__).parents[1]/"pendulum_analysis").rglob("*.py"))
    code_hashes = {str(p.relative_to(Path(__file__).parents[1])): fingerprint(p)["sha256"] for p in sorted(code)}
    progress("Freezing baseline phase templates and selecting event candidates…")
    templates, adjusted, template_status = first_templates(f, c, cfg)
    flags = flag_profile(f, c)
    flag_history = flag_evolution(f, c)
    events = select_events(c, cfg, event_hours)
    event_summary, event_profiles, views = event_analysis(f, c, events, cfg)
    progress("Comparing homologous edges and longer-cycle patterns…")
    periods = alternative_periods(f)
    period_profile, period_cycles = period_profiles(periods, c)
    acf, pairs, folds, spectra, modulation_metadata = investigate(c)
    progress("Fitting joint environmental models and testing later predictions…")
    # Changing the phase baseline must not make the speed reference use test data.
    h = hourly(c, 3600)
    model_results, predictions, coefficients = models(h)
    event_environment = []
    for row in event_summary.to_dict("records"):
        part = h.loc[h.epoch.eq(row["epoch"])]
        if part.empty:
            continue
        ref = float(part.reference_flag_us.iloc[0])
        for model in model_results:
            if model.get("status") != "available" or model["epoch"] != row["epoch"]:
                continue
            delta, valid = {}, True
            for name, coefficient in model["training_coefficients"].items():
                if name == "speed_proxy_pct":
                    a, b = row["flag_mean_us_before"], row["flag_mean_us_after"]
                    change = 100*ref*(1/b-1/a) if a > 0 and b > 0 else np.nan
                elif name in ("thermal_direction", "temperature_rate_C_per_hour"):
                    # Direction indicators are defined at hourly resolution; do
                    # not manufacture 30-minute event-window labels for this model.
                    valid = False
                    break
                else:
                    change = row.get(name+"_change", np.nan)
                valid &= np.isfinite(change)
                delta[name] = float(coefficient*change)
            if valid:
                measured = row[model["response"]+"_change"]
                expected = sum(delta.values())
                event_environment.append(dict(event_id=row["event_id"], epoch=row["epoch"], response=model["response"],
                    model=model["label"], observed_change_us=measured, conditional_prediction_us=expected,
                    remaining_change_us=measured-expected, heldout_model_rmse_us=model["test_rmse_us"]))
    statistics = []
    for epoch, group in c.loc[c.available].groupby("epoch"):
        for metric in ("period_us", "difference_us", "flag_mean_us"):
            values = group[metric].to_numpy(float)
            statistics.append(dict(epoch=int(epoch), metric=metric, n=len(values), mean_us=float(values.mean()),
                                   rms_us=float(np.std(values)), range_us=float(np.ptp(values))))
    alternation = []
    if not pairs.empty:
        for (epoch, metric), group in pairs.groupby(["epoch", "metric"]):
            alternation.append(dict(epoch=int(epoch), metric=metric, pairs=len(group),
                                    mean_odd_minus_even_us=float(group.odd_minus_even.mean()),
                                    rms_odd_minus_even_us=float(group.odd_minus_even.std(ddof=0))))
    observations = [f"{len(events)} candidates were selected using declared cycle-jump/window-contrast criteria.",
                    "Sequence phase is preserved through gaps; no physical impulse position has been inferred."]
    for response in ("period_us", "difference_us"):
        valid = [m for m in model_results if m.get("status") == "available" and m["response"] == response]
        for epoch in sorted({m["epoch"] for m in valid}):
            group = [m for m in valid if m["epoch"] == epoch]
            best = min(group, key=lambda m: m["test_rmse_us"])
            observations.append(f"Epoch {epoch}, {response}: lowest later RMSE among the declared models is {best['test_rmse_us']:.2f} µs ({best['label']}); training-mean benchmark {best['constant_test_rmse_us']:.2f} µs. This is one chronological split, not a general ranking.")
    common_hours = {}
    for model in model_results:
        common_hours[model["epoch"]] = max(common_hours.get(model["epoch"], 0), model.get("hours", 0))
    summary = dict(schema="synchronome-trial.v1", version=__version__, dataset=directory.name,
        input_directory=str(directory), generated_utc=datetime.now(timezone.utc).isoformat(), complete=False,
        config=asdict(cfg), input_fingerprints=hashes, implementation_hashes=code_hashes,
        software=dict(python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__),
        metrology=metrology, complete_cycles=int(c.available.sum()), template_status=template_status,
        matched_period_pairs=int(periods.pair_valid.sum()), events=events, statistics=statistics,
        modulation_metadata=modulation_metadata, alternation_summary=alternation, environmental_models=model_results,
        environment_population=dict(eligible_hours=int(h.eligible.sum()) if len(h) else 0,
            common_model_hours=sum(common_hours.values())),
        observations=observations,
        units=dict(period_us="full period minus 2 seconds, microseconds", difference_us="tick half minus tock half, microseconds",
                   tick_us="open tick duration, microseconds", tock_us="open tock duration, microseconds",
                   flag_mean_us="arithmetic mean of the two blocked durations, microseconds",
                   speed_proxy_pct="inverse cycle mean flag duration relative to initial reference, percent"),
        unavailable_investigations=["Physical impulse-phase/strength identification without an external anchor or free-motion reference",
            "Absolute amplitude/energy and coast-down Q without calibration and an identified coast-down recording",
            "8 Hz rod mode identification from sparse beam crossings", "UTC/solar-noon matching without a verified event-to-UTC anchor"],
        trial_status={name: "calculated" for name in ("phase", "events", "flags", "periods", "modulation", "environment")})
    progress("Writing figures, supporting tables and the standalone report…")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".synchronome-trial-", dir=out.parent) as temporary:
        stage = Path(temporary)
        (stage/"csv").mkdir()
        tables = dict(complete_cycles=c, phase_templates=templates, flag_profile=flags, flag_phase_evolution=flag_history, events=event_summary,
                      event_phase_profiles=event_profiles, alternative_period_profiles=period_profile,
                      alternative_period_cycles=period_cycles, cycle_correlations=acf, even_odd_pairs=pairs,
                      twenty_cycle_fold=folds, cycle_spectra=spectra, environmental_hours=h,
                      environmental_predictions=predictions, environmental_coefficients=coefficients,
                      event_environment_comparison=pd.DataFrame(event_environment), environmental_stale_runs=stale)
        for name, data in tables.items():
            if not len(data.columns):
                data = pd.DataFrame(columns=["unavailable_status"])
            data.to_csv(stage/"csv"/(name+".csv"), index=False)
        columns = ["source_row", "seq", "sequence_extended", "epoch", "cycle", "phase15", "time_s", "day",
                   "raw_valid", "calibrated_valid", *METRICS, "temperature_C", "humidity_pct", "pressure_hPa", "density_kg_m3", "environment_valid"]
        f[columns].to_csv(stage/"csv"/"swings.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        adjusted[["source_row", "epoch", "phase15", "time_s", *[m+"_adjusted" for m in METRICS]]].to_csv(
            stage/"csv"/"phase_adjusted_swings.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        periods.to_csv(stage/"csv"/"alternative_periods.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        from .plots import generate
        plots = generate(stage/"plots", f, c, templates, adjusted, flags, flag_history, events, event_summary,
                         event_profiles, views, periods, period_profile, period_cycles, acf, pairs, folds, spectra, h, predictions, cfg)
        summary["plots"] = plots
        # Input and reused-code immutability is checked again before publishing.
        if any(fingerprint(inputs[role]) != expected for role, expected in hashes.items()):
            raise ValueError("An input changed during analysis; discard this run and rerun from a closed recording")
        if any(fingerprint(Path(__file__).parents[1]/path)["sha256"] != expected for path, expected in code_hashes.items()):
            raise ValueError("Analysis code changed during this run; rerun for reproducible fingerprints")
        summary.update(complete=True, audit=dict(input_unchanged=True, implementation_unchanged=True,
            cycle_counts_match=bool(int(c.available.sum()) == metrology["time_series"]["complete_cycles"]),
            half_sum_max_error_us=float(np.nanmax(abs(f.tick_half_us+f.tock_half_us-(f.period_us+2e6)))),
            half_difference_max_error_us=float(np.nanmax(abs(f.difference_us-((f.tick_us+f.tick_block_us)-(f.tock_us+f.tock_block_us)))))))
        safe_summary = finite_json(summary)
        (stage/"summary.json").write_text(json.dumps(safe_summary, indent=2, allow_nan=False)+"\n")
        from .report import write
        write(stage, safe_summary, plots, event_summary, model_results)
        shutil.copytree(stage, out, dirs_exist_ok=True)
    progress(f"Complete: {out/'report.html'}")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Separate Synchronome trial: phase, events, flags, alternative periods, modulation and joint environmental relationships")
    parser.add_argument("input", type=Path, help="One closed recording directory or its PCPS file")
    parser.add_argument("--out", type=Path, help="Default RUN/synchronome_trial")
    parser.add_argument("--event-hour", type=float, action="append", default=[], help="Add a candidate at observed elapsed hour; repeatable")
    parser.add_argument("--max-events", type=int, default=6)
    parser.add_argument("--phase-origin", type=int, default=0)
    parser.add_argument("--baseline-minutes", type=float, default=60)
    parser.add_argument("--impulse-phase", type=int, help="Externally identified phase 0..14; requires --impulse-side")
    parser.add_argument("--impulse-side", choices=("tick", "tock"))
    parser.add_argument("--pps-window-seconds", type=float, default=61)
    args = parser.parse_args(argv)
    directory = args.input if args.input.is_dir() else args.input.parent
    try:
        config = TrialConfig(max_events=args.max_events, phase_origin=args.phase_origin,
            baseline_seconds=args.baseline_minutes*60, impulse_phase=args.impulse_phase,
            impulse_side=args.impulse_side, pps_window_seconds=args.pps_window_seconds)
        run(args.input, args.out or directory/"synchronome_trial", config, args.event_hour,
            progress=lambda message: print(message, flush=True))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Trial failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
