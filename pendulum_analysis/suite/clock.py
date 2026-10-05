"""Clock frequency, phase, gap-aware stability and environmental model evaluation."""
from dataclasses import dataclass
import numpy as np
import pandas as pd

from .common import ENV, U32, uint_valid, timeline, stats, environmental_validity
from ..pps.timescale import TimescaleConfig, build_timescale
from .pps_summary import pps_summary_tables

REQUIRED = ("seq", "edge_tcb0", "cap16", "latency16", "now32", "gps_status", "holdover_age_ms", "drop_pps")


@dataclass
class ClockResult:
    frame: pd.DataFrame
    tables: dict
    summary: dict
    timescale: object = None


def gap_aware_allan(values, eligible, epoch, max_tau=131072, bootstrap_samples=300):
    """Overlapping fractional-frequency ADEV; every 2m-window must be valid.

    Approximate intervals resample day-sized (or 4*tau) blocks of squared
    differences, preserving within-block dependence. They are unavailable with
    fewer than eight blocks, and are not metrological confidence guarantees.
    """
    y = np.asarray(values, float)
    ok = np.asarray(eligible,bool)&np.isfinite(y)
    epoch = np.asarray(epoch)
    ok &= np.r_[False,epoch[1:]==epoch[:-1]]
    sums = np.r_[0.,np.cumsum(np.where(ok,y,0.))]
    bad = np.r_[0,np.cumsum(~ok)]
    rows=[]
    rng=np.random.default_rng(20260910)
    m=1
    while 2*m<=len(y) and m<=max_tau:
        admissible=(bad[2*m:]-bad[:-2*m])==0
        starts=np.flatnonzero(admissible)
        # A conservative support count: actual disjoint eligible 2m windows.
        run_start=np.r_[0,np.flatnonzero(ok[1:]!=ok[:-1])+1]
        run_end=np.r_[run_start[1:],len(ok)]
        disjoint=int(np.sum((run_end-run_start)[ok[run_start]]//(2*m)))
        if disjoint<20:
            break
        differences=(sums[starts+2*m]-2*sums[starts+m]+sums[starts])/m
        sq=differences*differences
        adev=float(np.sqrt(np.mean(sq)/2))
        blocks=starts//max(86400,4*m)
        sums_b=np.bincount(blocks,weights=sq)
        n_b=np.bincount(blocks)
        sums_b,n_b=sums_b[n_b>0],n_b[n_b>0]
        lo=hi=np.nan
        if len(n_b)>=8 and bootstrap_samples:
            ix=rng.integers(0,len(n_b),size=(bootstrap_samples,len(n_b)))
            draws=np.sqrt(sums_b[ix].sum(axis=1)/n_b[ix].sum(axis=1)/2)
            lo,hi=np.quantile(draws,[.025,.975])
        rows.append(dict(tau_s=m,adev_fractional=adev,overlapping_pairs=len(starts),
            disjoint_pairs=disjoint,bootstrap_blocks=len(n_b),approx_low=lo,approx_high=hi))
        m*=2
    return pd.DataFrame(rows,columns=["tau_s","adev_fractional","overlapping_pairs","disjoint_pairs","bootstrap_blocks","approx_low","approx_high"])


def aggregate_clock(f, seconds, cfg, env_masks):
    valid=f.frequency_valid.to_numpy()
    work=pd.DataFrame({"epoch":f.epoch,"bin":(f.elapsed_cycles/cfg.nominal_hz//seconds).astype(int),
        "offset":np.where(valid,f.offset_cycles,np.nan),
        "diagnostic":np.where(valid,f.diagnostic_offset_cycles,np.nan),
        "day":f.elapsed_cycles/cfg.nominal_hz/86400,
        "phase_run":f.phase_run,"phase_raw":f.phase_raw_s,"phase_constant":f.phase_constant_s})
    for c,mask in env_masks.items():
        work[c]=np.where(mask & valid, f[c],np.nan)
    grouped=work.groupby(["epoch","bin"],sort=True)
    h=grouped.agg(day=("day","mean"),records=("day","size"),eligible=("offset","count"),
        offset_mean_cycles=("offset","mean"),offset_median_cycles=("offset","median"),
        offset_std_cycles=("offset","std"),offset_min_cycles=("offset","min"),offset_max_cycles=("offset","max"),
        diagnostic_mean_cycles=("diagnostic","mean"),phase_run=("phase_run","last"),
        phase_run_count=("phase_run","nunique"),phase_raw_s=("phase_raw","last"),phase_constant_s=("phase_constant","last"))
    # A bin spanning phase resets must not draw a connecting line through them.
    h.loc[h.phase_run_count>1,["phase_raw_s","phase_constant_s"]]=np.nan
    h["coverage_fraction"]=h.eligible/seconds
    for c in env_masks:
        h[c]=grouped[c].mean()
        h[c+"_samples"]=grouped[c].count()
        # Matched frequency avoids a different denominator when a sensor fails mid-bin.
        matched=np.where(valid & env_masks[c],f.offset_cycles,np.nan)
        work["matched"]=matched
        h[c+"_matched_offset_cycles"]=work.groupby(["epoch","bin"])["matched"].mean()
    return h.reset_index()


def _design(h, name):
    t=h.temperature_C.to_numpy(float)
    if name=="constant": return np.ones((len(h),1))
    cols=[np.ones(len(h)),t]
    if name=="temperature_quadratic": cols.append(t*t)
    if name=="temperature_humidity": cols.append(h.humidity_pct.to_numpy(float))
    if name=="temperature_history":
        lag=h.temperature_C.shift().to_numpy(float,copy=True)
        uninterrupted=(h.bin.diff()==1)&(h.epoch==h.epoch.shift())
        lag[~uninterrupted]=np.nan
        cols.append(lag)
    return np.column_stack(cols)


def environment_models(hourly, cfg):
    models,validation,weekly=[],[],[]
    h=hourly.copy()
    prediction=h[["epoch","bin","day"]].copy()
    if "temperature_C" not in h:
        return pd.DataFrame(),pd.DataFrame(),pd.DataFrame(),prediction
    ok=(h.eligible>=cfg.min_hour_samples)&(h.temperature_C_samples>=cfg.min_hour_samples)
    y=h.temperature_C_matched_offset_cycles.to_numpy(float)
    names=["constant","temperature","temperature_quadratic","temperature_history"]
    if "humidity_pct" in h: names.append("temperature_humidity")
    rng=np.random.default_rng(20260910)
    for name in names:
        X=_design(h,name)
        valid=ok.to_numpy()&np.isfinite(y)&np.isfinite(X).all(axis=1)
        if name=="temperature_humidity":
            valid &= (h.humidity_pct_samples==h.temperature_C_samples).to_numpy()
            valid &= (h.humidity_pct_samples==h.eligible).to_numpy()
        if valid.sum()<max(48,4*X.shape[1]): continue
        coef=np.linalg.lstsq(X[valid],y[valid],rcond=None)[0]
        residual=y[valid]-X[valid]@coef
        slope_lo=slope_hi=np.nan
        # Resample whole days, not millions of correlated one-second rows.
        if name=="temperature":
            groups=[np.flatnonzero(valid & (h.day.to_numpy().astype(int)==day)) for day in np.unique(h.day[valid].astype(int))]
            if len(groups)>=8:
                draws=[]
                for _ in range(300):
                    ix=np.concatenate([groups[i] for i in rng.integers(0,len(groups),len(groups))])
                    draws.append(np.linalg.lstsq(X[ix],y[ix],rcond=None)[0][1]/cfg.nominal_hz*1e6)
                slope_lo,slope_hi=np.quantile(draws,[.025,.975])
        models.append(dict(model=name,hours=int(valid.sum()),coefficients_cycles=coef.tolist(),
            rms_ppb=float(np.sqrt(np.mean(residual**2))/cfg.nominal_hz*1e9),
            r_squared=float(1-np.var(residual)/np.var(y[valid])) if np.var(y[valid]) else np.nan,
            temperature_slope_ppm_C=coef[1]/cfg.nominal_hz*1e6 if name=="temperature" else np.nan,
            approximate_slope_low=slope_lo,approximate_slope_high=slope_hi))
        prediction[name+"_ppm"]=np.where(valid,X@coef/cfg.nominal_hz*1e6,np.nan)
        prediction["observed_ppm"]=np.where(ok,y/cfg.nominal_hz*1e6,np.nan)
        for fraction in (.5,.75):
            cut=int(len(h)*fraction)
            train=valid & (np.arange(len(h))<cut)
            test=valid & (np.arange(len(h))>=cut)
            if train.sum()<24 or test.sum()<24: continue
            b=np.linalg.lstsq(X[train],y[train],rcond=None)[0]
            error=y[test]-X[test]@b
            baseline=y[test]-y[train].mean()
            validation.append(dict(model=name,training_fraction=fraction,train_hours=int(train.sum()),test_hours=int(test.sum()),
                rms_ppb=np.sqrt(np.mean(error**2))/cfg.nominal_hz*1e9,
                baseline_rms_ppb=np.sqrt(np.mean(baseline**2))/cfg.nominal_hz*1e9,
                improvement_fraction=1-np.sqrt(np.mean(error**2)/np.mean(baseline**2)) if np.mean(baseline**2)>0 else np.nan))
    for (epoch,week),g in h.loc[ok].groupby(["epoch",h.loc[ok,"day"].floordiv(7)]):
        a=g.temperature_C.to_numpy(float)
        b=g.temperature_C_matched_offset_cycles.to_numpy(float)
        if len(g)<24 or np.ptp(a)<.1: continue
        coef=np.linalg.lstsq(np.column_stack([np.ones(len(a)),a]),b,rcond=None)[0]
        weekly.append(dict(epoch=epoch,week=week,hours=len(g),temperature_slope_ppm_C=coef[1]/cfg.nominal_hz*1e6))
    return pd.DataFrame(models),pd.DataFrame(validation),pd.DataFrame(weekly),prediction


def analyze_clock(d,cfg):
    row_valid=np.ones(len(d),bool)
    for c in REQUIRED:
        row_valid &= uint_valid(d[c],16 if c in ("cap16","latency16") else 32)
    row_valid &= d.gps_status.isin([0,1,2,3]).to_numpy()
    tl=timeline(d.seq,d.edge_tcb0,row_valid,cfg.nominal_hz,max_consecutive_cycles=U32/2)
    f=pd.concat([d,tl],axis=1)
    f["row_valid"]=row_valid
    coherent=((f.now32-f.edge_tcb0)%U32==f.latency16).to_numpy()&row_valid
    delta_drop=f.drop_pps.diff().to_numpy()%U32
    gates = [
        ("Candidate intervals after first record", np.arange(len(f)) > 0),
        ("Both records have valid required fields", row_valid & np.r_[False,row_valid[:-1]]),
        ("Consecutive sequence within one epoch", (f.seq_step==1)&(~f.boundary)),
        ("Both endpoints GPS locked", (f.gps_status==2)&(f.gps_status.shift()==2)),
        ("Both timestamp reconstructions consistent", coherent & np.r_[False,coherent[:-1]]),
        ("No new cumulative PPS drop", delta_drop==0),
        ("Duration within 5% of one nominal second", np.abs(f.delta_cycles-cfg.nominal_hz)<=cfg.nominal_hz*.05),
    ]
    eligible = np.ones(len(f), bool)
    filter_rows = []
    for label, gate in gates:
        previous = int(eligible.sum())
        eligible &= np.asarray(gate, bool)
        filter_rows.append(dict(stage=label, remaining_intervals=int(eligible.sum()),
                                removed_at_stage=previous-int(eligible.sum())))
    f["frequency_valid"] = eligible
    f["offset_cycles"]=f.delta_cycles-cfg.nominal_hz
    projection=((f.cap16-f.edge_tcb0)%65536).where(row_valid)
    baseline=projection.groupby(f.epoch).transform(lambda x:x.mode().iloc[0] if x.notna().any() else np.nan)
    shift=(baseline-projection+32768)%65536-32768
    f["projection_shift_cycles"]=shift
    f["diagnostic_offset_cycles"]=(f.offset_cycles-shift.diff()).where((shift.abs()<=64)&(shift.shift().abs()<=64))
    valid=f.frequency_valid.to_numpy()
    phase_run=np.cumsum(~valid).astype(np.int32)
    f["phase_run"]=phase_run
    mean=float(f.loc[valid,"offset_cycles"].mean()) if valid.any() else np.nan
    # Cumulative phase resets after every exclusion; it never bridges an outage.
    f["phase_raw_s"]=pd.Series(np.where(valid,f.offset_cycles/cfg.nominal_hz,0.)).groupby(phase_run).cumsum().where(valid)
    f["phase_constant_s"]=pd.Series(np.where(valid,(f.offset_cycles-mean)/(cfg.nominal_hz+mean),0.)).groupby(phase_run).cumsum().where(valid)
    env,stale,coverage=environmental_validity(f,tl,cfg)
    conditions={"invalid_record":~row_valid,"timeline_boundary":f.boundary.to_numpy()& (np.arange(len(f))>0),
        "sequence_gap":f.seq_step.to_numpy()>1,"inconsistent_reconstruction":row_valid&~coherent,
        "exceptional_interval":(f.seq_step.to_numpy()==1)&(np.abs(f.offset_cycles.to_numpy())>cfg.nominal_hz*.05),
        "status_change":np.r_[False,f.gps_status.to_numpy()[1:]!=f.gps_status.to_numpy()[:-1]],
        "new_pps_drop":delta_drop>0,"historical_projection_shift":row_valid&(shift.to_numpy()!=0),
        "large_latency":row_valid&(f.latency16.to_numpy()>485)}
    ids=np.flatnonzero(np.logical_or.reduce(list(conditions.values())))
    events=f.iloc[ids][["source_row","seq","epoch","elapsed_cycles","seq_step","delta_cycles","gps_status","latency16","projection_shift_cycles"]].copy()
    events["reasons"]=[";".join(k for k,v in conditions.items() if v[i]) for i in ids]
    tables={"clock_events":events,"environment_stale_runs":stale,"environment_coverage":coverage,
            "clock_invalid_fields":pd.DataFrame(d.attrs.get("invalid_fields",[]),columns=["source_row","column","raw_value","reason"])}
    for name,seconds in (("minute",60),("hour",3600),("day",86400)):
        tables["clock_"+name]=aggregate_clock(f,seconds,cfg,env)
    tables["clock_allan_raw"]=gap_aware_allan(f.offset_cycles.to_numpy()/cfg.nominal_hz,valid,f.epoch)
    tables["clock_allan_diagnostic"]=gap_aware_allan(f.diagnostic_offset_cycles.to_numpy()/cfg.nominal_hz,valid,f.epoch)
    models,validation,weekly,pred=environment_models(tables["clock_hour"],cfg)
    tables.update(environment_models=models,environment_validation=validation,environment_weekly=weekly,environment_predictions=pred)
    daily=tables["clock_day"].copy()
    daily["change_ppb"]=daily.groupby("epoch").offset_mean_cycles.diff()/cfg.nominal_hz*1e9
    tables["clock_daily_changes"]=daily
    summary=dict(records=len(f),epochs=int(f.epoch.nunique()),observed_days=float(f.elapsed_cycles.iloc[-1]/cfg.nominal_hz/86400),
        eligible_intervals=int(valid.sum()),offset_cycles=stats(f.loc[valid,"offset_cycles"]),
        offset_ppm=mean/cfg.nominal_hz*1e6,uncorrected_ms_per_day=mean/cfg.nominal_hz*86400*1000,
        event_counts={k:int(np.sum(v)) for k,v in conditions.items()},
        missing_sequence_records=int(np.maximum(0,f.seq_step.fillna(1)-1).sum()),
        environment=coverage.to_dict("records"),models=models.to_dict("records"),
        diagnostic_normalization="Subtract changes in within-epoch modal timer projection; raw is always primary.")
    tables.update(pps_summary_tables(f, cfg, summary))
    tables["clock_interval_filters"] = pd.DataFrame(filter_rows)
    timescale = build_timescale(d, TimescaleConfig(nominal_hz=cfg.nominal_hz, window_seconds=cfg.pps_window_seconds))
    summary["timescale"] = timescale.metadata
    tables["clock_timescale"] = timescale.observations
    return ClockResult(f,tables,summary,timescale)
