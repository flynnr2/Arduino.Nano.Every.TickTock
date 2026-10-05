"""Bounded bus clear for the Pi Zero 2 W's dedicated hardware I2C bus 1.

Run only through pendulum-i2c-recovery.service. The helper accepts no paths,
commands, bus numbers or pins from clients. Workers keep their ordinary user.
"""
from contextlib import contextmanager
import json
import mmap
import os
import stat
from pathlib import Path
import signal
import socket
import struct
import threading
import time
import uuid

from .common import atomic_json, notify_watchdog, read_json
from .i2c_bus import RECOVERY_DIR, BusBusy, bus_lock


# Linux is not real-time: these are bounded polling counts/minimum delays,
# not a promise of microsecond wall-clock deadlines.
def clear_bus(io, sleep=time.sleep):
    """Open-drain SCL pulses, at most nine; STOP only after SDA releases."""
    result = {'pulses': 0, 'stop': False, 'clock_blocked': False, 'released': False}

    def clock_high():
        for _ in range(20):
            if io.scl():
                return True
            sleep(.000005)
        return io.scl()

    try:
        io.release_sda()
        io.release_scl()
        if not clock_high():
            result['clock_blocked'] = True
        else:
            while not io.sda() and result['pulses'] < 9:
                io.low_scl()
                sleep(.000005)
                io.release_scl()
                if not clock_high():
                    result['clock_blocked'] = True
                    break
                result['pulses'] += 1
                sleep(.000005)
            if not result['clock_blocked'] and io.sda():
                io.low_scl()
                io.low_sda()
                sleep(.000005)
                io.release_scl()
                if clock_high():
                    sleep(.000005)
                    io.release_sda()
                    sleep(.000005)
                    result['stop'] = io.sda() and io.scl()
                else:
                    result['clock_blocked'] = True
    finally:
        try:
            io.release_sda()
        finally:
            io.release_scl()
    result['released'] = io.sda() and io.scl()
    return result


def find_bcm2835_gpiochip(gpiod, directory=Path('/dev')):
    """Count distinct controllers, not compatibility symlinks to them."""
    matches = []
    observed = []
    paths = sorted({path.resolve(strict=True) for path in directory.glob('gpiochip*')})
    for path in paths:
        with gpiod.Chip(str(path)) as chip:
            label = chip.get_info().label
        observed.append(f'{path.name}={label}')
        if label == 'pinctrl-bcm2835':
            matches.append(path)
    if len(matches) != 1:
        details = ', '.join(observed) or 'no gpiochip devices'
        raise RuntimeError('Cannot identify the BCM2835 GPIO controller uniquely: '
                           f'{len(matches)} distinct matches; found {details}')
    return matches[0]


