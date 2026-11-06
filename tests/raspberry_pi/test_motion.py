"""Physical estimates, cycle coverage, additive upgrades and capture validity."""
import math
import sqlite3
from copy import deepcopy
import json

import pytest

from pendulum_pi.motion import MotionEstimator, FIELDS, MODEL
from pendulum_pi.history import _connect, SCHEMA_VERSION, HistoryWriter, query_history
from pendulum_pi.analysis import Analysis
from pendulum_pi.config import Settings


def parts(tick_ms=20, tock_ms=25):
    return [(1000-tick_ms)*1000, tick_ms*1000, (1000-tock_ms)*1000, tock_ms*1000]


def test_speed_magnitude_harmonic_amplitude_and_mean_of_individual_inverses():
    motion = MotionEstimator()
    motion.observe(0, parts())
    first = motion.snapshot(True)
    assert first['latest']['tick_velocity_mm_s'] == pytest.approx(189.5)
    assert first['latest']['tock_velocity_mm_s'] == pytest.approx(151.6)
    assert first['latest']['tick_amplitude_deg'] == pytest.approx(math.degrees(189.5 / (math.pi * 1242)))
    motion.observe(1, parts(40, 50))
    result = motion.snapshot(True)
    assert result['mean']['tick_velocity_mm_s'] == pytest.approx((189.5 + 94.75) / 2)
    assert result['mean']['tick_velocity_mm_s'] != pytest.approx(3.79 / .030)
    assert result['count'] == 2 and result['learning']
    assert all(v is None for v in motion.snapshot(True, True)['mean'].values())
    assert motion.snapshot(False)['latest']['tick_velocity_mm_s'] is None


def test_cycle_medians_only_use_complete_cycles_and_compare_like_directions():
    motion = MotionEstimator()
    for seq in range(4, 65):
        # Tock is always faster: an alternating baseline must not be identified
        # as the impulse. The true repeating increase is at tick phase ten.
        phase = seq % 15
        motion.observe(seq, parts(18 if phase >= 5 else 20, 10))
    state = motion.snapshot(True)
    assert state['cycle']['complete_cycles'] == 3
    assert state['cycle']['candidate_phase'] == 10
    assert all(point['count'] == 3 for point in state['cycle']['points'])
    assert state['cycle']['points'][10]['side'] == 'tick'
    assert state['cycle']['points'][11]['side'] == 'tock'
    for seq in range(65, 700):
        motion.observe(seq, parts())
    state = motion.snapshot(True)
    assert len(motion.samples) == 315 and state['count'] == 300
    assert state['cycle']['complete_cycles'] == 20
    assert state['cycle']['candidate_phase'] is None
    assert not state['learning']


def test_wrap_duplicate_and_gaps_do_not_rotate_or_bridge_cycle():
    motion = MotionEstimator()
    for seq in range(2**32-5, 2**32+40):
        motion.observe(seq & 0xffffffff, parts())
    assert motion.extended == 2**32+39
    before = deepcopy(motion.__dict__)
    motion.observe(39, parts(30))
    assert motion.__dict__ == before
    motion.observe(41, parts())
    assert motion.snapshot(True)['count'] == 1
    assert motion.snapshot(True)['cycle']['complete_cycles'] == 0
    motion.observe(42, [0, 1, 1, 1])
    assert not motion.snapshot(True)['available']
    assert motion.snapshot(True)['revision'] == 1


@pytest.mark.parametrize('bad', [[-1, 1, 1, 1], [1, float('nan'), 1, 1],
                                 [1, 1, 1, 1], [700000, 300000, 980000, 20000]])
def test_unsupported_passages_are_unavailable(bad):
    motion = MotionEstimator()
    motion.observe(0, parts())
    motion.observe(1, bad)
    assert not motion.snapshot(True)['available']


