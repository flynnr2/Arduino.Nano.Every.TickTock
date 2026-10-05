"""Rotation must not create fake timing boundaries or join independent sessions."""
import csv
import gzip
import json
import subprocess
import sys

import pytest

from pendulum_analysis.suite.collection import is_collection, run_collection
from pendulum_analysis.suite.common import Settings, discover, read_numeric
from pendulum_analysis.suite.clock import REQUIRED


def segment(root, session, number, seq, *, compressed=False, closed=True, contract=None):
    directory = root / session / f'segment-{number:06d}'
    directory.mkdir(parents=True)
    (directory / 'manifest.json').write_text(json.dumps(dict(
        session=session, segment=number, contract=contract or {'nominal_hz': 16000000},
        closed_utc='2026-09-27T12:00:00Z' if closed else None, closed_reason='rotation')))
    data = {
        'PCPS.CSV': 'seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps\n' +
                    f'{seq},{seq*16000000},2,0,0,97,{seq*16000000+97},0\n',
        'PCSW.CSV': 'seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing\n' +
                    f'{seq},0,1,2,3,4,0,0,0\n',
        'STS.CSV': 'ts_ms,raw\n0,"STS,OK"\n',
    }
    for name, content in data.items():
        if compressed:
            (directory / (name + '.gz')).write_bytes(gzip.compress(content.encode()))
        else:
            (directory / name).write_text(content)
    return directory


def runner(calls):
    def fake_run(source, out, *_):
        with (source / 'PCPS.CSV').open() as stream:
            calls.append([int(row['seq']) for row in csv.DictReader(stream)])
        out.mkdir(parents=True)
        (out / 'report.html').write_text('test report')
        return {'provenance': {}, 'complete': True}
    return fake_run


def test_contiguous_compressed_segments_join_but_sessions_and_missing_segments_do_not(tmp_path):
    root = tmp_path / 'data'
    segment(root, 'session-a', 1, 1)
    segment(root, 'session-a', 2, 2, compressed=True)
    segment(root, 'session-a', 4, 4)
    segment(root, 'session-b', 1, 5)
    segment(root, 'session-b', 2, 6, closed=False)
    calls = []
    result = run_collection(root, tmp_path / 'out', Settings(), False, lambda _: None, runner(calls))
    assert calls == [[1, 2], [4], [5]]
    assert len(result['reports']) == 3
    assert result['excluded'][0]['reason'] == 'segment is active or interrupted'
    provenance = json.loads((tmp_path / 'out/part-0001/summary.json').read_text())['provenance']
    assert [p['rows'] for p in provenance['source_files'] if p['role'] == 'pcps'] == [1, 1]
    assert [p['combined_first_row'] for p in provenance['source_files'] if p['role'] == 'pcps'] == [2, 3]
    assert len(provenance['recording_segments']) == 2


def test_expired_measurements_and_changed_contract_split_groups(tmp_path):
    root = tmp_path / 'data'
    segment(root, 's', 1, 1)
    expired = segment(root, 's', 2, 2)
    (expired / 'PCSW.CSV').unlink()
    segment(root, 's', 3, 3)
    segment(root, 's', 4, 4, contract={'nominal_hz': 8000000})
    calls = []
    result = run_collection(root, tmp_path / 'out', Settings(), False, lambda _: None, runner(calls))
    assert calls == [[1], [3], [4]]
    assert 'expired' in result['excluded'][0]['reason']


def test_collection_detection_and_gzip_single_segment_input(tmp_path):
    directory = segment(tmp_path, 's', 1, 1, compressed=True)
    assert is_collection(tmp_path)
    assert is_collection(tmp_path / 's')
    assert not is_collection(directory)
    inputs = discover(directory / 'PCPS.CSV.gz')
    assert read_numeric(inputs['pcps'], REQUIRED).seq.tolist() == [1]


def test_input_symlink_and_header_changes_are_rejected(tmp_path):
    root = tmp_path / 'data'
    first = segment(root, 's', 1, 1)
    second = segment(root, 's', 2, 2)
    (second / 'PCPS.CSV').write_text('wrong,header\n1,2\n')
    with pytest.raises(ValueError, match='header changed'):
        run_collection(root, tmp_path / 'out', Settings(), False, lambda _: None, runner([]))
    (second / 'PCPS.CSV').unlink()
    (second / 'PCPS.CSV').symlink_to(first / 'PCPS.CSV')
    with pytest.raises(ValueError, match='Unsafe recording'):
        run_collection(root, tmp_path / 'out', Settings(), False, lambda _: None, runner([]))


