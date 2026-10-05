"""SSD1306 changed-page transfers and the Uno's three-screen presentation."""
from datetime import datetime, timezone
import math
import time


class ChangedPagesOLED:
    """Use the driver's framebuffer, sending only changed spans of 8-pixel pages.

    The underlying SSD1306_I2C must use its default horizontal addressing mode.
    A failed write invalidates the cache. No operation changes the I2C speed.
    """
    def __init__(self, driver, clock=time.monotonic):
        self.driver = driver
        self.clock = clock
        self.last_full = clock()
        self.next_probe = clock() + 30
        self.last_transfer = None
        self.unchanged = 0
        self.previous = None
        self.pages_sent = 0
        self.bytes_sent = 0

    def image(self, image):
        self.driver.image(image)

    def show(self):
        frame = bytes(self.driver.buffer[1:])
        if len(frame) != 1024:
            raise ValueError('Expected a 128x64 SSD1306 I2C framebuffer')
        now = self.clock()
        full = self.previous is None or now - self.last_full >= 60
        wrote = False
        try:
            if now >= self.next_probe:
                # A harmless SSD1306 NOP checks ACK without a bus scan or an
                # unsupported zero-length transaction on the BCM2835 driver.
                with self.driver.i2c_device as device:
                    device.write(b'\x00\xe3')
                self.next_probe = now + 30
            for page in range(8):
                offset = page * 128
                data = frame[offset:offset + 128]
                old = None if full else self.previous[offset:offset + 128]
                changed = [i for i in range(128) if old is None or old[i] != data[i]]
                if not changed:
                    continue
                first, last = changed[0], changed[-1]
                with self.driver.i2c_device as device:
                    device.write(bytes((0x00, 0x21, first, last, 0x22, page, page)))
                    device.write(b'\x40' + data[first:last + 1])
                wrote = True
                self.pages_sent += 1
                self.bytes_sent += last - first + 1
        except Exception:
            self.previous = None
            raise
        self.previous = frame
        if full:
            self.last_full = now
        if wrote:
            self.last_transfer = self.clock()
        else:
            self.unchanged += 1


def number(value, places=1, suffix=''):
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f'{value:.{places}f}{suffix}'
    return '--'


def sensor_code(state):
    if state.get('fresh') is False:
        return 'S' if state.get('last_good_monotonic') is not None else 'X'
    if state.get('ok'):
        return 'D' if state.get('pressure_constant_suspect') else 'R'
    if state.get('last_good_monotonic') is not None:
        return 'D'
    return 'X' if state.get('error') else 'I'


def warnings(status, now, drop_warning=False):
    """The Uno's fault order; SD labels now describe Pi recording/storage."""
    faults = []
    display, log = status.get('display') or {}, status.get('logging') or {}
    pps, env = display.get('pps') or {}, status.get('environment') or {}
    transport = status.get('transport') or {}
    if transport.get('error') or not status.get('connected') or not status.get('ready'):
        faults.append('! SERIAL ERROR')
    if not log.get('active'):
        faults.append('! SD LOG OFF')
    elif log.get('error'):
        faults.append('! SD LOG NOT READY')
    # Feed arrivals belong to acquisition; saved analysis normally publishes in
    # five-second batches. Keep the legacy fallback for standalone snapshots.
    swing_at = status.get('last_swing_received_monotonic', display.get('last_swing_monotonic'))
    swing_stale = isinstance(swing_at, (int, float)) and now - swing_at >= 3
    if 'last_pps_received_monotonic' in status:
        pps_at = status['last_pps_received_monotonic']
        age = now - pps_at if isinstance(pps_at, (int, float)) else None
    else:
        age = pps.get('record_age_seconds')
    pps_stale = isinstance(age, (int, float)) and age >= 3
    if swing_stale:
        faults.append('! SWING FEED STALE')
    if pps_stale:
        faults.append('! PPS FEED STALE')
    if drop_warning:
        faults.append('! NANO DROPS RISING')
    for label, name in (('SHT', 'sht4x'), ('BMP', 'bmp280')):
        if sensor_code(env.get(name) or {}) in ('D', 'S', 'X'):
            faults.append('! ' + label + ' SENSOR FAULT')
    if display.get('stale'):
        faults.append('! PERIOD STALE')
    if pps.get('gps_status') == 0:
        faults.append('! GPS NO PPS')
    if any((env.get(name, {}).get('i2c_recovery') or {}).get('blocked')
           for name in ('sht4x', 'bmp280')):
        faults.append('! I2C BUS BLOCKED')
    if status.get('config_error'):
        faults.append('! CONFIG ERROR')
    return tuple(faults)


