"""Failure/recovery tests around the actual protocol, writer and serial owner."""
import csv
from dataclasses import replace
import json
from pathlib import Path
import queue
import time

import pytest

from pendulum_pi.commands import Commands
from pendulum_pi.common import atomic_json
from pendulum_pi.config import Settings, load_settings, save_settings, update_settings
from pendulum_pi.protocol import Contract, Framer, ProtocolError, SCHEMAS, decode, sequence_step
from pendulum_pi.recording import Recorder
from pendulum_pi.service import Acquisition, environment_snapshot
from pendulum_pi.transport import Input, Reader

CFG = b'CFG,pv=3,nhz=16000000,cst=CSW,css=canonical_swing_v2,cpt=CPS,cps=canonical_pps_v1,fw=test\n'
META = [CFG] + [(','.join(['SCH', tag, ident, *fields]) + '\n').encode() for tag, (ident, fields) in SCHEMAS.items()]
SWING = b'CSW,1,0,15600000,16000000,31600000,32000000,0,0,0\n'
PPS = b'CPS,1,16000000,2,0,9216,64,16000064,0\n'


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path/'logs', runtime_dir=tmp_path/'runtime', min_free_mb=0)


def event(raw, generation=1):
    return Input('line', raw, time.monotonic(), time.time(), generation)


def ready(app):
    for raw in META:
        app.process(event(raw))
    assert app.contract.ready


@pytest.fixture
def acquisition(settings):
    app = Acquisition(settings)
    app.recorder.start('test')
    yield app
    app.recorder.close()


@pytest.mark.parametrize('bad', [b'CSW,1\n', PPS.replace(b',2,',b',4,'), PPS.replace(b'9216',b'65536'),
                                  CFG.replace(b'16000000', b'NaN'), CFG.replace(b'pv=3', b'pv=3,pv=3'),
                                  b'CSW,1,2,3,4,5,6,7,8,-1\n', b'\xff\n'])
def test_invalid_records_rejected(bad):
    with pytest.raises(ProtocolError):
        decode(bad)


def test_framer_preserves_bytes_and_bounds_memory():
    framer = Framer(8)
    source = b'A'*25 + b'\nCPS,1\n'
    frames = framer.feed(source[:3]) + framer.feed(source[3:])
    assert b''.join(raw for raw, _ in frames) == source
    assert all(len(raw) <= 8 for raw, _ in frames)
    assert all(fragment for _, fragment in frames[:-1])
    assert frames[-1] == (b'CPS,1\n', False)
    assert not framer.buffer


def test_metadata_gate_duplicate_wrap_and_unknown_contract(acquisition):
    app = acquisition
    app.process(event(SWING))
    assert app.counters['before_ready'] == 1
    ready(app)
    app.process(event(SWING))
    app.process(event(SWING))
    assert app.counters['accepted_CSW'] == 1
    assert app.counters['duplicates'] == 1
    old_session = app.recorder.session
    app.process(event(b'NEW_TAG,1\n'))
    assert not app.contract.ready
    assert app.recorder.session != old_session
    app.process(event(SWING))
    assert app.counters['accepted_CSW'] == 1
    assert sequence_step(0xffffffff, 0) == ('next',0)
    assert sequence_step(100, 103) == ('gap',2)


def test_malformed_data_does_not_destroy_valid_contract(acquisition):
    ready(acquisition)
    acquisition.process(event(b'CSW,broken\n'))
    assert acquisition.contract.ready
    acquisition.process(event(b'CFG\n'))
    assert not acquisition.contract.ready


def test_contract_change_and_sequence_restart_open_fresh_sessions(acquisition):
    app = acquisition
    ready(app)
    session = app.recorder.session
    app.process(event(CFG.replace(b'fw=test',b'fw=changed')))
    assert app.recorder.session != session
    assert not app.contract.ready
    for raw in META[1:]:
        app.process(event(raw))
    assert app.contract.ready
    app.process(event(SWING.replace(b'CSW,1,', b'CSW,20,')))
    session = app.recorder.session
    app.process(event(SWING))
    assert not app.contract.ready
    assert app.recorder.session != session
    assert app.counters['restarts'] == 1


def test_periodic_replay_metadata_preserves_session_and_duplicates(acquisition):
    ready(acquisition)
    acquisition.process(event(SWING))
    session = acquisition.recorder.session
    ready(acquisition)
    assert acquisition.recorder.session == session
    acquisition.process(event(SWING))
    assert acquisition.counters['duplicates'] == 1


