"""Bounded, read-only gpsd diagnostics, separate from Pi clock selection."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import socket
import threading
import time


def _unavailable(error):
    return dict(status='unavailable', fix_mode=None, fix_status=None,
                satellites_visible=None, satellites_used=None, hdop=None,
                report_age_seconds=None, sky_age_seconds=None, error=error)


def _integer(value, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        return None
    return value


def _age(value, epoch):
    try:
        if isinstance(value, str):
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                return None
            seconds = stamp.timestamp()
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            seconds = float(value)
        else:
            return None
        age = epoch - seconds
        return max(0.0, age) if math.isfinite(age) and -2 <= age else None
    except (ValueError, OverflowError, OSError):
        return None


def parse_gpsd_poll(report: dict, epoch: float) -> dict:
    """Use one receiver's TPV/SKY; omitted gpsd fields remain unknown."""
    if not isinstance(report, dict) or report.get('class') != 'POLL':
        raise ValueError('unexpected gpsd response')
    if type(report.get('active')) is not int or report['active'] != 1 or not isinstance(report.get('tpv'), list) or not isinstance(report.get('sky'), list):
        return _unavailable('No single active GPS receiver in gpsd')
    tpv = next((item for item in report['tpv'] if isinstance(item, dict) and item.get('class') == 'TPV'), None)
    if tpv is None:
        return _unavailable('gpsd has no receiver report')
    device = tpv.get('device')
    # SKY.device and SKY.time are optional in gpsd's JSON protocol. With one
    # active receiver, an unlabelled SKY belongs to the sole TPV device.
    sky = next((item for item in report['sky'] if isinstance(item, dict) and item.get('class') == 'SKY'
                and (item.get('device') is None or item.get('device') == device)), None)
    mode = _integer(tpv.get('mode'), 3)
    fix_status = _integer(tpv.get('status'), 9)
    report_age = _age(tpv.get('time'), epoch)
    sky_age = _age(sky.get('time'), epoch) if sky else None
    # POLL contains cached reports. Do not present old fixes or sky views as current.
    if report_age is not None and report_age > 30:
        mode, fix_status = None, None
    visible = used = hdop = None
    # When SKY has no timestamp, require a current timestamped TPV from the
    # same sole receiver. The SKY age remains unknown in the published status.
    sky_current = sky_age is not None and sky_age <= 30
    sky_supported_by_tpv = (sky is not None and 'time' not in sky and
                            report_age is not None and report_age <= 30)
    if sky and (sky_current or sky_supported_by_tpv):
        satellites = sky.get('satellites')
        visible = _integer(sky.get('nSat'), 128)
        used = _integer(sky.get('uSat'), 128)
        # A partial SKY report may contain an empty satellites array while
        # uSat is populated. Empty alone does not mean zero in view or used.
        if isinstance(satellites, list) and 0 < len(satellites) <= 128:
            if visible is None:
                visible = len(satellites)
            if used is None:
                used = sum(isinstance(item, dict) and item.get('used') is True for item in satellites)
        candidate = sky.get('hdop')
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            try:
                candidate = float(candidate)
                hdop = candidate if math.isfinite(candidate) and candidate >= 0 else None
            except (OverflowError, ValueError):
                pass
    result = dict(status='available', fix_mode=mode, fix_status=fix_status,
                  satellites_visible=visible, satellites_used=used, hdop=hdop,
                  report_age_seconds=report_age, sky_age_seconds=sky_age, error=None)
    if report_age is not None and report_age > 30:
        result['status'], result['error'] = 'stale', 'gpsd receiver report is stale'
    return result


def _has_sky_view(message):
    return (isinstance(message, dict) and message.get('class') == 'SKY' and
            (_integer(message.get('nSat'), 128) is not None or
             isinstance(message.get('satellites'), list) and bool(message['satellites'])))


def poll_gpsd(timeout=7.0):
    """Poll gpsd, then await a full streamed SKY cycle within the time limit."""
    deadline = time.monotonic() + timeout
    with socket.create_connection(('127.0.0.1', 2947), timeout=timeout) as connection:
        connection.sendall(b'?WATCH={"enable":true,"json":true};?POLL;\n')
        pending = b''
        poll = full_sky = None
        while time.monotonic() < deadline:
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                chunk = connection.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            pending += chunk
            if len(pending) > 65536:
                raise ValueError('gpsd response exceeds diagnostic limit')
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get('class') == 'POLL':
                    poll = message
                    full_sky = next((sky for sky in poll.get('sky', []) if _has_sky_view(sky)), full_sky)
                elif _has_sky_view(message):
                    full_sky = message
                if poll is not None and full_sky is not None:
                    device = next((item.get('device') for item in poll.get('tpv', []) if isinstance(item, dict)), None)
                    if full_sky.get('device') in (None, device):
                        poll['sky'] = [full_sky]
                        return poll
        if poll is not None:
            return poll
    raise ValueError('gpsd did not return a POLL report')


class GPSHealthCollector:
    """Sample gpsd independently so acquisition and HTTP never wait for it."""

    def __init__(self, interval=3.0, timeout=7.0, stale_after=30.0, *,
                 poller=None, clock=None, wall_clock=None):
        if any(not math.isfinite(value) or value <= 0 for value in (interval, timeout, stale_after)):
            raise ValueError('GPS health intervals must be positive and finite')
        self.interval, self.timeout, self.stale_after = interval, timeout, stale_after
        self._poller = poller or poll_gpsd
        self._clock, self._wall_clock = clock or time.monotonic, wall_clock or time.time
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._cached = _unavailable('Not yet checked')
        self._sampled = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='pi-gps-health', daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.timeout + 0.25)
        with self._lock:
            self._cached = _unavailable('GPS health worker stopped')
            self._sampled = None

    def _collect(self):
        try:
            value = parse_gpsd_poll(self._poller(self.timeout), self._wall_clock())
        except (OSError, ValueError, json.JSONDecodeError, UnicodeError):
            value = _unavailable('gpsd diagnostics could not be read')
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
            value = _unavailable('GPS health diagnostic is stale')
        elif fresh:
            for key in ('report_age_seconds', 'sky_age_seconds'):
                if value[key] is not None:
                    value[key] += age
            if value['report_age_seconds'] is not None and value['report_age_seconds'] > self.stale_after:
                value.update(status='stale', fix_mode=None, fix_status=None,
                             satellites_visible=None, satellites_used=None, hdop=None,
                             error='gpsd receiver report is stale')
            if value['sky_age_seconds'] is not None and value['sky_age_seconds'] > self.stale_after:
                value.update(satellites_visible=None, satellites_used=None, hdop=None)
        value.update(fresh=fresh, sample_age_seconds=age if age is not None and math.isfinite(age) else None)
        return value
