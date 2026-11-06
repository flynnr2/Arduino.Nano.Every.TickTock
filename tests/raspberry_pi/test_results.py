"""Freshness of snapshots published independently while status is assembled."""
from pendulum_pi.config import Settings
from pendulum_pi import results


def test_freshness_clock_is_sampled_after_snapshot_reads(tmp_path, monkeypatch):
    settings = Settings(runtime_dir=tmp_path)
    clock = [100.]

    def read_mapping(path, default=None):
        # Each producer publishes between the start of the request and its read.
        clock[0] += 1
        at = clock[0]
        if path.name == 'status.json':
            return {'boot_id': 'boot', 'logging': {'session': 'recording'}}
        if path.name == 'receiver.json':
            return {'boot_id': 'boot', 'updated_monotonic': at}
        if path.name == 'analysis.json':
            return {'published_monotonic': at, 'result': {
                'boot_id': 'boot', 'logging': {'session': 'recording'},
                'updated_monotonic': at,
                'display': {'available': True, 'stale': False,
                            'forecast': {'stale': False, 'next': {'period_us': 2_000_000}}}}}
        if path.name == 'views-health.json':
            return {'updated_monotonic': at}
        return {}

    monkeypatch.setattr(results, 'read_mapping', read_mapping)
    monkeypatch.setattr(results.time, 'monotonic', lambda: clock[0])
    state = results.saved_status(settings)
    assert state['receiver']['updated_monotonic'] == 102.
    assert state['analysis']['result_age_seconds'] == 2.
    assert not state['analysis']['service_stale']
    assert not state['views']['service_stale']
    assert not state['display']['stale']
    assert state['display']['forecast']['next']['period_us'] == 2_000_000


def test_explicit_cutoff_still_rejects_future_snapshots(tmp_path, monkeypatch):
    settings = Settings(runtime_dir=tmp_path)
    capture = {'boot_id': 'boot', 'logging': {'session': 'recording'}}
    snapshots = {
        'status.json': capture,
        'receiver.json': {'boot_id': 'boot', 'updated_monotonic': 101.},
        'analysis.json': {'published_monotonic': 101., 'result': {
            **capture, 'updated_monotonic': 101., 'display': {'stale': False}}},
        'views-health.json': {'updated_monotonic': 101.},
    }
    monkeypatch.setattr(results, 'read_mapping', lambda path, default=None: snapshots.get(path.name, {}))
    state = results.saved_status(settings, now=100.)
    assert 'receiver' not in state
    assert state['analysis']['result_age_seconds'] is None
    assert state['analysis']['service_stale']
    assert state['views']['service_stale']
    assert state['display']['stale']


def test_health_collectors_do_not_age_before_acquiring_cache_lock():
    from pendulum_pi.gps_health import GPSHealthCollector
    from pendulum_pi.time_health import UTCHealthCollector

    for cls in (GPSHealthCollector, UTCHealthCollector):
        clock = [100.]
        collector = cls(clock=lambda: clock[0])
        collector._sampled = 100.
        collector._cached.update(status='available')

        class PublishingLock:
            def __enter__(self):
                clock[0] = 101.
                collector._sampled = 101.

            def __exit__(self, *_):
                pass

        collector._lock = PublishingLock()
        state = collector.snapshot()
        assert state['fresh']
        assert state['sample_age_seconds'] == 0
        assert not collector.snapshot(now=100.)['fresh']


def test_session_rotation_between_reads_retries_capture(tmp_path, monkeypatch):
    settings = Settings(runtime_dir=tmp_path)
    reads = []
    new_capture = {'boot_id': 'boot', 'logging': {'session': 'new'}}

    def read(path, default=None):
        if path == tmp_path / 'status.json':
            reads.append(path)
            return new_capture if len(reads) > 1 else {
                'boot_id': 'boot', 'logging': {'session': 'old'}}
        if path.name == 'analysis.json':
            return {'published_monotonic': 20., 'result': {
                **new_capture, 'updated_monotonic': 19., 'display': {'stale': False}}}
        return {}

    monkeypatch.setattr(results, 'read_mapping', read)
    state = results.saved_status(settings, now=20.)
    assert len(reads) == 2
    assert state['logging']['session'] == 'new'
    assert state['analysis']['matches_capture_session']
    assert not state['display']['stale']