def test_feed_timestamps_follow_accepted_records_and_reset_on_recovery(acquisition):
    app = acquisition
    ready(app)
    assert app.snapshot()['last_swing_received_monotonic'] is None
    assert app.snapshot()['last_pps_received_monotonic'] is None
    app.process(Input('line', SWING, 100., time.time(), 1))
    app.process(Input('line', SWING, 101., time.time(), 1))  # Duplicate.
    app.process(Input('line', b'CSW,broken\n', 102., time.time(), 1))
    app.process(Input('line', PPS, 103., time.time(), 1))
    app.process(Input('line', PPS, 104., time.time(), 1))  # Duplicate.
    snapshot = app.snapshot()
    assert snapshot['last_swing_received_monotonic'] == 100.
    assert snapshot['last_pps_received_monotonic'] == 103.
    app.recover('test_recovery')
    snapshot = app.snapshot()
    assert snapshot['last_swing_received_monotonic'] is None
    assert snapshot['last_pps_received_monotonic'] is None
    app.process(Input('line', SWING, 105., time.time(), 1))  # Metadata required.
    assert app.snapshot()['last_swing_received_monotonic'] is None


def test_repeated_disconnected_attempts_do_not_create_empty_sessions(acquisition):
    app = acquisition
    session = app.recorder.session
    for _ in range(3):
        app.process(Input('disconnected', generation=0, detail='no serial port'))
    assert app.recorder.session == session


def test_recording_pause_is_distinct_from_storage_failure(acquisition):
    ready(acquisition)
    acquisition.settings = replace(acquisition.settings, logging_enabled=False)
    acquisition.recorder.settings = acquisition.settings
    acquisition.recorder.tick()
    acquisition.process(event(SWING))
    assert acquisition.counters['storage_lost_CSW'] == 0
    assert acquisition.counters['not_logged_disabled_CSW'] == 1
    assert acquisition.latest['CSW']['seq'] == 1


def test_sensor_freshness_and_no_future_sample_join(settings, acquisition):
    now = time.monotonic()
    atomic_json(settings.runtime_dir/'sensors.json', {
        'temperature_C':21.25,'humidity_pct':45.0,'pressure_hPa':1000.0,
        'sht4x':{'ok':True,'last_good_monotonic':now-1},
        'bmp280':{'ok':False,'last_good_monotonic':now-20}})
    env = environment_snapshot(settings,now)
    assert env['temperature_C'] == 21.25 and env['pressure_hPa'] is None
    ready(acquisition)
    acquisition.process(Input('line',SWING,now-2,time.time(),1))
    acquisition.recorder.close()
    rows=list(csv.DictReader((acquisition.recorder.path/'PCSW.CSV').open()))
    assert rows[0]['temperature_C'] == 'nan'


@pytest.mark.parametrize('device', ['bmp280', 'sht4x'])
def test_sensor_published_during_snapshot_does_not_split_history(settings, acquisition, monkeypatch, device):
    from pendulum_pi import service

    ready(acquisition)
    clock = {'now': time.monotonic(), 'publish': False}
    sensor_path = settings.runtime_dir / 'sensors.json'
    sensors = {'temperature_C': 21.25, 'humidity_pct': 45.0, 'pressure_hPa': 1000.0,
               'sht4x': {'ok': True, 'last_good_monotonic': clock['now'] - 1},
               'bmp280': {'ok': True, 'last_good_monotonic': clock['now'] - 1}}
    original_read = service.read_json

    def read_with_sensor_publication(path, default):
        if path != sensor_path:
            return original_read(path, default)
        if clock['publish']:
            # The sensor writer publishes after the status snapshot begins,
            # before its sensors.json read completes (the observed 0.72 ms race).
            clock['now'] += 0.00072
            sensors[device]['last_good_monotonic'] = clock['now']
        return sensors

    monkeypatch.setattr(service.time, 'monotonic', lambda: clock['now'])
    monkeypatch.setattr(service, 'read_json', read_with_sensor_publication)
    from pendulum_pi.history import HistoryWriter
    history = HistoryWriter(settings)
    before = history._point(acquisition.snapshot(), settings)
    clock['publish'] = True
    snapshot = acquisition.snapshot()
    env = snapshot['environment']
    assert env[device]['fresh'] is True
    assert env[device]['age_seconds'] >= 0
    assert env[device]['last_good_monotonic'] <= snapshot['updated_monotonic']
    assert env['pressure_hPa'] == 1000.0
    assert env['temperature_C'] == 21.25
    after = history._point(snapshot, settings)
    assert after['segment'] == before['segment']
    assert 'environment_quality_changed' not in after['boundary']


