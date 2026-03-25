# Implementation Guide

## Scope & Terminology

This document describes the current Nano Every firmware after the reduced-surface cleanup. The runtime now exposes one raw-cycle sample schema, one small tuning CLI, and a compact `STS` contract focused on boot/config/PPS state.

Within the swing reconstruction pipeline:
- **tick** = beam-unblocked interval between a rising edge and the next falling edge
- **tock** = following beam-unblocked interval on the return side
- **tick_block** / **tock_block** = durations for which the bob blocks the beam on each side

Those four values are assembled into `FullSwing` records on the same 32-bit TCB0 timeline used by PPS capture.

## Module Ownership Overview

### Hardware capture and event transport

- **`CaptureInit.*`** configures EVSYS plus TCB0/TCB1/TCB2.
- **`PendulumCapture.*`** owns the ISR-fed edge/PPS rings, coherent TCB0 timestamp helpers, overflow bookkeeping, and shared capture diagnostics.
- **`SwingAssembler.*`** drains captured IR edges in the main loop and converts them into `FullSwing` records.

### PPS validation and disciplined timebase

- **`PpsValidator.*`** classifies full 32-bit PPS intervals as `OK`, `GAP`, `DUP`, or `HARD_GLITCH` and seeds/reseeds the reference interval.
- **`FreqDiscipliner.*`** maintains fast/slow frequency estimates plus the `FREE_RUN`, `ACQUIRE`, `DISCIPLINED`, and `HOLDOVER` state machine.
- **`DisciplinedTime.*`** turns discipliner state into the active ticks-per-second denominator that host-side tooling should use for cycle-to-time conversion.

### Runtime control, telemetry, and persistence

- **`SerialParser.*`** owns command tokenization, `HDR` emission, raw-cycle sample emission, and generic `STS` framing.
- **`TunableRegistry.*`** is the single source of truth for tunable names, parsing, validation, EEPROM mapping, and runtime help text.
- **`TunableCommands.*`** wires `get` / `set` / `reset defaults` commands to the registry.
- **`TunablesRuntime.cpp`** stores live tunable values and normalizes dependent settings.
- **`StatusTelemetry.*`** emits retained boot/config `STS` records plus optional PPS tuning snapshots.
- **`EEPROMConfig.*`** loads/saves the active tunable schema with CRC protection.

### Top-level orchestration

- **`PendulumCore.*`** coordinates setup, invokes the capture/PPS/swing/runtime modules, packages finished swings as raw-cycle samples, and publishes results.
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
2. computes the full 32-bit interval between consecutive PPS edges
3. classifies each interval with `PpsValidator`
4. feeds accepted/anomalous observations into `FreqDiscipliner`
5. updates `DisciplinedTime` with the active denominator
6. updates exported correction metrics and `gps_status`

## PPS Runtime Behavior

### Validation stage

`PpsValidator` decides whether a new PPS interval should be trusted. It:
- learns a reference interval during startup/recovery seeding
- classifies intervals as `OK`, `GAP`, `DUP`, or `HARD_GLITCH`
- tracks health counters and ok-streaks for downstream lock logic

### Disciplining stage

`FreqDiscipliner` maintains:
- **fast** estimate for quicker acquisition behavior
- **slow** estimate for quieter long-term behavior
- **R** = frequency error metric in ppm
- **MAD residual ticks** = the jitter/quality gate used for lock/unlock decisions
- transitions between `FREE_RUN`, `ACQUIRE`, `DISCIPLINED`, and `HOLDOVER`

### Applied timebase stage

`DisciplinedTime` is the runtime authority for “how many ticks equal one second right now.” The firmware exports raw cycle counts plus frequency metadata; host-side tooling is responsible for converting those cycles into seconds or sub-second units.

## Pendulum Sample Assembly and Output

`PendulumCore::pendulumLoop()` performs the high-level runtime sequence:
1. process serial commands
2. process queued PPS captures and update disciplined time
3. process queued IR captures via `SwingAssembler`
4. pop completed `FullSwing` records
5. copy the swing fields as raw cycle counts
6. attach `f_inst_hz`, `f_hat_hz`, `gps_status`, `holdover_age_ms`, `r_ppm`, `j_ticks`, and `dropped`
7. emit the sample through `SerialParser`

### Raw-cycle sample contract

The retained sample contract is:

- `CFG,nominal_hz=<ticks/sec>,sample_tag=SMP,sample_schema=raw_cycles_hz_v2`
- `HDR,tick,tock,tick_block,tock_block,f_inst_hz,f_hat_hz,gps_status,holdover_age_ms,r_ppm,j_ticks,dropped`
- `SMP,...` rows carrying values for exactly those columns

There is no on-device compatibility schema for converted durations, environmental sensors, or alternate sample tags.

## Serial Commands and Telemetry

### Command surface

`SerialParser` recognizes only:
- `help` / `?`
- `help <command>`
- `help tunables`
- `get <param>`
- `set <param> <value>`
- `reset defaults`