class PiBus1:
    """Strictly the Zero 2 W BSC1 on GPIO2/3, with Linux ownership released."""
    driver = Path('/sys/bus/platform/drivers/i2c-bcm2835')
    controller = Path('/sys/bus/platform/devices/3f804000.i2c')
    i2c_devices = Path('/sys/bus/i2c/devices')

    def __init__(self):
        model = Path('/proc/device-tree/model').read_bytes().rstrip(b'\0')
        if not model.startswith(b'Raspberry Pi Zero 2 W'):
            raise RuntimeError('Recovery is supported only on Raspberry Pi Zero 2 W')
        if not self.controller.is_dir():
            raise RuntimeError('Expected BSC1 platform device is unavailable')
        import gpiod
        self.gpiod = gpiod
        self.chip = find_bcm2835_gpiochip(gpiod)
        self.clock_hz = struct.unpack('>I', (self.controller / 'of_node/clock-frequency').read_bytes())[0]
        if self.clock_hz not in (100000, 400000):
            raise RuntimeError('Use a fixed 100 kHz or 400 kHz I2C bus clock')

    def validate_adapter(self):
        # The I2C bus exposes adapters here; the legacy i2c-adapter class
        # directory is not present on all kernels.
        adapter = (self.i2c_devices / 'i2c-1').resolve(strict=True)
        if adapter.parent != self.controller.resolve(strict=True):
            raise RuntimeError('Bus 1 is not the expected BSC1 controller')
        if (self.controller / 'driver').resolve(strict=True) != self.driver.resolve(strict=True):
            raise RuntimeError('Unexpected bus-1 kernel driver')
        # A kernel client could issue transactions without our userspace lock.
        if any(self.i2c_devices.glob('1-*')):
            raise RuntimeError('Bus 1 has kernel clients; automatic recovery refused')

    def levels(self):
        self.validate_adapter()
        # Read-only GPFSEL0/GPLEV0 observation leaves Linux's pin mux intact.
        # Register layout: BCM2835 ARM Peripherals, GPIO section. Never write
        # /dev/gpiomem; all pin changes use exclusive kernel GPIO line requests.
        with open('/dev/gpiomem', 'rb', buffering=0) as stream:
            with mmap.mmap(stream.fileno(), 4096, access=mmap.ACCESS_READ) as registers:
                function = struct.unpack_from('<I', registers, 0)[0]
                if any((function >> (pin * 3)) & 7 != 4 for pin in (2, 3)):
                    raise RuntimeError('GPIO2/3 are not both assigned to I2C ALT0')
                value = struct.unpack_from('<I', registers, 0x34)[0]
        return bool(value & 4), bool(value & 8)

    def handles_open(self):
        # i2c_del_adapter waits for EVERY i2c-dev fd to close. Waiting inside
        # unbind while holding our lock would deadlock other workers' cleanup.
        # Inspect before unbinding; non-cooperating applications defer recovery.
        for fd in Path('/proc').glob('[0-9]*/fd/*'):
            try:
                info = fd.stat()
            except FileNotFoundError:
                continue  # Process/fd exited while inspecting.
            if stat.S_ISCHR(info.st_mode) and os.major(info.st_rdev) == 89 and os.minor(info.st_rdev) == 1:
                return True
        return False

    def restore(self):
        if not (self.controller / 'driver').exists():
            (self.driver / 'bind').write_text(self.controller.name)
        self.validate_adapter()

    @contextmanager
    def gpio(self):
        self.validate_adapter()
        # The daemon writes a blocked/pending status before reaching here.
        # Restore even if GPIO acquisition or a later pin operation fails.
        try:
            (self.driver / 'unbind').write_text(self.controller.name)
            from gpiod.line import Direction, Drive, Value
            settings = self.gpiod.LineSettings(direction=Direction.OUTPUT,
                                               drive=Drive.OPEN_DRAIN,
                                               output_value=Value.ACTIVE)
            with self.gpiod.request_lines(str(self.chip), consumer='pendulum-i2c-recovery',
                                         config={(2, 3): settings}) as request:
                class Lines:
                    def sda(self): return request.get_value(2) == Value.ACTIVE
                    def scl(self): return request.get_value(3) == Value.ACTIVE
                    def low_sda(self): request.set_value(2, Value.INACTIVE)
                    def low_scl(self): request.set_value(3, Value.INACTIVE)
                    def release_sda(self): request.set_value(2, Value.ACTIVE)
                    def release_scl(self): request.set_value(3, Value.ACTIVE)
                yield Lines()
        finally:
            self.restore()


