"""Validated settings shared by the receiver and web process."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import math
from pathlib import Path

from .common import atomic_json

HOT_FIELDS = frozenset({"logging_enabled", "flush_seconds", "segment_bytes", "min_free_mb",
                        "forecast_cycle_length", "file_bytes", "segment_seconds",
                        "storage_budget_mb", "measurement_retention_days", "diagnostic_retention_days",
                        "summary_retention_days", "storage_interval_seconds", "export_max_mb",
                        "compression_enabled", "target_period_s", "history_retention_days",
                        "history_max_mb", "pps_holdover_seconds"})


@dataclass(frozen=True)
class Settings:
    serial_port: str = "/dev/serial0"
    baudrate: int = 115200
    data_dir: Path = Path("data")
    runtime_dir: Path = Path("runtime")
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    api_token: str = ""
    logging_enabled: bool = True
    flush_seconds: float = 2.0
    segment_bytes: int = 256 * 1024**2
    file_bytes: int = 128 * 1024**2
    segment_seconds: float = 7 * 86400.0
    min_free_mb: int = 1024
    storage_budget_mb: int = 8192
    measurement_retention_days: int = 90
    diagnostic_retention_days: int = 14
    summary_retention_days: int = 1825
    archive_dir: Path | None = None
    storage_interval_seconds: float = 60.0
    export_max_mb: int = 256
    compression_enabled: bool = True
    queue_capacity: int = 8192
    forecast_cycle_length: int = 0
    pps_holdover_seconds: int = 180
    target_period_s: float | None = None
    history_retention_days: int = 30
    history_max_mb: int = 256
    sensor_interval_seconds: float = 1.0
    sensor_stale_seconds: float = 10.0
    sensors_enabled: bool = True
    oled_enabled: bool = True
    sensor_bus: int = 1
    oled_bus: int = 1
    bmp280_address: int = 0x77
    sht4x_address: int = 0x44
    oled_address: int = 0x3D
    oled_contrast: int = 128


def settings_to_dict(settings: Settings, redact: bool = False) -> dict:
    result = asdict(settings)
    for key in ("data_dir", "runtime_dir", "archive_dir"):
        result[key] = str(result[key]) if result[key] is not None else None
    if redact:
        result.pop("api_token", None)
    return result


def validate(settings: Settings) -> Settings:
    ranges = {
        "baudrate": (115200, 115200), "web_port": (1024, 65535),
        "flush_seconds": (0.1, 60), "segment_bytes": (4096, 4 * 1024**3),
        "file_bytes": (4096, 1024**3), "segment_seconds": (60, 365 * 86400),
        "storage_budget_mb": (1, 1048576),
        "measurement_retention_days": (1, 36500), "diagnostic_retention_days": (1, 36500),
        "summary_retention_days": (1, 36500), "storage_interval_seconds": (1, 86400),
        "export_max_mb": (1, 4096), "min_free_mb": (0, 1048576), "queue_capacity": (32, 65536),
        "forecast_cycle_length": (0, 120),
        "pps_holdover_seconds": (5, 86400),
        "history_retention_days": (1, 3650), "history_max_mb": (8, 4096),
        "sensor_interval_seconds": (0.2, 60), "sensor_stale_seconds": (1, 300),
        "sensor_bus": (0, 255), "oled_bus": (0, 255),
        "bmp280_address": (0x76, 0x77), "sht4x_address": (0x44, 0x46),
        "oled_address": (0x3C, 0x3D), "oled_contrast": (0, 255),
    }
    floats = {"flush_seconds", "sensor_interval_seconds", "sensor_stale_seconds", "segment_seconds", "storage_interval_seconds"}
    for key, (lo, hi) in ranges.items():
        value = getattr(settings, key)
        kinds = (int, float) if key in floats else (int,)
        if type(value) not in kinds or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"{key} must be {'a number' if key in floats else 'an integer'} between {lo} and {hi}")
    for key in ("logging_enabled", "sensors_enabled", "oled_enabled", "compression_enabled"):
        if type(getattr(settings, key)) is not bool:
            raise ValueError(f"{key} must be true or false")
    if settings.target_period_s is not None:
        target = settings.target_period_s
        if type(target) not in (int, float) or not math.isfinite(target) or not 0.1 <= target <= 120:
            raise ValueError("target_period_s must be null or a number between 0.1 and 120 seconds")
    for key in ("serial_port", "web_host", "api_token"):
        value = getattr(settings, key)
        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
            raise ValueError(f"invalid {key}")
        if key != "api_token" and not value:
            raise ValueError(f"{key} cannot be empty")
    if settings.api_token and len(settings.api_token) < 24:
        raise ValueError("api_token must contain at least 24 characters, or be empty for read-only web access")
    if settings.sensor_stale_seconds < settings.sensor_interval_seconds:
        raise ValueError("sensor_stale_seconds must be at least sensor_interval_seconds")
    if settings.data_dir.resolve() == settings.runtime_dir.resolve():
        raise ValueError("data_dir and runtime_dir must be different")
    if settings.archive_dir is not None:
        archive = settings.archive_dir.resolve()
        for directory in (settings.data_dir, settings.runtime_dir):
            root = directory.resolve()
            if archive == root or archive in root.parents or root in archive.parents:
                raise ValueError("archive_dir must not overlap data_dir or runtime_dir")
    return settings


def update_settings(settings: Settings, patch: dict, hot_only: bool = True) -> Settings:
    if not isinstance(patch, dict):
        raise ValueError("configuration must be an object")
    allowed = HOT_FIELDS if hot_only else Settings.__dataclass_fields__.keys()
    unknown = patch.keys() - allowed
    if unknown:
        raise ValueError("unsupported settings: " + ", ".join(sorted(unknown)))
    patch = dict(patch)
    for key in ("data_dir", "runtime_dir", "archive_dir"):
        if key in patch:
            if key == "archive_dir" and patch[key] is None:
                continue
            if not isinstance(patch[key], (str, Path)) or not str(patch[key]):
                raise ValueError(f"{key} must be a nonempty path")
            patch[key] = Path(patch[key]).expanduser()
    return validate(replace(settings, **patch))


def load_settings(path: Path) -> Settings:
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    # Retired swing horizons may occur in an installed configuration. Discard
    # them on load; current writes reject them and no EWMA state is recreated.
    if isinstance(data, dict):
        data = {key: value for key, value in data.items() if key not in ('short_minutes', 'long_minutes')}
    settings = update_settings(Settings(), data, hot_only=False)
    return validate(replace(settings, **{key: (path.parent / getattr(settings, key)).resolve()
                                        for key in ("data_dir", "runtime_dir", "archive_dir")
                                        if getattr(settings, key) is not None}))


def save_settings(path: Path, settings: Settings) -> None:
    atomic_json(Path(path), settings_to_dict(validate(settings)), durable=True)