@pytest.mark.parametrize('age', [-1.0, 20.0])
def test_sensor_snapshot_still_rejects_future_and_stale_samples(settings, monkeypatch, age):
    from pendulum_pi import service

    atomic_json(settings.runtime_dir / 'sensors.json', {
        'pressure_hPa': 1000.0,
        'bmp280': {'ok': True, 'last_good_monotonic': 100.0 - age}})
    monkeypatch.setattr(service.time, 'monotonic', lambda: 100.0)
    env = environment_snapshot(settings)
    assert env['bmp280']['fresh'] is False
    assert env['pressure_hPa'] is None


def test_short_write_abandons_segment_and_recovers_without_retrying_tail(settings):
    log=Recorder(settings)
    log.start('test')
    old=log.path
    real=log.handles['PCSW.CSV']
    class Short:
        def write(self,payload):
            real.write(payload[:5])
            return 5
        def close(self): real.close()
    log.handles['PCSW.CSV']=Short()
    assert not log.capture('CSW',decode(SWING).values,{})
    assert not log.active and 'short write' in log.error
    partial=(old/'PCSW.CSV').read_bytes()
    log.last_attempt=float('-inf')
    log.tick()
    assert log.active and log.path != old
    log.capture('CSW',decode(SWING).values,{})
    log.close()
    assert (old/'PCSW.CSV').read_bytes() == partial
    assert len(list(csv.DictReader((log.path/'PCSW.CSV').open()))) == 1


def test_low_space_never_deletes_and_can_recover(settings,monkeypatch):
    import pendulum_pi.recording as recording
    space=recording.shutil.disk_usage(settings.data_dir.parent)
    settings=replace(settings,min_free_mb=1)
    log=Recorder(settings)
    monkeypatch.setattr(recording.shutil,'disk_usage',lambda _: space._replace(free=0))
    log.start('test')
    assert not log.active and 'free disk' in log.error
    monkeypatch.setattr(recording.shutil,'disk_usage',lambda _: space)
    log.last_attempt=float('-inf')
    log.tick()
    assert log.active
    log.close()


def test_segment_rotation_and_manifest_contract(settings):
    log=Recorder(settings)
    log.start('test')
    contract=Contract()
    for raw in META: contract.observe(decode(raw))
    log.set_contract(contract.snapshot())
    old=log.path
    log.byte_count=settings.segment_bytes
    log.tick()
    assert log.path != old
    assert json.loads((old/'manifest.json').read_text())['closed_reason'] == 'rotation'
    assert json.loads((log.path/'manifest.json').read_text())['contract']['cfg']['pv'] == '3'
    log.close()


def test_runtime_config_roundtrip_and_validation(settings,tmp_path):
    config=tmp_path/'config.json'
    save_settings(config,settings)
    assert load_settings(config) == settings
    with pytest.raises(ValueError): update_settings(settings,{'long_minutes':9})
    with pytest.raises(ValueError): update_settings(settings,{'serial_port':'/tmp/other'})
    with pytest.raises(ValueError): update_settings(settings,{'flush_seconds':float('nan')})
    config.write_text(json.dumps({'data_dir': 'logs', 'runtime_dir': str(tmp_path/'logs')}))
    with pytest.raises(ValueError, match='must be different'):
        load_settings(config)


def test_changing_target_does_not_reset_mean_or_forecast(settings, tmp_path):
    from pendulum_pi.analysis import Analysis
    from pendulum_pi.rating import rated_display
    worker = Analysis(settings)
    try:
        worker.display.window.observe((900_000, 100_561.5, 900_000, 100_438.5))
        worker.display.last_swing_at = time.monotonic()
        before = worker.display.snapshot(worker.display.last_swing_at)
        # Target is rating configuration; it does not change causal model state.
        after = rated_display(worker.display.snapshot(worker.display.last_swing_at), 2)
        assert after['window']['period_us'] == before['window']['period_us']
        assert after['forecast'] == before['forecast']
        assert after['estimate_revision'] == before['estimate_revision']
        assert after['window']['gain_seconds_per_day'] < 0
    finally:
        worker.close()


