"""Bounded, single-flight Nano commands. Never automatically retry a mutation."""
from __future__ import annotations

from pathlib import Path
import queue
import re
import time

from .common import atomic_json, read_json

IDENT = re.compile(r'[a-f0-9]{32}\Z')
COMMAND = re.compile(r'(get [A-Za-z][A-Za-z0-9_]*|set [A-Za-z][A-Za-z0-9_]* [0-9]+|emit (meta|startup)|repair eeprom)\Z')


def validate_command(command):
    if not isinstance(command, str) or len(command) > 63 or not COMMAND.fullmatch(command):
        raise ValueError('Use get PARAM, set PARAM UNSIGNED_INTEGER, emit meta, emit startup or repair eeprom (63 characters maximum)')
    if command.startswith('set ') and int(command.split()[2]) > 0xffffffff:
        raise ValueError('value exceeds an unsigned 32-bit integer')
    return command


class Commands:
    def __init__(self, runtime: Path, reader):
        self.runtime = runtime
        self.reader = reader
        self.pending = None
        self.started = time.monotonic()
        self.blocked = False
        for name in ('commands', 'results'):
            (runtime / name).mkdir(parents=True, exist_ok=True)

    def result(self, request, state, response=''):
        atomic_json(self.runtime / 'results' / (request['id'] + '.json'), {
            'id': request['id'], 'command': request['command'], 'state': state,
            'response': response, 'updated_monotonic': time.monotonic(),
        })

    def poll(self, ready):
        now = time.monotonic()
        # Do not time out an acknowledgement that is already waiting behind capture rows.
        if self.pending and now - self.pending['started'] > 10 and self.reader.events.empty():
            self.result(self.pending, 'timeout', 'No matching Nano acknowledgement. Execution/persistence is uncertain; inspect before retrying.')
            self.pending = None
            self.blocked = True
            self.reader.ready.clear()
            self.reader.reconnect.set()
        files = sorted((self.runtime / 'results').glob('*.json'), key=lambda p: p.stat().st_mtime)
        for path in files[:-256]:
            path.unlink(missing_ok=True)
        for path in sorted((self.runtime / 'commands').glob('*.json'), key=lambda p: p.stat().st_mtime):
            if not IDENT.fullmatch(path.stem):
                continue
            request = read_json(path, {})
            if not isinstance(request, dict):
                request = {}
            try:
                if request.get('id') != path.stem:
                    raise ValueError('invalid command identity')
                validate_command(request.get('command'))
                created = request.get('created_monotonic')
                if not isinstance(created, (int, float)) or not 0 <= now - created <= 30:
                    raise ValueError('command expired; it was not sent')
                if created < self.started:
                    raise ValueError('acquisition restarted after this request; not resent, execution may be uncertain')
                if self.reader.source != 'serial':
                    raise ValueError('Nano commands are unavailable in replay/demo mode')
            except (ValueError, TypeError) as error:
                self.result({'id': path.stem, 'command': request.get('command', '')}, 'error', str(error))
                path.unlink(missing_ok=True)
                continue
            if self.pending or self.blocked or not self.reader.connected or not ready:
                continue
            request['started'] = now
            request['generation'] = self.reader.generation
            try:
                self.reader.commands.put_nowait((request['id'], request['command'], self.reader.generation))
            except queue.Full:
                continue
            self.pending = request
            self.result(request, 'queued', 'Acquisition accepted this command; awaiting serial transmission')
            path.unlink(missing_ok=True)

    def transport_event(self, event):
        if not self.pending or self.pending['id'] != event.command_id:
            return
        if event.kind == 'command_sent':
            self.pending['sent_at'] = event.mono
            self.result(self.pending, 'sent', 'Awaiting Nano acknowledgement')
        elif event.kind == 'command_error':
            self.result(self.pending, 'error', event.detail + '; execution may be uncertain')
            self.pending = None

    def observe(self, record, event):
        if not self.pending or record.tag != 'STS' or len(record.fields) < 2:
            return
        if ('sent_at' not in self.pending or event.mono < self.pending['sent_at']
                or event.generation != self.pending['generation']):
            return
        fields = record.fields
        parts = self.pending['command'].split()
        if fields[1] == 'OK' and len(fields) >= 4 and fields[2].lower() == parts[0] and fields[3].lower() == parts[1].lower():
            self.result(self.pending, 'ok', ','.join(fields))
            self.pending = None
        elif fields[1] in {'UNKNOWN_COMMAND', 'INVALID_PARAM', 'INVALID_VALUE', 'INTERNAL_ERROR'}:
            self.result(self.pending, 'error', ','.join(fields))
            self.pending = None

    def interrupted(self, reason):
        self.blocked = False
        if self.pending:
            self.result(self.pending, 'error', reason + '; execution may be uncertain. Not retried.')
            self.pending = None
        while True:
            try:
                self.reader.commands.get_nowait()
            except queue.Empty:
                break
