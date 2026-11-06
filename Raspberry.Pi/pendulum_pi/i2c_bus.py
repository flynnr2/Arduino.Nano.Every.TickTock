"""Cooperate with the privileged bus-1 recovery service; no GPIO access here."""
from contextlib import contextmanager, ExitStack
import fcntl
import os
from pathlib import Path
import socket

from .common import read_json

RECOVERY_DIR = Path('/run/pendulum-i2c')


class BusBusy(Exception):
    """Another peripheral or recovery owns the bus; retry next worker tick."""


class BusSuspended(OSError):
    """Recovery has suspended transfers on the shared bus."""


@contextmanager
def bus_lock(directory):
    # Deployment creates this file in a root-owned directory. Never replace it:
    # flock must refer to the same inode in every worker and service restart.
    fd = os.open(directory / 'bus-1.lock', os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusBusy() from exc
        yield
    finally:
        os.close(fd)


class BusCoordinator:
    def __init__(self, number):
        self.number = number
        self.status = {}

    def refresh(self):
        if self.number == 1:
            state = read_json(RECOVERY_DIR / 'status.json', {}) or {}
            if state:
                self.status = state
        return self.status

    @contextmanager
    def transaction(self):
        if self.number != 1:
            self.status = {'state': 'unsupported', 'error': 'Recovery supports bus 1 only'}
            yield None
            return
        with ExitStack() as stack:
            try:
                stack.enter_context(bus_lock(RECOVERY_DIR))
            except FileNotFoundError:
                # Manual/non-systemd installs retain ordinary driver retries.
                # Install/start recovery with both peripheral workers stopped.
                self.status = {'state': 'unavailable', 'error': 'Recovery service is not installed'}
                yield None
                return
            self.status = read_json(RECOVERY_DIR / 'status.json', {}) or {}
            if self.status.get('blocked'):
                raise BusSuspended('I2C bus recovery: ' + self.status.get('state', 'suspended'))
            yield self.status.get('generation')

    def request_recovery(self):
        if self.number != 1:
            return
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.settimeout(.05)
                sock.sendto(b'fault', str(RECOVERY_DIR / 'request.sock'))
        except OSError:
            self.status = dict(self.status, service_error='Recovery service is unavailable')
