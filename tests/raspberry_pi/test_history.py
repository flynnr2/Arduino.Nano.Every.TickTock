"""Display history remains independent of raw files and browser lifetime."""
from dataclasses import replace
import json
import sqlite3

import pytest

from pendulum_pi.config import Settings
from pendulum_pi.history import HistoryWriter, query_history, _connect, DATABASE, SERIES, WINDOW_SERIES

BASE = 1800000000.0


def settings(tmp_path, **kw):
    return replace(Settings(), data_dir=tmp_path / 'data', runtime_dir=tmp_path / 'runtime',
                   min_free_mb=0, **kw)


def sample(second=0, **overrides):
    value = {'observed_epoch': BASE + second, 'updated_monotonic': second + 1.,
             'source': 'demo', 'ready': True, 'capture_stale': False,
             'logging': {'active': True, 'enabled': True, 'session': 'raw-session'},
             'display': {'available': True, 'stale': False, 'timebase': 'PPS', 'estimate_revision': 1,
                         'short': {'period_us': 2000000., 'block_delta_us': 12., 'learning': False,
                                   'elapsed_seconds': 300},
                         'long': {'period_us': 2000001., 'block_delta_us': 15., 'learning': True},
                         'window': {'period_us': 1999999., 'learning': False, 'gain_seconds_per_day': .0432000216, 'elapsed_seconds': 300, 'model': 'swing_mean_600s_pps_dual_ewma_v1'}},
             'latest': {'CSW': {'seq': int(second), 'drop_ir': 0, 'drop_swing': 0}},
             'environment': {'temperature_C': 21., 'humidity_pct': 45., 'pressure_hPa': 1001.,
                             'sht4x': {'fresh': True}, 'bmp280': {'fresh': True}}}
    value.update(overrides)
    return value


def open_writer(config):
    config.data_dir.mkdir(parents=True, exist_ok=True)
    writer = HistoryWriter(config, sample_interval_seconds=1)
    return writer, _connect(writer.path)


def test_browser_independent_persistence_and_recorded_settings(tmp_path):
    config = settings(tmp_path, target_period_s=2.)
    writer = HistoryWriter(config)
    writer.start()
    assert writer.submit(sample(), config)
    assert not writer.submit(sample(.2), config)
    writer.close()
    assert writer.snapshot()['written_points'] == 1
    result = query_history(config.data_dir, BASE - 1, BASE + 10)
    point = result['points'][0]
    assert point['short_period_s'] is None
    assert point['short_rate_s_day'] is None
    assert point['long_rate_s_day'] is None
    assert point['window_period_s'] == 1.999999
    assert point['window_rate_s_day'] > 0
    assert point['quality']['window_learning'] is False
    assert point['settings']['target_period_s'] == 2.
    assert point['settings']['estimator_model'] == 'swing_mean_600s_pps_dual_ewma_v1'
    assert point['source'] == 'demo'
    assert point['capture']['seq'] == 0
    second = HistoryWriter(config)
    second.start()
    second.submit(sample(2))
    second.close()
    result = query_history(config.data_dir, BASE - 1, BASE + 10)
    assert len(result['sessions']) == 2
    assert result['points'][0]['segment'] != result['points'][1]['segment']


