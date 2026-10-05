"""Complete mechanical cycles and their evolution on the offline PPS timescale."""
import numpy as np
import pandas as pd
from .common import ENV, environmental_validity


def cycle_series(frame, cfg):
    """Nonoverlapping phase-0..14 groups; never compress gaps or exclusions."""
    work = frame[["epoch", "sequence_extended", "source_row", "elapsed_cycles",
                  "calibrated_valid", "full_pps_s", "phase15"]].copy()
    work = work.loc[work.phase15.ge(0) & np.isfinite(work.sequence_extended)]
    work["cycle"] = ((work.sequence_extended - cfg.phase_origin) // 15).astype(np.int64)
    work["start_s"] = work.elapsed_cycles / cfg.nominal_hz
    work["duration_s"] = work.full_pps_s.where(work.calibrated_valid)
    grouped = work.groupby(["epoch", "cycle"], sort=True)
    blocks = grouped.agg(
        records=("source_row", "size"), eligible=("duration_s", "count"),
        first_source_row=("source_row", "min"), last_source_row=("source_row", "max"),
        first_sequence=("sequence_extended", "min"), last_sequence=("sequence_extended", "max"),
        phases=("phase15", "nunique"), start_s=("start_s", "min"),
        last_start_s=("start_s", "max"), duration_s=("duration_s", "sum"),
    ).reset_index()
    blocks["available"] = (blocks.records.eq(15) & blocks.eligible.eq(15)
                           & blocks.phases.eq(15)
                           & (blocks.last_sequence - blocks.first_sequence).eq(14)
                           & (blocks.last_source_row - blocks.first_source_row).eq(14))
    blocks["mean_period_s"] = (blocks.duration_s / 15).where(blocks.available)
    # Positions use the common observed hardware timeline; unknown gaps are not UTC.
    blocks["time_s"] = (blocks.start_s + blocks.last_start_s + cfg.swing_period_s) / 2
    blocks["period_error_us"] = (blocks.mean_period_s - cfg.swing_period_s) * 1e6
    blocks["frequency_offset_ppm"] = (cfg.swing_period_s / blocks.mean_period_s - 1) * 1e6
    blocks["frequency_deviation_ppm"] = blocks.frequency_offset_ppm - blocks.groupby("epoch").frequency_offset_ppm.transform("mean")
    breaks = (blocks.epoch.ne(blocks.epoch.shift()) | blocks.cycle.diff().ne(1)
              | ~blocks.available | ~blocks.available.shift(fill_value=False))
    blocks["series_run"] = breaks.cumsum().astype(np.int64)

    # Use only complete groups, giving each phase identical exposure in a time bin.
    duration = max(float(blocks.time_s.max() - blocks.time_s.min()), 0.) if len(blocks) else 0.
    bin_seconds = next((s for s in (3600, 21600, 86400, 604800) if duration / s <= 96), 604800)
    complete = blocks.loc[blocks.available, ["epoch", "cycle", "time_s"]].copy()
    complete["time_bin"] = (complete.time_s // bin_seconds).astype(np.int64)
    samples = work[["epoch", "cycle", "phase15", "duration_s"]].merge(
        complete, on=["epoch", "cycle"], how="inner", validate="many_to_one")
    evolution = samples.groupby(["epoch", "time_bin", "phase15"]).duration_s.agg(["count", "mean"]).reset_index()
    evolution["bin_seconds"] = bin_seconds
    evolution["bin_start_s"] = evolution.time_bin * bin_seconds
    evolution["bin_end_s"] = (evolution.time_bin + 1) * bin_seconds
    if len(evolution):
        centre = evolution.groupby(["epoch", "time_bin"])["mean"].transform("mean")
        evolution["phase_deviation_us"] = (evolution["mean"] - centre) * 1e6
    else:
        evolution["phase_deviation_us"] = pd.Series(dtype=float)
    metadata = dict(
        complete_cycles=int(blocks.available.sum()), incomplete_cycles=int((~blocks.available).sum()),
        evolution_bin_seconds=bin_seconds,
        cycle_definition="Nonoverlapping sequence phases 0..14; all 15 consecutive swings must be PPS-calibrated and eligible. No gap filling.",
        evolution_definition="Mean full period by phase within complete cycles; subtract equal-phase mean of each time bin. Physical direction and impulse position are not inferred.",
    )
    return blocks, evolution, metadata


def swing_frequency_summary(blocks, cfg):
    """Period scatter of complete cycles; linear drift retained and reported separately."""
    rows = []
    for epoch, group in blocks.groupby("epoch"):
        valid = group.loc[group.available]
        # Centre in observed time, independently of the optional local component
        # view. Do not search for a quiet window or compress calibration gaps.
        extent = valid if len(valid) else group
        first = float(extent.start_s.min())
        last = float(extent.last_start_s.max() + cfg.swing_period_s)
        duration = min(4 * 3600, max(last - first, 0.))
        start = first + (last - first - duration) / 2
        end = start + duration
        selected = valid.loc[(valid.time_s >= start) & (valid.time_s < end)]
        for scope, data in (("Whole epoch", valid), ("Central detail window", selected)):
            n = len(data)
            row = dict(epoch=int(epoch), scope=scope, cycles=n,
                       window_start_s=start if scope == "Central detail window" else float(group.start_s.min()),
                       window_end_s=end if scope == "Central detail window" else float(group.last_start_s.max()+cfg.swing_period_s),
                       mean_frequency_offset_ppm=float(data.frequency_offset_ppm.mean()) if n else np.nan,
                       period_peak_to_peak_us=np.nan, period_rms_us=np.nan,
                       linear_drift_us_per_hour=np.nan, detrended_period_rms_us=np.nan,
                       status="available" if n >= 2 else "insufficient complete groups")
            if n >= 2:
                y = data.period_error_us.to_numpy(float)
                dy = y - y.mean()
                row.update(period_peak_to_peak_us=float(np.ptp(y)),
                           period_rms_us=float(np.sqrt(np.mean(dy**2))))
                x = data.time_s.to_numpy(float) / 3600
                dx = x - x.mean()
                if n >= 3 and np.dot(dx, dx) > 0:
                    slope = float(np.dot(dx, dy) / np.dot(dx, dx))
                    row.update(linear_drift_us_per_hour=slope,
                               detrended_period_rms_us=float(np.sqrt(np.mean((dy-slope*dx)**2))))
            rows.append(row)
    return pd.DataFrame(rows)


def cycle_autocorrelation(blocks, max_lag=120, min_pairs=20):
    """Biased, epoch-mean-centered ACF, pairing only within uninterrupted runs.

    The denominator is the full epoch sum of squared deviations at every lag.
    Drift remains present. Missing cycles are never compressed into adjacent data.
    """
    rows = []
    for epoch, group in blocks.loc[blocks.available].groupby("epoch"):
        if group.mean_period_s.nunique() < 2:
            continue
        mean = group.mean_period_s.mean()
        runs = [(g.mean_period_s.to_numpy() - mean, g.time_s.to_numpy())
                for _, g in group.groupby("series_run")]
        denominator = sum(float(np.dot(x, x)) for x, _ in runs)
        if denominator <= 0 or not np.isfinite(denominator):
            continue
        for lag in range(min(max_lag + 1, max(len(x) for x, _ in runs))):
            numerator, elapsed, pairs = 0., 0., 0
            for x, time in runs:
                if len(x) <= lag:
                    continue
                left, right = x[:len(x)-lag], x[lag:]
                numerator += float(np.dot(left, right))
                elapsed += float(np.sum(time[lag:] - time[:len(x)-lag]))
                pairs += len(left)
            if pairs < min_pairs:
                break
            rows.append(dict(epoch=epoch, lag_cycles=lag, lag_seconds=elapsed / pairs,
                             pairs=pairs, autocorrelation=numerator / denominator))
    return pd.DataFrame(rows, columns=["epoch", "lag_cycles", "lag_seconds", "pairs", "autocorrelation"])


def swing_environment(frame, cfg):
    """Context uses PCSW's own timeline and screened channels, without a fit."""
    masks, stale, _ = environmental_validity(frame, frame, cfg)
    work = frame[["epoch", "elapsed_cycles"]].copy()
    work["hour"] = (work.elapsed_cycles / cfg.nominal_hz // 3600).astype(np.int64)
    work["time_s"] = work.elapsed_cycles / cfg.nominal_hz
    for name in ENV:
        if name in masks:
            work[name] = frame[name].where(masks[name])
    grouped = work.groupby(["epoch", "hour"])
    columns = {name: (name, "mean") for name in ENV if name in work}
    result = grouped.agg(time_s=("time_s", "mean"), **columns).reset_index()
    return result, stale