class Lines(list):
    invert_header = False
    invert_ticker = True


def environmental_value(value, width):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return '--'
    for spec in ('.1f', '.2g', '.1g'):
        result = format(value, spec)
        if len(result) <= width:
            return result
    return 'range'


def body_rows(status, screen):
    display = status.get('display') or {}
    pps = display.get('pps') or {}
    if screen == 0:
        return (list(display.get('rows') or ['Waiting for swings']) + [''] * 6)[:6]
    if screen == 1:
        env, log = status.get('environment') or {}, status.get('logging') or {}
        window, forecast = display.get('window') or {}, display.get('forecast') or {}
        model_line = f"MEAN {window.get('window_seconds', 600):g}s"
        if forecast.get('enabled') and forecast.get('cycle_length'):
            model_line += f" FC:{forecast['cycle_length']}"
        age, gps = pps.get('record_age_seconds'), pps.get('gps_status')
        gps_line = 'GPS:--- AGE:--s'
        if isinstance(age, (int, float)) and age >= 0 and gps is not None:
            stale = age >= 5
            holdover = not stale and gps == 3
            seconds = (pps.get('holdover_age_ms') or 0) / 1000 if holdover else age
            name = 'STL' if stale else {0: 'NO', 1: 'ACQ', 2: 'LCK', 3: 'HLD'}.get(gps, '---')
            gps_line = f"GPS:{name} {'HAG' if holdover else 'AGE'}:{int(seconds)}s"
        base = display.get('timebase', 'WAIT')
        base = {'HOLDOVER': 'HOLD', 'NOMINAL': 'NOM', 'STALE': 'STL'}.get(base, base)
        return ['T:' + environmental_value(env.get('temperature_C'), 6) + 'C RH:'
                + environmental_value(env.get('humidity_pct'), 6) + '%',
                'P:' + environmental_value(env.get('pressure_hPa'), 14) + 'hPa',
                gps_line, 'TIMEBASE:' + base,
                'LOG:' + ('ON' if log.get('active') else 'OFF')
                + ' SD:' + ('OK' if log.get('active') and not log.get('error') else '--')
                + ' S:' + sensor_code(env.get('sht4x') or {}) + ' B:' + sensor_code(env.get('bmp280') or {}),
                model_line]
    def frequency(label, key):
        value = number(pps.get(key), 6) if pps.get('initialized') else '--'
        return f'{label:<3} {value:>17}'
    return ['TIMEBASE Hz', frequency('EST', 'blended_hz'), frequency('20s', 'fast_hz'),
            frequency('1h', 'slow_hz'), '', 'PPS ' + pps.get('status', 'WAIT')]


