"""Impulse alignment must preserve provenance and fail open to sequence phases."""
import numpy as np
import pandas as pd
import pytest

from pendulum_analysis.suite.common import Settings, U32
from pendulum_analysis.suite.impulse_alignment import align_impulse, find_anchors
from pendulum_analysis.suite.time_series import cycle_series
from pendulum_analysis.suite.motion import motion_analysis


def observations(anchor=7, n=1800, origin=0, start=0):
    seq = np.arange(start, start+n, dtype=np.int64)
    half_id = np.column_stack([2*seq, 2*seq+1])
    rng = np.random.default_rng(746)
    # Two impulse-associated bins; separate directional baselines and modest noise.
    values = 1 + .002*(half_id%2) + rng.normal(0, 1e-6, half_id.shape)
    values += ((half_id-anchor)%30 < 2)*.001
    f = pd.DataFrame(dict(seq=seq, source_row=np.arange(n)+2, epoch=0,
        sequence_extended=seq, elapsed_cycles=np.arange(n)*32e6, phase15=(seq-origin)%15,
        raw_valid=True, calibrated_valid=True, tick_blocked_fraction=.02, tock_blocked_fraction=.02))
    # A contiguous source edge chain, including timer wraps.
    starts=np.r_[0,np.cumsum(values[:-1].sum(axis=1))*16e6]
    for i in range(5):
        offset=[0,values[:,0]-.02,values[:,0],values.sum(axis=1)-.03,values.sum(axis=1)][i]
        f[f'edge{i}_tcb0']=(starts+np.asarray(offset)*16e6)%U32
    # Generate the shared boundary exactly to avoid floating-point test artefacts.
    f['edge0_tcb0']=np.r_[0,f.edge4_tcb0.to_numpy()[:-1]]
    for i,side in enumerate(('tick','tock')):
        for suffix in ('_s','_pps_s'):
            f[side+'_half'+suffix]=values[:,i]
            f[side+'_blocked'+suffix]=.02+i*.01
            f[side+'_open'+suffix]=values[:,i]-(.02+i*.01)
    for suffix in ('_s','_pps_s'):
        f['full'+suffix]=values.sum(axis=1)
    return f


