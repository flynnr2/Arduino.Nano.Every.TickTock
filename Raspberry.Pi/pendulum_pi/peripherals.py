"""Isolated sensor and OLED workers; neither worker owns the serial port.

Driver imports are lazy so replay, tests and the web server work without GPIO
libraries. Kernel I2C calls can block until the adapter's timeout: process
isolation protects capture, but cannot promise recovery from every stuck bus.
The shared bus-1 coordinator pauses all transfers for bounded clock recovery;
per-device initialization retries and sample freshness remain independent.
"""

import math
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from .common import atomic_json, read_json, utc_now, notify_watchdog
from .i2c_bus import BusCoordinator, BusBusy, BusSuspended
from .oled import ChangedPagesOLED, OledView, oled_lines

BMP280_SETTLING_SECONDS = .1


def _close(bus):
    if bus is not None:
        try:
            bus.deinit()
        except Exception:
            pass


def _sensor_factory(name, settings):
    from adafruit_extended_bus import ExtendedI2C

    bus = ExtendedI2C(settings.sensor_bus)
    try:
        if name == "sht4x":
            import adafruit_sht4x

            sensor = adafruit_sht4x.SHT4x(bus, address=settings.sht4x_address)
            sensor.mode = adafruit_sht4x.Mode.NOHEAT_HIGHPRECISION
        else:
            import adafruit_bmp280

            sensor = adafruit_bmp280.Adafruit_BMP280_I2C(bus, address=settings.bmp280_address)
        return bus, sensor
    except Exception:
        _close(bus)
        raise


def _oled_factory(settings):
    from adafruit_extended_bus import ExtendedI2C
    import adafruit_ssd1306

    bus = ExtendedI2C(settings.oled_bus)
    try:
        oled = adafruit_ssd1306.SSD1306_I2C(128, 64, bus, addr=settings.oled_address)
        oled.contrast(settings.oled_contrast)
        return bus, ChangedPagesOLED(oled)
    except Exception:
        _close(bus)
        raise


@dataclass
class SensorChannel:
    """Last good data and bounded recovery state for one physical sensor."""

    name: str
    bus: object = None
    sensor: object = None
    last_good_monotonic: float | None = None
    next_attempt: float = 0.0
    consecutive_failures: int = 0
    error: str | None = None
    ok: bool = False
    values: dict = field(default_factory=dict)
    pressure_reference: float | None = None
    pressure_constant_since: float | None = None
    pressure_constant_suspect: bool = False
    warming_up: bool = False
    coordinator: object = None
    generation: str | None = None

    def close(self):
        _close(self.bus)
        self.bus = self.sensor = None
        self.warming_up = False

    def poll(self, settings, now, factory=None, clock=None):
        if self.coordinator is None:
            self.coordinator = BusCoordinator(settings.sensor_bus)
        recovery = self.coordinator.refresh()
        if recovery.get('blocked'):
            self.close()
            self.ok, self.error = False, 'I2C bus recovery: ' + recovery.get('state', 'suspended')
            return
        if recovery.get('generation') != self.generation:
            self.next_attempt = now
        if now < self.next_attempt:
            return
        factory = factory or _sensor_factory
        # Sample time is completion time, not start time of a slow transaction.
        clock = clock or time.monotonic
        try:
            with self.coordinator.transaction() as generation:
                if generation != self.generation:
                    self.close()
                    self.generation = generation
                if self.sensor is None:
                    self.bus, self.sensor = factory(self.name, settings)
                    if self.name == "bmp280":
                        # Start/discard a conversion, then release the bus while
                        # it settles. Startup register contents never become a
                        # measured value, timestamp or constant-pressure baseline.
                        self.sensor.pressure
                        self.warming_up = True
                        self.ok, self.error = False, None
                        self.pressure_reference = self.pressure_constant_since = None
                        self.pressure_constant_suspect = False
                        self.next_attempt = clock() + BMP280_SETTLING_SECONDS
                        return
                if self.name == "sht4x":
                    temperature, humidity = map(float, self.sensor.measurements)
                    if not (math.isfinite(temperature) and -40 <= temperature <= 125
                            and math.isfinite(humidity) and 0 <= humidity <= 100):
                        raise ValueError("SHT4x reading outside valid range")
                    values = {"temperature_C": temperature, "humidity_pct": humidity}
                else:
                    pressure = float(self.sensor.pressure)
                    if not math.isfinite(pressure) or not 300 <= pressure <= 1100:
                        raise ValueError("BMP280 pressure outside 300..1100 hPa")
                    values = {"pressure_hPa": pressure}
            finished = clock()
            if self.name == "bmp280":
                pressure = values["pressure_hPa"]
                if self.pressure_reference is None or abs(pressure - self.pressure_reference) > .01:
                    self.pressure_reference = pressure
                    self.pressure_constant_since = finished
                    self.pressure_constant_suspect = False
                elif finished - self.pressure_constant_since >= 600:
                    # Diagnostic only; constant pressure can be real.
                    self.pressure_constant_suspect = True
            self.values = values
            self.last_good_monotonic = finished
            self.ok, self.error, self.consecutive_failures = True, None, 0
            self.warming_up = False
            self.next_attempt = finished + settings.sensor_interval_seconds
        except BusBusy:
            return
        except Exception as exc:
            self.close()
            self.ok = False
            self.error = f"{type(exc).__name__}: {exc}"[:300]
            self.consecutive_failures = min(65535, self.consecutive_failures + 1)
            self.next_attempt = clock() + min(60, 2 ** min(6, self.consecutive_failures - 1))
            if not isinstance(exc, BusSuspended):
                self.coordinator.request_recovery()

    def health(self):
        health = {"ok": self.ok, "last_good_monotonic": self.last_good_monotonic,
                  "error": self.error, "consecutive_failures": self.consecutive_failures,
                  "i2c_recovery": self.coordinator.status if self.coordinator else {}}
        if self.name == "bmp280":
            health["pressure_constant_suspect"] = self.pressure_constant_suspect
            health["warming_up"] = self.warming_up
        return health


