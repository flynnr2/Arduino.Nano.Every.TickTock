"""Acquisition service: one serial owner, bounded receive queue, durable analysis journal; estimators run separately."""
from __future__ import annotations

from dataclasses import replace
import fcntl
import json
import math
from pathlib import Path
import queue
import signal
import sqlite3
import threading
import time

from . import __version__
from .commands import Commands
from .common import atomic_json, read_json, utc_now, notify_watchdog, boot_id
from .config import HOT_FIELDS, load_settings, settings_to_dict
from .display import MEAN_MODEL
from .forecast import MODEL as FORECAST_MODEL, VERSION as FORECAST_VERSION, HALF_LIFE_SECONDS
from .gps_health import GPSHealthCollector
from .time_health import UTCHealthCollector
from .protocol import Contract, ContractChanged, ProtocolError, decode, sequence_step
from .recording import Recorder
from .transport import Reader


def environment_snapshot(settings, now: float | None = None, source='serial') -> dict:
    if source == 'demo':
        return {'temperature_C': 21.5, 'humidity_pct': 48.0, 'pressure_hPa': 1013.25,
                'simulated': True, 'sht4x': {'ok': True}, 'bmp280': {'ok': True}}
    result = read_json(settings.runtime_dir / 'sensors.json', {})
    result = dict(result) if isinstance(result, dict) else {}
    if now is not None and result.get('updated_monotonic', float('-inf')) > now:
        from .sensor_history import as_of
        try:
            result = as_of(settings, now)
        except (OSError, ValueError, sqlite3.Error):
            result = {}
    # The independent sensor writer can publish while this file is being read.
    # Observe its age after the read, unless the caller requests a fixed cutoff.
    if now is None:
        now = time.monotonic()
    for device, names in (('sht4x', ('temperature_C', 'humidity_pct')), ('bmp280', ('pressure_hPa',))):
        state = result.get(device, {})
        state = dict(state) if isinstance(state, dict) else {}
        timestamp = state.get('last_good_monotonic')
        age = now - timestamp if isinstance(timestamp, (int, float)) else None
        fresh = settings.sensors_enabled and age is not None and math.isfinite(age) and 0 <= age <= settings.sensor_stale_seconds
        state.update(fresh=bool(fresh), age_seconds=age if age is not None and math.isfinite(age) else None)
        result[device] = state
        for name in names:
            value = result.get(name)
            if not fresh or type(value) not in (int, float) or not math.isfinite(value):
                result[name] = None
    return result


