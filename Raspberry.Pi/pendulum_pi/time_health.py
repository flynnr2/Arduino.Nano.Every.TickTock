"""Read-only, asynchronous Pi UTC diagnostics; never an event-to-UTC mapping.

CSV fields follow chronyc tracking/sources (chrony 4.x). See
https://chrony-project.org/doc/4.6/chronyc.html for their meaning. A selected
GPS/PPS refid is configuration evidence, not independent GPS validity proof.
"""
from __future__ import annotations

import csv
import math
import os
import subprocess
import threading
import time


def _unknown(error):
    return dict(status='unavailable', source='unknown', selected_source=None,
                selected_mode=None, leap_status=None, reference_age_seconds=None,
                last_offset_seconds=None, estimated_error_seconds=None,
                gps_valid=None, error=error)


def _number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('non-finite chrony value')
    return result


def parse_chrony(tracking: str, sources: str, epoch: float) -> dict:
    """Parse `chronyc -c tracking` and `chronyc -c sources` conservatively.

    Error is chrony's bound conditional on the upstream clock being correct;
    it is the estimate at collection time, not a calibrated UTC guarantee.
    """
    rows = list(csv.reader(tracking.strip().splitlines()))
    if len(rows) != 1 or len(rows[0]) != 14:
        raise ValueError('unexpected chrony tracking response')
    row = rows[0]
    refid, refname = row[:2]
    if len(refid) != 8:
        raise ValueError('invalid chrony reference ID')
    int(refid, 16)
    stratum = int(row[2])
    reference_epoch, correction, offset, rms, frequency, residual, skew, delay, dispersion, interval = map(_number, row[3:13])
    leap = row[13]
    if (not 0 <= stratum <= 16 or min(reference_epoch, rms, skew, delay, dispersion, interval) < 0
            or leap not in {'Normal', 'Insert second', 'Delete second', 'Not synchronised'}):
        raise ValueError('invalid chrony tracking values')
    selected = []
    for source in csv.reader(sources.strip().splitlines()):
        if len(source) != 10 or source[0] not in {'#', '^', '='} or source[1] not in {'*', '+', '-', '?', 'x', '~'}:
            raise ValueError('unexpected chrony sources response')
        if source[1] == '*':
            reach = int(source[5], 8)
            last_rx = _number(source[6])
            if not 0 <= reach <= 255 or last_rx < 0:
                raise ValueError('invalid chrony source freshness')
            selected.append((source[0], source[2], reach, last_rx))
    if len(selected) > 1:
        raise ValueError('multiple selected chrony sources')
    result = _unknown(None)
    age = epoch - reference_epoch if reference_epoch > 0 else None
    result.update(status='not_synchronized', leap_status=leap,
                  reference_age_seconds=age if age is not None and age >= 0 else None)
    # Normal leap status alone can occur during holdover or local-reference mode.
    # Require a current selected source and agreement between the two reports.
    if (leap == 'Not synchronised' or not 1 <= stratum <= 15
            or refid.upper() in {'00000000', '7F7F0101'} or not selected):
        result['error'] = 'No current external synchronization reference'
        return result
    mode, name, reach, last_rx = selected[0]
    if name != refname:
        return _unknown('Chrony source changed during collection')
    if not reach or last_rx == 4294967295 or age is None or age < 0:
        result['error'] = 'External reference is unavailable or its age is unknown'
        return result
    kind = 'NTP' if mode in {'^', '='} else 'GPS/PPS' if name.upper() in {'GPS', 'PPS'} else 'unknown'
    result.update(status='synchronized', source=kind, selected_source=name,
                  selected_mode=mode, last_offset_seconds=offset,
                  estimated_error_seconds=abs(correction) + dispersion + delay / 2,
                  error=None)
    return result


class UTCHealthCollector:
    """One periodic worker and a tiny cached snapshot; no I/O in snapshot()."""

    def __init__(self, interval=10.0, timeout=1.0, stale_after=30.0, *,
                 runner=None, clock=None, wall_clock=None):
        if any(not math.isfinite(value) or value <= 0 for value in (interval, timeout, stale_after)):
            raise ValueError('UTC health intervals must be positive and finite')
        self.interval, self.timeout, self.stale_after = interval, timeout, stale_after
        self._runner = runner or subprocess.run
        self._clock, self._wall_clock = clock or time.monotonic, wall_clock or time.time
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._cached = _unknown('Not yet checked')
        self._sampled = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='pi-utc-health', daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2 * self.timeout + 0.25)
        with self._lock:
            self._cached = _unknown('UTC health worker stopped')
            self._sampled = None

    def _read(self, command):
        response = self._runner(['chronyc', '-n', '-c', command],
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=self.timeout, check=True,
                                env=dict(os.environ, LC_ALL='C'))
        if len(response.stdout) > 65536:
            raise ValueError('chrony response exceeds diagnostic limit')
        return response.stdout

    def _collect(self):
        try:
            tracking = self._read('tracking')
            sources = self._read('sources')
            value = parse_chrony(tracking, sources, self._wall_clock())
        except FileNotFoundError:
            value = _unknown('chronyc is not installed')
        except subprocess.TimeoutExpired:
            value = _unknown('chronyc timed out')
        except subprocess.CalledProcessError:
            value = _unknown('chronyd is unavailable or refused the query')
        except (OSError, ValueError, csv.Error):
            value = _unknown('chrony diagnostics could not be read')
        with self._lock:
            if not self._stop.is_set():
                self._cached, self._sampled = value, self._clock()

    def _run(self):
        while not self._stop.is_set():
            self._collect()
            if self._stop.wait(self.interval):
                break

    def snapshot(self, now=None):
        now = self._clock() if now is None else now
        with self._lock:
            value, sampled = dict(self._cached), self._sampled
        age = now - sampled if sampled is not None else None
        fresh = age is not None and math.isfinite(age) and 0 <= age <= self.stale_after
        if sampled is not None and not fresh:
            value = _unknown('UTC health diagnostic is stale')
        elif fresh and value['reference_age_seconds'] is not None:
            value['reference_age_seconds'] += age
        value.update(fresh=fresh, sample_age_seconds=age if age is not None and math.isfinite(age) else None)
        return value
