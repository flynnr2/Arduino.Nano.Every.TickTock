"""Real files exercise compression, interrupted transitions and verified retention."""
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pendulum_pi.config import Settings
from pendulum_pi import storage


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc).timestamp()


def settings(tmp_path, **overrides):
    values = dict(asdict(Settings()), data_dir=tmp_path / 'data', runtime_dir=tmp_path / 'runtime')
    values.update(storage_budget_mb=8192, min_free_mb=0, archive_dir=None,
                  measurement_retention_days=90, diagnostic_retention_days=14,
                  summary_retention_days=1825, compression_enabled=True,
                  storage_interval_seconds=60)
    values.update(overrides)
    return SimpleNamespace(**values)


def segment(config, *, number=1, closed=True, age=0, payload=None):
    directory = config.data_dir / 'session-abc' / f'segment-{number:06d}'
    directory.mkdir(parents=True)
    files = {}
    for name in storage.DATA_FILES:
        data = (payload or {}).get(name, b'epoch,value\n123,4\n')
        (directory / name).write_bytes(data)
        files[name] = {'bytes': len(data), 'rows': 1}
    end = datetime.fromtimestamp(NOW - age * 86400, timezone.utc).isoformat()
    start = datetime.fromtimestamp(NOW - (age + 7) * 86400, timezone.utc).isoformat()
    manifest = dict(format_version=2, session='session-abc', segment=number,
                    started_utc=start, first_record_utc=start, last_record_utc=end,
                    files=files)
    if closed:
        manifest.update(closed_utc=end, closed_reason='rotation')
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    return directory, manifest


def state(directory):
    return json.loads((directory / 'lifecycle.json').read_text())


def archive_settings(tmp_path, **overrides):
    archive = tmp_path / 'archive'
    archive.mkdir()
    return settings(tmp_path, archive_dir=archive, **overrides)


def test_closed_set_compression_is_verified_and_idempotent_active_is_untouched(tmp_path):
    config = settings(tmp_path)
    closed, manifest = segment(config)
    active, _ = segment(config, number=2, closed=False)
    status = storage.maintain_storage(config, now=NOW)
    assert not status['errors']
    assert status['pending_segments'] == 0
    for name in storage.DATA_FILES:
        assert not (closed / name).exists()
        assert gzip.decompress((closed / (name + '.gz')).read_bytes()) == b'epoch,value\n123,4\n'
        assert (active / name).exists()
    assert json.loads((closed / 'manifest.json').read_text()) == manifest
    before = {p.name: p.read_bytes() for p in closed.iterdir()}
    assert not storage.maintain_storage(config, now=NOW)['errors']
    assert before == {p.name: p.read_bytes() for p in closed.iterdir()}
    entry = json.loads((config.data_dir / 'catalogue.json').read_text())['segments'][0]
    assert entry['id'] == 'session-abc/segment-000001'
    assert entry['files']['PCSW.CSV']['rows'] == 1
    assert entry['files']['PCSW.CSV']['uncompressed_sha256'] == hashlib.sha256(b'epoch,value\n123,4\n').hexdigest()


def test_no_archive_never_expires_sole_copy(tmp_path):
    config = settings(tmp_path)
    directory, _ = segment(config, age=2000)
    status = storage.maintain_storage(config, now=NOW)
    assert status['blocked']
    assert status['cleanup_blocked_segments'] == ['session-abc/segment-000001']
    assert all((directory / (name + '.gz')).exists() for name in storage.DATA_FILES)
    assert all(not record['expired'] for record in state(directory)['files'].values())


