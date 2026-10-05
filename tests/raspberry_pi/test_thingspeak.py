"""The public diary must be expendable, precise and credential-gated."""
import json

import pytest

from pendulum_pi.config import Settings
from pendulum_pi import thingspeak as ts

KEY = 'TESTONLY123456789'


@pytest.fixture
def snapshot(monkeypatch):
    monkeypatch.setattr(ts.common, 'boot_id', lambda: 'test-boot')
    return {
        'boot_id': 'test-boot', 'updated_monotonic': 100.0,
        'updated_utc': '2026-09-27T12:00:00+00:00', 'source': 'serial',
        'connected': True, 'ready': True, 'capture_stale': False,
        'last_capture_age_seconds': 0.2, 'config_error': None,
        'display': {'available': True, 'stale': False, 'timebase': 'PPS',
                    'target_period_s': 2.0, 'last_swing_monotonic': 99.8,
                    'window': {'period_us': 2000100.12345, 'window_seconds': 600,
                               'learning': False, 'bpm': 60_000_000 / 2000100.12345,
                               'gain_seconds_per_day': 86400 * (2 / 2.00010012345 - 1)},
                    'pps': {'status': 'LOCKED', 'initialized': True, 'stale': False,
                            'last_accepted_monotonic': 99.5}},
        'time_health': {'fresh': True, 'status': 'synchronized', 'sample_age_seconds': 1},
        'environment': {'temperature_C': 21.456, 'humidity_pct': 48.125, 'pressure_hPa': 1001.456,
                        'sht4x': {'last_good_monotonic': 99}, 'bmp280': {'last_good_monotonic': 99}}}


def test_precision_rate_uses_unrounded_period_and_explicit_snapshot_target(snapshot):
    payload, reason = ts.build_payload(snapshot, Settings(target_period_s=3), now=101)
    assert reason == 'eligible'
    assert payload['field1'] == '-4.325117'
    assert payload['field2'] == '2.000100'
    assert payload['field3'] == '21.46'
    assert payload['field4'] == '1001.46'
    assert payload['field5'] == '48.12'
    assert 'observation_utc=2026-09-27T12:00:00+00:00' in payload['status']
    assert len(payload['status']) < 255
    assert 'schema=2;estimator=mean;window_seconds=600;' in payload['status']
    assert 'half_life' not in payload['status']
    assert 'created_at' not in payload


def test_publisher_uses_canonical_rate_without_another_calculation(snapshot):
    # The acquisition's rated snapshot is the single calculation shared by
    # HTTP, OLED and publication. A publisher has no smoothing/rating owner.
    window = snapshot['display']['window']
    window['gain_seconds_per_day'] = 1.23456789
    payload, reason = ts.build_payload(snapshot, Settings(), now=101)
    assert reason == 'eligible'
    assert payload['field1'] == f"{window['gain_seconds_per_day']:.6f}"
    assert payload['field2'] == f"{window['period_us'] / 1_000_000:.6f}"


def test_old_ewma_snapshot_cannot_be_published_as_the_new_mean(snapshot):
    snapshot['display']['long'] = snapshot['display'].pop('window')
    assert ts.build_payload(snapshot, Settings(), now=101)[0] is None


def test_humidity_enabled_by_default_but_can_be_disabled(setup):
    _, path, _ = setup
    assert ts.load_config(path).include_humidity is True
    config = json.loads(path.read_text())
    config['include_humidity'] = False
    path.write_text(json.dumps(config))
    assert ts.load_config(path).include_humidity is False


@pytest.mark.parametrize('path,value', [
    ('updated_monotonic', 90), ('updated_monotonic', 102), ('updated_monotonic', float('nan')),
    ('updated_monotonic', True), ('boot_id', 'previous-boot'), ('boot_id', None),
    ('stopped', True), ('source', 'demo'), ('source', 'replay'),
    ('environment.simulated', True), ('connected', False), ('ready', False),
    ('capture_stale', True), ('last_capture_age_seconds', 9), ('config_error', 'private secret'),
    ('display.available', False), ('display.stale', True), ('display.window.learning', True),
    ('display.window.period_us', float('inf')), ('display.window.period_us', 0),
    ('display.window.period_us', True), ('display.window.period_us', 10**400),
    ('display.window.period_us', 1e-100), ('display.window.period_us', 1e308), ('display.window.window_seconds', None),
    ('display.window.window_seconds', 300), ('display.window.window_seconds', True),
    ('display.window.bpm', None), ('display.window.bpm', 0), ('display.window.bpm', float('nan')),
    ('display.window.gain_seconds_per_day', None), ('display.window.gain_seconds_per_day', True),
    ('display.window.gain_seconds_per_day', float('inf')),
    ('display.last_swing_monotonic', 91), ('display.last_swing_monotonic', None),
    ('display.timebase', 'NOMINAL'), ('display.pps.status', 'HOLDOVER'),
    ('display.pps.initialized', False), ('display.pps.stale', True),
    ('display.pps.last_accepted_monotonic', 96), ('display.pps.last_accepted_monotonic', None),
    ('display.target_period_s', None), ('display.target_period_s', True),
    ('display.target_period_s', float('nan')),
])
def test_unsafe_or_stale_snapshot_skipped(snapshot, path, value):
    node = snapshot
    parts = path.split('.')
    for name in parts[:-1]:
        node = node[name]
    node[parts[-1]] = value
    payload, reason = ts.build_payload(snapshot, Settings(), now=101)
    assert payload is None
    assert 'private secret' not in reason


