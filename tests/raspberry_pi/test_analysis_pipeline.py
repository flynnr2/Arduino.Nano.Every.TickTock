"""Durable replay, atomic recovery and capture-time metrology regression tests."""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
import time

import pytest

from pendulum_pi.analysis import Analysis
from pendulum_pi.common import atomic_json, boot_id
from pendulum_pi.config import Settings
from pendulum_pi.display import DisplayEstimator
from pendulum_pi.history import query_history
from pendulum_pi.recording import ANALYSIS_INPUT
from pendulum_pi.service import Acquisition, environment_snapshot
from pendulum_pi.storage import maintain_storage, analysis_consumed
from pendulum_pi.transport import Input

HZ = 16_000_000
MASK = 0xffffffff


@pytest.fixture
def rig(tmp_path):
    settings = Settings(data_dir=tmp_path/'data', runtime_dir=tmp_path/'run',
                        min_free_mb=0, forecast_cycle_length=15, target_period_s=2)
    acquisition = Acquisition(settings)
    acquisition.reader.connected = True
    acquisition.reader.generation = 1
    acquisition.recorder.start('service_start_serial')
    from pendulum_pi.protocol import SCHEMAS
    rows = [b'CFG,pv=3,nhz=16000000,cst=CSW,css=canonical_swing_v2,cpt=CPS,cps=canonical_pps_v1,fw=test\n']
    rows += [(','.join(['SCH', tag, ident, *fields])+'\n').encode() for tag, (ident, fields) in SCHEMAS.items()]
    for raw in rows:
        acquisition.process(Input('line', raw, 1000., 1_800_000_000., 1))
    yield acquisition
    acquisition.recorder.close()