@pytest.mark.parametrize('version', [1, 2])
def test_existing_history_is_upgraded_with_unavailable_older_window_mean(tmp_path, version):
    config = settings(tmp_path, target_period_s=2.)
    config.data_dir.mkdir(parents=True)
    path = config.data_dir / DATABASE
    old_series = [name for name in SERIES if name not in WINDOW_SERIES]
    if version == 2:
        old_series += ['median_period_s', 'median_rate_s_day']
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE observations (id INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, session TEXT NOT NULL, segment TEXT NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL,'
                       + ','.join(f'{name} REAL' for name in old_series) + ')')
    connection.execute('CREATE TABLE minutes (bucket INTEGER NOT NULL, segment TEXT NOT NULL, session TEXT NOT NULL, first_id INTEGER NOT NULL, last_id INTEGER NOT NULL,'
                       + ','.join(f'min_{name} REAL,max_{name} REAL,min_{name}_id INTEGER,max_{name}_id INTEGER' for name in old_series)
                       + ',PRIMARY KEY(bucket,segment))')
    old = {'time': BASE, 'session': 'old', 'segment': 'old-segment', 'source': 'demo',
           'quality': {}, 'short_period_s': 2., 'long_period_s': 2.}
    if version == 2:
        old.update(median_period_s=1.9, median_rate_s_day=100.)
        old['quality']['median_learning'] = False
    connection.execute('INSERT INTO observations (time,session,segment,source,payload) VALUES (?,?,?,?,?)',
                       (BASE, 'old', 'old-segment', 'demo', json.dumps(old)))
    connection.execute(f'PRAGMA user_version={version}')
    connection.commit()
    connection.close()
    writer, connection = open_writer(config)
    writer._write(connection, sample(1), config)
    connection.close()
    points = query_history(config.data_dir, BASE - 1, BASE + 2)['points']
    assert points[0]['window_period_s'] is None
    assert points[0]['quality']['window_learning'] is True
    assert 'median_period_s' not in points[0]
    assert 'median_learning' not in points[0]['quality']
    assert points[1]['window_period_s'] == 1.999999


def test_dashboard_reader_does_not_block_history_writer(tmp_path):
    config = settings(tmp_path)
    writer, connection = open_writer(config)
    writer._write(connection, sample(), config)
    reader = sqlite3.connect(f'{writer.path.resolve().as_uri()}?mode=ro', uri=True)
    try:
        reader.execute('BEGIN')
        assert reader.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 1
        writer._write(connection, sample(1), config)
        # The open dashboard snapshot remains stable while the writer commits.
        assert reader.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 1
    finally:
        reader.close()
        connection.close()
    assert len(query_history(config.data_dir, BASE - 1, BASE + 2)['points']) == 2


def test_boundary_reason_appears_only_at_segment_start(tmp_path):
    config = settings(tmp_path)
    writer, connection = open_writer(config)
    for second in range(3):
        writer._write(connection, sample(second), config)
    connection.close()
    points = query_history(config.data_dir, BASE - 1, BASE + 3)['points']
    assert points[0]['boundary'] == ['history_started']
    assert points[1]['boundary'] == points[2]['boundary'] == []
    assert len({point['segment'] for point in points}) == 1


def test_pause_stale_environment_and_rotation_boundaries(tmp_path):
    config = settings(tmp_path)
    writer, connection = open_writer(config)
    writer._write(connection, sample(), config)
    rotation = sample(1)
    rotation['logging']['segment'] = 2
    writer._write(connection, rotation, config)
    paused = sample(2)
    paused['logging']['enabled'] = False
    writer._write(connection, paused, config)
    paused = sample(3)
    paused['logging']['enabled'] = False
    writer._write(connection, paused, config)
    stale = sample(4)
    stale['display']['stale'] = True
    stale['environment']['sht4x']['fresh'] = False
    writer._write(connection, stale, config)
    writer._write(connection, sample(5), config)
    connection.close()
    points = query_history(config.data_dir, BASE - 1, BASE + 10)['points']
    assert len(points) == 5
    assert points[0]['segment'] == points[1]['segment']
    assert points[2]['gap_reason'] == 'recording_disabled'
    assert points[2]['short_period_s'] is None
    assert points[3]['gap_reason'] == 'stale'
    assert points[3]['temperature_C'] is None
    assert points[4]['segment'] != points[3]['segment']


