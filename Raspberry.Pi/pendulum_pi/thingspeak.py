"""Optional, lossy hourly publication, isolated from capture and recording."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from . import common

MAX_RESPONSE = 16 * 1024
MAX_SNAPSHOT = 1024 * 1024


class PublisherError(ValueError):
    """Messages are fixed local text, safe for the journal."""
    def __init__(self, message, *, rejected=False):
        super().__init__(message)
        self.rejected = rejected


@dataclass(frozen=True)
class PublisherConfig:
    enabled: bool = False
    channel_id: int | None = None
    write_key_file: Path | None = None
    include_humidity: bool = True


def _read_json(path, limit):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('oversized JSON')
    return json.loads(raw)


def load_config(path):
    try:
        value = _read_json(path, 16 * 1024)
    except FileNotFoundError:
        return PublisherConfig()
    except (OSError, ValueError):
        raise PublisherError('invalid publisher configuration') from None
    if not isinstance(value, dict) or set(value) - {'enabled', 'channel_id', 'write_key_file', 'include_humidity'}:
        raise PublisherError('invalid publisher configuration')
    enabled, humidity = value.get('enabled', False), value.get('include_humidity', True)
    channel, key_file = value.get('channel_id'), value.get('write_key_file')
    if type(enabled) is not bool or type(humidity) is not bool:
        raise PublisherError('invalid publisher configuration')
    if channel is not None and (type(channel) is not int or not 0 < channel < 2**63):
        raise PublisherError('invalid channel ID')
    if key_file is not None and (not isinstance(key_file, str) or not key_file or
                                 len(key_file) > 4096 or '\0' in key_file or not Path(key_file).is_absolute()):
        raise PublisherError('invalid write-key file setting')
    return PublisherConfig(enabled, channel, Path(key_file) if key_file else None, humidity)


def read_write_key(config):
    if config.channel_id is None or config.write_key_file is None:
        raise PublisherError('channel and write-key file must be configured')
    try:
        fd = os.open(config.write_key_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            mode = os.fstat(stream.fileno()).st_mode
            if not stat.S_ISREG(mode) or mode & 0o027:
                raise PublisherError('write-key file permissions must restrict access')
            raw = stream.read(257)
        key = raw.decode('ascii').strip()
    except (OSError, UnicodeError):
        raise PublisherError('write-key file is unavailable') from None
    if len(raw) > 256 or not re.fullmatch(r'[A-Za-z0-9]{16,128}', key):
        raise PublisherError('write-key file has invalid contents')
    return key


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _age(now, timestamp):
    if not _number(now) or not _number(timestamp):
        return None
    age = now - timestamp
    return age if math.isfinite(age) and age >= 0 else None


def build_payload(snapshot, settings, *, now=None, include_humidity=True):
    """Return public fields or a fixed skip reason; never include private diagnostics."""
    now = time.monotonic() if now is None else now
    snapshot = _mapping(snapshot)
    age = _age(now, snapshot.get('updated_monotonic'))
    if age is None or age > 5 or snapshot.get('stopped'):
        return None, 'snapshot unavailable or stale'
    boot = common.boot_id()
    if not boot or snapshot.get('boot_id') != boot:
        return None, 'snapshot boot identity unavailable or different'
    env = _mapping(snapshot.get('environment'))
    if snapshot.get('source') != 'serial' or env.get('simulated'):
        return None, 'real serial acquisition required'
    capture_age = snapshot.get('last_capture_age_seconds')
    if (snapshot.get('connected') is not True or snapshot.get('ready') is not True or
            snapshot.get('capture_stale') is not False or snapshot.get('config_error') or
            not _number(capture_age) or capture_age < 0 or capture_age + age >= 10):
        return None, 'acquisition unavailable or stale'
    observed_mono = snapshot.get('measurement_observed_monotonic')
    result_age = _age(now, observed_mono) if observed_mono is not None else age
    if result_age is None or result_age >= 10:
        return None, 'saved result unavailable or stale'
    sample_now = observed_mono if observed_mono is not None else now
    display = _mapping(snapshot.get('display'))
    window = _mapping(display.get('window'))
    swing_age = _age(sample_now, display.get('last_swing_monotonic'))
    period_us = window.get('period_us')
    if (display.get('available') is not True or display.get('stale') is not False or
            window.get('learning') is not False or swing_age is None or swing_age >= 10 or
            not _number(period_us) or period_us <= 0 or
            not _number(window.get('window_seconds')) or window['window_seconds'] != 600):
        return None, '600-second mean unavailable, stale or learning'
    pps = _mapping(display.get('pps'))
    pps_age = _age(sample_now, pps.get('last_accepted_monotonic'))
    if (display.get('timebase') != 'PPS' or pps.get('status') != 'LOCKED' or
            pps.get('initialized') is not True or pps.get('stale') is not False or
            pps_age is None or pps_age >= 5):
        return None, 'PPS correction unavailable or stale'
    target = display.get('target_period_s')
    period_s = period_us / 1_000_000
    if not _number(target) or not 0.1 <= target <= 120:
        return None, 'target period or derived rate invalid'
    # All views consume the rated saved analysis result; publication must not
    # maintain another estimator or independently round/recalculate its rate.
    rate, bpm = window.get('gain_seconds_per_day'), window.get('bpm')
    if not _number(rate) or not _number(bpm) or bpm <= 0:
        return None, 'target period or derived rate invalid'
    payload = {'field1': f'{rate:.6f}', 'field2': f'{period_s:.6f}'}
    if any(len(value) > 255 for value in payload.values()) or payload['field2'] == '0.000000':
        return None, 'period or rate outside public field range'
    if settings.sensors_enabled:
        fields = [('field3', 'temperature_C', 'sht4x'), ('field4', 'pressure_hPa', 'bmp280')]
        if include_humidity:
            fields.append(('field5', 'humidity_pct', 'sht4x'))
        for field, name, device in fields:
            sensor = _mapping(env.get(device))
            sensor_age = _age(sample_now, sensor.get('last_good_monotonic'))
            value = env.get(name)
            if sensor_age is not None and sensor_age <= settings.sensor_stale_seconds and _number(value):
                formatted = f'{value:.2f}'
                if len(formatted) <= 255:
                    payload[field] = formatted
    health = _mapping(snapshot.get('time_health'))
    health_age = health.get('sample_age_seconds')
    utc_good = (health.get('fresh') is True and health.get('status') == 'synchronized' and
                _number(health_age) and 0 <= health_age and health_age + result_age <= 30)
    observed = 'unknown'
    if utc_good:
        try:
            timestamp = snapshot.get('measurement_observed_utc') or snapshot.get('updated_utc')
            if not isinstance(timestamp, str) or len(timestamp) > 64:
                raise ValueError()
            parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError()
            observed = parsed.astimezone(timezone.utc).isoformat(timespec='seconds')
        except (ValueError, OverflowError):
            utc_good = False
    payload['status'] = (f'schema=2;estimator=mean;window_seconds=600;target_s={target:.15g};'
                         f'pps=LOCKED;learning=false;observation_utc={observed};'
                         f'pi_utc={"synchronized" if utc_good else "unknown"};snapshot_age_s={result_age:.3f}')
    return payload, 'eligible'


def post_payload(payload, key, channel_id):
    """One verified HTTPS POST; HTTPSConnection never follows redirects or retries."""
    body = json.dumps(dict(payload, api_key=key), allow_nan=False).encode('utf-8')
    connection = http.client.HTTPSConnection('api.thingspeak.com', timeout=5)
    try:
        connection.request('POST', '/update.json', body=body, headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise PublisherError('server rejected request' if response.status in (401, 403) else
                                 'HTTP request failed', rejected=response.status in (401, 403))
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise PublisherError('server response too large')
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise PublisherError('invalid server response') from None
        if result == 0:
            raise PublisherError('server did not accept write')
        if not isinstance(result, dict) or type(result.get('entry_id')) is not int or result['entry_id'] <= 0:
            raise PublisherError('invalid server response')
        if type(result.get('channel_id')) is not int or result['channel_id'] != channel_id:
            raise PublisherError('unexpected destination channel', rejected=True)
        return result['entry_id']
    except PublisherError:
        raise
    except (OSError, http.client.HTTPException, ValueError):
        raise PublisherError('network request failed; delivery may be unknown') from None
    finally:
        connection.close()


def _fingerprint(config, key):
    return hashlib.sha256(f'pendulum-thingspeak-v1:{config.channel_id}:{key}'.encode('ascii')).hexdigest()


def _receipt_path(path):
    path = Path(path)
    return path.parent / path.stem / 'validation.json'


def _validated(path, config, key):
    try:
        receipt = _read_json(_receipt_path(path), 4096)
        return (isinstance(receipt, dict) and receipt.get('schema') == 1 and
                receipt.get('fingerprint') == _fingerprint(config, key))
    except (OSError, ValueError):
        return False


def publication_status(settings, publisher_path, value):
    """Keep the last confirmed upload across retries and service/Pi restarts."""
    path = _receipt_path(publisher_path).with_name('publication.json')
    try:
        previous = _read_json(path, 4096)
    except (OSError, ValueError):
        previous = {}
    previous = previous if isinstance(previous, dict) else {}
    result = dict(value, updated_monotonic=time.monotonic(),
                  updated_utc=common.utc_now(), last_success=previous.get('last_success'))
    if value['state'] == 'published':
        result['last_success'] = {k: result[k] for k in
                                  ('entry_id', 'data_through_utc', 'updated_utc')}
    common.atomic_json(path, result, durable=True)
    common.atomic_json(settings.runtime_dir / 'thingspeak' / 'status.json', result)


def run_thingspeak(settings, publisher_path, *, dry_run=False, validate_key=False, check_ready=False):
    """CLI boundary. Expected skips succeed; ExecCondition uses 1 for not ready."""
    try:
        config = load_config(publisher_path)
        if not dry_run and not validate_key and not config.enabled:
            print('skipped: publisher disabled')
            return 1 if check_ready else 0
        key = None if dry_run else read_write_key(config)
        if not dry_run and not validate_key and not _validated(publisher_path, config, key):
            print('skipped: write key has not been validated for this channel')
            return 1 if check_ready else 0
        if check_ready:
            print('ready: configured write key validated')
            return 0
        lock_dir = settings.runtime_dir / 'thingspeak'
        lock_dir.mkdir(parents=True, exist_ok=True)
        with (lock_dir / 'publish.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('skipped: another publisher invocation is active')
                return 1 if validate_key else 0
            if validate_key:
                # Failed recommissioning must not retain old authorization.
                _receipt_path(publisher_path).unlink(missing_ok=True)
                # A commissioning entry contains status only, never invented measurements.
                entry = post_payload({'status': 'schema=1;publisher=credential-validation'}, key, config.channel_id)
                common.atomic_json(_receipt_path(publisher_path),
                                   {'schema': 1, 'fingerprint': _fingerprint(config, key), 'entry_id': entry},
                                   durable=True)
                print('validated: write key accepted for configured channel')
                return 0
            try:
                from .results import saved_status
                snapshot = saved_status(settings)
                snapshot['environment'] = snapshot.get('measurement_environment', {})
                snapshot['time_health'] = snapshot.get('measurement_time_health', {})
            except (OSError, ValueError):
                snapshot = None
            payload, reason = build_payload(snapshot, settings, include_humidity=config.include_humidity)
            if payload is None:
                if not dry_run:
                    publication_status(settings, publisher_path, {'state': 'skipped', 'reason': reason})
                print(f'skipped: {reason}')
                return 0
            if dry_run:
                print(json.dumps({'outcome': 'eligible', 'payload': payload}, allow_nan=False, sort_keys=True))
                return 0
            entry = post_payload(payload, key, config.channel_id)
            publication_status(settings, publisher_path, {'state': 'published',
                               'entry_id': entry, 'data_through_utc': snapshot.get('measurement_observed_utc')})
            print('published: saved clock result accepted')
            return 0
    except PublisherError as error:
        try:
            if not dry_run:
                publication_status(settings, publisher_path, {'state': 'failed', 'reason': str(error)})
        except OSError:
            pass
        if error.rejected:
            try:
                _receipt_path(publisher_path).unlink(missing_ok=True)
            except OSError:
                print('failed: unable to invalidate credential validation receipt')
        print(f'failed: {error}')
        return 1
    except OSError:
        print('failed: local publisher files unavailable')
        return 1
