"""Phase parity, bounded recording reads, and background isolation."""
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from pendulum_pi.common import atomic_json
from pendulum_pi.config import Settings
from pendulum_pi.phase import PPS_LIMIT, SWING_LIMIT, PhaseSnapshots, capture_tail, phase_statistics
from pendulum_analysis.suite.common import Settings as AnalysisSettings
from pendulum_analysis.suite.swings import analyze_swings
from pendulum_analysis.pps.timescale import TimescaleConfig, build_timescale

U32 = 2**32


def captures(n=180):
    # Sequence-dependent period pattern, unequal halves, timer wraps, valid edge chains.
    periods = 32_000_000 + (np.arange(n) % 15 - 7) * 40
    starts = np.r_[16_000_000, 16_000_000 + np.cumsum(periods)]
    swing = pd.DataFrame({'seq': np.arange(n), 'edge0_tcb0': starts[:-1] % U32,
                          'edge1_tcb0': (starts[:-1] + 14_400_000) % U32,
                          'edge2_tcb0': (starts[:-1] + 15_000_000 + np.arange(n) % 15 * 10) % U32,
                          'edge3_tcb0': (starts[1:] - 600_000) % U32,
                          'edge4_tcb0': starts[1:] % U32,
                          'drop_ir': 0, 'drop_pps': 0, 'drop_swing': 0})
    edge = np.arange(n * 2 + 6) * 16_000_000 % U32
    pps = pd.DataFrame({'seq': np.arange(len(edge)), 'edge_tcb0': edge,
                        'gps_status': 2, 'holdover_age_ms': 0, 'cap16': edge % 65536,
                        'latency16': 64, 'now32': (edge + 64) % U32, 'drop_pps': 0})
    return swing, pps


def packed(frame):
    return list(frame.columns), frame.astype(str).values.tolist(), False, str(frame.seq.iloc[0])


def test_medians_and_exclusions_match_offline_suite():
    swing, pps = captures()
    swing = swing.drop([31, 32]).reset_index(drop=True)
    swing.loc[60, 'drop_ir'] = 1
    pps.loc[180:190, 'gps_status'] = 3
    data = phase_statistics(packed(swing), packed(pps), 16_000_000)
    swing.insert(0, 'source_row', range(2, len(swing) + 2))
    reference = analyze_swings(swing, SimpleNamespace(timescale=build_timescale(pps, TimescaleConfig())), AnalysisSettings())
    epoch = reference.frame.epoch.iloc[-1]
    tables = reference.tables['swing_phase']
    totals = reference.tables['swing_component_summary']
    assert data['eligible'] < data['records']
    for chart in data['charts']:
        expected = tables[(tables.basis == 'pps_calibrated') & (tables.epoch == epoch) &
                          (tables.metric == chart['metric']) & (tables.bin_count == chart['bins'])]
        assert len(chart['points']) == chart['bins']
        for point, row in zip(chart['points'], expected.itertuples()):
            assert point['phase'] == row.phase
            assert point['median_s'] == pytest.approx(row.median)
            assert point['count'] == row.count
            name = 'full' if chart['metric'] == 'full' else ('tick_half' if point['phase'] % 2 == 0 else 'tock_half')
            base = totals[(totals.basis == 'pps_calibrated') & (totals.epoch == epoch) & (totals.metric == name)].iloc[0]['median']
            assert point['deviation_us'] == pytest.approx((row.median - base) * 1e6)
    json.dumps(data, allow_nan=False)


def test_no_nominal_fallback_and_empty_bins_are_null():
    swing, pps = captures(3)
    data = phase_statistics(packed(swing), packed(pps), 16_000_000)
    assert len(data['charts'][0]['points']) == 15
    assert data['charts'][0]['points'][10]['median_s'] is None
    pps['gps_status'] = 3
    data = phase_statistics(packed(swing), packed(pps), 16_000_000)
    assert data['eligible'] == 0
    assert all(p['deviation_us'] is None for chart in data['charts'] for p in chart['points'])
    assert data['message']
    json.dumps(data, allow_nan=False)


def test_phase_tail_stays_unambiguous_after_timer_wraps():
    swing, pps = captures(2000)
    # The older 4000-PPS tail spans an additional counter wrap and ties two
    # possible alignments, even though all capture records are valid.
    assert phase_statistics(packed(swing.tail(SWING_LIMIT)), packed(pps.tail(4000)), 16_000_000)['eligible'] == 0
    data = phase_statistics(packed(swing.tail(SWING_LIMIT)), packed(pps.tail(PPS_LIMIT)), 16_000_000)
    assert data['eligible'] == SWING_LIMIT


def test_latest_epoch_not_pooled_and_sequence_wrap_keeps_phase():
    swing, pps = captures(60)
    swing['seq'] = (np.arange(60) + U32 - 20) % U32
    data = phase_statistics(packed(swing), packed(pps), 16_000_000)
    expected = np.bincount((np.arange(60) + U32 - 20) % 15, minlength=15)
    assert [p['records'] for p in data['charts'][0]['points']] == expected.tolist()
    swing.loc[45:, 'seq'] = np.arange(15)
    data = phase_statistics(packed(swing), packed(pps), 16_000_000)
    assert data['records'] == 15
    assert data['epoch'] == 1
    assert data['first_seq'] == 0


