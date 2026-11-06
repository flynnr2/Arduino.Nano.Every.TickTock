"""Bounded, background phase snapshots using the offline suite's metrology.

Only the current recording segment is read. Never substitute display EWMAs or
nominal-clock durations when PPS coverage is missing. Imports stay in the worker
so acquisition and the basic web dashboard need no scientific dependencies.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from .common import read_json
from .exports import secure_open
from .storage import storage_lock

SWING_LIMIT = 1800
# For the nominal two-second swing, keep the PPS tail less than one 32-bit
# counter wrap longer than the swing tail. A wider window can give two equally
# good hardware-range alignments and exclude every otherwise valid swing.
PPS_LIMIT = 3800
TAIL_BYTES = 2 * 1024**2
REFRESH_SECONDS = 60


def capture_tail(root, path, limit, committed_bytes=None):
    """Read a bounded snapshot, dropping partial first/last lines, not bad rows."""
    with secure_open(root, path) as stream:
        size = os.fstat(stream.fileno()).st_size
        if committed_bytes is not None:
            size = min(size, committed_bytes)
        header = stream.readline(min(size, 4096))
        if not header.endswith(b'\n'):
            raise ValueError('The capture header is incomplete')
        first_line = stream.readline(min(4096, size - len(header)))
        start = max(len(header), size - TAIL_BYTES)
        stream.seek(start)
        raw = stream.read(size - start)
    if start > len(header):
        raw = raw.partition(b'\n')[2]
    raw = raw[:raw.rfind(b'\n') + 1]
    lines = raw.splitlines()
    rows = list(csv.reader(io.StringIO((header + b'\n'.join(lines[-limit:])).decode('ascii')), strict=True))
    names = rows.pop(0)
    if len(set(names)) != len(names) or any(len(row) != len(names) for row in rows):
        raise ValueError('Malformed capture records')
    first = next(csv.reader([first_line.decode('ascii')]), [])
    first_seq = first[names.index('seq')] if 'seq' in names and len(first) == len(names) else None
    return names, rows, start > len(header) or len(lines) > limit, first_seq


def phase_statistics(swing_data, pps_data, nominal_hz):
    """Use exactly the package's validity, calibration, bins and pooled medians."""
    import pandas as pd
    from pendulum_analysis.suite.common import Settings, finite_json
    from pendulum_analysis.suite.swings import REQUIRED, analyze_swings
    from pendulum_analysis.suite.clock import REQUIRED as PPS_REQUIRED
    from pendulum_analysis.pps.timescale import TimescaleConfig, build_timescale

    def frame(data, required):
        names, rows = data[:2]
        if set(required) - set(names):
            raise ValueError('Required capture fields are missing')
        result = pd.DataFrame(rows, columns=names).apply(pd.to_numeric, errors='coerce')
        result.insert(0, 'source_row', range(2, len(result) + 2))
        return result

    swings, pps = frame(swing_data, REQUIRED), frame(pps_data, PPS_REQUIRED)
    if swings.empty or len(pps) < 5:
        return {'charts': [], 'message': 'Waiting for recorded swings and at least five PPS captures.'}
    # The receiver starts a new segment/session on sequence restart. Preserve a
    # sequence wrap that preceded the bounded tail, otherwise advancing the
    # window past the wrap would rotate the charts by 2**32 modulo 15.
    first_seq = pd.to_numeric(swing_data[3], errors='coerce')
    start_seq = swings.seq.iloc[0]
    offset = 0
    if pd.notna(first_seq) and 0 <= first_seq < 2**32 and 0 <= start_seq < first_seq:
        if (start_seq - first_seq) % 2**32 < 2**31:
            offset = 2**32
    steps = swings.seq.diff()
    wraps = (swings.seq.shift() > 2**32 - 65536) & (swings.seq < 65536)
    if ((steps < 0) & ~wraps).any():
        offset = 0  # Only the latest reset epoch will be displayed.
    cfg = Settings(nominal_hz=nominal_hz, phase_origin=-offset)
    scale = build_timescale(pps, TimescaleConfig(nominal_hz=nominal_hz))
    result = analyze_swings(swings, SimpleNamespace(timescale=scale), cfg)
    # Show the latest epoch only, including when it has no eligible records.
    epoch = int(result.frame.epoch.iloc[-1])
    selected = result.frame[result.frame.epoch == epoch]
    table = result.tables['swing_phase']
    totals = result.tables['swing_component_summary']
    totals = totals[(totals.epoch == epoch) & (totals.basis == 'pps_calibrated')]
    charts = []
    for metric, bins, references in (('full', 15, ['full']), ('half', 30, ['tick_half', 'tock_half'])):
        refs = [float(totals[totals.metric == name].iloc[0]['median'])
                if pd.notna(totals[totals.metric == name].iloc[0]['median']) else None
                for name in references]
        subset = table[(table.epoch == epoch) & (table.basis == 'pps_calibrated') &
                       (table.metric == metric) & (table.bin_count == bins)]
        points = []
        for row in subset.to_dict('records'):
            ref = refs[int(row['phase']) % len(refs)]
            points.append(dict(phase=row['phase'], median_s=row['median'], count=row['count'],
                               excluded=row['excluded'], records=row['records'],
                               deviation_us=(row['median'] - ref) * 1e6 if ref is not None else None))
        charts.append(dict(metric=metric, bins=bins, references_s=refs, points=points))
    eligible = int(selected.calibrated_valid.sum())
    return finite_json(dict(charts=charts, epoch=epoch, records=len(selected), eligible=eligible,
        excluded=len(selected) - eligible, first_seq=selected.seq.iloc[0], last_seq=selected.seq.iloc[-1],
        observed_span_seconds=float((selected.elapsed_cycles.iloc[-1] - selected.elapsed_cycles.iloc[0]) / nominal_hz),
        limited=swing_data[2], pps_limited=pps_data[2], calibration=scale.metadata,
        message='' if eligible else 'No swings have complete valid PPS coverage in the latest capture epoch.'))