def test_output_cannot_overwrite_a_segment(tmp_path):
    root = tmp_path / 'data'
    directory = segment(root, 's', 1, 1)
    with pytest.raises(ValueError, match='inside a recording segment'):
        run_collection(root, directory, Settings(), False, lambda _: None, runner([]))


def test_pause_and_failed_closure_are_not_joined(tmp_path):
    root = tmp_path / 'data'
    first = segment(root, 's', 1, 1)
    segment(root, 's', 2, 2)
    failed = segment(root, 's', 3, 3)
    for directory, reason in [(first, 'logging_disabled'), (failed, 'write_error')]:
        manifest = json.loads((directory / 'manifest.json').read_text())
        manifest['closed_reason'] = reason
        (directory / 'manifest.json').write_text(json.dumps(manifest))
    calls = []
    result = run_collection(root, tmp_path / 'out', Settings(), False, lambda _: None, runner(calls))
    assert calls == [[1], [2]]
    assert len(result['excluded']) == 1


def test_collection_runs_real_clock_analysis_across_rotation(tmp_path, monkeypatch):
    from pendulum_analysis.suite.__main__ import run
    from pendulum_analysis.suite import report
    # Exercise real computation; capture derived groups instead of rendering plots.
    computed = {}
    def capture_report(_out, clock, swing, *_):
        computed.update(clock=clock, swing=swing)
        return []
    monkeypatch.setattr(report, 'write_report', capture_report)
    root = tmp_path / 'data'
    for number in (1, 2):
        directory = segment(root, 's', number, 1)
        with (directory / 'PCPS.CSV').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['seq', 'edge_tcb0', 'gps_status', 'holdover_age_ms', 'cap16', 'latency16', 'now32', 'drop_pps'])
            # Rotation at 40 seconds splits the mechanical phase-15..29 group.
            for seq in (range(40) if number == 1 else range(40, 120)):
                edge = seq * 16000016
                writer.writerow([seq, edge, 2, 0, (edge + 8) % 65536, 97, edge + 97, 0])
        with (directory / 'PCSW.CSV').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['seq', *[f'edge{i}_tcb0' for i in range(5)],
                             'drop_ir', 'drop_pps', 'drop_swing'])
            for seq in (range(20) if number == 1 else range(20, 59)):
                edges = [seq * 32000032 + offset for offset in
                         (0, 14000014, 16000016, 30000030, 32000032)]
                writer.writerow([seq, *edges, 0, 0, 0])
    result = run(root, tmp_path / 'out', progress=lambda _: None)
    assert len(result['reports']) == 1
    summary = json.loads((tmp_path / 'out/part-0001/summary.json').read_text())
    assert summary['clock']['offset_ppm'] == pytest.approx(1.0)
    assert summary['clock']['epochs'] == 1
    assert summary['swings']['epochs'] == 1
    assert summary['swings']['pps_calibrated_eligible'] == 59
    assert computed['clock'].frame.loc[40, 'frequency_valid']
    cycles = computed['swing'].tables['swing_cycles'].set_index('cycle')
    assert cycles.loc[1, 'available']
    assert cycles.loc[1, 'first_sequence'] == 15
    assert cycles.loc[1, 'last_sequence'] == 29
    assert len(summary['provenance']['recording_segments']) == 2


def pps_segment(root, session, number, sequences, *, hz=16000000, compressed=False):
    directory = segment(root, session, number, 0,
                        contract={'cfg': {'nhz': hz}})
    (directory / 'PCSW.CSV').unlink()  # Optional for standalone PPS.
    with (directory / 'PCPS.CSV').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['seq', 'edge_tcb0', 'gps_status', 'holdover_age_ms',
                         'cap16', 'latency16', 'now32', 'drop_pps'])
        for seq in sequences:
            edge = seq * hz % 2**32
            writer.writerow([seq, edge, 2, 0, (edge + 8) % 65536, 97,
                             (edge + 97) % 2**32, 0])
    (directory / 'STS.CSV').write_text(f'ts_ms,raw\n0,"STS,nhz={hz}"\n')
    if compressed:
        for path in directory.glob('*.CSV'):
            path.with_suffix('.CSV.gz').write_bytes(gzip.compress(path.read_bytes()))
            path.unlink()
    return directory


