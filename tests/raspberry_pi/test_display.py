"""Shared mean/forecast semantics plus unchanged PPS qualification regressions."""
import math

import pytest
from pendulum_pi.display import DisplayEstimator, MASK32, RollingSwingMean, MAX_WINDOW_SAMPLES
from pendulum_pi.rating import rated_display

HZ = 10_000_000

def pps(seq, ticks, status=2, drops=0):
    return {"seq": seq & MASK32, "edge_tcb0": ticks & MASK32,
            "gps_status": status, "drop_pps": drops}

def swing(seq, start, intervals=(9_000_000, 1_100_000, 9_000_000, 900_000), **changes):
    result = {"seq": seq & MASK32, "drop_ir": 0, "drop_swing": 0, "drop_pps": 0,
              "edge0_tcb0": start & MASK32}
    for index, value in enumerate(intervals, 1):
        start += value
        result[f"edge{index}_tcb0"] = start & MASK32
    result.update(changes)
    return result

def qualified(**kwargs):
    display=DisplayEstimator(**kwargs)
    display.observe('CPS',pps(0,0),HZ,-2)
    display.observe('CPS',pps(1,HZ),HZ,-1)
    return display

def test_clock_qualifies_adjacent_locked_pairs_and_blends_exactly():
    display = DisplayEstimator()
    display.observe("CPS", pps(MASK32, MASK32 - 30), HZ, 0)
    display.observe("CPS", pps(0, MASK32 - 30 + HZ), HZ, 1)
    assert display.snapshot(1)["pps"]["blended_hz"] == HZ
    display.observe("CPS", pps(1, MASK32 - 30 + 2 * HZ + 1000), HZ, 2)
    expected = HZ + .75 * -math.expm1(-math.log(2) / 20) * 1000
    expected += .25 * -math.expm1(-math.log(2) / 3600) * 1000
    assert display.snapshot(2)["pps"]["blended_hz"] == pytest.approx(expected)
    display.observe("CSW", swing(0, 0), HZ, 2)
    state = display.snapshot(2)
    assert state["timebase"] == "PPS"
    assert state["window"]["period_us"] == pytest.approx(20_000_000 * 1_000_000 / expected)


@pytest.mark.parametrize("bad", [pps(2, 2 * HZ, status=1), pps(2, 3 * HZ)])
def test_rejected_pps_preserves_estimate_without_refreshing_age(bad):
    display = DisplayEstimator()
    display.observe("CPS", pps(0, 0), HZ, 0)
    display.observe("CPS", pps(1, HZ), HZ, 1)
    display.observe("CPS", bad, HZ, 4)
    assert display.snapshot(4)["pps"]["blended_hz"] == HZ
    assert display.snapshot(4)["pps"]["last_accepted_monotonic"] == 1
    assert display.snapshot(6)["pps"]["stale"]
    display.observe("CSW", swing(1, 0), HZ, 6)
    assert display.snapshot(6)["timebase"] == "HOLDOVER"


def test_duplicate_pps_does_not_refresh_calibration_or_replace_previous():
    display = DisplayEstimator()
    display.observe("CPS", pps(0, 0), HZ, 0)
    display.observe("CPS", pps(1, HZ), HZ, 1)
    display.observe("CPS", pps(1, HZ + 100), HZ, 5)
    assert display.snapshot(6)["pps"]["stale"]
    assert display.snapshot(6)["pps"]["last_accepted_monotonic"] == 1
    display.observe("CPS", pps(2, 2 * HZ), HZ, 7)
    assert display.snapshot(7)["pps"]["observation_hz"] == HZ
    assert display.snapshot(7)["pps"]["last_accepted_monotonic"] == 7


def test_pps_window_mean_cancels_accepted_interval_errors_and_resets_after_gap():
    display = DisplayEstimator()
    edge = 0
    display.observe('CPS', pps(0, edge), HZ, 0)
    for seq in range(1, 601):
        edge += HZ + (1000 if seq % 2 else -1000)
        display.observe('CPS', pps(seq, edge), HZ, seq)
    assert display.snapshot(600)['pps']['mean_hz'] == HZ
    assert not display.snapshot(600)['pps']['mean_learning']
    edge += HZ + 3000
    display.observe('CPS', pps(601, edge), HZ, 601)
    assert display.snapshot(601)['pps']['mean_hz'] == pytest.approx(HZ + 2000 / 600)
    edge += HZ
    display.observe('CPS', pps(602, edge), HZ, 620)
    assert display.snapshot(620)['pps']['mean_samples'] == 1
    assert display.snapshot(620)['pps']['mean_learning']


