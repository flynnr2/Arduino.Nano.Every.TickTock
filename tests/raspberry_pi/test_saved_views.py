"""HTTP queues fits; one independent worker caches and deduplicates them."""
from dataclasses import replace
import json
import threading

from pendulum_pi.config import Settings, save_settings
from pendulum_pi import views


def test_same_live_minute_shares_job_and_worker_publishes_cache(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path/'data', runtime_dir=tmp_path/'run', min_free_mb=0)
    config = tmp_path/'config.json'
    save_settings(config, settings)
    first = views.request_environment(settings, 100., 1000.)
    second = views.request_environment(settings, 105., 1005.)
    assert first['state'] == second['state'] == 'updating'
    assert len(list((settings.runtime_dir/'views').glob('*.job.json'))) == 1
    stop = threading.Event()
    calls = []
    def fit(root, **job):
        calls.append(job)
        stop.set()
        return {'segments': [], 'environmental_window_seconds': 600}
    monkeypatch.setattr(views, 'query_environmental_relationships', fit)
    monkeypatch.setattr(views.PhaseSnapshots, 'get', lambda *_: {'charts': [], 'state': 'ready'})
    views.run_views(settings, config, stop)
    assert calls == [{'start': 120, 'end': 960, 'session': None}]
    ready = views.request_environment(settings, 105., 1005.)
    assert ready['state'] == 'ready'
    assert ready['environmental_window_seconds'] == 600
    assert not list((settings.runtime_dir/'views').glob('*.job.json'))


def test_cache_queue_is_bounded_and_fits_are_never_run_in_request(tmp_path, monkeypatch):
    settings = Settings(runtime_dir=tmp_path/'run')
    monkeypatch.setattr(views, 'query_environmental_relationships', lambda *_: (_ for _ in ()).throw(AssertionError('fit in HTTP request')))
    for i in range(8):
        assert views.request_environment(settings, i*600., i*600.+120.)['state'] == 'updating'
    assert views.request_environment(settings, 50000., 50100.)['state'] == 'busy'
    assert len(list((settings.runtime_dir/'views').glob('*.job.json'))) == 8
