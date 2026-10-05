"""Causal prediction ordering, captured-time weights, and bounded resets."""

import math
import importlib.util
import json
from pathlib import Path

import pytest

from pendulum_pi.forecast import MASK32, SwingForecaster


def observe(model, seq, period=500000.0, baseline=500000.0, **kwargs):
    model.observe(seq, period, baseline, period / 1e6, **kwargs)
    return model.snapshot()


def test_disabled_by_default_and_configuration_does_not_reset_unchanged_model():
    model = SwingForecaster()
    assert not model.snapshot()['enabled']
    observe(model, 0)
    assert model.snapshot()['next'] is None
    assert model.configure(1)
    state = observe(model, 0)
    assert state['next']['period_us'] == 500000
    assert not model.configure(1)
    assert model.snapshot() == state
    assert model.configure(15)
    assert model.snapshot()['next'] is None


@pytest.mark.parametrize('value', [-1, 121, 2.5, True, None, '2', float('inf'), float('nan')])
def test_configuration_rejects_ambiguous_cycle_lengths(value):
    with pytest.raises(ValueError):
        SwingForecaster(value)


def test_target_is_scored_against_frozen_origin_before_learning():
    model = SwingForecaster(1)
    before = observe(model, 7, baseline=500010)
    after = observe(model, 8, period=500020, baseline=500030)
    assert before['next']['target_seq'] == 8
    assert after['last'] == {'target_seq': 8, 'origin_seq': 7,
                             'predicted_period_us': 500010,
                             'observed_period_us': 500020, 'error_us': 10,
                             'baseline_error_us': 10, 'timebase': 'PPS', 'learning': True}
    assert after['next']['period_us'] == 500030
    assert after['recent']['rmse_us'] == 10
    # Returning a snapshot never lets a consumer alter the frozen forecast.
    before['next']['period_us'] = 0
    assert model.snapshot()['last']['predicted_period_us'] == 500010


def test_duplicate_does_not_refresh_duration_or_learn_and_gap_cannot_score():
    model = SwingForecaster(3)
    observe(model, 0)
    before = observe(model, 1)
    observe(model, 1, period=900000)
    assert model.snapshot() == before
    after = observe(model, 3)
    assert after['revision'] == before['revision'] + 1
    assert after['reset_reason'] == 'sequence discontinuity'
    assert after['last'] is None and after['recent']['count'] == 0
    assert after['next']['target_seq'] == 4


def test_uint32_rollover_keeps_pattern_phase_and_scores_exact_target():
    model = SwingForecaster(3)
    observe(model, MASK32 - 1)
    observe(model, MASK32)
    state = observe(model, 0)
    assert state['last']['origin_seq'] == MASK32
    assert model.phase == ((MASK32 - 1) % 3 + 2) % 3
    assert state['revision'] == 1
    assert state['recent']['count'] == 2


def test_shape_uses_complete_cycle_mean_and_phase_captured_duration():
    model = SwingForecaster(2)
    observe(model, 0, period=400000)
    observe(model, 1, period=600000)
    weight = -math.expm1(-math.log(2) * 1.0 / 600)
    assert model.shape[1] == pytest.approx(weight * 100000)
    observe(model, 2, period=400000)
    state = observe(model, 3, period=800000)
    second_weight = -math.expm1(-math.log(2) * 1.2 / 600)
    assert model.shape[1] == pytest.approx(weight * 100000 + second_weight *
                                          (200000 - weight * 100000))
    assert state['next']['pattern_us'] == pytest.approx(model.shape[0] - sum(model.shape) / 2)


def test_adaptive_pattern_improves_on_same_generic_baseline_without_rate_change():
    model = SwingForecaster(3)
    mean = 500000
    for seq in range(15000):
        observe(model, seq, period=mean + (-200, -100, 300)[seq % 3])
    state = model.snapshot()
    assert not state['learning']
    assert state['recent']['rmse_us'] < state['recent']['baseline_rmse_us'] / 100
    assert state['next']['baseline_period_us'] == mean
    centered = [phase - sum(model.shape) / 3 for phase in model.shape]
    assert sum(centered) == pytest.approx(0, abs=1e-10)


@pytest.mark.parametrize('period,baseline,timebase', [
    (0, 500000, 'PPS'), (float('nan'), 500000, 'PPS'),
    (500000, None, 'PPS'), (500000, 500000, 'NOMINAL'),
    (500000, 500000, 'STALE'),
])
def test_invalid_observation_or_unavailable_calibration_clears_model(period, baseline, timebase):
    model = SwingForecaster(2)
    observe(model, 0)
    observe(model, 1)
    model.observe(2, period, baseline, .5, timebase=timebase)
    state = model.snapshot()
    assert state['next'] is None and state['last'] is None
    assert state['recent']['count'] == 0
    assert not state['available']


def test_holdover_is_labelled_and_stale_snapshot_withholds_next_prediction():
    model = SwingForecaster(1)
    observe(model, 0, timebase='HOLDOVER')
    assert model.snapshot()['next']['timebase'] == 'HOLDOVER'
    assert model.snapshot(stale=True)['next'] is None
    assert model.snapshot(available=False)['next'] is None
    assert model.snapshot()['next'] is not None


