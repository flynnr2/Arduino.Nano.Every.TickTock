"""Event-identity and metrology checks independent of report rendering."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import pytest

from pendulum_analysis.suite.common import Settings,U32,timeline,environmental_validity,read_numeric
from pendulum_analysis.suite.clock import analyze_clock,gap_aware_allan,environment_models
from pendulum_analysis.suite.swings import analyze_swings,phase_bins,grouped_stats


def clock_frame(n=200,hz=16000000,start=0):
    e=(start+np.arange(n,dtype=np.int64)*(hz+16))%U32
    return pd.DataFrame(dict(source_row=np.arange(n)+2,seq=np.arange(n),edge_tcb0=e,
        cap16=(e+8)%65536,latency16=97,now32=(e+97)%U32,gps_status=2,holdover_age_ms=0,drop_pps=0))


def swings_frame(seq):
    seq=np.asarray(seq)
    start=seq*32000032+16000016
    d=pd.DataFrame(dict(source_row=np.arange(len(seq))+2,seq=seq))
    # Deliberately different open/blocked and tick/tock values expose ownership mistakes.
    components=np.column_stack([15000000+seq%15*10,np.full(len(seq),400000),
                                np.full(len(seq),16100000),500032-seq%15*10])
    edges=np.column_stack([start,start[:,None]+np.cumsum(components,axis=1)])%U32
    for i in range(5):d[f"edge{i}_tcb0"]=edges[:,i]
    for c in ("drop_ir","drop_pps","drop_swing"):d[c]=0
    return d


def test_phase_bins_are_sequence_based_not_row_based():
    seq=np.array([31572,31573,31574,31582,2869894,2869944])
    p,a,b=phase_bins(seq)
    assert np.array_equal(p,seq%15)
    assert np.array_equal(a,2*(seq%15))
    assert np.array_equal(b,a+1)
    assert len(np.unique((p-np.arange(len(seq)))%15))>1


def test_wrap_and_missing_rows_preserve_sequence_phase():
    seq=np.array([U32-2,U32-1,0,2],dtype=np.int64)
    edges=np.array([U32-100,15999900,31999900,63999900])
    t=timeline(seq,edges,np.ones(4,bool),16000000)
    assert t.epoch.nunique()==1
    assert t.sequence_extended.tolist()==[U32-2,U32-1,U32,U32+2]
    assert t.seq_step.iloc[-1]==2
    assert np.array_equal(phase_bins(t.sequence_extended)[0],np.array([U32-2,U32-1,U32,U32+2])%15)


def test_sequence_wrap_uses_64_bit_counts_on_32_bit_platform(monkeypatch):
    original_cumsum = np.cumsum

    def platform_cumsum(values, *args, **kwargs):
        if np.asarray(values).dtype == np.bool_ and 'dtype' not in kwargs:
            kwargs['dtype'] = np.int32
        return original_cumsum(values, *args, **kwargs)

    monkeypatch.setattr(np, 'cumsum', platform_cumsum)
    sequence = [U32-2, U32-1, 0, 1]
    result = timeline(sequence, [U32-100, 15999900, 31999900, 63999900],
                      np.ones(4, bool), 16000000)
    assert result.sequence_extended.tolist() == [U32-2, U32-1, U32, U32+1]


def test_sequence_reset_and_bad_row_split_chronology():
    t=timeline([30,31,1,np.nan,3],[100,16000100,100,200,32000100],[True,True,True,False,True],16000000)
    assert t.epoch.tolist()==[0,0,1,2,3]
    assert np.isnan(t.delta_cycles.iloc[2:]).all()


def test_cumulative_drop_does_not_permanently_exclude_clock():
    d=clock_frame()
    d.loc[50:,"drop_pps"]=1
    c=analyze_clock(d,Settings())
    assert not c.frame.frequency_valid.iloc[50]
    assert c.frame.frequency_valid.iloc[51:].all()
    assert c.summary["event_counts"]["new_pps_drop"]==1


def test_clock_checks_both_lock_endpoints_and_phase_resets():
    d=clock_frame()
    d.loc[50,"gps_status"]=3
    c=analyze_clock(d,Settings())
    assert not c.frame.frequency_valid.iloc[50:52].any()
    assert c.frame.phase_raw_s.iloc[52]==pytest.approx(16/16000000)
    assert c.summary["offset_ppm"]==pytest.approx(1)


def test_diagnostic_projection_is_separate_from_raw():
    d=clock_frame()
    d.loc[50,"edge_tcb0"]+=6
    d.loc[50,"now32"]+=6
    c=analyze_clock(d,Settings())
    assert c.frame.offset_cycles.iloc[50:52].tolist()==[22,10]
    assert c.frame.diagnostic_offset_cycles.iloc[50:52].tolist()==[16,16]


def test_allan_does_not_bridge_invalid_interval():
    # Two long, perfectly constant plateaus separated by an excluded sample.
    y=np.r_[np.zeros(150),np.nan,np.ones(150)]
    a=gap_aware_allan(y,np.isfinite(y),np.zeros(len(y),int),bootstrap_samples=0)
    assert len(a)>0
    assert (a.adev_fractional==0).all()
    # Compressing away the NaN would create an artificial step and nonzero ADEV.


def test_allan_matches_independent_disjoint_scale_formula_at_m2():
    y=np.tile([0.,1.,2.,3.,2.,1.],100)
    a=gap_aware_allan(y,np.ones(len(y),bool),np.zeros(len(y),int),max_tau=2,bootstrap_samples=0)
    v=y[1:]
    expected=np.sqrt(np.mean(((v[2:-1]+v[3:]-v[:-3]-v[1:-2])/2)**2)/2)
    assert a.loc[a.tau_s==2,"adev_fractional"].iloc[0]==pytest.approx(expected)


def test_allan_does_not_bridge_epochs():
    y=np.r_[np.zeros(150),np.ones(150)]
    a=gap_aware_allan(y,np.ones(300,bool),np.r_[np.zeros(150),np.ones(150)],bootstrap_samples=0)
    assert (a.adev_fractional==0).all()


def test_pressure_stale_run_starts_at_first_unchanged_record():
    cfg=Settings(stale_seconds=60)
    d=clock_frame(150)
    d["pressure_hPa"]=np.r_[np.arange(10)+1000,np.repeat(1022.62,140)]
    d["temperature_C"]=np.arange(150)/100+20
    tl=timeline(d.seq,d.edge_tcb0,np.ones(150,bool),cfg.nominal_hz)
    masks,events,_=environmental_validity(d,tl,cfg)
    assert events.iloc[0].start_source_row==12
    assert events.iloc[0].records==140
    assert masks["pressure_hPa"].sum()==10
    assert masks["temperature_C"].all()


def test_mean_median_total_is_not_sum_of_component_medians():
    a=np.array([0,0,10.]);b=np.array([0,10,0.])
    t=grouped_stats(a+b,np.zeros(3,int),np.zeros(3,int),np.ones(3,bool),15,"full","raw")
    assert t.iloc[0]["median"]==10
    assert np.median(a)+np.median(b)==0
    assert t.iloc[1]["count"]==0 and np.isnan(t.iloc[1]["median"])


def test_swing_components_and_half_parity_after_deletion():
    c=analyze_clock(clock_frame(400),Settings())
    seq=np.array([i for i in range(100) if i not in (4,5,35)])
    d=swings_frame(seq)
    s=analyze_swings(d,c,Settings())
    assert s.summary["raw_eligible"]==len(seq)
    t=s.tables["swing_phase"]
    raw=t[(t.basis=="raw")&(t.bin_count==30)&(t.metric=="open")]
    for p in range(15):
        assert raw.loc[raw.phase==2*p,"mean"].iloc[0]==pytest.approx((15000000+p*10)/16000000)
        assert raw.loc[raw.phase==2*p+1,"mean"].iloc[0]==pytest.approx(16100000/16000000)
    assert raw["count"].sum()==2*len(seq)
    assert not np.array_equal(s.frame.phase15.to_numpy(),np.arange(len(seq))%15)
    assert s.summary["event_counts"]["edge_chain_mismatch"]==0


def test_calibration_uses_clock_timestamp_intervals_across_wrap():
    start=U32-20000000
    c=analyze_clock(clock_frame(20,start=start),Settings())
    positions=np.array([[8000008,12000012,24000024,32000032,40000040]],float)
    result=c.timescale.calibrate_ticks((positions+start)%U32)
    ok=result.calibration_valid
    values={name:(result[f"edge{b}_s"]-result[f"edge{a}_s"]) for name,(a,b) in
            {"full":(0,4),"tick_half":(0,2),"tock_half":(2,4),"tick_open":(0,1),"tick_blocked":(1,2)}.items()}
    assert ok.all()
    assert values["full"][0]==pytest.approx(2,abs=1e-12)
    assert values["tick_half"][0]+values["tock_half"][0]==pytest.approx(values["full"][0])
    assert values["tick_open"][0]+values["tick_blocked"][0]==pytest.approx(values["tick_half"][0])


def test_calibration_masks_gap_and_never_falls_back():
    d=clock_frame(20)
    d.loc[5,"gps_status"]=3
    c=analyze_clock(d,Settings())
    positions=np.array([[3.5,3.8,4.5,5.2,5.5]])*16000016
    result=c.timescale.calibrate_ticks(positions%U32)
    ok=result.calibration_valid
    values={"full":result.period_s}
    assert not ok.any()
    assert all(np.isnan(a).all() for a in values.values())


def test_swing_drop_change_not_all_later_rows():
    c=analyze_clock(clock_frame(400),Settings())
    d=swings_frame(np.arange(100))
    d.loc[40:,"drop_swing"]=1
    s=analyze_swings(d,c,Settings())
    assert not s.frame.raw_valid.iloc[40]
    assert s.frame.raw_valid.iloc[41:].all()


def test_abnormal_terminal_swing_does_not_disable_earlier_calibration():
    c=analyze_clock(clock_frame(400),Settings())
    d=swings_frame(np.arange(100))
    for i in range(1,5):
        d.loc[99,f"edge{i}_tcb0"]=(d.loc[99,f"edge{i}_tcb0"]+i*8000000)%U32
    s=analyze_swings(d,c,Settings())
    assert not s.frame.raw_valid.iloc[-1]
    assert s.frame.calibrated_valid.iloc[:-1].all()
    assert s.summary["epochs"]==1


def test_synchronome_rejects_sensor_obstruction_despite_plausible_full_period():
    c=analyze_clock(clock_frame(400),Settings())
    d=swings_frame(np.arange(100))
    # Keep edge0, edge2 and edge4 unchanged: the total still looks like 2 seconds.
    # A much longer blocked component exposes the distorted optical measurement.
    d.loc[40:43,"edge1_tcb0"]=(d.loc[40:43,"edge2_tcb0"]-8000000)%U32
    s=analyze_swings(d,c,Settings())
    assert s.frame.full_s.iloc[40:44].between(1.99,2.01).all()
    assert s.frame.optical_clearance_suspect.iloc[40:44].all()
    assert not s.frame.raw_valid.iloc[40:44].any()
    assert not s.frame.calibrated_valid.iloc[40:44].any()
    assert s.frame.raw_valid.iloc[44:].all()
    assert s.tables["swing_sensor_runs"].iloc[0].records==4
    generic=analyze_swings(d,c,Settings(profile="generic"))
    assert generic.frame.raw_valid.iloc[40:44].all()


def test_sequence_reset_rejects_ambiguous_whole_wrap_alignment():
    c=analyze_clock(clock_frame(400),Settings())
    d=swings_frame(np.arange(100))
    d.loc[50:,"seq"]-=50
    s=analyze_swings(d,c,Settings())
    assert s.summary["epochs"]==2
    assert not s.frame.calibrated_valid.iloc[:50].any()
    assert s.frame.calibration_alignment_reason.iloc[:50].eq("ambiguous_hardware_overlap").all()
    assert s.frame.calibrated_valid.iloc[50:].all()


def test_jitter_measures_within_bin_spread_not_phase_offsets():
    p=np.tile(np.arange(15),10)
    t=grouped_stats(p*.01,np.zeros(len(p),int),p,np.ones(len(p),bool),15,"full","raw")
    assert np.allclose(t["std"],0)
    assert np.allclose(t.robust_sigma,0)
    assert t["mean"].max()>.1


def test_csv_duplicate_header_and_missing_required_rejected(tmp_path):
    p=tmp_path/"PCPS.CSV"
    p.write_text("seq,seq\n1,1\n")
    with pytest.raises(ValueError,match="Duplicate"):read_numeric(p,["seq"])
    p.write_text("seq\n1\n")
    with pytest.raises(ValueError,match="missing"):read_numeric(p,["seq","edge_tcb0"])


def test_invalid_source_field_text_is_preserved(tmp_path):
    p=tmp_path/"PCPS.CSV"
    p.write_text("seq,edge_tcb0\nwrong,123\n")
    d=read_numeric(p,["seq","edge_tcb0"])
    assert np.isnan(d.seq.iloc[0])
    assert d.attrs["invalid_fields"]==[dict(source_row=2,column="seq",raw_value="wrong",reason="invalid_required_field")]


def test_empty_csv_reports_a_clear_input_error(tmp_path):
    p=tmp_path/"PCPS.CSV"
    p.write_text("")
    with pytest.raises(ValueError,match="Empty CSV"):read_numeric(p,["seq"])


def test_duplicate_swing_event_is_not_valid_metrology():
    c=analyze_clock(clock_frame(400),Settings())
    d=swings_frame(np.arange(100))
    for edge in [f"edge{i}_tcb0" for i in range(5)]:d.loc[40,edge]=d.loc[39,edge]
    s=analyze_swings(d,c,Settings())
    assert not s.frame.raw_valid.iloc[40]


def test_environment_validation_is_forward_only():
    n=200
    temperature=np.sin(np.arange(n)/10)+22
    y=4+temperature*.35
    h=pd.DataFrame(dict(epoch=0,bin=np.arange(n),day=np.arange(n)/24,eligible=3600,
        temperature_C=temperature,temperature_C_samples=3600,temperature_C_matched_offset_cycles=y))
    _,v,_,_=environment_models(h,Settings())
    t=v[v.model=="temperature"]
    assert len(t)==2 and (t.rms_ppb<1e-6).all()
    h.loc[100:,"temperature_C_matched_offset_cycles"]+=100
    _,v,_,_=environment_models(h,Settings())
    error=v[(v.model=="temperature")&(v.training_fraction==.5)].rms_ppb.iloc[0]
    assert error==pytest.approx(6250)


def test_report_smoke(tmp_path):
    from pendulum_analysis.suite.__main__ import run
    d=clock_frame(130)
    d.drop(columns="source_row").to_csv(tmp_path/"PCPS.CSV",index=False)
    # PCPS-only mode must be complete and explicit about absent swing data.
    result=run(tmp_path,tmp_path/"result",Settings(diagnostics=True),progress=lambda _:None)
    assert result["complete"] and result["swings"] is None
    report=(tmp_path/"result"/"report.html").read_text()
    assert "Clock and swing analysis" in report
    assert "Environmental model validation" in report
    markdown=(tmp_path/"result"/"report.md").read_text()
    for title in ("PPS data quality", "GPS status counts", "GPS lock and holdover",
                  "PPS timing characteristics", "PPS capture latency", "PPS cap16 phase coverage"):
        assert title in report and title in markdown
    assert "1.000001" in report and "1.000001" in markdown
    assert "unavailable" in report and "unavailable" in markdown
    timing=pd.read_csv(tmp_path/"result"/"csv"/"clock_timing_summary.csv")
    assert timing.iloc[0]["mean"]==pytest.approx(1.000001)
    parsed=json.loads((tmp_path/"result"/"summary.json").read_text())
    assert parsed["clock"]["offset_ppm"]==pytest.approx(1)
    for image in parsed["plots"]:assert (tmp_path/"result"/"plots"/image).stat().st_size>0


def test_swing_component_percentiles_use_eligible_events():
    c=analyze_clock(clock_frame(400),Settings())
    d=swings_frame(np.arange(100))
    d.loc[40:,"drop_swing"]=1
    s=analyze_swings(d,c,Settings())
    for r in s.tables["swing_component_summary"].itertuples():
        mask=s.frame.raw_valid if r.basis=="raw" else s.frame.calibrated_valid
        suffix="_s" if r.basis=="raw" else "_pps_s"
        values=s.frame.loc[mask & s.frame.epoch.eq(r.epoch),r.metric+suffix].to_numpy()
        assert r.n==len(values)
        assert r.p05==pytest.approx(np.quantile(values,.05))
        assert r.p95==pytest.approx(np.quantile(values,.95))
    assert not s.frame.raw_valid.iloc[40]


def test_swing_summary_empty_singleton_and_epochs():
    from pendulum_analysis.suite.swings import phase_tables,COMPONENTS,SwingResult
    from pendulum_analysis.suite.report import swing_summary_tables,_table
    f=pd.DataFrame(dict(epoch=[0,1,1],phase15=[0,1,2],elapsed_cycles=[0,32e6,64e6]))
    values={name:np.array([2.,3.,100.]) for name in COMPONENTS}
    _,_,totals=phase_tables(f,values,np.array([True,False,False]),Settings(),"raw")
    first=totals[totals.epoch.eq(0)]
    empty=totals[totals.epoch.eq(1)]
    assert first.n.eq(1).all() and first["std"].isna().all()
    assert first.p05.eq(2).all() and first.p95.eq(2).all()
    assert empty.n.eq(0).all()
    assert empty[["mean","median","p05","p95","min","max"]].isna().all().all()
    views=list(swing_summary_tables(SwingResult(f,{"swing_component_summary":totals},{})))
    assert len(views)==2 and all(len(view)==7 for _,view in views)
    html,md=_table(views[1][1],digits=10)
    assert "unavailable" in html and "unavailable" in md


def test_swing_summary_report_preserves_existing_sections(tmp_path,monkeypatch):
    from pendulum_analysis.suite import report
    cfg=Settings(diagnostics=True)
    c=analyze_clock(clock_frame(100),cfg)
    s=analyze_swings(swings_frame(np.arange(20)),c,cfg)
    monkeypatch.setattr(report,"plot_clock",lambda *args:[("clock.png","Existing clock chart","Clock caption")])
    monkeypatch.setattr(report,"plot_swings",lambda *args:[("swing.png","Existing swing chart","Swing caption")])
    (tmp_path/"plots").mkdir()
    for name in ("clock.png","swing.png"):
        (tmp_path/"plots"/name).write_bytes(b"chart fixture")
    before=s.tables["swing_component_summary"].copy(deep=True)
    plots=report.write_report(tmp_path,c,s,cfg,{})
    assert plots==["clock.png","swing.png"]
    html=(tmp_path/"report.html").read_text()
    assert html.count("data:image/png;base64,")==2
    assert "src='plots/" not in html
    for name in ("report.html","report.md"):
        text=(tmp_path/name).read_text()
        for label in ("Clock result","Environmental model validation","Mechanical phase",
                      "Methods and limits","Existing clock chart","Existing swing chart","Data exports",
                      "Swing summary tables","Raw swing characteristics","PPS-calibrated swing characteristics",
                      "Full swing","Half A","Half B","Edge 0→1 (open)","Edge 1→2 (blocked)","Edge 2→3 (open)","Edge 3→4 (blocked)",
                      "Std dev (s)","Robust spread (s)","P05 (s)","P95 (s)"):
            assert label in text
    pd.testing.assert_frame_equal(s.tables["swing_component_summary"],before)
    saved=pd.read_csv(tmp_path/"csv"/"swing_component_summary.csv")
    assert len(saved)==14
    assert saved.loc[0,"p05"]==pytest.approx(before.loc[0,"p05"])
    for name in {**c.tables,**s.tables}:
        assert (tmp_path/"csv"/(name+".csv")).exists()


@pytest.mark.parametrize("jitter", [False, True])
def test_sparse_swing_phase_plot_preserves_missing_bins(tmp_path, jitter):
    from pendulum_analysis.suite.report import polar_pair
    table=pd.DataFrame(dict(metric="full",bin_count=15,basis="pps_calibrated",epoch=0,
        phase=np.arange(15),count=[1]+[0]*14,mean=[2.]+[np.nan]*14,
        median=[2.]+[np.nan]*14,std=np.full(15,np.nan),robust_sigma=[0.]+[np.nan]*14))
    summary=pd.DataFrame([dict(metric="full",basis="pps_calibrated",epoch=0,median=2.)])
    output=tmp_path/"sparse.png"
    assert polar_pair(table,summary,"full",15,"pps_calibrated",0,jitter,output)
    assert output.stat().st_size>0
    assert table["std"].isna().all()
    assert table["mean"].isna().sum()==14
