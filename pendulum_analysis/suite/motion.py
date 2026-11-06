"""Optical passage motion on the offline PPS timescale, without gap filling."""
import numpy as np
import pandas as pd
from .common import mechanical_cycles, half_phases

SIDES = ("tick", "tock")
METRICS = ("velocity_mm_s", "amplitude_deg", "amplitude_squared_rad2")


def motion_analysis(frame, blocks, cfg, bin_seconds):
    """Transform individual passages before aggregation; retain cycle exposure."""
    observations = frame[["epoch", "source_row", "sequence_extended", "phase15",
                          "calibrated_valid"]].copy()
    observations["start_s"] = frame.elapsed_cycles / cfg.nominal_hz
    observations["period_s"] = frame.full_pps_s
    observations["cycle"] = mechanical_cycles(frame, cfg)
    for side in SIDES:
        observations[side + "_phase30"] = half_phases(frame, side)
        for suffix in ("_source_row", "_source_sequence"):
            if side + suffix in frame:
                observations[side + suffix] = frame[side + suffix]
    valid = frame.calibrated_valid & np.isfinite(frame.full_pps_s) & frame.full_pps_s.gt(0)
    for side in SIDES:
        valid &= np.isfinite(frame[side + "_blocked_pps_s"]) & frame[side + "_blocked_pps_s"].gt(0)
    observations["available"] = valid
    breaks = (frame.epoch.ne(frame.epoch.shift()) | frame.sequence_extended.diff().ne(1)
              | frame.source_row.diff().ne(1) | ~valid | ~valid.shift(fill_value=False))
    observations["series_run"] = breaks.cumsum().astype(np.int64)
    summary = []
    for side in SIDES:
        blocked = frame[side + "_blocked_pps_s"].where(valid)
        speed = cfg.optical_path_mm / blocked
        amplitude_rad = speed * frame.full_pps_s / (2 * np.pi * cfg.pivot_to_beam_mm)
        observations[side + "_blocked_ms"] = blocked * 1000
        observations[side + "_velocity_mm_s"] = speed
        observations[side + "_amplitude_deg"] = np.rad2deg(amplitude_rad)
        observations[side + "_amplitude_squared_rad2"] = amplitude_rad**2
        first = half_phases(frame, side) % 2 == 0
        other = "tock" if side == "tick" else "tick"
        # Original tick-first inputs need not carry the unused tock-half column.
        preceding = frame.get(other + "_half_pps_s", pd.Series(0., index=frame.index))
        offset = frame[side + "_open_pps_s"] + blocked / 2 + preceding.where(~first, 0)
        observations[side + "_time_s"] = observations.start_s + offset
        for epoch, group in observations.groupby("epoch"):
            for metric in METRICS[:2]:
                values = group.loc[group.available, side + "_" + metric]
                summary.append(dict(epoch=int(epoch), side=side, metric=metric,
                                    records=len(group), count=len(values), excluded=len(group)-len(values),
                                    mean=values.mean(), median=values.median(), std=values.std(),
                                    p05=values.quantile(.05), p95=values.quantile(.95),
                                    minimum=values.min(), maximum=values.max()))
    summary = pd.DataFrame(summary)
    for side in SIDES:
        for metric in METRICS[:2]:
            column = side + "_" + metric
            baseline = observations.groupby("epoch")[column].transform("mean")
            observations[column + "_change_pct"] = (observations[column] / baseline - 1) * 100

    columns = [side + "_" + metric for side in SIDES for metric in METRICS]
    counts = observations.groupby(["epoch", "cycle"]).available.sum().rename("motion_count")
    means = observations.groupby(["epoch", "cycle"])[columns].mean()
    cycles = blocks.merge(counts, on=["epoch", "cycle"], how="left", validate="one_to_one")
    cycles = cycles.merge(means, on=["epoch", "cycle"], how="left", validate="one_to_one")
    cycles["available"] &= cycles.motion_count.eq(15)
    cycles.loc[~cycles.available, columns] = np.nan
    # Average A², never square the cycle's mean A.
    cycles["amplitude_squared_rad2"] = cycles[[s + "_amplitude_squared_rad2" for s in SIDES]].mean(axis=1)
    for side in SIDES:
        for metric in METRICS[:2]:
            reference = summary.loc[summary.side.eq(side) & summary.metric.eq(metric)].set_index("epoch")["mean"]
            cycles[side + "_" + metric + "_change_pct"] = (
                cycles[side + "_" + metric] / cycles.epoch.map(reference) - 1) * 100
    breaks = (cycles.epoch.ne(cycles.epoch.shift()) | cycles.cycle.diff().ne(1)
              | ~cycles.available | ~cycles.available.shift(fill_value=False))
    cycles["series_run"] = breaks.cumsum().astype(np.int64)

    complete = cycles.loc[cycles.available, ["epoch", "cycle", "time_s"]].copy()
    complete["time_bin"] = (complete.time_s // bin_seconds).astype(np.int64)
    paired = observations.merge(complete, on=["epoch", "cycle"], how="inner", validate="many_to_one")
    profiles, evolution = [], []
    for side in SIDES:
        work = paired[["epoch", "phase15", "time_bin", *[side + "_" + m for m in METRICS[:2]]]].copy()
        work["phase"] = paired[side + "_phase30"]
        for metric in METRICS[:2]:
            values = work.groupby(["epoch", "phase"])[side + "_" + metric]
            profile = values.agg(["count", "mean", "median", "std", "min", "max"]).reset_index()
            profile["p05"] = values.quantile(.05).to_numpy()
            profile["p95"] = values.quantile(.95).to_numpy()
            profile["side"], profile["metric"] = side, metric
            previous = profile.groupby("epoch")["mean"].transform(lambda x: np.roll(x.to_numpy(), 1))
            profile["same_direction_change_pct"] = (profile["mean"] / previous - 1) * 100
            profiles.append(profile)
            ev = work.groupby(["epoch", "time_bin", "phase"])[side + "_" + metric].agg(["count", "mean"]).reset_index()
            centre = ev.groupby(["epoch", "time_bin"])["mean"].transform("mean")
            ev["change_pct"] = (ev["mean"] / centre - 1) * 100
            ev["metric"], ev["side"], ev["bin_seconds"] = metric, side, bin_seconds
            evolution.append(ev)
    tables = dict(swing_motion_observations=observations, swing_motion_cycles=cycles,
                  swing_motion_summary=summary, swing_motion_phase=pd.concat(profiles, ignore_index=True),
                  swing_motion_evolution=pd.concat(evolution, ignore_index=True))
    from .motion_fits import motion_fits
    tables.update(motion_fits(frame, cycles, cfg))
    metadata = dict(model="optical_passage_harmonic_semi_amplitude_v1",
                    effective_path_mm=cfg.optical_path_mm, pivot_to_beam_mm=cfg.pivot_to_beam_mm,
                    eligible_swings=int(valid.sum()), complete_cycles=int(cycles.available.sum()),
                    evolution_bin_seconds=bin_seconds,
                    speed_definition="Effective blocked path / PPS-calibrated blocked duration; passage speed magnitude, not signed velocity.",
                    amplitude_definition="Estimated centre-to-peak angle: v * full_period / (2*pi*pivot_to_beam), converted to degrees. Near-BDC harmonic approximation; provisional optical geometry; impulse overlap can violate the free-swing model.",
                    relative_reference="Separate epoch/side arithmetic mean of eligible individual passage estimates; no per-phase centring.",
                    phase_population="Complete 15-swing cycles only; 30 half phases. Physical left/right and mechanical impulse phase unassigned.",
                    evolution_definition="Percentage deviation from the equal-phase mean separately for each side and time bin; absolute drift retained in timeline.")
    return tables, metadata
