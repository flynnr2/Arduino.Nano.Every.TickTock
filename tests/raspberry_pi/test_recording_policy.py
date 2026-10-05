"""Coordinated storage boundaries, durable publication and trend reduction."""
import csv
from dataclasses import replace
import json
from pathlib import Path

import pytest

from pendulum_pi.config import Settings, load_settings, save_settings, update_settings
from pendulum_pi.recording import CLOSURE_RESERVE, HEADERS, Recorder
from pendulum_pi.summaries import MinuteSummaries, SUMMARY_FIELDS


def configuration(tmp_path, **changes):
    return Settings(data_dir=tmp_path/'data', runtime_dir=tmp_path/'runtime', min_free_mb=0, **changes)


def swing(seq=1, *, drop=0, base=0):
    return dict(seq=seq, **{f'edge{i}_tcb0': (base + i * 8_000_000) & 0xffffffff for i in range(5)},
                drop_ir=drop, drop_pps=0, drop_swing=0)


def pps(seq, ticks, drops=0, gps=2):
    return dict(seq=seq, edge_tcb0=ticks & 0xffffffff, drop_pps=drops, gps_status=gps)


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize('limit', ['combined', 'individual', 'age'])
def test_all_files_advance_together_at_each_limit(tmp_path, monkeypatch, limit):
    import pendulum_pi.recording as module
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    recorder = Recorder(configuration(tmp_path))
    recorder.start('test', {'cfg': {'nhz': '16000000'}})
    old = recorder.path
    recorder.raw(b'CSW,row\n', clock[0], 10000)
    if limit == 'combined':
        recorder.byte_count = recorder.settings.segment_bytes
    elif limit == 'individual':
        recorder.files['RAW.jsonl']['bytes'] = recorder.settings.file_bytes
    else:
        clock[0] += recorder.settings.segment_seconds
    # A threshold crossed by raw bytes must not detach the associated measurement.
    assert recorder.capture('CSW', swing(), {}, clock[0], 10000)
    assert recorder.path == old
    recorder.tick()
    assert recorder.path != old
    assert len(rows(old/'PCSW.CSV')) == 1
    assert len(rows(recorder.path/'PCSW.CSV')) == 0
    assert {p.name for p in old.iterdir()} == {*HEADERS, 'RAW.jsonl', 'ANALYSIS.jsonl', 'manifest.json'}
    assert {p.name for p in recorder.path.iterdir()} == {*HEADERS, 'RAW.jsonl', 'ANALYSIS.jsonl', 'manifest.json'}
    assert json.loads((old/'manifest.json').read_text())['closed_reason'] == 'rotation'
    recorder.close()


def test_rotation_uses_monotonic_age_not_midnight(tmp_path, monkeypatch):
    import pendulum_pi.recording as module
    recorder = Recorder(configuration(tmp_path))
    recorder.start('test')
    old = recorder.path
    monkeypatch.setattr(module, 'utc_now', lambda: '2099-12-31T23:59:59+00:00')
    recorder.tick()
    assert recorder.path == old
    recorder.close()


def test_manifest_counts_coverage_and_immutable_closed_publication(tmp_path, monkeypatch):
    recorder = Recorder(configuration(tmp_path))
    recorder.start('test', {'cfg': {'nhz': '16000000'}})
    recorder.raw(b'line\n', recorder.origin+2, 10000)
    recorder.capture('CSW', swing(), {}, recorder.origin+2, 10000)
    published = recorder._manifest
    def check_publish(reason=None):
        if reason:
            assert not recorder.handles
        return published(reason)
    monkeypatch.setattr(recorder, '_manifest', check_publish)
    assert recorder.close()
    path = recorder.path/'manifest.json'
    original = path.read_bytes()
    manifest = json.loads(original)
    assert manifest['first_record_ts_ms'] == manifest['last_record_ts_ms'] == 2000
    assert manifest['first_record_utc'] == manifest['last_record_utc']
    for name, info in manifest['files'].items():
        assert info['bytes'] == (recorder.path/name).stat().st_size
        expected = len((recorder.path/name).read_text().splitlines()) - (name in HEADERS)
        assert info['rows'] == expected
    recorder.set_contract({'cfg': {'nhz': '32000000'}})
    recorder.raw(b'late', recorder.origin+3, 10001)
    recorder.close()
    assert path.read_bytes() == original