def build_snapshot(settings, segment):
    # Hold maintenance off only while copying a few MiB; compute after release.
    with storage_lock(settings.data_dir, exclusive=False, blocking=False):
        with secure_open(settings.data_dir, f'{segment}/manifest.json') as stream:
            manifest = json.loads(stream.read(128 * 1024))
        nominal_hz = float(manifest['contract']['cfg']['nhz'])
        swing_data = capture_tail(settings.data_dir, f'{segment}/PCSW.CSV', SWING_LIMIT, manifest.get('committed_bytes', {}).get('PCSW.CSV'))
        pps_data = capture_tail(settings.data_dir, f'{segment}/PCPS.CSV', PPS_LIMIT, manifest.get('committed_bytes', {}).get('PCPS.CSV'))
    data = phase_statistics(swing_data, pps_data, nominal_hz)
    return dict(data, segment=segment, generated_at=time.time(), nominal_hz=nominal_hz,
                committed_capture=manifest.get('committed_capture'))


class PhaseSnapshots:
    """One scheduled job and one cached result per saved-view worker, regardless of client count."""
    def __init__(self):
        self.lock = threading.Lock()
        self.key = None
        self.result = None
        self.error = None
        self.busy = False
        self.last_attempt = float('-inf')

    def get(self, settings):
        status = read_json(settings.runtime_dir / 'status.json', {}) or {}
        recording = status.get('logging', {})
        try:
            # Do not resolve symlinks: secure_open rejects them at every level.
            segment = str(Path(recording['path']).relative_to(settings.data_dir))
            if len(segment.split('/')) != 2:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            return dict(charts=[], state='unavailable', message='Waiting for a recording segment.')
        if 'committed_bytes' in recording:
            from .recording import PCSW_FIELDS, PCPS_FIELDS, csv_line
            committed = recording['committed_bytes']
            if (committed.get('PCSW.CSV', 0) <= len(csv_line(PCSW_FIELDS))
                    or committed.get('PCPS.CSV', 0) <= len(csv_line(PCPS_FIELDS))):
                return dict(charts=[], state='waiting', message='Waiting for committed swing and PPS captures.')
        key = (str(settings.data_dir), segment)
        now = time.monotonic()
        age = now - status.get('updated_monotonic', float('-inf'))
        live = bool(0 <= age <= 5 and recording.get('active') and not status.get('stopped')
                    and not status.get('capture_stale', True))
        with self.lock:
            if key != self.key:
                self.key, self.result, self.error = key, None, None
                self.last_attempt = float('-inf')
            if not self.busy and now - self.last_attempt >= REFRESH_SECONDS:
                self.busy = True
                self.last_attempt = now
                threading.Thread(target=self._build, args=(settings, segment, key), daemon=True,
                                 name='phase-snapshot').start()
            data = dict(self.result or {'charts': []})
            data.update(segment=segment,
                        state='updating' if self.busy else 'unavailable' if self.error else 'ready',
                        updating=self.busy, source=status.get('source', 'unknown'), live=live,
                        refresh_seconds=REFRESH_SECONDS, swing_limit=SWING_LIMIT)
            if self.error:
                data['message'] = self.error
                data['stale'] = True
            return data

    def _build(self, settings, segment, key):
        result, error = None, None
        try:
            result = build_snapshot(settings, segment)
        except ImportError as exc:
            logging.getLogger(__name__).exception('Phase analysis import failed')
            if isinstance(exc, ModuleNotFoundError) and exc.name == 'pendulum_analysis':
                error = 'Swing analysis is not installed. Install the repository analysis package in the web service environment.'
            elif isinstance(exc, ModuleNotFoundError) and exc.name:
                error = (f'Swing analysis cannot load: Python module {exc.name} is missing. '
                         'Install the analysis package and its dependencies in the web service environment.')
            else:
                error = ('Swing analysis could not load. Check the pendulum-views service log '
                         'for the import error; the package or a dependency may need reinstalling.')
        except BlockingIOError:
            error = 'Storage maintenance is running. The phase view will retry shortly.'
        except Exception:
            logging.getLogger(__name__).exception('Phase snapshot unavailable')
            error = 'Phase analysis is unavailable for this recording. Waiting for complete, valid capture files.'
        with self.lock:
            if key == self.key:
                if result is not None:
                    self.result = result
                self.error = error
            self.busy = False
