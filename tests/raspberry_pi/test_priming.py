"""Restart age, configuration and calibration gates for the derived mean cache."""
import json
from copy import deepcopy

import pytest

from pendulum_pi.common import atomic_json
from pendulum_pi.display import DisplayEstimator, MEAN_MODEL, RollingSwingMean
from pendulum_pi.priming import MeanCheckpoint, CHECKPOINT

HZ = 16_000_000
UTC = {'status': 'synchronized', 'fresh': True}


def estimator():
    display = DisplayEstimator()
    display.nominal_hz = display.clock.nominal_hz = HZ
    display.clock.calibrated_at = 990
    display.clock.previous = {'gps_status': 2}
    display.window.observe((500000.,) * 4, 990)
    display.last_swing_at = 990
    return display


def payload(**changes):
    value = {'version': 1, 'model': MEAN_MODEL, 'source': 'serial',
             'nominal_hz': HZ, 'pps_holdover_seconds': 180,
             'boot_id': 'previous-boot', 'saved_monotonic': 900,
             'saved_epoch': 1800000000.,
             'samples': [[599, [490000.] * 4], [10, [490000.] * 4], [0, [490000.] * 4]]}
    value.update(changes)
    return value


def checkpoint(tmp_path, value, boot='current-boot'):
    atomic_json(tmp_path / CHECKPOINT, value)
    return MeanCheckpoint(tmp_path, boot, 'serial')


def test_across_boot_waits_for_verified_utc_then_primes_only_eligible_samples(tmp_path):
    cache = checkpoint(tmp_path, payload())
    display = estimator()
    before = deepcopy(display.clock.__dict__)
    assert not cache.restore(display, 990, 1800000020., {'status': 'unavailable', 'fresh': False})
    assert cache.pending is not None
    assert cache.restore(display, 990, 1800000020., UTC)
    state = display.snapshot(990)
    assert state['window']['count'] == 3
    assert state['window']['period_us'] == pytest.approx((2000000 + 1960000 * 2) / 3)
    assert state['window']['priming']['restored_samples'] == 2
    assert state['window']['priming']['checkpoint_age_seconds'] == 20
    assert state['window']['learning']
    assert state['window']['filling_seconds'] == 2
    assert display.clock.__dict__ == before
    assert cache.pending is None


def test_same_boot_uses_monotonic_age_despite_wrong_wall_clock(tmp_path):
    cache = checkpoint(tmp_path, payload(boot_id='current-boot', saved_monotonic=970, saved_epoch=None))
    display = estimator()
    assert cache.restore(display, 990, 1, {})
    assert display.snapshot(990)['window']['priming']['checkpoint_age_seconds'] == 20


@pytest.mark.parametrize('changes', [
    {'saved_epoch': None}, {'saved_epoch': 1800000021.}, {'saved_epoch': 1799999420.},
    {'model': 'other'}, {'nominal_hz': 10000000}, {'pps_holdover_seconds': 5},
    {'source': 'demo'}, {'samples': []}, {'samples': [[0, [1, 2, 3]]]},
    {'samples': [[0, [1, 2, 3, float('nan')]]]}, {'samples': [[0, [0, 2, 3, 4]]]},
    {'samples': [[-1, [1, 2, 3, 4]]]}, {'samples': [[0, [1, 2, 3, 4]], [1, [1, 2, 3, 4]]]},
    {'samples': [[599, [1, 2, 3, 4]]]}, {'samples': 'bad'},
])
def test_bad_expired_future_and_incompatible_checkpoints_fail_closed(tmp_path, changes):
    # Write permissively to exercise the reader with corrupt external cache data.
    (tmp_path / CHECKPOINT).write_text(json.dumps(payload(**changes)))
    cache = MeanCheckpoint(tmp_path, 'current-boot', 'serial')
    display = estimator()
    assert not cache.restore(display, 990, 1800000020., UTC)
    assert cache.health['state'] == 'rejected'
    assert not display.window.restored
    assert display.window.snapshot(True)['count'] == 1


@pytest.mark.parametrize('condition', ['no_swings', 'pps_wait', 'holdover'])
def test_restore_requires_fresh_pps_and_current_swing(tmp_path, condition):
    cache = checkpoint(tmp_path, payload())
    display = estimator()
    if condition == 'no_swings':
        display.window = RollingSwingMean()
    elif condition == 'pps_wait':
        display.clock.calibrated_at = None
    else:
        display.clock.previous['gps_status'] = 3
    assert not cache.restore(display, 990, 1800000020., UTC)
    assert not display.window.restored


def test_restored_samples_age_out_and_never_supply_fresh_coverage():
    window = RollingSwingMean()
    window.observe((500000.,) * 4, 100)
    window.restore([(90, (490000.,) * 4), (95, (490000.,) * 4)], 98)
    assert window.snapshot(True, now=100)['count'] == 3
    assert window.snapshot(True, now=690)['priming']['restored_samples'] == 1
    assert window.snapshot(True, now=695)['count'] == 1
    assert window.snapshot(True, now=695)['learning']
    assert window.snapshot(True, now=695)['filling_seconds'] == 2


def test_fresh_600_seconds_replace_restored_samples_and_reset_clears_them():
    window = RollingSwingMean()
    window.restore([(100, (490000.,) * 4)], 100)
    for i in range(300):
        window.observe((500000.,) * 4, 101 + i * 2)
    assert not window.snapshot(True, now=699)['priming']['active']
    assert not window.snapshot(True, now=699)['learning']
    display = estimator()
    display.window.restore([(980, (490000.,) * 4)], 980)
    display.reset()
    assert not display.window.restored


def test_background_save_roundtrip_flush_and_no_replay_checkpoint(tmp_path):
    cache = MeanCheckpoint(tmp_path, 'current-boot', 'serial')
    cache.start()
    display = estimator()
    assert cache.submit(display, 990, 1800000020., UTC)
    assert not cache.submit(display, 991, 1800000021., UTC)
    cache.close()
    saved = json.loads((tmp_path / CHECKPOINT).read_text())
    assert saved['saved_epoch'] == 1800000020.
    assert saved['samples'] == [[0, [500000.] * 4]]
    cache = MeanCheckpoint(tmp_path, 'current-boot', 'serial')
    later = estimator()
    later.window.sample_times[0] = 994
    later.clock.calibrated_at = 994
    assert cache.restore(later, 994, 1800000024., {})
    replay = MeanCheckpoint(tmp_path, 'current-boot', 'replay')
    replay.start()
    assert replay.pending is None and replay.thread is None
    assert not replay.submit(later, 994, 1800000024., UTC)


def test_malformed_or_oversize_cache_is_bounded(tmp_path):
    from pendulum_pi.priming import MAX_BYTES
    for text in ('[]', '{bad json', ' ' * (MAX_BYTES + 1)):
        (tmp_path / CHECKPOINT).write_text(text)
        cache = MeanCheckpoint(tmp_path, 'boot', 'serial')
        assert cache.pending is None and cache.health['state'] == 'rejected'


def test_full_fresh_window_discards_deferred_cache_even_without_verified_utc(tmp_path):
    cache = checkpoint(tmp_path, payload())
    display = estimator()
    display.window.filling_seconds = 600
    assert not cache.restore(display, 990, 1, {})
    assert cache.pending is None
    assert not display.window.restored
    assert cache.health['state'] == 'fresh window complete'
