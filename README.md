# Arduino Pendulum Timer

Capture firmware, logging and display software, and analysis tooling for high-precision pendulum timing.

## System Layout

```text
Arduino.Pendulum.Timer/
├── Nano.Every/         Precision capture firmware
├── Uno.R4.Deprecated/  Deprecated Uno receiver, retained for reference
├── Raspberry.Pi/       Pi Zero 2 W receiver, sensors, OLED and web
├── Docs/               System, protocol and component documentation
├── tests/              Capture, Pi, analysis and deprecated Uno tests
├── tools/              Minimal Nano serial capture tool
└── pendulum_analysis/  Offline clock and swing analysis
```

This is the combined repository for the complete Arduino Pendulum Timer (A.P.T)
system. The Nano Every timestamps pendulum and PPS events. A receiver handles storage,
environmental measurements, display and network access. The deprecated Uno receiver
and the Raspberry Pi replacement live here alongside the capture firmware.

- [Deprecated Uno R4 setup and validation](Uno.R4.Deprecated/README.md)
- [Raspberry Pi receiver and quickstart](Raspberry.Pi/README.md)
- [Display repository import and provenance](Docs/Uno.R4.Deprecated/IMPORT.md)

The former `Arduino.Pendulum.Timer.Display` repository provides historical reference;
all maintained system components now belong here.

## What Is This?

The firmware captures pendulum beam-crossing edges and GPS PPS pulses on a shared ATmega4809 timer base, then emits serial timing records for host capture and offline analysis.

The repository also includes `pendulum_analysis`, a local PCPS clock and PCSW swing analysis suite.

## Why Does It Exist?

The project is intended to measure pendulum rate, stability, jitter, impulse-cycle structure, and clock/timebase health while preserving raw timing evidence for later reinterpretation.

## How Do I Build the Nano Firmware?

Open [Nano.Every/Nano.Every.ino](Nano.Every/Nano.Every.ino) in the Arduino IDE, select **Arduino Nano Every**, and build/flash.

The default build uses an external main clock on **D2/PA0**. Supply that clock
before boot at the frequency selected by the build (`F_CPU`), or build with
`USE_EXTCLK_MAIN=0` for the internal-clock path. See [wiring](Docs/Wiring.md).

The [development and validation guide](Docs/Development_and_Validation.md)
records build prerequisites, the tested board core and separate host/board checks.
With Arduino megaAVR core 1.8.8 installed, compile from the repository root:

```bash
arduino-cli compile \
  --fqbn arduino:megaavr:nona4809 \
  --build-property compiler.cpp.extra_flags="-Os -ffunction-sections -fdata-sections -flto" \
  --build-property compiler.c.extra_flags="-Os -ffunction-sections -fdata-sections -flto" \
  --build-property compiler.elf.extra_flags="-Wl,--gc-sections -flto" \
  --build-path build/nano-every \
  Nano.Every
```

## How Do I Run It?

Flash the firmware and connect the Nano to a laptop with a USB data cable.
Data and commands default to USB **`Serial` at 115200 baud**. The
[Nano capture tool](tools/README.md) writes files for later analysis:

```bash
python3 -m pip install pyserial
python3 tools/nano_capture.py --out ./my-recording
```

Use Python 3.9 or later (`python` on Windows). A single identified Nano Every
port is selected automatically; use `--list-ports` and `--port PORT` when needed.
The tool requests `emit meta` automatically, preserves received bytes in a raw
log, and writes `PCPS.CSV` and `PCSW.CSV` after validating the capture metadata.
Stop with Ctrl+C. Choose a new output directory for each recording: existing
output files are never overwritten, and storage management is yours.

The firmware emits canonical capture records for host calculation and offline
PPS calibration:

- `CFG` and `STS` metadata/status rows
- `SCH` schema declarations
- `CSW` swing rows
- `CPS` PPS rows

For continuous logging, sensor enrichment, OLED and Wi-Fi monitoring, use the
[Raspberry Pi receiver](Raspberry.Pi/README.md), with its
[OS Lite setup checklist](Raspberry.Pi/SETUP_CHECKLIST.md),
[installation guide](Raspberry.Pi/INSTALL.md), [wiring schedule](Raspberry.Pi/WIRING.md),
[GPS/chrony timekeeping plan](Raspberry.Pi/TIMEKEEPING.md) and
[recording contract](Raspberry.Pi/DATA_FORMAT.md). Its software has automated
coverage; physical Pi acceptance remains to be completed. OLED, dashboard and
ThingSpeak share a PPS-calibrated 600-second swing mean. The optional
[individual-swing forecast](Raspberry.Pi/OBSERVATORY.md#individual-swing-forecast)
learns a configurable repeating cycle and scores predictions before learning.

Try the analysis with the checked-in synthetic recording, from the repository
root (Python 3.9 or newer):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
pendulum-analyze examples/synthetic --out analysis_test/synthetic
```

Or run directly from this checkout:

```bash
python -m pendulum_analysis.suite examples/synthetic --out analysis_test/synthetic
```

Open `analysis_test/synthetic/report.html`. On Windows, activate with
`.venv\Scripts\Activate.ps1` in PowerShell. This small synthetic example checks
the workflow; it cannot demonstrate long-run stability or environmental models.
Replace `examples/synthetic` with your recording directory for real analysis;
without `--out`, output goes into its `analysis_suite` directory. Local recordings
under `Data/` are not distributed with the repository.

PCPS is required; PCSW adds the swing analysis. The default report leads with
PPS-calibrated individual periods, complete 15-swing averages, a mean phase
profile and phase evolution over time. Clock frequency, gap-aware Allan
deviation and compact coverage/statistics support these views. Calibrated
median and jitter polar charts, complete-cycle pendulum frequency/scatter and hourly
swing-period fits to temperature, humidity and pressure are also included. Add
`--detail-start-hours 24` for a local 90-second component view,
`--autocorrelation` for persistence across impulse cycles, or `--diagnostics`
for raw comparisons, distribution plots and environmental models. Full CSV
statistics remain available. HTML charts are embedded, so the report can
be copied on its own; downloadable CSVs and JSON remain companion files.

See [Clock and swing analysis](Docs/Clock_Swing_Analysis.md) for the input contract,
bin/component mapping, settings and validation. This replaces the earlier
canonical-v2 and capture-only commands; old configuration and output schemas are
not translated. Historical analysis modules remain only for regression reference.

For tests, use the [validation guide](Docs/Development_and_Validation.md).
Whole-repository tests require Python 3.11 or newer and both packages' test
dependencies; the analysis-only installation above is not sufficient.

## Where Is Everything Else Documented?

Start with [Docs/README.md](Docs/README.md).

Primary owners:

- Wire protocol: [Docs/Protocol_Wire_Contract.md](Docs/Protocol_Wire_Contract.md)
- Data-record semantics: [Docs/Pendulum_Data_Record_Guide.md](Docs/Pendulum_Data_Record_Guide.md)
- Analysis suite: [Docs/Clock_Swing_Analysis.md](Docs/Clock_Swing_Analysis.md)
- Firmware architecture: [Docs/Implementation_Overview.md](Docs/Implementation_Overview.md)
- Hardware wiring: [Docs/Wiring.md](Docs/Wiring.md)
- Development setup and checks: [Docs/Development_and_Validation.md](Docs/Development_and_Validation.md)
