"""Exploratory event selection and frozen pre-event comparisons."""
import numpy as np
import pandas as pd
from .data import METRICS, ENV
from .phase import template, apply_template


def select_events(cycles, config, event_hours=()):
    candidates = []
    criteria_by_cycle = {}
    thresholds = {"difference_us": config.difference_threshold_us,
                  "period_us": config.period_threshold_us, "flag_mean_us": config.flag_threshold_us}
    for (epoch, run), g in cycles.loc[cycles.available].groupby(["epoch", "series_run"]):
        g = g.sort_values("cycle").copy()
        if len(g) < 22:
            continue
        scores = pd.DataFrame(index=g.index)
        jumps = pd.DataFrame(index=g.index)
        changes = pd.DataFrame(index=g.index)
        contrasts = pd.DataFrame(index=g.index)
        for name, threshold in thresholds.items():
            before = g[name].shift(1).rolling(10, min_periods=10).median()
            after = g[name].shift(-1).iloc[::-1].rolling(10, min_periods=10).median().iloc[::-1]
            changes[name] = g[name].diff()
            contrasts[name] = after-before
            jumps[name] = changes[name].abs()/threshold
            scores[name] = np.maximum(contrasts[name].abs()/threshold, jumps[name])
        score = scores.max(axis=1).where(scores.notna().all(axis=1))
        for index, row in g.iterrows():
            criteria_by_cycle[(int(epoch), int(row.cycle))] = {
                **{name+"_selection_jump": float(changes.loc[index, name]) for name in thresholds},
                **{name+"_selection_contrast": float(contrasts.loc[index, name]) for name in thresholds},
            }
        peaks = score.eq(score.rolling(21, center=True, min_periods=1).max()) & score.ge(1)
        for index in g.index[peaks]:
            row = g.loc[index]
            candidates.append(dict(epoch=int(epoch), cycle=int(row.cycle), time_s=float(row.time_s),
                                   score=float(score.loc[index]), jump_score=float(jumps.loc[index].max()),
                                   trigger=str(scores.loc[index].idxmax()),
                                   **criteria_by_cycle[(int(epoch), int(row.cycle))],
                                   selection="automatic window contrast / cycle jump"))
    selected = []
    # A median-window contrast has a flat maximum around an ideal step. Prefer
    # the actual jump within that plateau rather than its earliest eligible row.
    for candidate in sorted(candidates, key=lambda x: (x["score"], x["jump_score"]), reverse=True):
        if any(x["epoch"] == candidate["epoch"] and abs(x["time_s"]-candidate["time_s"]) < config.event_separation_seconds for x in selected):
            continue
        selected.append(candidate)
        if len(selected) >= config.max_events:
            break
    good = cycles.loc[cycles.available]
    for hour in event_hours:
        target = float(hour)*3600
        if not np.isfinite(target) or target < 0:
            raise ValueError("event hours must be finite and nonnegative")
        row = good.loc[(good.time_s-target).abs().idxmin()]
        if abs(row.time_s-target) > 60:
            raise ValueError(f"No complete cycle within 60 seconds of requested event hour {hour}")
        if not any(x["epoch"] == row.epoch and x["cycle"] == row.cycle for x in selected):
            selected.append(dict(epoch=int(row.epoch), cycle=int(row.cycle), time_s=float(row.time_s),
                                 **criteria_by_cycle.get((int(row.epoch), int(row.cycle)), {}),
                                 score=None, trigger="user-specified time", selection="requested"))
    for i, event in enumerate(sorted(selected, key=lambda x: (x["epoch"], x["time_s"])), 1):
        event["event_id"] = f"E{i:02d}"
        event["elapsed_hours"] = event["time_s"]/3600
    return sorted(selected, key=lambda x: (x["epoch"], x["time_s"]))


def event_analysis(frame, cycles, events, config):
    summaries, profiles, views = [], [], {}
    for event in events:
        epoch, t, eid = event["epoch"], event["time_s"], event["event_id"]
        start, end = t-config.event_guard_seconds-config.event_baseline_seconds, t-config.event_guard_seconds
        p, n = template(frame, cycles, epoch, start, end)
        post_start, post_end = t+config.event_guard_seconds, t+config.event_guard_seconds+config.event_baseline_seconds
        post, post_n = template(frame, cycles, epoch, post_start, post_end)
        local = frame.loc[frame.epoch.eq(epoch) & frame.calibrated_valid
                          & frame.time_s.between(t-config.event_view_seconds, t+config.event_view_seconds)].copy()
        adjusted = apply_template(local, p)
        adjusted["relative_minutes"] = (adjusted.time_s-t)/60
        views[eid] = adjusted
        for label, profile in (("before", p), ("after", post)):
            if len(profile):
                profiles.append(profile.assign(event_id=eid, epoch=epoch, window=label))
        before_cycles = cycles.loc[cycles.epoch.eq(epoch) & cycles.available & cycles.start_s.ge(start)
                                   & (cycles.last_start_s+2).le(end)]
        after_cycles = cycles.loc[cycles.epoch.eq(epoch) & cycles.available & cycles.start_s.ge(post_start)
                                  & (cycles.last_start_s+2).le(post_end)]
        row = {**event, "baseline_start_s": start, "baseline_end_s": end,
               "after_start_s": post_start, "after_end_s": post_end,
               "before_cycles": n, "after_cycles": post_n,
               "template_status": "available" if len(p) else "insufficient pre-event cycles",
               "comparison_status": "available" if len(p) and len(post) else "insufficient complete cycles"}
        for metric in (*METRICS, *ENV, "density_kg_m3"):
            a, b = before_cycles[metric].mean(), after_cycles[metric].mean()
            row[metric+"_before"] = float(a) if n >= 10 else np.nan
            row[metric+"_after"] = float(b) if post_n >= 10 else np.nan
            row[metric+"_change"] = float(b-a) if n >= 10 and post_n >= 10 else np.nan
        for label, group in (("before", before_cycles), ("after", after_cycles)):
            rows = frame.merge(group[["epoch", "cycle"]], on=["epoch", "cycle"], validate="many_to_one")
            clean = apply_template(rows, p)
            for metric in ("period_us", "difference_us", "flag_mean_us"):
                row[metric+"_adjusted_rms_"+label] = float(clean[metric+"_adjusted"].std(ddof=0)) if len(p) and len(clean) else np.nan
        summaries.append(row)
    return pd.DataFrame(summaries), (pd.concat(profiles, ignore_index=True) if profiles else pd.DataFrame()), views
