"""Combine independent capture health with the latest saved analysis result."""
from __future__ import annotations

from copy import deepcopy
import math
import json
import time


def read_mapping(path, default=None):
    try:
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return default
        value = json.loads(raw)
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def elapsed(now, at):
    if type(at) not in (int, float):
        return None
    try:
        value = now - at
        return value if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def saved_status(settings, *, now=None):
    now = time.monotonic() if now is None else now
    capture = read_mapping(settings.runtime_dir / 'status.json', {}) or {}
    capture = dict(capture)
    receiver = read_mapping(settings.runtime_dir / 'receiver.json', {}) or {}
    receiver_age = elapsed(now, receiver.get('updated_monotonic'))
    if receiver.get('boot_id') == capture.get('boot_id') and receiver_age is not None and receiver_age <= 5:
        capture['receiver'] = receiver
    saved = read_mapping(settings.runtime_dir / 'analysis.json', {}) or {}
    point = saved.get('result') or {}
    point = point if isinstance(point, dict) else {}
    matching = (point.get('boot_id') == capture.get('boot_id') and
                point.get('logging', {}).get('session') == capture.get('logging', {}).get('session'))
    age = elapsed(now, point.get('updated_monotonic')) if point else None
    valid_age = age is not None and math.isfinite(age) and age >= 0
    health = saved.get('health')
    analysis = dict(health) if isinstance(health, dict) else {}
    heartbeat_age = elapsed(now, saved.get('published_monotonic'))
    analysis.update(result_age_seconds=age if valid_age else None,
                    service_stale=heartbeat_age is None or heartbeat_age > 10,
                    matches_capture_session=bool(matching and point),
                    data_through_utc=point.get('updated_utc'))
    display = deepcopy(point.get('display') or {}) if matching else {}
    if not matching or not valid_age or age >= 10 or analysis['service_stale']:
        display.update(stale=True)
        if isinstance(display.get('forecast'), dict):
            display['forecast'].update(stale=True, next=None)
    views = read_mapping(settings.runtime_dir / 'views-health.json', {}) or {}
    views_age = elapsed(now, views.get('updated_monotonic'))
    capture['views'] = dict(views, service_stale=views_age is None or views_age > 10)
    capture['thingspeak'] = read_mapping(settings.runtime_dir / 'thingspeak' / 'status.json', {}) or {}
    capture.update(display=display, analysis=analysis,
                   history=point.get('history', {}), mean_checkpoint=point.get('mean_checkpoint', {}),
                   measurement_environment=point.get('environment', {}),
                   measurement_observed_utc=point.get('updated_utc'),
                   measurement_observed_monotonic=point.get('updated_monotonic'),
                   measurement_time_health=point.get('time_health', {}))
    return capture
