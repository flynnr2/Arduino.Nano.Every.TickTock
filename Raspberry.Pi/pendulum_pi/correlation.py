"""Temperature association from paired history averages, never chart extrema."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sqlite3
import time

from .history import DATABASE, SCHEMA_VERSION, _number

MAX_AVERAGES = 2000
# Minimum-history policy, not a claim of finite-sample 95% coverage. The lag
# floor follows the actual mean window (historical EWMA reads use recorded
# half-lives); slower unmodelled drift and calibration dependence remain possible.
SMOOTHING_HALF_LIVES = 5
MIN_HAC_WINDOWS = 5
MIN_HAC_POINTS = 30
NORMAL_95 = 1.959963984540054


def slope_uncertainty(points, dx, residual_us, xx, slope_us, *, bucket_seconds,
                      half_life_minutes=None, smoothing_window_seconds=None, parameter_count=2):
    """Bartlett Newey-West slope covariance, with n/(n-k) correction.

    For centred OLS with an intercept the slope's influence is x_i*e_i/Sxx.
    HAC sums lagged cross-products of these influences, not just residual ACF.
    Normal-reference CI/p are asymptotic, conditional on a stable linear model.
    See statsmodels' cov_hac reference linked in DATA_FORMAT.md.
    """
    result = dict(available=False, method='Newey-West HAC (Bartlett)',
                  reference='asymptotic normal', confidence_level=.95,
                  half_life_minutes=_number(half_life_minutes),
                  smoothing_window_seconds=_number(smoothing_window_seconds),
                  lag_buckets=None, window_seconds=None, minimum_points=None,
                  slope_se_us_per_C=None, slope_ci95_us_per_C=None, p_value=None)

    def unavailable(reason):
        return dict(result, reason=reason)

    dependence_seconds = (_number(smoothing_window_seconds) if smoothing_window_seconds is not None
                          else SMOOTHING_HALF_LIVES * half_life_minutes * 60
                          if _number(half_life_minutes) is not None else None)
    if (_number(bucket_seconds) is None or bucket_seconds <= 0
            or dependence_seconds is None or dependence_seconds <= 0):
        return unavailable('Recorded smoothing or time-bucket information is unavailable.')
    count = len(points)
    automatic_lags = math.floor(4 * (count / 100) ** (2 / 9))
    lags = max(automatic_lags, math.ceil(dependence_seconds / bucket_seconds))
    required = max(MIN_HAC_POINTS, MIN_HAC_WINDOWS * (lags + 1))
    result.update(lag_buckets=lags, window_seconds=lags * bucket_seconds,
                  minimum_points=required)
    # Use epoch-aligned bucket identities, not rounded average sample times.
    # Omitting a bucket must not compress time for the HAC lag calculation.
    starts = [p.get('bucket_start') for p in points]
    if (any(_number(value) is None for value in starts)
            or any(not math.isclose(b-a, bucket_seconds, rel_tol=0, abs_tol=1e-6)
                   for a, b in zip(starts, starts[1:]))):
        return unavailable('Time buckets are missing or irregular; uncertainty is withheld. Select a continuous range.')
    if count < required:
        return unavailable(f'Insufficient history for uncertainty: {count} paired averages; '
                           f'at least {required} consecutive averages are required for this smoothing window.')
    # A numerically exact/constant line supplies no usable residual variation
    # for estimating uncertainty; never turn it into a zero-width interval/p=0.
    rms = math.sqrt(math.fsum(e*e for e in residual_us) / count)
    resolution = max(math.ulp(p['period_s']) for p in points) * 1e6
    if rms <= 32 * resolution:
        return unavailable('No resolvable residual variation; uncertainty cannot be estimated.')
    scores = [x*e/xx for x, e in zip(dx, residual_us)]
    terms = [math.fsum(score*score for score in scores)]
    for lag in range(1, lags + 1):
        cross = math.fsum(scores[i]*scores[i-lag] for i in range(lag, count))
        terms.append(2 * (1 - lag / (lags + 1)) * cross)
    variance = math.fsum(terms) * count / (count - parameter_count)
    if not math.isfinite(variance) or variance <= 0:
        return unavailable('The slope uncertainty could not be resolved from this segment.')
    se = math.sqrt(variance)
    return dict(result, available=True, reason=None, slope_se_us_per_C=se,
                slope_ci95_us_per_C=[slope_us - NORMAL_95*se, slope_us + NORMAL_95*se],
                p_value=math.erfc(abs(slope_us/se) / math.sqrt(2)))


def linear_fit(points, *, bucket_seconds=None, half_life_minutes=None, smoothing_window_seconds=None):
    """Equal-weight OLS with an intercept, centred to preserve period precision."""
    if len(points) < 3:
        return {'available': False, 'reason': 'At least three paired averages are needed.'}
    count = len(points)
    mean_x = math.fsum(p['temperature_C'] for p in points) / count
    mean_y = math.fsum(p['period_s'] for p in points) / count
    dx = [p['temperature_C'] - mean_x for p in points]
    dy = [p['period_s'] - mean_y for p in points]
    xx = math.fsum(x*x for x in dx)
    yy = math.fsum(y*y for y in dy)
    if max(p['temperature_C'] for p in points) == min(p['temperature_C'] for p in points):
        return {'available': False, 'reason': 'No temperature variation in these averages.'}
    xy = math.fsum(x*y for x, y in zip(dx, dy))
    constant_period = max(p['period_s'] for p in points) == min(p['period_s'] for p in points)
    slope = 0. if constant_period else xy / xx
    residual_us = [(y - slope*x)*1e6 for x, y in zip(dx, dy)]
    # R² is undefined for a constant response; a horizontal fitted line is valid.
    return {'available': True, 'slope_us_per_C': slope * 1e6,
            'mean_temperature_C': mean_x, 'mean_period_s': mean_y,
            'residual_rms_us': math.sqrt(math.fsum(e*e for e in residual_us) / count),
            'r_squared': None if constant_period else max(0., min(1., xy*xy / (xx*yy))),
            'uncertainty': slope_uncertainty(
                points, dx, residual_us, xx, slope*1e6, bucket_seconds=bucket_seconds,
                half_life_minutes=half_life_minutes, smoothing_window_seconds=smoothing_window_seconds),
            'reason': 'R² is undefined when period does not vary.' if constant_period else None}


def query_temperature_correlation(data_dir, start, end, *, session=None, estimate='window'):
    """Read all eligible observations into fixed-width, paired means per segment.

    Every occupied bucket gets one equally weighted point. Empty buckets are
    omitted, never interpolated. The selected horizon must have left learning;
    stale temperatures and periods are already null in the history store.
    """
    if (any(_number(value) is None for value in (start, end))
            or not 0 <= start < end or end - start > 366 * 86400):
        raise ValueError('Use a finite range up to 366 days with start before end')
    if session is not None and (not isinstance(session, str)
                               or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', session)):
        raise ValueError('Invalid history session')
    if estimate not in ('window', 'short', 'long', 'blended'):
        raise ValueError('Choose window, short, long or blended estimate')
    # About 600 bins per uninterrupted range; minute-aligned widths do not shift
    # with each browser refresh. Partial edge bins are labelled by sample count.
    width = max(60, math.ceil((end-start) / 600 / 60) * 60)
    result = dict(start=start, end=end, estimate=estimate, bucket_seconds=width,
                  segments=[], tracking_only=True)
    path = Path(data_dir) / DATABASE
    if not path.exists():
        return result
    horizons = ('short', 'long') if estimate == 'blended' else (estimate,)
    period = '(0.75*short_period_s + 0.25*long_period_s)' if estimate == 'blended' else f'{estimate}_period_s'
    conditions = ['time >= ?', 'time <= ?', 'temperature_C IS NOT NULL',
                  "json_extract(payload, '$.gap_reason') IS NULL"]
    for horizon in horizons:
        conditions += [f'{horizon}_period_s > 0',
                       f"json_extract(payload, '$.quality.{horizon}_learning') = 0"]
    parameters = [start, end]
    if session is not None:
        conditions.append('session = ?')
        parameters.append(session)
    connection = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=.2)
    connection.row_factory = sqlite3.Row
    deadline = time.monotonic() + 4
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
    try:
        if connection.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('Unsupported history schema')
        # Group by continuity identity as well as bucket. Nothing is pooled
        # across timebase, source, settings, reset or freshness transitions.
        rows = connection.execute(f'''
            SELECT session,segment,source,CAST(time / ? AS INTEGER) AS bucket,
                   MIN(time) AS start,MAX(time) AS end,AVG(time) AS time,
                   AVG(temperature_C) AS temperature_C,AVG({period}) AS period_s,
                   COUNT(*) AS samples,
                   json_extract(payload, '$.quality.timebase') AS timebase,
                   json_extract(payload, '$.settings') AS settings
            FROM observations WHERE {' AND '.join(conditions)}
            GROUP BY session,segment,bucket ORDER BY start LIMIT ?
        ''', [width, *parameters, MAX_AVERAGES + 1]).fetchall()
        if len(rows) > MAX_AVERAGES:
            raise ValueError('Too many measurement segments; choose a shorter range or one session')
        groups = {}
        for row in rows:
            if any(_number(row[key]) is None for key in ('temperature_C', 'period_s')):
                continue
            key = (row['session'], row['segment'])
            group = groups.setdefault(key, dict(
                session=row['session'], segment=row['segment'], source=row['source'],
                timebase=row['timebase'], settings=json.loads(row['settings'] or '{}'),
                start=row['start'], end=row['end'], samples=0, points=[]))
            group['end'] = max(group['end'], row['end'])
            group['samples'] += row['samples']
            group['points'].append({name: row[name] for name in
                                    ('time', 'temperature_C', 'period_s', 'samples')})
            group['points'][-1]['bucket_start'] = row['bucket'] * width
        for group in groups.values():
            half_lives = [group['settings'].get(f'{horizon}_minutes') for horizon in horizons]
            half_life = (max(half_lives) if all(_number(value) is not None and value > 0
                                              for value in half_lives) else None)
            group['fit'] = linear_fit(group['points'], bucket_seconds=width,
                                      half_life_minutes=half_life,
                                      smoothing_window_seconds=600 if estimate == 'window' else None)
            temperatures = [p['temperature_C'] for p in group['points']]
            group['temperature_min_C'] = min(temperatures)
            group['temperature_max_C'] = max(temperatures)
        result['segments'] = list(groups.values())
        return result
    finally:
        connection.close()
