"""Peripheral failures and freshness tested without importing Pi hardware."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from pendulum_pi.config import Settings, load_settings, save_settings, validate
from pendulum_pi.peripherals import SensorChannel, _sensor_snapshot, oled_lines, run_sensors, run_oled


class Bus:
    def __init__(self):
        self.closed = False

    def deinit(self):
        self.closed = True


def test_sensors_fail_and_recover_independently_with_last_good_age():
    settings = Settings()
    sht, bmp = SensorChannel("sht4x"), SensorChannel("bmp280")
    source = SimpleNamespace(measurements=(20, 45), pressure=1005)
    bus = Bus()
    factory = lambda name, config: (bus, source)
    sht.poll(settings, 0, factory=factory, clock=lambda: 0)
    bmp.poll(settings, 0, factory=factory, clock=lambda: 0)
    source.measurements = (float("nan"), 45)
    source.pressure = 1006
    sht.poll(settings, 1, factory=factory, clock=lambda: 1)
    bmp.poll(settings, 1, factory=factory, clock=lambda: 1)
    result = _sensor_snapshot((sht, bmp), 1)
    assert result["temperature_C"] == 20
    assert result["sht4x"]["last_good_monotonic"] == 0
    assert not result["sht4x"]["ok"]
    assert result["pressure_hPa"] == 1006
    assert result["bmp280"]["last_good_monotonic"] == 1
    assert result["bmp280"]["ok"]
    assert bus.closed
    source.measurements = (21, 46)
    sht.poll(settings, 2, factory=factory, clock=lambda: 2)
    assert sht.health()["ok"]
    assert sht.health()["error"] is None
    assert sht.last_good_monotonic == 2
    assert sht.values["temperature_C"] == 21


def test_sensor_retry_backoff_is_bounded_and_does_not_refresh_last_good():
    channel = SensorChannel("bmp280")
    calls = []

    def factory(name, settings):
        calls.append(name)
        raise OSError("device absent")

    now = 0
    for expected in (1, 2, 4, 8, 16, 32, 60, 60):
        channel.poll(Settings(), now, factory=factory, clock=lambda: now)
        assert channel.next_attempt == now + expected
        count = len(calls)
        channel.poll(Settings(), now + .5, factory=factory, clock=lambda: now + .5)
        assert len(calls) == count
        now += expected
    assert channel.last_good_monotonic is None
    assert "device absent" in channel.error


@pytest.mark.parametrize("pressure", [float("nan"), float("inf"), 299, 1101, None])
def test_pressure_invalid_reading_is_never_published(pressure):
    channel = SensorChannel("bmp280")
    channel.poll(Settings(), 0, factory=lambda *args: (Bus(), SimpleNamespace(pressure=pressure)), clock=lambda: 0)
    assert not channel.ok
    assert not channel.values


@pytest.mark.parametrize("reading", [(126, 40), (-41, 40), (20, -1), (20, 101)])
def test_temperature_humidity_ranges_are_checked(reading):
    channel = SensorChannel("sht4x")
    channel.poll(Settings(), 0, factory=lambda *args: (Bus(), SimpleNamespace(measurements=reading)), clock=lambda: 0)
    assert not channel.ok
    assert not channel.values


def test_pressure_constant_is_diagnostic_and_resets_after_movement():
    channel = SensorChannel("bmp280")
    source = SimpleNamespace(pressure=1000)
    factory = lambda *args: (Bus(), source)
    for now in (0, 599, 600):
        channel.poll(Settings(), now, factory=factory, clock=lambda: now)
    assert channel.health()["pressure_constant_suspect"]
    assert channel.ok and channel.values["pressure_hPa"] == 1000
    source.pressure = 1001
    channel.poll(Settings(), 601, factory=factory, clock=lambda: 601)
    assert not channel.health()["pressure_constant_suspect"]


def test_slow_read_uses_completion_time_for_freshness():
    channel = SensorChannel("sht4x")
    channel.poll(Settings(), 0, factory=lambda *args: (Bus(), SimpleNamespace(measurements=(20, 40))), clock=lambda: 2)
    assert channel.last_good_monotonic == 2
    assert channel.next_attempt == 3


def test_disabled_workers_publish_explicit_health_without_hardware(tmp_path):
    settings = replace(Settings(), runtime_dir=tmp_path, sensors_enabled=False, oled_enabled=False)
    run_sensors(settings)
    run_oled(settings)
    sensors = json.loads((tmp_path / "sensors.json").read_text())
    oled = json.loads((tmp_path / "oled.json").read_text())
    assert sensors["sht4x"]["error"] == sensors["bmp280"]["error"] == "disabled"
    assert sensors["temperature_C"] is None
    assert oled["error"] == "disabled"


def test_oled_rotation_and_dead_acquisition_are_explicit():
    status = {"updated_monotonic": 0, "connected": True, "ready": True,
              "logging": {"active": True}, "display": {"rows": ["row"] * 6,
              "timebase": "PPS", "pps": {"status": "LOCKED"}},
              "environment": {"temperature_C": 20, "humidity_pct": None, "pressure_hPa": 1000}}
    assert oled_lines(status, 0, 0)[1:7] == ["row"] * 6
    status["updated_monotonic"] = 30
    lines = oled_lines(status, 30, 0)
    assert "RH:--" in lines[1]
    assert lines[2] == "P:1000.0hPa"
    assert lines[6] == "MEAN 600s"
    status["updated_monotonic"] = 45
    assert oled_lines(status, 45, 0)[1] == "TIMEBASE Hz"
    assert "! ACQUISITION STALE" in oled_lines(status, 51, 0)
    # Future timestamps from an old boot must not look fresh either.
    assert "! ACQUISITION STALE" in oled_lines(status, 0, 0)


def test_oled_render_fits_eight_rows():
    pytest.importorskip("PIL")
    from pendulum_pi.peripherals import render_oled

    image = render_oled(["P L~ 2000000.00us", "dB L~ +10000000.00us"] * 4)
    assert image.size == (128, 64)
    assert image.mode == "1"
    assert image.getbbox() is not None


def test_workers_release_bus_and_publish_stop_state(tmp_path, monkeypatch):
    import pendulum_pi.peripherals as module

    class Stop:
        done = False

        def is_set(self):
            return self.done

        def wait(self, seconds):
            self.done = True

    buses = []

    def factory(name, settings):
        bus = Bus()
        buses.append(bus)
        return bus, SimpleNamespace(measurements=(20, 40), pressure=1000)

    monkeypatch.setattr(module, "_sensor_factory", factory)
    run_sensors(replace(Settings(), runtime_dir=tmp_path), stop=Stop())
    assert len(buses) == 2 and all(bus.closed for bus in buses)
    result = json.loads((tmp_path / "sensors.json").read_text())
    assert result["sht4x"]["error"] == "worker stopped"
    assert not result["bmp280"]["ok"]
    assert result["temperature_C"] == 20


@pytest.mark.parametrize("settings, sensor_bus, oled_bus", [
    (Settings(), 1, 1),
    (replace(Settings(), sensor_bus=2, oled_bus=2), 2, 2),
    (replace(Settings(), sensor_bus=1, oled_bus=3), 1, 3),
])
def test_driver_factories_route_addresses_to_configured_buses(monkeypatch, settings, sensor_bus, oled_bus):
    import sys
    import pendulum_pi.peripherals as module

    calls = []

    def bus_factory(number):
        bus = Bus()
        bus.number = number
        return bus

    def sht_factory(bus, address):
        calls.append(("sht4x", bus.number, address))
        return SimpleNamespace(mode=None)

    def bmp_factory(bus, address):
        calls.append(("bmp280", bus.number, address))
        return SimpleNamespace()

    def oled_factory(width, height, bus, addr):
        calls.append(("oled", bus.number, addr, width, height))
        return SimpleNamespace(contrast=lambda value: calls.append(("contrast", value)))

    monkeypatch.setitem(sys.modules, "adafruit_extended_bus", SimpleNamespace(ExtendedI2C=bus_factory))
    monkeypatch.setitem(sys.modules, "adafruit_sht4x", SimpleNamespace(
        SHT4x=sht_factory, Mode=SimpleNamespace(NOHEAT_HIGHPRECISION=1)))
    monkeypatch.setitem(sys.modules, "adafruit_bmp280", SimpleNamespace(Adafruit_BMP280_I2C=bmp_factory))
    monkeypatch.setitem(sys.modules, "adafruit_ssd1306", SimpleNamespace(SSD1306_I2C=oled_factory))
    validate(settings)
    _, sht = module._sensor_factory("sht4x", settings)
    module._sensor_factory("bmp280", settings)
    module._oled_factory(settings)
    assert sht.mode == 1
    assert calls == [("sht4x", sensor_bus, 0x44), ("bmp280", sensor_bus, 0x77),
                     ("oled", oled_bus, 0x3d, 128, 64), ("contrast", 128)]


def test_oled_retries_failure_and_recovers_without_serial(tmp_path, monkeypatch):
    import pendulum_pi.peripherals as module

    clock = [0.0]
    published = []
    frames = []
    calls = []
    bus = Bus()

    class Stop:
        def is_set(self):
            return clock[0] >= 1.5

        def wait(self, seconds):
            clock[0] += seconds

    def factory(settings):
        calls.append(clock[0])
        if len(calls) == 1:
            raise OSError("OLED unplugged")
        return bus, SimpleNamespace(image=frames.append, show=lambda: None)

    monkeypatch.setattr(module, "_oled_factory", factory)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "atomic_json", lambda path, value: published.append(value))
    monkeypatch.setattr(module, "render_oled", lambda lines: lines)
    run_oled(replace(Settings(), runtime_dir=tmp_path), Stop())
    assert calls == [0, 1]
    assert "OLED unplugged" in published[0]["error"]
    assert any(value["ok"] for value in published)
    assert frames and "! ACQUISITION STALE" in frames[0]
    assert published[-1]["error"] == "worker stopped"
    assert bus.closed


def test_shared_bus_configuration_loads_and_roundtrips(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}")
    settings = load_settings(path)
    assert settings.sensor_bus == settings.oled_bus == 1
    save_settings(path, settings)
    assert load_settings(path) == settings
    # Existing installations can migrate only the old OLED bus setting.
    path.write_text(json.dumps({"sensor_bus": 1, "oled_bus": 1}))
    assert load_settings(path) == settings