@pytest.mark.parametrize('values', [(True, 500000, .5), (500000, True, .5), (500000, 500000, True)])
def test_boolean_numeric_measurements_are_invalid(values):
    model = SwingForecaster(1)
    model.observe(0, *values)
    assert model.snapshot()['next'] is None


def test_negative_shape_prediction_is_withheld_and_cannot_score_next_target():
    model = SwingForecaster(2)
    observe(model, 0, period=400000)
    observe(model, 1, period=600000, baseline=.000001)
    assert model.snapshot()['next'] is None
    assert model.snapshot()['reset_reason'] == 'invalid learned prediction'
    observe(model, 2)
    assert model.snapshot()['last'] is None


def test_extreme_measurements_cannot_create_nonfinite_scores():
    model = SwingForecaster(1)
    model.observe(0, 1, 1e308, 1)
    model.observe(1, 1, 1, 1)
    assert model.snapshot()['recent']['count'] == 0
    assert model.snapshot()['reset_reason'] == 'nonfinite prediction error'


def test_score_window_is_captured_time_and_memory_is_bounded():
    model = SwingForecaster(1)
    for seq in range(1000):
        model.observe(seq, 1000000, 1000000, 1)
    assert model.snapshot()['recent']['count'] == 600
    for seq in range(1000, 12000):
        model.observe(seq, 1, 1, .000001)
    assert len(model.scores) == 10000
    assert len(model.periods) == 1


def replay_module():
    path = Path(__file__).resolve().parents[2] / 'tools' / 'swing_forecast_replay.py'
    spec = importlib.util.spec_from_file_location('swing_forecast_replay', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_raw_replay_keeps_recorded_order_and_uses_actual_recording_schema(tmp_path):
    module = replay_module()
    path = tmp_path / 'RAW.jsonl'
    # A swing received before PPS must remain before PPS, despite its edge time.
    lines = ['CSW,0,0,4000000,5000000,9000000,10000000,0,0,0\n',
             'CPS,0,0,2,0,0,0,0,0\n', 'CPS,1,10000000,2,0,0,0,10000000,0\n',
             'CSW,1,10000000,14000000,15000000,19000000,20000000,0,0,0\n',
             'CSW,2,20000000,24000000,25000000,29000000,30000000,0,0,0\n']
    path.write_text(''.join(json.dumps({'ts_ms': i * 1000, 'raw_hex': line.encode().hex(),
                                        'text': line, 'fragment': False}) + '\n'
                            for i, line in enumerate(lines)))
    records = list(module.raw_records(path))
    assert [record.tag for _, record in records] == ['CSW', 'CPS', 'CPS', 'CSW', 'CSW']
    result = module.replay(iter(records), 10000000, 1, 180)
    assert result['all_scores']['count'] == 1
    assert result['final']['forecast']['last']['target_seq'] == 2
    assert result['all_scores']['rmse_us'] == 0


def test_raw_replay_rejects_reversed_receipt_timestamps(tmp_path):
    module = replay_module()
    path = tmp_path / 'RAW.jsonl'
    path.write_text('\n'.join(json.dumps({'ts_ms': ts, 'text': 'get:foo\n'}) for ts in [1000, 0]))
    with pytest.raises(ValueError, match='receipt timestamps reverse'):
        list(module.raw_records(path))


def test_reconstructed_event_order_is_timer_based_across_wrap(tmp_path):
    module = replay_module()
    origin = MASK32 - 5000000
    pps = 'seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps\n'
    pps += f'0,{origin},2,0,0,0,{origin},0\n'
    pps += f'1,{(origin + 10000000) & MASK32},2,0,0,0,0,0\n'
    (tmp_path / 'PCPS.CSV').write_text(pps)
    swing = 'seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing\n'
    swing += '0,' + ','.join(str((origin + ticks) & MASK32) for ticks in
                            (0, 4000000, 5000000, 9000000, 10000000)) + ',0,0,0\n'
    (tmp_path / 'PCSW.CSV').write_text(swing)
    records = list(module.reconstructed_records(tmp_path, 10000000))
    assert [(now, record.tag) for now, record in records] == [(0, 'CPS'), (1, 'CPS'), (1, 'CSW')]


def test_scored_results_backfill_browser_misses_are_bounded_and_reset_together():
    model = SwingForecaster(1)
    for seq in range(200):
        model.observe(seq, 500000., 500000., .5, observed_epoch=1800000000. + seq * .5)
    state = model.snapshot()
    results = state['results']
    assert len(results) == 120
    assert [row['target_seq'] for row in results] == list(range(80, 200))
    assert results[-1]['observed_epoch'] == 1800000099.5
    results[-1]['error_us'] = 999
    assert model.snapshot()['results'][-1]['error_us'] == 0
    assert model.snapshot(stale=True)['results']  # Historical scores survive a stale UI poll.
    model.reset('capture gap')
    assert model.snapshot()['results'] == []