def test_clock_step_config_loss_and_hidden_estimator_reset_are_boundaries(tmp_path):
    config = settings(tmp_path)
    writer, connection = open_writer(config)
    initial = sample()
    initial['display']['window']['gain_seconds_per_day'] = None
    writer._write(connection, initial, config)
    config = replace(config, target_period_s=2.1)
    changed = sample(1)
    changed['display']['window']['gain_seconds_per_day'] = 4320.
    writer._write(connection, changed, config)
    step = sample(2, observed_epoch=BASE + 1000)
    step['display']['estimate_revision'] = 3
    step['counters'] = {'swing_missing': 3}
    writer._write(connection, step, config)
    connection.close()
    points = query_history(config.data_dir, BASE - 1, BASE + 1001)['points']
    assert 'settings_changed' in points[1]['boundary']
    assert {'wall_clock_correction', 'capture_loss', 'estimator_reset'} <= set(points[2]['boundary'])
    assert points[0]['window_rate_s_day'] is None
    assert points[1]['window_rate_s_day'] == 4320.


def test_full_queue_is_nonblocking_and_recovery_marks_gap(tmp_path):
    config = settings(tmp_path)
    writer, connection = open_writer(config)
    writer.queue = __import__('queue').Queue(maxsize=1)
    assert writer.submit(sample())
    assert not writer.submit(sample(1))
    assert writer.snapshot()['dropped_points'] == 1
    writer.queue.get_nowait()
    writer._write(connection, sample(2), config)
    connection.close()
    point = query_history(config.data_dir, BASE, BASE + 3)['points'][0]
    assert 'history_queue_overflow' in point['boundary']


def test_long_query_uses_minute_extrema_preserves_excursions_and_budget(tmp_path):
    config = settings(tmp_path, target_period_s=2.)
    writer, connection = open_writer(config)
    # Spread display samples over multiple days; acquisition gaps remain explicit.
    for i in range(1200):
        value = sample(i * 300)
        if i == 333:
            value['display']['window']['period_us'] = 1998000.
            value['display']['window']['gain_seconds_per_day'] = 86.486486
        if i == 701:
            value['environment']['temperature_C'] = 39.
        writer._write(connection, value, config)
    connection.close()
    response = query_history(config.data_dir, BASE + 7, BASE + 1200 * 300, max_points=100)
    assert response['reduced']
    assert response['returned_points'] <= 100
    assert all(p['short_period_s'] is None for p in response['points'])
    assert min(p['window_period_s'] for p in response['points']) == 1.998
    assert max(p['temperature_C'] for p in response['points']) == 39.
    rate_only = query_history(config.data_dir, BASE, BASE + 1200 * 300, max_points=32,
                              series=['window_rate_s_day'])
    assert max(p['window_rate_s_day'] for p in rate_only['points']) > 40.
    assert all('temperature_C' not in p for p in rate_only['points'])


def test_retention_and_size_only_remove_derived_observations(tmp_path):
    config = settings(tmp_path, history_retention_days=1, history_max_mb=8)
    writer, connection = open_writer(config)
    raw = config.data_dir / 'raw.csv'
    raw.write_text('do not change')
    writer._write(connection, sample(), config)
    writer._write(connection, sample(86401), config)
    assert connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 1
    assert raw.read_text() == 'do not change'
    assert writer.path.stat().st_size < 8 * 1024**2
    connection.close()


@pytest.mark.parametrize('selected_session', [None, 'edge-session'])
def test_reduced_partial_minutes_keep_edge_extrema_and_exact_range(tmp_path, selected_session):
    config = settings(tmp_path)
    writer, connection = open_writer(config)
    writer.session = 'edge-session'
    for second in range(0, 4031, 10):
        value = sample(second)
        # The excluded edges are more extreme than the in-range edge extrema.
        temperature = {0: -99., 10: -10., 4020: 40., 4030: 99.}.get(second, 21.)
        value['environment']['temperature_C'] = temperature
        writer._write(connection, value, config)
    connection.close()
    result = query_history(config.data_dir, BASE + 7, BASE + 4023,
                           session=selected_session, series=['temperature_C'], max_points=32)
    assert result['reduced']
    points = result['points']
    assert len(points) <= 32
    assert points[0]['time'] == BASE + 10
    assert points[-1]['time'] == BASE + 4020
    assert min(p['temperature_C'] for p in points) == -10.
    assert max(p['temperature_C'] for p in points) == 40.


