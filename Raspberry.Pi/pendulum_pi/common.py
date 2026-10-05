"""Small, process-safe file exchange helpers; never publish half a JSON document."""
from __future__ import annotations

import json
import os
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def boot_id() -> str | None:
    """Identify the Linux monotonic-clock epoch; unknown hosts fail closed."""
    try:
        value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return str(UUID(value))
    except (OSError, ValueError):
        return None


def atomic_json(path: Path, data, *, durable: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, allow_nan=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        os.replace(temporary, path)
        if durable:
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_json(path: Path, default=None):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return default


def notify_watchdog() -> None:
    """Let systemd restart a worker whose main servicing loop stops progressing."""
    address = os.environ.get('NOTIFY_SOCKET')
    if not address:
        return
    if address.startswith('@'):
        address = '\0' + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.1)
            sock.sendto(b'WATCHDOG=1', address)
    except OSError:
        pass
