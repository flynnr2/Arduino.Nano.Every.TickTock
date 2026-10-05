"""Replay committed captures; save model state, history and cursor together.

No UART, I2C or network ownership. Processing time never becomes observation
time. A failed transaction is retried from its preceding model state.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import fcntl
import json
import sqlite3
import threading
import time

from .common import atomic_json, boot_id, read_json, notify_watchdog
from .config import HOT_FIELDS, load_settings
from .display import DisplayEstimator, MEAN_MODEL
from .forecast import MODEL, VERSION
from .history import HistoryWriter, _connect
from .priming import MeanCheckpoint
from .rating import rated_display
from .recording import ANALYSIS_INPUT
from .exports import secure_open
from .storage import storage_lock

STATE_VERSION = (1, MEAN_MODEL, MODEL, VERSION)
HISTORY_STATE = ('session', '_previous', '_segment', '_segment_reasons', '_continuity',
                 '_pending', '_dropped_seen', '_last_gap', '_last_written_mono',
                 '_last_written_segment', '_start_mono', '_start_epoch', '_writes', '_last_prune', '_health')


def pack(value):
    """Only JSON containers and known deque/tuple types; never executable pickle."""
    if isinstance(value, deque):
        return {'deque': [pack(v) for v in value], 'maxlen': value.maxlen}
    if isinstance(value, tuple):
        return {'tuple': [pack(v) for v in value]}
    if isinstance(value, list):
        return [pack(v) for v in value]
    if isinstance(value, dict):
        return {k: pack(v) for k, v in value.items()}
    return value


def unpack(value):
    if isinstance(value, list):
        return [unpack(v) for v in value]
    if isinstance(value, dict):
        if set(value) == {'deque', 'maxlen'}:
            return deque((unpack(v) for v in value['deque']), maxlen=value['maxlen'])
        if set(value) == {'tuple'}:
            return tuple(unpack(v) for v in value['tuple'])
        return {k: unpack(v) for k, v in value.items()}
    return value


class Analysis:
    def __init__(self, settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.connection = _connect(settings.data_dir / 'observatory-history.sqlite3')
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS analysis_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        self.connection.execute('CREATE TABLE IF NOT EXISTS analysis_offsets (path TEXT PRIMARY KEY, offset INTEGER NOT NULL)')
        self.connection.commit()
        self.display = DisplayEstimator(settings.pps_holdover_seconds, settings.forecast_cycle_length)
        self.history = HistoryWriter(settings)
        self.latest = {}
        self.recording_session = None
        self.result = None
        self.last_loss = None
        self.checkpoint = MeanCheckpoint(settings.data_dir, boot_id(), 'serial')
        row = self.connection.execute('SELECT payload FROM analysis_state WHERE id=1').fetchone()
        if row:
            self.restore(json.loads(row[0]))
        elif self.connection.execute('SELECT 1 FROM analysis_offsets WHERE offset>0 LIMIT 1').fetchone():
            raise ValueError('Saved analysis state is missing while replay progress exists; restore the database before continuing')
        self.checkpoint.start()
        self.health = {'state': 'waiting', 'error': None, 'processed_records': 0}
        self.last_scan = float('-inf')
        self.paths = []

    def state(self):
        objects = {name: pack(obj.__dict__) for name, obj in
                   (('clock', self.display.clock), ('window', self.display.window),
                    ('forecaster', self.display.forecaster))}
        objects['display'] = pack({k: v for k, v in self.display.__dict__.items()
                                   if k not in ('clock', 'window', 'forecaster')})
        return dict(version=list(STATE_VERSION), models=objects,
                    history=pack({k: getattr(self.history, k) for k in HISTORY_STATE}),
                    latest=self.latest, recording_session=self.recording_session,
                    last_loss=self.last_loss, result=self.result,
                    priming_pending=self.checkpoint.pending)

    def restore(self, state):
        if state.get('version') != list(STATE_VERSION):
            raise ValueError('Saved analysis model differs; migration/rebuild required before replay can continue')
        self.display = DisplayEstimator()
        for name in ('clock', 'window', 'forecaster'):
            target = getattr(self.display, name)
            values = unpack(state['models'][name])
            if set(values) != set(target.__dict__):
                raise ValueError('Saved analysis fields differ; explicit model migration required')
            target.__dict__.update(values)
        values = unpack(state['models']['display'])
        expected = set(self.display.__dict__) - {'clock', 'window', 'forecaster'}
        if set(values) != expected:
            raise ValueError('Saved display fields differ; explicit model migration required')
        self.display.__dict__.update(values)
        for key, value in unpack(state['history']).items():
            if key not in HISTORY_STATE:
                raise ValueError('Unknown history checkpoint field')
            setattr(self.history, key, value)
        self.latest = state['latest']
        self.recording_session = state['recording_session']
        self.last_loss = state.get('last_loss')
        self.result = state['result']
        self.checkpoint.pending = state.get('priming_pending')

    def discover(self):
        # previous_session links retain acquisition order across wall-clock steps.
        manifests = {}
        for path in self.settings.data_dir.glob('*/segment-*/manifest.json'):
            if any(p.is_symlink() for p in (path, path.parent, path.parent.parent)):
                continue
            info = read_json(path, {}) or {}
            if ANALYSIS_INPUT in info.get('files', {}):
                manifests.setdefault(info['session'], []).append((path, info))
        sessions, visiting = [], set()
        def visit(session):
            if session in visiting or session not in manifests:
                return
            visiting.add(session)
            visit(manifests[session][0][1].get('previous_session'))
            sessions.append(session)
        for session in sorted(manifests):
            visit(session)
        self.paths = [path.parent.relative_to(self.settings.data_dir).as_posix()
                      for session in sessions for path, _ in sorted(manifests[session], key=lambda p: p[1]['segment'])]
        self.last_scan = time.monotonic()

    def consume(self, value, path, offset, *, batch=False):
        if value.get('version') != 1 or value.get('tag') not in ('CSW', 'CPS'):
            raise ValueError('Unsupported saved capture input')
        before = None if batch else json.loads(json.dumps(self.state(), allow_nan=False))
        try:
            config = value['settings']
            settings = replace(self.settings, **config)
            session = value['logging']['session']
            if session != self.recording_session:
                if self.recording_session is not None:
                    # Preserve only the mean priming facility at a new capture
                    # timeline; PPS qualification and forecasts start afresh.
                    self.checkpoint = self._new_checkpoint(value['source'])
                    if not value['logging'].get('reason', '').startswith('service_start'):
                        self.checkpoint.pending = None
                    self.display.reset()
                self.latest = {}
                self.recording_session = session
                self.last_loss = None
            loss = tuple(value['counters'].get(k, 0) for k in
                         ('storage_lost_CSW', 'storage_lost_CPS', 'analysis_input_lost', 'receiver_queue_drops'))
            if self.last_loss is not None and tuple(self.last_loss) != loss:
                self.display.reset()
                self.checkpoint.pending = None
            self.last_loss = list(loss)
            self.display.configure(settings.pps_holdover_seconds, settings.forecast_cycle_length)
            revision = self.display.estimate_revision
            self.display.observe(value['tag'], value['values'], value['nominal_hz'], value['mono'], value['epoch'])
            if revision != self.display.estimate_revision and self.latest:
                self.checkpoint.pending = None
            self.latest[value['tag']] = value['values']
            if value['source'] == 'serial' and value['boot_id'] == self.checkpoint.boot_id:
                self.checkpoint.restore(self.display, value['mono'], value['epoch'], value['time_health'])
            result = dict(updated_monotonic=value['mono'], observed_epoch=value['epoch'],
                          updated_utc=datetime.fromtimestamp(value['epoch'], timezone.utc).isoformat(),
                          boot_id=value['boot_id'], source=value['source'], connected=True, ready=True,
                          capture_stale=False, last_capture_age_seconds=0,
                          logging=value['logging'], counters=value['counters'], latest=dict(self.latest),
                          display=rated_display(self.display.snapshot(value['mono']), settings.target_period_s),
                          environment=value['environment'], time_health=value['time_health'],
                          mean_checkpoint=dict(self.checkpoint.health),
                          capture_provenance={'path': path, 'offset': offset, 'tag': value['tag'], 'seq': value['values']['seq']})
            # _write may prune before its insertion. The insertion, summaries,
            # complete causal model and replay cursor share the final transaction.
            self.history._write(self.connection, result, settings, managed_transaction=True)
            result['history'] = self.history.snapshot()
            self.result = result
            self.connection.execute('INSERT OR REPLACE INTO analysis_offsets VALUES (?,?)', (path, offset))
            if not batch:
                self.save_state()
                self.connection.commit()
        except Exception:
            if not batch:
                self.connection.rollback()
                self.restore(before)
            raise
        if not batch:
            self.submit_checkpoint()
        self.health.update(state='processing', error=None,
                           processed_records=self.health['processed_records'] + 1,
                           processed_frontier=result['capture_provenance'])

    def _new_checkpoint(self, source):
        self.checkpoint.close()
        result = MeanCheckpoint(self.settings.data_dir, boot_id(), source)
        result.start()
        return result

    def save_state(self):
        self.connection.execute('INSERT OR REPLACE INTO analysis_state VALUES (1,?)',
                                (json.dumps(self.state(), allow_nan=False, separators=(',', ':')),))

    def submit_checkpoint(self, *, force=False):
        if self.result and self.result['source'] == 'serial' and self.result['boot_id'] == self.checkpoint.boot_id:
            self.checkpoint.submit(self.display, self.result['updated_monotonic'],
                                   self.result['observed_epoch'], self.result['time_health'], force=force)

    def step(self, max_records=128):
        if time.monotonic() - self.last_scan >= 5:
            self.discover()
        processed = 0
        before = json.loads(json.dumps(self.state(), allow_nan=False))
        health_before = dict(self.health)
        try:
            with storage_lock(self.settings.data_dir, exclusive=False, blocking=False), self.connection:
                offsets = dict(self.connection.execute('SELECT path,offset FROM analysis_offsets'))
                for path in self.paths:
                    manifest = read_json(self.settings.data_dir / path / 'manifest.json', {}) or {}
                    limit = manifest.get('committed_bytes', {}).get(ANALYSIS_INPUT, 0)
                    offset = offsets.get(path, 0)
                    if limit < offset:
                        raise ValueError('Committed analysis input moved backwards')
                    if limit == offset:
                        continue
                    with secure_open(self.settings.data_dir, path + '/' + ANALYSIS_INPUT) as stream:
                        stream.seek(offset)
                        while stream.tell() < limit and processed < max_records:
                            remaining = limit - stream.tell()
                            raw = stream.readline(min(remaining, 65536) + 1)
                            if not raw.endswith(b'\n') or len(raw) > min(remaining, 65536):
                                raise ValueError('Incomplete or oversized record within committed frontier')
                            self.consume(json.loads(raw), path, stream.tell(), batch=True)
                            processed += 1
                            if processed % 8 == 0:
                                notify_watchdog()
                    if processed >= max_records:
                        break
                if processed:
                    self.save_state()
        except Exception:
            self.restore(before)
            self.health = health_before
            raise
        if processed:
            self.submit_checkpoint()
        self.health.update(state='caught_up' if processed < max_records else 'catching_up', error=None)
        return processed

    def publish(self):
        now = time.monotonic()
        capture = read_json(self.settings.runtime_dir / 'status.json', {}) or {}
        received = capture.get('transport', {}).get('last_received_monotonic')
        processed = self.result.get('updated_monotonic') if self.result and self.result.get('boot_id') == boot_id() else None
        self.health['processing_lag_seconds'] = max(0, received - processed) if received is not None and processed is not None else None
        atomic_json(self.settings.runtime_dir / 'analysis.json',
                    dict(published_monotonic=now, result=self.result, health=self.health))
        notify_watchdog()

    def close(self):
        self.submit_checkpoint(force=True)
        self.checkpoint.close()
        self.connection.close()


def run_analysis(settings, config_path, stop=None):
    stop = stop or threading.Event()
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    with (settings.runtime_dir / 'analysis.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another analysis worker owns this runtime directory') from None
        app = Analysis(settings)
        try:
            while not stop.is_set():
                try:
                    updated = load_settings(config_path)
                    app.settings = replace(app.settings, **{k: getattr(updated, k) for k in HOT_FIELDS})
                    count = app.step()
                except BlockingIOError:
                    app.health.update(state='waiting_for_storage')
                    count = 0
                except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
                    app.health.update(state='error', error=str(error))
                    count = 0
                app.publish()
                stop.wait(.01 if count >= 128 else 5)
        finally:
            app.close()