@pytest.mark.parametrize('snapshot', [None, [], 1, {}, {'updated_monotonic': 100, 'display': []}])
def test_malformed_snapshot_is_safe(snapshot):
    assert ts.build_payload(snapshot, Settings(), now=101)[0] is None


def test_sensor_freshness_rechecked_independently_and_recording_pause_allowed(snapshot):
    snapshot['logging'] = {'active': False}
    snapshot['environment']['bmp280']['last_good_monotonic'] = 90
    payload, _ = ts.build_payload(snapshot, Settings(), now=101, include_humidity=True)
    assert 'field4' not in payload
    assert payload['field5'] == '48.12'
    snapshot['environment']['sht4x']['last_good_monotonic'] = 102
    payload, _ = ts.build_payload(snapshot, Settings(), now=101, include_humidity=True)
    assert set(payload) == {'field1', 'field2', 'status'}


def test_disabled_and_nonfinite_environment_omitted(snapshot):
    snapshot['environment']['temperature_C'] = float('nan')
    payload, _ = ts.build_payload(snapshot, Settings(), now=101)
    assert 'field3' not in payload and 'field4' in payload
    payload, _ = ts.build_payload(snapshot, Settings(sensors_enabled=False), now=101, include_humidity=True)
    assert set(payload) == {'field1', 'field2', 'status'}


@pytest.mark.parametrize('change', [{'sample_age_seconds': 30}, {'fresh': False}, {'status': 'unavailable'}])
def test_unknown_utc_does_not_suppress_precise_period(snapshot, change):
    snapshot['time_health'].update(change)
    payload, _ = ts.build_payload(snapshot, Settings(), now=101)
    assert 'observation_utc=unknown' in payload['status']
    assert 'pi_utc=unknown' in payload['status']


def test_status_allowlist_does_not_expose_diagnostics(snapshot):
    snapshot['time_health']['selected_source'] = 'private-host'
    snapshot['environment']['error'] = KEY
    snapshot['updated_utc'] = 'private timestamp'
    payload, _ = ts.build_payload(snapshot, Settings(), now=101)
    assert KEY not in json.dumps(payload)
    assert 'private' not in json.dumps(payload)


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status = body, status
    def read(self, limit):
        assert limit == ts.MAX_RESPONSE + 1
        return self.body[:limit]


def fake_network(monkeypatch, response):
    calls = []
    class Connection:
        def __init__(self, host, timeout):
            assert host == 'api.thingspeak.com' and timeout == 5
        def request(self, method, path, body, headers):
            calls.append(json.loads(body))
            assert method == 'POST' and path == '/update.json'
            assert headers == {'Content-Type': 'application/json'}
        def getresponse(self):
            if isinstance(response, Exception):
                raise response
            return response
        def close(self):
            pass
    monkeypatch.setattr(ts.http.client, 'HTTPSConnection', Connection)
    return calls


@pytest.mark.parametrize('body,status,rejected', [
    (b'0', 200, False), (b'{bad', 200, False), (b'{}', 200, False),
    (b'{"entry_id":1,"channel_id":99}', 200, True),
    (b'{"entry_id":true,"channel_id":12}', 200, False),
    (b'{"entry_id":0,"channel_id":12}', 200, False),
    (b'x' * (ts.MAX_RESPONSE + 1), 200, False),
    (b'ignored', 302, False), (b'ignored', 401, True), (b'ignored', 503, False),
])
def test_invalid_responses_fail_once_without_secret(monkeypatch, body, status, rejected):
    calls = fake_network(monkeypatch, FakeResponse(body, status))
    with pytest.raises(ts.PublisherError) as caught:
        ts.post_payload({'field1': '1.000000'}, KEY, 12)
    assert caught.value.rejected is rejected
    assert KEY not in str(caught.value)
    assert len(calls) == 1


