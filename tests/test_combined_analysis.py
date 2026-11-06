"""Combine validated epochs without creating any new cross-boundary timing."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pendulum_analysis.combined import (
    _compatible, _load, pool_stability, within_epoch_environment, write_combined_report,
)
from pendulum_analysis.suite.common import fingerprint


def report(tmp_path, name, *, package='swing', anchor=16, utc=1793540000, session=None):
    folder = tmp_path / name
    (folder / 'csv').mkdir(parents=True)
    source = tmp_path / (name + '-input')
    source.mkdir()
    n = 60
    frame = pd.DataFrame(dict(source_row=np.arange(n)+2, seq=np.arange(n)+1,
                              elapsed_cycles=np.arange(n)*32000000))
    role = 'pcsw' if package == 'swing' else 'pcps'
    path = source / (role.upper()+'.CSV')
    path.write_text('distinct source '+name)
    if package == 'swing':
        frame['epoch'] = 0
        frame['calibrated_valid'] = True
        frame.loc[0, 'calibrated_valid'] = False
        frame['full_pps_s'] = 2 + np.arange(n)*1e-6
        frame['phase15'] = np.arange(n) % 15
        frame.to_csv(folder / 'csv/swing_intervals.csv.gz', index=False)
        cycles = pd.DataFrame(dict(epoch=[0, 0], available=[True, True],
                                  last_source_row=[31, 61], series_run=[0, 1],
                                  period_error_us=[15., 45.], time_s=[30., 90.]))
        cycles.to_csv(folder / 'csv/swing_cycles.csv', index=False)
        summary = dict(schema='pendulum-clock-swing-suite.v1', settings={
            'nominal_hz': 16000000, 'swing_period_s': 2., 'profile': 'synchronome'},
            swings={'impulse_alignment': {'epochs': [dict(epoch=0,
                status='aligned' if anchor is not None else 'fallback', anchor_phase30=anchor)]}})
    else:
        frame['segment'] = 0
        frame['frequency_valid'] = True
        frame.loc[0, 'frequency_valid'] = False
        frame['frequency_offset_cycles'] = np.arange(n)*.1
        frame.to_csv(folder / 'csv/intervals.csv.gz', index=False)
        summary = dict(schema='pendulum_analysis.pps.v1', config={'nominal_hz': 16000000}, records=n)
    summary.update(complete=True, provenance={'inputs': {role: fingerprint(path)}})
    if utc is not None:
        pd.DataFrame(dict(source_row=frame.source_row, seq=frame.seq,
            selection_epoch=utc+np.arange(n)*2, utc_basis='recorded receipt UTC',
            source_session=session or name)).to_csv(source / (role.upper()+'_provenance.csv'), index=False)
    (folder / 'summary.json').write_text(json.dumps(summary))
    (folder / 'report.html').write_text('<html>Independent report</html>')
    return folder


def test_combined_swing_preserves_population_runs_gaps_and_independent_anchors(tmp_path):
    a = report(tmp_path, 'a', anchor=16)
    b = report(tmp_path, 'b', anchor=3, utc=1793541000)
    before = [(p, p.read_bytes()) for folder in (a,b) for p in folder.rglob('*') if p.is_file()]
    out = tmp_path / 'combined'
    result = write_combined_report([a,b], out)
    assert result['pooled_statistics']['n'] == 118
    assert result['pooled_statistics']['mean'] == pytest.approx(30.)
    assert [e['original_anchor_half_phase'] for e in result['epochs']] == [16,3]
    assert result['phase_pooling']['eligible_observations'] == 118
    assert result['gaps'][0]['receipt_separation_s'] == pytest.approx(882.)
    assert not result['gaps'][0]['filled']
    timeline = pd.read_csv(out / 'csv/timeline.csv.gz')
    assert timeline.groupby(['analysis_epoch','run']).size().tolist() == [1,1,1,1]
    assert all(p.read_bytes() == content for p,content in before)
    assert (out / 'plots/timeline.png').is_file()
    assert 'a/report.html' in (out / 'report.html').read_text()
    # Generated Markdown tables must also be aligned in the raw source.
    table = [line for line in (out / 'report.md').read_text().splitlines() if line.startswith('|')]
    for block in (table[:4],):
        assert len({tuple(i for i,c in enumerate(line) if c == '|') for line in block}) == 1


def test_fallback_phase_not_pooled_and_missing_utc_not_invented(tmp_path):
    a = report(tmp_path, 'a', anchor=None, utc=None)
    b = report(tmp_path, 'b')
    result = write_combined_report([a,b], tmp_path / 'combined')
    assert result['pooled_statistics']['n'] == 118  # Still usable descriptive timing.
    assert result['phase_pooling']['eligible_epochs'] == 1
    assert result['phase_pooling']['eligible_observations'] == 59
    assert result['epochs'][0]['utc_records'] == 0
    assert any('no accepted impulse' in w for w in result['warnings'])
    assert any('no per-record receipt UTC' in w for w in result['warnings'])
    assert result['gaps'] == []


def test_pps_never_creates_an_interval_between_reports(tmp_path):
    a = report(tmp_path, 'a', package='pps')
    b = report(tmp_path, 'b', package='pps', utc=1793541000)
    result = write_combined_report([a,b], tmp_path / 'combined')
    assert result['pooled_statistics']['n'] == 118
    assert sum(e['excluded'] for e in result['epochs']) == 2
    derived = pd.read_csv(tmp_path / 'combined/csv/combined_intervals.csv.gz')
    assert derived.groupby('analysis_epoch').eligible.sum().tolist() == [59,59]


@pytest.mark.parametrize('error', ['sequence', 'backwards', 'duplicate', 'settings', 'overlap', 'implementation'])
def test_combiner_rejects_invalid_receipt_or_source_identity(tmp_path, error):
    a = report(tmp_path, 'a', session='shared' if error == 'overlap' else None)
    b = report(tmp_path, 'b', session='shared' if error == 'overlap' else None)
    sidecar = tmp_path / 'b-input/PCSW_provenance.csv'
    frame = pd.read_csv(sidecar)
    if error == 'sequence':
        frame.loc[1, 'seq'] += 1
    if error == 'backwards':
        frame.loc[1, 'selection_epoch'] -= 10
    if error == 'duplicate':
        frame.loc[1, 'source_row'] = frame.loc[0, 'source_row']
    frame.to_csv(sidecar,index=False)
    if error == 'settings':
        summary = json.loads((b/'summary.json').read_text())
        summary['settings']['nominal_hz'] = 8000000
        (b/'summary.json').write_text(json.dumps(summary))
    if error == 'implementation':
        for folder, digest in [(a,'old'),(b,'new')]:
            summary = json.loads((folder/'summary.json').read_text())
            summary['provenance']['implementation'] = {'clock.py':digest}
            (folder/'summary.json').write_text(json.dumps(summary))
    with pytest.raises(ValueError):
        _compatible([_load(a,1), _load(b,2)])


def test_source_output_and_incomplete_report_rejected(tmp_path):
    a = report(tmp_path, 'a')
    with pytest.raises(ValueError, match='outside'):
        write_combined_report([a], a)
    with pytest.raises(ValueError, match='distinct'):
        write_combined_report([a,a], tmp_path/'out')
    summary = json.loads((a/'summary.json').read_text()); summary['complete'] = False
    (a/'summary.json').write_text(json.dumps(summary))
    with pytest.raises(ValueError, match='Incomplete'):
        write_combined_report([a], tmp_path/'out')


def test_auxiliary_products_are_hashed_and_changes_abort_publication(tmp_path, monkeypatch):
    from pendulum_analysis import combined
    a = report(tmp_path, 'a')
    assert 'swing_cycles.csv' in _load(a,1)['provenance']
    out = tmp_path/'out';out.mkdir();(out/'report.html').write_text('previous')
    def changed(*_):
        (a/'csv/swing_cycles.csv').write_text('changed during combination')
        return {'complete':True}
    monkeypatch.setattr(combined,'_build',changed)
    with pytest.raises(ValueError,match='changed during combination'):
        write_combined_report([a],out)
    assert (out/'report.html').read_text() == 'previous'


def test_stability_pools_only_existing_squared_difference_support():
    frame = pd.DataFrame(dict(analysis_epoch=['a','b'],averaging_cycles=[1,1],
        tau_s=[30.,30.],adev_ppm=[2.,4.],hdev_ppm=[3.,6.],
        allan_pairs=[3,1],hadamard_triples=[1,3]))
    result = pool_stability(frame).iloc[0]
    assert result.allan_pairs == 4
    assert result.hadamard_triples == 4
    assert result.adev_ppm == pytest.approx(np.sqrt(7))
    assert result.hdev_ppm == pytest.approx(np.sqrt(29.25))
    frame.loc[1,'tau_s'] = 60.
    result = pool_stability(frame).iloc[0]
    assert result.status == 'cadences differ'
    assert 'adev_ppm' not in result


def test_environment_fit_removes_between_epoch_steps_and_validates_inside_epochs():
    x = np.arange(12, dtype=float)
    frame = pd.DataFrame(dict(analysis_epoch=['a']*12+['b']*12,
        hour=np.tile(x,2),temperature_C=np.concatenate([x,100+x]),
        response=np.concatenate([3*x,1000+3*x]),samples=10))
    result = within_epoch_environment(frame,['temperature_C'])
    assert result['slopes']['temperature_C'] == pytest.approx(3)
    assert result['epochs'] == 2
    assert result['train_hours'] == 16
    assert result['test_hours'] == 8
    assert result['test_rmse'] < 1e-10
    frame['temperature_C'] = np.repeat([10.,100.],12)
    assert within_epoch_environment(frame,['temperature_C'])['status'] != 'available'
