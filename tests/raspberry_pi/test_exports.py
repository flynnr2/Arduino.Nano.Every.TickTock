"""Date-range packages preserve boundaries and cannot race retention."""
from dataclasses import replace
import fcntl
import gzip
import io
import json
import tarfile

import pytest

from pendulum_pi.common import atomic_json
from pendulum_pi.config import Settings, save_settings
from pendulum_pi.web import create_app


@pytest.fixture
def setup(tmp_path):
    config = tmp_path / 'config.json'
    settings = Settings(data_dir=tmp_path / 'data', runtime_dir=tmp_path / 'runtime')
    settings.data_dir.mkdir()
    settings.runtime_dir.mkdir()
    save_settings(config, settings)
    app = create_app(config)
    app.testing = True
    return app.test_client(), settings, config


def segment(root, index, start, end, compressed=False):
    path = f'session/segment-{index:06d}'
    directory = root / path
    directory.mkdir(parents=True)
    manifest = {'session': 'session', 'segment': index, 'started_utc': start,
                'closed_utc': end, 'time_semantics': 'host UTC only'}
    atomic_json(directory / 'manifest.json', manifest)
    files = {}
    for name in ('PCSW.CSV', 'PCPS.CSV', 'SUMMARY.CSV', 'RAW.jsonl'):
        payload = b'header\nrecord\n'
        filename = name + ('.gz' if compressed else '')
        if compressed:
            payload = gzip.compress(payload)
        (directory / filename).write_bytes(payload)
        files[name] = {'path': f'{path}/{filename}', 'bytes': len(payload), 'expired': False}
    return {**manifest, 'id': path, 'path': path, 'files': files, 'archive': {'verified': False}}


def seed(settings, compressed=False):
    segments = [segment(settings.data_dir, 1, '2026-01-01T00:00:00Z', '2026-01-08T00:00:00Z', compressed),
                segment(settings.data_dir, 2, '2026-01-08T00:00:00Z', '2026-01-15T00:00:00Z', compressed)]
    atomic_json(settings.data_dir / 'catalogue.json', {'format_version': 1, 'segments': segments})
    return segments


def params(**kwargs):
    return {'start': '2026-01-02', 'end': '2026-01-04', 'kind': 'measurements', **kwargs}


@pytest.mark.parametrize('compressed', [False, True])
def test_streaming_package_preserves_set_paths_manifest_and_time_provenance(setup, compressed):
    client, settings, _ = setup
    seed(settings, compressed)
    estimate = client.get('/api/export/estimate', query_string=params())
    assert estimate.status_code == 200
    assert estimate.json['segment_count'] == 1
    response = client.get('/api/export', query_string=params())
    assert response.status_code == 200
    assert len(response.data) <= estimate.json['maximum_download_bytes']
    with tarfile.open(fileobj=io.BytesIO(response.data), mode='r:gz') as archive:
        names = archive.getnames()
        assert 'session/segment-000001/manifest.json' in names
        filename = 'session/segment-000001/PCSW.CSV' + ('.gz' if compressed else '')
        payload = archive.extractfile(filename).read()
        assert (gzip.decompress(payload) if compressed else payload) == b'header\nrecord\n'
        assert not any('segment-000002' in n for n in names)
        assert not any('RAW' in n for n in names)
        metadata = json.load(archive.extractfile('export.json'))
        assert 'not verified Nano event UTC' in metadata['time_semantics']
        assert metadata['segments'][0]['started_utc'] == '2026-01-01T00:00:00Z'
        assert metadata['requested_end_utc_exclusive'] == '2026-01-04'


def test_overlap_end_exclusive_and_unclosed_excluded(setup):
    client, settings, _ = setup
    items = seed(settings)
    items[1]['closed_utc'] = None
    atomic_json(settings.data_dir / 'catalogue.json', {'segments': items})
    response = client.get('/api/export/estimate', query_string=params(end='2026-01-08'))
    assert response.json['segment_count'] == 1
    assert client.get('/api/export', query_string=params(start='2026-02-01', end='2026-02-02')).status_code == 404