def test_transport_success_and_ambiguous_timeout(monkeypatch):
    calls = fake_network(monkeypatch, FakeResponse(b'{"entry_id":2,"channel_id":12}'))
    assert ts.post_payload({'field1': '1.000000'}, KEY, 12) == 2
    assert calls == [{'field1': '1.000000', 'api_key': KEY}]
    calls = fake_network(monkeypatch, TimeoutError(KEY))
    with pytest.raises(ts.PublisherError, match='delivery may be unknown') as caught:
        ts.post_payload({}, KEY, 12)
    assert KEY not in str(caught.value) and len(calls) == 1


@pytest.fixture
def setup(tmp_path, monkeypatch, snapshot):
    settings = Settings(runtime_dir=tmp_path / 'runtime')
    settings.runtime_dir.mkdir()
    (settings.runtime_dir / 'status.json').write_text(json.dumps(snapshot))
    (settings.runtime_dir / 'analysis.json').write_text(json.dumps(
        {'published_monotonic': 100., 'result': snapshot, 'health': {'state': 'caught_up'}}))
    path, keyfile = tmp_path / 'thingspeak.json', tmp_path / 'write-key'
    keyfile.write_text(KEY + '\n')
    keyfile.chmod(0o600)
    path.write_text(json.dumps({'enabled': True, 'channel_id': 12, 'write_key_file': str(keyfile)}))
    monkeypatch.setattr(ts.time, 'monotonic', lambda: 101)
    return settings, path, keyfile


def test_dry_run_no_key_or_network(setup, monkeypatch, capsys):
    settings, path, keyfile = setup
    keyfile.unlink()
    path.unlink()
    monkeypatch.setattr(ts, 'post_payload', lambda *args: pytest.fail('network during dry run'))
    assert ts.run_thingspeak(settings, path, dry_run=True) == 0
    output = capsys.readouterr().out
    assert '2.000100' in output and KEY not in output


def test_validation_gates_service_and_rotated_key_requires_revalidation(setup, monkeypatch, capsys):
    settings, path, keyfile = setup
    calls = fake_network(monkeypatch, FakeResponse(b'{"entry_id":2,"channel_id":12}'))
    assert ts.run_thingspeak(settings, path, check_ready=True) == 1
    assert ts.run_thingspeak(settings, path) == 0
    assert not calls
    assert ts.run_thingspeak(settings, path, validate_key=True) == 0
    assert set(calls[0]) == {'status', 'api_key'}
    receipt = ts._receipt_path(path)
    assert receipt == path.parent / 'thingspeak' / 'validation.json'
    assert KEY not in receipt.read_text()
    assert ts.run_thingspeak(settings, path, check_ready=True) == 0
    assert len(calls) == 1
    assert ts.run_thingspeak(settings, path) == 0
    assert calls[1]['field1'] == '-4.325117'
    keyfile.write_text('ANOTHERKEY1234567')
    assert ts.run_thingspeak(settings, path, check_ready=True) == 1
    assert KEY not in capsys.readouterr().out


def test_rejection_removes_validation_but_transient_failure_does_not(setup, monkeypatch):
    settings, path, keyfile = setup
    fake_network(monkeypatch, FakeResponse(b'{"entry_id":1,"channel_id":12}'))
    assert ts.run_thingspeak(settings, path, validate_key=True) == 0
    receipt = ts._receipt_path(path)
    fake_network(monkeypatch, TimeoutError(KEY))
    assert ts.run_thingspeak(settings, path) == 1
    assert receipt.exists()
    fake_network(monkeypatch, FakeResponse(b'0'))
    assert ts.run_thingspeak(settings, path) == 1
    assert receipt.exists()
    fake_network(monkeypatch, FakeResponse(b'ignored', 401))
    assert ts.run_thingspeak(settings, path) == 1
    assert not receipt.exists()
    assert ts.run_thingspeak(settings, path, check_ready=True) == 1


@pytest.mark.parametrize('mode', [0o644, 0o666, 0o620])
def test_key_permissions_restricted(setup, mode):
    _, path, keyfile = setup
    keyfile.chmod(mode)
    with pytest.raises(ts.PublisherError, match='permissions'):
        ts.read_write_key(ts.load_config(path))


def test_invalid_config_is_sanitized(setup, capsys):
    settings, path, _ = setup
    path.write_text(json.dumps({'write_api_key': KEY}))
    assert ts.run_thingspeak(settings, path) == 1
    assert KEY not in capsys.readouterr().out