class Recovery:
    """One persistent two-attempt budget per fault, shared by all workers."""
    def __init__(self, backend, directory=RECOVERY_DIR, clock=time.monotonic, sleep=time.sleep):
        self.backend, self.directory, self.clock, self.sleep = backend, directory, clock, sleep
        self.path = directory / 'status.json'
        self.state = read_json(self.path, {}) or {}
        if not self.state:
            self.state = {'state': 'idle', 'attempts': 0, 'blocked': False,
                          'generation': uuid.uuid4().hex, 'next_check': 0,
                          'high_since': None, 'high_samples': 0}

    def publish(self):
        self.state['updated_monotonic'] = self.clock()
        atomic_json(self.path, self.state)
        # Root creates status; workers can read but cannot replace or change it.
        self.path.chmod(0o640)

    def startup(self):
        if self.state.get('pending_restore'):
            self.backend.restore()
            self.state.update(pending_restore=False, state='waiting', blocked=True)
        self.state['clock_hz'] = getattr(self.backend, 'clock_hz', None)
        self.publish()

    def step(self, requested=False):
        now = self.clock()
        if now < self.state.get('next_check', 0):
            return
        if not requested and self.state['state'] == 'idle':
            return
        sda, scl = self.backend.levels()
        if sda and scl:
            if not self.state['attempts'] and not self.state['blocked']:
                self.state.update(state='idle', error=None)
            else:
                since = self.state.get('high_since')
                if since is None:
                    since = now
                    self.state.update(high_since=since, high_samples=0)
                count = self.state.get('high_samples', 0) + 1
                self.state.update(high_samples=count, next_check=now + 1)
                if count >= 3 and now - since >= 2:
                    self.state.update(state='idle', attempts=0, blocked=False,
                                      high_since=None, high_samples=0, error=None)
            self.publish()
            return
        self.state.update(blocked=True, high_since=None, high_samples=0)
        self.publish()
        # Both observations are made with ALL application transfers locked out.
        self.sleep(.001)
        if all(self.backend.levels()):
            self.state.update(state='verifying', next_check=now + 1)
            self.publish()
            return
        if self.state['attempts'] >= 2:
            self.state.update(state='exhausted', next_check=now + 30)
            self.publish()
            return
        if self.backend.handles_open():
            # Return and release the lock so workers can see the blocked state
            # and close their descriptors, even during a 60-second retry delay.
            self.state.update(state='quiescing', next_check=now + .5)
            self.publish()
            return
        self.state.update(attempts=self.state['attempts'] + 1, state='recovering',
                          pending_restore=True, generation=uuid.uuid4().hex)
        self.publish()  # Survives helper termination during unbind/GPIO/rebind.
        with self.backend.gpio() as io:
            result = clear_bus(io, self.sleep)
        self.state.update(result=result, pending_restore=False, next_check=self.clock() + 1,
                          blocked=not result['released'],
                          state='verifying' if result['released'] else 'waiting')
        if not result['released'] and self.state['attempts'] >= 2:
            self.state.update(state='exhausted', next_check=self.clock() + 30)
        self.publish()
        print(json.dumps({'i2c_recovery': self.state}), flush=True)


def run():
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    recovery = Recovery(None)
    # Retry initialization without letting service restarts reset the budget.
    backend = None
    socket_path = RECOVERY_DIR / 'request.sock'
    socket_path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(str(socket_path))
        socket_path.chmod(0o660)
        server.settimeout(.5)
        requested = False
        next_init = 0
        while not stop.is_set():
            try:
                requested |= server.recv(16) == b'fault'
            except socket.timeout:
                pass
            if stop.is_set():
                break
            try:
                with bus_lock(RECOVERY_DIR):
                    if backend is None and time.monotonic() >= next_init:
                        next_init = time.monotonic() + 30
                        backend = PiBus1()
                        recovery.backend = backend
                        recovery.startup()
                    if backend is not None:
                        recovery.step(requested)
                        requested = False
            except BusBusy:
                pass
            except Exception as exc:
                # A failed restore stays blocked. Initialization retries restoration
                # every 30s without resetting the budget or sending pulses.
                recovery.state.update(error=f'{type(exc).__name__}: {exc}'[:300])
                if recovery.state.get('pending_restore'):
                    recovery.state.update(state='restore_failed', blocked=True)
                else:
                    recovery.state['state'] = 'unavailable'
                recovery.publish()
                print(json.dumps({'i2c_recovery_error': recovery.state['error']}), flush=True)
                backend = None
                next_init = time.monotonic() + 30
            notify_watchdog()
    socket_path.unlink(missing_ok=True)


if __name__ == '__main__':
    run()