def test_verified_archive_and_separate_retention_preserve_identity(tmp_path):
    config = archive_settings(tmp_path)
    directory, manifest = segment(config, age=20)
    status = storage.maintain_storage(config, now=NOW)
    assert not status['errors']
    lifecycle = state(directory)
    assert lifecycle['archive']['verified']
    destination = config.archive_dir / lifecycle['id']
    for name in storage.DATA_FILES:
        assert (destination / (name + '.gz')).exists()
        assert (directory / (name + '.gz')).exists() == (name not in storage.DIAGNOSTICS)
        assert lifecycle['files'][name]['expired'] == (name in storage.DIAGNOSTICS)
    assert json.loads((directory / 'manifest.json').read_text()) == manifest
    assert not storage.maintain_storage(config, now=NOW + 100 * 86400)['errors']
    assert not (directory / 'PCSW.CSV.gz').exists()
    assert (directory / 'SUMMARY.CSV.gz').exists()
    assert (directory / 'manifest.json').exists()
    assert not storage.maintain_storage(config, now=NOW + 2000 * 86400)['errors']
    assert not (directory / 'SUMMARY.CSV.gz').exists()


def test_corrupted_archive_blocks_deletion(tmp_path):
    config = archive_settings(tmp_path)
    directory, _ = segment(config)
    storage.maintain_storage(config, now=NOW)
    destination = Path(state(directory)['archive']['path'])
    (destination / 'RAW.jsonl.gz').write_bytes(b'corrupted')
    status = storage.maintain_storage(config, now=NOW + 100 * 86400)
    assert status['blocked']
    assert any('checksum mismatch' in error for error in status['errors'])
    assert not state(directory)['archive']['verified']
    assert state(directory)['archive']['error']
    assert all((directory / (name + '.gz')).exists() for name in storage.DATA_FILES)


def test_missing_archive_root_is_not_created_and_prevents_deletion(tmp_path):
    config = settings(tmp_path, archive_dir=tmp_path / 'unmounted')
    directory, _ = segment(config, age=100)
    status = storage.maintain_storage(config, now=NOW)
    assert not config.archive_dir.exists()
    assert not status['archive_available']
    assert status['errors']
    assert (directory / 'PCSW.CSV.gz').exists()


def test_archive_cannot_overlap_data(tmp_path):
    config = settings(tmp_path, archive_dir=tmp_path)
    directory, _ = segment(config, age=100)
    status = storage.maintain_storage(config, now=NOW)
    assert any('separate' in error for error in status['errors'])
    assert (directory / 'RAW.jsonl.gz').exists()


def test_compression_publication_crash_is_recovered_without_losing_original(tmp_path, monkeypatch):
    config = settings(tmp_path)
    directory, _ = segment(config)
    save = storage._save
    failures = []

    def fail_once(path, value):
        if path.name == 'lifecycle.json' and not failures:
            failures.append(True)
            raise OSError('simulated power failure after compression publication')
        save(path, value)

    monkeypatch.setattr(storage, '_save', fail_once)
    status = storage.maintain_storage(config, now=NOW)
    assert status['errors']
    assert (directory / 'PCPS.CSV').exists()
    first = sorted(storage.DATA_FILES)[0]
    assert (directory / (first + '.gz')).exists()
    assert (directory / first).exists()
    assert not storage.maintain_storage(config, now=NOW)['errors']
    assert not (directory / 'PCPS.CSV').exists()


def test_existing_bad_compressed_copy_never_replaces_original(tmp_path):
    config = settings(tmp_path)
    directory, _ = segment(config)
    (directory / 'PCPS.CSV.gz').write_bytes(gzip.compress(b'wrong content'))
    status = storage.maintain_storage(config, now=NOW)
    assert status['errors']
    assert (directory / 'PCPS.CSV').read_bytes() == b'epoch,value\n123,4\n'


def test_closed_missing_or_truncated_member_is_never_archived(tmp_path):
    config = archive_settings(tmp_path)
    directory, _ = segment(config)
    (directory / 'RAW.jsonl').unlink()
    status = storage.maintain_storage(config, now=NOW)
    assert any('incomplete' in error for error in status['errors'])
    assert not (config.archive_dir / 'session-abc').exists()
    (directory / 'RAW.jsonl').write_bytes(b'short')
    status = storage.maintain_storage(config, now=NOW)
    assert any('byte count mismatch' in error for error in status['errors'])
    assert not (config.archive_dir / 'session-abc').exists()