@pytest.mark.parametrize('query', [params(start='bad'), params(start='2026-01-02T00:00:00'),
                                   params(start='2026-01-04'), params(end='2025-12-31'),
                                   params(kind='secret'), {}, params(start='2026-02-31')])
def test_invalid_selection(setup, query):
    client, settings, _ = setup
    seed(settings)
    assert client.get('/api/export/estimate', query_string=query).status_code == 400


def test_oversize_rejected_without_partial_package(setup):
    client, settings, config = setup
    items = seed(settings)
    entry = items[0]['files']['PCSW.CSV']
    with (settings.data_dir / entry['path']).open('wb') as stream:
        stream.truncate(2 * 1024**2)
    entry['bytes'] = 2 * 1024**2
    atomic_json(settings.data_dir / 'catalogue.json', {'segments': items})
    save_settings(config, replace(settings, export_max_mb=1))
    for route in ('/api/export', '/api/export/estimate'):
        response = client.get(route, query_string=params())
        assert response.status_code == 413
        assert 'narrower range' in response.json['error']


@pytest.mark.parametrize('attack', ['symlink', 'path', 'catalogue_link'])
def test_rejects_symlinks_and_catalogue_path_traversal(setup, tmp_path, attack):
    client, settings, _ = setup
    items = seed(settings)
    entry = items[0]['files']['PCSW.CSV']
    if attack == 'symlink':
        path = settings.data_dir / entry['path']
        path.unlink()
        path.symlink_to(tmp_path / 'config.json')
    elif attack == 'path':
        entry['path'] = '../config.json'
        atomic_json(settings.data_dir / 'catalogue.json', {'segments': items})
    else:
        (settings.data_dir / 'catalogue.json').unlink()
        (settings.data_dir / 'catalogue.json').symlink_to(tmp_path / 'config.json')
    assert client.get('/api/export', query_string=params()).status_code in (409, 503)


def test_expired_files_explicit_and_summary_kind(setup):
    client, settings, _ = setup
    items = seed(settings)
    items[0]['files']['PCPS.CSV']['expired'] = True
    atomic_json(settings.data_dir / 'catalogue.json', {'segments': items})
    estimate = client.get('/api/export/estimate', query_string=params()).json
    assert estimate['unavailable_file_count'] == 1
    assert estimate['segments'][0]['unavailable_files'] == ['PCPS.CSV']
    response = client.get('/api/export', query_string=params(kind='summary'))
    with tarfile.open(fileobj=io.BytesIO(response.data), mode='r:gz') as archive:
        assert 'session/segment-000001/SUMMARY.CSV' in archive.getnames()
        assert not any('PCSW' in name for name in archive.getnames())


def test_download_lock_and_semaphore_released_on_disconnect(setup):
    client, settings, _ = setup
    seed(settings)
    first = client.get('/api/export', query_string=params(), buffered=False)
    second = client.get('/api/export', query_string=params(), buffered=False)
    assert client.get('/api/export', query_string=params()).status_code == 429
    with (settings.data_dir / '.storage.lock').open('rb') as lock:
        with pytest.raises(BlockingIOError):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        first.close()
        with pytest.raises(BlockingIOError):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        second.close()
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert client.get('/api/export', query_string=params()).status_code == 200


def test_catalogue_pagination_and_gzip_file_download(setup):
    client, settings, _ = setup
    seed(settings, compressed=True)
    page = client.get('/api/catalogue?limit=1').json
    assert page['total'] == 2
    assert page['next_cursor'] == 1
    assert len(page['segments']) == 1
    assert client.get('/api/catalogue?cursor=1&limit=1').json['next_cursor'] is None
    assert client.get('/api/catalogue?limit=-1').status_code == 400
    assert client.get('/api/download/session/segment-000001/SUMMARY.CSV.gz').status_code == 200
    assert client.get('/api/download/catalogue.json').status_code == 200


