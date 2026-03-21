# Implementation Guide

## Scope & Terminology

This document explains how the current Nano Every firmware turns EVSYS-routed captures into pendulum measurements and PPS-disciplined timing. It is intentionally organized around the extracted modules now present in `Nano.Every/src/` rather than the older "everything lives in `PendulumCore.cpp` / `SerialParser.cpp`" mental model.

Within the swing reconstruction pipeline:
- **tick** = beam-unblocked interval between a rising edge and the next falling edge
- **tock** = following beam-unblocked interval on the return side
- **tick_block** / **tock_block** = durations for which the bob blocks the beam on each side

Those four values are assembled into `FullSwing` records on the same 32-bit TCB0 timeline used by PPS capture.

## Module Ownership Overview

### Hardware capture and event transport

- **`CaptureInit.*`** configures EVSYS plus TCB0/TCB1/TCB2.
- **`PendulumCapture.*`** owns the ISR-fed edge/PPS rings, coherent TCB0 timestamp helpers, overflow bookkeeping, and shared capture diagnostics behind a queue/snapshot API rather than exported ISR globals.
- **`SwingAssembler.*`** drains captured IR edges in the main loop and converts them into `FullSwing` records.

### PPS validation and disciplined timebase

- **`PpsValidator.*`** classifies full 32-bit PPS intervals (`OK`, `GAP`, `DUP`, `HARD_GLITCH`) and seeds/reseeds the reference interval.
- **`FreqDiscipliner.*`** maintains the fast/slow frequency estimates plus `FREE_RUN`, `ACQUIRE`, `DISCIPLINED`, and `HOLDOVER` state.
- **`DisciplinedTime.*`** turns discipliner state into the active ticks-per-second denominator used for pendulum unit conversion.

### Runtime control, telemetry, and persistence

- **`SerialParser.*`** owns command tokenization, help output, CSV headers, sample emission, and serial metrics reporting.
- **`TunableRegistry.*`** is the single source of truth for tunable names, parsing, validation, EEPROM mapping, and help text.
- **`TunableCommands.*`** wires `get` / `set` commands to the registry.
- **`TunablesRuntime.cpp`** stores the live tunable values and normalizes dependent settings.
- **`StatusTelemetry.*`** emits boot-time `STS` metadata, PPS config summaries, and optional tuning snapshots.
- **`EEPROMConfig.*`** loads/saves versioned tunable configs with CRC protection.

### Top-level orchestration

- **`PendulumCore.*`** coordinates setup, invokes the capture/PPS/swing/runtime modules, converts finished swings into output samples, and publishes results.
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

The TCB2 ISR mirrors the IR pattern by capturing PPS edges, projecting them onto the TCB0 timeline, and pushing compact `PpsCapture` records into the PPS ring.

In the main loop, `PendulumCore::process_pps()` then:
1. drains queued PPS captures from `PendulumCapture`
2. computes the full 32-bit interval between consecutive PPS edges
3. classifies each interval with `PpsValidator`
4. feeds valid/anomalous observations into `FreqDiscipliner`
5. asks `DisciplinedTime` to update the active ticks-per-second estimate
6. updates exported correction metrics and GPS state

This is the live runtime path; the older README/implementation language about Hampel + median3 ownership inside `PendulumCore.cpp` is no longer the best description of the code structure.

## PPS Runtime Behavior

### Validation stage

`PpsValidator` is responsible for deciding whether a new PPS interval should be trusted. It:
- learns a reference interval during startup/recovery seeding
- classifies intervals as `OK`, `GAP`, `DUP`, or `HARD_GLITCH`
- tracks health counters and ok-streaks for downstream lock logic

### Disciplining stage

`FreqDiscipliner` consumes the classified PPS stream and maintains:
- **fast** estimate for quicker acquisition behavior
- **slow** estimate for quieter long-term behavior
- **R** = frequency error metric in ppm
- **MAD residual ticks** = the live jitter/quality gate used for lock/unlock decisions
- state transitions between `FREE_RUN`, `ACQUIRE`, `DISCIPLINED`, and `HOLDOVER`

