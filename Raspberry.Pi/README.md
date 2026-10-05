# Raspberry Pi receiver

A Python receiver for the **Raspberry Pi Zero 2 W** that replaces the deprecated
Uno's serial acquisition, environmental sensing, logging, OLED and web duties.
The Nano Every keeps capturing and timestamping PPS and pendulum edges; its
timing firmware remains in use. The revised [wiring schedule](WIRING.md) selects
Nano USB serial and GPS serial on the Pi GPIO UART. Nano data/commands default
to USB `Serial`; configure Pi acquisition for the observed Nano USB device.
The read-only chrony and gpsd health collectors are implemented. Installing and
configuring those OS services remains separate commissioning work.

The application is implemented and exercised with automated tests, synthetic
input and serial replay. **Physical Pi and peripheral acceptance is still to be
completed.** Start with [installation](INSTALL.md), the [wiring and voltage
schedule](WIRING.md), and the [hardware checks](HARDWARE_TESTS.md).

## What it does

- Receives protocol v3 at 115200 baud through the configured Nano serial interface (USB `Serial` by default). Requests metadata on
  joining/recovery and validates CFG plus both schemas before accepting captures.
- Records analysis-compatible `PCSW.CSV` and `PCPS.CSV`, Nano `STS.CSV`, Pi
  `PI.CSV`, byte-preserving raw evidence and a manifest. See [data format and
  recovery](DATA_FORMAT.md) for the precise output contract.
- Reads SHT4x temperature/humidity and BMP280 pressure and drives an
  SSD1306 128 × 64 OLED on shared hardware I²C bus 1 (GPIO2/SDA, GPIO3/SCL),
  at one boot-configured 400 kHz clock (100 kHz fallback).
- Coordinates sensor/OLED transfers with a bounded, privileged
  [bus recovery helper](I2C_RECOVERY.md) for the Zero 2 W. It can issue up to
  nine SCL pulses for a verified stuck bus, with two attempts per episode.
- Uses the Uno's three-screen information layout, UTC/fault header and warning
  ticker, transmitting only changed OLED page spans; see [OLED details](OLED.md).
- Uses one PPS-calibrated 600-second swing mean for period, full-swing BPM,
  gain/loss and beam-balance metrics on both OLED and HTTP dashboard. Keeps the
  PPS dual EWMA unchanged and shows filling/waiting/stale indicators. Recent
  calibrated checkpoints can prime a provisional mean after a short restart.
- Provides live status, sensor/storage health, browsing and downloads during
  recording, receiver configuration, and a restricted Nano command interface.
- Compares the swing mean with an explicitly chosen target full period. Stores
  bounded capture-based estimate history independently of browser use, with aligned
  environmental charts and gaps at discontinuities.
- Optionally forecasts the next full swing from the mean plus a zero-mean
  repeating pattern, scoring each frozen prediction when its observation arrives.
  The forecast is diagnostic and does not alter the rate estimate.
- Reports Pi UTC health through read-only background chrony checks and GPS fix,
  satellite and report-age diagnostics through gpsd, separately from Nano PPS
  calibration. Missing services are reported as unavailable.
- Separates acquisition, causal analysis, cached views, sensors, OLED, web and storage maintenance into seven processes. The serial
  reader uses a bounded queue so a slow filesystem does not immediately stop
  serial reads; overflow is counted and forces a metadata rejoin.
- Optionally publishes an hourly ThingSpeak clock diary with gain/loss, period,
  temperature and pressure. It remains inactive until a separate write key has
  been configured and verified; see [ThingSpeak operation](THINGSPEAK.md).
- Accepts lost measurements during a Pi reboot. It opens a fresh session and
  rejoins the running Nano without resetting it or fabricating missing rows.

The acquisition device remains configurable. For the revised wiring, override
the existing `/dev/serial0` default with the Nano USB serial device; the GPIO
UART is now reserved for the GPS. Nano-to-Pi communication uses USB without an
external UART level converter. GPS PPS branches directly to Pi GPIO17 at 3.3 V
and through a 5 V AHCT buffer to the Nano. The [wiring schedule](WIRING.md) distinguishes the
5 V capture domain from the Pi's 3.3 V signals and sensor/display supplies.

## Long-running storage