def test_failed_fsync_never_marks_segment_closed(tmp_path, monkeypatch):
    import pendulum_pi.recording as module
    recorder = Recorder(configuration(tmp_path))
    recorder.start('test')
    def fail(_):
        raise OSError('sync failed')
    monkeypatch.setattr(module.os, 'fsync', fail)
    assert not recorder.close()
    assert not recorder.active
    assert 'closed_utc' not in json.loads((recorder.path/'manifest.json').read_text())
    assert 'sync failed' in recorder.error


def test_hard_budget_preserves_closed_data_and_can_resume(tmp_path):
    settings = configuration(tmp_path, storage_budget_mb=1)
    recorder = Recorder(settings)
    recorder.start('test')
    old = recorder.path
    recorder.data_bytes = settings.storage_budget_mb * 1024**2 - CLOSURE_RESERVE
    assert not recorder.raw(b'will not fit', recorder.origin, 10000)
    assert not recorder.active
    assert 'budget' in recorder.error
    assert json.loads((old/'manifest.json').read_text())['closed_reason'] == 'storage_limit'
    # Actual usage is small: the next retry rescans it and resumes a fresh set.
    recorder.last_attempt = float('-inf')
    recorder.tick()
    assert recorder.active and recorder.path != old
    recorder.close()


def test_existing_files_count_towards_budget_before_open(tmp_path):
    settings = configuration(tmp_path, storage_budget_mb=1)
    settings.data_dir.mkdir()
    old = settings.data_dir/'existing.bin'
    old.write_bytes(b'x' * (1024**2))
    recorder = Recorder(settings)
    recorder.start('test')
    assert not recorder.active and 'budget' in recorder.error
    assert old.stat().st_size == 1024**2


def test_identical_diagnostics_summarize_repeat_counts_and_preserve_changes(tmp_path, monkeypatch):
    import pendulum_pi.recording as module
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    recorder = Recorder(configuration(tmp_path))
    recorder.start('test')
    for _ in range(11):
        recorder.event('network', 'retry', 'waiting', 'No listener')
    clock[0] += 61
    recorder.tick()
    recorder.event('network', 'retry', 'waiting', 'No listener')
    recorder.event('network', 'retry', 'connected', 'Listener ready')
    recorder.event('health', 'snapshot', '', '{"counter":1}')
    recorder.event('health', 'snapshot', '', '{"counter":2}')
    recorder.close()
    events = rows(recorder.path/'PI.CSV')
    repeats = [row for row in events if row['category'] == 'diagnostic.repeat']
    assert [int(row['value']) for row in repeats] == [10, 1]
    assert sum(row['category'] == 'network' for row in events) == 2
    assert sum(row['category'] == 'health' for row in events) == 2


def test_close_flushes_final_suppressed_diagnostics(tmp_path):
    recorder = Recorder(configuration(tmp_path))
    recorder.start('test')
    for _ in range(5):
        recorder.event('network', 'down')
    recorder.close()
    repeats = [row for row in rows(recorder.path/'PI.CSV') if row['category'] == 'diagnostic.repeat']
    assert len(repeats) == 1 and repeats[0]['value'] == '4'


def summary_dict(row):
    return dict(zip(SUMMARY_FIELDS, ['session', 1, *row]))


def test_summary_statistics_missing_data_wrap_and_partial_flush():
    summary = MinuteSummaries()
    assert not summary.observe('CSW', swing(0xffffffff, base=0xfffffff0),
                               {'temperature_C': 20, 'humidity_pct': None}, 1000, 10001, 16000000)
    assert not summary.observe('CSW', swing(0, base=20),
                               {'temperature_C': 24, 'humidity_pct': float('nan')}, 3000, 10003, 16000000)
    row = summary_dict(summary.observe('CSW', swing(3, drop=2), {}, 61000, 10061, 16000000)[0])
    assert row['partial'] == 0 and row['capture_count'] == 2
    assert row['temperature_C_count'] == 2 and row['temperature_C_mean'] == 22
    assert row['temperature_C_min'] == 20 and row['temperature_C_max'] == 24
    assert row['temperature_C_stddev'] == 2
    assert row['humidity_pct_count'] == 0 and row['humidity_pct_mean'] == ''
    assert row['period_nominal_us_mean'] == 2_000_000
    assert row['baseline_unknown'] == 1 and row['missing_sequences'] == 0
    final = summary_dict(summary.flush(62000)[0])
    assert final['partial'] == 1 and final['missing_sequences'] == 2
    assert final['drop_ir_increase'] == 2 and final['period_invalid_count'] == 1
    assert not summary.flush(63000)