def test_retired_horizons_are_discarded_on_file_load_and_not_reintroduced(settings,tmp_path):
    path=tmp_path/'config.json'
    save_settings(path,settings)
    data=json.loads(path.read_text())
    data.update(short_minutes=5,long_minutes=60,forecast_cycle_length=15)
    path.write_text(json.dumps(data))
    loaded=load_settings(path)
    assert loaded.forecast_cycle_length==15
    save_settings(path,loaded)
    assert 'short_minutes' not in json.loads(path.read_text())
    for retired in ('short_minutes','long_minutes'):
        with pytest.raises(ValueError,match='unsupported'):
            update_settings(loaded,{retired:5})


def test_gps_health_is_published_and_recorded_in_periodic_diagnostics(acquisition, monkeypatch):
    gps = {'status': 'available', 'fresh': True, 'fix_mode': 3,
           'fix_status': None, 'satellites_used': 6, 'satellites_visible': 8}
    monkeypatch.setattr(acquisition.gps_health, 'snapshot', lambda now=None: dict(gps))
    acquisition.periodic(force=True)
    published = json.loads((acquisition.settings.runtime_dir / 'status.json').read_text())
    assert published['gps_health'] == gps
    with (acquisition.recorder.path / 'PI.CSV').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    health = next(row for row in rows if row['category'] == 'health')
    assert json.loads(health['msg'])['gps_health'] == gps


def test_commands_are_single_flight_acknowledged_not_resent_after_restart(settings):
    reader=Reader(settings)
    reader.connected=True
    reader.generation=7
    commands=Commands(settings.runtime_dir,reader)
    req={'id':'a'*32,'command':'set ppsFastShift 6','created_monotonic':time.monotonic()}
    atomic_json(settings.runtime_dir/'commands'/('a'*32+'.json'),req)
    commands.poll(True)
    assert reader.commands.get_nowait() == ('a'*32,'set ppsFastShift 6',7)
    commands.transport_event(Input('command_sent',mono=time.monotonic(),generation=7,command_id='a'*32))
    commands.observe(decode(b'STS,OK,set,ppsFastShift,6\n'),event(b'',generation=7))
    assert json.loads((settings.runtime_dir/'results'/('a'*32+'.json')).read_text())['state']=='ok'
    req.update(id='b'*32,created_monotonic=commands.started-1)
    atomic_json(settings.runtime_dir/'commands'/('b'*32+'.json'),req)
    commands.poll(True)
    assert reader.commands.empty()
    assert 'not resent' in json.loads((settings.runtime_dir/'results'/('b'*32+'.json')).read_text())['response']


def test_command_timeout_waits_for_backlog_then_forces_rejoin(settings):
    reader = Reader(settings)
    reader.connected = True
    reader.generation = 1
    reader.ready.set()
    commands = Commands(settings.runtime_dir, reader)
    request = {'id': 'a'*32, 'command': 'set ppsFastShift 6',
               'created_monotonic': time.monotonic()}
    atomic_json(settings.runtime_dir/'commands'/('a'*32+'.json'), request)
    commands.poll(True)
    sent = time.monotonic()
    commands.transport_event(Input('command_sent', mono=sent, generation=1, command_id='a'*32))
    commands.pending['started'] = sent - 11
    ack = event(b'STS,OK,set,ppsFastShift,6\n')
    reader.events.put(ack)
    commands.poll(True)
    assert commands.pending and not reader.reconnect.is_set()
    reader.events.get_nowait()
    commands.poll(True)
    assert commands.pending is None and commands.blocked
    assert reader.reconnect.is_set() and not reader.ready.is_set()
    commands.observe(decode(ack.raw), ack)
    result = json.loads((settings.runtime_dir/'results'/('a'*32+'.json')).read_text())
    assert result['state'] == 'timeout'
    commands.interrupted('rejoining')
    assert not commands.blocked and reader.commands.empty()


def test_old_connection_metadata_cannot_enable_new_uart_commands(acquisition):
    app = acquisition
    app.reader.connected = True
    app.reader.generation = 2
    ready(app)  # queued generation 1 metadata is still useful for recording that backlog
    assert app.recorder.contract['cfg']['pv'] == '3'
    assert not app.reader.ready.is_set()
    assert not app.snapshot()['ready']
    request = {'id': 'a'*32, 'command': 'get ppsFastShift',
               'created_monotonic': time.monotonic()}
    atomic_json(app.settings.runtime_dir/'commands'/('a'*32+'.json'), request)
    app.periodic(force=True)
    assert app.reader.commands.empty()
    app.process(Input('connected', generation=2))
    for raw in META:
        app.process(event(raw, generation=2))
    app.periodic(force=True)
    assert app.reader.ready.is_set()
    assert app.reader.commands.get_nowait() == ('a'*32, 'get ppsFastShift', 2)