Completed segments are compressed, catalogued and copied to a configured archive.
All recording files rotate together at any individual/combined size or age limit.
Verified archived files can expire locally, with shorter diagnostic retention
and long-lived minute summaries. Date-range exports package completed sets;
see [storage policy and archive setup](STORAGE.md). Configure an archive before
relying on automatic cleanup: sole unarchived measurements are preserved and
recording stops at the storage limit.

## UTC timekeeping

The selected deployment uses **gpsd and chrony**, alongside the seven application services.
GPS NMEA supplies the UTC date/second; PPS on GPIO17 supplies the precise edge.
Valid GPS/PPS is preferred, with internet NTP retained for fallback and comparison.
Without either source the Pi continues on its own clock; time quality must be
reported separately from acquisition health. The Nano keeps its existing
capture duties, with no NMEA parsing or clock-setting commands.

See [TIMEKEEPING.md](TIMEKEEPING.md) for deployment, verification and limitations.
The installer does not install or configure these OS services. The dashboard
reads chrony and gpsd independently; receiver fix and satellite reports do not
establish precise Nano event-to-UTC mapping.

## Observatory

The live overview shows the shared 600-second full-period mean, full-swing BPM
and estimated gain(+)/loss(-) in seconds/day against an explicitly chosen target.
Each swing's four durations use the PPS dual-EWMA calibration available at
observation. Breakbeam diagnostics show
open/blocked interval averages and timing balance. Persistent trends align timing
and environment. Environmental relationships compare period with matching 600-second
means of temperature, humidity and pressure, individually and jointly, using the
same paired observations. Adjusted slopes and approximate time-dependent
uncertainty are shown only when they can be estimated reliably. Recorded/fitted
period and residual plots show what the combined model accounts for. Separate retrospective swing-phase
charts analyse recent raw recordings using the offline analysis package. The
individual-swing forecast panel shows the next frozen prediction, previous
observation/error and recent performance against mean-only prediction. A service
buffer retains 120 scored results for browser backfill, with observation-time labels.

The mean is provisional until 600 seconds of fresh captured duration accumulate.
It requires PPS calibration, with no nominal fallback. Expired calibration masks
current values; resumption retains previous measured observations while
provisionally refilling and requires 600 fresh seconds before readiness.

`forecast_cycle_length` is the sole pattern setting: `0` disables it by default,
`1` forecasts the mean alone, and `15` represents this Synchronome's impulse
cycle. Values through `120` support other repeating pendulum patterns. The
learned shape has a fixed 600-second adaptation half-life and averages to zero.
No clock-type-specific rule is built into the general mean.

See [the dashboard reference](OBSERVATORY.md) for learning/stale states, PPS
holdover, resets, metric meanings and chart limitations. [DATA_FORMAT.md](DATA_FORMAT.md)
owns recorded-history semantics and statistics; [OBSERVATORY_PLAN.md](OBSERVATORY_PLAN.md)
separates delivered stages from the deferred adjustment notebook and UTC work.

See [capture and saved-result architecture](ARCHITECTURE.md) for durability, replay,
service boundaries and analysis lag.

## Try it on the Mac without hardware

Python **3.11 or newer** is required. From the repository root:

```sh
python3 -m venv Raspberry.Pi/.venv
Raspberry.Pi/.venv/bin/python -m pip install -e ./Raspberry.Pi
Raspberry.Pi/.venv/bin/pendulum-pi init --config .cache/pi-demo/config.json
Raspberry.Pi/.venv/bin/pendulum-pi demo --config .cache/pi-demo/config.json
```

In a second terminal, again from the repository root:

```sh
Raspberry.Pi/.venv/bin/pendulum-pi web --config .cache/pi-demo/config.json
```

In two further terminals, run the saved-result workers:

```sh
Raspberry.Pi/.venv/bin/pendulum-pi analyze --config .cache/pi-demo/config.json
Raspberry.Pi/.venv/bin/pendulum-pi views --config .cache/pi-demo/config.json
```

