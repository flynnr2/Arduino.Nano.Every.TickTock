"""Bounded restart checkpoint of already calibrated swings, written off-thread."""
from __future__ import annotations

import json
import math
import queue
import threading
from pathlib import Path

from .common import atomic_json
from .display import MEAN_MODEL, WINDOW_SECONDS, MAX_WINDOW_SAMPLES

CHECKPOINT = 'swing-mean-checkpoint.json'
MAX_BYTES = 2 * 1024 * 1024


def trusted_utc(health):
    return health.get('status') == 'synchronized' and health.get('fresh') is True


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class MeanCheckpoint:
    def __init__(self, data_dir, boot_id, source):
        self.path = Path(data_dir) / CHECKPOINT
        self.boot_id, self.source = boot_id, source
        self.pending = None
        self.health = {'state': 'waiting', 'error': None}
        self.queue = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.thread = None
        self.last_submit = None
        if source == 'serial':
            try:
                with self.path.open('rb') as stream:
                    payload = stream.read(MAX_BYTES + 1)
                if len(payload) > MAX_BYTES:
                    raise ValueError('checkpoint exceeds size limit')
                value = json.loads(payload)
                if not isinstance(value, dict) or value.get('version') != 1:
                    raise ValueError('unsupported mean checkpoint')
                self.pending = value
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError) as error:
                self.health.update(state='rejected', error=str(error))

    def start(self):
        if self.source == 'serial' and self.thread is None:
            self.thread = threading.Thread(target=self._run, name='mean-checkpoint', daemon=True)
            self.thread.start()

    def _run(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                value = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                atomic_json(self.path, value, durable=True)
                self.health.update(state='saved', error=None)
            except (OSError, ValueError) as error:
                self.health.update(state='error', error=str(error))
            finally:
                self.queue.task_done()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def restore(self, display, now, epoch, health):
        """Same-boot monotonic age is authoritative; across boots require UTC.

        Fresh PPS qualification and at least one current swing are prerequisites.
        A checkpoint never supplies clock scale, sequence or forecast state.
        """
        value = self.pending
        if value is not None and display.window.filling_seconds >= WINDOW_SECONDS:
            self.pending = None
            self.health.update(state='fresh window complete', error=None)
            return False
        if (value is None or not display.window.samples or display.clock.calibrated_at is None
                or display.last_swing_at is None or now - display.last_swing_at >= 10
                or display.calibration_paused or display.clock.holding(now)):
            return False
        same_boot = bool(self.boot_id and value.get('boot_id') == self.boot_id)
        if not same_boot and not trusted_utc(health):
            self.health.update(state='awaiting verified UTC')
            return False
        self.pending = None
        try:
            if (value.get('source') != 'serial' or value.get('model') != MEAN_MODEL
                    or value.get('nominal_hz') != display.nominal_hz
                    or value.get('pps_holdover_seconds') != display.pps_holdover_seconds):
                raise ValueError('checkpoint measurement configuration differs')
            saved = value.get('saved_monotonic') if same_boot else value.get('saved_epoch')
            current = now if same_boot else epoch
            if not finite(saved) or not finite(current):
                raise ValueError('checkpoint has no verified age')
            age = current - saved
            if not 0 <= age < WINDOW_SECONDS:
                raise ValueError('checkpoint is expired or from the future')
            samples = value.get('samples')
            if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_WINDOW_SAMPLES:
                raise ValueError('invalid checkpoint sample count')
            restored = []
            previous_age = WINDOW_SECONDS
            first_fresh = display.window.sample_times[0]
            for row in samples:
                if not isinstance(row, list) or len(row) != 2:
                    raise ValueError('invalid checkpoint sample')
                sample_age, parts = row
                if (not finite(sample_age) or not 0 <= sample_age <= previous_age
                        or not isinstance(parts, list) or len(parts) != 4
                        or not all(finite(part) and part > 0 for part in parts)
                        or sum(parts) > 0x7fffffff * 1e6 / display.nominal_hz):
                    raise ValueError('invalid calibrated checkpoint sample')
                previous_age = sample_age
                at = now - age - sample_age
                if age + sample_age < WINDOW_SECONDS and finite(first_fresh) and at < first_fresh:
                    restored.append((at, tuple(parts)))
            if len(restored) + len(display.window.samples) > MAX_WINDOW_SAMPLES:
                raise ValueError("combined mean exceeds sample limit")
            if not restored:
                raise ValueError('checkpoint has no eligible preceding swings')
            display.window.restore(restored, now - age)
            self.health.update(state='restored', error=None, restored_samples=len(restored))
            return True
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as error:
            self.health.update(state='rejected', error=str(error))
            return False

    def submit(self, display, now, epoch, health, *, force=False):
        if (self.source != 'serial' or self.thread is None or self.pending is not None
                or display.last_swing_at is None
                or display.calibration_paused or now - display.last_swing_at >= 10
                or display.clock.holding(now) or display.clock.calibrated_at is None):
            return False
        if not force and self.last_submit is not None and now - self.last_submit < 30:
            return False
        samples = display.window.checkpoint_samples(now)
        if not samples:
            return False
        value = {'version': 1, 'source': self.source, 'model': MEAN_MODEL,
                 'nominal_hz': display.nominal_hz,
                 'pps_holdover_seconds': display.pps_holdover_seconds,
                 'boot_id': self.boot_id, 'saved_monotonic': now,
                 'saved_epoch': epoch if trusted_utc(health) else None,
                 'samples': samples}
        try:
            self.queue.put_nowait(value)
        except queue.Full:
            return False
        self.last_submit = now
        return True