@pytest.mark.parametrize('response', [FakeResponse(b'0'), TimeoutError(KEY), FakeResponse(b'no', 403)])
def test_failed_revalidation_removes_old_receipt(setup, monkeypatch, response):
    settings, path, _ = setup
    fake_network(monkeypatch, FakeResponse(b'{"entry_id":1,"channel_id":12}'))
    assert ts.run_thingspeak(settings, path, validate_key=True) == 0
    fake_network(monkeypatch, response)
    assert ts.run_thingspeak(settings, path, validate_key=True) == 1
    assert not ts._receipt_path(path).exists()


def test_validation_while_disabled_allowed_but_activation_not_ready(setup, monkeypatch):
    settings, path, _ = setup
    config = json.loads(path.read_text())
    config['enabled'] = False
    path.write_text(json.dumps(config))
    calls = fake_network(monkeypatch, FakeResponse(b'{"entry_id":1,"channel_id":12}'))
    assert ts.run_thingspeak(settings, path, validate_key=True) == 0
    assert ts.run_thingspeak(settings, path, check_ready=True) == 1
    assert ts.run_thingspeak(settings, path) == 0
    assert len(calls) == 1


def test_lock_contention_skips_and_never_looks_like_successful_validation(setup, monkeypatch):
    settings, path, _ = setup
    calls = fake_network(monkeypatch, FakeResponse(b'{"entry_id":1,"channel_id":12}'))
    assert ts.run_thingspeak(settings, path, validate_key=True) == 0
    def busy(*args):
        raise BlockingIOError()
    monkeypatch.setattr(ts.fcntl, 'flock', busy)
    assert ts.run_thingspeak(settings, path) == 0
    assert ts.run_thingspeak(settings, path, validate_key=True) == 1
    assert len(calls) == 1


def test_key_oversize_and_symlink_rejected(setup):
    _, path, keyfile = setup
    keyfile.write_text(KEY + ' ' * 300)
    with pytest.raises(ts.PublisherError):
        ts.read_write_key(ts.load_config(path))
    keyfile.unlink()
    keyfile.symlink_to(path)
    with pytest.raises(ts.PublisherError):
        ts.read_write_key(ts.load_config(path))


def test_publisher_reads_saved_result_and_its_observation_time(setup, monkeypatch):
    settings, path, _ = setup
    fake_network(monkeypatch, FakeResponse(b'{"entry_id":1,"channel_id":12}'))
    assert ts.run_thingspeak(settings, path, validate_key=True) == 0
    capture = json.loads((settings.runtime_dir/'status.json').read_text())
    capture['updated_monotonic'] = 106.
    capture['updated_utc'] = '2026-09-27T12:00:06+00:00'
    capture['display']['window']['gain_seconds_per_day'] = 999.  # Ignore live copy.
    (settings.runtime_dir/'status.json').write_text(json.dumps(capture))
    monkeypatch.setattr(ts.time, 'monotonic', lambda: 106.)
    calls = fake_network(monkeypatch, FakeResponse(b'{"entry_id":2,"channel_id":12}'))
    assert ts.run_thingspeak(settings, path) == 0
    assert calls[0]['field1'] == '-4.325117'
    assert 'observation_utc=2026-09-27T12:00:00+00:00' in calls[0]['status']
    assert 'snapshot_age_s=6.000' in calls[0]['status']
    status = json.loads((settings.runtime_dir/'thingspeak/status.json').read_text())
    assert status['entry_id'] == 2


def test_failed_upload_keeps_last_success_and_next_run_uses_latest_result(setup, monkeypatch):
    settings, path, _ = setup
    fake_network(monkeypatch, FakeResponse(b'{"entry_id":1,"channel_id":12}'))
    assert ts.run_thingspeak(settings, path, validate_key=True) == 0
    fake_network(monkeypatch, FakeResponse(b'{"entry_id":2,"channel_id":12}'))
    assert ts.run_thingspeak(settings, path) == 0
    receipt = ts._receipt_path(path).with_name('publication.json')
    success = json.loads(receipt.read_text())['last_success']
    fake_network(monkeypatch, TimeoutError(KEY))
    assert ts.run_thingspeak(settings, path) == 1
    assert json.loads(receipt.read_text())['last_success'] == success
    saved = json.loads((settings.runtime_dir/'analysis.json').read_text())
    saved['result']['display']['window']['gain_seconds_per_day'] = 1.234567
    (settings.runtime_dir/'analysis.json').write_text(json.dumps(saved))
    calls = fake_network(monkeypatch, FakeResponse(b'{"entry_id":3,"channel_id":12}'))
    assert ts.run_thingspeak(settings, path) == 0
    assert len(calls) == 1 and calls[0]['field1'] == '1.234567'
    assert json.loads(receipt.read_text())['last_success']['entry_id'] == 3