Open [the local dashboard](http://127.0.0.1:8080/). Demo mode emits synthetic
PPS/swings and simulated environmental values; its source is identified in the
dashboard and recording metadata. It does not open serial or I²C. Stop it with
Ctrl+C when finished. The demo stores recordings under `.cache/pi-demo/data/`.

To exercise a checked-in wire-format fixture, stop demo acquisition and run:

```sh
Raspberry.Pi/.venv/bin/pendulum-pi replay \
  --config .cache/pi-demo/config.json \
  --input tests/fixtures/nano_capture/valid_canonical_serial.txt \
  --pace 0.01
```

Replay accepts newline-delimited Nano serial bytes, including metadata; it does
not accept `PCSW.CSV`, `PCPS.CSV` or `RAW.jsonl` directly. `--pace` is a delay per
line rather than reconstructed capture timing; use `--loop` for an ongoing
exercise. A finite replay exits cleanly, so the dashboard then reports stopped
acquisition. Replay applies queue backpressure and is not a throughput benchmark.

## Install on the Pi

Start with the concise [Pi setup checklist](SETUP_CHECKLIST.md) for Wi-Fi,
headless access, interfaces and power saving, then follow
[INSTALL.md](INSTALL.md) and [WIRING.md](WIRING.md). On the Pi, from the repository
root:

```sh
sudo bash Raspberry.Pi/deploy/install.sh
```

The installer creates seven automatically started services and preserves existing
configuration and recordings on subsequent runs. Optional ThingSpeak units are
installed but stay disabled on a new installation; activation requires a verified
write key. It does not alter boot/GPIO
settings. The installed dashboard listens on port 8080 on the local network;
see the installation guide for paths, service management, updates and SSH access.

## Configuration and commands

`pendulum-pi init --config PATH` creates a new configuration and administrator
token without overwriting an existing file. It binds the web service to localhost
unless `--lan` is supplied. Relative data/runtime paths resolve beside the config
file. Inspect settings without exposing the token:

```sh
Raspberry.Pi/.venv/bin/pendulum-pi check-config --config .cache/pi-demo/config.json
```

The web interface supports live changes to recording/storage settings, display
forecast cycle length, PPS holdover and target period, and display-history limits.
Acquisition reloads supported settings about once a second. See
[CONFIGURATION.md](CONFIGURATION.md) for the complete setting inventory,
validation rules, defaults, reload behaviour and service restart requirements.

Monitoring and downloads are readable without a token; changes require the token
from the configuration file. An empty token disables web changes. This is a
**trusted-LAN HTTP service**; HTTP does not encrypt the token or recordings.
See [the installation guide](INSTALL.md#administrator-token-and-network-access)
for SSH-tunnel access.

The restricted Nano form supports `get`, `set`, `emit meta`, `emit startup` and
`repair eeprom`; see the [command interface](HTTP_API.md) for accepted syntax and
queued-result states, and the [Nano command contract](../Docs/Command_Interface_Contract.md)
for firmware semantics. **Repair EEPROM redundancy** preserves valid saved
settings without resetting the device; follow the
[Nano repair procedure](../Docs/PPS_Discipliner_Guide.md#eeprom-diagnosis-and-repair).

For the current firmware, a successful `set` acknowledgement follows validated,
verified EEPROM saving. The Pi matches the Nano acknowledgement; it does not
perform an independent EEPROM readback. Mutations are never automatically
retried. An uncertain acknowledgement timeout forces a serial reconnect and
fresh metadata before more commands, potentially creating a recording gap.
Inspect the result before retrying. Replay/demo reject Nano commands. The web
interface does not expose arbitrary shell or Nano reset commands.

## Development and tests

The base application needs no hardware libraries for demo, replay or web use.
The installer adds the hardware dependency group. Use the shared
[development and validation guide](../Docs/Development_and_Validation.md) for
Python environments, Pi/analysis dependencies and automated checks. Physical
reliability remains a separate [acceptance exercise](HARDWARE_TESTS.md).

## Related documentation

- [Configuration reference](CONFIGURATION.md)
- [HTTP interface](HTTP_API.md)
- [Observatory dashboard reference](OBSERVATORY.md)
- [Wire contract](../Docs/Protocol_Wire_Contract.md)
- [Readiness and metadata recovery](../Docs/Host_Parser_State_Machine.md)
- [Deprecated Uno reference](../Uno.R4.Deprecated/README.md)
- [Uno OLED behaviour](../Docs/Uno.R4.Deprecated/oled-display.md)
- [Offline clock and swing analysis](../Docs/Clock_Swing_Analysis.md)
