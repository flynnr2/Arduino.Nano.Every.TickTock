"""Serial reading never waits for disk, I²C or a web request."""
from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time
from pathlib import Path

from .protocol import Framer, SCHEMAS


@dataclass
class Input:
    kind: str
    raw: bytes = b''
    mono: float = 0.0
    epoch: float = 0.0
    generation: int = 0
    fragment: bool = False
    command_id: str = ''
    detail: str = ''


class Reader(threading.Thread):
    def __init__(self, settings, source='serial', replay: Path | None = None, pace=0.0, loop=False):
        super().__init__(name='serial-reader', daemon=True)
        self.settings = settings
        self.source = source
        self.replay = replay
        self.pace = pace
        self.loop = loop
        self.events = queue.Queue(maxsize=settings.queue_capacity)
        self.commands = queue.Queue(maxsize=32)
        self.stop = threading.Event()
        self.reconnect = threading.Event()
        self.ready = threading.Event()
        self.done = threading.Event()
        self.connected = False
        self.error = None
        self.generation = 0
        self.received_bytes = self.dropped_bytes = self.dropped_events = 0
        self.last_capture_received = None
        self.last_received = None
        self.last_connect = None

    def post(self, kind, raw=b'', fragment=False, command_id='', detail=''):
        event = Input(kind, raw, time.monotonic(), time.time(), self.generation,
                      fragment, command_id, detail)
        if kind == 'line':
            self.received_bytes += len(raw)
            self.last_received = event.mono
            if not fragment and raw.startswith((b'CSW,', b'CPS,')):
                self.last_capture_received = event.mono
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.dropped_events += 1
            self.dropped_bytes += len(raw)

    def run(self):
        try:
            if self.source == 'serial':
                self.serial_loop()
            elif self.source == 'replay':
                self.replay_loop()
            else:
                self.demo_loop()
        except Exception as error:
            self.error = f'{type(error).__name__}: {error}'
        finally:
            self.connected = False
            self.done.set()

    def opened(self):
        self.generation += 1
        self.ready.clear()
        self.connected = True
        self.error = None
        self.last_connect = time.monotonic()
        self.post('connected')

    def serial_loop(self):
        import serial
        last_error_report = float('-inf')
        last_error = None
        while not self.stop.is_set():
            port = None
            framer = Framer()
            first = True
            try:
                port = serial.Serial(port=None, baudrate=self.settings.baudrate,
                                     timeout=0.1, write_timeout=0.2,
                                     xonxoff=False, rtscts=False, dsrdtr=False, exclusive=True)
                port.dtr = False
                port.rts = False
                port.port = self.settings.serial_port
                port.open()
                self.opened()
                next_meta = 0.0
                while not self.stop.is_set() and not self.reconnect.is_set():
                    now = time.monotonic()
                    if not self.ready.is_set() and now >= next_meta:
                        if port.write(b'emit meta\n') != 10:
                            raise OSError('short metadata command write')
                        next_meta = now + 5
                    try:
                        if not self.ready.is_set():
                            raise queue.Empty
                        ident, command, generation = self.commands.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        if generation != self.generation:
                            self.post('command_error', command_id=ident,
                                      detail='Command belongs to an earlier serial connection; not sent')
                            continue
                        try:
                            payload = (command + '\n').encode('ascii')
                            if port.write(payload) != len(payload):
                                raise OSError('short command write; execution uncertain')
                            self.post('command_sent', command_id=ident)
                        except Exception as error:
                            self.post('command_error', command_id=ident, detail=str(error))
                            raise
                    data = port.read(min(4096, max(1, port.in_waiting)))
                    for raw, fragment in framer.feed(data):
                        # Attaching can begin in the middle of a line. Preserve but do not parse it.
                        self.post('line', raw, fragment or first)
                        if raw.endswith(b'\n'):
                            first = False
                if self.reconnect.is_set():
                    self.connected = False
                    self.ready.clear()
                    self.post('disconnected', detail='Command acknowledgement uncertain; reopening UART without resetting Nano')
                    self.reconnect.clear()
            except (OSError, serial.SerialException) as error:
                self.error = str(error)
                was_connected = self.connected
                self.connected = False
                self.ready.clear()
                if was_connected or self.error != last_error or time.monotonic() - last_error_report >= 30:
                    self.post('disconnected', detail=str(error))
                    last_error_report, last_error = time.monotonic(), self.error
            finally:
                for raw, fragment in framer.finish():
                    self.post('line', raw, fragment)
                if port:
                    port.close()
            self.stop.wait(2)

    def replay_loop(self):
        while not self.stop.is_set():
            self.opened()
            framer = Framer()
            with self.replay.open('rb') as stream:
                while not self.stop.is_set():
                    data = stream.read(1024)
                    if not data:
                        break
                    for raw, fragment in framer.feed(data):
                        # Replay applies backpressure intentionally; it is not a loss-rate benchmark.
                        while self.events.full() and not self.stop.wait(0.01):
                            pass
                        self.post('line', raw, fragment)
                        if self.stop.wait(self.pace):
                            break
            for raw, fragment in framer.finish():
                self.post('line', raw, fragment)
            if not self.loop:
                break
            self.stop.wait(max(self.pace, 0.1))

    def demo_loop(self):
        self.opened()
        cfg = 'CFG,pv=3,nhz=16000000,cst=CSW,css=canonical_swing_v2,cpt=CPS,cps=canonical_pps_v1,fw=synthetic-demo'
        self.post('line', (cfg + '\n').encode())
        for tag, (ident, names) in SCHEMAS.items():
            self.post('line', (','.join(['SCH', tag, ident, *names]) + '\n').encode())
        self.post('line', b'STS,PROGRESS_UPDATE,source,synthetic-demo\n')
        pps = 0
        while not self.stop.wait(1):
            pps += 1
            edge = pps * 16000000 & 0xffffffff
            self.post('line', f'CPS,{pps & 0xffffffff},{edge},2,0,{edge & 65535},64,{(edge + 64) & 0xffffffff},0\n'.encode())
            if pps % 2 == 0:
                start = (pps - 2) * 16000000
                values = [pps // 2 & 0xffffffff, *[(start + offset) & 0xffffffff
                          for offset in (0, 15600000, 16000000, 31600000, 32000000)], 0, 0, 0]
                self.post('line', ('CSW,' + ','.join(map(str, values)) + '\n').encode())

    def snapshot(self):
        return {'connected': self.connected, 'error': self.error, 'generation': self.generation,
                'received_bytes': self.received_bytes, 'dropped_events': self.dropped_events,
                'dropped_bytes': self.dropped_bytes, 'queued': self.events.qsize(),
                'capacity': self.settings.queue_capacity, 'last_received_monotonic': self.last_received,
                'last_capture_candidate_monotonic': self.last_capture_received}
