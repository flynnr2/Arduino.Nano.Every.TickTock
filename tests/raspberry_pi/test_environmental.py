"""Independent fits, robust covariance, window alignment and continuity checks."""
import json
import math
import random
import tracemalloc

import pytest

from pendulum_pi.environmental import environmental_fit, query_environmental_relationships, EnvironmentalQuery, VARIABLES
from pendulum_pi.history import _connect, DATABASE

BASE = 1800000000


def points(count=480):
    rng = random.Random(7021)
    result = []
    error = 0.
    for i in range(count):
        t = 20+math.sin(i/61)+i/1800
        h = 50+3*math.cos(i/38)+math.sin(i/13)
        p = 1000+2*math.sin(i/24)+i/160
        error = .83*error+rng.gauss(0, .2)
        result.append(dict(time=BASE+60*i, bucket_start=BASE+60*i, samples=6,
            temperature_C=t, humidity_pct=h, pressure_hPa=p,
            period_s=2+(4*(t-20)-.7*(h-50)+1.2*(p-1000)+error)*1e-6))
    return result


def test_joint_fit_and_hac_against_independent_numpy_reference():
    # NumPy is an analysis/test dependency; the Pi implementation needs none.
    np = pytest.importorskip('numpy')
    sample = points()
    individual, fit = environmental_fit(sample, bucket_seconds=60)
    x = np.array([[p[k] for k in VARIABLES] for p in sample])
    x -= x.mean(axis=0)
    design = np.column_stack((np.ones(len(x)), x))
    y = np.array([p['period_s'] for p in sample])
    y = (y-y.mean())*1e6
    beta = np.linalg.lstsq(design, y, rcond=None)[0]
    residual = y-design@beta
    bread = np.linalg.inv(design.T@design)
    scores = design*residual[:, None]
    meat = scores.T@scores
    for lag in range(1, 11):
        cross = scores[lag:].T@scores[:-lag]
        meat += (1-lag/11)*(cross+cross.T)
    covariance = bread@meat@bread*len(x)/(len(x)-4)
    assert fit['coefficients_available'] and fit['uncertainty']['available']
    for j, key in enumerate(VARIABLES):
        coefficient = fit['coefficients'][key]
        assert coefficient['slope_us_per_unit'] == pytest.approx(beta[j+1], abs=1e-9)
        assert coefficient['se_us_per_unit'] == pytest.approx(math.sqrt(covariance[j+1,j+1]), rel=1e-8)
    assert fit['residual_rms_us'] == pytest.approx(math.sqrt(np.mean(residual**2)), rel=1e-8)
    assert fit['r_squared'] > individual['temperature_C']['r_squared']
    assert fit['residual_reduction_pct'] > 80
    for p, e in zip(sample, residual):
        assert p['residual_us'] == pytest.approx(e, abs=1e-8)
        assert (p['period_s']-p['fitted_period_s'])*1e6 == pytest.approx(e, abs=1e-8)
    json.dumps(fit, allow_nan=False)


def test_exact_known_slopes_without_numpy():
    sample = points(120)
    for p in sample:
        p['period_s'] = 2+(4*p['temperature_C']-.7*p['humidity_pct']+1.2*p['pressure_hPa'])*1e-6
    _, fit = environmental_fit(sample, bucket_seconds=60)
    assert [fit['coefficients'][k]['slope_us_per_unit'] for k in VARIABLES] == pytest.approx([4,-.7,1.2], abs=1e-8)
    assert fit['r_squared'] == pytest.approx(1)
    assert not fit['uncertainty']['available']  # Exact line cannot estimate noise.


@pytest.mark.parametrize('kind', ['collinear', 'near_collinear', 'constant', 'all_constant'])
def test_unidentifiable_slopes_withheld_but_fitted_history_retained(kind):
    sample = points()
    for i, p in enumerate(sample):
        if kind in ('collinear', 'near_collinear'):
            p['humidity_pct'] = p['temperature_C']*2+(1e-4*math.sin(i) if kind=='near_collinear' else 0)
        elif kind == 'constant':
            p['pressure_hPa'] = 1000.
        else:
            for k in VARIABLES:
                p[k] = 1.
    _, fit = environmental_fit(sample, bucket_seconds=60)
    assert fit['available'] and not fit['coefficients_available']
    assert not fit['uncertainty']['available']
    assert 'cannot be separated' in fit['reason']
    assert all(c['slope_us_per_unit'] is None for c in fit['coefficients'].values())
    assert all(math.isfinite(p['fitted_period_s']) for p in sample)
    assert fit['predictor_rank'] < 3 or fit['condition_number'] > fit['condition_limit']
    json.dumps(fit, allow_nan=False)


