"""Aligned environmental associations, with no additional Pi dependencies."""
from __future__ import annotations

from collections import deque
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


class _TrailingEnvironment:
    """One segment's causal window; a bad observation ages out naturally."""
    def __init__(self):
        self.rows = deque()
        self.totals = [0., 0., 0.]
        self.invalid = self.sparse = self.updates = 0
        self.last_time = None

    def add(self, row, gap):
        now = row['time']
        while self.rows and self.rows[0][0] < now - WINDOW:
            _, values, invalid, sparse = self.rows.popleft()
            for j, value in enumerate(values):
                self.totals[j] -= value
            self.invalid -= invalid
            self.sparse -= sparse
        values = tuple(_number(row[name]) for name in VARIABLES)
        invalid = int(gap is not None or any(value is None for value in values))
        sparse = int(self.last_time is not None and now - self.last_time > COVERAGE_TOLERANCE)
        values = tuple(value if value is not None else 0. for value in values)
        self.rows.append((now, values, invalid, sparse))
        for j, value in enumerate(values):
            self.totals[j] += value
        self.invalid += invalid
        self.sparse += sparse
        self.last_time = now
        self.updates += 1
        # Bound round-off after repeated additions/removals, without rescanning
        # every window for every observation.
        if self.updates % 128 == 0:
            self.totals = [math.fsum(entry[1][j] for entry in self.rows) for j in range(3)]

    def ready(self, now):
        return (self.rows and now - self.rows[0][0] >= WINDOW - COVERAGE_TOLERANCE
                and not self.invalid and not self.sparse)