def test_high_water_cleanup_removes_diagnostics_before_measurements(tmp_path):
    config = archive_settings(tmp_path, compression_enabled=False, storage_budget_mb=.20)
    directory, _ = segment(config, payload={'RAW.jsonl': b'x' * 180000, 'PCPS.CSV': b'm' * 12000})
    status = storage.maintain_storage(config, now=NOW)
    assert not status['errors']
    assert not (directory / 'RAW.jsonl').exists()
    assert (directory / 'PCPS.CSV').exists()
    assert (directory / 'SUMMARY.CSV').exists()
    assert status['usage_bytes'] < status['budget_bytes'] * .8


def test_expiry_commit_before_unlink_recovers_after_interruption(tmp_path, monkeypatch):
    config = archive_settings(tmp_path)
    directory, _ = segment(config)
    storage.maintain_storage(config, now=NOW)
    unlink = Path.unlink
    failed = []

    def fail_once(path, *args, **kwargs):
        if path == directory / 'PI.CSV.gz' and not failed:
            failed.append(True)
            raise OSError('interrupted unlink')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_once)
    assert storage.maintain_storage(config, now=NOW + 20 * 86400)['errors']
    assert state(directory)['files']['PI.CSV']['expired']
    assert (directory / 'PI.CSV.gz').exists()
    assert not storage.maintain_storage(config, now=NOW + 20 * 86400)['errors']
    assert not (directory / 'PI.CSV.gz').exists()


def test_symlink_member_is_rejected_without_following(tmp_path):
    config = settings(tmp_path)
    directory, _ = segment(config)
    unrelated = tmp_path / 'precious'
    unrelated.write_text('keep me')
    (directory / 'RAW.jsonl').unlink()
    (directory / 'RAW.jsonl').symlink_to(unrelated)
    with pytest.raises(ValueError, match='symlink'):
        storage.maintain_storage(config, now=NOW)
    assert unrelated.read_text() == 'keep me'


def test_only_one_backlogged_segment_processed_per_pass(tmp_path):
    config = settings(tmp_path)
    first, _ = segment(config)
    second, _ = segment(config, number=2)
    assert storage.maintain_storage(config, now=NOW)['pending_segments'] == 1
    assert (first / 'RAW.jsonl.gz').exists()
    assert (second / 'RAW.jsonl').exists()
    assert storage.maintain_storage(config, now=NOW)['pending_segments'] == 0
    assert (second / 'RAW.jsonl.gz').exists()


def test_corrupt_manifest_does_not_block_other_completed_sets(tmp_path):
    config = settings(tmp_path)
    broken, _ = segment(config)
    healthy, _ = segment(config, number=2)
    (broken / 'manifest.json').write_text('{broken')
    status = storage.maintain_storage(config, now=NOW)
    assert status['errors']
    assert (broken / 'PCSW.CSV').exists()
    assert (healthy / 'PCSW.CSV.gz').exists()


def test_bad_compression_does_not_starve_later_segment(tmp_path):
    config = settings(tmp_path)
    broken, _ = segment(config)
    healthy, _ = segment(config, number=2)
    (broken / 'PCPS.CSV.gz').write_bytes(gzip.compress(b'wrong'))
    assert storage.maintain_storage(config, now=NOW)['errors']
    assert not storage.maintain_storage(config, now=NOW)['errors']
    assert (healthy / 'PCSW.CSV.gz').exists()
    assert (broken / 'PCSW.CSV').exists()


