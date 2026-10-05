# Pi configuration reference

This document owns the shared Pi application's JSON configuration. The implemented
validator is [config.py](pendulum_pi/config.py); installation and service setup
remain in [INSTALL.md](INSTALL.md). The separate ThingSpeak publisher file and
credentials are owned by [THINGSPEAK.md](THINGSPEAK.md), and are not fields in this
configuration. Nano tunables use the [firmware command contract](../Docs/Command_Interface_Contract.md)
rather than this file.

## Loading, validation and application

Every command except `init` requires `--config PATH`. Missing keys receive the
`Settings` defaults below; unknown keys fail validation. The sole file-load
migration exception is retired `short_minutes` and `long_minutes`: they are
ignored, are absent from serialized settings and are rejected in current API
patches. No swing EWMA state is retained. JSON booleans must be
`true` or `false`, integers must be integers rather than floats or booleans, and
numbers must be finite. Bounds below are inclusive. Numeric strings are not
coerced. The file must contain one JSON object.

`data_dir`, `runtime_dir` and non-null `archive_dir` accept nonempty paths. `~` is
expanded and relative paths resolve against the **configuration file's parent**,
not the process working directory. Loading resolves these paths; API saves based
on loaded settings retain resolved paths. `init` writes relative defaults.
Data and runtime must resolve to different directories. Archive must neither equal,
contain nor be contained by either data or runtime. Validation does not provision
hardware, verify a serial device, create an archive mount or test permissions.

