"""Full-population PPS summary tables; eligibility comes from the clock analysis."""
import numpy as np
import pandas as pd

from .common import stats


def distribution(metric, unit, values, population, note=""):
    a = np.asarray(values, float)
    a = a[np.isfinite(a)]
    result = stats(a)
    quantiles = np.quantile(a, [.01, .05, .25, .75, .95, .99]) if len(a) else [None]*6
    p01, p05, q25, q75, p95, p99 = quantiles
    result.update(p01=p01, p05=p05, p95=p95, p99=p99,
                  mad=float(np.median(np.abs(a-result["median"]))) if len(a) else None,
                  iqr=float(q75-q25) if len(a) else None)
    return dict(metric=metric, unit=unit, population=population, **result, note=note)


def metric_table(rows):
    return pd.DataFrame(rows, columns=["metric", "value", "unit", "basis"])


def pps_summary_tables(f, cfg, summary):
    valid = f.row_valid.to_numpy(bool)
    eligible = f.frequency_valid.to_numpy(bool)
    rows = int(valid.sum())
    quality = metric_table([
        ("Recorded PPS captures", len(f), "records", "All input records"),
        ("Valid required fields", rows, "records", "All required integers and GPS state valid"),
        ("Invalid required fields", int((~valid).sum()), "records", "Preserved in diagnostic exports"),
        ("Observed duration", summary["observed_days"]*24, "hours", "Known spans joined; unknown time across epochs excluded"),
        ("Timeline epochs", summary["epochs"], "epochs", "Resets and ambiguous chronology split epochs"),
        ("Forward sequence gaps", summary["event_counts"]["sequence_gap"], "events", "Recorded sequence step greater than one"),
        ("Missing forward sequence records", summary["missing_sequence_records"], "records", "Inferred from forward sequence steps"),
        ("New PPS drop counter changes", summary["event_counts"]["new_pps_drop"], "events", "Counter changes, not the sum of cumulative counts"),
        ("Inconsistent reconstruction", summary["event_counts"]["inconsistent_reconstruction"], "records", "Valid fields but now32 minus edge disagrees with latency16"),
        ("Eligible frequency intervals", int(eligible.sum()), "intervals", "All sequential filters passed"),
    ])
    labels = {0: "Free run", 1: "Acquiring", 2: "Locked", 3: "Holdover"}
    gps = pd.DataFrame([dict(gps_status=state, state=label,
        records=int((valid & f.gps_status.eq(state)).sum()),
        percent_valid_records=100*int((valid & f.gps_status.eq(state)).sum())/rows if rows else None)
        for state, label in labels.items()])
    adjacent = valid & np.r_[False, valid[:-1]] & ~f.boundary & f.seq_step.eq(1)
    locked = f.gps_status.eq(2)
    transitions = adjacent & locked.ne(locked.shift())
    health = metric_table([
        ("Locked fraction", 100*int((valid & locked).sum())/rows if rows else None, "%", "Valid required-field records; not elapsed-time weighting"),
        ("Lock/unlock transitions", int(transitions.sum()), "transitions", "Adjacent valid consecutive records within one epoch"),
        ("Positive holdover age", int((valid & f.holdover_age_ms.gt(0)).sum()), "records", "Valid records with holdover_age_ms > 0"),
        ("Maximum holdover age", f.loc[valid, "holdover_age_ms"].max() if rows else None, "ms", "Valid required-field records"),
    ])
    population = "Eligible one-second intervals"
    raw = f.loc[eligible, "offset_cycles"]
    diagnostic = f.loc[eligible, "diagnostic_offset_cycles"]
    timing = pd.DataFrame([
        distribution("PPS interval", "s", f.loc[eligible, "delta_cycles"]/cfg.nominal_hz, population),
        distribution("Raw frequency offset", "cycles/s", raw, population),
        distribution("Raw frequency offset", "ppm", raw/cfg.nominal_hz*1e6, population),
        distribution("Diagnostic normalized offset", "cycles/s", diagnostic, population + "; normalization available",
                     "Subtracts changes in modal timer projection; does not replace raw timestamps"),
        distribution("Diagnostic normalized offset", "ppm", diagnostic/cfg.nominal_hz*1e6,
                     population + "; normalization available"),
    ])
    jitter = f.loc[valid, "pps_jitter_ticks"] if "pps_jitter_ticks" in f else []
    timing.loc[len(timing)] = distribution("Reported PPS jitter", "ticks", jitter,
        "Valid required-field records; finite optional values",
        "Firmware-provided channel" if "pps_jitter_ticks" in f else "Optional pps_jitter_ticks column absent")
    latency = f.loc[valid, "latency16"]
    latency_stats = pd.DataFrame([
        distribution("Capture/ISR latency", "cycles", latency, "Valid required-field records"),
        distribution("Capture/ISR latency", "µs", latency/cfg.nominal_hz*1e6, "Valid required-field records"),
    ])
    large = valid & f.latency16.gt(485)
    # Runs never bridge excluded records, sequence gaps or epochs.
    starts = large & ~(large.shift(fill_value=False) & adjacent)
    run_sizes = pd.Series(np.cumsum(starts)).loc[large].value_counts()
    latency_health = metric_table([
        ("Large-latency threshold (strictly above)", 485, "cycles", "Existing historical event threshold; not an adaptive outlier test"),
        ("Large-latency captures", int(large.sum()), "records", "Valid required-field records"),
        ("Large-latency fraction", 100*int(large.sum())/rows if rows else None, "%", "Valid required-field records"),
        ("Consecutive large-latency runs", int(starts.sum()), "runs", "Consecutive valid records within one epoch"),
        ("Longest large-latency run", int(run_sizes.max()) if len(run_sizes) else 0, "records", "Consecutive valid records within one epoch"),
        ("Changed timer projection", int((valid & f.projection_shift_cycles.ne(0)).sum()), "records", "Difference from modal projection within each epoch"),
    ])
    largest = f.loc[large, ["source_row", "seq", "epoch", "elapsed_cycles", "latency16", "gps_status",
                           "holdover_age_ms", "drop_pps", "frequency_valid", "offset_cycles", "projection_shift_cycles"]].copy()
    largest["elapsed_hours"] = largest.pop("elapsed_cycles")/cfg.nominal_hz/3600
    largest = largest.sort_values("latency16", ascending=False, kind="stable")

    phase = f.loc[valid, "cap16"].to_numpy(dtype=np.int64)
    counts = np.bincount(phase//256, minlength=256)
    unique = np.unique(phase)
    expected = rows/256
    gap = int(np.max(np.diff(np.r_[unique, unique[0]+65536])-1)) if rows else None
    coverage = metric_table([
        ("Captured phases", rows, "records", "Valid required-field records"),
        ("Unique cap16 values", len(unique), "values", "Out of 65,536 possible timer phases"),
        ("Unique cap16 coverage", 100*len(unique)/65536 if rows else None, "%", "Observed distinct phases / 65,536"),
        ("Observed minimum cap16", int(unique[0]) if rows else None, "ticks", "Valid required-field records"),
        ("Observed maximum cap16", int(unique[-1]) if rows else None, "ticks", "Valid required-field records"),
        ("Largest circular empty gap", gap, "ticks", "Unobserved integer phases between captures, including wrap"),
        ("Phase bins", 256, "bins", "256 timer ticks per bin"),
        ("Expected records per bin", expected if rows else None, "records", "Uniform reference: valid records / 256"),
        ("Maximum bin / expected", counts.max()/expected if rows else None, "ratio", "Descriptive coverage diagnostic"),
        ("Minimum bin / expected", counts.min()/expected if rows else None, "ratio", "Descriptive coverage diagnostic"),
        ("Chi-square statistic", float(np.sum((counts-expected)**2)/expected) if rows else None, "statistic", "Uniform-bin reference; no independence or significance claim"),
    ])
    bins = pd.DataFrame(dict(cap16_start=np.arange(256)*256,
                             cap16_end=np.arange(256)*256+255, records=counts))
    return dict(clock_data_quality=quality, clock_gps_states=gps, clock_timebase_health=health,
                clock_timing_summary=timing, clock_latency_summary=latency_stats,
                clock_latency_diagnostics=latency_health, clock_largest_latency_events=largest,
                clock_cap16_coverage=coverage, clock_cap16_bins=bins)