def captures(app, first, last, reference=None):
    for seq in range(first, last):
        edge = seq * HZ & MASK
        values = dict(seq=seq, edge_tcb0=edge, gps_status=2, holdover_age_ms=0,
                      edge_timer=edge & 65535, latency_ticks=64, isr_tcb0=(edge+64)&MASK, drop_pps=0)
        raw = f'CPS,{seq},{edge},2,0,{edge & 65535},64,{(edge+64)&MASK},0\n'.encode()
        app.process(Input('line', raw, 1000.+seq, 1_800_000_000.+seq, 1))
        if reference: reference.observe('CPS', values, HZ, 1000.+seq, 1_800_000_000.+seq)
        if seq and not seq % 2:
            edges = [((seq-2)*HZ + n) & MASK for n in (0, 15600000, 16000000, 31600000, 32000000)]
            values = dict(seq=seq//2, **{f'edge{i}_tcb0': e for i,e in enumerate(edges)}, drop_ir=0, drop_swing=0, drop_pps=0)
            raw = ('CSW,'+','.join(map(str,[seq//2,*edges,0,0,0]))+'\n').encode()
            app.process(Input('line', raw, 1000.+seq+.001, 1_800_000_000.+seq+.001, 1))
            if reference: reference.observe('CSW', values, HZ, 1000.+seq+.001, 1_800_000_000.+seq+.001)


def catch_up(worker):
    while worker.step() == 128:
        pass


def test_uncommitted_tail_is_not_read_and_delay_does_not_make_history_stale(rig):
    captures(rig, 0, 40)
    assert 'display' not in rig.snapshot()
    worker = Analysis(rig.settings)
    try:
        assert worker.step() == 0
        rig.recorder.sync()
        catch_up(worker)
        assert worker.result['updated_monotonic'] == 1039
        assert worker.result['display']['stale'] is False
        history = query_history(rig.settings.data_dir, 1_800_000_000., 1_800_000_040.)
        assert history['points'][-1]['window_period_s'] == pytest.approx(2.)
        assert not any(p['gap_reason'] == 'stale' for p in history['points'])
    finally:
        worker.close()


def test_restart_preserves_full_causal_state_and_no_history_duplicates(rig):
    reference = DisplayEstimator(forecast_cycle_length=15)
    captures(rig, 0, 310, reference)
    rig.recorder.sync()
    worker = Analysis(rig.settings)
    catch_up(worker)
    before = worker.state()
    count = worker.connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
    worker.close()
    worker = Analysis(rig.settings)
    try:
        assert worker.state() == before
        assert worker.step() == 0
        assert worker.connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == count
        captures(rig, 310, 650, reference)
        rig.recorder.sync()
        catch_up(worker)
        assert worker.display.snapshot(1649) == reference.snapshot(1649)
        assert worker.result['display']['window']['learning'] is False
    finally:
        worker.close()


def test_failed_history_transaction_rolls_back_model_outputs_and_cursor(rig, monkeypatch):
    captures(rig, 0, 5)
    rig.recorder.sync()
    worker = Analysis(rig.settings)
    original = worker.history._summarize
    def fail(*_):
        raise sqlite3.OperationalError('injected disk failure after observation insert')
    before = deepcopy(worker.state())
    monkeypatch.setattr(worker.history, '_summarize', fail)
    try:
        with pytest.raises(sqlite3.OperationalError): worker.step()
        assert worker.state() == before
        assert worker.connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 0
        assert worker.connection.execute('SELECT COUNT(*) FROM analysis_offsets').fetchone()[0] == 0
        monkeypatch.setattr(worker.history, '_summarize', original)
        catch_up(worker)
        assert worker.latest['CSW']['seq'] == 2
    finally:
        worker.close()


def test_storage_pins_input_until_consumed_then_compresses(rig):
    captures(rig, 0, 5)
    path = rig.recorder.path
    rig.recorder.close()
    manifest = json.loads((path/'manifest.json').read_text())
    assert not analysis_consumed(rig.settings.data_dir, path, manifest)
    status = maintain_storage(rig.settings)
    assert status['analysis_pending_segments'] == 1
    assert (path/ANALYSIS_INPUT).exists()
    worker = Analysis(rig.settings)
    try: catch_up(worker)
    finally: worker.close()
    assert analysis_consumed(rig.settings.data_dir, path, manifest)
    status = maintain_storage(rig.settings)
    assert not status['errors']
    assert (path/(ANALYSIS_INPUT+'.gz')).exists()
    worker = Analysis(rig.settings)
    try: assert worker.step() == 0
    finally: worker.close()


def test_sensor_as_of_join_uses_pre_capture_reading(rig, monkeypatch):
    from pendulum_pi import sensor_history
    monkeypatch.setattr(sensor_history, 'boot_id', lambda: 'test-boot')
    history = sensor_history.SensorHistory(rig.settings)
    try:
        for now, temperature in ((100.,20.),(102.,25.)):
            snapshot = dict(updated_monotonic=now, temperature_C=temperature,
                            sht4x={'last_good_monotonic':now}, bmp280={})
            history.append(snapshot)
        atomic_json(rig.settings.runtime_dir/'sensors.json', snapshot)
        result = environment_snapshot(rig.settings, now=101.)
        assert result['temperature_C'] == 20.
        assert result['sht4x']['age_seconds'] == 1.
    finally: history.close()


def test_configuration_change_replays_at_its_saved_capture_frontier(rig):
    captures(rig, 0, 20)
    worker = Analysis(rig.settings)
    try:
        rig.recorder.sync()
        catch_up(worker)
        revision = worker.display.estimate_revision
        forecast_revision = worker.display.forecaster.revision
        rig.settings = replace(rig.settings, target_period_s=2.001, forecast_cycle_length=1)
        captures(rig, 20, 22)
        rig.recorder.sync()
        catch_up(worker)
        assert worker.display.estimate_revision == revision
        assert worker.display.forecaster.revision == forecast_revision + 1
        assert worker.result['display']['target_period_s'] == 2.001
        assert worker.result['display']['window']['gain_seconds_per_day'] == pytest.approx(43.2)
    finally:
        worker.close()


def test_committed_partial_tail_never_advances_progress(rig):
    captures(rig, 0, 2)
    rig.recorder.sync()
    path = rig.recorder.path
    worker = Analysis(rig.settings)
    try:
        catch_up(worker)
        before = deepcopy(worker.state())
        progress = list(worker.connection.execute('SELECT * FROM analysis_offsets'))
        assert rig.recorder.write(ANALYSIS_INPUT, b'{"partial":')
        rig.recorder.sync()
        with pytest.raises(ValueError, match='Incomplete'):
            worker.step()
        assert worker.state() == before
        assert list(worker.connection.execute('SELECT * FROM analysis_offsets')) == progress
    finally:
        worker.close()


def test_phase_tail_obeys_zero_and_partial_commit_frontiers(tmp_path):
    from pendulum_pi.phase import capture_tail
    path = tmp_path/'PCSW.CSV'
    path.write_bytes(b'seq,value\n1,2\n2,3\n')
    with pytest.raises(ValueError, match='header'):
        capture_tail(tmp_path, 'PCSW.CSV', 100, 0)
    names, rows, *_ = capture_tail(tmp_path, 'PCSW.CSV', 100, len(b'seq,value\n1,2\n'))
    assert rows == [['1', '2']]