@pytest.mark.parametrize('anchor',[0,1,7,16,29])
def test_aligns_all_origins_and_preserves_directional_identity(anchor):
    source=observations(anchor, origin=4, start=75)
    before=source.copy(deep=True)
    cfg=Settings(phase_origin=4,impulse_alignment=True)
    f,tables,meta=align_impulse(source,cfg)
    assert meta['status']=='aligned'
    assert tables['swing_impulse_anchors'].anchor_phase30.iloc[0]==anchor
    pd.testing.assert_frame_equal(source,before)
    for side in ('tick','tock'):
        ids=2*f[side+'_source_sequence']+(side=='tock')
        good=f.calibrated_valid
        assert np.array_equal(f.loc[good,side+'_phase30'],((ids-anchor)%30)[good])
        expected=source[side+'_half_pps_s'].shift(-1) if anchor%2 and side=='tick' else source[side+'_half_pps_s']
        np.testing.assert_allclose(f.loc[good,side+'_half_pps_s'],expected[good])
    assert np.array_equal(f.phase15,f.tick_phase30//2)
    assert np.array_equal(f.phase15,f.tock_phase30//2)
    np.testing.assert_allclose(f.full_pps_s,f.tick_half_pps_s+f.tock_half_pps_s,equal_nan=True)
    if anchor%2:
        assert not f.calibrated_valid.iloc[-1]
        assert f.tock_phase30.mod(2).eq(0).all()
        np.testing.assert_allclose(f.elapsed_cycles,source.elapsed_cycles+source.tick_half_s*cfg.nominal_hz)
    blocks,_,_=cycle_series(f,cfg)
    for row in blocks.loc[blocks.available].itertuples():
        group=f.loc[f.analysis_cycle.eq(row.cycle)]
        assert len(group)==15 and set(group.phase15)==set(range(15))
        assert row.mean_period_s==pytest.approx(group.full_pps_s.mean())
    motion,_=motion_analysis(f,blocks,cfg,3600)
    obs=motion['swing_motion_observations']
    if anchor%2:
        good=f.calibrated_valid
        assert (obs.loc[good,'tock_time_s']<obs.loc[good,'tick_time_s']).all()
        # Phase profiles retain source direction even when its parity is reversed.
        assert motion['swing_motion_phase'].query("side == 'tick'").phase.mod(2).eq(1).all()


@pytest.mark.parametrize('kind',['flat','nonadjacent','unstable','short','sparse'])
def test_ambiguous_absent_unstable_or_insufficient_anchor_keeps_original_data(kind):
    f=observations()
    if kind=='flat':
        for side in ('tick','tock'):f[side+'_half_pps_s']=1.
    elif kind=='nonadjacent':
        f['tick_half_pps_s']=1.+f.phase15.isin([2,5])*.001
        f['tock_half_pps_s']=1.
    elif kind=='unstable':
        changed=observations(anchor=15)
        for side in ('tick','tock'):
            f.loc[900:,side+'_half_pps_s']=changed.loc[900:,side+'_half_pps_s']
    elif kind=='short':f=f.iloc[:300].copy()
    elif kind=='sparse':f.loc[350:,'calibrated_valid']=False
    before=f.copy(deep=True)
    result,_,meta=align_impulse(f,Settings(impulse_alignment=True))
    assert meta['status']=='fallback'
    assert 'phases are sequence-based' in meta['warnings'][0]
    pd.testing.assert_frame_equal(result[before.columns],before)
    pd.testing.assert_frame_equal(f,before)


def test_odd_anchor_never_bridges_missing_records_or_invalid_half():
    f=observations(7)
    f.loc[600,'calibrated_valid']=False
    f=f.drop(index=901).reset_index(drop=True)
    aligned,_,meta=align_impulse(f,Settings(impulse_alignment=True))
    assert meta['status']=='aligned'
    for row in [599,600,900,len(f)-1]:assert not aligned.calibrated_valid.iloc[row]
    assert pd.isna(aligned.tick_source_sequence.iloc[900])
    assert aligned.calibrated_valid.iloc[601]


def test_resets_are_evaluated_separately_with_mixed_status():
    a=observations(16)
    b=observations(7)
    b['epoch']=1;b['source_row']+=len(a);b['elapsed_cycles']+=a.elapsed_cycles.iloc[-1]+32e6
    b['tick_half_pps_s']=1.;b['tock_half_pps_s']=1.
    f=pd.concat([a,b],ignore_index=True)
    aligned,anchors,meta=align_impulse(f,Settings(impulse_alignment=True))
    assert meta['status']=='mixed'
    assert anchors['swing_impulse_anchors'].status.tolist()==['aligned','fallback']
    assert aligned.loc[aligned.epoch.eq(1),'phase15'].equals(f.loc[f.epoch.eq(1),'phase15'])


def test_generic_profile_rejects_synchronome_option():
    with pytest.raises(ValueError,match='requires the synchronome profile'):
        Settings(profile='generic',impulse_alignment=True)


def test_invalid_optical_geometry_cannot_supply_an_anchor():
    f=observations()
    f['calibrated_valid']=False
    result,_,meta=align_impulse(f,Settings(impulse_alignment=True))
    assert meta['status']=='fallback'
    pd.testing.assert_frame_equal(result[f.columns],f)


def test_odd_reconstruction_screens_both_environment_records_and_bad_timing():
    f=observations(7)
    for col,base in [('temperature_C',20),('humidity_pct',50),('pressure_hPa',1010)]:
        f[col]=base+np.arange(len(f))*.00001
    f.loc[500,'humidity_pct']=150
    f.loc[600,['tick_half_s','tick_half_pps_s']]=np.nan
    f.loc[600,['raw_valid','calibrated_valid']]=False
    result,_,meta=align_impulse(f,Settings(impulse_alignment=True))
    assert meta['status']=='aligned'
    assert np.isfinite(result.elapsed_cycles).all()
    assert result.humidity_pct.iloc[499:501].isna().all()
    assert result.temperature_C.iloc[499]==pytest.approx(f.temperature_C.iloc[499:501].mean())
    assert not result.calibrated_valid.iloc[599:601].any()


def test_pipeline_fallback_is_numerically_identical_to_disabled_mode():
    from pendulum_analysis.suite.clock import analyze_clock, REQUIRED as CLOCK_REQUIRED
    from pendulum_analysis.suite.swings import analyze_swings, REQUIRED as SWING_REQUIRED
    from pendulum_analysis.suite.common import read_numeric
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'examples'/'synthetic'
    clock=analyze_clock(read_numeric(root/'PCPS.CSV',CLOCK_REQUIRED),Settings())
    source=read_numeric(root/'PCSW.CSV',SWING_REQUIRED)
    original=analyze_swings(source,clock,Settings())
    aligned=analyze_swings(source,clock,Settings(impulse_alignment=True))
    assert aligned.summary['impulse_alignment']['status']=='fallback'
    pd.testing.assert_frame_equal(aligned.frame[original.frame.columns],original.frame)
    for name,table in original.tables.items():
        # Added provenance columns do not affect any original numerical table.
        pd.testing.assert_frame_equal(aligned.tables[name][table.columns],table)


def test_occasional_ambiguous_windows_warn_without_moving_a_stable_anchor():
    f=observations(7,n=3000)
    # One unclear window among ten; every confident window retains the anchor.
    for side in ('tick','tock'):f.loc[1200:1499,side+'_half_pps_s']=1.
    aligned,tables,meta=align_impulse(f,Settings(impulse_alignment=True))
    assert meta['status']=='aligned'
    assert tables['swing_impulse_anchors'].anchor_phase30.iloc[0]==7
    assert tables['swing_impulse_anchors'].ambiguous_windows.iloc[0]==1
    assert '1 sufficiently exposed windows are ambiguous' in meta['warnings'][0]
    assert aligned.impulse_aligned.all()


def test_any_confident_conflict_triggers_fallback_even_if_most_windows_match():
    f=observations(7,n=3000)
    changed=observations(15,n=3000)
    for side in ('tick','tock'):
        f.loc[1200:1499,side+'_half_pps_s']=changed.loc[1200:1499,side+'_half_pps_s']
    aligned,_,meta=align_impulse(f,Settings(impulse_alignment=True))
    assert meta['status']=='fallback'
    assert 'disagree' in meta['warnings'][0]
    pd.testing.assert_frame_equal(aligned[f.columns],f)


def test_many_ambiguous_windows_cannot_qualify_as_stable():
    f=observations(7,n=3000)
    for side in ('tick','tock'):f.loc[1800:2699,side+'_half_pps_s']=1.
    aligned,_,meta=align_impulse(f,Settings(impulse_alignment=True))
    assert meta['status']=='fallback'
    assert 'insufficient clear windows' in meta['warnings'][0]
    pd.testing.assert_frame_equal(aligned[f.columns],f)