def test_full_data_budget_offloads_originals_to_recover_without_scratch(tmp_path):
    config = archive_settings(tmp_path, storage_budget_mb=.20)
    directory, _ = segment(config, payload={'RAW.jsonl': b'x' * 200000})
    status = storage.maintain_storage(config, now=NOW)
    assert not status['errors']
    lifecycle = state(directory)
    assert lifecycle['archive']['verified']
    assert not lifecycle['files']['RAW.jsonl']['compressed']
    assert lifecycle['files']['RAW.jsonl']['compression_deferred']
    assert (config.archive_dir / lifecycle['id'] / 'RAW.jsonl').read_bytes() == b'x' * 200000
    assert not (directory / 'RAW.jsonl').exists()
    assert status['usage_bytes'] < status['budget_bytes'] * .8
    assert not storage.maintain_storage(config, now=NOW)['errors']


def test_compression_setting_change_does_not_change_archived_representation(tmp_path):
    config = archive_settings(tmp_path, compression_enabled=False)
    directory, _ = segment(config)
    assert not storage.maintain_storage(config, now=NOW)['errors']
    config.compression_enabled = True
    assert not storage.maintain_storage(config, now=NOW + 20 * 86400)['errors']
    assert (directory / 'PCSW.CSV').exists()
    assert not (directory / 'PCSW.CSV.gz').exists()
    assert (config.archive_dir / 'session-abc/segment-000001/PCSW.CSV').exists()


def test_archive_session_directory_is_durable_before_acknowledgement(tmp_path, monkeypatch):
    config = archive_settings(tmp_path)
    directory, _ = segment(config)
    sync, save = storage._sync_dir, storage._save
    events = []

    def note_sync(path):
        events.append(('sync', path))
        sync(path)

    def note_save(path, value):
        if path.name == 'lifecycle.json' and value.get('archive', {}).get('verified'):
            events.append(('ack', path))
        save(path, value)

    monkeypatch.setattr(storage, '_sync_dir', note_sync)
    monkeypatch.setattr(storage, '_save', note_save)
    storage.maintain_storage(config, now=NOW)
    assert events.index(('sync', config.archive_dir)) < events.index(('ack', directory / 'lifecycle.json'))
    assert events.index(('sync', config.archive_dir / 'session-abc')) < events.index(('ack', directory / 'lifecycle.json'))


def test_shared_export_lock_can_fail_fast_during_mutation(tmp_path):
    config = settings(tmp_path)
    with storage.storage_lock(config.data_dir):
        with pytest.raises(BlockingIOError):
            with storage.storage_lock(config.data_dir, exclusive=False, blocking=False):
                pytest.fail('overlapping mutation and export')
    with storage.storage_lock(config.data_dir, exclusive=False):
        with storage.storage_lock(config.data_dir, exclusive=False, blocking=False):
            pass


def test_worker_singleton_and_reload_stop(tmp_path, monkeypatch):
    config = settings(tmp_path)
    config.runtime_dir.mkdir()
    with (config.runtime_dir / 'storage-worker.lock').open('w') as locked:
        fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='already running'):
            storage.run_storage(config, tmp_path / 'settings.json')

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            assert seconds == 60
            self.stopped = True

    calls = []
    monkeypatch.setattr(storage, 'load_settings', lambda path: config)
    monkeypatch.setattr(storage, 'maintain_storage', lambda current: calls.append(current))
    storage.run_storage(config, tmp_path / 'settings.json', Stop())
    assert calls == [config]


def test_growth_estimates_wait_for_meaningful_history(tmp_path):
    config = settings(tmp_path)
    directory, manifest = segment(config)
    manifest.update(first_record_ts_ms=0, last_record_ts_ms=1)
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    status = storage.maintain_storage(config, now=NOW)
    assert status['bytes_per_day_estimate'] is None
    assert status['seconds_until_full_estimate'] is None
    assert status['unarchived_segments'] == 1
    second, manifest = segment(config, number=2)
    manifest.update(first_record_ts_ms=0, last_record_ts_ms=3600000)
    manifest['files']['PCPS.CSV']['rows'] = 3600
    (second / 'manifest.json').write_text(json.dumps(manifest))
    status = storage.maintain_storage(config, now=NOW)
    assert status['bytes_per_day_estimate'] > 0