There is no separate metrics/debug command surface in the reduced firmware.

### Tunable handling

Responsibilities are intentionally split:
- `SerialParser` tokenizes input and routes commands
- `TunableCommands` performs command-specific flow
- `TunableRegistry` owns the authoritative list of tunables and runtime help text
- `TunablesRuntime.cpp` holds live values and normalization helpers

### Retained STS contract

`StatusTelemetry` emits these boot-time `STS,PROGRESS_UPDATE,...` payload families:

- `rstfr`
- `build`
- `schema`
- `flags`
- three tunables snapshot lines emitted as `<param>,<value>,...` pairs:
  - line 1: `ppsFastShift`, `ppsSlowShift`, `ppsBlendLoPpm`, `ppsBlendHiPpm`, `ppsLockRppm`
  - line 2: `ppsLockMadTicks`, `ppsUnlockRppm`, `ppsUnlockMadTicks`, `ppsLockCount`, `ppsUnlockCount`
  - line 3: `ppsHoldoverMs`, `ppsStaleMs`, `ppsIsrStaleMs`, `ppsCfgReemitDelayMs`, `ppsAcquireMinMs`
- `cfg`
- `pps_cfg`
- `pps_freshness`

Optional families compiled in only when explicitly enabled:

- `TUNE_CFG`, `TUNE_WIN`, `TUNE_EVT` when `PPS_TUNING_TELEMETRY=1`
- `PPS_BASE` when `ENABLE_PPS_BASELINE_TELEMETRY=1`

### Boot record details

- `build` identifies the binary (`git`, dirty bit, UTC, board, MCU, raw toolchain clock, selected main clock source/rate, baud)
- `schema` states the current `STS` schema version, sample schema name, and EEPROM schema version
- `flags` advertises which intentional compile-time runtime modes were compiled in
- the three tunables snapshot lines summarize retained tunables as `param,value` pairs
- `cfg` publishes `nominal_hz`, the active sample row tag, and the sample schema name for host recovery
- `CFG` mirrors that sample-stream metadata as a top-level line-tagged record for host recovery
- `pps_cfg` publishes PPS validator acceptance windows and seeding thresholds
- `pps_freshness` documents the meaning of `ppsStaleMs` vs `ppsIsrStaleMs`

## Configuration and Persistence

### Compile-time defaults and supported modes

`Config.h` now contains only active defaults plus intentional supported modes:
- semantic main clock configuration (`MAIN_CLOCK_HZ`, `USE_EXTCLK_MAIN`)
- timebase selection (`USE_ARDUINO_TIMEBASE`, `DISABLE_ARDUINO_TCB3_TIMEBASE`)
- optional telemetry (`PPS_TUNING_TELEMETRY`, `ENABLE_PPS_BASELINE_TELEMETRY`)
- serial behavior (`ENABLE_PERIODIC_FLUSH`, `FLUSH_PERIOD_MS`, `LED_ACTIVITY_ENABLE`, `LED_ACTIVITY_DIV`)
- build metadata (`GIT_SHA`, `BUILD_UTC`, `BUILD_DIRTY`)

Historical compile-time leftovers for deleted coherent-now checks and unused fixed-point conversion paths have been removed.

`MAIN_CLOCK_HZ` is the firmware's semantic nominal clock contract for runtime math, validation, and emitted `nominal_hz`/`main_clock_hz` telemetry. It must match the board/toolchain `F_CPU`.

When `USE_EXTCLK_MAIN=1`, the sketch performs a one-shot boot-time handoff to the ATmega4809 `EXTCLK` input on **PA0 / Arduino D2** before serial and timer initialization. This mode is opt-in, consumes D2/PA0 as the driven clock input, requires the external clock to already be present at boot, and should not be switched dynamically after startup.

### Runtime tunables

The live tunables are only the active discipliner settings:
- `ppsFastShift`, `ppsSlowShift`
- blend thresholds
- lock/unlock thresholds and streak counts
- holdover/stale timers (`ppsStaleMs` for queued PPS sample processing freshness, `ppsIsrStaleMs` for ISR-edge freshness)
- PPS config re-emit delay
- minimum acquire dwell

### EEPROM config path

`EEPROMConfig` serializes the tunables through the registry, stores schema-versioned records in alternating slots, validates them with CRC, and only accepts the current schema version. Older layouts fall back to compiled defaults until a fresh `set` or `reset defaults` rewrites EEPROM.

## Practical Extension Points

For future work, the most natural insertion points are:
- **new capture/diagnostic counters** -> `PendulumCapture` / `SwingAssembler`
- **new PPS validation logic** -> `PpsValidator`
- **new discipline / holdover behavior** -> `FreqDiscipliner` and `DisciplinedTime`
- **new CLI tunables** -> `TunablesRuntime` + `TunableRegistry` + `TunableCommands`
- **new boot/status records** -> `StatusTelemetry`
- **new sample fields or CSV framing changes** -> `SerialParser` and `PendulumProtocol`

That modular split is the key architectural model for the current firmware.