### Applied timebase stage

`DisciplinedTime` is the final runtime authority for "how many ticks equal one second right now". Pendulum conversions use its current denominator rather than manually picking between raw fast/slow estimates at every output site.

That separation is important:
- `PpsValidator` answers **"is this PPS sample plausible?"**
- `FreqDiscipliner` answers **"what should the tracked frequency/state be?"**
- `DisciplinedTime` answers **"what denominator should output conversion use right now?"**

## Pendulum Sample Assembly and Output

`PendulumCore::pendulumLoop()` performs the high-level runtime sequence:
1. process serial commands
2. process queued PPS captures and update disciplined time
3. process queued IR captures via `SwingAssembler`
4. pop completed `FullSwing` records
5. convert each field into the selected `dataUnits` mode
6. attach correction ppm fields, `gps_status`, and dropped-event counts
7. emit the sample through `SerialParser`

The output header/data contract is managed in `SerialParser`, not in the tunable or telemetry modules. When `dataUnits` changes, the next sample automatically re-emits the appropriate `HDR` line before data resumes.

## Serial Commands and Telemetry

### Command surface

`SerialParser` recognizes the top-level commands:
- `help` / `?`
- `help <command>`
- `help tunables`
- `get <param>`
- `set <param> <value>`
- `stats`

### Tunable handling

The command implementations intentionally split responsibilities:
- `SerialParser` tokenizes input and routes commands
- `TunableCommands` performs the command-specific flow
- `TunableRegistry` owns the authoritative list of tunables and their parse/apply/serialize behavior, including the runtime help text that operators see via `help tunables`
- `TunablesRuntime.cpp` holds the live values and normalization helpers

This means adding a new tunable typically involves the registry/runtime/config path, not extending a monolithic parser switch statement.

### Status telemetry

`StatusTelemetry` owns the boot-time `STS` records such as:
- build/schema/flags headers
- tunable summaries
- PPS validator configuration summaries
- optional tuning snapshots

Runtime metrics and pendulum/PPS samples still flow through `SerialParser`, but the content of structured boot/status lines is centralized in `StatusTelemetry`.

## Configuration and Persistence

### Compile-time defaults

`Config.h` remains the home for compile-time defaults, feature flags, ring sizes, and default tunable values, while `TunableRegistry` is the authoritative runtime metadata path for names/descriptions/examples.

### Runtime tunables

The live runtime tunables now fall into three broad groups:
- **active discipliner settings** such as `ppsFastShift`, `ppsSlowShift`, `ppsBlendLoPpm`, `ppsBlendHiPpm`, lock/unlock thresholds, and holdover/acquire timing
- **output control** such as `dataUnits`
- **compatibility-only fields** such as `correctionJumpThresh`, `ppsHampelWin`, `ppsHampelKx100`, and `ppsMedian3`, which remain exposed for CLI/EEPROM/STS compatibility but are currently no-op in the live discipliner path

`ppsEmaShift` also remains as a backward-compatible alias for the slow shift and is normalized to mirror `ppsSlowShift`.

### EEPROM config path

`EEPROMConfig` serializes the tunables through the registry, stores schema-versioned records in alternating slots, validates them with CRC, and applies backward-compatibility rules when loading older layouts.

## Practical Extension Points

For future work, the most natural insertion points are now:
- **new capture/diagnostic counters** -> `PendulumCapture` / `SwingAssembler`
- **new PPS validation logic** -> `PpsValidator`
- **new discipline / holdover behavior** -> `FreqDiscipliner` and `DisciplinedTime`
- **new CLI tunables** -> `TunablesRuntime` + `TunableRegistry` + `TunableCommands`
- **new boot/status records** -> `StatusTelemetry`
- **new sample fields or CSV framing changes** -> `SerialParser` and `PendulumProtocol`

That modular split is the key update from the pre-refactor documentation set.