class OledView:
    """Port of Display.cpp row retention, banners and OledScreenRotation."""
    def __init__(self):
        self.key = self.body = None
        self.next_body = 0
        self.counters = None
        self.drop_until = 0
        self.faults = ()
        self.fault_changed = 0
        self.clock_slot = None
        self.clock_text = 'UTC: waiting for sync'
        self.messages = ['STATUS: OK']
        self.message_index = self.char_offset = 0
        self.next_ticker = 0
        self.ticker = 'STATUS: OK'
        self.alerts = []
        self.alert_refresh = False
        self.errors = ()

    def scroll_log(self, message, now, ttl=12):
        self.alerts = (self.alerts + [(str(message)[:47], now + ttl)])[-6:]
        self.alert_refresh = True

    def lines(self, status, now, started):
        errors = tuple(str(value) for value in (
            (status.get('transport') or {}).get('error'),
            (status.get('logging') or {}).get('error'), status.get('config_error'),
            ((status.get('environment') or {}).get('sht4x') or {}).get('error'),
            ((status.get('environment') or {}).get('bmp280') or {}).get('error')) if value)
        for error in errors:
            if error not in self.errors:
                self.scroll_log(error, now)
        self.errors = errors
        counters = {k: v for k, v in (status.get('counters') or {}).items()
                    if 'missing' in k or 'lost' in k or k in ('malformed', 'fragments')}
        for key in ('dropped_events', 'dropped_bytes'):
            counters[key] = (status.get('transport') or {}).get(key, 0)
        for tag, values in (status.get('latest') or {}).items():
            for key, value in values.items():
                if key.startswith('drop_'):
                    counters[tag + '.' + key] = value
        if self.counters is not None and any(v > self.counters.get(k, v) for k, v in counters.items()):
            self.drop_until = now + 12
        self.counters = counters
        updated = status.get('updated_monotonic')
        fresh = isinstance(updated, (int, float)) and 0 <= now - updated < 5
        faults = warnings(status, now, now < self.drop_until) if fresh else ('! ACQUISITION STALE',)
        changed = faults != self.faults
        if changed:
            self.faults, self.fault_changed = faults, now
        heading = self.clock_text
        if faults:
            heading = faults[int((now - self.fault_changed) // 2) % len(faults)]
        else:
            utc_health = status.get('time_health') or {}
            utc = None
            if utc_health.get('status') == 'synchronized' and utc_health.get('fresh'):
                try:
                    utc = datetime.fromisoformat(status['updated_utc'].replace('Z', '+00:00')).astimezone(timezone.utc)
                except (KeyError, ValueError, TypeError):
                    pass
            slot = int(utc.timestamp()) // 15 if utc else None
            if slot != self.clock_slot or changed:
                self.clock_text = utc.strftime('%Y-%m-%d %H:%M:%SZ') if utc else 'UTC: waiting for sync'
                self.clock_slot = slot
            heading = self.clock_text
        phase = (now - started) % 60
        screen = 0 if phase < 30 else 1 if phase < 45 else 2
        display = status.get('display') or {}
        key = (screen, (display.get('window') or {}).get('window_seconds'),
               (display.get('forecast') or {}).get('cycle_length'),
               (display.get('forecast') or {}).get('enabled'), fresh)
        if key != self.key or now >= self.next_body:
            self.body = body_rows(status, screen) if fresh else ['Check Pi services', '', 'No live measurements', '', '', '']
            self.key = key
            self.next_body = now + (10 if screen == 1 else 2)
        if changed or self.alert_refresh or now >= self.next_ticker:
            # Match the Uno: hold whole 21-character windows for four seconds,
            # then move to the next window/message. Never crawl one character.
            if changed or self.alert_refresh:
                self.message_index = self.char_offset = 0
            elif self.char_offset + 21 < len(self.messages[self.message_index]):
                self.char_offset += 21
            else:
                self.char_offset = 0
                self.message_index = (self.message_index + 1) % len(self.messages)
            previous = self.messages[self.message_index]
            self.alerts = [(text, expiry) for text, expiry in self.alerts if now < expiry]
            self.messages = list(faults) + [text for text, _ in self.alerts] or ['STATUS: OK']
            if self.message_index >= len(self.messages):
                self.message_index = 0
            if self.messages[self.message_index] != previous or self.char_offset >= len(self.messages[self.message_index]):
                self.char_offset = 0
            self.ticker = self.messages[self.message_index][self.char_offset:self.char_offset + 21]
            self.next_ticker, self.alert_refresh = now + 4, False
        result = Lines([heading] + self.body + [self.ticker])
        result.invert_header = bool(faults)
        return result


def oled_lines(status, now, started):
    """Stateless convenience for callers; the live worker retains OledView."""
    return OledView().lines(status, now, started)
