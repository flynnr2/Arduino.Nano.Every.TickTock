"""Timestamped sensor evidence for as-of joins after a capture backlog."""
from __future__ import annotations

import json
import sqlite3
import time

from .common import boot_id

DATABASE = 'sensor-history.sqlite3'


class SensorHistory:
    def __init__(self, settings):
        self.settings = settings
        self.connection = None
        self.last = None
        self.last_prune = 0
        self.boot = boot_id()

    def append(self, snapshot):
        now = snapshot['updated_monotonic']
        if self.last is not None and now - self.last < self.settings.sensor_interval_seconds:
            return
        if self.connection is None:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.settings.data_dir / DATABASE, timeout=.2)
            self.connection.execute('PRAGMA journal_mode=WAL')
            self.connection.execute('PRAGMA synchronous=NORMAL')
            self.connection.execute('PRAGMA max_page_count=16384')  # 64 MiB with default pages.
            self.connection.execute('CREATE TABLE IF NOT EXISTS samples (boot TEXT, mono REAL, epoch REAL, payload TEXT, PRIMARY KEY(boot,mono))')
            self.connection.execute('CREATE INDEX IF NOT EXISTS sample_epoch ON samples(epoch)')
        with self.connection:
            pages = self.connection.execute('PRAGMA page_count').fetchone()[0]
            free = self.connection.execute('PRAGMA freelist_count').fetchone()[0]
            if pages - free > 13000:
                self.connection.execute('DELETE FROM samples WHERE rowid IN (SELECT rowid FROM samples ORDER BY epoch LIMIT 2048)')
            self.connection.execute('INSERT OR REPLACE INTO samples VALUES (?,?,?,?)',
                                    (self.boot, now, time.time(), json.dumps(snapshot, allow_nan=False)))
            if now - self.last_prune >= 60:
                # Two days comfortably exceed the bounded serial backlog. Values
                # joined to captures are retained with those captures thereafter.
                self.connection.execute('DELETE FROM samples WHERE rowid IN '
                                        '(SELECT rowid FROM samples WHERE epoch < ? ORDER BY epoch LIMIT 2048)',
                                        (time.time() - 2 * 86400,))
                self.last_prune = now
        self.last = now

    def close(self):
        if self.connection:
            self.connection.close()


def as_of(settings, mono):
    path = settings.data_dir / DATABASE
    if not path.exists():
        return {}
    connection = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=.05)
    try:
        row = connection.execute('SELECT payload FROM samples WHERE boot=? AND mono<=? ORDER BY mono DESC LIMIT 1',
                                 (boot_id(), mono)).fetchone()
        return json.loads(row[0]) if row else {}
    finally:
        connection.close()