def test_uncertainty_requires_continuous_sufficient_history_and_variable_response():
    sample = points()[::12]
    for i, p in enumerate(sample):
        p['bucket_start'] = BASE+i*60
    _, fit = environmental_fit(sample, bucket_seconds=60)
    assert fit['coefficients_available'] and not fit['uncertainty']['available']
    assert fit['uncertainty']['minimum_points'] == 55
    sample = points()
    del sample[100]
    _, fit = environmental_fit(sample, bucket_seconds=60)
    assert not fit['uncertainty']['available']
    assert 'missing or irregular' in fit['uncertainty']['reason']
    for p in sample:
        p['period_s'] = 2.
    _, fit = environmental_fit(sample, bucket_seconds=60)
    assert fit['r_squared'] is None and fit['adjusted_r_squared'] is None
    assert fit['residual_rms_us'] == 0
    assert not fit['uncertainty']['available']
    assert not environmental_fit(sample[:4], bucket_seconds=60)[1]['available']


def add(connection, second, *, segment='a', session='s', temperature=None, humidity=50., pressure=1000., learning=False, gap=None):
    payload = dict(quality=dict(window_learning=learning,timebase='PPS'),gap_reason=gap,settings=dict(window_seconds=600))
    connection.execute('''INSERT INTO observations
        (time,session,segment,source,payload,temperature_C,humidity_pct,pressure_hPa,window_period_s)
        VALUES (?,?,?,?,?,?,?,?,?)''', (BASE+second,session,segment,'demo',json.dumps(payload),
        second if temperature is None else temperature,humidity,pressure,2+second*1e-8))


def test_trailing_environmental_alignment_complete_cases_and_no_future_readings(tmp_path):
    connection = _connect(tmp_path/DATABASE)
    for second in range(0, 1201, 10):
        add(connection, second, learning=second<600)
    connection.commit();connection.close()
    # 600..650: a trailing mean of a ramp is t-300; paired period is current t.
    result = query_environmental_relationships(tmp_path, BASE+600, BASE+659)
    group = result['segments'][0]
    assert len(group['points']) == 1
    p = group['points'][0]
    assert p['samples'] == 6
    assert p['temperature_C'] == 325
    assert p['period_s'] == pytest.approx(2+625*1e-8)
    assert p['time'] == BASE+625
    # Future observations at 660+ must never affect this fit; warm-up readings
    # before selected range are required, even while period was learning.
    assert result['environmental_window_seconds'] == 600


@pytest.mark.parametrize('fault', ['missing', 'null', 'gap', 'boundary'])
def test_invalid_history_breaks_windows_and_no_bridging(tmp_path, fault):
    connection = _connect(tmp_path/DATABASE)
    for second in range(0, 2401, 10):
        if fault=='missing' and 900 <= second <= 960:
            continue
        add(connection, second, segment='b' if fault=='boundary' and second>=900 else 'a',
            pressure=None if fault=='null' and second==900 else 1000.,
            gap='recording_disabled' if fault=='gap' and second==900 else None)
    connection.commit();connection.close()
    result = query_environmental_relationships(tmp_path, BASE+600, BASE+2400)
    all_points = [p for group in result['segments'] for p in group['points']]
    # Every observation affected by the fault is excluded for the next 600s.
    # Buckets partly outside that span keep only their eligible observations.
    assert not any(BASE+980 <= p['time'] <= BASE+1440 for p in all_points)
    assert any(p['time'] > BASE+1800 for p in all_points)
    if fault=='boundary':
        assert len(result['segments']) == 2
        assert result['segments'][1]['start'] >= BASE+1470
    else:
        assert not result['segments'][0]['joint']['uncertainty']['available']


def test_all_fits_share_paired_points_and_sessions_are_separate(tmp_path):
    connection = _connect(tmp_path/DATABASE)
    for session in ('s','other'):
        for second in range(0, 4201, 10):
            add(connection, second, session=session, humidity=50+math.sin(second/80), pressure=1000+math.cos(second/110))
    connection.commit();connection.close()
    result = query_environmental_relationships(tmp_path, BASE+600, BASE+4200, session='s')
    assert len(result['segments']) == 1
    group = result['segments'][0]
    assert group['samples'] == sum(p['samples'] for p in group['points'])
    assert len(group['individual']) == 3 and group['joint']['available']
    assert group['joint']['uncertainty']['minimum_points'] == 55
    assert len(query_environmental_relationships(tmp_path, BASE+600, BASE+4200)['segments']) == 2
    json.dumps(result, allow_nan=False)


def test_query_validation_missing_store_and_bounds(tmp_path, monkeypatch):
    assert query_environmental_relationships(tmp_path, BASE, BASE+60)['segments'] == []
    for start, end in [(float('nan'),BASE), (BASE,float('inf')),(-1,1),(2,1),(0,367*86400)]:
        with pytest.raises(ValueError):
            query_environmental_relationships(tmp_path,start,end)
    with pytest.raises(ValueError):
        query_environmental_relationships(tmp_path,BASE,BASE+60,session='../bad')
    connection=_connect(tmp_path/DATABASE)
    for second in range(0,1001,10):
        add(connection,second)
    connection.commit();connection.close()
    monkeypatch.setattr('pendulum_pi.environmental.MAX_AVERAGES',2)
    with pytest.raises(ValueError,match='shorter range'):
        query_environmental_relationships(tmp_path,BASE+600,BASE+1000)


