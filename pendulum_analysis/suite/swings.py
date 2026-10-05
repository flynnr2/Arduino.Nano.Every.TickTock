"""Mechanical event ownership, PPS calibration and 15/30-bin phase summaries."""
from dataclasses import dataclass
import numpy as np
import pandas as pd

from .common import U32, uint_valid, timeline, stats
from ..pps.timescale import TimescaleConfig, build_timescale

EDGES=tuple(f"edge{i}_tcb0" for i in range(5))
DROPS=("drop_ir","drop_pps","drop_swing")
REQUIRED=("seq",)+EDGES+DROPS
COMPONENTS={"tick_open":(0,1),"tick_blocked":(1,2),"tick_half":(0,2),
            "tock_open":(2,3),"tock_blocked":(3,4),"tock_half":(2,4),"full":(0,4)}


@dataclass
class SwingResult:
    frame: pd.DataFrame
    tables: dict
    summary: dict


def phase_bins(sequence_extended, origin=0):
    p=(np.asarray(sequence_extended,dtype=np.int64)-origin)%15
    return p,2*p,2*p+1


def grouped_stats(values,epoch,bins,valid,bin_count,metric,basis):
    """Mean/median of event totals; dispersion is centred within each phase."""
    work=pd.DataFrame(dict(epoch=np.asarray(epoch),phase=np.asarray(bins),value=np.asarray(values,float)))
    all_counts=work.groupby(["epoch","phase"]).size()
    work=work.loc[np.asarray(valid,bool)&np.isfinite(work.value)]
    g=work.groupby(["epoch","phase"])
    result=g.value.agg(["count","mean","median","std","min","max"])
    if len(work):
        deviation=(work.value-g.value.transform("median")).abs()
        result["robust_sigma"]=deviation.groupby([work.epoch,work.phase]).median()*1.4826
        result["p05"]=g.value.quantile(.05)
        result["p95"]=g.value.quantile(.95)
    else:
        result["robust_sigma"]=np.nan
        result["p05"]=np.nan
        result["p95"]=np.nan
    full=pd.MultiIndex.from_product([np.unique(epoch),range(bin_count)],names=["epoch","phase"])
    result=result.reindex(full)
    result["count"]=result["count"].fillna(0).astype(int)
    result["records"]=all_counts.reindex(full).fillna(0).astype(int)
    result["excluded"]=result.records-result["count"]
    result["status"]=np.where(result["count"]>0,"available",np.where(result.records>0,"excluded","empty"))
    result["metric"]=metric
    result["basis"]=basis
    result["bin_count"]=bin_count
    return result.reset_index()


def phase_tables(f,values,eligible,cfg,basis):
    epoch=f.epoch.to_numpy()
    p=f.phase15.to_numpy()
    tables=[]
    weekly=[]
    summaries=[]
    for name,a in values.items():
        tables.append(grouped_stats(a,epoch,p,eligible,15,name,basis))
        for e in np.unique(epoch):
            sample=np.asarray(a)[eligible&(epoch==e)]
            sample=sample[np.isfinite(sample)]
            p05,p95=np.quantile(sample,[.05,.95]) if len(sample) else (None,None)
            summaries.append(dict(basis=basis,epoch=int(e),metric=name,**stats(sample),p05=p05,p95=p95))
    # Interleave by event identity, never flatten all ticks followed by all tocks.
    for metric,left,right in (("half","tick_half","tock_half"),("open","tick_open","tock_open"),("blocked","tick_blocked","tock_blocked")):
        a=np.column_stack([values[left],values[right]]).ravel()
        half_phase=np.column_stack([2*p,2*p+1]).ravel()
        half_epoch=np.repeat(epoch,2)
        half_valid=np.repeat(eligible,2)
        table=grouped_stats(a,half_epoch,half_phase,half_valid,30,metric,basis)
        table["side"]=np.where(table.phase%2==0,"tick","tock")
        tables.append(table)
        w=pd.DataFrame(dict(epoch=half_epoch,week=np.repeat((f.elapsed_cycles/cfg.nominal_hz/604800).astype(int),2),
                            phase=half_phase,value=a))
        w=w.loc[half_valid]
        z=w.groupby(["epoch","week","phase"]).value.agg(["count","mean","median","std"])
        z=z.reset_index()
        z["metric"]=metric
        z["basis"]=basis
        weekly.append(z)
    return pd.concat(tables,ignore_index=True),pd.concat(weekly,ignore_index=True),pd.DataFrame(summaries)


