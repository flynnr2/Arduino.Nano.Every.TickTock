"""Both analysis front ends consume the same offline PPS model."""
import numpy as np
import pandas as pd
import pytest

from pendulum_analysis.config import AnalysisConfig
from pendulum_analysis.derive import add_derived_fields, add_pps_calibration
from pendulum_analysis.canonical_v2 import prepare_pcsw_frame
from pendulum_analysis.suite.common import Settings
from pendulum_analysis.suite.clock import analyze_clock
from pendulum_analysis.suite.swings import analyze_swings

U32 = 2**32


def recording():
    hz = 16_000_016
    e = np.arange(100, dtype=np.int64)*hz
    pps = pd.DataFrame(dict(source_row=np.arange(100)+2, seq=np.arange(100), edge_tcb0=e%U32,
        cap16=e%65536, latency16=20, now32=(e+20)%U32, gps_status=2, holdover_age_ms=0, drop_pps=0))
    start = (1+np.arange(40)*2)*hz
    swings = pd.DataFrame(dict(source_row=np.arange(40)+2, seq=np.arange(40),drop_ir=0,drop_pps=0,drop_swing=0))
    offsets = [0, 15_000_000, hz, hz+15_000_000, 2*hz]
    for i, offset in enumerate(offsets):
        swings[f'edge{i}_tcb0'] = (start+offset)%U32
    for name,value in [('tick',15_000_000),('tick_block',hz-15_000_000),('tock',15_000_000),('tock_block',hz-15_000_000),
                       ('open_A',15_000_000),('block_A',hz-15_000_000),('half_A',hz),
                       ('open_B',15_000_000),('block_B',hz-15_000_000),('half_B',hz),('full',2*hz)]:
        swings[name]=value
    return pps,swings


def test_canonical_and_suite_share_metrology_and_preserve_raw_ticks():
    pps, swings = recording()
    cfg = AnalysisConfig()
    derived = add_derived_fields(swings,cfg)
    calibrated = add_pps_calibration(derived,pps,cfg)
    frame = prepare_pcsw_frame(calibrated,cfg,pps)
    suite = analyze_swings(swings,analyze_clock(pps,Settings()),Settings())
    assert frame.full_s.iloc[0] == pytest.approx(2.000002)
    assert np.allclose(frame.full_pps_adj_s,2,atol=1e-12)
    assert np.allclose(calibrated.period_s,suite.frame.full_pps_s,atol=1e-12)
    assert np.allclose(calibrated.rate_ppm,0,atol=1e-6)
    pd.testing.assert_frame_equal(calibrated[swings.columns],swings)
    from pendulum_analysis.filters import locked_mask
    assert locked_mask(calibrated).all()
    assert frame.attrs['pps_timescale']['window_seconds']==61


def test_suite_missing_pulse_keeps_counter_epoch_and_resumes_calibration():
    pps, swings = recording()
    # Hardware dropped a pulse: surviving packet numbers can still be consecutive.
    pps=pps.drop(index=20).reset_index(drop=True)
    pps['seq']=np.arange(len(pps))
    clock=analyze_clock(pps,Settings())
    suite=analyze_swings(swings,clock,Settings())
    assert clock.frame.epoch.nunique()==1
    assert not suite.frame.calibrated_valid.iloc[9]
    assert suite.frame.calibrated_valid.iloc[12:].all()


def test_parser_local_epoch_labels_do_not_participate_in_alignment():
    pps,swings=recording()
    clock=analyze_clock(pps,Settings())
    clock.frame['epoch']=17
    suite=analyze_swings(swings,clock,Settings())
    assert suite.frame.calibrated_valid.all()
    assert np.allclose(suite.frame.full_pps_s,2)


def test_missing_pps_has_no_calibrated_fallback():
    _, swings = recording()
    cfg = AnalysisConfig()
    frame = prepare_pcsw_frame(add_derived_fields(swings,cfg),cfg,pd.DataFrame())
    assert frame.full_s.notna().all()
    assert frame.period_s.isna().all()
    assert frame.full_pps_adj_s.isna().all()
    assert frame.pps_adjustment_status.eq("unavailable").all()


def test_calibration_preserves_input_gps_status_and_uses_matched_support():
    pps, swings = recording()
    swings['gps_status'] = 0
    frame=add_pps_calibration(add_derived_fields(swings,AnalysisConfig()),pps,AnalysisConfig())
    from pendulum_analysis.filters import locked_mask
    assert frame.gps_status.eq(0).all()
    assert locked_mask(frame).all()


def test_calibrated_allan_does_not_bridge_rejected_pps_span():
    from pendulum_analysis.allan import compute_allan
    from pendulum_analysis.filters import add_filter_flags
    pps,swings=recording()
    cfg=AnalysisConfig(min_allan_diffs=1)
    frame=add_pps_calibration(add_derived_fields(swings,cfg),pps,cfg)
    frame.loc[:18,'period_s']=2.0
    frame.loc[20:,'period_s']=2.002
    frame.loc[19,'period_s']=np.nan
    frame.loc[19,'pps_calibration_valid']=False
    frame,_=add_filter_flags(frame,cfg)
    result=compute_allan(frame,cfg)
    assert len(result)>0
    assert np.allclose(result.adev_fractional,0,atol=1e-12)
