"""Electrical sequencing, fault budgets and cross-process exclusion without GPIO."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pendulum_pi import i2c_bus
from pendulum_pi.common import atomic_json
from pendulum_pi.config import Settings
from pendulum_pi.i2c_bus import BusBusy, BusCoordinator, BusSuspended, bus_lock
from pendulum_pi.i2c_recovery import Recovery, PiBus1, clear_bus, find_bcm2835_gpiochip
from pendulum_pi.peripherals import SensorChannel


class Pins:
    def __init__(self, release_after=None, scl_stuck=False):
        self.release_after, self.scl_stuck = release_after, scl_stuck
        self.clocks = 0
        self.clock_low = self.data_low = False
        self.events = []

    def sda(self):
        return not self.data_low and self.release_after is not None and self.clocks >= self.release_after

    def scl(self):
        return not self.clock_low and not self.scl_stuck

    def low_scl(self):
        self.events.append('SCL low')
        self.clock_low = True

    def release_scl(self):
        self.events.append('SCL release')
        if self.clock_low:
            self.clocks += 1
        self.clock_low = False

    def low_sda(self):
        assert self.clock_low  # STOP preparation must not create START.
        self.events.append('SDA low')
        self.data_low = True

    def release_sda(self):
        self.events.append('SDA release')
        self.data_low = False


@pytest.mark.parametrize('release_after', [1, 5, 9])
def test_bus_clear_stops_clocking_on_release_then_sends_stop(release_after):
    pins = Pins(release_after)
    outcome = clear_bus(pins, lambda _: None)
    assert outcome == dict(pulses=release_after, stop=True, clock_blocked=False, released=True)
    assert pins.clocks == release_after + 1  # Last edge prepares STOP, not a tenth data clock.
    assert pins.events[-6:] == ['SCL low', 'SDA low', 'SCL release', 'SDA release', 'SDA release', 'SCL release']


def test_stuck_sda_gets_only_nine_clocks_and_no_stop():
    pins = Pins()
    assert clear_bus(pins, lambda _: None) == dict(pulses=9, stop=False, clock_blocked=False, released=False)
    assert pins.clocks == 9
    assert 'SDA low' not in pins.events


def test_stuck_scl_gets_no_clocks_and_bounded_wait():
    pins, waits = Pins(scl_stuck=True), []
    assert clear_bus(pins, waits.append)['clock_blocked']
    assert pins.clocks == 0 and len(waits) == 20


def test_gpio_failure_releases_both_lines():
    pins = Pins()
    def fail():
        raise OSError('GPIO error')
    pins.sda = fail
    with pytest.raises(OSError):
        clear_bus(pins, lambda _: None)
    assert pins.events[-2:] == ['SDA release', 'SCL release']


class Backend:
    def __init__(self, pins):
        self.pins, self.attempts, self.restores = pins, 0, 0
        self.fail_restore = False

    def levels(self):
        return self.pins.sda(), self.pins.scl()

    def handles_open(self):
        return False

    def restore(self):
        self.restores += 1
        if self.fail_restore:
            raise OSError('cannot rebind')

    @contextmanager
    def gpio(self):
        self.attempts += 1
        try:
            yield self.pins
        finally:
            self.restore()


def recovery(tmp_path, pins):
    clock = [0.0]
    backend = Backend(pins)
    state = Recovery(backend, tmp_path, lambda: clock[0], lambda _: None)
    return state, backend, clock


def test_absent_device_with_high_lines_never_consumes_budget(tmp_path):
    state, backend, clock = recovery(tmp_path, Pins(0))
    for clock[0] in range(10):
        state.step(requested=True)
    assert backend.attempts == 0
    assert state.state['attempts'] == 0
    assert not state.state['blocked']


def test_low_must_persist_across_two_idle_observations(tmp_path):
    state, backend, _ = recovery(tmp_path, Pins())
    samples = iter([(False, True), (True, True)])
    backend.levels = lambda: next(samples)
    state.step(True)
    assert backend.attempts == 0
    assert state.state['state'] == 'verifying'


def test_two_attempts_are_spaced_and_persist_across_restart(tmp_path):
    state, backend, clock = recovery(tmp_path, Pins())
    state.step(True)
    assert backend.attempts == 1 and state.state['blocked']
    clock[0] = .5
    state.step(True)
    assert backend.attempts == 1
    state = Recovery(backend, tmp_path, lambda: clock[0], lambda _: None)
    clock[0] = 1
    state.step(True)
    assert backend.attempts == 2 and state.state['state'] == 'exhausted'
    for clock[0] in (2, 30, 31, 100):
        state.step(True)
    assert backend.attempts == 2
    assert state.state['attempts'] == 2


def test_exhaustion_rearms_only_after_three_spaced_high_samples(tmp_path):
    state, backend, clock = recovery(tmp_path, Pins())
    state.step(True)
    clock[0] = 1
    state.step(True)
    backend.pins.release_after = 0
    clock[0] = 31
    state.step()
    assert state.state['blocked'] and state.state['attempts'] == 2
    clock[0] = 32
    state.step()
    assert state.state['blocked']
    clock[0] = 33
    state.step()
    assert not state.state['blocked'] and state.state['attempts'] == 0


def test_success_retains_budget_until_stable_and_changes_generation(tmp_path):
    state, backend, clock = recovery(tmp_path, Pins(3))
    old = state.state['generation']
    state.step(True)
    assert state.state['generation'] != old
    assert not state.state['blocked'] and state.state['attempts'] == 1
    for clock[0] in (1, 2):
        state.step()
        assert state.state['attempts'] == 1
    clock[0] = 3
    state.step()
    assert state.state['attempts'] == 0


def test_restore_failure_keeps_pending_marker_and_restart_restores(tmp_path):
    state, backend, clock = recovery(tmp_path, Pins(2))
    backend.fail_restore = True
    with pytest.raises(OSError):
        state.step(True)
    saved = json.loads(state.path.read_text())
    assert saved['pending_restore'] and saved['blocked'] and saved['attempts'] == 1
    backend.fail_restore = False
    restarted = Recovery(backend, tmp_path, lambda: clock[0], lambda _: None)
    restarted.startup()
    assert not restarted.state['pending_restore']
    assert restarted.state['blocked'] and restarted.state['attempts'] == 1
    assert backend.restores == 2


def test_cross_process_bus_exclusion_and_release_after_process_exit(tmp_path):
    (tmp_path / 'bus-1.lock').touch()
    program = '''import fcntl, sys
f = open(sys.argv[1], 'r+')
fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
print('locked', flush=True)
sys.stdin.read(1)
'''
    child = subprocess.Popen([sys.executable, '-c', program, str(tmp_path / 'bus-1.lock')],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(BusBusy):
            with bus_lock(tmp_path):
                pytest.fail('overlapping transaction')
    finally:
        child.communicate('q', timeout=5)
    with bus_lock(tmp_path):
        pass


@pytest.fixture
def coordinated(tmp_path, monkeypatch):
    (tmp_path / 'bus-1.lock').touch()
    monkeypatch.setattr(i2c_bus, 'RECOVERY_DIR', tmp_path)
    atomic_json(tmp_path / 'status.json', {'state': 'idle', 'blocked': False, 'generation': 'old'})
    return tmp_path


def test_blocked_bus_never_initializes_sensor_or_refreshes_values(coordinated):
    atomic_json(coordinated / 'status.json', {'state': 'exhausted', 'blocked': True})
    channel = SensorChannel('bmp280')
    channel.poll(Settings(), 0, factory=lambda *_: pytest.fail('must not access bus'), clock=lambda: 0)
    assert not channel.ok and not channel.values
    assert 'exhausted' in channel.error
    assert channel.health()['i2c_recovery']['blocked']


def test_generation_forces_other_sensor_to_reopen(coordinated):
    buses = []
    def factory(*_):
        bus = SimpleNamespace(deinit=lambda: setattr(bus, 'closed', True), closed=False)
        buses.append(bus)
        return bus, SimpleNamespace(pressure=1000)
    channel = SensorChannel('bmp280')
    channel.poll(Settings(), 0, factory=factory, clock=lambda: 0)
    atomic_json(coordinated / 'status.json', {'state': 'verifying', 'blocked': False, 'generation': 'new'})
    channel.poll(Settings(), 1, factory=factory, clock=lambda: 1)
    assert len(buses) == 2 and buses[0].closed and channel.ok


def test_missing_device_node_error_is_not_swallowed_by_lock_fallback(coordinated):
    with pytest.raises(FileNotFoundError, match='device disappeared'):
        with BusCoordinator(1).transaction():
            raise FileNotFoundError('device disappeared')


def test_non_bus1_never_uses_recovery(coordinated):
    atomic_json(coordinated / 'status.json', {'blocked': True})
    with BusCoordinator(3).transaction() as generation:
        assert generation is None


def test_backend_refuses_non_pi_before_loading_gpio(monkeypatch):
    monkeypatch.setattr(Path, 'read_bytes', lambda _: b'Other board\0')
    with pytest.raises(RuntimeError, match='Zero 2 W'):
        PiBus1()


def gpio_discovery(tmp_path, labels):
    opened = []
    for name in labels:
        (tmp_path / name).touch()

    @contextmanager
    def chip(path):
        opened.append(Path(path).name)
        yield SimpleNamespace(get_info=lambda: SimpleNamespace(label=labels[Path(path).name]))

    return SimpleNamespace(Chip=chip), opened


@pytest.mark.parametrize('alias', [False, True])
def test_gpio_discovery_counts_a_controller_once_with_or_without_alias(tmp_path, alias):
    gpiod, opened = gpio_discovery(tmp_path, {'gpiochip0': 'pinctrl-bcm2835',
                                             'gpiochip1': 'raspberrypi-exp-gpio'})
    if alias:
        (tmp_path / 'gpiochip4').symlink_to('gpiochip0')
    assert find_bcm2835_gpiochip(gpiod, tmp_path) == (tmp_path / 'gpiochip0').resolve()
    assert opened == ['gpiochip0', 'gpiochip1']


@pytest.mark.parametrize('labels, details', [
    ({}, 'no gpiochip devices'),
    ({'gpiochip0': 'pinctrl-bcm2711'}, 'gpiochip0=pinctrl-bcm2711'),
])
def test_gpio_discovery_reports_missing_controller(tmp_path, labels, details):
    gpiod, _ = gpio_discovery(tmp_path, labels)
    with pytest.raises(RuntimeError, match='0 distinct matches') as exc:
        find_bcm2835_gpiochip(gpiod, tmp_path)
    assert details in str(exc.value)


def test_gpio_discovery_still_refuses_two_distinct_matching_controllers(tmp_path):
    gpiod, _ = gpio_discovery(tmp_path, {'gpiochip0': 'pinctrl-bcm2835',
                                       'gpiochip1': 'pinctrl-bcm2835'})
    (tmp_path / 'gpiochip4').symlink_to('gpiochip0')
    with pytest.raises(RuntimeError, match='2 distinct matches') as exc:
        find_bcm2835_gpiochip(gpiod, tmp_path)
    assert 'gpiochip0=pinctrl-bcm2835' in str(exc.value)
    assert 'gpiochip1=pinctrl-bcm2835' in str(exc.value)


@pytest.fixture
def sysfs_backend(tmp_path):
    backend = PiBus1.__new__(PiBus1)
    backend.controller = tmp_path / 'devices/platform/soc/3f804000.i2c'
    backend.driver = tmp_path / 'bus/platform/drivers/i2c-bcm2835'
    backend.i2c_devices = tmp_path / 'bus/i2c/devices'
    for directory in (backend.controller / 'i2c-1', backend.driver, backend.i2c_devices):
        directory.mkdir(parents=True)
    (backend.controller / 'driver').symlink_to(backend.driver, target_is_directory=True)
    (backend.i2c_devices / 'i2c-1').symlink_to(backend.controller / 'i2c-1', target_is_directory=True)
    return backend


def test_adapter_validation_works_without_legacy_class_directory(sysfs_backend):
    sysfs_backend.validate_adapter()


def test_adapter_validation_refuses_another_controller(sysfs_backend, tmp_path):
    adapter = sysfs_backend.i2c_devices / 'i2c-1'
    adapter.unlink()
    other = tmp_path / 'devices/platform/other.i2c/i2c-1'
    other.mkdir(parents=True)
    adapter.symlink_to(other, target_is_directory=True)
    with pytest.raises(RuntimeError, match='not the expected BSC1'):
        sysfs_backend.validate_adapter()


def test_adapter_validation_refuses_another_driver(sysfs_backend, tmp_path):
    driver = sysfs_backend.controller / 'driver'
    driver.unlink()
    other = tmp_path / 'bus/platform/drivers/other'
    other.mkdir()
    driver.symlink_to(other, target_is_directory=True)
    with pytest.raises(RuntimeError, match='Unexpected bus-1 kernel driver'):
        sysfs_backend.validate_adapter()


def test_adapter_validation_refuses_kernel_clients(sysfs_backend):
    (sysfs_backend.i2c_devices / '1-0077').mkdir()
    with pytest.raises(RuntimeError, match='kernel clients'):
        sysfs_backend.validate_adapter()


def test_adapter_validation_refuses_missing_adapter(sysfs_backend):
    (sysfs_backend.i2c_devices / 'i2c-1').unlink()
    with pytest.raises(FileNotFoundError):
        sysfs_backend.validate_adapter()


def test_unavailable_recovery_returns_to_idle_after_successful_inspection(tmp_path):
    state, backend, _ = recovery(tmp_path, Pins(0))
    state.state.update(state='unavailable', error='old adapter lookup failure')
    state.publish()
    restarted = Recovery(backend, tmp_path, lambda: 0, lambda _: None)
    restarted.startup()
    restarted.step()
    assert restarted.state['state'] == 'idle'
    assert restarted.state['error'] is None
    assert not restarted.state['blocked']
    assert restarted.state['attempts'] == backend.attempts == 0


def test_open_descriptors_quiesce_before_unbind_without_consuming_attempt(tmp_path):
    state, backend, clock = recovery(tmp_path, Pins(3))
    backend.handles_open = lambda: True
    state.step(True)
    assert state.state['state'] == 'quiescing' and state.state['blocked']
    assert state.state['attempts'] == backend.attempts == 0
    backend.handles_open = lambda: False
    clock[0] = .5
    state.step()
    assert backend.attempts == 1 and not state.state['blocked']


def test_quiescing_closes_sensor_handles_even_during_long_backoff(coordinated):
    closed = []
    channel = SensorChannel('bmp280', bus=SimpleNamespace(deinit=lambda: closed.append(True)),
                            sensor=object(), next_attempt=60, values={'pressure_hPa': 1000},
                            last_good_monotonic=0)
    atomic_json(coordinated / 'status.json', {'state': 'quiescing', 'blocked': True})
    channel.poll(Settings(), 1, factory=lambda *_: pytest.fail('must not initialize'), clock=lambda: 1)
    assert closed == [True] and channel.sensor is None
    assert channel.last_good_monotonic == 0 and channel.values == {'pressure_hPa': 1000}


@pytest.mark.parametrize('failure', [None, 'request', 'transfer'])
def test_real_gpio_adapter_unbinds_before_open_drain_and_restores_on_errors(tmp_path, monkeypatch, failure):
    from enum import Enum
    from pendulum_pi import i2c_recovery
    events = []
    class Value(Enum):
        INACTIVE = 0
        ACTIVE = 1
    line = SimpleNamespace(Direction=SimpleNamespace(OUTPUT='output'),
                           Drive=SimpleNamespace(OPEN_DRAIN='open_drain'), Value=Value)
    monkeypatch.setitem(sys.modules, 'gpiod.line', line)
    class Request:
        def __enter__(self):
            events.append('request entered')
            return self
        def __exit__(self, *args): events.append('released')
        def get_value(self, pin): return Value.ACTIVE
        def set_value(self, pin, value): events.append((pin, value))
    def request(chip, *, consumer, config):
        assert events == ['validated', 'unbind']
        assert config[(2, 3)] == dict(direction='output', drive='open_drain', output_value=Value.ACTIVE)
        if failure == 'request':
            raise OSError('request failed')
        return Request()
    backend = PiBus1.__new__(PiBus1)
    backend.controller, backend.driver, backend.chip = tmp_path / 'controller', tmp_path / 'driver', tmp_path / 'chip'
    backend.gpiod = SimpleNamespace(LineSettings=lambda **kwargs: kwargs, request_lines=request)
    backend.validate_adapter = lambda: events.append('validated')
    backend.restore = lambda: events.append('restored')
    monkeypatch.setattr(Path, 'write_text', lambda self, value: events.append(self.name))
    def operation():
        with backend.gpio() as io:
            assert io.sda() and io.scl()
            io.low_scl()
            io.release_scl()
            if failure == 'transfer':
                raise OSError('transfer failed')
    if failure:
        with pytest.raises(OSError): operation()
    else:
        operation()
    assert events[-1] == 'restored'
    if failure != 'request':
        assert events[-2] == 'released'
        assert (3, Value.INACTIVE) in events and (3, Value.ACTIVE) in events