def test_storage_status_reports_archive_setup_required(setup):
    client, settings, _ = setup
    atomic_json(settings.runtime_dir / 'storage.json', {'budget_bytes': 123, 'pending_segments': 2, 'errors': []})
    state = client.get('/api/status').json['storage']
    assert not state['archive_configured']
    assert state['budget_bytes'] == 123


def test_reversed_wall_clock_flagged_and_failed_sets_excluded(setup):
    client, settings, _ = setup
    items = seed(settings)
    items[0]['started_utc'], items[0]['closed_utc'] = items[0]['closed_utc'], items[0]['started_utc']
    items[1]['manifest'] = {'closed_reason': 'write_error'}
    atomic_json(settings.data_dir / 'catalogue.json', {'segments': items})
    result = client.get('/api/export/estimate', query_string=params(end='2026-01-16')).json
    assert result['segment_count'] == 1
    assert result['segments'][0]['host_clock_reversed']


def test_export_uses_catalogue_canonical_member_when_original_and_gzip_exist(setup):
    client, settings, _ = setup
    seed(settings, compressed=True)
    (settings.data_dir / 'session/segment-000001/PCSW.CSV').write_text('header\nrecord\n')
    response = client.get('/api/export', query_string=params())
    with tarfile.open(fileobj=io.BytesIO(response.data), mode='r:gz') as archive:
        assert 'session/segment-000001/PCSW.CSV.gz' in archive.getnames()
        assert 'session/segment-000001/PCSW.CSV' not in archive.getnames()


def test_storage_status_flags_stale_and_fresh_worker(setup):
    from pendulum_pi.common import utc_now
    client, settings, _ = setup
    assert client.get('/api/status').json['storage']['stale']
    atomic_json(settings.runtime_dir / 'storage.json', {'updated_utc': utc_now()})
    assert not client.get('/api/status').json['storage']['stale']
    atomic_json(settings.runtime_dir / 'storage.json', {'updated_utc': '2001-01-01T00:00:00Z'})
    assert client.get('/api/status').json['storage']['stale']


def test_internal_host_clock_excursion_selects_segment_and_preserves_extrema(setup):
    client, settings, _ = setup
    items = seed(settings)
    items[0]['manifest'] = {'min_record_utc': '2025-12-01T00:00:00Z',
                            'max_record_utc': '2026-02-01T00:00:00Z',
                            'host_clock_reversed': True, 'host_clock_discontinuities': 3}
    atomic_json(settings.data_dir / 'catalogue.json', {'segments': items})
    for start, end in [('2025-12-01', '2025-12-02'), ('2026-01-30', '2026-01-31')]:
        response = client.get('/api/export/estimate', query_string=params(start=start, end=end))
        assert response.status_code == 200
        assert response.json['segment_count'] == 1
        metadata = response.json['segments'][0]
        assert metadata['min_record_utc'] == '2025-12-01T00:00:00Z'
        assert metadata['max_record_utc'] == '2026-02-01T00:00:00Z'
        assert metadata['host_clock_reversed']
        assert metadata['host_clock_discontinuities'] == 3


def test_maintenance_returns_retryable_503_without_blocking_status_or_leaking_slots(setup):
    from pendulum_pi.storage import storage_lock
    client, settings, _ = setup
    seed(settings)
    with storage_lock(settings.data_dir):
        for route in ('/api/export', '/api/export/estimate', '/api/catalogue',
                      '/api/download/session/segment-000001/PCSW.CSV'):
            response = client.get(route, query_string=params())
            assert response.status_code == 503
            assert 'maintenance is running' in response.json['error']
        assert client.get('/api/status').status_code == 200
    first = client.get('/api/export', query_string=params(), buffered=False)
    second = client.get('/api/export', query_string=params(), buffered=False)
    assert first.status_code == second.status_code == 200
    first.close()
    second.close()