class EnvironmentalQuery:
    """Incremental range calculation with bounded reads and a budget per step.

    The views worker advances this job between heartbeat/phase publications.
    Long ranges can finish across several steps without holding up that worker
    or failing solely because their total work exceeds four seconds.
    """
    def __init__(self, data_dir, start, end, *, session=None):
        if (any(_number(value) is None for value in (start, end))
                or not 0 <= start < end or end-start > 366*86400):
            raise ValueError('Use a finite range up to 366 days with start before end')
        if session is not None and (not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', session)):
            raise ValueError('Invalid history session')
        self.path = Path(data_dir) / DATABASE
        self.start, self.end, self.session = start, end, session
        self.width = max(60, math.ceil((end-start)/600/60)*60)
        self.deadline = 0.
        self.processed_records = 0
        self.result = None
        self._work = self._run()

    def step(self):
        if self.result is not None:
            return True
        self.deadline = time.monotonic() + 4
        try:
            next(self._work)
        except StopIteration as done:
            self.result = done.value
            return True
        return False

    def close(self):
        self._work.close()

    def _collect(self, peers, now, windows, buckets):
        # All equal-time peers have entered their windows before any is scored,
        # matching SQLite RANGE semantics even with overlapping sessions.
        for key, peer in peers.items():
            window = windows[key]
            if not window.ready(now):
                continue
            bucket = int(now / self.width)
            identity = (*key, bucket)
            if identity not in buckets:
                if len(buckets) >= MAX_AVERAGES:
                    raise ValueError('Too many measurement segments; choose a shorter range or one session')
                buckets[identity] = dict(session=key[0], segment=key[1], source=peer['source'],
                    timebase=peer['timebase'], settings=peer['settings'], bucket=bucket,
                    start=now, end=now, samples=0, totals=[0.] * 5)
            entry = buckets[identity]
            count = peer['count']
            entry['end'] = now
            entry['samples'] += count
            values = [now, *(value / len(window.rows) for value in window.totals)]
            for j, value in enumerate(values):
                entry['totals'][j] += value * count
            entry['totals'][4] += peer['period_sum']

    def _run(self):
        result = dict(start=self.start, end=self.end, estimate='window', bucket_seconds=self.width,
                      environmental_window_seconds=WINDOW, coverage_tolerance_seconds=COVERAGE_TOLERANCE,
                      segments=[], tracking_only=True)
        if not self.path.exists():
            return result
        connection = sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro', uri=True, timeout=.2)
        connection.row_factory = sqlite3.Row
        connection.set_progress_handler(lambda: int(time.monotonic() > self.deadline), 2000)
        windows, buckets = {}, {}
        try:
            if connection.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
                raise ValueError('Unsupported history schema')
            session_condition = 'AND session = ?' if self.session else ''
            params = [max(0, self.start-WINDOW-COVERAGE_TOLERANCE), self.end,
                      *([self.session] if self.session else [])]
            # One time-index scan. Extract only small metadata once, avoiding
            # full-payload Python decoding and SQL sorts/window intermediates.
            rows = connection.execute(f'''
                SELECT time,session,segment,source,temperature_C,humidity_pct,pressure_hPa,window_period_s,
                    json_extract(payload,'$.quality.window_learning','$.settings.window_seconds',
                        '$.quality.timebase','$.settings','$.gap_reason') AS metadata
                FROM observations WHERE time >= ? AND time <= ? {session_condition}
                ORDER BY time,id
            ''', params)
            peers, previous_time = {}, None
            for row in rows:
                now = row['time']
                if previous_time is not None and now != previous_time:
                    self._collect(peers, previous_time, windows, buckets)
                    peers = {}
                key = (row['session'], row['segment'])
                if key not in windows:
                    windows[key] = _TrailingEnvironment()
                learning, period_window, timebase, settings, gap = json.loads(row['metadata'])
                windows[key].add(row, gap)
                period = _number(row['window_period_s'])
                if (now >= self.start and period is not None and period > 0 and learning == 0
                        and (WINDOW if period_window is None else period_window) == WINDOW):
                    peer = peers.setdefault(key, dict(count=0, period_sum=0., source=row['source'],
                                                      timebase=timebase, settings=settings or {}))
                    peer['count'] += 1
                    peer['period_sum'] += period
                previous_time = now
                self.processed_records += 1
                if self.processed_records % 512 == 0:
                    # Retain the prior timestamp for gap detection if a segment
                    # reappears, but release windows that cannot affect a future
                    # observation in this time-ordered scan.
                    for window in windows.values():
                        if window.rows and window.last_time < now - WINDOW:
                            window.rows.clear()
                            window.totals = [0., 0., 0.]
                            window.invalid = window.sparse = 0
                    yield
            if previous_time is not None:
                self._collect(peers, previous_time, windows, buckets)
        finally:
            connection.close()
        # Release the read snapshot before fitting or yielding between segments.
        groups = {}
        for row in sorted(buckets.values(), key=lambda value: (value['start'], value['session'], value['segment'])):
            key = (row['session'], row['segment'])
            group = groups.setdefault(key, dict(session=row['session'], segment=row['segment'],
                source=row['source'], timebase=row['timebase'], settings=row['settings'],
                start=row['start'], end=row['end'], samples=0, points=[]))
            group['end'] = max(group['end'], row['end'])
            group['samples'] += row['samples']
            point = dict(zip(('time', *VARIABLES, 'period_s'),
                             (value / row['samples'] for value in row['totals'])))
            point.update(samples=row['samples'], bucket_start=row['bucket'] * self.width)
            group['points'].append(point)
        for group in groups.values():
            group['individual'], group['joint'] = environmental_fit(group['points'], bucket_seconds=self.width)
            group['spans'] = {name: [min(p[name] for p in group['points']), max(p[name] for p in group['points'])]
                              for name in VARIABLES}
            yield
        result['segments'] = list(groups.values())
        return result


def query_environmental_relationships(data_dir, start, end, *, session=None):
    """Synchronous adapter; the production worker advances EnvironmentalQuery."""
    job = EnvironmentalQuery(data_dir, start, end, session=session)
    try:
        while not job.step():
            pass
        return job.result
    finally:
        job.close()
