"""Publisher CLI exit semantics and acquisition freshness provenance."""
from pathlib import Path

import pytest

from pendulum_pi import common
from pendulum_pi.__main__ import main
from pendulum_pi.config import Settings, save_settings
from pendulum_pi.service import Acquisition


@pytest.mark.parametrize('mode', [None, '--dry-run', '--validate-key', '--check-ready'])
@pytest.mark.parametrize('result', [0, 1])
def test_cli_routes_publisher_mode_and_propagates_exit(tmp_path, monkeypatch, mode, result):
    from pendulum_pi import thingspeak
    config = tmp_path / 'config.json'
    publisher = tmp_path / 'publisher.json'
    save_settings(config, Settings(data_dir=tmp_path / 'data', runtime_dir=tmp_path / 'run'))
    calls = []

    def run(settings, path, **kwargs):
        calls.append((settings, path, kwargs))
        return result

    monkeypatch.setattr(thingspeak, 'run_thingspeak', run)
    args = ['thingspeak', '--config', str(config), '--publisher-config', str(publisher)]
    if mode:
        args.append(mode)
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == result
    assert len(calls) == 1
    assert calls[0][1] == publisher
    assert calls[0][2] == {
        'dry_run': mode == '--dry-run',
        'validate_key': mode == '--validate-key',
        'check_ready': mode == '--check-ready',
    }


def test_cli_dry_run_cannot_also_validate_key(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(['thingspeak', '--config', str(tmp_path / 'unused.json'),
              '--dry-run', '--validate-key'])
    assert exc.value.code == 2


@pytest.mark.parametrize('value', ['not-a-boot-id', '', 'x' * 100])
def test_unknown_boot_identity_fails_closed(monkeypatch, value):
    monkeypatch.setattr(Path, 'read_text', lambda *_args, **_kwargs: value)
    assert common.boot_id() is None


def test_acquisition_caches_boot_epoch_in_status(tmp_path, monkeypatch):
    identity = '31b3737f-b945-44ce-899c-de40324d0aac'
    monkeypatch.setattr('pendulum_pi.service.boot_id', lambda: identity)
    settings = Settings(data_dir=tmp_path / 'data', runtime_dir=tmp_path / 'run')
    app = Acquisition(settings)
    monkeypatch.setattr('pendulum_pi.service.boot_id', lambda: None)
    state = app.snapshot()
    assert state['boot_id'] == identity
    assert 'display' not in state
    assert not hasattr(app, 'display')


def test_mean_snapshot_is_identical_for_oled_http_and_publication(monkeypatch):
    from pendulum_pi.display import DisplayEstimator, MASK32
    from pendulum_pi.oled import body_rows
    from pendulum_pi.rating import rated_display
    from pendulum_pi.thingspeak import build_payload

    monkeypatch.setattr(common, 'boot_id', lambda: 'test-boot')
    estimator = DisplayEstimator()
    hz, start = 10_000_000, 0
    period_ticks = 20_001_000
    estimator.observe('CPS', {'seq': 0, 'edge_tcb0': 0, 'gps_status': 2, 'drop_pps': 0}, hz, 0)
    pps_seq = 1
    for seq in range(302):
        now = (seq + 1) * period_ticks / hz
        while pps_seq <= int(now):
            estimator.observe('CPS', {'seq': pps_seq, 'edge_tcb0': pps_seq * hz & MASK32,
                                     'gps_status': 2, 'drop_pps': 0}, hz, pps_seq)
            pps_seq += 1
        values = {'seq': seq, 'drop_ir': 0, 'drop_swing': 0, 'drop_pps': 0,
                  'edge0_tcb0': start & MASK32}
        for edge, interval in enumerate((9_000_000, 1_001_000, 9_000_000, 1_000_000), 1):
            start += interval
            values[f'edge{edge}_tcb0'] = start & MASK32
        estimator.observe('CSW', values, hz, now)

    # Acquisition.snapshot provides this rated display unchanged to status.json
    # and HTTP; both OLED and the isolated publisher consume that same object.
    display = rated_display(estimator.snapshot(now), 2)
    snapshot = {'boot_id': 'test-boot', 'updated_monotonic': now,
                'source': 'serial', 'connected': True, 'ready': True,
                'capture_stale': False, 'last_capture_age_seconds': 0,
                'display': display}
    window = display['window']
    assert window['learning'] is False
    assert window['period_us'] == pytest.approx(2_000_100)
    assert body_rows(snapshot, 0) == display['rows']
    assert display['rows'][0] == f"P {window['period_us'] / 1_000_000:.9f}s"
    assert display['rows'][1] == f"B {window['bpm']:.6f}BPM"
    assert display['rows'][2] == f"R {window['gain_seconds_per_day']:+.6f}s/d"
    payload, reason = build_payload(snapshot, Settings(), now=now)
    assert reason == 'eligible'
    assert payload['field1'] == f"{window['gain_seconds_per_day']:.6f}"
    assert payload['field2'] == f"{window['period_us'] / 1_000_000:.6f}"
    assert window['bpm'] == pytest.approx(60_000_000 / window['period_us'])

    snapshot['display'] = rated_display(estimator.snapshot(now + 10), 2)
    stale_window = snapshot['display']['window']
    assert stale_window['period_us'] is None
    assert stale_window['bpm'] is None
    assert stale_window['gain_seconds_per_day'] is None
    assert all('stale' in row for row in body_rows(snapshot, 0))
    assert build_payload(snapshot, Settings(), now=now)[0] is None
