"""HTTP queues fits; one independent worker caches and deduplicates them."""
from dataclasses import replace
import json
import sqlite3
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
    class Query:
        def __init__(self, root, **job): self.root, self.job = root, job
        def step(self):
            self.result = fit(self.root, **self.job)
            return True
        def close(self): pass
    monkeypatch.setattr(views, 'EnvironmentalQuery', Query)
    monkeypatch.setattr(views.PhaseSnapshots, 'get', lambda *_: {'charts': [], 'state': 'ready'})
    views.run_views(settings, config, stop)
    assert calls == [{'start': 120, 'end': 960, 'session': None}]
    ready = views.request_environment(settings, 105., 1005.)
    assert ready['state'] == 'ready'
    assert ready['environmental_window_seconds'] == 600
    assert not list((settings.runtime_dir/'views').glob('*.job.json'))


def test_cache_queue_is_bounded_and_fits_are_never_run_in_request(tmp_path, monkeypatch):
    settings = Settings(runtime_dir=tmp_path/'run')
    monkeypatch.setattr(views, 'EnvironmentalQuery', lambda *_, **kw: (_ for _ in ()).throw(AssertionError('fit in HTTP request')))
    for i in range(8):
        assert views.request_environment(settings, i*600., i*600.+120.)['state'] == 'updating'
    assert views.request_environment(settings, 50000., 50100.)['state'] == 'busy'
    assert len(list((settings.runtime_dir/'views').glob('*.job.json'))) == 8


def test_long_job_publishes_health_between_steps_and_closes_on_stop(tmp_path, monkeypatch):
    settings = Settings(runtime_dir=tmp_path/'run')
    config = tmp_path/'config.json'
    save_settings(config, settings)
    views.request_environment(settings, 100., 1000.)
    stop = threading.Event()
    instances = []
    class Query:
        def __init__(self, *_args, **_kw):
            self.steps, self.closed = 0, False
            instances.append(self)
        def step(self):
            self.steps += 1
            if self.steps > 1:
                assert json.loads((settings.runtime_dir/'views-health.json').read_text())['state'] == 'running'
            if self.steps == 3:
                stop.set()
            return False
        def close(self): self.closed = True
    monkeypatch.setattr(views, 'EnvironmentalQuery', Query)
    monkeypatch.setattr(views.PhaseSnapshots, 'get', lambda *_: {'charts': [], 'state': 'ready'})
    views.run_views(settings, config, stop)
    assert instances[0].steps == 3 and instances[0].closed
    assert list((settings.runtime_dir/'views').glob('*.job.json'))  # Resume after restart.


def test_query_timeout_is_explained_instead_of_no_data(tmp_path, monkeypatch):
    settings = Settings(runtime_dir=tmp_path/'run')
    config = tmp_path/'config.json'
    save_settings(config, settings)
    views.request_environment(settings, 100., 1000.)
    stop = threading.Event()
    class Query:
        def __init__(self, *_args, **_kw): pass
        def step(self):
            stop.set()
            error = sqlite3.OperationalError('interrupted')
            error.sqlite_errorcode = sqlite3.SQLITE_INTERRUPT
            raise error
        def close(self): pass
    monkeypatch.setattr(views, 'EnvironmentalQuery', Query)
    monkeypatch.setattr(views.PhaseSnapshots, 'get', lambda *_: {'charts': []})
    views.run_views(settings, config, stop)
    result = views.request_environment(settings, 100., 1000.)
    assert result['state'] == 'unavailable'
    assert 'took too long' in result['message'] and 'shorter range' in result['message']
    assert 'interrupted' not in result['message']


def test_invalid_queued_job_does_not_block_following_jobs(tmp_path, monkeypatch):
    settings = Settings(runtime_dir=tmp_path/'run')
    config = tmp_path/'config.json'
    save_settings(config, settings)
    views.request_environment(settings, 100., 1000.)
    root = settings.runtime_dir/'views'
    invalid = next(root.glob('*.job.json'))
    invalid.write_text('{}')
    views.request_environment(settings, 2000., 3000.)
    stop = threading.Event()
    class Query:
        def __init__(self, _root, start, end, session): pass
        def step(self):
            self.result = {'segments': []}
            stop.set()
            return True
        def close(self): pass
    monkeypatch.setattr(views, 'EnvironmentalQuery', Query)
    monkeypatch.setattr(views.PhaseSnapshots, 'get', lambda *_: {'charts': []})
    views.run_views(settings, config, stop)
    assert not list(root.glob('*.job.json'))
    failed = json.loads(invalid.with_name(invalid.name.replace('.job.json', '.result.json')).read_text())
    assert failed['state'] == 'unavailable'
    assert views.request_environment(settings, 2000., 3000.)['state'] == 'ready'
