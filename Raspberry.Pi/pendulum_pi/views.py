"""Scheduled phase analysis and bounded, cached custom-range jobs.

HTTP handlers only request jobs and read published JSON. Heavy computations
run in this service regardless of the number of browsers.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time

from .common import atomic_json, read_json
from .config import load_settings
from .environmental import query_environmental_relationships
from .phase import PhaseSnapshots

MAX_JOBS = 8
MAX_CACHE = 32


def request_environment(settings, start, end, session=None):
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
            or not 0 <= start < end or end - start > 366 * 86400):
        raise ValueError('Use a finite range up to 366 days with start before end')
    if session is not None and (not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', session)):
        raise ValueError('Invalid history session')
    # Stable minute cuts let live clients share work instead of generating a new
    # regression for each request. Sub-minute selections retain their exact cut.
    first, last = math.ceil(start / 60) * 60, math.floor(end / 60) * 60
    if first >= last:
        first, last = start, end
    job = dict(start=first, end=last, session=session)
    key = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()
    root = settings.runtime_dir / 'views'
    root.mkdir(parents=True, exist_ok=True)
    cached = read_json(root / (key + '.result.json'), {}) or {}
    age = time.time() - cached.get('generated_epoch', float('-inf'))
    pending = root / (key + '.job.json')
    with (root / 'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        jobs = list(root.glob('*.job.json'))
        if not pending.exists() and (not cached or age >= 60):
            if len(jobs) >= MAX_JOBS:
                return dict(cached.get('result') or {'segments': []}, state='busy',
                            message='Analysis queue is busy; this range will retry shortly.')
            atomic_json(pending, job)
    result = dict(cached.get('result') or {'segments': []})
    result.update(state='updating' if pending.exists() else cached.get('state', 'ready'),
                  requested_start=start, requested_end=end,
                  cache_start=first, cache_end=last,
                  generated_epoch=cached.get('generated_epoch'),
                  message=cached.get('message') or ('Calculating saved environmental relationships.' if pending.exists() else None))
    return result


def run_views(settings, config_path, stop=None):
    stop = stop or threading.Event()
    root = settings.runtime_dir / 'views'
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another saved-view worker is already running') from None
        phases = PhaseSnapshots()
        previous = read_json(settings.runtime_dir / 'phase.json', {}) or {}
        if isinstance(previous, dict) and previous.get('segment') and previous.get('charts'):
            phases.key = (str(settings.data_dir), previous['segment'])
            phases.result = previous
        last_phase = float('-inf')
        while not stop.is_set():
            try:
                settings = load_settings(config_path)
                if time.monotonic() - last_phase >= 1:
                    # get() starts at most one bounded background job on its own
                    # schedule; visitors never trigger it.
                    phase = phases.get(settings)
                    phase['published_monotonic'] = time.monotonic()
                    atomic_json(settings.runtime_dir / 'phase.json', phase)
                    last_phase = time.monotonic()
                jobs = sorted(root.glob('*.job.json'), key=lambda p: p.stat().st_mtime)
                if jobs:
                    job_path = jobs[0]
                    job = read_json(job_path, {})
                    try:
                        result = query_environmental_relationships(settings.data_dir, **job)
                        value = dict(result=result, state='ready', generated_epoch=time.time())
                    except (OSError, ValueError, sqlite3.Error) as error:
                        value = dict(result={'segments': []}, state='unavailable',
                                     generated_epoch=time.time(), message=str(error))
                    atomic_json(root / job_path.name.replace('.job.json', '.result.json'), value)
                    job_path.unlink(missing_ok=True)
                cache = sorted(root.glob('*.result.json'), key=lambda p: p.stat().st_mtime, reverse=True)
                for path in cache[MAX_CACHE:]:
                    path.unlink(missing_ok=True)
                atomic_json(settings.runtime_dir / 'views-health.json',
                            {'state': 'running', 'updated_monotonic': time.monotonic(), 'error': None})
            except (OSError, ValueError, TypeError) as error:
                atomic_json(settings.runtime_dir / 'views-health.json', {'state': 'error', 'updated_monotonic': time.monotonic(), 'error': str(error)})
            stop.wait(1)
