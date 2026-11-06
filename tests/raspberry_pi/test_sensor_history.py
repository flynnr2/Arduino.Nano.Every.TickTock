"""Sensor retention stays bounded and preserves capture-time joins."""
from dataclasses import replace
import sqlite3

from pendulum_pi.config import Settings
from pendulum_pi import sensor_history


def test_existing_database_gets_index_and_retention_deletes_one_bounded_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(sensor_history, 'boot_id', lambda: 'current')
    monkeypatch.setattr(sensor_history.time, 'time', lambda: 1_800_000_000.)
    settings = replace(Settings(), data_dir=tmp_path)
    # Existing installations have this table but no epoch index.
    with sqlite3.connect(tmp_path / sensor_history.DATABASE) as connection:
        connection.execute('CREATE TABLE samples (boot TEXT, mono REAL, epoch REAL, payload TEXT, PRIMARY KEY(boot,mono))')
        connection.executemany('INSERT INTO samples VALUES (?,?,?,?)',
                               [('old', float(i), 1., '{}') for i in range(3000)])
    history = sensor_history.SensorHistory(settings)
    try:
        snapshot = dict(updated_monotonic=100., temperature_C=21.)
        history.append(snapshot)
        assert history.connection.execute('SELECT COUNT(*) FROM samples WHERE boot="old"').fetchone()[0] == 952
        assert sensor_history.as_of(settings, 100.) == snapshot
        assert sensor_history.as_of(settings, 99.) == {}
        # A later maintenance tick finishes the backlog, keeping fresh evidence.
        history.append(dict(snapshot, updated_monotonic=161.))
        assert history.connection.execute('SELECT COUNT(*) FROM samples WHERE boot="old"').fetchone()[0] == 0
        assert history.connection.execute('SELECT COUNT(*) FROM samples').fetchone()[0] == 2
        indexes = {row[1] for row in history.connection.execute('PRAGMA index_list(samples)')}
        assert 'sample_epoch' in indexes
    finally:
        history.close()