def test_schema_three_migration_preserves_old_history_and_leaves_motion_missing(tmp_path):
    path = tmp_path / 'observatory-history.sqlite3'
    connection = _connect(path)
    old = dict(time=1800000000., session='s', segment='s', source='serial', quality={},
               window_period_s=2.)
    connection.execute('INSERT INTO observations(time,session,segment,source,payload) VALUES(?,?,?,?,?)',
                       (old['time'], 's', 's', 'serial', json.dumps(old)))
    # Recreate a genuine schema-three database, without new motion columns.
    for field in FIELDS:
        connection.execute(f'ALTER TABLE observations DROP COLUMN {field}')
        for prefix in ('min', 'max'):
            connection.execute(f'ALTER TABLE minutes DROP COLUMN {prefix}_{field}')
            connection.execute(f'ALTER TABLE minutes DROP COLUMN {prefix}_{field}_id')
    connection.execute('PRAGMA user_version=3')
    connection.commit(); connection.close()
    connection = _connect(path)
    assert connection.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
    connection.close()
    points = query_history(tmp_path, old['time']-1, old['time']+1)['points']
    assert points[0]['window_period_s'] == 2.
    assert all(points[0][field] is None for field in FIELDS)
    assert points[0]['quality']['motion_learning']


def test_old_analysis_checkpoint_adds_motion_without_replaying_or_resetting_clock(tmp_path):
    config = Settings(data_dir=tmp_path/'data', runtime_dir=tmp_path/'run', min_free_mb=0)
    worker = Analysis(config)
    try:
        worker.display.clock.blended_hz = 15999999.5
        old = worker.state()
        old['history']['_health']['schema_version'] = 3
        del old['models']['motion']; del old['models']['motion_model']
        worker.restore(old)
        assert worker.history.snapshot()['schema_version'] == SCHEMA_VERSION
        assert worker.display.clock.blended_hz == 15999999.5
        assert not worker.display.motion.samples
        worker.display.motion.observe(15, parts())
        saved = worker.state()
        worker.restore(saved)
        assert worker.state() == saved
        saved['models']['motion_model'] = 'unknown'
        with pytest.raises(ValueError, match='motion model'):
            worker.restore(saved)
    finally:
        worker.close()


def test_common_timebase_scale_cancels_from_amplitude_but_not_speed():
    a, b = MotionEstimator(), MotionEstimator()
    a.observe(0, parts())
    b.observe(0, [part * 1.001 for part in parts()])
    first, second = a.snapshot(True)['latest'], b.snapshot(True)['latest']
    assert second['tick_velocity_mm_s'] == pytest.approx(first['tick_velocity_mm_s'] / 1.001)
    assert second['tick_amplitude_deg'] == pytest.approx(first['tick_amplitude_deg'])


def test_motion_history_persists_and_uses_independent_gaps(tmp_path):
    config = Settings(data_dir=tmp_path/'data', runtime_dir=tmp_path/'run', min_free_mb=0)
    config.data_dir.mkdir()
    writer = HistoryWriter(config, sample_interval_seconds=1)
    connection = _connect(writer.path)
    motion = MotionEstimator()
    def point(seq):
        return dict(updated_monotonic=seq+1., observed_epoch=1800000000.+seq, source='demo',
                    ready=True, capture_stale=False, logging=dict(active=True, enabled=True, session='a'),
                    display=dict(available=True, stale=False, timebase='PPS',
                                 window=dict(period_us=2e6, elapsed_seconds=seq*2, learning=False),
                                 motion=motion.snapshot(True)))
    try:
        motion.observe(0, parts()); writer._write(connection, point(0), config)
        motion.observe(1, parts()); writer._write(connection, point(1), config)
        motion.observe(2, [0,1,1,1]); writer._write(connection, point(2), config)
        motion.observe(3, parts()); writer._write(connection, point(3), config)
        points = query_history(config.data_dir, 1799999999., 1800000004.)['points']
        assert points[0]['tick_velocity_mm_s'] == pytest.approx(189.5)
        assert points[0]['motion_geometry']['model'] == MODEL
        assert points[0]['quality']['motion_count'] == 1
        assert points[0]['continuity']['motion'] == points[1]['continuity']['motion']
        assert points[1]['continuity']['motion'] != points[3]['continuity']['motion']
        assert points[1]['continuity']['timing'] == points[3]['continuity']['timing']
        assert points[2]['tick_velocity_mm_s'] is None
    finally:
        connection.close()
