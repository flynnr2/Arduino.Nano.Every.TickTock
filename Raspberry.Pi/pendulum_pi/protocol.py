"""Protocol v3 decoder; raw records and metadata readiness are separate concerns."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
import re

CSW_FIELDS = ['seq', 'edge0_tcb0', 'edge1_tcb0', 'edge2_tcb0', 'edge3_tcb0',
              'edge4_tcb0', 'drop_ir', 'drop_pps', 'drop_swing']
CPS_FIELDS = ['seq', 'edge_tcb0', 'gps_status', 'holdover_age_ms', 'cap16',
              'latency16', 'now32', 'drop_pps']
SCHEMAS = {'CSW': ('canonical_swing_v2', CSW_FIELDS), 'CPS': ('canonical_pps_v1', CPS_FIELDS)}
EXPECTED_CFG = {'pv': '3', 'cst': 'CSW', 'css': SCHEMAS['CSW'][0],
                'cpt': 'CPS', 'cps': SCHEMAS['CPS'][0]}
UINT32 = (1 << 32) - 1


class ProtocolError(ValueError):
    pass


class ContractChanged(ProtocolError):
    pass


@dataclass
class Record:
    tag: str
    fields: list[str]
    values: dict = field(default_factory=dict)


def decode(raw: bytes) -> Record:
    try:
        line = raw.decode('ascii').rstrip('\r\n')
        if '\x00' in line or '\n' in line or '\r' in line:
            raise ProtocolError('control character in line')
        fields = next(csv.reader([line], strict=True))
    except (UnicodeError, csv.Error, StopIteration) as error:
        raise ProtocolError(f'invalid serial text: {error}') from error
    if not fields:
        return Record('TEXT', fields)
    tag = fields[0]
    if tag in SCHEMAS:
        names = SCHEMAS[tag][1]
        if len(fields) != len(names) + 1:
            raise ProtocolError(f'{tag} field count does not match protocol v3')
        values = {}
        for name, value in zip(names, fields[1:]):
            maximum = 3 if name == 'gps_status' else 65535 if name in {'cap16', 'latency16'} else UINT32
            if not re.fullmatch(r'[0-9]{1,10}', value) or int(value) > maximum:
                raise ProtocolError(f'invalid {tag}.{name}')
            values[name] = int(value)
        return Record(tag, fields, values)
    if tag == 'CFG':
        values = {}
        for token in fields[1:]:
            if '=' not in token:
                raise ProtocolError('CFG requires key=value')
            key, value = token.split('=', 1)
            if not key or key in values:
                raise ProtocolError('empty or duplicate CFG key')
            values[key] = value
        if any(values.get(key) != value for key, value in EXPECTED_CFG.items()):
            raise ProtocolError('unsupported CFG contract')
        nhz = values.get('nhz', '')
        if not re.fullmatch(r'[0-9]{1,10}', nhz) or not 1000 <= int(nhz) <= UINT32 or not values.get('fw'):
            raise ProtocolError('CFG requires nominal frequency and firmware identity')
        return Record(tag, fields, values)
    if tag == 'SCH':
        if len(fields) < 3 or fields[1] not in SCHEMAS:
            raise ProtocolError('unknown schema family')
        ident, names = SCHEMAS[fields[1]]
        if fields[2:] != [ident, *names]:
            raise ProtocolError('unsupported schema declaration')
        return Record(tag, fields, {'tag': fields[1], 'id': ident, 'fields': names})
    if tag == 'STS':
        if len(fields) < 2 or not fields[1]:
            raise ProtocolError('STS requires a status code')
        return Record(tag, fields)
    # Command replies coexist with CSV on Serial1. Retain rather than reinterpret.
    if line.startswith(('get:', 'set:', 'reset:', 'ERROR:', 'help', 'Usage:')):
        return Record('TEXT', fields)
    raise ProtocolError('unknown record tag')


@dataclass
class Contract:
    cfg: dict = field(default_factory=dict)
    schemas: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return bool(self.cfg) and set(self.schemas) == set(SCHEMAS)

    @property
    def nominal_hz(self) -> int:
        return int(self.cfg.get('nhz', 0))

    def observe(self, record: Record) -> None:
        if record.tag == 'CFG':
            if self.cfg and self.cfg != record.values:
                raise ContractChanged('capture contract changed')
            self.cfg = dict(record.values)
        elif record.tag == 'SCH':
            self.schemas[record.values['tag']] = dict(record.values)

    def snapshot(self) -> dict:
        return {'cfg': self.cfg, 'schemas': self.schemas}


class Framer:
    """Bound partial-line memory. Oversized frames are retained as bounded raw chunks."""
    def __init__(self, limit: int = 2048):
        self.limit = limit
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, data: bytes):
        out = []
        for value in data:
            self.buffer.append(value)
            if value == 10:
                out.append((bytes(self.buffer), self.discarding))
                self.buffer.clear()
                self.discarding = False
            elif len(self.buffer) >= self.limit:
                out.append((bytes(self.buffer), True))
                self.buffer.clear()
                self.discarding = True
        return out

    def finish(self):
        if self.buffer:
            result = (bytes(self.buffer), True)
            self.buffer.clear()
            self.discarding = False
            return [result]
        return []


def sequence_step(previous: int | None, current: int) -> tuple[str, int]:
    if previous is None:
        return 'join', 0
    step = (current - previous) & UINT32
    if step == 0:
        return 'duplicate', 0
    if step >= 1 << 31:
        return 'restart', 0
    return ('gap', step - 1) if step > 1 else ('next', 0)
