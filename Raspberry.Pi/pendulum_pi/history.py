"""Bounded sampled history of estimates, separate from canonical captures.

The production analysis worker writes capture-time observations and its replay
checkpoint in a shared transaction. The asynchronous snapshot adapter remains
available to standalone callers. Indexed minute extrema bound long-range reads;
routine observations are sampled every ten seconds with quality boundaries.
Pi receipt/observation time is never represented as exact Nano-event UTC.
"""
from __future__ import annotations

from datetime import datetime
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import queue
import shutil
import sqlite3
import threading
import time
import uuid

SCHEMA_VERSION = 3
DATABASE = 'observatory-history.sqlite3'
SERIES = ('short_period_s', 'long_period_s', 'short_rate_s_day', 'long_rate_s_day',
          'short_block_delta_us', 'long_block_delta_us', 'temperature_C',
          'humidity_pct', 'pressure_hPa', 'window_period_s', 'window_rate_s_day')
WINDOW_SERIES = ('window_period_s', 'window_rate_s_day')
# Rate extrema must be retained independently: targets can change within a
# query bucket while periods remain unchanged.
EXTREMA = SERIES
MAX_POINTS = 5000
RESERVE = 1024 * 1024


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _epoch(snapshot):
    value = _number(snapshot.get('observed_epoch'))
    if value is not None:
        return value
    try:
        return datetime.fromisoformat(snapshot['updated_utc'].replace('Z', '+00:00')).timestamp()
    except (KeyError, TypeError, ValueError):
        return time.time()


def _json(value):
    return json.dumps(value, allow_nan=False, separators=(',', ':'), sort_keys=True)


def _connect(path):
    connection = sqlite3.connect(path, timeout=0.2)
    # Dashboard range and correlation reads can run for several seconds. WAL
    # lets those readers keep a stable snapshot without blocking new samples.
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA wal_autocheckpoint=128')
    connection.execute('PRAGMA journal_size_limit=1048576')
    connection.execute('PRAGMA synchronous=NORMAL')
    connection.execute('PRAGMA auto_vacuum=INCREMENTAL')
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1, 2, SCHEMA_VERSION):
        connection.close()
        raise ValueError(f'unsupported history schema {version}')
    columns = ','.join(f'{name} REAL' for name in SERIES)
    connection.execute(f'''CREATE TABLE IF NOT EXISTS observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, session TEXT NOT NULL,
        segment TEXT NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL,{columns})''')
    connection.execute('CREATE INDEX IF NOT EXISTS observation_time ON observations(time)')
    connection.execute('CREATE INDEX IF NOT EXISTS observation_session_time ON observations(session,time)')
    # Range/session labels must not pull every large provenance payload off disk.
    connection.execute('CREATE INDEX IF NOT EXISTS observation_range_metadata ON observations(time,session,source)')
    extrema = ','.join(f'min_{name} REAL,max_{name} REAL,min_{name}_id INTEGER,max_{name}_id INTEGER'
                       for name in EXTREMA)
    connection.execute(f'''CREATE TABLE IF NOT EXISTS minutes (
        bucket INTEGER NOT NULL, segment TEXT NOT NULL, session TEXT NOT NULL,
        first_id INTEGER NOT NULL,last_id INTEGER NOT NULL,{extrema},PRIMARY KEY(bucket,segment))''')
    connection.execute('CREATE INDEX IF NOT EXISTS minute_session_time ON minutes(session,bucket)')
    if version in (1, 2):
        # Old samples cannot reconstruct the two new windowed means.
        observation_columns = {row[1] for row in connection.execute('PRAGMA table_info(observations)')}
        minute_columns = {row[1] for row in connection.execute('PRAGMA table_info(minutes)')}
        for name in WINDOW_SERIES:
            if name not in observation_columns:
                connection.execute(f'ALTER TABLE observations ADD COLUMN {name} REAL')
            for direction in ('min', 'max'):
                column = f'{direction}_{name}'
                if column not in minute_columns:
                    connection.execute(f'ALTER TABLE minutes ADD COLUMN {column} REAL')
                if f'{column}_id' not in minute_columns:
                    connection.execute(f'ALTER TABLE minutes ADD COLUMN {column}_id INTEGER')
    connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
    connection.commit()
    return connection