def test_invalid_command_file_does_not_stop_acquisition(settings):
    commands = Commands(settings.runtime_dir, Reader(settings))
    atomic_json(settings.runtime_dir/'commands'/('a'*32+'.json'), ['invalid'])
    commands.poll(False)
    assert json.loads((settings.runtime_dir/'results'/('a'*32+'.json')).read_text())['state'] == 'error'


def test_replay_full_service_preserves_raw_and_finalizes_files(settings,tmp_path):
    wire=tmp_path/'wire.txt'
    payload=b''.join([SWING,*META,SWING,PPS,b'STS,OK,get,ppsFastShift,6\n',b'bad,record\n'])
    wire.write_bytes(payload)
    reader=Reader(settings,'replay',wire,pace=0.002)
    app=Acquisition(settings,reader=reader)
    app.run()
    assert app.counters['accepted_CSW']==1 and app.counters['accepted_CPS']==1
    raws=list(settings.data_dir.glob('*/segment-*/RAW.jsonl'))
    recovered=b''.join(bytes.fromhex(json.loads(line)['raw_hex']) for path in sorted(raws,key=lambda p:p.stat().st_mtime) for line in path.read_text().splitlines())
    assert recovered==payload
    status=json.loads((settings.runtime_dir/'status.json').read_text())
    assert status['stopped'] and not status['connected']
    for path in settings.data_dir.glob('*/segment-*/manifest.json'):
        assert json.loads(path.read_text()).get('closed_utc')
    assert any(json.loads(path.read_text())['contract'].get('cfg', {}).get('pv') == '3'
               for path in settings.data_dir.glob('*/segment-*/manifest.json'))


def test_live_serial_owner_requests_metadata_and_handles_web_command(settings,tmp_path):
    """Exercise pyserial against a real POSIX pseudo-terminal, including an ACK."""
    import os
    import pty
    import select
    import threading
    from pendulum_pi.web import create_app

    def until(predicate, seconds=5):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if predicate(): return
            time.sleep(.01)
        raise AssertionError('timed out waiting for serial integration')

    master, slave=pty.openpty()
    settings=replace(settings,serial_port=os.ttyname(slave),api_token='x'*32)
    config=tmp_path/'config.json'
    save_settings(config,settings)
    app=Acquisition(settings,config)
    errors=[]
    def run():
        try: app.run()
        except BaseException as error: errors.append(error)
    thread=threading.Thread(target=run)
    thread.start()
    try:
        until(lambda: app.reader.connected)
        assert select.select([master],[],[],3)[0]
        assert b'emit meta\n' in os.read(master,1024)
        os.write(master,b'partial first line\n'+b''.join(META)+SWING+PPS)
        until(lambda: app.counters['accepted_CSW']==1)
        client=create_app(config).test_client()
        reply=client.post('/api/commands',json={'command':'set ppsFastShift 6'},
                          headers={'Authorization':'Bearer '+'x'*32})
        assert reply.status_code==202
        ident=reply.json['id']
        assert select.select([master],[],[],3)[0]
        wire=os.read(master,1024)
        assert b'set ppsFastShift 6\n' in wire
        os.write(master,b'STS,OK,set,ppsFastShift,6\n')
        until(lambda: client.get('/api/commands/'+ident).json['state']=='ok')
        assert app.counters['fragments']==1
        assert app.counters['malformed']==0
    finally:
        app.stop.set()
        thread.join(5)
        os.close(master)
        os.close(slave)
    assert not thread.is_alive() and not errors
    assert app.recorder.written=={'CSW':1,'CPS':1}


def test_stale_generation_commands_are_not_transmitted(settings,monkeypatch):
    """A queued mutation must not cross a serial reconnect, even if core is delayed."""
    import sys
    from types import SimpleNamespace
    reader=Reader(settings)
    reader.commands.put(('a'*32,'set ppsFastShift 6',0))
    writes=[]
    class Port:
        in_waiting=0
        def __init__(self,**kwargs): pass
        def open(self): pass
        def write(self,data):
            writes.append(data)
            return len(data)
        def read(self,n):
            reader.ready.set()
            if reader.commands.empty(): reader.stop.set()
            return b''
        def close(self): pass
    monkeypatch.setitem(sys.modules,'serial',SimpleNamespace(Serial=Port,SerialException=OSError))
    reader.serial_loop()
    assert b'set ppsFastShift 6\n' not in writes
    events=list(reader.events.queue)
    assert any(e.kind=='command_error' and 'earlier serial' in e.detail for e in events)


