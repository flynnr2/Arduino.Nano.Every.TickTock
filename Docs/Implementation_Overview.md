# Implementation Overview

Status: implementation overview. This document maps firmware responsibilities and source modules. It intentionally cross-links to specialised docs rather than duplicating full schemas, command tables, or config tables.

Companion subsystem docs:
- `Docs/Memory_and_Telemetry_Budget.md` (memory/telemetry tradeoffs)
- `Docs/Capture_Timebase_Architecture.md` (EVSYS/TCB and shared-timeline projection)
- `Docs/Config_Defines_Guide.md` (src/Config.h #defines)
- `Docs/Emit_Mode_Guide.md` (CANONICAL vs DERIVED behavior)
- `Docs/Pendulum_CSV_Semantics.md` (DERIVED mode emission reference)
- `Docs/Pendulum_Data_Record_Guide.md` (Guide to emitted pendulum data fields)
- `Docs/PPS_Discipliner_Guide.md` (EWMA model, state machine, tuning workflow)

## Document status

This document is **implementation-oriented** and should be kept. It describes module ownership and pipeline behavior that are still current, but some stream-contract details below describe DERIVED (`HDR_PART`/`SMP`) output as if it were always active.

Current firmware defaults to **CANONICAL emit mode** (`SCH`/`CSW`/`CPS`) via `ACTIVE_EMIT_MODE` in `PendulumProtocol.h`; use `Docs/Emit_Mode_Guide.md` plus `PendulumProtocol.h` as the normative source for output mode and wire tags.

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

- **`SerialParser.*`** owns command tokenization, mode-aware schema/sample emission (`SCH`/`CSW`/`CPS` or `HDR_PART`/`SMP`), and generic `STS` framing.
- **`TunableRegistry.*`** is the single source of truth for tunable names, parsing, validation, EEPROM mapping, and runtime help text.
- **`TunableCommands.*`** wires `get` / `set` / `reset defaults` commands to the registry.
- **`TunablesRuntime.cpp`** stores live tunable values and normalizes dependent settings.
- **`StatusTelemetry.*`** emits retained boot/config `STS` records plus optional PPS tuning snapshots.
- **`MemoryTelemetry.*`** computes current free SRAM on AVR (`__brkval`/`__heap_start` vs stack), tracks retained runtime minimum, and emits boot/periodic `mem` STS records.
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

`DisciplinedTime` is the runtime authority for “how many ticks equal one second right now.” The firmware now exports both raw cycle counts and authoritative per-interval PPS-adjusted cycle counts (`*_adj`) in nominal 16 MHz-equivalent ticks.

## Pendulum Sample Assembly and Output

`PendulumCore::pendulumLoop()` performs the high-level runtime sequence:
1. process serial commands
2. process queued PPS captures and update disciplined time
3. process queued IR captures via `SwingAssembler`
4. pop completed `FullSwing` records
5. copy raw swing fields and per-interval PPS-adjusted `*_adj` fields
6. attach row-level context/diagnostics (`tick_total_f_hat_hz`, `tock_total_f_hat_hz`, `gps_status`, `holdover_age_ms`, `dropped`, and adjustment diagnostics)
7. emit the sample through `SerialParser`

### Raw-cycle sample contract (DERIVED mode)

When DERIVED mode is selected, the retained sample contract is:

- `CFG,protocol_version=1,nominal_hz=<ticks/sec>,sample_tag=SMP,sample_schema=raw_cycles_hz_v7,adj_semantics_version=<n>,fw=<version>`
- `HDR_PART,<part_index>,<part_count>,...` segmented schema declaration (always enabled)
- `SMP,...` rows carrying values for exactly those columns

When CANONICAL mode is selected (the current default), the stream contract is:

- `CFG,...` / `STS,...` metadata including emit-mode/schema keys
- `SCH,<tag>,<schema_id>,<csv_fields>` declarations
- `CSW,...` canonical swing rows
- `CPS,...` canonical PPS rows

Raw fields remain capture source truth. Component `*_adj` fields are authoritative PPS-aware sub-interval corrections. `*_total_adj_direct` fields are authoritative PPS-aware full half-swing corrections. `tick_total_f_hat_hz/tock_total_f_hat_hz` remains row-level context and must not be interpreted as a universal per-interval correction factor when exact adjusted fields are present.

`adj_semantics_version` is the explicit wire contract for that authority split and diagnostic-family interpretation.

Parser compatibility rule: `HDR_PART` payloads are segmented readability groups, but canonical `SMP` serialization order is `SAMPLE_SCHEMA` / `CsvField` in `PendulumProtocol.h`. Persist `sample_schema`, `adj_semantics_version`, and `hdr_mode` from `cfg`/`CFG` metadata before interpreting row values.

## Serial Commands and Telemetry

### Command surface

`SerialParser` recognizes only:
- `help` / `?`
- `help <command>`
- `help tunables`
- `get <param>`
- `set <param> <value>`
- `reset defaults`
- `emit meta`
- `emit startup`

There is no separate metrics/debug command surface in the reduced firmware.

When `CLI_ALLOW_MUTATIONS=0`, the parser remains readable but mutating commands are blocked:
- allowed: `help`, `get`, `emit`
- blocked: `set`, `reset defaults` (returns explicit invalid-param status)

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
- `mem`
- four tunables snapshot lines emitted as `<param>,<value>,...` pairs:
  - line 1: `ppsFastShift`, `ppsSlowShift`, `ppsBlendLoPpm`, `ppsBlendHiPpm`, `ppsLockRppm`
  - line 2: `ppsLockMadTicks`, `ppsUnlockRppm`, `ppsUnlockMadTicks`, `ppsLockCount`, `ppsUnlockCount`
  - line 3: `ppsHoldoverMs`, `ppsStaleMs`, `ppsIsrStaleMs`, `ppsCfgReemitDelayMs`, `ppsAcquireMinMs`
  - line 4: `ppsMetrologyGraceMs`
- `cfg`
- `pps_cfg`
- `pps_freshness`

Optional families compiled in only when explicitly enabled:

- `TUNE_CFG`, `TUNE_WIN`, `TUNE_EVT` when `PPS_TUNING_TELEMETRY=1`
- `PPS_BASE` when `ENABLE_PPS_BASELINE_TELEMETRY=1`
- `mem_warn` when `ENABLE_MEMORY_LOW_WATER_WARN_STS=1` and free SRAM crosses `MEMORY_LOW_WATER_WARN_BYTES`

### Boot record details

- `build` identifies the binary (`git`, dirty bit, UTC, board, MCU, raw toolchain clock, selected main clock source/rate, baud)
- `schema` states the current `STS` schema version, sample schema name, and EEPROM schema version
- `flags` advertises which intentional compile-time runtime modes were compiled in
- `mem` reports `free_now`, retained low-water `free_min`, and `phase` (`boot` at startup, `periodic` thereafter at `MEMORY_TELEMETRY_PERIOD_MS`); sampling updates continuously in `pendulumLoop()` to preserve a runtime watermark between emissions
- the four tunables snapshot lines summarize retained tunables as `param,value` pairs
- `schema` also publishes `adj_semantics_version` so host parsers can bind adjusted-field meaning before row parsing
- `cfg` publishes `nominal_hz`, the active sample row tag, the sample schema name, and `adj_semantics_version` for host recovery
- `CFG` mirrors that sample-stream metadata as a top-level line-tagged record for host recovery, including `adj_semantics_version`
- `pps_cfg` publishes PPS validator acceptance windows and seeding thresholds
- `pps_freshness` documents the meaning of `ppsStaleMs` vs `ppsIsrStaleMs`
- `serial_diag` (when emitted) includes both aggregate and required-only emission-drop counters (`fmt_acq_fail_required`, `required_drop`) to make best-effort vs required telemetry loss visible

### Adjusted semantics authority and bump policy

- Authority split:
  - component `*_adj` fields are authoritative for component/sub-interval analysis
  - direct-total `*_total_adj_direct` fields are authoritative for full half-swing/period analysis
- Diagnostic split:
  - `adj_diag` applies to component adjusted fields only
  - `adj_comp_diag` encodes per-component degradation (`missing/degraded/multi`) for `tick`, `tick_block`, `tock`, `tock_block`
  - `*_total_adj_diag` applies to direct-total adjusted fields only
- Versioning rule:
  - increment `adj_semantics_version` whenever authority meaning or diagnostic interpretation changes, even if all field names stay the same.

## Configuration and Persistence

### Compile-time defaults and supported modes

`Config.h` now contains only active defaults plus intentional supported modes:
- semantic main clock configuration (`MAIN_CLOCK_HZ`, `USE_EXTCLK_MAIN`)
- timebase selection (`USE_ARDUINO_TIMEBASE`, `DISABLE_ARDUINO_TCB3_TIMEBASE`)
- optional telemetry (`PPS_TUNING_TELEMETRY`, `ENABLE_PPS_BASELINE_TELEMETRY`)
- memory telemetry controls (`ENABLE_MEMORY_TELEMETRY_STS`, `MEMORY_TELEMETRY_PERIOD_MS`, optional `ENABLE_MEMORY_LOW_WATER_WARN_STS` / `MEMORY_LOW_WATER_WARN_BYTES`)
- serial behavior (`ENABLE_PERIODIC_FLUSH`, `FLUSH_PERIOD_MS`, `LED_ACTIVITY_ENABLE`, `LED_ACTIVITY_DIV`)
- command mutability (`CLI_ALLOW_MUTATIONS`, where `0` locks `set`/`reset defaults`)
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