def test_standalone_pps_assembles_before_analysis_and_preserves_frequency_inference(tmp_path, monkeypatch):
    from pendulum_analysis.pps import run_analysis
    from pendulum_analysis.pps import report
    monkeypatch.setattr(report, 'make_plots', lambda *_: [])
    root = tmp_path / 'data'
    first = pps_segment(root, 's', 1, range(4), hz=8000000)
    second = pps_segment(root, 's', 2, range(4, 8), hz=8000000, compressed=True)
    original = (second / 'PCPS.CSV.gz').read_bytes()
    result = run_analysis(root, tmp_path / 'out', export_intervals=True,
                          config_overrides={'min_hourly_samples': 2}, progress=lambda _: None)
    assert len(result['reports']) == 1
    summary = json.loads((tmp_path / 'out/part-0001/summary.json').read_text())
    assert summary['records'] == 8
    assert summary['frequency_valid_intervals'] == 7  # Includes rotation boundary.
    assert summary['segments'] == 1
    assert summary['config']['nominal_hz'] == 8000000
    assert summary['config']['min_hourly_samples'] == 2
    assert not summary['swing_association']['available']
    files = [p for p in summary['provenance']['source_files'] if p['role'] == 'pcps']
    assert [p['combined_first_row'] for p in files] == [2, 6]
    assert [p['rows'] for p in files] == [4, 4]
    assert [p['path'] for p in files] == [str(first / 'PCPS.CSV'), str(second / 'PCPS.CSV.gz')]
    assert (tmp_path / 'out/part-0001/csv/intervals.csv.gz').exists()
    assert 'part-0001/report.html' in (tmp_path / 'out/report.html').read_text()
    assert (second / 'PCPS.CSV.gz').read_bytes() == original


def test_standalone_pps_keeps_session_gap_pause_and_contract_boundaries(tmp_path, monkeypatch):
    from pendulum_analysis.pps import run_analysis
    from pendulum_analysis.pps import report
    monkeypatch.setattr(report, 'make_plots', lambda *_: [])
    root = tmp_path / 'data'
    pps_segment(root, 'a', 1, range(4))
    pps_segment(root, 'a', 2, range(4, 8), compressed=True)
    pause = pps_segment(root, 'a', 4, range(8, 12))
    manifest = json.loads((pause / 'manifest.json').read_text())
    manifest['closed_reason'] = 'logging_disabled'
    (pause / 'manifest.json').write_text(json.dumps(manifest))
    pps_segment(root, 'a', 5, range(12, 16))
    pps_segment(root, 'a', 6, range(16, 20), hz=8000000)
    pps_segment(root, 'b', 1, range(20, 24))
    active = pps_segment(root, 'b', 2, range(24, 28))
    manifest = json.loads((active / 'manifest.json').read_text())
    manifest['closed_utc'] = None
    (active / 'manifest.json').write_text(json.dumps(manifest))
    missing = pps_segment(root, 'b', 3, range(28, 32))
    (missing / 'PCPS.CSV').unlink()
    result = run_analysis(root, tmp_path / 'out', progress=lambda _: None)
    assert [(r['session'], r['first_segment'], r['last_segment']) for r in result['reports']] == [
        ('a', 1, 2), ('a', 4, 4), ('a', 5, 5), ('a', 6, 6), ('b', 1, 1)]
    assert len(result['excluded']) == 2


def test_standalone_pps_cli_collection_resolves_settings_per_group(tmp_path):
    root = tmp_path / 'data'
    pps_segment(root, 'a', 1, range(4), hz=8000000)
    pps_segment(root, 'a', 2, range(4, 8), hz=8000000, compressed=True)
    pps_segment(root, 'b', 1, range(4))
    (root / 'catalogue.json').write_text('{}')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'min_hourly_samples': 2}))
    out = tmp_path / 'out'
    completed = subprocess.run([sys.executable, '-m', 'pendulum_analysis.pps.historical_main',
                                str(root / 'catalogue.json'), '--out', str(out),
                                '--config', str(config)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert '12 records in 2 reports' in completed.stdout
    summaries = [json.loads((out / f'part-{i:04d}/summary.json').read_text()) for i in (1, 2)]
    assert [s['config']['nominal_hz'] for s in summaries] == [8000000, 16000000]
    assert all(s['config']['min_hourly_samples'] == 2 for s in summaries)
    assert all(s['complete'] for s in summaries)


def test_standalone_pps_single_gzip_and_explicit_frequency_override(tmp_path, monkeypatch):
    from pendulum_analysis.pps import PpsResult, run_analysis
    from pendulum_analysis.pps import report
    monkeypatch.setattr(report, 'make_plots', lambda *_: [])
    directory = pps_segment(tmp_path / 'data', 'a', 1, range(4), hz=8000000, compressed=True)
    result = run_analysis(directory / 'PCPS.CSV.gz', tmp_path / 'out',
                          config_overrides={'nominal_hz': 16000000})
    assert isinstance(result, PpsResult)
    assert result.config.nominal_hz == 16000000
    assert result.summary['status_metadata']['nhz'] == '8000000'
    assert result.summary['records'] == 4
    assert any('differs from configured' in warning for warning in result.warnings)