def test_rejected_pps_burst_is_preserved_at_full_resolution(acquisition):
    app = acquisition
    ready(app)
    hz = 16000000
    frames = []
    base = time.monotonic()
    samples = [(1, 0, 0), (2, hz, 0), (3, hz + 15005894, 22),
               (4, hz + 15006113, 22), (27, hz + 15014650, 24), (30, 2 * hz, 24)]
    for seq, ticks, drops in samples:
        raw = f'CPS,{seq},{ticks},2,0,{ticks & 65535},151,{ticks + 151},{drops}\n'.encode()
        frames.append(raw)
        app.process(Input('line', raw, base + ticks / hz, time.time(), 1))
    assert app.counters['accepted_CPS'] == len(samples)
    assert app.counters['pps_missing'] == 24
    path = app.recorder.path
    app.recorder.close()
    from pendulum_pi.analysis import Analysis
    worker = Analysis(app.settings)
    try:
        worker.step()
        assert worker.display.clock.observation_hz == hz
        assert worker.display.clock.rejected_edges == 3
    finally:
        worker.close()
    rows = list(csv.DictReader((path / 'PCPS.CSV').open()))
    assert [(int(r['seq']), int(r['edge_tcb0']), int(r['drop_pps'])) for r in rows] == samples
    recovered = [bytes.fromhex(json.loads(line)['raw_hex']) for line in (path / 'RAW.jsonl').read_text().splitlines()]
    assert [raw for raw in recovered if raw.startswith(b'CPS,')] == frames


def test_live_forecast_configuration_provenance(settings, tmp_path):
    path = tmp_path / 'config.json'
    save_settings(path, settings)
    app = Acquisition(settings, path)
    app.recorder.start('test')
    ready(app)
    app.process(event(PPS))
    app.process(event(SWING))
    save_settings(path, replace(settings, forecast_cycle_length=15))
    app.reload_settings()
    app.reload_settings()  # Unchanged polls must not manufacture transitions.
    app.recorder.close()
    with (app.recorder.path / 'PI.CSV').open(newline='') as stream:
        rows = [row for row in csv.DictReader(stream)
                if row['category'] == 'display' and row['key'] == 'configuration']
    assert len(rows) == 2
    initial, changed = [json.loads(row['msg']) for row in rows]
    assert initial['forecast_cycle_length'] == 0
    assert changed['forecast_cycle_length'] == 15
    assert changed['capture_frontier'] == {'CPS': 1, 'CSW': 1}
    assert changed['mean_model'] == initial['mean_model']
    assert initial['capture_frontier'] == {}


def test_startup_primes_after_fresh_capture_but_reconnect_does_not(acquisition):
    from pendulum_pi.display import MEAN_MODEL
    from pendulum_pi.analysis import Analysis
    app = acquisition
    app.boot_id = 'boot'
    now = time.monotonic()
    worker = Analysis(app.settings)
    worker.checkpoint.boot_id = 'boot'
    worker.checkpoint.pending = {'version': 1, 'source': 'serial', 'model': MEAN_MODEL,
                                'nominal_hz': 16000000, 'pps_holdover_seconds': 180,
                                'boot_id': 'boot', 'saved_monotonic': now - 10,
                                'samples': [[0, [490000.] * 4]]}
    try:
        app.reader.connected = True
        app.reader.generation = 1
        ready(app)
        app.process(Input('line', PPS, now - 2, time.time() - 2, 1))
        app.process(Input('line', PPS.replace(b'CPS,1,16000000', b'CPS,2,32000000'),
                          now - 1, time.time() - 1, 1))
        app.process(Input('line', SWING, now - .5, time.time() - .5, 1))
        app.recorder.sync()
        worker.step()
        assert worker.result['display']['window']['priming']['active']
        app.process(Input('connected', generation=2))
        for raw in META:
            app.process(event(raw, generation=2))
        app.process(event(PPS, generation=2))
        app.recorder.sync()
        worker.last_scan = float('-inf')
        worker.step()
        assert not worker.display.window.restored
        assert worker.checkpoint.pending is None
    finally:
        worker.close()
