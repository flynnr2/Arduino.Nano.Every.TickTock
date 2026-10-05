"""Bounded streaming exports of closed recording sets, preserving provenance.

Ranges refer to receiver wall-clock recording coverage, never verified event UTC.
The caller holds the shared storage lock from planning until response closure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import zlib

COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z')
DATA_NAMES = frozenset({'PCSW.CSV', 'PCPS.CSV', 'STS.CSV', 'PI.CSV', 'RAW.jsonl', 'ANALYSIS.jsonl', 'SUMMARY.CSV'})
KINDS = {'measurements': {'PCSW.CSV', 'PCPS.CSV'}, 'full': DATA_NAMES, 'summary': {'SUMMARY.CSV'}}
TIME_NOTE = ('Selection uses host-recorded UTC coverage, not verified Nano event UTC. '
             'Whole overlapping closed segments are included, so records may lie outside the requested range. '
             'Active segments are excluded. Reversed wall-clock endpoints are selected conservatively and flagged. '
             'Preserve session boundaries, capture-counter wraps, and collection gaps.')


class ExportError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def timestamp(value):
    if not isinstance(value, str):
        raise ExportError('Supply an ISO UTC date or timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if len(value) == 10:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ExportError('Supply dates as YYYY-MM-DD or timestamps with an explicit UTC offset') from None


def date_range(start, end):
    first, last = timestamp(start), timestamp(end)
    if first >= last:
        raise ExportError('The end must be later than the start; the end is exclusive')
    return first, last


def secure_open(root: Path, value: str):
    """Open only regular files beneath real directories, never follow symlinks."""
    parts = value.split('/')
    if not 1 <= len(parts) <= 4 or any(not COMPONENT.fullmatch(p) or p in {'.', '..'} for p in parts):
        raise ExportError('Invalid recording path', 409)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    stream = os.fdopen(fd, 'rb')
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        stream.close()
        raise ExportError('Recording path is not a regular file', 409)
    return stream


def catalogue(root):
    try:
        with secure_open(root, 'catalogue.json') as stream:
            # A corrupt/unbounded catalogue must not exhaust a web worker.
            raw = stream.read(32 * 1024**2 + 1)
        if len(raw) > 32 * 1024**2:
            raise ExportError('Catalogue is too large; archive maintenance is required', 503)
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get('segments'), list):
            raise ValueError()
        return result
    except FileNotFoundError:
        return {'format_version': 1, 'segments': []}
    except (OSError, ValueError) as error:
        if isinstance(error, ExportError):
            raise
        raise ExportError('Catalogue is unavailable or invalid; wait for storage maintenance', 503) from error


def coverage_metadata(segment):
    """V2 extrema preserve excursions that started/closed endpoints alone miss."""
    manifest = segment.get('manifest') or {}
    return {key: segment.get(key, manifest.get(key)) for key in
            ('started_utc', 'last_record_utc', 'closed_utc', 'min_record_utc',
             'max_record_utc', 'host_clock_reversed', 'host_clock_discontinuities')}


def select_segments(data, start, end):
    first, last = date_range(start, end)
    selected = []
    for segment in data['segments']:
        if not isinstance(segment, dict) or not segment.get('closed_utc'):
            continue
        manifest = segment.get('manifest') or segment
        if manifest.get('closed_reason') in {'failed', 'error', 'write_error'} or manifest.get('error'):
            continue
        try:
            coverage = coverage_metadata(segment)
            bounds = [timestamp(coverage[key]) for key in ('started_utc', 'closed_utc')]
            bounds.extend(timestamp(coverage[key]) for key in ('min_record_utc', 'max_record_utc', 'last_record_utc') if coverage[key] is not None)
            # Intermediate wall-clock excursions can exceed the opening/closing endpoints.
            lower, upper = min(bounds), max(bounds)
            if lower < last and upper >= first:
                selected.append(segment)
        except (KeyError, ExportError):
            raise ExportError('Catalogue has invalid coverage; storage maintenance is required', 503) from None
    return sorted(selected, key=lambda s: (s['started_utc'], s['path']))


@dataclass
class Member:
    path: str
    size: int
    content: bytes | None = None

    def header(self):
        info = tarfile.TarInfo(self.path)
        info.size = self.size
        info.mode = 0o644
        info.mtime = 0
        return info.tobuf(format=tarfile.USTAR_FORMAT)


@dataclass
class ExportPlan:
    root: Path
    members: list[Member]
    description: dict

    def chunks(self):
        """A tar.gz stream: no temporary archive, bounded 64 KiB reads."""
        compressor = zlib.compressobj(level=1, wbits=31)
        def raw():
            for member in self.members:
                yield member.header()
                if member.content is not None:
                    yield member.content
                else:
                    with secure_open(self.root, member.path) as stream:
                        if os.fstat(stream.fileno()).st_size != member.size:
                            raise ExportError('Recording changed during download', 409)
                        remaining = member.size
                        while remaining:
                            chunk = stream.read(min(65536, remaining))
                            if not chunk:
                                raise ExportError('Recording was truncated during download', 409)
                            remaining -= len(chunk)
                            yield chunk
                if member.size % 512:
                    yield b'\0' * (512 - member.size % 512)
            yield b'\0' * 1024
        for chunk in raw():
            result = compressor.compress(chunk)
            if result:
                yield result
        yield compressor.flush()


def plan_export(root, data, start, end, kind, max_bytes):
    if kind not in KINDS:
        raise ExportError('Choose measurements, full, or summary')
    segments = select_segments(data, start, end)
    if not segments:
        raise ExportError('No closed segments overlap this range', 404)
    members, provenance = [], []
    identities = set()
    data_members = 0
    for segment in segments:
        base = segment.get('path', '')
        if base in identities:
            raise ExportError('Duplicate segment identity in catalogue', 409)
        identities.add(base)
        parts = base.split('/')
        if len(parts) != 2 or any(not COMPONENT.fullmatch(p) or p in {'.', '..'} for p in parts):
            raise ExportError('Invalid segment path in catalogue', 409)
        present, missing = {}, []
        files = segment.get('files', {})
        for logical in sorted(KINDS[kind]):
            entry = files.get(logical)
            if not entry or entry.get('expired'):
                missing.append(logical)
                continue
            path = entry.get('path')
            if path not in {f'{base}/{logical}', f'{base}/{logical}.gz'}:
                raise ExportError('Invalid file path in catalogue', 409)
            try:
                with secure_open(root, path) as stream:
                    size = os.fstat(stream.fileno()).st_size
            except OSError as error:
                raise ExportError('A catalogued file is unavailable; refresh after storage maintenance', 409) from error
            if size != entry.get('bytes'):
                raise ExportError('A catalogued file changed; refresh after storage maintenance', 409)
            members.append(Member(path, size))
            present[logical] = entry
            data_members += 1
        manifest_path = f'{base}/manifest.json'
        try:
            with secure_open(root, manifest_path) as stream:
                size = os.fstat(stream.fileno()).st_size
            members.append(Member(manifest_path, size))
        except OSError as error:
            raise ExportError('A segment manifest is unavailable', 409) from error
        provenance.append({k: segment.get(k) for k in ('id', 'session', 'segment')})
        provenance[-1].update(coverage_metadata(segment))
        provenance[-1].update(files=present, unavailable_files=missing,
                              host_clock_reversed=bool(provenance[-1]['host_clock_reversed']) or timestamp(segment['closed_utc']) < timestamp(segment['started_utc']))
    if not data_members:
        raise ExportError('No retained files of this kind overlap the range; check the archive', 404)
    description = {'format_version': 1, 'kind': kind, 'requested_start_utc': start,
                   'requested_end_utc_exclusive': end, 'time_semantics': TIME_NOTE,
                   'segment_count': len(segments), 'segments': provenance,
                   'unavailable_file_count': sum(len(p['unavailable_files']) for p in provenance)}
    payload = (json.dumps(description, indent=2, sort_keys=True) + '\n').encode()
    members.insert(0, Member('export.json', len(payload), payload))
    try:
        for member in members:
            member.header()
    except (ValueError, UnicodeError):
        raise ExportError('Recording paths cannot be packaged safely', 409) from None
    tar_bytes = sum(512 + ((m.size + 511) // 512) * 512 for m in members) + 1024
    # Conservative deflate expansion allowance; already compressed inputs stay bounded.
    bound = tar_bytes + tar_bytes // 100 + 65536
    if bound > max_bytes:
        raise ExportError(f'Export may exceed the {max_bytes // 1024**2} MiB download limit. Choose a narrower range or summaries. For a single oversized set, download its files individually or enable compression and wait for maintenance.', 413)
    description.update(source_bytes=sum(m.size for m in members), maximum_download_bytes=bound,
                       download_limit_bytes=max_bytes)
    return ExportPlan(root, members, description)
