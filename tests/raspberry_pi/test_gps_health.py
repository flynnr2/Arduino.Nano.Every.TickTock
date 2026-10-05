"""gpsd diagnostics stay bounded, independent, and honest about stale reports."""
from datetime import datetime, timezone
import json
import socket

from pendulum_pi.gps_health import GPSHealthCollector, parse_gpsd_poll, poll_gpsd


EPOCH = 1800000000.0
STAMP = datetime.fromtimestamp(EPOCH - 2, timezone.utc).isoformat().replace('+00:00', 'Z')


def report(**changes):
    value = {'class': 'POLL', 'active': 1,
             'tpv': [{'class': 'TPV', 'device': '/dev/serial0', 'mode': 3,
                      'status': 2, 'time': STAMP}],
             'sky': [{'class': 'SKY', 'device': '/dev/serial0', 'time': STAMP,
                      'nSat': 8, 'uSat': 6, 'hdop': 1.4}]}
    value.update(changes)
    return value


def test_fix_satellites_and_optional_status():
    health = parse_gpsd_poll(report(), EPOCH)
    assert health['status'] == 'available'
    assert (health['fix_mode'], health['fix_status']) == (3, 2)
    assert (health['satellites_used'], health['satellites_visible']) == (6, 8)
    assert health['report_age_seconds'] == 2
    no_status = report()
    del no_status['tpv'][0]['status']
    no_status['sky'][0] = {'class': 'SKY', 'device': '/dev/serial0', 'time': STAMP,
                            'satellites': [{'used': True}, {'used': False}]}
    health = parse_gpsd_poll(no_status, EPOCH)
    assert health['fix_status'] is None
    assert (health['satellites_used'], health['satellites_visible']) == (1, 2)


def test_stale_or_other_device_reports_do_not_claim_current_fix_or_satellites():
    old = datetime.fromtimestamp(EPOCH - 100, timezone.utc).isoformat()
    stale = report()
    stale['tpv'][0]['time'] = old
    stale['sky'][0]['time'] = old
    health = parse_gpsd_poll(stale, EPOCH)
    assert health['status'] == 'stale'
    assert health['fix_mode'] is None and health['satellites_visible'] is None
    mismatched = report()
    mismatched['sky'][0]['device'] = '/dev/other'
    assert parse_gpsd_poll(mismatched, EPOCH)['satellites_used'] is None
    assert parse_gpsd_poll(report(active=0), EPOCH)['status'] == 'unavailable'


def test_optional_sky_time_and_device_keep_counts_with_fresh_receiver_report():
    no_sky_metadata = report()
    del no_sky_metadata['sky'][0]['time']
    del no_sky_metadata['sky'][0]['device']
    health = parse_gpsd_poll(no_sky_metadata, EPOCH)
    assert health['status'] == 'available'
    assert (health['satellites_used'], health['satellites_visible']) == (6, 8)
    assert health['sky_age_seconds'] is None

    clock = [10.0]
    collector = GPSHealthCollector(poller=lambda _: no_sky_metadata,
                                   clock=lambda: clock[0], wall_clock=lambda: EPOCH)
    collector._collect()
    clock[0] = 39
    stale = collector.snapshot()
    assert stale['status'] == 'stale'
    assert stale['satellites_used'] is None

    no_sky_metadata['tpv'][0]['time'] = None
    assert parse_gpsd_poll(no_sky_metadata, EPOCH)['satellites_visible'] is None
    no_sky_metadata['tpv'][0]['time'] = STAMP
    no_sky_metadata['sky'][0]['time'] = 'invalid'
    assert parse_gpsd_poll(no_sky_metadata, EPOCH)['satellites_visible'] is None


def test_partial_sky_does_not_turn_missing_view_into_zero():
    partial = report()
    partial['sky'] = [{'class': 'SKY', 'device': '/dev/serial0', 'uSat': 13,
                       'hdop': 0.77, 'satellites': []}]
    health = parse_gpsd_poll(partial, EPOCH)
    assert health['satellites_used'] == 13
    assert health['satellites_visible'] is None


def test_collector_caches_then_expires_without_affecting_other_time_health():
    clock = [10.0]
    collector = GPSHealthCollector(poller=lambda _: report(), clock=lambda: clock[0],
                                   wall_clock=lambda: EPOCH)
    assert collector.snapshot()['status'] == 'unavailable'
    collector._collect()
    clock[0] = 15
    assert collector.snapshot()['report_age_seconds'] == 7
    assert collector.snapshot()['satellites_used'] == 6
    clock[0] = 41
    stale = collector.snapshot()
    assert stale['status'] == 'unavailable'
    assert stale['satellites_used'] is None
    collector.close()


def test_gpsd_failure_clears_previous_receiver_state():
    failing = [False]
    def poller(_):
        if failing[0]:
            raise ConnectionRefusedError()
        return report()
    collector = GPSHealthCollector(poller=poller, wall_clock=lambda: EPOCH)
    collector._collect()
    assert collector.snapshot()['fix_mode'] == 3
    failing[0] = True
    collector._collect()
    assert collector.snapshot()['status'] == 'unavailable'
    assert collector.snapshot()['fix_mode'] is None


def test_socket_poll_handles_partial_lines_and_stops_at_poll(monkeypatch):
    payload = json.dumps(report()).encode()
    chunks = [b'{"class":"VERSION"}\r\n' + payload[:15],
              payload[15:] + b'\r\n']
    class Connection:
        def __init__(self):
            self.sent = b''
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def sendall(self, value): self.sent += value
        def settimeout(self, value): assert 0 < value <= 7.0
        def recv(self, size): return chunks.pop(0) if chunks else b''
    connection = Connection()
    monkeypatch.setattr(socket, 'create_connection', lambda address, timeout: connection)
    assert poll_gpsd()['class'] == 'POLL'
    assert b'?POLL;' in connection.sent


def test_socket_poll_waits_for_full_sky_after_partial_poll(monkeypatch):
    partial = report()
    partial['sky'] = [{'class': 'SKY', 'device': '/dev/serial0', 'uSat': 13,
                       'satellites': []}]
    full = {'class': 'SKY', 'device': '/dev/serial0', 'nSat': 21, 'uSat': 12,
            'satellites': [{'used': True}] * 12 + [{'used': False}] * 9}
    chunks = [(json.dumps(partial) + '\r\n').encode(),
              (json.dumps({'class': 'SKY', 'uSat': 13}) + '\r\n').encode(),
              (json.dumps(full) + '\r\n').encode()]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def sendall(self, value): pass
        def settimeout(self, value): pass
        def recv(self, size): return chunks.pop(0)
    monkeypatch.setattr(socket, 'create_connection', lambda address, timeout: Connection())
    health = parse_gpsd_poll(poll_gpsd(), EPOCH)
    assert (health['satellites_used'], health['satellites_visible']) == (12, 21)
