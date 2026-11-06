"""Atomic replacement, raw-only replay, archive recovery and live continuation."""
import gzip
import json
import sqlite3

import pytest

from pendulum_pi.analysis import Analysis
from pendulum_pi.common import atomic_json
from pendulum_pi.display import MEAN_MODEL
from pendulum_pi.history import DATABASE, _connect, query_history
from pendulum_pi.rebuild import rebuild_history
from test_analysis_pipeline import rig, captures, catch_up


def old_history(settings):
    connection = _connect(settings.data_dir/DATABASE)
    connection.execute('CREATE TABLE obsolete (value TEXT)')
    connection.execute("INSERT INTO obsolete VALUES ('600-second results')")
    connection.execute("INSERT INTO observations (time,session,segment,source,payload) VALUES (1,'old','old','serial','{}')")
    connection.commit()
    connection.close()


@pytest.mark.parametrize('raw_only', [False, True])
def test_rebuild_replaces_old_results_and_replays_captures(rig, raw_only):
    if raw_only:
        # Use the captures' synthetic clock so this change follows the initial
        # configuration even when the runner's uptime is below 1000 seconds.
        rig.recorder.event('display', 'configuration', msg='{"target_period_s":3}',
                           received_mono=1000.)
    captures(rig, 0, 650)
    rig.recorder.sync()
    if raw_only:
        path = rig.recorder.path/'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['files'].pop('ANALYSIS.jsonl')
        atomic_json(path, manifest)
        # A crash can leave uncommitted CSV rows and zero-filled tails beyond
        # the last durable manifest. Only the committed prefix is replayable.
        for name in ('RAW.jsonl', 'PCSW.CSV', 'PCPS.CSV', 'PI.CSV'):
            with (rig.recorder.path/name).open('ab') as stream:
                stream.write(b'\x00'*128)
    old_history(rig.settings)
    report = rebuild_history(rig.settings, progress=lambda _: None)
    assert report['records'] == 650+324
    assert not report['missing']
    connection = sqlite3.connect(rig.settings.data_dir/DATABASE)
    assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='obsolete'").fetchone()
    assert connection.execute('SELECT MIN(time) FROM observations').fetchone()[0] > 1
    assert connection.execute("SELECT DISTINCT json_extract(payload,'$.settings.estimator_model') FROM observations").fetchall() == [(MEAN_MODEL,)]
    connection.close()
    points = query_history(rig.settings.data_dir, 1_800_000_000., 1_800_000_650.)['points']
    assert points[-1]['settings']['window_swings'] == 300
    assert points[-1]['window_period_s'] == pytest.approx(2.)
    assert not points[-1]['quality']['window_learning']
    assert points[-1]['quality']['pps_status'] == 'LOCKED'
    if raw_only:
        assert points[-1]['settings']['target_period_s'] == 3
        assert points[-1]['window_rate_s_day'] == pytest.approx(43200.)
    if not raw_only:
        worker = Analysis(rig.settings)
        try:
            assert worker.step() == 0
            before = worker.connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
            captures(rig, 650, 670)
            rig.recorder.sync()
            catch_up(worker)
            assert worker.display.window.snapshot(True)['count'] == 300
            assert worker.latest['CSW']['seq'] == 334
            assert worker.connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0] > before
        finally:
            worker.close()


def test_corrupt_input_does_not_replace_existing_database(rig):
    captures(rig, 0, 10)
    rig.recorder.sync()
    old_history(rig.settings)
    with (rig.recorder.path/'ANALYSIS.jsonl').open('r+b') as stream:
        stream.write(b'!')
    with pytest.raises(ValueError):
        rebuild_history(rig.settings, progress=lambda _: None)
    with sqlite3.connect(rig.settings.data_dir/DATABASE) as connection:
        assert connection.execute('SELECT * FROM obsolete').fetchone() == ('600-second results',)


def test_compressed_archive_input_and_uncompressed_frontier(rig):
    from dataclasses import replace
    captures(rig, 0, 650)
    rig.recorder.sync()
    archive = rig.settings.data_dir.parent/'archive'
    target = archive/rig.recorder.path.relative_to(rig.settings.data_dir)
    target.mkdir(parents=True)
    source = rig.recorder.path/'ANALYSIS.jsonl'
    (target/'ANALYSIS.jsonl.gz').write_bytes(gzip.compress(source.read_bytes()))
    source.unlink()
    settings = replace(rig.settings, archive_dir=archive)
    report = rebuild_history(settings, progress=lambda _: None)
    assert not report['missing']
    worker = Analysis(settings)
    try:
        assert worker.step() == 0
        assert worker.result['display']['window']['count'] == 300
    finally:
        worker.close()
