"""Fits use paired time averages, excluding chart selection bias and boundaries."""
import json
import math
import random

import pytest

from pendulum_pi.correlation import linear_fit, query_temperature_correlation
from pendulum_pi.history import _connect, DATABASE

BASE = 1800000000


def add(connection, second, temperature=20, short=2, long=2, *, window=None, segment='a',
        session='s', learning=False, long_learning=False, gap=None):
    payload = {'quality': {'short_learning': learning, 'long_learning': long_learning,
                           'window_learning': learning, 'timebase': 'PPS'}, 'gap_reason': gap,
               'settings': {'short_minutes': 5, 'long_minutes': 60, 'target_period_s': 2, 'window_seconds': 600, 'estimator_model': 'swing_mean_600s_pps_dual_ewma_v1'}}
    connection.execute('''INSERT INTO observations
        (time,session,segment,source,payload,temperature_C,short_period_s,long_period_s,window_period_s)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (BASE+second, session, segment, 'demo', json.dumps(payload), temperature, short, long, window))
    connection.commit()


def query(path, **kwargs):
    return query_temperature_correlation(path, BASE, BASE+600, **kwargs)


@pytest.mark.parametrize('slope', [4, -4])
def test_centred_fit_resolves_microseconds_and_direction(slope):
    points = [{'temperature_C': x, 'period_s': 2 + slope*(x-20)/1e6} for x in (18, 19, 20, 21)]
    fit = linear_fit(points)
    assert fit['available']
    assert fit['slope_us_per_C'] == pytest.approx(slope)
    assert fit['r_squared'] == pytest.approx(1)
    # Non-linear residuals must reduce R².
    points[1]['period_s'] += .000020
    assert 0 <= linear_fit(points)['r_squared'] < 1


def test_no_fit_for_insufficient_or_constant_temperature_and_no_r2_for_flat_period():
    assert not linear_fit([])['available']
    assert not linear_fit([{'temperature_C': 20, 'period_s': 2}]*3)['available']
    fit = linear_fit([{'temperature_C': x, 'period_s': 2} for x in (19, 20, 21)])
    assert fit['available'] and fit['slope_us_per_C'] == 0
    assert fit['r_squared'] is None
    assert 'does not vary' in fit['reason']


def test_known_imperfect_fit():
    fit = linear_fit([{'temperature_C': x, 'period_s': y}
                      for x, y in [(19, 2), (20, 2.000001), (21, 2.000001)]])
    assert fit['slope_us_per_C'] == pytest.approx(.5)
    assert fit['r_squared'] == pytest.approx(.75)
    assert fit['residual_rms_us'] == pytest.approx(math.sqrt(1/18))


def correlated_points(count=480, slope=4):
    rng = random.Random(20260928)
    error = 0
    points = []
    for i in range(count):
        temperature = 20 + math.sin(i/63) + i/1000
        error = .94*error + rng.gauss(0, .2)
        points.append({'temperature_C': temperature,
                       'period_s': 2 + (slope*(temperature-20) + error)*1e-6,
                       'bucket_start': BASE+i*60})
    return points


def uncertainty_fit(points, **kwargs):
    return linear_fit(points, bucket_seconds=kwargs.get('bucket_seconds', 60),
                      half_life_minutes=kwargs.get('half_life_minutes', 5))


def test_hac_matches_independent_matrix_reference_and_exceeds_iid_error():
    fit = uncertainty_fit(correlated_points())
    uncertainty = fit['uncertainty']
    assert uncertainty['available']
    # Independent NumPy reference: X=[1, centred temperature], K_ij=max(0,
    # 1-|i-j|/26), V=(X'X)^-1 (Xe)'K(Xe) (X'X)^-1 * 480/478.
    # Golden values avoid a scientific-library dependency on the Pi/test suite.
    assert fit['slope_us_per_C'] == pytest.approx(4.282617579363953, abs=1e-8)
    assert fit['residual_rms_us'] == pytest.approx(.4614725471)
    assert uncertainty['slope_se_us_per_C'] == pytest.approx(.10097865090603717, abs=1e-8)
    assert uncertainty['slope_se_us_per_C'] > 3*.0321339027464101  # Naive IID standard error.
    assert uncertainty['lag_buckets'] == 25
    assert uncertainty['slope_ci95_us_per_C'] == pytest.approx([4.08470306, 4.48053210])
    assert uncertainty['p_value'] < .001


def test_more_copies_of_smoothed_readings_do_not_create_independent_information():
    points = correlated_points()
    doubled = [{**point, 'bucket_start': point['bucket_start']+offset}
               for point in points for offset in (0, 30)]
    original = uncertainty_fit(points)['uncertainty']['slope_se_us_per_C']
    dense = uncertainty_fit(doubled, bucket_seconds=30)['uncertainty']
    assert dense['lag_buckets'] == 50
    assert dense['slope_se_us_per_C'] == pytest.approx(original, rel=.02)


def test_two_sided_p_value_and_interval_crossing_zero():
    fit = uncertainty_fit(correlated_points(slope=-.2))
    uncertainty = fit['uncertainty']
    low, high = uncertainty['slope_ci95_us_per_C']
    assert low < 0 < high
    assert .4 < uncertainty['p_value'] < .5
    reflected = [{**p, 'period_s': 4-p['period_s']} for p in correlated_points(slope=-.2)]
    other = uncertainty_fit(reflected)['uncertainty']
    assert other['p_value'] == pytest.approx(uncertainty['p_value'])


def test_uncertainty_requires_smoothing_history_and_keeps_missing_time():
    points = correlated_points()
    short = uncertainty_fit(points[:60])
    assert short['available']  # Descriptive fit remains available.
    assert not short['uncertainty']['available']
    assert short['uncertainty']['minimum_points'] == 130
    assert 'Insufficient history' in short['uncertainty']['reason']
    long = uncertainty_fit(points, half_life_minutes=60)['uncertainty']
    assert not long['available'] and long['lag_buckets'] == 300
    for irregular in (points[:200]+points[201:], list(reversed(points)),
                      [dict(point, bucket_start=BASE) for point in points]):
        result = uncertainty_fit(irregular)['uncertainty']
        assert not result['available']
        assert 'missing or irregular' in result['reason']
    for half_life in (None, 0, -1, math.nan, True):
        result = uncertainty_fit(points, half_life_minutes=half_life)['uncertainty']
        assert not result['available']
        json.dumps(result, allow_nan=False)


def test_exact_line_and_constant_period_never_claim_zero_uncertainty():
    for slope in (0, 4):
        points = [{**p, 'period_s': 2+slope*(p['temperature_C']-20)*1e-6}
                  for p in correlated_points()]
        fit = uncertainty_fit(points)
        assert fit['available']
        assert not fit['uncertainty']['available']
        assert 'No resolvable residual variation' in fit['uncertainty']['reason']
        assert fit['uncertainty']['p_value'] is None


def test_query_uses_recorded_half_lives_and_blend_uses_the_longer_one(tmp_path):
    connection = _connect(tmp_path/DATABASE)
    for i, point in enumerate(correlated_points()):
        add(connection, i*60, point['temperature_C'], point['period_s'], point['period_s'])
    connection.close()
    for estimate, half_life, available in [('short', 5, True), ('long', 60, False), ('blended', 60, False)]:
        result = query_temperature_correlation(tmp_path, BASE, BASE+480*60, estimate=estimate)
        group = result['segments'][0]
        assert group['points'][0]['bucket_start'] == BASE
        uncertainty = group['fit']['uncertainty']
        assert uncertainty['half_life_minutes'] == half_life
        assert uncertainty['available'] is available


def test_paired_averages_use_all_eligible_points_and_equal_bucket_weights(tmp_path):
    connection = _connect(tmp_path/DATABASE)
    # First bucket's paired mean is 20 C / 2 s. Null pairs and learning do not
    # affect either side of the mean; all eligible values (not just extremes) do.
    for second, temp, period in [(1, 18, 1.99998), (2, 20, 2), (3, 22, 2.00002),
                                 (61, 21, 2.00001), (121, 22, 2.00002)]:
        add(connection, second, temp, period)
    add(connection, 4, None, 10)
    add(connection, 5, 50, None)
    add(connection, 6, 50, 10, learning=True)
    add(connection, 7, 50, 10, gap='stale')
    connection.close()
    result = query(tmp_path, estimate='short')
    group = result['segments'][0]
    assert result['bucket_seconds'] == 60
    assert len(group['points']) == 3
    assert group['samples'] == 5
    assert group['points'][0]['temperature_C'] == 20
    assert group['points'][0]['period_s'] == pytest.approx(2)
    assert group['fit']['slope_us_per_C'] == pytest.approx(10)
    assert group['fit']['r_squared'] == pytest.approx(1)


def test_horizons_blend_learning_and_segment_session_separation(tmp_path):
    connection = _connect(tmp_path/DATABASE)
    for second, temp in [(1, 20), (61, 21), (121, 22)]:
        add(connection, second, temp, 2+(temp-20)*4e-6, 2+(temp-20)*8e-6)
    add(connection, 2, 50, 10, 10, long_learning=True)
    add(connection, 3, 20, segment='b')
    add(connection, 4, 20, session='other')
    connection.close()
    groups = query(tmp_path, estimate='blended')['segments']
    assert len(groups) == 3
    main = next(group for group in groups if group['session']=='s' and group['segment']=='a')
    assert main['fit']['slope_us_per_C'] == pytest.approx(5)
    assert main['samples'] == 3
    assert len(query(tmp_path, session='other', estimate='blended')['segments']) == 1
    assert query(tmp_path, session='missing')['segments'] == []
    short = query(tmp_path, estimate='short')['segments'][0]
    assert short['samples'] == 4  # Short can be tracking while long is learning.
    long = query(tmp_path, estimate='long')['segments'][0]
    assert long['fit']['slope_us_per_C'] == pytest.approx(8)


def test_empty_range_readonly_and_bounds(tmp_path):
    assert query(tmp_path)['segments'] == []
    assert not (tmp_path/DATABASE).exists()
    connection = _connect(tmp_path/DATABASE)
    add(connection, 0)
    add(connection, 60)
    connection.close()
    assert query_temperature_correlation(tmp_path, BASE+1, BASE+59)['segments'] == []
    for start, end in [(math.nan, BASE), (BASE, math.inf), (BASE, BASE), (-1, BASE), (0, 367*86400)]:
        with pytest.raises(ValueError):
            query_temperature_correlation(tmp_path, start, end)
    for kwargs in ({'estimate': 'unknown'}, {'session': '../private'}):
        with pytest.raises(ValueError):
            query(tmp_path, **kwargs)


def test_large_ranges_use_wider_bins_and_fragmentation_never_silently_truncates(tmp_path, monkeypatch):
    connection = _connect(tmp_path/DATABASE)
    for second in range(3):
        add(connection, second, window=2, segment=str(second))
    connection.close()
    result = query_temperature_correlation(tmp_path, BASE, BASE+7*86400)
    assert result['bucket_seconds'] == 1020
    monkeypatch.setattr('pendulum_pi.correlation.MAX_AVERAGES', 2)
    with pytest.raises(ValueError, match='shorter range'):
        query(tmp_path)


def test_window_default_excludes_old_ewma_and_uses_actual_mean_window(tmp_path):
    connection = _connect(tmp_path/DATABASE)
    for i, point in enumerate(correlated_points()):
        add(connection, i*60, point['temperature_C'], window=point['period_s'])
    add(connection, 1, 90, short=9, long=9)  # Old EWMAs have no mean value.
    add(connection, 2, 90, window=9, learning=True)
    connection.close()
    result = query_temperature_correlation(tmp_path, BASE, BASE+480*60)
    assert result['estimate'] == 'window'
    group = result['segments'][0]
    assert group['samples'] == 480
    uncertainty = group['fit']['uncertainty']
    assert uncertainty['smoothing_window_seconds'] == 600
    assert uncertainty['half_life_minutes'] is None
    assert uncertainty['lag_buckets'] == 10
    assert uncertainty['available']


def test_mean_window_uncertainty_keeps_time_gaps_and_density(tmp_path):
    points = correlated_points()
    fit = linear_fit(points, bucket_seconds=60, smoothing_window_seconds=600)
    dense = [{**point, 'bucket_start': point['bucket_start']+offset}
             for point in points for offset in (0, 30)]
    other = linear_fit(dense, bucket_seconds=30, smoothing_window_seconds=600)
    assert other['uncertainty']['lag_buckets'] == 20
    assert other['uncertainty']['slope_se_us_per_C'] == pytest.approx(fit['uncertainty']['slope_se_us_per_C'], rel=.03)
    broken = linear_fit(points[:200]+points[201:], bucket_seconds=60, smoothing_window_seconds=600)
    assert not broken['uncertainty']['available']