def test_fully_expired_tombstones_leave_catalogue_but_identity_remains_archived(tmp_path):
    config = archive_settings(tmp_path)
    directory, _ = segment(config, age=2000)
    status = storage.maintain_storage(config, now=NOW)
    assert not status['errors']
    assert not directory.exists()
    assert not directory.parent.exists()
    assert json.loads((config.data_dir / 'catalogue.json').read_text())['segments'] == []
    assert (config.archive_dir / 'session-abc/segment-000001/manifest.json').exists()
    assert all((config.archive_dir / 'session-abc/segment-000001' / (name + '.gz')).exists()
               for name in storage.DATA_FILES)


def test_corrupt_archive_preserves_tombstones_and_remaining_local_copies(tmp_path):
    config = archive_settings(tmp_path)
    directory, _ = segment(config)
    storage.maintain_storage(config, now=NOW)
    (config.archive_dir / 'session-abc/segment-000001/PCPS.CSV.gz').write_bytes(b'corrupt')
    status = storage.maintain_storage(config, now=NOW + 2000 * 86400)
    assert status['errors']
    assert (directory / 'manifest.json').exists()
    assert (directory / 'lifecycle.json').exists()
    assert (directory / 'SUMMARY.CSV.gz').exists()


def test_retirement_rename_crash_is_recovered(tmp_path, monkeypatch):
    config = archive_settings(tmp_path)
    directory, _ = segment(config, age=2000)
    finish = storage._finish_retired
    failed = []

    def fail_once(path, archive):
        if not failed:
            failed.append(True)
            raise OSError('power lost after retirement rename')
        finish(path, archive)

    monkeypatch.setattr(storage, '_finish_retired', fail_once)
    assert storage.maintain_storage(config, now=NOW)['errors']
    staged = directory.with_name('.expired-' + directory.name)
    assert (staged / '.retirement.json').exists()
    assert not storage.maintain_storage(config, now=NOW)['errors']
    assert not staged.exists()
    assert not directory.parent.exists()


def test_unknown_members_prevent_tombstone_deletion(tmp_path):
    config = archive_settings(tmp_path)
    directory, _ = segment(config, age=2000)
    (directory / 'notes.txt').write_text('retain unknown user files')
    status = storage.maintain_storage(config, now=NOW)
    assert any('unexpected local members' in error for error in status['errors'])
    assert (directory / 'notes.txt').read_text() == 'retain unknown user files'
    assert (directory / 'manifest.json').exists()


def test_repaired_uncompressed_archive_recovers_without_representation_change(tmp_path):
    config = archive_settings(tmp_path, compression_enabled=False)
    directory, _ = segment(config)
    storage.maintain_storage(config, now=NOW)
    archived = config.archive_dir / 'session-abc/segment-000001/RAW.jsonl'
    original = archived.read_bytes()
    archived.write_bytes(b'corrupt')
    config.compression_enabled = True
    assert storage.maintain_storage(config, now=NOW + 20 * 86400)['errors']
    assert not state(directory)['archive']['verified']
    assert state(directory)['archive']['immutable']
    archived.write_bytes(original)
    assert not storage.maintain_storage(config, now=NOW + 20 * 86400)['errors']
    assert state(directory)['archive']['verified']
    assert (directory / 'PCSW.CSV').exists()
    assert not (directory / 'PCSW.CSV.gz').exists()


def test_malformed_lifecycle_is_isolated_from_healthy_backlog(tmp_path):
    config = settings(tmp_path)
    broken, _ = segment(config)
    healthy, _ = segment(config, number=2)
    (broken / 'lifecycle.json').write_text(json.dumps({'id': 'session-abc/segment-000001', 'files': []}))
    status = storage.maintain_storage(config, now=NOW)
    assert any('lifecycle structure' in error for error in status['errors'])
    assert (broken / 'PCSW.CSV').exists()
    assert (healthy / 'PCSW.CSV.gz').exists()
