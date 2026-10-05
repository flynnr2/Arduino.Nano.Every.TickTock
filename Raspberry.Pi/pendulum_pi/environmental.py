"""Aligned environmental associations, with no additional Pi dependencies."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sqlite3
import time

from .correlation import MAX_AVERAGES, linear_fit, slope_uncertainty
from .history import DATABASE, SCHEMA_VERSION, _number

VARIABLES = ('temperature_C', 'humidity_pct', 'pressure_hPa')
WINDOW = 600
COVERAGE_TOLERANCE = 30  # History normally samples every ~10 seconds.
CONDITION_LIMIT = 30.0  # Display policy for separating the adjusted slopes.


def _eigen(matrix):
    """Jacobi eigendecomposition of a symmetric 3×3 predictor correlation matrix."""
    a = [row[:] for row in matrix]
    v = [[float(i == j) for j in range(3)] for i in range(3)]
    for _ in range(40):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda ij: abs(a[ij[0]][ij[1]]))
        if abs(a[p][q]) < 1e-14:
            break
        angle = .5 * math.atan2(2*a[p][q], a[q][q]-a[p][p])
        c, s = math.cos(angle), math.sin(angle)
        app, aqq, apq = a[p][p], a[q][q], a[p][q]
        a[p][p] = c*c*app - 2*s*c*apq + s*s*aqq
        a[q][q] = s*s*app + 2*s*c*apq + c*c*aqq
        a[p][q] = a[q][p] = 0.
        for k in range(3):
            if k not in (p, q):
                akp, akq = a[k][p], a[k][q]
                a[k][p] = a[p][k] = c*akp-s*akq
                a[k][q] = a[q][k] = s*akp+c*akq
            vkp, vkq = v[k][p], v[k][q]
            v[k][p], v[k][q] = c*vkp-s*vkq, s*vkp+c*vkq
    return [a[i][i] for i in range(3)], v


def environmental_fit(points, *, bucket_seconds):
    """Centred OLS in microseconds; scale predictors before rank/conditioning checks.

    A truncated spectral inverse gives a unique fitted response even when slopes
    cannot be identified. In that case no individual adjusted slope is exposed.
    HAC uses the complete model's influence scores and n/(n-rank-1) correction.
    """
    individual = {}
    for name in VARIABLES:
        paired = [{**p, 'temperature_C': p[name]} for p in points]
        fit = linear_fit(paired, bucket_seconds=bucket_seconds, smoothing_window_seconds=WINDOW)
        if fit.get('available'):
            fit['slope_us_per_unit'] = fit.pop('slope_us_per_C')
            fit['mean_x'] = fit.pop('mean_temperature_C')
            uncertainty = fit['uncertainty']
            uncertainty['se_us_per_unit'] = uncertainty.pop('slope_se_us_per_C')
            uncertainty['ci95_us_per_unit'] = uncertainty.pop('slope_ci95_us_per_C')
        elif name != 'temperature_C':
            fit['reason'] = fit['reason'].replace('temperature', 'environmental reading')
        individual[name] = fit
    if len(points) < 5:
        return individual, dict(available=False, reason='At least five paired averages are needed for the combined model.')
    n = len(points)
    means = [math.fsum(p[name] for p in points)/n for name in VARIABLES]
    centred = [[p[name]-means[j] for j, name in enumerate(VARIABLES)] for p in points]
    scales = [math.sqrt(math.fsum(x[j]**2 for x in centred)/n) for j in range(3)]
    x = [[row[j]/scales[j] if scales[j] else 0. for j in range(3)] for row in centred]
    gram = [[math.fsum(row[j]*row[k] for row in x)/n for k in range(3)] for j in range(3)]
    eigenvalues, vectors = _eigen(gram)
    active = [k for k, value in enumerate(eigenvalues) if value > max(eigenvalues)*1e-10 and value > 0]
    rank = len(active)
    condition = math.sqrt(max(eigenvalues)/min(eigenvalues)) if rank == 3 else None
    separable = rank == 3 and condition <= CONDITION_LIMIT
    inverse = [[math.fsum(vectors[j][k]*vectors[l][k]/eigenvalues[k] for k in active)
                for l in range(3)] for j in range(3)]
    mean_y = math.fsum(p['period_s'] for p in points)/n
    y = [(p['period_s']-mean_y)*1e6 for p in points]
    xy = [math.fsum(row[j]*response for row, response in zip(x, y))/n for j in range(3)]
    beta = [math.fsum(inverse[j][k]*xy[k] for k in range(3)) for j in range(3)]
    predictions = [math.fsum(row[j]*beta[j] for j in range(3)) for row in x]
    residuals = [observed-fitted for observed, fitted in zip(y, predictions)]
    for p, fitted, residual in zip(points, predictions, residuals):
        p.update(fitted_period_s=mean_y+fitted*1e-6, residual_us=residual)
    sse = math.fsum(e*e for e in residuals)
    sst = math.fsum(value*value for value in y)
    constant_period = max(p['period_s'] for p in points) == min(p['period_s'] for p in points)
    r2 = None if constant_period else max(0., min(1., 1-sse/sst))
    reason = None if separable else 'These variables cannot be separated reliably in this segment; adjusted slopes are withheld.'
    # The joint slope's influence is (X'X)^-1 X_i e_i, converted back
    # from standardized predictors. Share all inference gates with legacy fits.
    coefficients = {}
    uncertainties = []
    for j, name in enumerate(VARIABLES):
        slope = beta[j]/scales[j] if separable else None
        influence = ([math.fsum(inverse[j][k]*row[k] for k in range(3))/n/scales[j]
                      for row in x] if separable else [1./n]*n)
        u = slope_uncertainty(points, influence, residuals, 1., slope or 0.,
                              bucket_seconds=bucket_seconds, smoothing_window_seconds=WINDOW,
                              parameter_count=rank+1)
        if not separable:
            u.update(available=False, reason=reason)
        uncertainties.append(u)
        coefficients[name] = dict(slope_us_per_unit=slope,
            se_us_per_unit=u['slope_se_us_per_C'] if u['available'] else None,
            ci95_us_per_unit=u['slope_ci95_us_per_C'] if u['available'] else None)
    policy = next((u for u in uncertainties if not u['available']), uncertainties[0])
    uncertainty = {k: value for k, value in policy.items()
                   if k not in ('slope_se_us_per_C', 'slope_ci95_us_per_C', 'p_value')}
    if not uncertainty['available']:
        for coefficient in coefficients.values():
            coefficient.update(se_us_per_unit=None, ci95_us_per_unit=None)
    temperature_rms = individual['temperature_C'].get('residual_rms_us')
    rms = math.sqrt(sse/n)
    return individual, dict(available=True, reason=reason, coefficients_available=separable,
        coefficients=coefficients, mean_period_s=mean_y, means=dict(zip(VARIABLES, means)),
        r_squared=r2, adjusted_r_squared=None if r2 is None else 1-(1-r2)*(n-1)/(n-rank-1),
        residual_rms_us=rms, temperature_residual_rms_us=temperature_rms,
        residual_reduction_pct=100*(1-rms/temperature_rms) if temperature_rms else None,
        predictor_rank=rank, condition_number=condition, condition_limit=CONDITION_LIMIT,
        uncertainty=uncertainty)


def query_environmental_relationships(data_dir, start, end, *, session=None):
    """Trailing 600-second environmental means, then complete paired bucket means.

    SQL windows include learning observations for environmental warm-up. Null,
    stale, gap or sparse history invalidates a window; no interpolation or joins
    across continuity identities. Queries retain the existing four-second budget.
    """
    if (any(_number(value) is None for value in (start, end))
            or not 0 <= start < end or end-start > 366*86400):
        raise ValueError('Use a finite range up to 366 days with start before end')
    if session is not None and (not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', session)):
        raise ValueError('Invalid history session')
    width = max(60, math.ceil((end-start)/600/60)*60)
    result = dict(start=start, end=end, estimate='window', bucket_seconds=width,
                  environmental_window_seconds=WINDOW, coverage_tolerance_seconds=COVERAGE_TOLERANCE,
                  segments=[], tracking_only=True)
    path = Path(data_dir)/DATABASE
    if not path.exists():
        return result
    connection = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=.2)
    connection.row_factory = sqlite3.Row
    deadline = time.monotonic()+4
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
    try:
        if connection.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('Unsupported history schema')
        session_condition = 'AND session = ?' if session else ''
        params = [max(0, start-WINDOW-COVERAGE_TOLERANCE), end, *([session] if session else []), width, start, MAX_AVERAGES+1]
        rows = connection.execute(f'''
          WITH raw AS (
            SELECT time,session,segment,source,temperature_C,humidity_pct,pressure_hPa,window_period_s,
              json_extract(payload,'$.quality.window_learning') AS learning,
              COALESCE(json_extract(payload,'$.settings.window_seconds'),{WINDOW}) AS period_window,
              json_extract(payload,'$.quality.timebase') AS timebase,
              json_extract(payload,'$.settings') AS settings,
              time-LAG(time) OVER (PARTITION BY session,segment ORDER BY time) AS spacing,
              CASE WHEN temperature_C IS NULL OR humidity_pct IS NULL OR pressure_hPa IS NULL
                OR json_extract(payload,'$.gap_reason') IS NOT NULL THEN 1 ELSE 0 END AS invalid
            FROM observations WHERE time >= ? AND time <= ? {session_condition}
          ), rolling AS (
            SELECT *, AVG(temperature_C) OVER trailing AS temperature_mean,
              AVG(humidity_pct) OVER trailing AS humidity_mean, AVG(pressure_hPa) OVER trailing AS pressure_mean,
              MIN(time) OVER trailing AS first_time, MAX(invalid) OVER trailing AS invalid_window,
              MAX(COALESCE(spacing,0)) OVER trailing AS max_spacing
            FROM raw WINDOW trailing AS (PARTITION BY session,segment ORDER BY time
              RANGE BETWEEN {WINDOW} PRECEDING AND CURRENT ROW)
          ), eligible AS (
            SELECT * FROM rolling WHERE window_period_s > 0 AND learning = 0 AND period_window = {WINDOW}
              AND time-first_time >= {WINDOW-COVERAGE_TOLERANCE}
              AND invalid_window = 0 AND max_spacing <= {COVERAGE_TOLERANCE}
          )
          SELECT session,segment,source,CAST(time / ? AS INTEGER) AS bucket,
            MIN(time) AS start,MAX(time) AS end,AVG(time) AS time,COUNT(*) AS samples,
            AVG(temperature_mean) AS temperature_C,AVG(humidity_mean) AS humidity_pct,
            AVG(pressure_mean) AS pressure_hPa,AVG(window_period_s) AS period_s,
            timebase,settings
          FROM eligible WHERE time >= ? GROUP BY session,segment,bucket ORDER BY start LIMIT ?
        ''', params).fetchall()
        if len(rows) > MAX_AVERAGES:
            raise ValueError('Too many measurement segments; choose a shorter range or one session')
        groups = {}
        for row in rows:
            if any(_number(row[key]) is None for key in (*VARIABLES, 'period_s')):
                continue
            group = groups.setdefault((row['session'], row['segment']), dict(
                session=row['session'], segment=row['segment'], source=row['source'], timebase=row['timebase'],
                settings=json.loads(row['settings'] or '{}'), start=row['start'], end=row['end'], samples=0, points=[]))
            group['end'] = max(group['end'], row['end'])
            group['samples'] += row['samples']
            group['points'].append({key: row[key] for key in ('time', *VARIABLES, 'period_s', 'samples')})
            group['points'][-1]['bucket_start'] = row['bucket']*width
        for group in groups.values():
            group['individual'], group['joint'] = environmental_fit(group['points'], bucket_seconds=width)
            group['spans'] = {name: [min(p[name] for p in group['points']), max(p[name] for p in group['points'])]
                              for name in VARIABLES}
        result['segments'] = list(groups.values())
        return result
    finally:
        connection.close()