def _sensor_snapshot(channels, now):
    result = {"updated_monotonic": now, "updated_utc": utc_now(),
              "temperature_C": None, "humidity_pct": None, "pressure_hPa": None}
    for channel in channels:
        result.update(channel.values)
        result[channel.name] = channel.health()
    return result


def run_sensors(settings, stop=None):
    """Publish cached values with per-device ages; core decides freshness."""
    stop = stop if stop is not None else threading.Event()
    channels = [SensorChannel("sht4x"), SensorChannel("bmp280")]
    path = settings.runtime_dir / "sensors.json"
    if not settings.sensors_enabled:
        for channel in channels:
            channel.error = "disabled"
        atomic_json(path, _sensor_snapshot(channels, time.monotonic()))
        return
    from .sensor_history import SensorHistory
    history = SensorHistory(settings)
    try:
        while not stop.is_set():
            for channel in channels:
                if stop.is_set():
                    break
                channel.poll(settings, time.monotonic())
            snapshot = _sensor_snapshot(channels, time.monotonic())
            try:
                history.append(snapshot)
            except (OSError, ValueError, sqlite3.Error) as error:
                snapshot["history_error"] = str(error)
            atomic_json(path, snapshot)
            notify_watchdog()
            stop.wait(min(.25, settings.sensor_interval_seconds))
    except KeyboardInterrupt:
        pass
    finally:
        history.close()
        for channel in channels:
            channel.close()
            channel.ok, channel.error = False, "worker stopped"
        atomic_json(path, _sensor_snapshot(channels, time.monotonic()))


def render_oled(lines):
    """Same classic 6x8 Adafruit GFX cells as the Uno, including inverse banners."""
    from PIL import Image
    from .font5x7 import FONT

    image = Image.new("1", (128, 64))
    for row, line in enumerate(lines[:8]):
        inverted = ((row == 0 and getattr(lines, 'invert_header', False))
                    or (row == 7 and getattr(lines, 'invert_ticker', False)))
        if inverted:
            image.paste(1, (0, row * 8, 128, (row + 1) * 8))
        for col, char in enumerate(str(line)[:21]):
            index = ord(char) if 32 <= ord(char) <= 126 else ord('?')
            for x, bits in enumerate(FONT[(index - 32) * 5:(index - 31) * 5]):
                for y in range(8):
                    if bits & (1 << y):
                        image.putpixel((col * 6 + x, row * 8 + y), 0 if inverted else 1)
    return image


def run_oled(settings, stop=None):
    stop = stop if stop is not None else threading.Event()
    path = settings.runtime_dir / "oled.json"
    if not settings.oled_enabled:
        atomic_json(path, {"updated_monotonic": time.monotonic(), "ok": False, "error": "disabled"})
        return
    bus = oled = None
    coordinator = BusCoordinator(settings.oled_bus)
    generation = None
    view = OledView()
    started = None
    next_attempt = time.monotonic()
    failures = 0
    last_good = None
    error = None
    try:
        while not stop.is_set():
            now = time.monotonic()
            recovery = coordinator.refresh()
            if recovery.get('blocked'):
                _close(bus)
                bus = oled = None
                error = 'I2C bus recovery: ' + recovery.get('state', 'suspended')
            elif now >= next_attempt or recovery.get('generation') != generation:
                try:
                    with coordinator.transaction() as current_generation:
                        if current_generation != generation:
                            _close(bus)
                            bus = oled = None
                            generation = current_generation
                        if oled is None:
                            bus, oled = _oled_factory(settings)
                        from .results import saved_status
                        status = saved_status(settings)
                        if started is None:
                            started = now
                        oled.image(render_oled(view.lines(status, now, started)))
                        oled.show()
                    last_good = time.monotonic()
                    error, failures = None, 0
                    next_attempt = last_good + 1
                except BusBusy:
                    pass
                except Exception as exc:
                    _close(bus)
                    bus = oled = None
                    error = f"{type(exc).__name__}: {exc}"[:300]
                    failures = min(65535, failures + 1)
                    next_attempt = time.monotonic() + min(60, 2 ** min(6, failures - 1))
                    if not isinstance(exc, BusSuspended):
                        coordinator.request_recovery()
            atomic_json(path, {"updated_monotonic": time.monotonic(), "ok": error is None and last_good is not None,
                               "last_good_monotonic": last_good, "error": error,
                               "consecutive_failures": failures, "i2c_recovery": coordinator.status,
                               "last_transfer_monotonic": getattr(oled, 'last_transfer', None),
                               "pages_sent": getattr(oled, 'pages_sent', 0),
                               "bytes_sent": getattr(oled, 'bytes_sent', 0),
                               "unchanged_frames": getattr(oled, 'unchanged', 0)})
            notify_watchdog()
            stop.wait(.5)
    except KeyboardInterrupt:
        pass
    finally:
        _close(bus)
        atomic_json(path, {"updated_monotonic": time.monotonic(), "ok": False,
                           "last_good_monotonic": last_good, "error": "worker stopped"})