class HistoryWriter:
    """Queue capacity and sampling keep acquisition work and memory bounded."""
    def __init__(self, settings, queue_capacity=64, sample_interval_seconds=10):
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 4096:
            raise ValueError('history queue capacity must be between 1 and 4096')
        if _number(sample_interval_seconds) is None or not 1 <= sample_interval_seconds <= 300:
            raise ValueError('history sample interval must be between 1 and 300 seconds')
        self.settings = settings
        self.path = Path(settings.data_dir) / DATABASE
        self.session = uuid.uuid4().hex
        self.queue = queue.Queue(maxsize=queue_capacity)
        self._stop = threading.Event()
        self._thread = None
        self._last_submit = None
        self._previous = None
        self._segment = None
        self._segment_reasons = []
        self._continuity = {}
        self._pending = []
        self._dropped_seen = 0
        self._overflow_after_mono = None
        self._last_gap = None
        self.sample_interval_seconds = sample_interval_seconds
        self._last_written_mono = None
        self._last_written_segment = None
        self._start_mono = None
        self._start_epoch = None
        self._writes = 0
        self._last_prune = None
        self._health = {'state': 'waiting', 'error': None, 'dropped_points': 0,
                        'written_points': 0, 'session': self.session, 'schema_version': SCHEMA_VERSION,
                        'time_semantics': 'Pi observation time; not exact Nano event UTC'}

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='observatory-history', daemon=True)
            self._thread.start()

    def submit(self, snapshot, settings=None):
        """Never wait for disk or the worker, including when the queue is full."""
        now = _number(snapshot.get('updated_monotonic'))
        if now is None:
            return False
        if self._last_submit is not None and 0 <= now - self._last_submit < 1:
            return False
        self._last_submit = now
        try:
            self.queue.put_nowait((snapshot, settings or self.settings))
            return True
        except queue.Full:
            self._health['dropped_points'] += 1
            self._overflow_after_mono = now
            return False

    def snapshot(self):
        result = dict(self._health)
        result.update(queue_depth=self.queue.qsize(), queue_capacity=self.queue.maxsize,
                      retention_days=getattr(self.settings, 'history_retention_days', 30),
                      max_mb=getattr(self.settings, 'history_max_mb', 256),
                      sample_interval_seconds=self.sample_interval_seconds)
        return result

    def close(self, timeout=5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self):
        connection = None
        try:
            while not self._stop.is_set() or not self.queue.empty():
                try:
                    snapshot, settings = self.queue.get(timeout=.1)
                except queue.Empty:
                    continue
                try:
                    self.settings = settings
                    if connection is None:
                        self.path.parent.mkdir(parents=True, exist_ok=True)
                        connection = _connect(self.path)
                    self._write(connection, snapshot, settings)
                except (OSError, sqlite3.Error, ValueError, TypeError) as error:
                    self._health.update(state='error', error=str(error))
                    if 'history_write_failure' not in self._pending:
                        self._pending.append('history_write_failure')
                    if connection:
                        connection.close()
                        connection = None
                finally:
                    self.queue.task_done()
        finally:
            if connection:
                connection.close()

    def _point(self, snapshot, settings):
        display = snapshot.get('display', {})
        logging = snapshot.get('logging', {})
        environment = snapshot.get('environment', {})
        mono = snapshot['updated_monotonic']
        epoch = _epoch(snapshot)
        if self._start_mono is None:
            self._start_mono = mono
            self._start_epoch = epoch
        config = {name: getattr(settings, name, None)
                  for name in ('target_period_s', 'pps_holdover_seconds')}
        config.update(window_seconds=600, estimator_model=display.get('window', {}).get('model', 'legacy_mean_calibration_unspecified'))
        config_id = hashlib.sha256(_json(config).encode()).hexdigest()[:16]
        gap = ('recording_disabled' if not logging.get('enabled', settings.logging_enabled)
               else 'storage_unavailable' if not logging.get('active', False)
               else 'stale' if display.get('stale') or snapshot.get('capture_stale')
               else 'waiting' if not display.get('available') or not snapshot.get('ready') else None)
        counters = snapshot.get('counters', {})
        swing = snapshot.get('latest', {}).get('CSW', {})
        health = snapshot.get('time_health', {})
        # Keep validity transitions without splitting whenever a quality age advances.
        utc = health.get('chrony', health.get('utc', health))
        if not isinstance(utc, dict):
            utc = {}
        quality_state = (utc.get('state', utc.get('status', 'unknown')),
                         utc.get('source'), utc.get('selected_source'), utc.get('fresh'))
        state = {'recording_session': logging.get('session'), 'source': snapshot.get('source', 'unknown'),
                 'config': config_id, 'estimator_model': config['estimator_model'], 'estimate_revision': display.get('estimate_revision'), 'timebase': display.get('timebase', 'WAIT'), 'gap': gap,
                 'utc': quality_state,
                 'loss': tuple(counters.get(name, 0) for name in
                               ('swing_missing', 'pps_missing', 'restarts', 'storage_lost_CSW', 'storage_lost_CPS')),
                 'nano_drops': tuple(swing.get(name) for name in ('drop_ir', 'drop_swing')),
                 'environment': tuple(environment.get(name, {}).get('fresh', environment.get('simulated', False))
                                      for name in ('sht4x', 'bmp280'))}
        reasons = list(dict.fromkeys(self._pending))
        dropped = self._health['dropped_points']
        if (dropped != self._dropped_seen and self._overflow_after_mono is not None
                and mono > self._overflow_after_mono):
            reasons.append('history_queue_overflow')
            self._dropped_seen = dropped
        previous = self._previous
        if previous is None:
            reasons.append('history_started')
        else:
            for name, reason in (('recording_session', 'capture_session_changed'), ('source', 'source_changed'),
                                 ('config', 'settings_changed'), ('estimator_model', 'estimator_changed'), ('estimate_revision', 'estimator_reset'), ('timebase', 'timebase_changed'),
                                 ('gap', 'availability_changed'), ('utc', 'utc_quality_changed'),
                                 ('loss', 'capture_loss'), ('nano_drops', 'capture_loss'),
                                 ('environment', 'environment_quality_changed')):
                if state[name] != previous[name]:
                    reasons.append(reason)
            delay = mono - previous['mono']
            if delay <= 0 or delay > 30:
                reasons.append('observation_gap')
            elif delay > 3:
                # A brief missed status publication does not imply lost captures.
                # Capture counters, estimator revision and freshness remain gates.
                reasons.append('observation_delay')
            if abs((epoch - previous['epoch']) - (mono - previous['mono'])) > 2:
                reasons.append('wall_clock_correction')
            elapsed = display.get('window', {}).get('elapsed_seconds', 0)
            if elapsed < previous['estimator_elapsed']:
                reasons.append('estimator_reset')
        state.update(mono=mono, epoch=epoch,
                     estimator_elapsed=display.get('window', {}).get('elapsed_seconds', 0))
        self._previous = state
        self._pending.clear()
        diagnostic_reasons = {'utc_quality_changed', 'observation_delay'}
        measurement_reasons = [reason for reason in reasons if reason not in diagnostic_reasons]
        if measurement_reasons:
            self._segment = uuid.uuid4().hex
            self._segment_reasons = list(dict.fromkeys(measurement_reasons))
        # Each trace follows its own validity. Sensor outages and timing changes
        # are still combined in the segment used for environmental regression.
        common = set(measurement_reasons) - {
            'environment_quality_changed', 'settings_changed', 'estimator_changed',
            'estimator_reset', 'timebase_changed', 'capture_loss', 'availability_changed'}
        for group, index in (('timing', None), ('sht4x', 0), ('bmp280', 1)):
            changed = bool(common) or group not in self._continuity
            if group == 'timing':
                changed |= bool(set(measurement_reasons) - {'environment_quality_changed'})
            elif previous is not None:
                changed |= state['environment'][index] != previous['environment'][index]
                changed |= ((gap in ('recording_disabled', 'storage_unavailable')) !=
                            (previous['gap'] in ('recording_disabled', 'storage_unavailable')))
            if changed:
                self._continuity[group] = uuid.uuid4().hex
        boundaries = list(dict.fromkeys(self._segment_reasons +
                                        [r for r in reasons if r in diagnostic_reasons]))
        point = {'time': epoch, 'monotonic': mono, 'elapsed_seconds': mono - self._start_mono,
                 'session': self.session, 'session_start_time': self._start_epoch,
                 'recording_session': logging.get('session'),
                 'source': state['source'], 'segment': self._segment, 'boundary': boundaries,
                 'continuity': dict(self._continuity),
                 'gap_reason': gap, 'settings': config, 'config_revision': config_id,
                 'capture': dict(swing),
                 'quality': {'timebase': state['timebase'],
                             'calibration_age_seconds': display.get('pps', {}).get('calibration_age_seconds'),
                             'holdover_limit_seconds': display.get('pps', {}).get('holdover_limit_seconds'), 'utc': health or {'state': 'unknown'},
                             'sht4x': environment.get('sht4x', {}), 'bmp280': environment.get('bmp280', {})}}
        # Retain historical EWMA columns for readers, but never populate them
        # from the current mean or silently reconstruct a mean from old EWMAs.
        point.update({name: None for name in SERIES})
        estimate = display.get('window', {})
        period_us = _number(estimate.get('period_us'))
        period = period_us / 1e6 if period_us is not None and period_us > 0 and gap is None else None
        point['window_period_s'] = period
        point['window_rate_s_day'] = _number(estimate.get('gain_seconds_per_day')) if period else None
        point['quality']['window_learning'] = estimate.get('learning', True)
        point['quality']['window_priming'] = estimate.get('priming', {})
        for key, sensor in (('temperature_C', 'sht4x'), ('humidity_pct', 'sht4x'), ('pressure_hPa', 'bmp280')):
            fresh = environment.get(sensor, {}).get('fresh', environment.get('simulated', False))
            point[key] = _number(environment.get(key)) if fresh and gap not in ('recording_disabled', 'storage_unavailable') else None
        return point

    def _write(self, connection, snapshot, settings, *, managed_transaction=False):
        point = self._point(snapshot, settings)
        gap = point['gap_reason']
        max_bytes = int(getattr(settings, 'history_max_mb', 256) * 1024**2)
        # Leave room for SQLite's rollback journal; only this derived DB is pruned.
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
        page_limit = max(32, int(max_bytes * .8) // page_size)
        connection.execute(f'PRAGMA max_page_count={page_limit}')
        self._prune(connection, point['time'], settings, page_limit, managed_transaction=managed_transaction)
        # A hot reduction of the cache limit also shrinks an existing larger
        # database before accepting another observation. All work stays here in
        # the worker; a slow cleanup can only overflow the bounded history queue.
        while connection.execute('PRAGMA page_count').fetchone()[0] > page_limit:
            live_pages = (connection.execute('PRAGMA page_count').fetchone()[0]
                          - connection.execute('PRAGMA freelist_count').fetchone()[0])
            if live_pages > page_limit * .8:
                self._prune(connection, point['time'], settings, page_limit, force=True, managed_transaction=managed_transaction)
            connection.execute('PRAGMA incremental_vacuum(1024)').fetchall()
        connection.execute(f'PRAGMA max_page_count={page_limit}')
        # Retention still runs while recording is paused. Persist a single
        # pause marker, then avoid writing disabled observations indefinitely.
        if gap in ('recording_disabled', 'storage_unavailable') and gap == self._last_gap:
            self._health.update(state='paused', error=None)
            return
        if (self._last_written_segment == point['segment'] and self._last_written_mono is not None
                and not set(point['boundary']) & {'utc_quality_changed', 'observation_delay'}
                and 0 <= point['monotonic'] - self._last_written_mono < self.sample_interval_seconds):
            return
        logging = snapshot.get('logging', {})
        if shutil.disk_usage(self.path.parent).free < settings.min_free_mb * 1024**2 + RESERVE:
            raise OSError('history paused: minimum free disk reserve reached')
        # Recorder includes the sidecar in its usage accounting. Leave a reserve
        # here as well; stale accounting is supplemented by the recorder's active flag.
        usage = logging.get('data_bytes', 0)
        if usage and usage + max_bytes + RESERVE >= settings.storage_budget_mb * 1024**2:
            raise OSError('history paused: local recording storage budget reached')
        # Keep the reason on the first stored observation of a new segment.
        # A sampled point may be skipped or fail before it reaches the database.
        if point['segment'] == self._last_written_segment:
            point['boundary'] = [r for r in point['boundary']
                                 if r in ('utc_quality_changed', 'observation_delay')]
        fields = ['time', 'session', 'segment', 'source', 'payload', *SERIES]
        values = [point[name] for name in fields[:4]] + [_json(point)] + [point[name] for name in SERIES]
        try:
            with nullcontext() if managed_transaction else connection:
                cursor = connection.execute(f"INSERT INTO observations ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", values)
                identity = cursor.lastrowid
                self._summarize(connection, point, identity)
        except sqlite3.OperationalError as error:
            if 'full' in str(error).lower():
                self._prune(connection, point['time'], settings, page_limit, force=True, managed_transaction=managed_transaction)
            raise
        self._last_gap = gap
        self._last_written_mono = point['monotonic']
        self._last_written_segment = point['segment']
        self._writes += 1
        self._health.update(state='paused' if gap in ('recording_disabled', 'storage_unavailable') else 'recording',
                            error=None, written_points=self._writes, last_written_epoch=point['time'],
                            database_bytes=self.path.stat().st_size)

    def _summarize(self, connection, point, identity):
        fields = ['bucket', 'segment', 'session', 'first_id', 'last_id']
        values = [int(point['time'] // 60) * 60, point['segment'], point['session'], identity, identity]
        updates = ['last_id=excluded.last_id']
        for name in EXTREMA:
            fields.extend((f'min_{name}', f'max_{name}', f'min_{name}_id', f'max_{name}_id'))
            values.extend((point[name], point[name], identity, identity))
            for direction, operator in (('min', '<'), ('max', '>')):
                col = f'{direction}_{name}'
                choose = f'(excluded.{col} IS NOT NULL AND (minutes.{col} IS NULL OR excluded.{col}{operator}minutes.{col}))'
                updates.extend((f'{col}_id=CASE WHEN {choose} THEN excluded.{col}_id ELSE minutes.{col}_id END',
                                f'{col}=CASE WHEN {choose} THEN excluded.{col} ELSE minutes.{col} END'))
        connection.execute(f"INSERT INTO minutes ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)}) "
                           f"ON CONFLICT(bucket,segment) DO UPDATE SET {','.join(updates)}", values)

    def _prune(self, connection, epoch, settings, page_limit, force=False, *, managed_transaction=False):
        count = connection.execute('PRAGMA page_count').fetchone()[0]
        free = connection.execute('PRAGMA freelist_count').fetchone()[0]
        pressure = count - free > page_limit * .88
        retention_due = self._last_prune is None or abs(epoch - self._last_prune) >= 60
        if not retention_due and not pressure and not force:
            return
        cutoff = epoch - getattr(settings, 'history_retention_days', 30) * 86400
        # Small committed deletions bound rollback-journal growth, even when
        # retention is shortened dramatically. Summary cleanup uses the same
        # cap; raw files and their publication/archival locks are never touched.
        while True:
            with nullcontext() if managed_transaction else connection:
                deleted = connection.execute(
                    'DELETE FROM observations WHERE id IN (SELECT id FROM observations WHERE time < ? LIMIT 128)',
                    (cutoff,)).rowcount
            if deleted < 128:
                break
        if pressure or force:
            total = connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
            remaining = max(64, total // 8)
            while remaining > 0:
                with nullcontext() if managed_transaction else connection:
                    connection.execute('DELETE FROM observations WHERE id IN (SELECT id FROM observations ORDER BY id LIMIT ?)',
                                       (min(128, remaining),))
                remaining -= 128
        minimum = connection.execute('SELECT MIN(id) FROM observations').fetchone()[0]
        while True:
            with nullcontext() if managed_transaction else connection:
                deleted = connection.execute(
                    'DELETE FROM minutes WHERE rowid IN (SELECT rowid FROM minutes WHERE first_id < ? LIMIT 128)',
                    (minimum if minimum is not None else 2**63 - 1,)).rowcount
            if deleted < 128:
                break
        connection.execute('PRAGMA incremental_vacuum(64)')
        self._last_prune = epoch


def query_history(data_dir, start_epoch, end_epoch, *, session=None, series=None, max_points=1000):
    """Read bounded extrema-preserving points; never read raw capture files.

    Long ranges select extrema from minute summaries. Every returned point keeps
    its continuity segment, so omitted/invalid observations cannot bridge gaps.
    """
    if any(_number(value) is None for value in (start_epoch, end_epoch)) or start_epoch >= end_epoch:
        raise ValueError('history requires finite start < end epoch seconds')
    if type(max_points) is not int or not 32 <= max_points <= MAX_POINTS:
        raise ValueError(f'max_points must be an integer between 32 and {MAX_POINTS}')
    if session is not None and (not isinstance(session, str) or len(session) > 128):
        raise ValueError('invalid history session')
    if isinstance(series, str):
        series = series.split(',')
    if series is not None and not isinstance(series, (list, tuple)):
        raise ValueError('history series must be a list or comma-separated string')
    selected = list(SERIES if series is None else series)
    if not selected or any(name not in SERIES for name in selected):
        raise ValueError('unknown history series')
    result = dict(schema_version=SCHEMA_VERSION, start=start_epoch, end=end_epoch,
                  time_semantics='Pi observation time; not exact Nano event UTC', points=[], sessions=[],
                  returned_points=0, reduced=False, available_start=None, available_end=None)
    path = Path(data_dir) / DATABASE
    if not path.exists():
        return result
    connection = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=.2)
    connection.row_factory = sqlite3.Row
    # Bound CPU work for pathological databases/ranges, without blocking acquisition.
    deadline = time.monotonic() + 8
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
    try:
        if connection.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('unsupported history schema')
        where = 'time >= ? AND time <= ?'
        parameters = [start_epoch, end_epoch]
        if session is not None:
            where += ' AND session = ?'
            parameters.append(session)
        bounds = connection.execute('SELECT (SELECT MIN(time) FROM observations),'
                                    '(SELECT MAX(time) FROM observations)').fetchone()
        result.update(available_start=bounds[0], available_end=bounds[1])
        # Limit process-session metadata independently of sample count.
        result['sessions'] = [dict(row) for row in connection.execute(
            'SELECT session,source,MIN(time) AS start,MAX(time) AS end FROM observations '
            f'WHERE {where} GROUP BY session,source ORDER BY end DESC LIMIT 100', parameters)]
        for entry in result['sessions']:
            bounds = connection.execute(
                'SELECT (SELECT MIN(time) FROM observations WHERE session=?),'
                '(SELECT MAX(time) FROM observations WHERE session=?)',
                (entry['session'], entry['session'])).fetchone()
            first = connection.execute('SELECT payload FROM observations WHERE session=? ORDER BY time LIMIT 1',
                                       (entry['session'],)).fetchone()
            if first:
                # Process origin is repeated as provenance in each observation,
                # so retention and selecting a shorter range do not relabel it.
                origin = json.loads(first['payload']).get('session_start_time', bounds[0])
                entry.update(start=origin, end=bounds[1], retained_start=bounds[0])
        raw = connection.execute(f'SELECT id FROM observations WHERE {where} ORDER BY time,id LIMIT ?',
                                 [*parameters, max_points + 1]).fetchall()
        if len(raw) <= max_points:
            rows = connection.execute(f'SELECT id,payload FROM observations WHERE {where} ORDER BY time,id LIMIT ?',
                                      [*parameters, max_points]).fetchall()
        else:
            result['reduced'] = True
            metrics = list(dict.fromkeys(selected))
            # Each bucket returns endpoints plus min/max of each requested series.
            bins = max(1, max_points // (2 + 2 * len(metrics)) - 3)
            width = (end_epoch - start_epoch) / bins
            ids = set()
            if end_epoch - start_epoch >= 3600:
                # Fully covered minutes use the persisted extrema. Partial edge
                # minutes use raw rows, keeping queries exact at arbitrary bounds.
                lower = math.ceil(max(start_epoch, result['available_start'] or start_epoch) / 60) * 60
                upper = math.floor(end_epoch / 60) * 60
                clause = 'bucket >= ? AND bucket < ?'
                args = [lower, upper]
                if session is not None:
                    clause += ' AND session = ?'
                    args.append(session)
                group = 'CAST((bucket - ?) / ? AS INTEGER)'
                for expression, identity in [('MIN(first_id)', 'first_id'), ('MAX(last_id)', 'last_id')]:
                    for row in connection.execute(f'SELECT {identity} AS id,{expression} FROM minutes WHERE {clause} GROUP BY {group}', [*args, start_epoch, width]):
                        ids.add(row['id'])
                for metric in metrics:
                    for direction in ('min', 'max'):
                        col = f'{direction}_{metric}'
                        for row in connection.execute(f'SELECT {col}_id AS id,{direction.upper()}({col}) AS value FROM minutes WHERE {clause} AND {col} IS NOT NULL GROUP BY {group}', [*args, start_epoch, width]):
                            ids.add(row['id'])
                # Two index seeks, rather than scanning the entire range for
                # every endpoint/metric and discarding all complete minutes.
                session_clause = ' AND session = ?' if session is not None else ''
                session_args = [session] if session is not None else []
                edge_columns = ','.join(['id', 'time', *metrics])
                edge_table = (f'(SELECT {edge_columns} FROM observations WHERE time >= ? AND time < ?{session_clause} '
                              f'UNION ALL SELECT {edge_columns} FROM observations WHERE time >= ? AND time <= ?{session_clause})')
                edge_params = [start_epoch, min(lower, end_epoch), *session_args,
                               max(upper, start_epoch), end_epoch, *session_args]
            else:
                edge_table = f'(SELECT * FROM observations WHERE {where})'
                edge_params = parameters
            group = 'CAST((time - ?) / ? AS INTEGER)'
            for expression in ('MIN(id)', 'MAX(id)'):
                for row in connection.execute(f'SELECT {expression} AS id FROM {edge_table} GROUP BY {group}', [*edge_params, start_epoch, width]):
                    ids.add(row['id'])
            for metric in metrics:
                for direction in ('MIN', 'MAX'):
                    for row in connection.execute(f'SELECT id,{direction}({metric}) FROM {edge_table} WHERE {metric} IS NOT NULL GROUP BY {group}', [*edge_params, start_epoch, width]):
                        ids.add(row['id'])
            # Edge buckets can overlap summary buckets; reduce once more if needed.
            rows = []
            identities = sorted(ids)
            for offset in range(0, len(identities), 500):
                chunk = identities[offset:offset + 500]
                rows.extend(connection.execute(f"SELECT id,payload FROM observations WHERE id IN ({','.join('?' for _ in chunk)}) AND {where}", [*chunk, *parameters]).fetchall())
            rows.sort(key=lambda row: (json.loads(row['payload'])['time'], row['id']))
            if len(rows) > max_points:
                # Preserve the global range endpoints. All retained points still
                # carry segment boundaries even if very short segments are omitted.
                keep = {0, len(rows) - 1}
                payloads = [json.loads(row['payload']) for row in rows]
                for metric in metrics:
                    valid = [i for i, point in enumerate(payloads) if point.get(metric) is not None]
                    if valid:
                        keep.add(min(valid, key=lambda i: payloads[i][metric]))
                        keep.add(max(valid, key=lambda i: payloads[i][metric]))
                remaining = [i for i in range(len(rows)) if i not in keep]
                slots = max_points - len(keep)
                keep.update(remaining[round(i * (len(remaining) - 1) / max(1, slots - 1))]
                            for i in range(slots))
                rows = [rows[i] for i in sorted(keep)]
                result['budget_limited'] = True
        for row in rows:
            point = json.loads(row['payload'])
            for name in WINDOW_SERIES:
                point.setdefault(name, None)
            point.pop('median_period_s', None)
            point.pop('median_rate_s_day', None)
            quality = point.setdefault('quality', {})
            quality.pop('median_learning', None)
            quality.setdefault('window_learning', True)
            point['id'] = row['id']
            for name in SERIES:
                if name not in selected:
                    point.pop(name, None)
            result['points'].append(point)
        result['returned_points'] = len(result['points'])
        return result
    finally:
        connection.close()