def test_equal_time_peers_share_the_same_trailing_environment(tmp_path):
    connection = _connect(tmp_path / DATABASE)
    for second in range(0, 601, 10):
        add(connection, second, temperature=10.)
    add(connection, 600, temperature=40.)
    connection.commit(); connection.close()
    point = query_environmental_relationships(tmp_path, BASE+600, BASE+601)['segments'][0]['points'][0]
    assert point['samples'] == 2
    assert point['temperature_C'] == pytest.approx((61*10+40)/62)


def test_streaming_windows_match_independent_complete_case_reference(tmp_path):
    connection = _connect(tmp_path / DATABASE)
    raw = []
    for second in range(0, 3601, 10):
        for session in ('s', 'other'):
            if session == 's' and 900 <= second <= 960:
                continue
            segment = 'b' if 1200 <= second < 2400 else 'a'
            for duplicate in range(2 if second in (600, 1800) else 1):
                t = 20 + math.sin(second/90) + duplicate
                h = 50 + math.cos(second/70)
                p = None if second == 2700 else 1000 + math.sin(second/110)
                gap = 'test_gap' if second == 3000 else None
                learning = second < 600 or second == 800
                add(connection, second, session=session, segment=segment, temperature=t,
                    humidity=h, pressure=p, gap=gap, learning=learning)
                raw.append(dict(time=BASE+second, key=(session, segment), values=(t, h, p),
                                invalid=p is None or gap is not None, learning=learning,
                                period=2+second*1e-8))
    connection.commit(); connection.close()
    # Deliberately rescan each window rather than sharing the streaming sums.
    # Include equal-time peers and the spacing preceding the oldest window row.
    previous = {}
    for row in raw:
        row['spacing'] = row['time'] - previous.get(row['key'], row['time'])
        previous[row['key']] = row['time']
    expected = {}
    start, end = BASE+600, BASE+3600
    result = query_environmental_relationships(tmp_path, start, end)
    width = result['bucket_seconds']
    for row in raw:
        if row['time'] < start or row['learning']:
            continue
        window = [r for r in raw if r['key'] == row['key']
                  and row['time']-600 <= r['time'] <= row['time']]
        if (row['time']-window[0]['time'] < 570 or any(r['invalid'] for r in window)
                or max(r['spacing'] for r in window) > 30):
            continue
        values = [row['time'], *(math.fsum(r['values'][j] for r in window)/len(window)
                                for j in range(3)), row['period']]
        identity = (*row['key'], int(row['time']/width)*width)
        expected.setdefault(identity, []).append(values)
    actual = { (group['session'], group['segment'], p['bucket_start']): p
              for group in result['segments'] for p in group['points'] }
    assert actual.keys() == expected.keys()
    for key, values in expected.items():
        assert actual[key]['samples'] == len(values)
        for j, name in enumerate(('time', *VARIABLES, 'period_s')):
            assert actual[key][name] == pytest.approx(math.fsum(v[j] for v in values)/len(values), abs=1e-8)


def test_seven_days_complete_across_budgeted_steps_without_loading_raw_rows(tmp_path, monkeypatch):
    connection = _connect(tmp_path / DATABASE)
    payload = json.dumps(dict(quality=dict(window_learning=False, timebase='PPS'), settings=dict(window_seconds=600)))
    count = 7 * 8640
    with connection:
        connection.executemany('''INSERT INTO observations
            (time,session,segment,source,payload,temperature_C,humidity_pct,pressure_hPa,window_period_s)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            ((BASE+i*10,'s','a','demo',payload,20+i*.00001,50+math.sin(i/200),1000+math.cos(i/300),2+i*1e-10)
             for i in range(count)))
    connection.close()
    # A long job must survive more than four seconds of total wall time while
    # returning control regularly so the views worker can publish its health.
    clock = [0.]
    monkeypatch.setattr('pendulum_pi.environmental.time.monotonic', lambda: clock[0])
    job = EnvironmentalQuery(tmp_path, BASE, BASE+7*86400)
    tracemalloc.start()
    steps = 0
    try:
        while True:
            before = job.processed_records
            done = job.step()
            assert job.processed_records - before <= 512
            steps += 1
            clock[0] += 5
            if done:
                break
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        job.close()
    assert steps > 100 and job.processed_records == count
    assert peak < 8 * 1024**2
    group = job.result['segments'][0]
    assert group['start'] == BASE+570
    assert group['end'] == BASE+(count-1)*10
    assert sum(p['samples'] for p in group['points']) == count-57
    assert len(group['points']) <= 600