def test_seven_day_reduction_does_not_rescan_raw_range_for_every_metric(tmp_path, monkeypatch):
    # SQLite work is deterministic enough to bound repeated range scans without
    # timing assertions that depend on the machine or its disk cache.
    connection = _connect(tmp_path / DATABASE)
    count = 7 * 8640
    with connection:
        connection.executemany(
            'INSERT INTO observations (time,session,segment,source,payload,window_period_s) VALUES (?,?,?,?,?,?)',
            ((BASE + i * 10, 's', 'a', 'demo', json.dumps(dict(time=BASE+i*10, session='s', segment='a',
                                                           session_start_time=BASE, window_period_s=2.)), 2.)
             for i in range(count)))
        connection.execute('''INSERT INTO minutes
            (bucket,segment,session,first_id,last_id,min_window_period_s,max_window_period_s,
             min_window_period_s_id,max_window_period_s_id)
            SELECT CAST(time/60 AS INTEGER)*60,segment,session,MIN(id),MAX(id),
                   2.,2.,MIN(id),MAX(id) FROM observations GROUP BY CAST(time/60 AS INTEGER)''')
    connection.close()
    calls = 0
    real_connect = sqlite3.connect
    class Meter(sqlite3.Connection):
        def set_progress_handler(self, callback, steps):
            def progress():
                nonlocal calls
                calls += 1
                return callback()
            super().set_progress_handler(progress, steps)
    monkeypatch.setattr(sqlite3, 'connect', lambda *args, **kwargs: real_connect(*args, **kwargs, factory=Meter))
    result = query_history(tmp_path, BASE+7, BASE+7*86400-7, max_points=1200)
    assert result['reduced'] and result['points']
    assert all(p['window_period_s'] == 2. for p in result['points'])
    # The old edge filters execute roughly 19 million instructions here. The
    # indexed seeks plus summary/metadata reads should stay below 8 million.
    assert calls * 2000 < 8_000_000


def test_missing_database_and_invalid_requests(tmp_path):
    assert query_history(tmp_path, BASE, BASE + 1)['points'] == []
    assert not (tmp_path / DATABASE).exists()
    for args in ({'max_points': 1}, {'series': ['sql;drop']}, {'max_points': True}):
        with pytest.raises(ValueError):
            query_history(tmp_path, BASE, BASE + 1, **args)
    with pytest.raises(ValueError):
        query_history(tmp_path, float('nan'), BASE)


def test_storage_pressure_failure_is_visible_and_recovery_breaks_trace(tmp_path, monkeypatch):
    config = settings(tmp_path)
    writer = HistoryWriter(config)
    import pendulum_pi.history as history
    real_usage = history.shutil.disk_usage
    monkeypatch.setattr(history.shutil, 'disk_usage', lambda _: type('Disk', (), {'free': 0})())
    writer.start()
    writer.submit(sample())
    writer.queue.join()
    assert writer.snapshot()['state'] == 'error'
    monkeypatch.setattr(history.shutil, 'disk_usage', real_usage)
    writer.submit(sample(1))
    writer.close()
    point = query_history(config.data_dir, BASE - 1, BASE + 10)['points'][0]
    assert 'history_write_failure' in point['boundary']
    assert writer.snapshot()['state'] == 'recording'