class Acquisition:
    def __init__(self, settings, config_path=None, reader=None):
        self.settings = settings
        self.config_path = Path(config_path) if config_path else None
        self.reader = reader or Reader(settings)
        self.settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.recorder = Recorder(settings)
        self.contract = Contract()
        self.time_health = UTCHealthCollector()
        self.gps_health = GPSHealthCollector()
        self.commands = Commands(settings.runtime_dir, self.reader)
        self.stop = threading.Event()
        self.latest = {}
        self.previous_seq = {}
        self.previous_drops = {}
        self.generation = 0
        self.last_queue_drops = 0
        self.last_line_mono = self.last_capture_mono = None
        self.last_swing_received_monotonic = self.last_pps_received_monotonic = None
        self.start_mono = time.monotonic()
        self.boot_id = boot_id()
        self.config_error = None
        self.recorded_display_configuration = None
        self.last_periodic = self.last_diagnostic = 0.0
        self.counters = dict(lines=0, malformed=0, fragments=0, before_ready=0, duplicates=0,
                             swing_missing=0, pps_missing=0, restarts=0, accepted_CSW=0,
                             accepted_CPS=0, storage_lost_CSW=0, storage_lost_CPS=0)
        self.counters.update(not_logged_disabled_CSW=0, not_logged_disabled_CPS=0)

    def recover(self, reason, new_session=True):
        self.reader.ready.clear()
        self.contract = Contract()
        self.latest.clear()
        self.previous_seq.clear()
        self.previous_drops.clear()
        self.last_swing_received_monotonic = self.last_pps_received_monotonic = None
        self.commands.interrupted(reason)
        if new_session:
            self.recorder.start(reason)
        self.recorder.event('acquisition', 'recovery', reason, 'Awaiting fresh CFG and both SCH declarations; no missing captures are replayed.')
        self._record_display_configuration()

    def process(self, event):
        if event.generation != self.generation:
            self.recover('serial_reconnected' if self.generation else f'{self.reader.source}_connected', bool(self.generation))
            self.generation = event.generation
        if event.kind == 'disconnected':
            self.recorder.event('serial', 'disconnected', '', event.detail)
            self.recover('serial_disconnected', bool(self.contract.cfg or self.latest))
            return
        if event.kind in {'command_sent', 'command_error'}:
            self.commands.transport_event(event)
            self.recorder.event('command', event.kind, event.command_id, event.detail)
            return
        if event.kind != 'line':
            return
        self.counters['lines'] += 1
        self.last_line_mono = event.mono
        self.recorder.raw(event.raw, event.mono, event.epoch, fragment=event.fragment)
        if event.fragment:
            self.counters['fragments'] += 1
            self.recorder.event('serial', 'fragment', len(event.raw), 'Partial or oversized line retained in RAW.jsonl')
            return
        try:
            record = decode(event.raw)
            try:
                self.contract.observe(record)
            except ContractChanged:
                self.recover('contract_changed')
                self.contract.observe(record)
        except ProtocolError as error:
            self.counters['malformed'] += 1
            self.recorder.event('protocol', 'rejected', '', str(error), received_mono=event.mono, epoch=event.epoch)
            rejected_tag = event.raw.split(b',', 1)[0].strip()
            if rejected_tag in {b'CFG', b'SCH'} or rejected_tag not in {b'CSW', b'CPS', b'STS'}:
                self.recover('invalid_metadata', self.contract.ready)
            return
        if record.tag == 'STS':
            self.recorder.status_line(event.raw, event.mono)
            self.commands.observe(record, event)
            return
        if record.tag in {'CFG', 'SCH'}:
            self.recorder.event('metadata', record.tag, '', event.raw.decode('ascii').strip())
            if self.contract.ready:
                contract = self.contract.snapshot()
                if contract != self.recorder.contract:
                    self.recorder.set_contract(contract)
                    self.recorder.event('acquisition', 'ready', 1, json.dumps(self.contract.cfg))
                # A backlog may still contain valid metadata from a previous connection.
                if self.generation == self.reader.generation and self.reader.connected:
                    self.reader.ready.set()
            return
        if record.tag not in {'CSW', 'CPS'}:
            return
        if not self.contract.ready:
            self.counters['before_ready'] += 1
            return
        tag, values = record.tag, record.values
        kind, missing = sequence_step(self.previous_seq.get(tag), values['seq'])
        if kind == 'duplicate':
            self.counters['duplicates'] += 1
            self.recorder.event('sequence', tag, 'duplicate', str(values['seq']))
            return
        if kind == 'restart':
            self.counters['restarts'] += 1
            self.recorder.event('sequence', tag, 'restart_or_reorder', str(values['seq']))
            self.recover('nano_sequence_restart_or_reorder')
            self.counters['before_ready'] += 1
            return
        self.previous_seq[tag] = values['seq']
        if missing:
            self.counters['swing_missing' if tag == 'CSW' else 'pps_missing'] += missing
            self.recorder.event('sequence', tag, 'missing', str(missing))
        for key, value in values.items():
            if key.startswith('drop_'):
                previous = self.previous_drops.get((tag, key))
                if previous is not None and previous != value:
                    delta = (value - previous) & 0xffffffff
                    self.recorder.event('nano.loss', f'{tag}.{key}', value,
                                        f'increase={delta}' if delta < 0x80000000 else 'counter reset')
                self.previous_drops[tag, key] = value
        self.latest[tag] = dict(values)
        self.counters['accepted_' + tag] += 1
        self.last_capture_mono = event.mono
        if tag == 'CSW':
            self.last_swing_received_monotonic = event.mono
        else:
            self.last_pps_received_monotonic = event.mono
        environment = environment_snapshot(self.settings, now=event.mono, source=self.reader.source)
        # Never attach an environmental sample obtained after this capture arrived.
        if self.reader.source != 'demo':
            for device, names in (('sht4x', ('temperature_C', 'humidity_pct')), ('bmp280', ('pressure_hPa',))):
                sampled = environment.get(device, {}).get('last_good_monotonic')
                if sampled is not None and sampled > event.mono:
                    for name in names:
                        environment[name] = None
        if not self.recorder.capture(tag, values, environment, event.mono, event.epoch):
            self.counters[('storage_lost_' if self.settings.logging_enabled else 'not_logged_disabled_') + tag] += 1
        self.counters['receiver_queue_drops'] = self.reader.dropped_events
        context = {'version': 1, 'tag': tag, 'values': dict(values),
                   'nominal_hz': self.contract.nominal_hz, 'mono': event.mono, 'epoch': event.epoch,
                   'boot_id': self.boot_id, 'source': self.reader.source,
                   'logging': self.recorder.snapshot(), 'counters': dict(self.counters),
                   'environment': environment, 'time_health': self.time_health.snapshot(),
                   'settings': {key: getattr(self.settings, key) for key in
                                ('target_period_s', 'pps_holdover_seconds', 'forecast_cycle_length')}}
        if not self.recorder.analysis_input(context):
            self.counters['analysis_input_lost'] = self.counters.get('analysis_input_lost', 0) + 1

    def _record_display_configuration(self):
        # Manifests describe the latest settings. Preserve changes and each
        # recording segment's initial model configuration alongside captures.
        if not self.recorder.active:
            return
        identity = (self.recorder.path, self.settings.forecast_cycle_length,
                    self.settings.pps_holdover_seconds, self.settings.target_period_s)
        if identity == self.recorded_display_configuration:
            return
        detail = {'mean_model': MEAN_MODEL, 'forecast_model': FORECAST_MODEL,
                  'forecast_version': FORECAST_VERSION,
                  'forecast_cycle_length': self.settings.forecast_cycle_length,
                  'forecast_half_life_seconds': HALF_LIFE_SECONDS,
                  'pps_holdover_seconds': self.settings.pps_holdover_seconds,
                  'target_period_s': self.settings.target_period_s,
                  'capture_frontier': dict(self.previous_seq)}
        if self.recorder.event('display', 'configuration', '',
                               json.dumps(detail, allow_nan=False, separators=(',', ':'))):
            self.recorded_display_configuration = identity

    def reload_settings(self):
        if not self.config_path:
            return
        try:
            updated = load_settings(self.config_path)
            self.settings = replace(self.settings, **{key: getattr(updated, key) for key in HOT_FIELDS})
            self.recorder.settings = self.settings
            self.config_error = None
            self._record_display_configuration()
        except (OSError, ValueError, TypeError) as error:
            self.config_error = str(error)

    def snapshot(self):
        environment = environment_snapshot(self.settings, source=self.reader.source)
        now = time.monotonic()
        transport = self.reader.snapshot()
        capture_age = now - self.last_capture_mono if self.last_capture_mono is not None else None
        return {
            'version': __version__, 'updated_utc': utc_now(), 'updated_monotonic': now,
            'boot_id': self.boot_id,
            'source': self.reader.source, 'uptime_seconds': now - self.start_mono,
            'connected': self.reader.connected,
            'ready': self.contract.ready and self.generation == self.reader.generation and self.reader.connected,
            'capture_stale': capture_age is None or capture_age >= 10,
            'last_capture_age_seconds': capture_age, 'transport': transport,
            'last_swing_received_monotonic': self.last_swing_received_monotonic,
            'last_pps_received_monotonic': self.last_pps_received_monotonic,
            'logging': self.recorder.snapshot(), 'counters': dict(self.counters),
            'contract': self.contract.snapshot(), 'latest': dict(self.latest),
            'time_health': self.time_health.snapshot(now),
            'gps_health': self.gps_health.snapshot(now),
            'environment': environment,
            'oled': read_json(self.settings.runtime_dir / 'oled.json', {}),
            'config_error': self.config_error,
        }

    def periodic(self, force=False):
        now = time.monotonic()
        self.recorder.tick()
        if now - self.last_periodic < 1 and not force:
            return
        self.last_periodic = now
        self.reload_settings()
        self._record_display_configuration()
        dropped = self.reader.dropped_events
        if dropped != self.last_queue_drops:
            self.recover('receiver_queue_overflow')
            self.recorder.event('receiver.loss', 'queue_drops', dropped - self.last_queue_drops,
                                f'total_bytes={self.reader.dropped_bytes}')
            self.last_queue_drops = dropped
        self.commands.poll(self.contract.ready and self.generation == self.reader.generation)
        snapshot = self.snapshot()
        atomic_json(self.settings.runtime_dir / 'status.json', snapshot)
        notify_watchdog()
        if now - self.last_diagnostic >= 30:
            self.last_diagnostic = now
            self.recorder.event('health', 'snapshot', '', json.dumps({
                'transport': snapshot['transport'], 'counters': snapshot['counters'],
                'logging': snapshot['logging'], 'environment': snapshot['environment'],
                'time_health': snapshot['time_health'],
                'gps_health': snapshot['gps_health'],
                'config_error': self.config_error,
            }, allow_nan=False, separators=(',', ':')))

    def receiver_health(self):
        # This small publisher can keep reporting the finite receive queue while
        # the recording loop waits for storage. It never touches the UART.
        while not self.stop.is_set():
            try:
                atomic_json(self.settings.runtime_dir / 'receiver.json',
                            {'boot_id': self.boot_id, 'updated_monotonic': time.monotonic(),
                             'transport': self.reader.snapshot()})
            except OSError:
                pass
            self.stop.wait(1)

    def run(self):
        # Lock ownership prevents two processes opening one UART or receiving the same command.
        with (self.settings.runtime_dir / 'acquisition.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError('another acquisition service owns this runtime directory') from error
            self.recorder.start(f'service_start_{self.reader.source}')
            self._record_display_configuration()
            try:
                self.time_health.start()
                self.gps_health.start()
                self.reader.start()
                receiver_health = threading.Thread(target=self.receiver_health, name="receiver-health", daemon=True)
                receiver_health.start()
                while not self.stop.is_set():
                    try:
                        event = self.reader.events.get(timeout=0.1)
                    except queue.Empty:
                        if self.reader.done.is_set():
                            if self.reader.error:
                                raise RuntimeError(self.reader.error)
                            break
                    else:
                        self.process(event)
                    self.periodic()
            finally:
                self.stop.set()
                self.reader.stop.set()
                if self.reader.ident is not None:
                    self.reader.join(timeout=3)
                # Process already received complete rows during graceful shutdown.
                while not self.reader.events.empty():
                    self.process(self.reader.events.get_nowait())
                self.commands.interrupted('acquisition stopped')
                self.recorder.event('service', 'stop', '', 'Graceful shutdown; next start opens a new session')
                self.recorder.close()
                self.time_health.close()
                self.gps_health.close()
                self.reader.connected = False
                atomic_json(self.settings.runtime_dir / 'status.json', dict(self.snapshot(), stopped=True))


def run_acquisition(settings, config_path, source='serial', replay=None, pace=0, loop=False):
    reader = Reader(settings, source, replay, pace, loop)
    app = Acquisition(settings, config_path, reader)
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: app.stop.set())
    app.run()
