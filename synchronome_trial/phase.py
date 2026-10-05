"""Frozen phase templates; adjusted observations retain their original time/IDs."""
import numpy as np
import pandas as pd
from .data import METRICS


def template(frame, cycles, epoch, start_s, end_s, min_cycles=10):
    accepted = cycles.loc[cycles.epoch.eq(epoch) & cycles.available
                          & cycles.start_s.ge(start_s)
                          & (cycles.last_start_s+2).le(end_s), ["epoch", "cycle"]]
    rows = frame.merge(accepted, on=["epoch", "cycle"], validate="many_to_one")
    if len(accepted) < min_cycles:
        return pd.DataFrame(columns=["phase15", "metric", "mean_us", "offset_us", "std_us", "count"]), len(accepted)
    tables = []
    for metric in METRICS:
        g = rows.groupby("phase15")[metric].agg(["mean", "std", "count"]).reset_index()
        if len(g) != 15 or not g["count"].eq(len(accepted)).all():
            raise ValueError("Phase template must have equal exposure to all 15 positions")
        g["offset_us"] = g["mean"]-g["mean"].mean()
        g["metric"] = metric
        tables.append(g.rename(columns={"mean": "mean_us", "std": "std_us"}))
    return pd.concat(tables, ignore_index=True), len(accepted)


def apply_template(frame, profile):
    result = frame.copy()
    for metric in METRICS:
        part = profile.loc[profile.metric.eq(metric)]
        offsets = part.set_index("phase15").offset_us if len(part) else pd.Series(dtype=float)
        result[metric+"_adjusted"] = result[metric]-result.phase15.map(offsets)
    return result


def first_templates(frame, cycles, config):
    tables, adjusted, statuses = [], [], []
    for epoch, c in cycles.loc[cycles.available].groupby("epoch"):
        start = float(c.start_s.min())
        p, n = template(frame, cycles, epoch, start, start+config.baseline_seconds)
        f = frame.loc[frame.epoch.eq(epoch) & frame.calibrated_valid]
        adjusted.append(apply_template(f, p))
        if len(p):
            p = p.assign(epoch=epoch, baseline_start_s=start, baseline_end_s=start+config.baseline_seconds)
            tables.append(p)
        statuses.append(dict(epoch=int(epoch), cycles=n, start_s=start, end_s=start+config.baseline_seconds,
                             status="available" if len(p) else "insufficient complete baseline cycles"))
    empty = pd.DataFrame(columns=["phase15", "metric", "mean_us", "offset_us", "std_us", "count", "epoch"])
    return (pd.concat(tables, ignore_index=True) if tables else empty,
            pd.concat(adjusted, ignore_index=True), statuses)


def flag_profile(frame, cycles):
    """Interleaved tick/tock flags; distinguish scatter from uncertainty of mean."""
    accepted = cycles.loc[cycles.available, ["epoch", "cycle"]]
    rows = frame.merge(accepted, on=["epoch", "cycle"], validate="many_to_one")
    parts = []
    for side in ("tick", "tock"):
        w = rows[["epoch", "phase15", side+"_block_us"]].rename(columns={side+"_block_us": "flag_us"})
        reference = rows.groupby(["epoch", "cycle"])[side+"_block_us"].transform("mean")
        w["cycle_normalized_speed_pct"] = 100*(reference/rows[side+"_block_us"]-1)
        w["side"] = side
        w["position30"] = 2*w.phase15+(side == "tock")
        parts.append(w)
    grouped = pd.concat(parts).groupby(["epoch", "position30", "phase15", "side"])
    g = grouped.agg(count=("flag_us", "size"), mean=("flag_us", "mean"), std=("flag_us", "std"),
                    cycle_normalized_speed_mean_pct=("cycle_normalized_speed_pct", "mean"),
                    cycle_normalized_speed_std_pct=("cycle_normalized_speed_pct", "std")).reset_index()
    g["relative_speed_pct"] = np.nan
    for (epoch, side), part in g.groupby(["epoch", "side"]):
        reference = part["mean"].mean()
        g.loc[part.index, "relative_speed_pct"] = 100*(reference/part["mean"]-1)
    return g


def flag_evolution(frame, cycles):
    complete = cycles.loc[cycles.available, ["epoch", "cycle", "time_s"]].copy()
    complete["hour"] = (complete.time_s//3600).astype(int)
    counts = complete.groupby(["epoch", "hour"]).size()
    complete = complete.merge(counts.loc[counts.ge(60)].rename("cycles"), on=["epoch", "hour"], validate="many_to_one")
    rows = frame.merge(complete[["epoch", "cycle", "hour"]], on=["epoch", "cycle"], validate="many_to_one")
    parts = []
    for side in ("tick", "tock"):
        grouped = rows.groupby(["epoch", "hour", "phase15"])[side+"_block_us"]
        part = grouped.agg(["count", "mean", "std"]).reset_index()
        part["side"] = side
        part["position30"] = 2*part.phase15+(side == "tock")
        reference = part.groupby(["epoch", "hour"])["mean"].transform("mean")
        part["within_hour_speed_pct"] = 100*(reference/part["mean"]-1)
        parts.append(part)
    return pd.concat(parts, ignore_index=True)