def test_summary_bin_fragments_keep_counts_and_baseline_across_rotation():
    summary = MinuteSummaries()
    summary.observe('CSW', swing(1), {}, 1000, 10001, 16000000)
    first = summary_dict(summary.flush(30000)[0])
    summary.observe('CSW', swing(3), {}, 40000, 10040, 16000000)
    second = summary_dict(summary.observe('CSW', swing(4), {}, 61000, 10061, 16000000)[0])
    assert first['bin_start_ms'] == second['bin_start_ms'] == 0
    assert first['partial'] == second['partial'] == 1
    assert first['capture_count'] + second['capture_count'] == 2
    assert second['missing_sequences'] == 1 and second['baseline_unknown'] == 0


def test_summary_pps_interval_requires_adjacent_locked_unchanged_counters():
    summary = MinuteSummaries()
    for seq, ticks, drop, gps in [(1, 0xfffffff0, 0, 2), (2, 15999984, 0, 2),
                                  (4, 47999984, 0, 2), (5, 63999984, 1, 2),
                                  (6, 79999984, 1, 3)]:
        summary.observe('CPS', pps(seq, ticks, drop, gps), {}, seq*1000, 10000+seq, 16000000)
    row = summary_dict(summary.flush(7000)[0])
    assert row['period_nominal_us_count'] == 1 and row['period_nominal_us_mean'] == 1_000_000
    assert row['period_invalid_count'] == 4 and row['missing_sequences'] == 1
    assert row['drop_pps_increase'] == 1 and row['gps_unlocked_count'] == 1


def test_recorder_flushes_partial_summary_with_final_capture(tmp_path):
    recorder = Recorder(configuration(tmp_path))
    recorder.start('test', {'cfg': {'nhz': '16000000'}})
    recorder.capture('CSW', swing(), {'temperature_C': 20}, recorder.origin, 10000)
    recorder.close()
    records = rows(recorder.path/'SUMMARY.CSV')
    assert len(records) == 1 and records[0]['partial'] == '1'
    assert records[0]['session'] == recorder.session
    assert records[0]['capture_count'] == '1'


@pytest.mark.parametrize('patch', [
    {'segment_seconds': float('nan')}, {'file_bytes': 4*1024**3},
    {'compression_enabled': 1}, {'storage_budget_mb': 0}, {'summary_retention_days': -1},
])
def test_settings_policy_validation(tmp_path, patch):
    with pytest.raises(ValueError):
        update_settings(configuration(tmp_path), patch)


def test_archive_path_roundtrip_and_overlap_validation(tmp_path):
    settings = configuration(tmp_path, archive_dir=tmp_path/'archive')
    config = tmp_path/'config.json'
    save_settings(config, settings)
    assert load_settings(config) == settings
    for bad in (tmp_path, settings.data_dir, settings.data_dir/'archive', settings.runtime_dir/'archive'):
        with pytest.raises(ValueError, match='overlap'):
            update_settings(settings, {'archive_dir': bad}, hot_only=False)
    config.write_text(json.dumps({'data_dir': 'data', 'runtime_dir': 'runtime', 'archive_dir': 'archive'}))
    assert load_settings(config).archive_dir == tmp_path/'archive'


def test_manifest_time_bounds_preserve_host_clock_excursions(tmp_path):
    recorder = Recorder(configuration(tmp_path))
    recorder.start('test')
    for offset, epoch in [(1, 10000), (2, 50000), (3, 5000), (4, 10001)]:
        assert recorder.raw(b'receipt\n', recorder.origin+offset, epoch)
    recorder.close()
    manifest = json.loads((recorder.path/'manifest.json').read_text())
    assert manifest['min_record_utc'] < manifest['first_record_utc']
    assert manifest['max_record_utc'] > manifest['last_record_utc']
    assert manifest['host_clock_reversed'] is True
    assert manifest['host_clock_discontinuities'] == 3