def analyze_swings(d,clock,cfg):
    valid=np.ones(len(d),bool)
    for col in REQUIRED: valid &= uint_valid(d[col])
    # An abnormal mechanical period is not itself a reset of the shared counter.
    # Keep chronological forward deltas below half a wrap, then classify duration.
    tl=timeline(d.seq,d[EDGES[0]],valid,cfg.nominal_hz*cfg.swing_period_s,max_consecutive_cycles=U32/2)
    f=pd.concat([d,tl],axis=1)
    sequence=np.nan_to_num(f.sequence_extended.to_numpy(),nan=0).astype(np.int64)
    f["phase15"],_,_=phase_bins(sequence,cfg.phase_origin)
    f.loc[~uint_valid(f.seq),"phase15"]=-1
    edges=f[list(EDGES)].to_numpy(float)
    increments=np.diff(edges,axis=1)%U32
    ordered=(increments>0).all(axis=1)&(increments<cfg.swing_period_s*cfg.nominal_hz).all(axis=1)
    full=np.sum(increments,axis=1)
    plausible=(full>cfg.swing_period_s*cfg.nominal_hz*.75)&(full<cfg.swing_period_s*cfg.nominal_hz*1.25)
    halves=np.column_stack([increments[:,:2].sum(axis=1),increments[:,2:].sum(axis=1)])
    blocked_fraction=np.divide(increments[:,[1,3]],halves,out=np.full_like(halves,np.nan),where=halves>0)
    f["tick_blocked_fraction"]=blocked_fraction[:,0]
    f["tock_blocked_fraction"]=blocked_fraction[:,1]
    # A full period can remain close to 2 s even while the sensor no longer
    # defines normal half-swings. These are profile-specific metrology guards,
    # not a global outlier filter that might delete the repeating impulse.
    optical_suspect=np.zeros(len(f),bool)
    if cfg.profile=="synchronome":
        half_expected=cfg.swing_period_s*cfg.nominal_hz/2
        optical_suspect=valid&((blocked_fraction>cfg.max_blocked_fraction).any(axis=1)|
            (np.abs(halves-half_expected)>half_expected*cfg.half_period_tolerance).any(axis=1))
    f["optical_clearance_suspect"]=optical_suspect
    chain=(f.seq_step.to_numpy()==1)&(~f.boundary.to_numpy())&np.r_[False,edges[1:,0]!=edges[:-1,4]]
    bad_chain=chain|np.r_[chain[1:],False]
    drop_change=np.zeros(len(f),bool)
    for c in DROPS:
        a=f[c].to_numpy(float)
        change=np.r_[a[0],np.diff(a)%U32]
        drop_change |= change>0
    chronology_valid=~f.ambiguous.to_numpy()&(f.seq_step.to_numpy()!=0)
    eligible=valid&ordered&plausible&~bad_chain&~drop_change&~optical_suspect&chronology_valid
    f["raw_valid"]=eligible
    raw={name:((edges[:,b]-edges[:,a])%U32)/cfg.nominal_hz for name,(a,b) in COMPONENTS.items()}
    for name,a in raw.items(): f[name+"_s"]=a
    tables15,weekly,totals=phase_tables(f,raw,eligible,cfg,"raw")
    calibration_reason=None
    calibrated={name:np.full(len(f),np.nan) for name in COMPONENTS}
    cal_ok=np.zeros(len(f),bool)
    scale = clock.timescale or build_timescale(clock.frame, TimescaleConfig(
        nominal_hz=cfg.nominal_hz, window_seconds=cfg.pps_window_seconds))
    calibration = scale.calibrate_ticks(edges, seq=d.seq, period_hint_s=cfg.swing_period_s)
    cal_ok = calibration.calibration_valid.to_numpy(bool) & eligible
    for name, (a,b) in COMPONENTS.items():
        calibrated[name] = (calibration[f"edge{b}_s"]-calibration[f"edge{a}_s"]).to_numpy()
    for name in ("frequency_hz", "counter_segment", "alignment_reason"):
        f["calibration_"+name] = calibration[name].to_numpy()
    if not cal_ok.any():
        calibration_reason = "No swing has unambiguous alignment and complete valid PPS fit coverage."
    f["calibrated_valid"]=cal_ok
    for name,a in calibrated.items(): f[name+"_pps_s"]=np.where(cal_ok,a,np.nan)
    cp,cw,ct=phase_tables(f,calibrated,cal_ok,cfg,"pps_calibrated")
    phase=pd.concat([tables15,cp],ignore_index=True)
    all_weekly=pd.concat([weekly,cw],ignore_index=True)
    all_totals=pd.concat([totals,ct],ignore_index=True)
    reasons={"invalid_record":~valid,"invalid_edge_order":~ordered,"implausible_full_period":~plausible,
        "ambiguous_or_duplicate_event":~chronology_valid,
        "edge_chain_mismatch":bad_chain,"reported_drop_change":drop_change,"sequence_gap":f.seq_step.to_numpy()>1,
        "suspected_optical_clearance_loss":optical_suspect,
        "timeline_boundary":f.boundary.to_numpy()&(np.arange(len(f))>0)}
    ix=np.flatnonzero(np.logical_or.reduce(list(reasons.values())))
    events=f.iloc[ix][["source_row","seq","epoch","seq_step","elapsed_cycles","phase15","raw_valid","calibrated_valid",
        "tick_half_s","tock_half_s","tick_blocked_fraction","tock_blocked_fraction"]].copy()
    events["reasons"]=[";".join(k for k,v in reasons.items() if v[i]) for i in ix]
    # Explicit per-bin totals are shared across components; no statistic is a sum of medians.
    aggregate=[]
    for seconds,label in ((60,"minute"),(3600,"hour"),(86400,"day")):
        w=f[["epoch","elapsed_cycles","full_s","full_pps_s","raw_valid","calibrated_valid"]].copy()
        w["bin"]=(w.elapsed_cycles/cfg.nominal_hz//seconds).astype(int)
        w["day"]=w.elapsed_cycles/cfg.nominal_hz/86400
        w["full_s"]=w.full_s.where(w.raw_valid)
        w["full_pps_s"]=w.full_pps_s.where(w.calibrated_valid)
        g=w.groupby(["epoch","bin"])
        a=g.agg(day=("day","mean"),records=("day","size"),raw_count=("full_s","count"),raw_mean_s=("full_s","mean"),
                raw_median_s=("full_s","median"),raw_std_s=("full_s","std"),pps_count=("full_pps_s","count"),
                pps_mean_s=("full_pps_s","mean"),pps_median_s=("full_pps_s","median"),pps_std_s=("full_pps_s","std")).reset_index()
        aggregate.append(("swing_"+label,a))
    tables=dict(aggregate)
    breaks=np.r_[True,(optical_suspect[1:]!=optical_suspect[:-1])|(f.epoch.to_numpy()[1:]!=f.epoch.to_numpy()[:-1])|(f.seq_step.to_numpy()[1:]!=1)]
    run_id=np.cumsum(breaks)
    sensor=f.loc[optical_suspect].copy()
    sensor["run"]=run_id[optical_suspect]
    sensor["day"]=sensor.elapsed_cycles/cfg.nominal_hz/86400
    runs=sensor.groupby("run").agg(epoch=("epoch","first"),start_source_row=("source_row","first"),end_source_row=("source_row","last"),
        start_seq=("seq","first"),end_seq=("seq","last"),records=("seq","size"),start_day=("day","first"),end_day=("day","last"),
        max_tick_blocked_fraction=("tick_blocked_fraction","max"),max_tock_blocked_fraction=("tock_blocked_fraction","max")).reset_index()
    runs["classification"]=np.where(runs.records>=3,"sustained_suspected_clearance_loss","isolated_suspect_optical_geometry")
    tables["swing_sensor_runs"]=runs
    tables.update(swing_phase=phase,swing_phase_weekly=all_weekly,swing_component_summary=all_totals,swing_events=events,
                  swing_invalid_fields=pd.DataFrame(d.attrs.get("invalid_fields",[]),columns=["source_row","column","raw_value","reason"]))
    # Edge ownership examples preserve the original source IDs before and after every gap.
    example_ids=np.unique(np.clip(np.r_[np.arange(min(4,len(f))),ix-1,ix,ix+1],0,len(f)-1))
    examples=f.iloc[example_ids][["source_row","seq","epoch","phase15",*EDGES,"raw_valid","calibrated_valid"]].copy()
    examples["tick_phase30"]=2*examples.phase15
    examples["tock_phase30"]=2*examples.phase15+1
    tables["swing_bin_examples"]=examples
    summary=dict(records=len(f),epochs=int(f.epoch.nunique()),raw_eligible=int(eligible.sum()),pps_calibrated_eligible=int(cal_ok.sum()),
        missing_sequence_records=int(np.maximum(0,f.seq_step.fillna(1)-1).sum()),
        event_counts={k:int(np.sum(v)) for k,v in reasons.items()},phase_origin=cfg.phase_origin,profile=cfg.profile,
        sensor_clearance_rule="Synchronome: reject either blocked fraction > configured limit or half duration outside configured nominal tolerance; possible swing-down/optical clearance loss, not a hardware diagnosis.",
        unassigned_phase_records=int((f.phase15<0).sum()),
        calibration_unavailable_reason=calibration_reason,
        phase_convention="p=(extended swing seq-origin)%15; tick=2p, tock=2p+1. Epochs are never pooled.",
        timescale=scale.metadata,
        alignment_assumption="Shared TCB0 timestamp range overlap; parser epoch numbers are local metadata. Ambiguous counter-run or whole-wrap matches are excluded.")
    from .time_series import cycle_series, cycle_autocorrelation, swing_environment, swing_frequency_summary
    blocks, evolution, metadata = cycle_series(f, cfg)
    tables.update(swing_cycles=blocks, swing_phase_evolution=evolution)
    tables["swing_frequency_summary"] = swing_frequency_summary(blocks, cfg)
    summary["time_series"] = metadata
    environment, stale = swing_environment(f, cfg)
    tables.update(swing_environment=environment, swing_environment_stale_runs=stale)
    from .swing_environment_fits import swing_environment_fits
    tables.update(swing_environment_fits(f, blocks, cfg))
    if cfg.autocorrelation:
        tables["swing_autocorrelation"] = cycle_autocorrelation(blocks)
    return SwingResult(f,tables,summary)