def test_reduced_rate_extrema_survive_target_change_with_constant_period(tmp_path):
    config = settings(tmp_path, target_period_s=2.)
    writer, connection = open_writer(config)
    for second in range(200):
        chosen = replace(config, target_period_s=2.002) if second == 110 else config
        value = sample(second)
        value['display']['window']['gain_seconds_per_day'] = 86.4 if second == 110 else 0.
        writer._write(connection, value, chosen)
    connection.close()
    for end in (BASE + 201, BASE + 4000):
        points = query_history(config.data_dir, BASE - 1, end, max_points=32,
                               series=['window_rate_s_day'])['points']
        assert max(point['window_rate_s_day'] for point in points) == pytest.approx(86.4)


def test_ten_second_cadence_keeps_immediate_validity_transitions(tmp_path):
    config = settings(tmp_path)
    writer, connection = open_writer(config)
    writer.sample_interval_seconds = 10
    for i in range(21):
        value = sample(i)
        if i >= 5:
            value['display']['timebase'] = 'NOMINAL'
        writer._write(connection, value, config)
    connection.close()
    points = query_history(config.data_dir, BASE - 1, BASE + 22)['points']
    assert [point['time'] - BASE for point in points] == [0, 5, 15]
    assert 'timebase_changed' in points[1]['boundary']


def test_retention_runs_during_prolonged_pause(tmp_path):
    config = settings(tmp_path, history_retention_days=1)
    writer, connection = open_writer(config)
    writer._write(connection, sample(), config)
    for second in (1, 86402):
        paused = sample(second)
        paused['logging']['enabled'] = False
        writer._write(connection, paused, config)
    assert connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 0
    connection.close()


def test_size_pressure_prunes_sidecar_and_never_reuses_observation_ids(tmp_path):
    config = settings(tmp_path, history_max_mb=8)
    writer, connection = open_writer(config)
    raw = config.data_dir / 'capture.csv'
    raw.write_text('raw captures')
    peak = 0
    for i in range(4500):
        value = sample(i)
        value['latest']['CSW'].update({f'edge{n}_tcb0': 123456789 + n for n in range(5)})
        value['time_health'] = {'status': 'synchronized', 'source': 'NTP', 'fresh': True,
                                'selected_source': '192.0.2.1', 'reference_age_seconds': 1.2,
                                'estimated_error_seconds': 0.00001, 'sample_age_seconds': .1}
        writer._write(connection, value, config)
        peak = max(peak, writer.path.stat().st_size)
    assert peak <= 8 * 1024**2
    assert connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0] < 4500
    last_id = connection.execute('SELECT MAX(id) FROM observations').fetchone()[0]
    with connection:
        connection.execute('DELETE FROM observations')
    writer._write(connection, sample(5000), config)
    assert connection.execute('SELECT MIN(id) FROM observations').fetchone()[0] > last_id
    assert raw.read_text() == 'raw captures'
    connection.close()


def test_session_origin_is_stable_across_range_and_retention(tmp_path):
    config = settings(tmp_path, history_retention_days=1)
    writer, connection = open_writer(config)
    writer._write(connection, sample(), config)
    writer._write(connection, sample(10), config)
    result = query_history(config.data_dir, BASE + 9, BASE + 11)
    assert result['sessions'][0]['start'] == BASE
    writer._write(connection, sample(86401), config)
    result = query_history(config.data_dir, BASE + 86400, BASE + 86402)
    assert result['sessions'][0]['start'] == BASE
    assert result['sessions'][0]['retained_start'] == BASE + 10
    connection.close()


def test_holdover_quality_survives_retention_without_estimator_reset(tmp_path):
    config = settings(tmp_path, target_period_s=2.)
    writer, connection = open_writer(config)
    writer._write(connection, sample(), config)
    held = sample(1)
    held['display']['timebase'] = 'HOLDOVER'
    held['display']['pps'] = {'calibration_age_seconds': 91., 'holdover_limit_seconds': 180}
    writer._write(connection, held, config)
    connection.close()
    points = query_history(config.data_dir, BASE - 1, BASE + 10)['points']
    assert len(points) == 2
    assert points[1]['quality']['timebase'] == 'HOLDOVER'
    assert points[1]['quality']['calibration_age_seconds'] == 91
    assert points[1]['settings']['pps_holdover_seconds'] == 180
    assert points[1]['window_period_s'] == points[0]['window_period_s']
    assert points[1]['window_rate_s_day'] == points[0]['window_rate_s_day']
    assert not points[1]['gap_reason']