@pytest.mark.parametrize('start', [0, MASK32 - HZ // 2])
def test_early_bursts_preserve_anchor_even_with_raw_queue_losses(start):
    display = DisplayEstimator()
    display.observe('CPS', pps(1, start), HZ, 0)
    display.observe('CPS', pps(2, start + HZ), HZ, 1)
    display.observe('CSW', swing(1, 0), HZ, 1)
    revision = display.estimate_revision
    # Saved burst began 62 ms early; microsecond-spaced extras and lost captures
    # must not shift the next one-second measurement candidate.
    for seq, offset in enumerate([.937868375, .9378820625, .938415625], 3):
        display.observe('CPS', pps(seq, start + HZ + int(HZ * offset), drops=22), HZ, 1 + offset)
    assert display.clock.calibrated_at == 1
    assert display.clock.rejected_edges == 3
    display.observe('CPS', pps(29, start + 2 * HZ, drops=24), HZ, 2)
    assert display.clock.last_accepted
    assert display.clock.observation_hz == HZ
    display.observe('CSW', swing(2, 20_000_000), HZ, 3)
    assert display.estimate_revision == revision
    assert display.snapshot(3)['window']['elapsed_seconds'] == 4


def test_pps_silence_longer_than_timer_wrap_cannot_alias_a_good_second():
    display = DisplayEstimator()
    display.observe('CPS', pps(0, 0), HZ, 0)
    display.observe('CPS', pps(1, HZ), HZ, 1)
    elapsed = (MASK32 + 1) / HZ + 1
    display.observe('CPS', pps(2, 2 * HZ), HZ, 1 + elapsed)
    assert display.clock.calibrated_at == 1
    display.observe('CPS', pps(3, 3 * HZ), HZ, 2 + elapsed)
    assert display.clock.calibrated_at == 2 + elapsed


def test_primary_mean_is_unavailable_without_qualified_pps():
    d=DisplayEstimator()
    d.observe('CSW',swing(0,0),HZ,0)
    state=d.snapshot(0)
    assert not state['available'] and state['timebase']=='WAIT'
    assert state['window']['period_us'] is None
    assert 'short' not in state and 'long' not in state

@pytest.mark.parametrize('intervals,expected',[
    ((9_000_000,1_000_000,9_000_000,1_000_000),(0,0,0,0,0)),
    ((10_000_000,1_100_000,8_000_000,900_000),(200_000,20_000,220_000,10,20)),
    ((8_000_000,900_000,10_000_000,1_100_000),(-200_000,-20_000,-220_000,-10,-20))])
def test_components_share_mean_and_scale_across_wrap(intervals,expected):
    d=qualified();d.observe('CSW',swing(0,MASK32-100,intervals),HZ,0)
    w=d.snapshot(0)['window']
    assert [w[k] for k in ('tick_us','tick_block_us','tock_us','tock_block_us')]==pytest.approx([v/10 for v in intervals])
    assert sum(w[k] for k in ('tick_us','tick_block_us','tock_us','tock_block_us'))==w['period_us']
    for k,v in zip(('tick_delta_us','block_delta_us','half_delta_us','open_imbalance_pct','block_imbalance_pct'),expected):
        assert w[k]==pytest.approx(v)

def test_mean_uses_each_observation_pps_dual_scale_not_later_pps_mean():
    d=qualified();d.observe('CSW',swing(0,0),HZ,0)
    d.observe('CPS',pps(2,2*HZ+1000),HZ,1)
    calibrated=d.clock.blended_hz
    d.observe('CSW',swing(1,20_000_000),HZ,2)
    expected=(2_000_000+20_000_000*1e6/calibrated)/2
    assert d.snapshot(2)['window']['period_us']==pytest.approx(expected)
    assert sum(d.snapshot(2)['window'][k] for k in ('tick_us','tick_block_us','tock_us','tock_block_us'))==pytest.approx(expected)
    before=d.snapshot(2)['window']['period_us']
    d.observe('CPS',pps(3,3*HZ+2000),HZ,3)
    assert d.snapshot(3)['window']['period_us']==before

def test_captured_window_not_receipt_batch_controls_mean():
    states=[]
    for arrivals in ((0,2),(0,.01)):
        d=qualified();a=swing(0,0);b=swing(1,a['edge4_tcb0'],(10_000_000,1_100_000,9_000_000,900_000))
        d.observe('CSW',a,HZ,arrivals[0]);d.observe('CSW',b,HZ,arrivals[1]);states.append(d.snapshot(arrivals[1])['window'])
    assert states[0]==states[1]
    assert states[0]['period_us']==2_050_000

def test_window_mean_retains_impulse_and_is_not_fixed_swing_count():
    for nominal_period in (1.,2.,3.):
        d=DisplayEstimator();d.observe('CPS',pps(0,0),HZ,0)
        elapsed=0.;start=0;next_pps=1
        for seq in range(math.ceil(602/nominal_period)):
            total=round(HZ*(nominal_period+(.015 if seq%15==14 else 0)))
            parts=(total//4,)*3+(total-3*(total//4),)
            value=swing(seq,start,parts);elapsed+=total/HZ
            while next_pps<=int(elapsed):
                d.observe('CPS',pps(next_pps,next_pps*HZ),HZ,next_pps);next_pps+=1
            d.observe('CSW',value,HZ,elapsed);start=value['edge4_tcb0']
        w=d.snapshot(elapsed)['window']
        assert not w['learning']
        assert w['period_us']>nominal_period*1e6
        assert w['count']==pytest.approx(600/nominal_period,abs=2)
        assert w['bpm']==pytest.approx(60e6/w['period_us'])

def test_exact_window_cutoff_evicts_boundary_completed_swing():
    w=RollingSwingMean()
    for _ in range(301):w.observe((500000,)*4)
    assert w.snapshot(True)['count']==300
    assert w.snapshot(True)['filling_seconds']==600

@pytest.mark.parametrize('changes',[{'seq':3},{'edge0_tcb0':20_000_001},{'drop_ir':1},{'drop_swing':1}])
def test_discontinuity_resets_mean_and_pending_forecast(changes):
    d=qualified(forecast_cycle_length=15)
    d.observe('CSW',swing(0,0),HZ,0);d.observe('CSW',swing(1,20_000_000),HZ,2)
    revision=d.estimate_revision
    changed=swing(2,40_000_000);changed.update(changes)
    d.observe('CSW',changed,HZ,4)
    state=d.snapshot(4)
    assert d.estimate_revision>revision
    assert state['window']['count']==1 and state['window']['learning']
    assert state['forecast']['last'] is None

def test_duplicate_does_not_refresh_feed_or_pending_prediction():
    d=qualified(forecast_cycle_length=15);value=swing(0,0)
    d.observe('CSW',value,HZ,0);old=d.snapshot(0)['forecast']
    d.observe('CSW',value,HZ,9)
    assert d.snapshot(10)['stale'] and d.snapshot(10)['window']['period_us'] is None
    assert d.snapshot(9)['forecast']['next']==old['next']
    assert all('stale' in row for row in d.snapshot(10)['rows'])

def test_expiry_keeps_mean_observations_but_never_scores_across_missing_calibration():
    d=qualified(pps_holdover_seconds=5,forecast_cycle_length=15)
    d.observe('CSW',swing(0,0),HZ,0);d.observe('CSW',swing(1,20_000_000),HZ,2)
    count=d.snapshot(2)['window']['count'];revision=d.estimate_revision
    d.observe('CSW',swing(2,40_000_000),HZ,4)
    expired=d.snapshot(4)
    assert expired['stale'] and expired['window']['period_us'] is None
    assert expired['window']['count']==count and expired['forecast']['next'] is None
    d.observe('CPS',pps(2,5*HZ),HZ,5);d.observe('CPS',pps(3,6*HZ),HZ,6)
    d.observe('CSW',swing(3,60_000_000),HZ,6)
    resumed=d.snapshot(6)
    assert resumed['window']['count']==count+1 and resumed['window']['learning']
    assert resumed['forecast']['last'] is None
    assert d.estimate_revision==revision

def test_configuration_affects_forecast_only_and_holdover_does_not_clear_mean():
    d=qualified();d.observe('CSW',swing(0,0),HZ,0)
    w=d.snapshot(0)['window'];revision=d.estimate_revision
    assert d.configure(forecast_cycle_length=15)
    assert d.snapshot(0)['window']==w and d.estimate_revision==revision
    assert not d.configure(pps_holdover_seconds=5)
    assert d.snapshot(4)['stale']
    assert not d.configure(pps_holdover_seconds=180)
    assert not d.snapshot(4)['stale']
    assert d.snapshot(4)['window']==w

def test_invalid_intervals_and_silence_clear_pending_forecast():
    d=qualified(forecast_cycle_length=15);d.observe('CSW',swing(0,0),HZ,0)
    d.observe('CSW',swing(1,20_000_000,(0,1,1,1)),HZ,2)
    assert not d.snapshot(2)['available'] and d.snapshot(2)['forecast']['next'] is None
    d.observe('CSW',swing(2,20_000_000),HZ,3)
    rev=d.estimate_revision;d.observe('CSW',swing(3,40_000_000),HZ,20)
    assert d.estimate_revision>rev

def test_mean_capacity_cannot_grow_unbounded_or_claim_complete_window():
    w=RollingSwingMean()
    for _ in range(MAX_WINDOW_SAMPLES+1):w.observe((1.,)*4)
    assert len(w.samples)==0
    assert w.snapshot(True)['learning'] and w.snapshot(True)['period_us'] is None

def test_oled_rows_use_canonical_rated_values_and_fit_screen():
    d=qualified();d.observe('CSW',swing(0,0),HZ,0)
    state=rated_display(d.snapshot(0),2)
    assert all(len(row)<=21 for row in state['rows'])
    assert state['rows'][0]=='P 2.000000000s'
    assert state['rows'][2]=='R +0.000000s/d'
