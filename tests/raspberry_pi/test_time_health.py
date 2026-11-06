"""Chrony monitoring cannot stall capture or turn stale readings into UTC proof."""
import subprocess
import threading
from types import SimpleNamespace

import pytest

from pendulum_pi.time_health import UTCHealthCollector, parse_chrony


TRACKING = '47505300,GPS,1,1000.000000000,0.000002,-0.000003,0.000004,1,0,0.1,0.000010,0.000020,1,Normal\n'
SOURCES = '#,*,GPS,0,0,377,1,-0.000003,-0.000003,0.000010\n^,+,192.0.2.1,2,6,377,20,0.001,0.001,0.01\n'


def test_selected_gps_has_chrony_estimates_but_no_gps_validity_claim():
    health = parse_chrony(TRACKING, SOURCES, 1002)
    assert health['status'] == 'synchronized'
    assert health['source'] == 'GPS/PPS'
    assert health['selected_source'] == 'GPS'
    assert health['selected_mode'] == '#'
    assert health['reference_age_seconds'] == 2
    assert health['last_offset_seconds'] == -0.000003
    assert health['estimated_error_seconds'] == pytest.approx(0.000027)
    assert health['gps_valid'] is None


def test_selected_network_source_is_ntp_even_with_gps_available():
    tracking = TRACKING.replace('47505300,GPS', 'C0000201,192.0.2.1')
    sources = SOURCES.replace('#,*,GPS', '#,+,GPS').replace('^,+,', '^,*,')
    health = parse_chrony(tracking, sources, 1002)
    assert health['status'] == 'synchronized'
    assert health['source'] == 'NTP'
    assert health['selected_source'] == '192.0.2.1'


def test_arbitrary_reference_clock_does_not_imply_gps():
    health = parse_chrony(TRACKING.replace('GPS', 'ATOM'), SOURCES.replace('GPS', 'ATOM'), 1002)
    assert health['status'] == 'synchronized'
    assert health['selected_source'] == 'ATOM'
    assert health['source'] == 'unknown'


@pytest.mark.parametrize('tracking,sources', [
    (TRACKING.replace('Normal', 'Not synchronised'), SOURCES),
    (TRACKING.replace('47505300', '7F7F0101'), SOURCES),
    (TRACKING.replace('47505300', '00000000'), SOURCES),
    (TRACKING, SOURCES.replace('#,*,', '#,?,')),
    (TRACKING, ''),
    (TRACKING, SOURCES.replace('0,0,377,1,', '0,0,0,1,')),
    (TRACKING, SOURCES.replace('0,0,377,1,', '0,0,377,4294967295,')),
    (TRACKING.replace('1000.000000000', '2000.000000000'), SOURCES),
])
def test_no_current_external_reference_is_not_synchronized(tracking, sources):
    health = parse_chrony(tracking, sources, 1002)
    assert health['status'] == 'not_synchronized'
    assert health['source'] == 'unknown'
    assert health['estimated_error_seconds'] is None


def test_source_selection_race_is_unknown():
    health = parse_chrony(TRACKING, SOURCES.replace('GPS', 'PPS'), 1002)
    assert health['status'] == 'unavailable'
    assert health['selected_source'] is None


@pytest.mark.parametrize('tracking,sources', [
    ('506 Cannot talk to daemon', SOURCES),
    (TRACKING.replace('0.000002', 'nan'), SOURCES),
    (TRACKING.replace('0.000020', '-0.1'), SOURCES),
    (TRACKING.replace('Normal', 'Invalid'), SOURCES),
    (TRACKING, SOURCES.replace('^,+,', '^,*,')),
    (TRACKING, 'invalid sources output'),
])
def test_malformed_diagnostics_fail_closed(tracking, sources):
    with pytest.raises(ValueError):
        parse_chrony(tracking, sources, 1002)


def test_queries_are_read_only_bounded_and_cached():
    calls = []
    clock = [10.0]

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=TRACKING if command[-1] == 'tracking' else SOURCES)

    collector = UTCHealthCollector(runner=run, clock=lambda: clock[0], wall_clock=lambda: 1002)
    assert collector.snapshot()['status'] == 'unavailable'
    collector._collect()
    assert [call[0] for call in calls] == [['chronyc', '-n', '-c', 'tracking'], ['chronyc', '-n', '-c', 'sources']]
    for _, kwargs in calls:
        assert kwargs['timeout'] == 1
        assert kwargs['check'] is True
        assert kwargs.get('shell', False) is False
        assert kwargs['env']['LC_ALL'] == 'C'
    clock[0] = 15
    snapshot = collector.snapshot()
    assert snapshot['reference_age_seconds'] == 7
    assert snapshot['sample_age_seconds'] == 5
    snapshot['source'] = 'modified'
    assert collector.snapshot()['source'] == 'GPS/PPS'
    assert len(calls) == 2
    clock[0] = 41
    stale = collector.snapshot()
    assert stale['status'] == 'unavailable'
    assert not stale['fresh']
    assert stale['source'] == 'unknown'
    assert stale['estimated_error_seconds'] is None
    assert collector.snapshot(5)['status'] == 'unavailable'


@pytest.mark.parametrize('error,message', [
    (FileNotFoundError(), 'not installed'),
    (subprocess.TimeoutExpired('chronyc', 1), 'timed out'),
    (subprocess.CalledProcessError(1, 'chronyc'), 'unavailable'),
    (PermissionError(), 'could not be read'),
])
def test_collection_failures_clear_prior_good_state_and_recover(error, message):
    failing = [False]

    def run(command, **kwargs):
        if failing[0]:
            raise error
        return SimpleNamespace(stdout=TRACKING if command[-1] == 'tracking' else SOURCES)

    collector = UTCHealthCollector(runner=run, wall_clock=lambda: 1002)
    collector._collect()
    assert collector.snapshot()['status'] == 'synchronized'
    failing[0] = True
    collector._collect()
    health = collector.snapshot()
    assert health['status'] == 'unavailable'
    assert health['source'] == 'unknown'
    assert message in health['error']
    failing[0] = False
    collector._collect()
    assert collector.snapshot()['status'] == 'synchronized'


def test_blocked_worker_does_not_block_snapshot_or_start_duplicate_queries():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        entered.set()
        assert release.wait(2)
        return SimpleNamespace(stdout=TRACKING if command[-1] == 'tracking' else SOURCES)

    collector = UTCHealthCollector(runner=run, wall_clock=lambda: 1002)
    collector.start()
    try:
        assert entered.wait(1)
        collector.start()
        assert collector.snapshot()['status'] == 'unavailable'
        assert len(calls) == 1
    finally:
        release.set()
        collector.close()
    assert not collector._thread.is_alive()
    assert collector.snapshot()['status'] == 'unavailable'
    assert collector.snapshot()['error'] == 'UTC health worker stopped'


def test_oversized_response_is_unknown():
    collector = UTCHealthCollector(runner=lambda *a, **kw: SimpleNamespace(stdout='x' * 65537))
    collector._collect()
    assert collector.snapshot()['status'] == 'unavailable'
