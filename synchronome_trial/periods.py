"""Four homologous-edge and two flag-midpoint periods on one matched population."""
import numpy as np
import pandas as pd


def alternative_periods(frame):
    f = frame.sort_values("source_row").copy()
    nxt = f.shift(-1)
    joint = (f.calibrated_valid & nxt.calibrated_valid.eq(True)
             & f.epoch.eq(nxt.epoch) & (nxt.sequence_extended-f.sequence_extended).eq(1)
             & f.edge4_tcb0.eq(nxt.edge0_tcb0)
             & f.calibration_counter_segment.eq(nxt.calibration_counter_segment))
    raw_joint = (f.raw_valid & nxt.raw_valid.eq(True) & f.epoch.eq(nxt.epoch)
                 & (nxt.sequence_extended-f.sequence_extended).eq(1) & f.edge4_tcb0.eq(nxt.edge0_tcb0))
    out = f[["source_row", "seq", "sequence_extended", "epoch", "cycle", "phase15", "time_s", "day"]].copy()
    out["next_source_row"] = nxt.source_row
    out["pair_valid"] = joint
    out["raw_pair_valid"] = raw_joint
    for basis, suffix, valid in (("pps", "_pps_s", joint), ("raw", "_s", raw_joint)):
        full = f["full"+suffix]
        offsets = dict(e0=full*0, e1=f["tick_open"+suffix], e2=f["tick_half"+suffix],
                       e3=f["tick_half"+suffix]+f["tock_open"+suffix])
        offsets["tick_mid"] = f["tick_open"+suffix]+f["tick_blocked"+suffix]/2
        offsets["tock_mid"] = f["tick_half"+suffix]+f["tock_open"+suffix]+f["tock_blocked"+suffix]/2
        for name, offset in offsets.items():
            duration = full+offset.shift(-1)-offset
            out[basis+"_"+name+"_us"] = ((duration-2)*1e6).where(valid)
    return out


def period_profiles(periods, cycles):
    columns = [name for name in periods if name.startswith("pps_")]
    selected = periods.merge(cycles.loc[cycles.available, ["epoch", "cycle"]],
                             on=["epoch", "cycle"], validate="many_to_one")
    counts = selected.groupby(["epoch", "cycle"]).pair_valid.sum()
    good = counts.loc[counts.eq(15)].reset_index()[["epoch", "cycle"]]
    selected = selected.merge(good, on=["epoch", "cycle"], validate="many_to_one")
    profiles = selected.groupby(["epoch", "phase15"])[columns].mean().reset_index()
    means = selected.groupby(["epoch", "cycle"]).agg(
        **{name: (name, "mean") for name in columns}, time_s=("time_s", "mean"), rows=("source_row", "size")
    ).reset_index()
    means["day"] = means.time_s/86400
    means["edge_spread_us"] = means[["pps_e0_us", "pps_e1_us", "pps_e2_us", "pps_e3_us"]].max(axis=1)-means[["pps_e0_us", "pps_e1_us", "pps_e2_us", "pps_e3_us"]].min(axis=1)
    return profiles, means
