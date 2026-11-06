"""Bounded, restart-safe housekeeping, run separately from serial acquisition.

Only manifest-closed segments are eligible. Compression and offload process one
segment per pass so a backlog cannot monopolise a worker indefinitely. Retention
never removes a sole copy: the *whole* immutable archive is checksum-verified
again immediately before deleting any local member. An unavailable or corrupt
archive fails closed. The source manifest and segment identity remain throughout
local retention; after the summary horizon, fully expired tombstones are removed
only after rechecking their permanent archive.

Age limits apply independently to diagnostics, measurements and minute summaries.
At 90% of the configured byte budget (or below the free-space reserve), cleanup
aims for 80% and the reserve, oldest diagnostics first, then measurements. Summaries
are deleted only after their own age limit. If safe cleanup is impossible,
storage.json reports blocked; acquisition enforces its own hard budget/reserve.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time

from .common import atomic_json, utc_now
from .config import load_settings

MEASUREMENTS = frozenset({'PCSW.CSV', 'PCPS.CSV'})
SUMMARIES = frozenset({'SUMMARY.CSV'})
REPLAY_FILES = frozenset({'ANALYSIS.jsonl'})
DIAGNOSTICS = frozenset({'RAW.jsonl', 'PI.CSV', 'STS.CSV'})
DATA_FILES = MEASUREMENTS | SUMMARIES | DIAGNOSTICS | REPLAY_FILES
CHUNK = 1024 * 1024


def _safe(path: Path) -> Path:
    """Reject links in every path component, including configured roots."""
    path = Path(os.path.abspath(path))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f'symlinks are not permitted: {component}')
    return path


def _regular(path: Path) -> Path:
    path = _safe(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f'not a regular file: {path}')
    return path


def _read(path: Path, default=None):
    if not path.exists():
        _safe(path)
        return default
    with _regular(path).open(encoding='utf-8') as stream:
        return json.load(stream)


def _save(path: Path, value):
    _safe(path)
    atomic_json(path, value, durable=True)


def _sync_dir(path: Path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def storage_lock(data_dir: Path, exclusive: bool = True, *, blocking: bool = True):
    """Exports hold a shared lock for their complete stream lifetime."""
    root = _safe(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(_safe(root / '.storage.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('storage lock must be a regular file')
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fd, mode if blocking else mode | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _digest(path: Path, *, decompress=False):
    digest = hashlib.sha256()
    size = 0
    _regular(path)
    with (gzip.open(path, 'rb') if decompress else path.open('rb')) as stream:
        while data := stream.read(CHUNK):
            digest.update(data)
            size += len(data)
    return digest.hexdigest(), size


def _fingerprint(path: Path):
    info = _regular(path).stat()
    return [info.st_size, info.st_mtime_ns, info.st_ino]


def _seconds(value):
    if not isinstance(value, str):
        raise ValueError('missing or invalid segment UTC timestamp')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('segment UTC timestamps must include a timezone')
    return stamp.timestamp()


def _segments(root, errors):
    for session in sorted(root.iterdir()):
        if session.is_symlink():
            raise ValueError(f'symlink in data directory: {session}')
        if not session.is_dir() or session.name.startswith('.'):
            continue
        for directory in sorted(session.iterdir()):
            if directory.is_symlink():
                raise ValueError(f'symlink in session directory: {directory}')
            if not directory.is_dir() or not re.fullmatch(r'segment-\d{6,}', directory.name):
                continue
            try:
                manifest = _read(directory / 'manifest.json', {})
                if not isinstance(manifest, dict) or not manifest.get('closed_utc'):
                    continue
                if not isinstance(manifest.get('files', {}), dict):
                    raise ValueError('manifest files must be an object')
                if manifest.get('closed_reason') in {'failed', 'error', 'write_error'} or manifest.get('error'):
                    continue
                _seconds(manifest['closed_utc'])
                if manifest.get('started_utc'):
                    _seconds(manifest['started_utc'])
                if manifest.get('session') != session.name or manifest.get('segment') != int(directory.name[8:]):
                    raise ValueError(f'segment identity mismatch: {directory}')
                for member in directory.iterdir():
                    _regular(member)
                yield directory, manifest
            except (OSError, ValueError, KeyError) as error:
                errors.append(f'{directory}: {error}')


def _new_state(root, directory, manifest):
    identity = directory.relative_to(root).as_posix()
    return {'id': identity, 'path': identity, 'session': manifest['session'],
            'segment': manifest['segment'], 'started_utc': manifest.get('started_utc'),
            'first_record_utc': manifest.get('first_record_utc'),
            'min_record_utc': manifest.get('min_record_utc'),
            'max_record_utc': manifest.get('max_record_utc'),
            'host_clock_reversed': manifest.get('host_clock_reversed', False),
            'host_clock_discontinuities': manifest.get('host_clock_discontinuities', 0),
            'last_record_utc': manifest.get('last_record_utc'), 'closed_utc': manifest['closed_utc'],
            'manifest': manifest, 'files': {}, 'archive': {'verified': False}}


def _member(root, directory, logical, record):
    if logical not in DATA_FILES or not isinstance(record, dict):
        raise ValueError('invalid lifecycle file record')
    expected = directory / (logical + '.gz' if record.get('compressed') else logical)
    if record.get('path') != expected.relative_to(root).as_posix():
        raise ValueError('lifecycle path is outside its segment')
    return _safe(expected)


def _load_state(root, directory, manifest):
    state = _read(directory / 'lifecycle.json')
    if state is None:
        return _new_state(root, directory, manifest)
    if not isinstance(state, dict) or state.get('id') != directory.relative_to(root).as_posix():
        raise ValueError(f'invalid lifecycle identity: {directory}')
    if not isinstance(state.get('files'), dict) or not isinstance(state.get('archive'), dict):
        raise ValueError(f'invalid lifecycle structure: {directory}')
    if state.get('path') != state['id']:
        raise ValueError(f'invalid lifecycle path: {directory}')
    if state.get('manifest') != manifest:
        raise ValueError(f'closed manifest changed: {directory}')
    for name, record in state['files'].items():
        member = _member(root, directory, name, record)
        if record.get('expired'):
            # Expiry is committed before unlink; finish an interrupted deletion.
            continue
        if member.exists() and record.get('fingerprint') != _fingerprint(member):
            if _digest(member) != (record['sha256'], record['bytes']):
                raise ValueError(f'local checksum mismatch: {member}')
            record['fingerprint'] = _fingerprint(member)
        elif not member.exists():
            raise ValueError(f'unexpected missing local file: {member}')
    return state


def _check_complete(directory, state):
    expected = set(state['manifest'].get('files', {})) or (MEASUREMENTS | DIAGNOSTICS)
    if not expected <= DATA_FILES:
        raise ValueError('closed segment contains unsupported data files')
    for name in expected:
        record = state['files'].get(name, {})
        if not record.get('expired') and not (directory / name).exists() and not (directory / (name + '.gz')).exists():
            raise ValueError(f'incomplete closed segment: missing {name}')


def _compress(root, directory, state, settings):
    _check_complete(directory, state)
    if state['archive'].get('verified') or state['archive'].get('immutable'):
        # An acknowledged archive fixes file representation, including when
        # compression settings or the archive destination later change.
        return
    for temporary in directory.glob('.compress-*'):
        _regular(temporary).unlink()
    for name in sorted(DATA_FILES):
        original = directory / name
        compressed = directory / (name + '.gz')
        old = state['files'].get(name)
        if old and old.get('expired'):
            continue
        if old and old.get('compressed') and not original.exists():
            continue
        if not original.exists() and not compressed.exists():
            continue
        if original.exists():
            plain_digest, plain_size = _digest(original)
            if old and (plain_digest, plain_size) != (old['uncompressed_sha256'], old['uncompressed_bytes']):
                raise ValueError(f'original checksum changed: {original}')
        else:
            if old is None:
                raise ValueError(f'compressed file has no verified lifecycle record: {compressed}')
            plain_digest, plain_size = _digest(compressed, decompress=True)
        expected_size = state['manifest'].get('files', {}).get(name, {}).get('bytes')
        if expected_size is not None and expected_size != plain_size:
            raise ValueError(f'closed manifest byte count mismatch: {name}')
        compress_now = settings.compression_enabled
        deferred = None
        if compress_now and not compressed.exists():
            # Keep the original exportable/offloadable if scratch would consume
            # the reserve. A full device can therefore recover via its archive.
            scratch = plain_size + plain_size // 1000 + 65536
            if _usage(root) + scratch > settings.storage_budget_mb * 1024**2:
                compress_now = False
                deferred = 'insufficient storage budget for scratch space'
            elif shutil.disk_usage(root).free < settings.min_free_mb * 1024**2 + scratch:
                compress_now = False
                deferred = 'free-space reserve would be consumed'
        if compress_now or compressed.exists():
            if compressed.exists():
                if _digest(compressed, decompress=True) != (plain_digest, plain_size):
                    raise ValueError(f'compressed checksum mismatch: {compressed}')
            else:
                fd, temporary = tempfile.mkstemp(prefix='.compress-', dir=directory)
                try:
                    with os.fdopen(fd, 'wb') as target:
                        with gzip.GzipFile(fileobj=target, mode='wb', compresslevel=6, mtime=0) as zipped:
                            with _regular(original).open('rb') as source:
                                shutil.copyfileobj(source, zipped, CHUNK)
                        target.flush()
                        os.fsync(target.fileno())
                    if _digest(Path(temporary), decompress=True) != (plain_digest, plain_size):
                        raise ValueError(f'compression verification failed: {original}')
                    os.replace(temporary, compressed)
                    _sync_dir(directory)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            current = compressed
        else:
            current = original
        checksum, size = _digest(current)
        state['files'][name] = {
            'path': current.relative_to(root).as_posix(), 'compressed': current == compressed,
            'bytes': size, 'sha256': checksum, 'uncompressed_bytes': plain_size,
            'uncompressed_sha256': plain_digest, 'expired': False,
            'fingerprint': _fingerprint(current),
            'rows': state['manifest'].get('files', {}).get(name, {}).get('rows'),
            'compression_deferred': deferred,
        }
        # Persist the verified replacement before removing the original.
        _save(directory / 'lifecycle.json', state)
        if current == compressed and original.exists():
            _regular(original).unlink()
            _sync_dir(directory)


def _archive_root(settings, root):
    if settings.archive_dir is None:
        return None
    target = _safe(settings.archive_dir)
    if target == root or target in root.parents or root in target.parents:
        raise ValueError('archive_dir must be separate from data_dir')
    if not target.is_dir():
        raise ValueError('archive_dir is unavailable; its root must already exist')
    return target


def _archive_expected(directory, state):
    result = {'manifest.json': dict(zip(('sha256', 'bytes'), _digest(directory / 'manifest.json')))}
    for name, record in state['files'].items():
        result[name + ('.gz' if record['compressed'] else '')] = {
            'sha256': record['sha256'], 'bytes': record['bytes']}
    return result


def _verify_archive(destination, state, expected):
    _safe(destination)
    metadata = _read(destination / 'archive.json', {})
    if not isinstance(metadata, dict) or metadata.get('id') != state['id'] or metadata.get('files') != expected:
        raise ValueError(f'archive identity/checksum manifest mismatch: {destination}')
    if {p.name for p in destination.iterdir()} != set(expected) | {'archive.json'}:
        raise ValueError(f'unexpected archive members: {destination}')
    for name, record in expected.items():
        if _digest(destination / name) != (record['sha256'], record['bytes']):
            raise ValueError(f'archive checksum mismatch: {destination / name}')


def _invalidate_archive(directory, state, error):
    acknowledgement = state['archive']
    immutable = bool(acknowledgement.get('verified') or acknowledgement.get('immutable'))
    acknowledgement.update(verified=False, immutable=immutable, error=str(error))
    _save(directory / 'lifecycle.json', state)


def _recheck_archive(destination, directory, state, expected):
    try:
        _verify_archive(destination, state, expected)
    except (OSError, ValueError, KeyError) as error:
        _invalidate_archive(directory, state, error)
        raise


def _offload(root, directory, state, archive_root, settings):
    if archive_root is None:
        return
    _check_complete(directory, state)
    destination = _safe(archive_root / state['id'])
    expected = _archive_expected(directory, state)
    if destination.exists():
        _recheck_archive(destination, directory, state, expected)
    else:
        if any(record.get('expired') for record in state['files'].values()):
            raise ValueError('cannot reconstruct full archive after local expiry')
        required = sum(record['bytes'] for record in expected.values()) + 65536
        reserve = settings.min_free_mb * 1024**2 if root.stat().st_dev == archive_root.stat().st_dev else 0
        if shutil.disk_usage(archive_root).free < required + reserve:
            raise OSError('archive deferred: insufficient destination space')
        destination.parent.mkdir(exist_ok=True)
        _sync_dir(archive_root)
        prefix = '.' + destination.name + '.incoming-'
        for previous in destination.parent.glob(prefix + '*'):
            _safe(previous)
            if not previous.is_dir():
                raise ValueError('archive staging path must be a directory')
            for member in previous.iterdir():
                _regular(member)
                if member.name not in set(expected) | {'archive.json'}:
                    raise ValueError('unrecognised archive staging member')
            shutil.rmtree(previous)
        temporary = Path(tempfile.mkdtemp(prefix=prefix, dir=destination.parent))
        try:
            for name in expected:
                with _regular(directory / name).open('rb') as source, (temporary / name).open('xb') as target:
                    shutil.copyfileobj(source, target, CHUNK)
                    target.flush()
                    os.fsync(target.fileno())
            _save(temporary / 'archive.json', {'format_version': 1, 'id': state['id'], 'files': expected})
            _verify_archive(temporary, state, expected)
            os.rename(temporary, destination)
            _sync_dir(destination.parent)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    state['archive'] = {'verified': True, 'immutable': True, 'path': str(destination), 'verified_utc': utc_now()}
    _save(directory / 'lifecycle.json', state)


def _usage(root):
    total = 0
    for directory, directories, names in os.walk(root, followlinks=False):
        for name in directories:
            _safe(Path(directory) / name)
        for name in names:
            try:
                total += _regular(Path(directory) / name).stat().st_size
            except FileNotFoundError:
                # Acquisition can replace its active manifest while we scan.
                continue
    return total


def _prune(settings, root, entries, archive_root, now, usage, free):
    budget = settings.storage_budget_mb * 1024**2
    reserve = settings.min_free_mb * 1024**2
    pressure = usage >= budget * .9 or free < reserve
    blocked = []
    errors = []
    verified = set()
    ordered = sorted(entries, key=lambda entry: _seconds(entry[1]['closed_utc']))
    groups = ((DIAGNOSTICS, settings.diagnostic_retention_days),
              (MEASUREMENTS | REPLAY_FILES, settings.measurement_retention_days),
              (SUMMARIES, settings.summary_retention_days))
    for group, days in groups:
        for directory, state in ordered:
            age_expired = now - _seconds(state['closed_utc']) >= days * 86400
            for name in sorted(group & state['files'].keys()):
                record = state['files'][name]
                path = _member(root, directory, name, record)
                need_space = pressure and (usage > budget * .8 or free < reserve) and group != SUMMARIES
                if not record.get('expired') and not age_expired and not need_space:
                    continue
                if not path.exists() and record.get('expired'):
                    continue
                if archive_root is None or not state['archive'].get('verified'):
                    blocked.append(state['id'])
                    continue
                try:
                    expected_destination = archive_root / state['id']
                    if state['archive'].get('path') != str(expected_destination):
                        raise ValueError('archive destination changed; verification required')
                    if state['id'] not in verified:
                        _recheck_archive(expected_destination, directory, state, _archive_expected(directory, state))
                        verified.add(state['id'])
                    size = _regular(path).stat().st_size
                    record.update(expired=True, expired_utc=utc_now(), expiry_reason='age' if age_expired else 'storage_budget')
                    # Commit expiry first: a crash can leave an extra copy, never an unexplained missing one.
                    _save(directory / 'lifecycle.json', state)
                    path.unlink()
                    _sync_dir(directory)
                    usage -= size
                    free += size
                except (OSError, ValueError, KeyError) as error:
                    errors.append(str(error))
                    blocked.append(state['id'])
    return sorted(set(blocked)), errors


def _finish_retired(directory, archive_root):
    """Finish an interrupted metadata retirement; never recurse into unknown files."""
    _safe(directory)
    names = {member.name for member in directory.iterdir()}
    if not names:
        directory.rmdir()
        _sync_dir(directory.parent)
        return
    allowed = {'manifest.json', 'lifecycle.json', '.retirement.json'}
    if not names <= allowed:
        raise ValueError('retired segment contains unexpected files')
    for member in directory.iterdir():
        _regular(member)
    marker = _read(directory / '.retirement.json', {})
    identity = marker.get('id')
    expected_identity = directory.parent.name + '/' + directory.name.removeprefix('.expired-')
    if identity != expected_identity or archive_root is None:
        raise ValueError('retired segment requires its verified archive')
    expected = marker.get('files', {})
    allowed_archive = {'manifest.json'} | DATA_FILES | {name + '.gz' for name in DATA_FILES}
    if not expected or not expected.keys() <= allowed_archive:
        raise ValueError('invalid retirement archive metadata')
    _verify_archive(archive_root / identity, {'id': identity}, expected)
    # The marker is removed last; a crash leaves either a recoverable marker or
    # an empty directory. Archive acknowledgement remains independently durable.
    for name in ('manifest.json', 'lifecycle.json'):
        (directory / name).unlink(missing_ok=True)
    _sync_dir(directory)
    (directory / '.retirement.json').unlink()
    _sync_dir(directory)
    directory.rmdir()
    _sync_dir(directory.parent)


def _retire_segments(settings, root, entries, archive_root, now):
    removed, errors = set(), []
    cleanup_sessions = set()
    if archive_root is None:
        return removed, errors
    for session in root.iterdir():
        if session.is_symlink() or not session.is_dir():
            continue
        for directory in session.glob('.expired-segment-*'):
            try:
                if not re.fullmatch(r'\.expired-segment-\d{6,}', directory.name):
                    continue
                _finish_retired(directory, archive_root)
                cleanup_sessions.add(directory.parent)
            except (OSError, ValueError, KeyError) as error:
                errors.append(f'{directory.name}: {error}')
    for directory, state in entries:
        if now - _seconds(state['closed_utc']) < settings.summary_retention_days * 86400:
            continue
        if not state['files'] or not all(record.get('expired') for record in state['files'].values()):
            continue
        try:
            if state['archive'].get('path') != str(archive_root / state['id']):
                raise ValueError('retirement requires the original verified archive')
            names = {member.name for member in directory.iterdir()}
            if not names <= {'manifest.json', 'lifecycle.json', '.retirement.json'}:
                raise ValueError('retirement deferred: unexpected local members')
            expected = _archive_expected(directory, state)
            _recheck_archive(archive_root / state['id'], directory, state, expected)
            _save(directory / '.retirement.json', {'id': state['id'], 'files': expected})
            retired = directory.with_name('.expired-' + directory.name)
            _safe(retired)
            if retired.exists():
                raise ValueError('retirement staging directory already exists')
            os.rename(directory, retired)
            _sync_dir(directory.parent)
            removed.add(state['id'])
            _finish_retired(retired, archive_root)
            cleanup_sessions.add(directory.parent)
        except (OSError, ValueError, KeyError) as error:
            errors.append(f'{directory.name}: {error}')
    # Empty session directories contain no identity or data worth retaining.
    for session in cleanup_sessions:
        if session.is_dir() and not session.is_symlink():
            try:
                session.rmdir()
                _sync_dir(root)
            except OSError:
                pass
    return removed, errors


def analysis_consumed(root, directory, manifest):
    """Never compress, archive or prune replay input ahead of its saved cursor."""
    if manifest.get('analysis_input_version') != 1 or 'ANALYSIS.jsonl' not in manifest.get('files', {}):
        return True
    limit = manifest.get('committed_bytes', {}).get('ANALYSIS.jsonl')
    if limit == 0 and manifest['files']['ANALYSIS.jsonl'].get('bytes') == 0:
        return True
    if type(limit) is not int or limit < 0:
        return False
    path = root / 'observatory-history.sqlite3'
    try:
        connection = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=.05)
        try:
            row = connection.execute('SELECT offset FROM analysis_offsets WHERE path=?',
                                     (directory.relative_to(root).as_posix(),)).fetchone()
            return (row[0] if row else 0) >= limit
        finally:
            connection.close()
    except sqlite3.Error:
        return False


def maintain_storage(settings, *, now=None):
    """Perform one pass and publish catalogue/status; errors preserve source data."""
    now = time.time() if now is None else now
    root = _safe(settings.data_dir)
    runtime = _safe(settings.runtime_dir)
    root.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    errors, entries, known_states, blocked = [], [], [], []
    archive_root = None
    with storage_lock(root):
        try:
            previous = _read(runtime / 'storage.json', {})
        except (OSError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        last_attempted = previous.get('last_attempted_segment', '')
        try:
            archive_root = _archive_root(settings, root)
        except (OSError, ValueError) as error:
            errors.append(str(error))
        worked = False
        pending = 0
        analysis_pending = 0
        try:
            segments = sorted(_segments(root, errors), key=lambda item: (
                item[0].relative_to(root).as_posix() <= last_attempted,
                item[0].relative_to(root).as_posix()))
            for directory, manifest in segments:
                if not analysis_consumed(root, directory, manifest):
                    analysis_pending += 1
                    continue
                state = None
                try:
                    state = _load_state(root, directory, manifest)
                    _check_complete(directory, state)
                    if settings.archive_dir is not None and archive_root is None and state['archive'].get('verified'):
                        _invalidate_archive(directory, state, 'configured archive is unavailable')
                    needs_work = not state['files'] or any(
                        name not in state['files'] and ((directory / name).exists() or (directory / (name + '.gz')).exists())
                        for name in DATA_FILES) or (not state['archive'].get('verified') and any(
                        (directory / name).exists() and settings.compression_enabled
                        for name in DATA_FILES if not state['files'].get(name, {}).get('expired')))
                    needs_archive = archive_root is not None and (
                        not state['archive'].get('verified') or state['archive'].get('path') != str(archive_root / state['id']))
                    if needs_work or needs_archive:
                        pending += 1
                        if not worked:
                            worked = True
                            last_attempted = state['id']
                            _compress(root, directory, state, settings)
                            _offload(root, directory, state, archive_root, settings)
                            pending -= 1
                except (OSError, ValueError, KeyError, EOFError) as error:
                    errors.append(f'{directory.name}: {error}')
                finally:
                    if state:
                        known_states.append(state)
                        expected = set(state['manifest'].get('files', {})) or (MEASUREMENTS | DIAGNOSTICS)
                        if expected <= state['files'].keys():
                            entries.append((directory, state))
        except (OSError, ValueError, KeyError) as error:
            errors.append(str(error))
        usage = _usage(root)
        free = shutil.disk_usage(root).free
        blocked, prune_errors = _prune(settings, root, entries, archive_root, now, usage, free)
        errors.extend(prune_errors)
        retired, retire_errors = _retire_segments(settings, root, entries, archive_root, now)
        errors.extend(retire_errors)
        entries = [(directory, state) for directory, state in entries if state['id'] not in retired]
        known_states = [state for state in known_states if state['id'] not in retired]
        _save(root / 'catalogue.json', {'format_version': 1, 'updated_utc': utc_now(),
                                       'segments': [state for _, state in entries]})
        usage = _usage(root)
        free = shutil.disk_usage(root).free
        unarchived = [state for state in known_states if not state['archive'].get('verified')]
        total_duration = 0
        sampled_bytes = 0
        sampled_rows = 0
        for _, state in entries:
            manifest = state['manifest']
            first, last = manifest.get('first_record_ts_ms'), manifest.get('last_record_ts_ms')
            if isinstance(first, (int, float)) and isinstance(last, (int, float)):
                duration = max(0, (last - first) / 1000)
            elif state.get('started_utc') and not manifest.get('host_clock_discontinuities'):
                duration = max(0, _seconds(state['closed_utc']) - _seconds(state['started_utc']))
            else:
                continue
            rows = sum((state['files'].get(name, {}).get('rows') or 0) for name in MEASUREMENTS)
            if rows and duration:
                total_duration += duration
                sampled_rows += rows
                sampled_bytes += sum(record['bytes'] for record in state['files'].values())
        # Startup headers, short tests and clock jumps cannot give useful growth
        # predictions. Include every stream's stored bytes once history is useful.
        byte_rate = sampled_bytes / total_duration if total_duration >= 3600 and sampled_rows >= 60 else None
        budget = settings.storage_budget_mb * 1024**2
        available = max(0, min(budget - usage, free - settings.min_free_mb * 1024**2))
        status = {
            'updated_utc': utc_now(), 'usage_bytes': usage, 'free_bytes': free,
            'budget_bytes': budget, 'reserve_bytes': settings.min_free_mb * 1024**2,
            'archive_configured': settings.archive_dir is not None,
            'archive_available': archive_root is not None, 'pending_segments': pending,
            'analysis_pending_segments': analysis_pending,
            'unarchived_segments': len(unarchived), 'last_attempted_segment': last_attempted,
            'oldest_unarchived_utc': min((state['closed_utc'] for state in unarchived), default=None),
            'bytes_per_day_estimate': byte_rate * 86400 if byte_rate else None,
            'growth_sample_seconds': total_duration,
            'growth_estimate_basis': 'all recorded stream bytes in their stored representation; at least one hour and 60 measurement rows',
            'seconds_until_full_estimate': available / byte_rate if byte_rate else None,
            'cleanup_blocked_segments': blocked, 'errors': errors,
            'blocked': bool(blocked or errors or usage >= budget or free < settings.min_free_mb * 1024**2),
        }
        _save(runtime / 'storage.json', status)
        return status


def _worker_loop(settings, config_path, stop):
    while not stop.is_set():
        try:
            settings = load_settings(config_path)
            maintain_storage(settings)
        except Exception as error:
            # Keep the worker alive after an I/O failure, and expose a useful error.
            try:
                _save(_safe(settings.runtime_dir) / 'storage.json', {
                    'updated_utc': utc_now(), 'blocked': True, 'errors': [str(error)],
                    'archive_configured': settings.archive_dir is not None})
            except (OSError, ValueError):
                pass
        stop.wait(settings.storage_interval_seconds)


def run_storage(settings, config_path, stop=None):
    """Separate singleton worker; reload settings and respond promptly to stop."""
    stop = stop or threading.Event()
    runtime = _safe(settings.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    fd = os.open(_safe(runtime / 'storage-worker.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another storage worker is already running') from None
        _worker_loop(settings, config_path, stop)
    finally:
        os.close(fd)