def test_snapshot_tail_is_bounded_complete_and_confined(tmp_path, monkeypatch):
    path = tmp_path / 'PCSW.CSV'
    path.write_bytes(b'seq,value\n' + b''.join(f'{i},{i}\n'.encode() for i in range(1000)) + b'1000,partial')
    monkeypatch.setattr('pendulum_pi.phase.TAIL_BYTES', 100)
    names, rows, limited, first = capture_tail(tmp_path, 'PCSW.CSV', 5)
    assert names == ['seq', 'value']
    assert first == '0'
    assert rows == [[str(i), str(i)] for i in range(995, 1000)]
    assert limited
    (tmp_path / 'link.CSV').symlink_to(path)
    with pytest.raises(OSError):
        capture_tail(tmp_path, 'link.CSV', 5)
    with pytest.raises(ValueError):
        capture_tail(tmp_path, '../private', 5)
    path.write_bytes(b'seq,value\n0,1,extra\n')
    with pytest.raises(ValueError):
        capture_tail(tmp_path, 'PCSW.CSV', 5)


def test_background_single_flight_cache_and_session_change(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / 'data', runtime_dir=tmp_path / 'run')
    cache = PhaseSnapshots()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def build(settings, segment):
        calls.append(segment)
        entered.set()
        assert release.wait(5)
        return {'charts': [], 'segment': segment, 'generated_at': time.time()}
    monkeypatch.setattr('pendulum_pi.phase.build_snapshot', build)
    def status(session):
        atomic_json(settings.runtime_dir / 'status.json', {'updated_monotonic': time.monotonic(),
            'capture_stale': False, 'source': 'demo',
            'logging': {'path': str(settings.data_dir / session / 'segment-000001'), 'active': True}})
    status('one')
    assert cache.get(settings)['updating']
    assert entered.wait(2)
    for _ in range(10):
        assert cache.get(settings)['updating']
    assert len(calls) == 1
    status('two')
    assert cache.get(settings)['segment'] == 'two/segment-000001'
    assert not cache.get(settings).get('generated_at')
    release.set()
    deadline = time.monotonic() + 3
    while cache.busy and time.monotonic() < deadline:
        time.sleep(.01)
    assert cache.result is None, 'Old job must not populate the new session'
    cache.get(settings)
    deadline = time.monotonic() + 3
    while cache.busy and time.monotonic() < deadline:
        time.sleep(.01)
    assert cache.get(settings)['segment'] == 'two/segment-000001'
    assert len(calls) == 2


def test_wrap_before_tail_does_not_rotate_phase_as_window_advances():
    swing, pps = captures(20)
    swing['seq'] = np.arange(20) + 20
    data = packed(swing)
    data = (*data[:3], str(U32 - 100))
    result = phase_statistics(data, packed(pps), 16_000_000)
    expected = np.bincount((swing.seq.to_numpy() + U32) % 15, minlength=15)
    assert [p['records'] for p in result['charts'][0]['points']] == expected.tolist()


def test_background_failure_keeps_previous_snapshot_labelled(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / 'data', runtime_dir=tmp_path / 'run')
    cache = PhaseSnapshots()
    key = (str(settings.data_dir), 'one/segment-000001')
    cache.key = key
    cache.result = {'charts': [{'metric': 'full'}], 'generated_at': 123}
    cache.last_attempt = time.monotonic()
    def broken(*args):
        raise ValueError('incomplete capture')
    monkeypatch.setattr('pendulum_pi.phase.build_snapshot', broken)
    cache._build(settings, key[1], key)
    atomic_json(settings.runtime_dir / 'status.json', {'updated_monotonic': time.monotonic(),
        'capture_stale': True, 'logging': {'path': str(settings.data_dir / key[1]), 'active': False}})
    result = cache.get(settings)
    assert result['stale'] and not result['live']
    assert result['generated_at'] == 123
    assert result['charts'] == [{'metric': 'full'}]
    assert result['state'] == 'unavailable'


@pytest.mark.parametrize('error,expected', [
    (ModuleNotFoundError("No module named 'pendulum_analysis'", name='pendulum_analysis'), 'Swing analysis is not installed'),
    (ModuleNotFoundError("No module named 'numpy'", name='numpy'), 'Python module numpy is missing'),
    (ImportError('binary dependency failed to load'), 'could not load'),
])
def test_import_failures_are_identified_and_logged(tmp_path, monkeypatch, caplog, error, expected):
    settings = Settings(data_dir=tmp_path / 'data', runtime_dir=tmp_path / 'run')
    cache = PhaseSnapshots()
    cache.key = (str(settings.data_dir), 'test/segment-000001')
    def unavailable(*args):
        raise error
    monkeypatch.setattr('pendulum_pi.phase.build_snapshot', unavailable)
    cache._build(settings, cache.key[1], cache.key)
    assert expected in cache.error
    assert str(error) in caplog.text
    assert 'Phase analysis import failed' in caplog.text
    assert not cache.busy
