# Implementation Overview

Status: implementation overview. This document maps firmware responsibilities and source modules. It intentionally cross-links to specialised docs rather than duplicating full schemas, command tables, or config tables.

Companion subsystem docs:
- `Docs/Memory_and_Telemetry_Budget.md` (memory/telemetry tradeoffs)
- `Docs/Capture_Timebase_Architecture.md` (EVSYS/TCB and shared-timeline projection)
- `Docs/Config_Defines_Guide.md` (src/Config.h #defines)
- `Docs/Acquisition_Guide.md` (capture and host responsibilities)
- `Docs/Pendulum_Data_Record_Guide.md` (pendulum record interpretation)
- `Docs/PPS_Discipliner_Guide.md` (EWMA model, state machine, tuning workflow)

## Document status

This document describes the capture, health monitoring and serial output pipeline.
The firmware emits five swing boundaries and PPS captures on one shared counter.
Hosts calculate durations and calibrate the clock from those boundaries.

`FullSwing` holds a sequence number and five TCB0 timestamps. No frequency
estimate changes these timestamps. The wire layout is defined in
[Protocol_Wire_Contract.md](Protocol_Wire_Contract.md).

## Module Ownership Overview

### Hardware capture and event transport

- **`CaptureInit.*`** configures EVSYS plus TCB0/TCB1/TCB2.
- **`PendulumCapture.*`** owns the ISR-fed edge/PPS rings, coherent TCB0 timestamp helpers, overflow bookkeeping, and shared capture diagnostics.
- **`SwingAssembler.*`** drains captured IR edges in the main loop and converts them into `FullSwing` records.

### PPS validation and disciplined timebase

- **`PpsValidator.*`** classifies full 32-bit PPS intervals as `OK`, `GAP`, `DUP`, or `HARD_GLITCH` and seeds/reseeds the reference interval.
- **`FreqDiscipliner.*`** maintains fast/slow frequency estimates plus the `FREE_RUN`, `ACQUIRE`, `DISCIPLINED`, and `HOLDOVER` state machine.
- **`DisciplinedTime.*`** turns discipliner state into the frequency estimate used in optional tuning telemetry. The current host suite separately derives calibration from canonical PPS intervals.

### Runtime control, telemetry, and persistence

- **`SerialParser.*`** owns command tokenization, capture schema/record emission (`SCH`/`CSW`/`CPS`), and generic `STS` framing.
- **`TunableRegistry.*`** is the single source of truth for tunable names, parsing, validation, EEPROM mapping, and runtime help text.
- **`TunableCommands.*`** handles tunable reads/writes, default restoration, and EEPROM redundancy repair.
- **`TunablesRuntime.cpp`** stores live tunable values and normalizes dependent settings.
- **`StatusTelemetry.*`** emits retained boot/config `STS` records plus optional PPS tuning snapshots.
- **`MemoryTelemetry.*`** computes current free SRAM on AVR (`__brkval`/`__heap_start` vs stack), tracks retained runtime minimum, and emits boot/periodic `mem` STS records.
- **`EEPROMConfig.*`** loads/saves the active tunable schema with CRC protection.

### Top-level orchestration

- **`PendulumCore.*`** coordinates setup, invokes the capture/PPS/swing/runtime modules, packages finished swings as capture records, and publishes results.
- **`Nano.Every.ino`** is only a sketch wrapper calling `pendulumSetup()` / `pendulumLoop()`.

## Timing and Data Flow

### 1. Shared timer base

`CaptureInit` sets up:
- **TCB0** as the free-running reference with a software-maintained high word
- **TCB1** as the IR capture timer
- **TCB2** as the PPS capture timer
- **EVSYS** routes PB0 to TCB1 and PD0 to TCB2 so edge detection happens in hardware

`PendulumCapture` exposes coherent TCB0 reads so both IR and PPS events can be projected into one monotonic timestamp space.

### 2. IR edge capture -> `SwingAssembler`

The TCB1 ISR records each IR edge with minimal work:
- latch capture timing
- backdate onto the TCB0 timeline
- tag the edge type
- push into the edge ring
- arm the next expected polarity

Later, `swingAssemblerProcessEdges()` walks those edge events through a five-state reconstruction machine. Once both half-swings are complete, it publishes a `FullSwing` into a separate swing ring for `PendulumCore` to consume.

### 3. PPS capture -> `PpsValidator` -> `FreqDiscipliner` -> `DisciplinedTime`

The TCB2 ISR mirrors the IR path by capturing PPS edges, projecting them onto the TCB0 timeline, and pushing compact `PpsCapture` records into the PPS ring.

In the main loop, `PendulumCore::process_pps()` then:
1. drains queued PPS captures from `PendulumCapture`
2. preserves consecutive raw PPS captures for telemetry
3. uses `PpsEdgeFilter` and `PpsValidator` to select plausible one-second intervals
4. feeds accepted/anomalous observations into `FreqDiscipliner`
5. updates `DisciplinedTime` when optional tuning telemetry is enabled
6. updates exported correction metrics and `gps_status`

## PPS Runtime Behavior

### Validation stage

`PpsValidator` decides whether a new PPS interval should be trusted. It:
- learns a reference interval during startup/recovery seeding
- classifies intervals as `OK`, `GAP`, `DUP`, or `HARD_GLITCH`
- tracks health counters and ok-streaks for downstream lock logic

`PpsEdgeFilter` keeps the measurement candidate separate from raw capture.
Early rejected captures leave the candidate unchanged, so an extra capture
between genuine PPS edges does not spoil the following one-second interval.
Late rejected captures become recovery candidates; a subsequent acceptable
interval resumes estimation. Early extras bypass validator health and discipliner
updates, preserving lock/unlock streaks. Late/recovery observations still enter
health accounting; rejected intervals cannot update frequency estimates.
Freshness timeouts reset acquisition health. See the
[PPS guide](PPS_Discipliner_Guide.md#1-model-overview) for these distinctions.

Canonical `CPS` emission is attempted for every queued raw capture when output
is ready, including rejected intervals. Failed PPS sends are not retried.
Optional `PPS_BASE.d` and `PPS_BASE.c` describe consecutive raw intervals and their
classification. After an extra edge these may differ from the interval accepted
for calculations; the remaining discipliner fields and `TUNE_*` describe the
filtered processing state. No wire columns are added or removed.

### Disciplining stage

`FreqDiscipliner` maintains:
- **fast** estimate for quicker acquisition behavior
- **slow** estimate for quieter long-term behavior
- **R** = frequency error metric in ppm
- **MAD residual ticks** = the jitter/quality gate used for lock/unlock decisions
- transitions between `FREE_RUN`, `ACQUIRE`, `DISCIPLINED`, and `HOLDOVER`

Fast and slow filters retain Q16 fractional state internally and round only
when exposing integer-Hz estimates. Small positive and negative frequency errors
therefore accumulate instead of disappearing through integer shifts.

Offline `canonical_metrology` uses PPS phase regression and is deliberately
separate from firmware parity. `pendulum_analysis.pps.firmware_parity` provides
`FirmwareParity`, `FirmwareConfig`, and `DiscState` for replaying the current
Q16 discipliner:

```python
from pendulum_analysis.pps.firmware_parity import FirmwareParity, FirmwareConfig

replay = FirmwareParity(config=FirmwareConfig())
replay.observe("OK", True, 16_000_014, now_ms=1000)
print(replay.state, replay.fast, replay.slow, replay.applied)
```

Exact replay requires the actual validator class, validity, foreground
`now_ms`, anomaly flag, reset events and effective tunables for each call.
PCPS captures alone omit queue timing and timeout calls, so they cannot establish
exact firmware-state parity. Call `reset(nominal_hz)` on runtime resets and
update `config` when tunables change. This replay models the current Q16 firmware,
not historical integer-only estimator output. Cross-language tests compare its
public telemetry and state transitions against production C++.

### Diagnostic frequency stage

`DisciplinedTime` tracks the current frequency estimate for health and optional
telemetry. The shared TCB0 counter continues free-running. All swing calibration
is performed by the host from captured PPS and swing timestamps.

## Swing Assembly and Output

`PendulumCore::pendulumLoop()` processes commands, queued PPS captures and then
IR edges. `SwingAssembler` groups alternating IR edges into five-boundary swings.
Consecutive swings share their terminal/initial edge. Each completed row contains
six uint32 values (24 bytes): the sequence number and five timestamps.

The foreground loop attaches cumulative drop counters and emits `CSW`. A failed
send leaves the oldest row pending for retry. A full completed-swing ring drops
the newly completed row and increments its drop counter. Queued raw PPS captures
are emitted as `CPS` before their health classification.

The stream contains `CFG` and `STS` metadata, full `SCH` declarations, and
`CSW`/`CPS` captures. See the [wire contract](Protocol_Wire_Contract.md).

## Serial Commands and Telemetry

### Command surface

`SerialParser` supports help, tunable reads/writes, `reset defaults`,
`repair eeprom`, and metadata replay (`emit meta` / `emit startup`). The
[command contract](Command_Interface_Contract.md) owns the complete grammar,
argument checks, replies and mutation policy.

When `CLI_ALLOW_MUTATIONS=0`, `set`, `reset`, and `repair` return explicit
invalid-param status; `help`, `get`, and `emit` remain available.

### Tunable handling

Responsibilities are intentionally split:
- `SerialParser` tokenizes input and routes commands
- `TunableCommands` performs command-specific flow
- `TunableRegistry` owns the authoritative list of tunables and runtime help text
- `TunablesRuntime.cpp` holds live values and normalization helpers

### Retained STS contract

Startup always emits reset-cause/boot-sequence status, `schema`, and mirrored
`cfg` metadata, followed by capture schema declarations. With
`ENABLE_DIAGNOSTIC_TELEMETRY=1`, the boot replay additionally includes:

- `build`
- `flags`
- `PREV_BOOT` when restart breadcrumbs are enabled
- `mem` when `ENABLE_MEMORY_TELEMETRY_STS=1` (off in the default profile)
- clock and serial diagnostics subject to their individual gates
- four tunables snapshot lines emitted as `<param>,<value>,...` pairs:
  - line 1: `ppsFastShift`, `ppsSlowShift`, `ppsBlendLoPpm`, `ppsBlendHiPpm`, `ppsLockRppm`
  - line 2: `ppsLockMadTicks`, `ppsUnlockRppm`, `ppsUnlockMadTicks`, `ppsLockCount`, `ppsUnlockCount`
  - line 3: `ppsHoldoverMs`, `ppsStaleMs`, `ppsIsrStaleMs`, `ppsCfgReemitDelayMs`, `ppsAcquireMinMs`
  - line 4: `ppsMetrologyGraceMs`
- `pps_cfg`
- `pps_freshness`

Other optional families are controlled by their individual flags and profile
defaults (see [Config_Defines_Guide.md](Config_Defines_Guide.md)):

- `TUNE_CFG`, `TUNE_WIN`, `TUNE_EVT` when `PPS_TUNING_TELEMETRY=1`
- `PPS_BASE` when `ENABLE_PPS_BASELINE_TELEMETRY=1`
- `mem_warn` when memory telemetry and low-water warnings are enabled and free SRAM crosses `MEMORY_LOW_WATER_WARN_BYTES`

### Boot record details

- `build` identifies the binary (`git`, dirty bit, UTC, board, MCU, raw toolchain clock, selected main clock source/rate, baud)
- `schema` states the current `STS` schema version, capture schema IDs, and EEPROM schema version
- `flags` advertises which intentional compile-time runtime modes were compiled in
- `mem` reports `free_now`, retained low-water `free_min`, and `phase` (`boot` at startup, `periodic` thereafter at `MEMORY_TELEMETRY_PERIOD_MS`); sampling updates continuously in `pendulumLoop()` to preserve a runtime watermark between emissions
- the four tunables snapshot lines summarize retained tunables as `param,value` pairs
- `cfg` publishes protocol/schema IDs, nominal counter frequency and firmware identity.
- `CFG` mirrors that metadata as a top-level record.
- `pps_cfg` publishes PPS validator acceptance windows and seeding thresholds
- `pps_freshness` documents the meaning of `ppsStaleMs` vs `ppsIsrStaleMs`
- `serial_diag` (when emitted) includes both aggregate and required-only emission-drop counters (`fmt_acq_fail_required`, `required_drop`) to make best-effort vs required telemetry loss visible

## Configuration and Persistence

### Compile-time defaults and supported modes

`Config.h` contains runtime defaults, supported mode controls, and some retained
or reserved flags (see [Config_Defines_Guide.md](Config_Defines_Guide.md)):
- semantic main clock configuration (`MAIN_CLOCK_HZ`, `USE_EXTCLK_MAIN`)
- timebase selection (`USE_ARDUINO_TIMEBASE`, `DISABLE_ARDUINO_TCB3_TIMEBASE`)
- optional telemetry (`PPS_TUNING_TELEMETRY`, `ENABLE_PPS_BASELINE_TELEMETRY`)
- memory telemetry controls (`ENABLE_MEMORY_TELEMETRY_STS`, `MEMORY_TELEMETRY_PERIOD_MS`, optional `ENABLE_MEMORY_LOW_WATER_WARN_STS` / `MEMORY_LOW_WATER_WARN_BYTES`)
- serial behavior (`ENABLE_PERIODIC_FLUSH`, `FLUSH_PERIOD_MS`, `LED_ACTIVITY_ENABLE`, `LED_ACTIVITY_DIV`)
- command mutability (`CLI_ALLOW_MUTATIONS`, where `0` locks `set`/`reset defaults`/`repair eeprom`)
- build metadata (`GIT_SHA`, `BUILD_UTC`, `BUILD_DIRTY`)

Historical compile-time leftovers for deleted coherent-now checks and unused fixed-point conversion paths have been removed.

`MAIN_CLOCK_HZ` is the firmware's semantic nominal clock contract for runtime math, validation, and emitted `nhz`/`main_clock_hz` telemetry. It must match the board/toolchain `F_CPU`.

With the default `USE_EXTCLK_MAIN=1`, the early clock initialization performs a one-shot boot-time handoff to the ATmega4809 `EXTCLK` input on **PA0 / Arduino D2** before serial and timer initialization. It consumes D2/PA0 as the driven clock input and requires an external clock at boot matching `F_CPU`. Build with `USE_EXTCLK_MAIN=0` for the internal-clock path; do not switch dynamically after startup.

### Runtime tunables

The live tunables are only the active discipliner settings:
- `ppsFastShift`, `ppsSlowShift`
- blend thresholds
- lock/unlock thresholds and streak counts
- holdover/stale timers (`ppsStaleMs` for queued PPS sample processing freshness, `ppsIsrStaleMs` for ISR-edge freshness)
- PPS config re-emit delay
- minimum acquire dwell
- metrology grace duration (`ppsMetrologyGraceMs`)

### EEPROM config path

`EEPROMConfig` serializes the tunables through the registry into two
schema-versioned, CRC-protected slots. Loading selects the newest semantically
valid record of the current schema. If neither slot is valid, startup uses
compiled defaults; there is no automatic migration of older layouts.

`set` validates the candidate against the same semantic rules as EEPROM loading.
`set` and `reset defaults` acknowledge success only after EEPROM readback verifies
the header, payload and final commit marker. A failed save returns an error and
restores the previous live settings, leaving the other valid slot untouched.
`reset defaults` resets PPS acquisition only after saving succeeds.

`repair eeprom` copies the newest valid saved configuration into an invalid slot
and verifies it without changing live tunables or PPS acquisition. It is a no-op
success when both slots are valid and fails without writing when neither is valid.
See the [command contract](Command_Interface_Contract.md#tunable-command-ack-payload-formats-emittunablecommandack)
for replies and the [PPS guide](PPS_Discipliner_Guide.md#eeprom-diagnosis-and-repair)
for slot diagnosis. A missing acknowledgement leaves the host uncertain whether a
command executed; it does not establish that the save failed.

## Practical Extension Points

For future work, the most natural insertion points are:
- **new capture/diagnostic counters** -> `PendulumCapture` / `SwingAssembler`
- **new PPS validation logic** -> `PpsValidator`
- **new discipline / holdover behavior** -> `FreqDiscipliner` and `DisciplinedTime`
- **new CLI tunables** -> `TunablesRuntime` + `TunableRegistry` + `TunableCommands`
- **new boot/status records** -> `StatusTelemetry`
- **new sample fields or CSV framing changes** -> `SerialParser` and `PendulumProtocol`

That modular split is the key architectural model for the current firmware.
