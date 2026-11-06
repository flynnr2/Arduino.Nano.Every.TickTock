"""Append-only coordinated run segments; failed tails are never appended again."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from . import __version__
from .common import atomic_json, read_json, utc_now
from .config import Settings, settings_to_dict
from .protocol import CSW_FIELDS, CPS_FIELDS
from .summaries import MinuteSummaries, SUMMARY_FIELDS

ENV_FIELDS = ['temperature_C', 'humidity_pct', 'pressure_hPa']
# Old firmware exported these diagnostics. Current v3 does not; blank is missing.
PCSW_FIELDS = CSW_FIELDS + ['adj_diag', 'adj_comp_diag'] + ENV_FIELDS
PCPS_FIELDS = CPS_FIELDS + ENV_FIELDS
STS_FIELDS = ['ts_ms', 'raw']
DIAG_FIELDS = ['ts_ms', 'epoch', 'category', 'key', 'value', 'msg']
HEADERS = {'PCSW.CSV': PCSW_FIELDS, 'PCPS.CSV': PCPS_FIELDS,
           'STS.CSV': STS_FIELDS, 'PI.CSV': DIAG_FIELDS, 'SUMMARY.CSV': SUMMARY_FIELDS}
# Reserved for final summary/diagnostic rows and durable metadata when stopping.
CLOSURE_RESERVE = 64 * 1024
ANALYSIS_INPUT = 'ANALYSIS.jsonl'


class StorageLimit(OSError):
    pass


def csv_line(values) -> bytes:
    stream = io.StringIO(newline='')
    csv.writer(stream, lineterminator='\n').writerow(values)
    return stream.getvalue().encode('utf-8')


class Recorder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.origin = time.monotonic()
        self.session = ''
        self.segment = 0
        self.path: Path | None = None
        self.handles = {}
        self.contract = {}
        self.reason = ''
        self.previous_session = None
        self.error = None
        self.last_attempt = float('-inf')
        self.last_sync = self.last_space_check = 0.0
        self.byte_count = 0
        self.files = {}
        self.written = {'CSW': 0, 'CPS': 0}
        self.unrecorded = 0
        self.first_record_utc = self.last_record_utc = None
        self.min_record_utc = self.max_record_utc = None
        self.first_record_ms = self.last_record_ms = None
        self.last_record_epoch = None
        self.host_clock_reversed = False
        self.host_clock_discontinuities = 0
        self.segment_started = None
        self.segment_started_mono = 0.0
        self.free_bytes = None
        self.data_bytes = 0
        self.summaries = MinuteSummaries()
        self.diagnostic = None
        self.committed = {}
        self.written_capture = self.committed_capture = None
        self.last_sync_duration = 0.0
        self.last_space_duration = 0.0

    @property
    def active(self):
        return bool(self.handles) and self.settings.logging_enabled

    def start(self, reason: str, contract: dict | None = None):
        self.close('new_session')
        self.origin = time.monotonic()
        previous = read_json(self.settings.data_dir / 'last_session.json', {})
        self.previous_session = previous.get('session') if isinstance(previous, dict) else None
        self.session = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]
        self.segment = 0
        self.reason = reason
        self.contract = contract or {}
        self.summaries = MinuteSummaries()
        self.last_attempt = float('-inf')
        self.error = None
        self.tick()

    def manifest(self, closed_reason: str | None = None):
        result = {
            'format_version': 2, 'receiver_version': __version__,
            'session': self.session, 'segment': self.segment, 'reason': self.reason,
            'previous_session': self.previous_session, 'started_utc': self.segment_started,
            'first_record_utc': self.first_record_utc, 'last_record_utc': self.last_record_utc,
            'min_record_utc': self.min_record_utc, 'max_record_utc': self.max_record_utc,
            'host_clock_reversed': self.host_clock_reversed,
            'host_clock_discontinuities': self.host_clock_discontinuities,
            'first_record_ts_ms': self.first_record_ms, 'last_record_ts_ms': self.last_record_ms,
            'contract': self.contract, 'files': {name: dict(info) for name, info in self.files.items()},
            'settings': settings_to_dict(self.settings, redact=True),
            'columns': HEADERS, 'written_since_service_start': dict(self.written),
            'unrecorded_since_service_start': self.unrecorded,
            'missing_legacy_columns': ['adj_diag', 'adj_comp_diag'],
            'time_semantics': 'ts_ms is receiver session monotonic elapsed milliseconds; epoch and record coverage are host receipt UTC, not Nano capture time',
            'summary_semantics': 'One-minute session-monotonic bins per recorded capture stream; segment splits may produce partial fragments of the same bin. Environment statistics are capture-weighted; stddev is population. Periods use nominal frequency, not PPS calibration. Missing sequences and drop changes derive from recorded captures; initial drop totals are unknown. Empty minutes have no rows.',
            'committed_bytes': dict(self.committed),
            'committed_capture': self.committed_capture,
            'analysis_input_version': 1,
        }
        if closed_reason:
            result.update(closed_utc=utc_now(), closed_reason=closed_reason)
        return result

    def _manifest(self, closed_reason=None):
        if self.path:
            atomic_json(self.path / 'manifest.json', self.manifest(closed_reason), durable=True)

    def _space(self):
        started = time.monotonic()
        self.free_bytes = shutil.disk_usage(self.settings.data_dir).free
        if self.free_bytes < self.settings.min_free_mb * 1024**2 + CLOSURE_RESERVE:
            raise StorageLimit('minimum free disk space reached; recording paused to preserve existing data')
        total = 0
        for root, directories, names in os.walk(self.settings.data_dir, followlinks=False):
            directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
            for name in names:
                path = Path(root) / name
                try:
                    if not path.is_symlink():
                        total += path.stat().st_size
                except FileNotFoundError:
                    # The independent storage worker can complete a compression or deletion.
                    pass
        self.data_bytes = total
        self.last_space_duration = time.monotonic() - started
        if total + CLOSURE_RESERVE >= self.settings.storage_budget_mb * 1024**2:
            raise StorageLimit('local data storage budget reached; recording paused to preserve unarchived data')

    def _open(self):
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._space()
        self.segment += 1
        self.path = self.settings.data_dir / self.session / f'segment-{self.segment:06d}'
        self.path.mkdir(parents=True, exist_ok=False)
        self.byte_count = 0
        self.files = {name: {'bytes': 0, 'rows': 0} for name in (*HEADERS, 'RAW.jsonl', ANALYSIS_INPUT)}
        self.committed = {name: 0 for name in self.files}
        self.written_capture = self.committed_capture = None
        self.first_record_utc = self.last_record_utc = None
        self.min_record_utc = self.max_record_utc = None
        self.first_record_ms = self.last_record_ms = None
        self.last_record_epoch = None
        self.host_clock_reversed = False
        self.host_clock_discontinuities = 0
        self.segment_started = utc_now()
        self.segment_started_mono = time.monotonic()
        self.diagnostic = None
        self.error = None
        for name, header in HEADERS.items():
            self.handles[name] = (self.path / name).open('xb', buffering=0)
            self._write_exact(name, csv_line(header), row=False)
        self.handles['RAW.jsonl'] = (self.path / 'RAW.jsonl').open('xb', buffering=0)
        self.handles[ANALYSIS_INPUT] = (self.path / ANALYSIS_INPUT).open('xb', buffering=0)
        self._manifest()
        atomic_json(self.settings.data_dir / 'last_session.json', {'session': self.session}, durable=True)
        self.last_sync = self.last_space_check = time.monotonic()
        self.event('session', 'start', self.reason,
                   'New segment. Reboot/disconnection gaps are not replayed. Nano capture continues independently.')

    def _write_exact(self, name, payload, *, row=True):
        written = self.handles[name].write(payload)
        if written != len(payload):
            raise OSError(f'short write in {name}: {written}/{len(payload)} bytes')
        self.byte_count += written
        self.data_bytes += written
        if self.free_bytes is not None:
            self.free_bytes -= written
        self.files[name]['bytes'] += written
        self.files[name]['rows'] += bool(row)

    def _fail(self, error):
        self.error = f'{type(error).__name__}: {error}'
        self.last_attempt = time.monotonic()
        for stream in self.handles.values():
            try:
                stream.close()
            except OSError:
                pass
        self.handles.clear()
        self.diagnostic = None
        self.summaries = MinuteSummaries()
        # Do not publish a closed manifest or append to this possibly partial segment.

    def _at_limit(self):
        return (self.byte_count >= self.settings.segment_bytes
                or any(info['bytes'] >= self.settings.file_bytes for info in self.files.values())
                or time.monotonic() - self.segment_started_mono >= self.settings.segment_seconds)

    def tick(self):
        """Run between input events, never between a raw line and its parsed capture.

        Limits can be exceeded by one bounded input event and final summary/diagnostic
        rows. This preserves event alignment across every file in the set.
        """
        now = time.monotonic()
        if not self.settings.logging_enabled:
            if self.handles:
                self.close('logging_disabled')
            return
        if not self.session:
            return
        if not self.handles:
            if now - self.last_attempt >= 5:
                self.last_attempt = now
                try:
                    self._open()
                except OSError as error:
                    self._fail(error)
            return
        try:
            if now - self.last_space_check >= 5:
                self._space()
                self.last_space_check = now
            self._flush_diagnostic(now, periodic=True)
            if now - self.last_sync >= self.settings.flush_seconds:
                self.sync()
                self.last_sync = now
            if self._at_limit():
                if self.close('rotation'):
                    self._open()
        except StorageLimit as error:
            self.close('storage_limit')
            self._fail(error)
        except OSError as error:
            self._fail(error)

    def write(self, name, payload):
        if not self.active:
            self.unrecorded += 1
            return False
        try:
            if self.data_bytes + len(payload) + CLOSURE_RESERVE >= self.settings.storage_budget_mb * 1024**2:
                raise StorageLimit('local data storage budget reached; recording paused to preserve unarchived data')
            if self.free_bytes is not None and self.free_bytes - len(payload) < self.settings.min_free_mb * 1024**2 + CLOSURE_RESERVE:
                raise StorageLimit('minimum free disk space reached; recording paused to preserve existing data')
            self._write_exact(name, payload)
            return True
        except StorageLimit as error:
            self.unrecorded += 1
            self.close('storage_limit')
            self._fail(error)
            return False
        except OSError as error:
            self.unrecorded += 1
            self._fail(error)
            return False

    def sync(self):
        """Publish a replay frontier only after every coordinated stream is durable."""
        started = time.monotonic()
        for stream in self.handles.values():
            os.fsync(stream.fileno())
        previous = self.committed
        previous_capture = self.committed_capture
        self.committed_capture = self.written_capture
        self.committed = {name: info['bytes'] for name, info in self.files.items()}
        try:
            self._manifest()
        except OSError:
            self.committed = previous
            self.committed_capture = previous_capture
            raise
        self.last_sync_duration = time.monotonic() - started

    def analysis_input(self, value):
        result = self.write(ANALYSIS_INPUT, (json.dumps(value, allow_nan=False,
                          separators=(',', ':')) + '\n').encode())
        if result:
            self.written_capture = {'tag': value['tag'], 'seq': value['values']['seq'],
                                    'monotonic': value['mono'], 'epoch': value['epoch']}
        return result

    def _record_time(self, mono, epoch):
        timestamp = datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='milliseconds')
        elapsed = self.elapsed(mono)
        if self.first_record_utc is None:
            self.first_record_utc, self.first_record_ms = timestamp, elapsed
        self.min_record_utc = timestamp if self.min_record_utc is None else min(timestamp, self.min_record_utc)
        self.max_record_utc = timestamp if self.max_record_utc is None else max(timestamp, self.max_record_utc)
        if self.last_record_epoch is not None:
            host_delta = epoch - self.last_record_epoch
            receipt_delta = (elapsed - self.last_record_ms) / 1000.0
            self.host_clock_reversed |= host_delta < 0
            self.host_clock_discontinuities += abs(host_delta - receipt_delta) > 1.0
        self.last_record_utc, self.last_record_ms = timestamp, elapsed
        self.last_record_epoch = epoch

    def raw(self, raw: bytes, received_mono: float, received_utc: float, *, fragment=False):
        result = self.write('RAW.jsonl', (json.dumps({
            'ts_ms': self.elapsed(received_mono), 'epoch': received_utc,
            'raw_hex': raw.hex(), 'text': raw.decode('ascii', errors='replace'), 'fragment': fragment,
        }, ensure_ascii=True, separators=(',', ':')) + '\n').encode())
        if result:
            self._record_time(received_mono, received_utc)
        return result

    def elapsed(self, now=None):
        return max(0, int(1000 * ((time.monotonic() if now is None else now) - self.origin)))

    def status_line(self, raw: bytes, received_mono: float):
        return self.write('STS.CSV', csv_line([self.elapsed(received_mono), raw.decode('ascii').rstrip('\r\n')]))

    def _flush_diagnostic(self, now, *, periodic=False):
        previous = self.diagnostic
        if not previous or (periodic and now - previous['flushed'] < 60):
            return
        if previous['count']:
            self._write_exact('PI.CSV', csv_line([
                self.elapsed(now), time.time(), 'diagnostic.repeat', previous['identity'][0],
                previous['count'], json.dumps({'event': previous['identity'],
                                               'first_ts_ms': previous['first'],
                                               'last_ts_ms': previous['last']}, separators=(',', ':')),
            ]))
        previous['count'] = 0
        previous['flushed'] = now

    def event(self, category: str, key='', value='', msg='', *, received_mono=None, epoch=None):
        now = time.monotonic() if received_mono is None else received_mono
        identity = [str(category), str(key), str(value), str(msg)]
        if self.active and self.diagnostic and self.diagnostic['identity'] == identity:
            self.diagnostic['count'] += 1
            self.diagnostic['last'] = self.elapsed(now)
            try:
                self._flush_diagnostic(now, periodic=True)
                return True
            except OSError as error:
                self._fail(error)
                return False
        if self.active:
            try:
                self._flush_diagnostic(now)
            except OSError as error:
                self._fail(error)
                return False
        result = self.write('PI.CSV', csv_line([self.elapsed(now), time.time() if epoch is None else epoch,
                                               category, key, value, msg]))
        self.diagnostic = ({'identity': identity, 'count': 0, 'flushed': now,
                            'first': self.elapsed(now), 'last': self.elapsed(now)} if result else None)
        return result

    def _summary_rows(self, rows):
        for row in rows:
            self._write_exact('SUMMARY.CSV', csv_line([self.session, self.segment, *row]))

    def capture(self, tag: str, values: dict, environment: dict,
                received_mono: float | None = None, received_utc: float | None = None) -> bool:
        columns = PCSW_FIELDS if tag == 'CSW' else PCPS_FIELDS
        merged = dict(values)
        for key in ENV_FIELDS:
            value = environment.get(key)
            merged[key] = 'nan' if value is None else f'{value:.2f}'
        result = self.write('PCSW.CSV' if tag == 'CSW' else 'PCPS.CSV',
                            csv_line([merged.get(key, '') for key in columns]))
        if result:
            self.written[tag] += 1
            mono = time.monotonic() if received_mono is None else received_mono
            epoch = time.time() if received_utc is None else received_utc
            self._record_time(mono, epoch)
            try:
                self._summary_rows(self.summaries.observe(tag, values, environment, self.elapsed(mono), epoch,
                    int(self.contract.get('cfg', {}).get('nhz', 0))))
            except OSError as error:
                # The capture itself is recorded; a summary failure abandons this segment.
                self._fail(error)
        return result

    def set_contract(self, contract):
        self.contract = contract
        if self.active:
            try:
                self._manifest()
            except OSError as error:
                self._fail(error)

    def close(self, reason='shutdown'):
        if not self.handles:
            return True
        try:
            self._flush_diagnostic(time.monotonic())
            self._summary_rows(self.summaries.flush(self.elapsed()))
            for stream in self.handles.values():
                os.fsync(stream.fileno())
            self.committed = {name: info['bytes'] for name, info in self.files.items()}
            self.committed_capture = self.written_capture
            for stream in self.handles.values():
                stream.close()
            self.handles.clear()
            # A closed manifest is the storage worker's publication boundary. The
            # writer must never touch these files/manifest after this succeeds.
            self._manifest(reason)
            self.diagnostic = None
            return True
        except OSError as error:
            self._fail(error)
            return False

    def snapshot(self):
        return {'active': self.active, 'enabled': self.settings.logging_enabled,
                'error': self.error, 'session': self.session,
                'segment': self.segment, 'reason': self.reason, 'path': str(self.path) if self.path else None,
                'bytes': self.byte_count, 'written': dict(self.written),
                'unrecorded': self.unrecorded, 'free_bytes': self.free_bytes,
                'data_bytes': self.data_bytes, 'storage_budget_mb': self.settings.storage_budget_mb,
                'flush_seconds': self.settings.flush_seconds,
                'committed_bytes': dict(self.committed),
                'written_capture': self.written_capture, 'committed_capture': self.committed_capture,
                'last_sync_duration_seconds': self.last_sync_duration,
                'last_space_check_duration_seconds': self.last_space_duration}
