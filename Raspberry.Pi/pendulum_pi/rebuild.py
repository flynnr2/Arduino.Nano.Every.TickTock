"""Rebuild derived history from recorded captures, then replace it atomically.

Stop analysis, views, web, OLED and publishing before running this command.
Acquisition can continue: each input is bounded by its committed frontier.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from collections import deque
import csv
from dataclasses import replace
import fcntl
import gzip
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile

from .analysis import Analysis
from .common import atomic_json, utc_now
from .display import MEAN_MODEL
from .exports import secure_open
from .history import DATABASE
from .priming import CHECKPOINT
from .protocol import decode, ProtocolError
from .recording import ANALYSIS_INPUT
from .storage import storage_lock


@contextmanager
def member(roots, identity, logical):
    """Prefer a retained local file; otherwise read its immutable archive copy."""
    with ExitStack() as stack:
        for root in roots:
            for name in (logical, logical+'.gz'):
                try:
                    raw = secure_open(root, identity+'/'+name)
                except FileNotFoundError:
                    continue
                stack.enter_context(raw)
                yield stack.enter_context(gzip.GzipFile(fileobj=raw)) if name.endswith('.gz') else raw
                return
        raise FileNotFoundError(f'No retained or archived {logical} for {identity}')


def segments(roots):
    manifests = {}
    for root in roots:
        for path in root.glob('*/segment-*/manifest.json'):
            identity = path.parent.relative_to(root).as_posix()
            if identity in manifests:
                continue
            with secure_open(root, identity+'/manifest.json') as stream:
                info = json.load(stream)
            if info.get('error') or info.get('closed_reason') in ('failed', 'error', 'write_error'):
                continue
            manifests[identity] = info
    sessions = {}
    for identity, info in manifests.items():
        sessions.setdefault(info['session'], []).append((identity, info))
    ordered, visited = [], set()
    def visit(session):
        if session in visited or session not in sessions:
            return
        visited.add(session)
        visit(sessions[session][0][1].get('previous_session'))
        ordered.extend(sorted(sessions[session], key=lambda pair: pair[1]['segment']))
    for session in sorted(sessions):
        visit(session)
    return ordered


def committed_lines(stream, limit):
    while stream.tell() < limit:
        remaining = limit-stream.tell()
        raw = stream.readline(min(remaining, 65536)+1)
        if not raw.endswith(b'\n') or len(raw) > min(remaining, 65536):
            raise ValueError('Missing, incomplete or oversized committed capture input')
        yield raw, stream.tell()


def recorded_csv(stream, info, name):
    """Ignore bytes beyond the manifest frontier, including torn crash tails."""
    limit = (info.get('committed_bytes') or {}).get(name, info['files'][name]['bytes'])
    return csv.DictReader(raw.decode('utf-8') for raw, _ in committed_lines(stream, limit))


def legacy_inputs(roots, identity, info):
    """Join byte-preserved receipts to accepted capture CSVs, without inventing UTC health."""
    cfg = info.get('contract', {}).get('cfg', {})
    nominal = decode(('CFG,'+','.join(f'{k}={v}' for k, v in cfg.items())).encode()).values['nhz']
    settings = {k: info.get('settings', {}).get(k, default) for k, default in
                (('target_period_s', None), ('pps_holdover_seconds', 180), ('forecast_cycle_length', 0))}
    changes = []
    try:
        with member(roots, identity, 'PI.CSV') as raw:
            for row in recorded_csv(raw, info, 'PI.CSV'):
                if row.get('category') == 'display' and row.get('key') == 'configuration':
                    changes.append((float(row['ts_ms']), json.loads(row['msg'])))
    except FileNotFoundError:
        pass
    changes = deque(sorted(changes, key=lambda change: change[0]))
    with ExitStack() as stack:
        captures, expected = {}, {}
        for tag, name in (('CSW', 'PCSW.CSV'), ('CPS', 'PCPS.CSV')):
            raw = stack.enter_context(member(roots, identity, name))
            captures[tag] = iter(recorded_csv(raw, info, name))
            expected[tag] = next(captures[tag], None)
        stream = stack.enter_context(member(roots, identity, 'RAW.jsonl'))
        limit = info.get('committed_bytes', {}).get('RAW.jsonl', info['files']['RAW.jsonl']['bytes'])
        for raw, offset in committed_lines(stream, limit):
            receipt = json.loads(raw)
            while changes and changes[0][0] <= receipt['ts_ms']:
                _, config = changes.popleft()
                settings.update({key: config[key] for key in settings if key in config})
            if receipt.get('fragment'):
                continue
            try:
                record = decode(bytes.fromhex(receipt['raw_hex']))
            except ProtocolError:
                continue
            if record.tag not in captures:
                continue
            row = expected[record.tag]
            # RAW includes rejected/duplicate records. Only replay captures
            # which match the independently recorded accepted CSV row.
            if row is None or any(int(row[key]) != value for key, value in record.values.items()):
                continue
            expected[record.tag] = next(captures[record.tag], None)
            env = {}
            for key in ('temperature_C', 'humidity_pct', 'pressure_hPa'):
                value = float(row[key]) if row.get(key) else None
                env[key] = value if value is not None and math.isfinite(value) else None
            env['sht4x'] = {'fresh': env['temperature_C'] is not None and env['humidity_pct'] is not None}
            env['bmp280'] = {'fresh': env['pressure_hPa'] is not None}
            yield dict(version=1, tag=record.tag, values=record.values, nominal_hz=int(nominal),
                mono=receipt['ts_ms']/1000., epoch=receipt['epoch'], boot_id='raw-backfill-'+info['session'],
                source='serial', logging=dict(session=info['session'], path=identity,
                    reason=info.get('reason', 'raw_backfill'), active=True, enabled=True),
                counters={}, environment=env, time_health={'status': 'unavailable', 'fresh': False},
                settings=dict(settings)), offset
        if any(row is not None for row in expected.values()):
            raise ValueError(f'Accepted capture CSVs do not match RAW receipts in {identity}')


def rebuild_history(settings, progress=print):
    """Never modify recordings or install a partially rebuilt database."""
    roots = [settings.data_dir]
    if settings.archive_dir and settings.archive_dir.exists():
        roots.append(settings.archive_dir)
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    report = dict(model=MEAN_MODEL, rebuilt_utc=utc_now(), records=0, segments=[], missing=[])
    with (settings.runtime_dir/'analysis.lock').open('a') as lock, storage_lock(settings.data_dir, exclusive=False):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Stop the analysis service before rebuilding history') from None
        with tempfile.TemporaryDirectory(prefix='.history-rebuild-', dir=settings.data_dir) as directory:
            stage = Path(directory)
            worker = Analysis(replace(settings, data_dir=stage, runtime_dir=stage/'runtime'))
            # Start replay without a restart-cache writer; any session-local
            # caches created later remain isolated in the staging directory.
            worker.checkpoint.close()
            worker.checkpoint.thread = None
            try:
                for identity, info in segments(roots):
                    logical = ANALYSIS_INPUT if ANALYSIS_INPUT in info.get('files', {}) else 'RAW.jsonl'
                    count = 0
                    try:
                        with ExitStack() as stack:
                            if logical == ANALYSIS_INPUT:
                                stream = stack.enter_context(member(roots, identity, logical))
                                limit = info.get('committed_bytes', {}).get(logical, info['files'][logical]['bytes'])
                                inputs = ((json.loads(raw), offset) for raw, offset in committed_lines(stream, limit))
                            elif info.get('files', {}).get(logical, {}).get('rows', 0):
                                inputs = legacy_inputs(roots, identity, info)
                            else:
                                inputs = ()
                            for value, offset in inputs:
                                worker.consume(value, identity, offset, batch=True)
                                count += 1
                                if count % 512 == 0:
                                    worker.save_state()
                                    worker.connection.commit()
                    except FileNotFoundError as error:
                        report['missing'].append(dict(path=identity, reason=str(error)))
                        worker.display.reset()
                        worker.history._pending.append('backfill_input_missing')
                        if logical == ANALYSIS_INPUT:
                            limit = info.get('committed_bytes', {}).get(logical, info['files'][logical]['bytes'])
                            worker.connection.execute('INSERT OR REPLACE INTO analysis_offsets VALUES (?,?)', (identity, limit))
                    worker.save_state()
                    worker.connection.commit()
                    report['records'] += count
                    report['segments'].append(dict(path=identity, input=logical, records=count))
                    progress(f'{identity}: {count} captures; total {report["records"]}')
                report['observations'] = worker.connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
                report['coverage'] = worker.connection.execute('SELECT MIN(time),MAX(time) FROM observations').fetchone()
                if not report['observations']:
                    raise ValueError('No history could be reconstructed; existing database is unchanged')
                integrity = worker.connection.execute('PRAGMA integrity_check').fetchone()[0]
                if integrity != 'ok':
                    raise ValueError(f'Rebuilt database integrity check failed: {integrity}')
                worker.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            finally:
                worker.close()
            target = settings.data_dir/DATABASE
            if target.exists():
                old = sqlite3.connect(target, timeout=.2)
                try:
                    if old.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                        raise RuntimeError('Stop history readers before replacing the database')
                finally:
                    old.close()
            for suffix in ('-wal', '-shm'):
                Path(str(target)+suffix).unlink(missing_ok=True)
            os.replace(stage/DATABASE, target)
            fd = os.open(settings.data_dir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            (settings.data_dir/CHECKPOINT).unlink(missing_ok=True)
            atomic_json(settings.data_dir/'history-rebuild.json', report, durable=True)
            (settings.runtime_dir/'analysis.json').unlink(missing_ok=True)
            cache = settings.runtime_dir/'views'
            if cache.exists():
                for path in cache.glob('*.json'):
                    path.unlink()
    return report