Run `pendulum-pi check-config --config PATH` to validate and print the resolved
configuration without the administrator token. `GET /api/config` similarly omits
the token. Its `editable` array identifies the live fields, and an authenticated
`POST /api/config` accepts only a nonempty patch of those fields. See the
[HTTP reference](HTTP_API.md#configuration).

In the tables, **Live** means the field is in `HOT_FIELDS`: acquisition reloads
these fields about once a second, retaining its previous configuration and
reporting `config_error` if loading fails. Storage reloads the entire file at
each maintenance pass. Web requests load current settings individually, but the
listener address and port are established at process startup. Sensor/OLED workers
use startup settings. For fields marked **Restart**, edit the file, validate it
and restart affected services; moving shared directories requires coordinating
all services and their filesystem permissions. Although storage can pick up an
archive path without a restart and web authentication can pick up a token on
the next request, neither is exposed through the live-settings API.

Live persistence does not guarantee immediate completion of work. Rotation
thresholds take effect at recorder checks; retention/compression act on subsequent
maintenance; existing measurements are never recalibrated by a display setting.

## Connection, paths and web access

| Field         | JSON type | Default        | Allowed values / meaning                                 | Apply   |
| ------------- | --------- | -------------- | -------------------------------------------------------- | ------- |
| `serial_port` | string    | `/dev/serial0` | Nonempty serial device path, maximum 512 characters      | Restart |
| `baudrate`    | integer   | 115200         | Exactly 115200 baud                                      | Restart |
| `data_dir`    | string    | `data`         | Recording and display-history root                       | Restart |
| `runtime_dir` | string    | `runtime`      | Status snapshots, command queue/results and worker locks | Restart |
| `web_host`    | string    | `127.0.0.1`    | Nonempty listener host/address, maximum 512 characters   | Restart |
| `web_port`    | integer   | 8080           | 1024–65535                                               | Restart |
| `api_token`   | string    | empty string   | Empty disables mutations; otherwise 24–512 characters    | Restart |

`serial_port`, `web_host` and `api_token` reject characters with code points below
32. Read-only monitoring and downloads do not require the administrator token.
The built-in HTTP listener does not encrypt it; deployment access guidance is in
[INSTALL.md](INSTALL.md#administrator-token-and-network-access).

The default serial path is a software default, not a hardware recommendation for
all wiring. The current USB receiver arrangement needs the observed Nano
`/dev/serial/by-id/...` path; see [WIRING.md](WIRING.md).

## Recording and storage

All `_mb` values below are **MiB** (multiples of 1,048,576 bytes). Byte thresholds
are bytes, and durations marked days are elapsed days. See
[STORAGE.md](STORAGE.md) for coordinated rotation, verified archive expiry,
pressure handling and archive provisioning.

| Field                        | JSON type      | Default   | Allowed values / meaning                                          | Apply   |
| ---------------------------- | -------------- | --------- | ----------------------------------------------------------------- | ------- |
| `logging_enabled`            | boolean        | true      | Enables recording; reception and loss accounting continue if off  | Live    |
| `flush_seconds`              | number         | 2.0       | 0.1–60 seconds between periodic syncs                             | Live    |
| `segment_bytes`              | integer        | 268435456 | 4096–4294967296; rotate at combined recording byte threshold      | Live    |
| `file_bytes`                 | integer        | 134217728 | 4096–1073741824; rotate when any recording file reaches threshold | Live    |
| `segment_seconds`            | number         | 604800.0  | 60–31536000 seconds; monotonic segment duration                   | Live    |
| `min_free_mb`                | integer        | 1024      | 0–1048576 MiB; filesystem reserve                                 | Live    |
| `storage_budget_mb`          | integer        | 8192      | 1–1048576 MiB; local data-root budget                             | Live    |
| `measurement_retention_days` | integer        | 90        | 1–36500 days after closure; PCSW/PCPS retention target            | Live    |
| `diagnostic_retention_days`  | integer        | 14        | 1–36500 days after closure; RAW/STS/PI retention target           | Live    |
| `summary_retention_days`     | integer        | 1825      | 1–36500 days after closure; SUMMARY retention target              | Live    |
| `archive_dir`                | string or null | null      | Existing writable archive root; null disables archive offload     | Restart |
| `storage_interval_seconds`   | number         | 60.0      | 1–86400 seconds between maintenance passes                        | Live    |
| `export_max_mb`              | integer        | 256       | 1–4096 MiB; conservative date-range export size ceiling           | Live    |
| `compression_enabled`        | boolean        | true      | Compress eligible closed recording files                          | Live    |
| `queue_capacity`             | integer        | 8192      | 32–65536 received events; bounded in-memory queue                 | Restart |

There is no validator requirement ordering `file_bytes` and `segment_bytes`, or
ordering retention horizons. Whichever rotation condition is met first rotates
the whole set. Size limits are application checks, not filesystem quotas.
Changing `compression_enabled` does not change an already verified archive's
file representation. Retention cannot delete the only unarchived copy.

## Display and derived history

| Field                    | JSON type      | Default | Allowed values / meaning                                          | Apply |
| ------------------------ | -------------- | ------- | ----------------------------------------------------------------- | ----- |
| `forecast_cycle_length`  | integer        | 0       | 0–120 swings; optional next-full-swing repeating pattern          | Live  |
| `pps_holdover_seconds`   | integer        | 180     | 5–86400 seconds; maximum age for display PPS calibration holdover | Live  |
| `target_period_s`        | number or null | null    | 0.1–120 seconds; target used for displayed clock-rate comparison  | Live  |
| `history_retention_days` | integer        | 30      | 1–3650 days; derived display-history retention                    | Live  |
| `history_max_mb`         | integer        | 256     | 8–4096 MiB; derived history budget                                | Live  |

The swing mean window is fixed at 600 seconds, with no smoothing setting.
Each completed swing's four durations are calibrated using the unchanged PPS
dual EWMA at observation, and averaged in the same window. OLED, HTTP dashboard
and ThingSpeak all consume the resulting shared period, BPM and gain/loss.
The current swing estimate requires PPS calibration; there is no nominal fallback.
Its values are provisional until 600 seconds of fresh captured duration are
available. On calibration expiry, values are masked. Recovery retains previous
observations during provisional refilling and requires 600 fresh seconds before
becoming ready again.

`forecast_cycle_length=0` disables the individual-swing diagnostic. Set `1` for
mean-only next-swing prediction on a uniform pendulum, or `15` for this
Synchronome's repeating impulse pattern. Other lengths through 120 allow another
pendulum's repeating pattern without a clock-type setting. Pattern adaptation
uses a fixed 600-second half-life. Each forecast is frozen before the next swing
arrives, then scored before learning from that observation. Changing cycle length
resets only the forecast. It never changes the primary mean or published rate.

A null target leaves gain/loss comparison unavailable. These settings describe
display estimates and sampled history; they do not rewrite raw counters, change
Nano firmware PPS discipline or configure offline analysis. [DATA_FORMAT.md](DATA_FORMAT.md#observatory-display-history)
owns history sampling, continuity and statistical semantics.

## Sensors and OLED

I²C addresses are **integers in JSON**, so enter decimal values rather than
hexadecimal literals or strings. For example `0x77` is 119 and `0x3D` is 61.

| Field                     | JSON type | Default | Allowed values / meaning                                | Apply   |
| ------------------------- | --------- | ------- | ------------------------------------------------------- | ------- |
| `sensor_interval_seconds` | number    | 1.0     | 0.2–60 seconds; worker sampling interval                | Restart |
| `sensor_stale_seconds`    | number    | 10.0    | 1–300 seconds; maximum age of last-good sensor readings | Restart |
| `sensors_enabled`         | boolean   | true    | Enable physical environmental sensor reads              | Restart |
| `oled_enabled`            | boolean   | true    | Enable OLED output                                      | Restart |
| `sensor_bus`              | integer   | 1       | 0–255; sensor I²C bus number                            | Restart |
| `oled_bus`                | integer   | 1       | 0–255; OLED I²C bus number                              | Restart |
| `bmp280_address`          | integer   | 119     | 118–119 (`0x76`–`0x77`)                                 | Restart |
| `sht4x_address`           | integer   | 68      | 68–70 (`0x44`–`0x46`)                                   | Restart |
| `oled_address`            | integer   | 61      | 60–61 (`0x3C`–`0x3D`)                                   | Restart |
| `oled_contrast`           | integer   | 128     | 0–255; display contrast                                 | Restart |

`sensor_stale_seconds` must be at least `sensor_interval_seconds`. Valid address
ranges do not prove that the fitted device responds there. Per-device freshness
and missing-reading semantics are documented in
[measurement CSVs](DATA_FORMAT.md#measurement-csvs).

## Defaults versus generated installations

`pendulum-pi init --config PATH` refuses to overwrite an existing file. It writes
the defaults above but generates a random administrator token; `--lan` also sets
`web_host` to `0.0.0.0`. The generated relative `data` and `runtime` paths subsequently
resolve beside that configuration file.

The supplied [installer](deploy/install.sh) creates a missing configuration with
`data_dir=/var/lib/pendulum/data`, `runtime_dir=/run/pendulum`,
`web_host=0.0.0.0` and a generated administrator token. Its other shared settings
start at the defaults above, including `serial_port=/dev/serial0`. Existing
configuration is preserved. Configure the actual serial device during hardware
setup. The service units' writable paths must be updated if directories move;
changing the JSON alone does not change systemd sandbox permissions.

A successful configuration save means the Pi file was validated and durably
replaced. It is distinct from a Nano command, its acknowledgement, and EEPROM
persistence; those are covered by [HTTP command handling](HTTP_API.md#nano-commands).