def test_estimator_identity_transition_preserves_old_semantics(tmp_path):
    config = settings(tmp_path, target_period_s=2.)
    writer, connection = open_writer(config)
    old = sample()
    old['display']['window']['model'] = 'legacy_swing_mean_divided_by_pps_mean'
    writer._write(connection, old, config)
    writer._write(connection, sample(1), config)
    connection.close()
    points = query_history(config.data_dir, BASE - 1, BASE + 2)['points']
    assert points[0]['settings']['estimator_model'] == 'legacy_swing_mean_divided_by_pps_mean'
    assert 'estimator_changed' in points[1]['boundary']
    assert points[0]['segment'] != points[1]['segment']
    assert points[1]['settings']['window_seconds'] == 600


def test_chrony_timeout_is_persisted_without_splitting_measurements(tmp_path):
    config = settings(tmp_path)
    writer = HistoryWriter(config)
    config.data_dir.mkdir(parents=True)
    connection = _connect(writer.path)
    good = {'status': 'synchronized', 'source': 'GPS', 'fresh': True}
    for second, health in [(0, good), (1, {'status': 'unavailable', 'error': 'chronyc timed out'}), (2, good)]:
        writer._write(connection, sample(second, time_health=health), config)
    connection.close()
    points = query_history(config.data_dir, BASE - 1, BASE + 3)['points']
    assert len(points) == 3  # Quality transitions persist even between routine samples.
    assert len({p['segment'] for p in points}) == 1
    assert points[1]['boundary'] == points[2]['boundary'] == ['utc_quality_changed']
    assert all(p['window_period_s'] and p['temperature_C'] for p in points)
    assert points[0]['continuity'] == points[1]['continuity'] == points[2]['continuity']


def test_short_status_pause_stays_continuous_but_long_pause_and_capture_loss_break(tmp_path):
    config = settings(tmp_path)
    writer, connection = open_writer(config)
    for second in (0, 8, 40, 48):
        point = sample(second)
        if second == 48:
            point['counters'] = {'swing_missing': 1}
        writer._write(connection, point, config)
    connection.close()
    points = query_history(config.data_dir, BASE - 1, BASE + 49)['points']
    assert points[1]['segment'] == points[0]['segment']
    assert points[1]['boundary'] == ['observation_delay']
    assert points[2]['segment'] != points[1]['segment']
    assert 'observation_gap' in points[2]['boundary']
    assert points[3]['continuity']['timing'] != points[2]['continuity']['timing']
    assert 'capture_loss' in points[3]['boundary']


def test_trace_continuity_is_independent_of_unrelated_sensor_and_timing_changes(tmp_path):
    config = settings(tmp_path)
    writer, connection = open_writer(config)
    writer._write(connection, sample(), config)
    sensor = sample(1)
    sensor['environment']['bmp280']['fresh'] = False
    writer._write(connection, sensor, config)
    stale = sample(2)
    stale['environment']['bmp280']['fresh'] = False
    stale['display']['stale'] = True
    writer._write(connection, stale, config)
    connection.close()
    a, b, c = query_history(config.data_dir, BASE - 1, BASE + 3)['points']
    assert a['segment'] != b['segment']  # Regression still requires complete paired continuity.
    assert a['continuity']['timing'] == b['continuity']['timing']
    assert a['continuity']['sht4x'] == b['continuity']['sht4x'] == c['continuity']['sht4x']
    assert a['continuity']['bmp280'] != b['continuity']['bmp280']
    assert b['continuity']['timing'] != c['continuity']['timing']
    assert c['temperature_C'] == 21 and c['window_period_s'] is None
