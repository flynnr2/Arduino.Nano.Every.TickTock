#!/usr/bin/env python3
"""Bounded, durable host diagnostics, independent of pendulum acquisition."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
import urllib.request

SERVICES = tuple(f'pendulum-{name}.service' for name in
                 ('acquire', 'analyze', 'web', 'sensors', 'oled', 'storage', 'views'))
MAX_BYTES = 4 * 1024 * 1024
BACKUPS = 7


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        return {'returncode': result.returncode, 'output': result.stdout.strip(),
                'error': result.stderr.strip() or None}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'error': str(exc)}


def sample():
    mem = {}
    for line in (read('/proc/meminfo') or '').splitlines():
        name, value = line.split(':', 1)
        if name in ('MemAvailable', 'MemFree', 'SwapTotal', 'SwapFree', 'Dirty', 'Writeback'):
            mem[name + '_kB'] = int(value.split()[0])
    vm = {}
    for line in (read('/proc/vmstat') or '').splitlines():
        name, value = line.split()
        if name in ('oom_kill', 'pgmajfault', 'pswpin', 'pswpout'):
            vm[name] = int(value)
    space = shutil.disk_usage('/')
    temp = read('/sys/class/thermal/thermal_zone0/temp')
    result = {'event': 'health', 'utc': datetime.now(timezone.utc).isoformat(),
              'monotonic': time.monotonic(), 'boot_id': read('/proc/sys/kernel/random/boot_id'),
              'uptime': read('/proc/uptime'), 'load': os.getloadavg(), 'memory': mem,
              'vm': vm, 'root_free_bytes': space.free,
              'temperature_C': int(temp) / 1000 if temp else None,
              'throttled': command(['/usr/bin/vcgencmd', 'get_throttled']),
              'watchdog_bootstatus': read('/sys/class/watchdog/watchdog0/bootstatus'),
              'services': command(['systemctl', 'show', *SERVICES, '--property=Id',
                                   '--property=ActiveState', '--property=SubState',
                                   '--property=NRestarts', '--property=Result',
                                   '--property=ExecMainStatus', '--property=InvocationID'])}
    try:
        with urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=1) as response:
            status = json.load(response)
        result['pendulum'] = {key: status.get(key) for key in
                              ('boot_id', 'healthy', 'capture_stale', 'counters', 'analysis')}
        result['pendulum']['history'] = {key: status.get('history', {}).get(key)
                                         for key in ('state', 'error', 'dropped_points')}
    except (OSError, ValueError) as exc:
        result['pendulum'] = {'error': str(exc)}
    return result


def append(directory, record):
    """Keep at most eight 4 MiB files; sync each observation and rotation."""
    path = directory / 'health.jsonl'
    data = (json.dumps(record, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if path.exists() and path.stat().st_size + len(data) > MAX_BYTES:
        for index in range(BACKUPS, 0, -1):
            old = path if index == 1 else directory / f'health.jsonl.{index - 1}'
            if old.exists():
                os.replace(old, directory / f'health.jsonl.{index}')
    with path.open('ab') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    # A compact durable state lets the next start report the prior boot even
    # when that boot's full health log has rotated out.
    temporary = directory / 'last-state.tmp'
    with temporary.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, directory / 'last-state.json')
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def run(directory, once=False):
    directory.mkdir(parents=True, exist_ok=True)
    os.umask(0o027)
    stop = threading.Event()
    received = []

    def stopping(signum, frame):
        received.append(signum)
        stop.set()

    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    previous = read(directory / 'last-state.json')
    try:
        previous = json.loads(previous) if previous else None
        if isinstance(previous, dict):
            # Do not recursively embed earlier startup records.
            previous = {key: previous.get(key) for key in
                        ('event', 'utc', 'monotonic', 'boot_id', 'system_state', 'signal')}
    except ValueError:
        previous = {'error': 'previous state could not be decoded'}
    record = sample()
    record.update(event='recorder_started', previous_state=previous)
    append(directory, record)
    print(json.dumps(record), flush=True)
    deadline = time.monotonic()
    while not stop.is_set():
        try:
            append(directory, sample())
        except (OSError, ValueError) as exc:
            print(json.dumps({'event': 'recording_error', 'error': str(exc)}), flush=True)
        if once:
            return
        deadline += 10
        stop.wait(max(0, deadline - time.monotonic()))
        if time.monotonic() > deadline + 10:
            deadline = time.monotonic()
    record = sample()
    state = command(['systemctl', 'is-system-running']).get('output')
    record.update(event='shutdown_requested' if state == 'stopping' else 'recorder_stopped',
                  signal=received[-1], system_state=state)
    # A shutdown request is evidence of intent, not proof shutdown completed.
    append(directory, record)
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('/var/log/pendulum-health'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    run(args.directory, args.once)
