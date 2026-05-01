# Arduino Pendulum Timer (Nano Every)

High-precision pendulum timing on a single **Arduino Nano Every (ATmega4809)**, disciplined by GPS PPS.
The firmware captures IR beam edges and PPS pulses on one shared timer base, reconstructs full swings in the main loop, and emits a reduced serial surface consisting of raw-cycle samples plus compact `STS` telemetry.

---

## Architecture

- **TCB0** - 16-bit free-running base timer with a software-maintained high word.
- **TCB1** - IR capture via EVSYS. Alternating rising/falling edges become pendulum edge events.
- **TCB2** - GPS PPS capture via EVSYS. Each pulse is projected back onto the TCB0 timeline.
- **EVSYS** - hardware routing for low-jitter capture:
  - **PB0 -> TCB1 CAPT** (IR)
  - **PD0 -> TCB2 CAPT** (PPS)

The ISRs stay intentionally small: they timestamp captures, maintain counters, and push events into rings. Swing assembly, PPS validation/disciplining, command handling, EEPROM persistence, and serial output all run in the main loop.

---

## Hardware & Wiring

- **IR beam sensor** -> **PB0**
- **GPS PPS** -> **PD0**
- Optional: **EXTCLK** -> PA0
- IR beam sensor: OPB912W55Z (requires ~180 Ω resistor on anode (red), pull-up not required)
- GPS module: Adafruit Ultimate GPS Breakout (MTK3339-class PPS source)
- Nano Every build frequency set by `F_CPU` / `MAIN_CLOCK_HZ` (default Nano Every build: 16 MHz)
- Pull-ups / signal conditioning as required by the attached sensors

---

## Codebase Layout

```text
Nano.Every/
  Nano.Every.ino         -> tiny sketch wrapper that calls pendulumSetup()/pendulumLoop()
  src/
    CaptureInit.*        -> EVSYS + TCB0/TCB1/TCB2 hardware setup
    PendulumCapture.*    -> ISR-owned capture rings, coherent TCB0 clock reads, shared counters
    SwingAssembler.*     -> main-loop reconstruction of EdgeEvent streams into FullSwing records
    PpsValidator.*       -> PPS interval classification and startup/recovery reference seeding
    FreqDiscipliner.*    -> fast/slow estimates plus FREE_RUN/ACQUIRE/DISCIPLINED/HOLDOVER state
    DisciplinedTime.*    -> active ticks-per-second estimate used by host-side conversion
    PendulumCore.*       -> top-level orchestration and sample packaging
    SerialParser.*       -> command parsing plus HDR_PART/sample/STS emission
    TunableRegistry.*    -> authoritative tunable metadata, parsing, validation, and EEPROM mapping
    TunableCommands.*    -> get/set/reset handlers built on the tunable registry
    TunablesRuntime.cpp  -> live tunable storage plus normalization helpers
    StatusTelemetry.*    -> retained boot/config STS records
    EEPROMConfig.*       -> EEPROM load/save for the active PPS tunable schema
    PlatformTime.*       -> millis()/delay() wrapper and optional TCB3 shutdown in custom-timebase mode
    PendulumProtocol.h   -> serial tags, status codes, tunable names, and sample schema
    Config.h             -> compile-time defaults and supported build-time modes
```

---

## Quick Start

1. Open `Nano.Every/Nano.Every.ino` in Arduino IDE or an equivalent Arduino build flow.
2. Select **Arduino Nano Every** as the board.
3. Build and flash the sketch.
4. Open the serial port at **115200 baud**.
5. On boot the firmware emits boot metadata (`STS,...` plus `CFG,...`) and a segmented sample header declaration (`HDR_PART,...` sequence).

### Raw serial capture and schema-width verification

Use raw file logging (not an interactive serial monitor) when validating long CSV rows:

```bash
python3 scripts/capture_serial_raw.py --port /dev/ttyACM0 --baud 115200 --output logs/session.raw --tee-text
```

Then verify the latest complete `HDR_PART` sequence and enforce exact `SMP` field count with a minimum consecutive run before analysis:

```bash
python3 scripts/verify_smp_schema.py logs/session.raw --min-consecutive-smp 1000
```

The verifier rejects runs where `SMP` rows are shorter (or otherwise different width) than the schema declared by the latest complete `HDR_PART` sequence.

6. Type `help` to inspect the retained CLI surface.

## Reproducible Build / Test Notes

For repeatable local builds, prefer `arduino-cli` over ad-hoc IDE state:

```bash
arduino-cli compile \
  --fqbn arduino:megaavr:nona4809 \
  --build-property compiler.cpp.extra_flags="-Os -ffunction-sections -fdata-sections -flto" \
  --build-property compiler.c.extra_flags="-Os -ffunction-sections -fdata-sections -flto" \
  --build-property compiler.ar.extra_flags="-flto" \
  --build-property compiler.elf.extra_flags="-Wl,--gc-sections -flto" \
  --build-path build/nano-every \
  Nano.Every
```

Suggested verification workflow after a firmware change:

1. Run the compile command above and archive the emitted binary plus a short boot log.
2. Run `python3 scripts/check_protocol_docs.py` to ensure protocol docs still match the wire-contract constants.
3. Verify the runtime CLI still responds as expected (`help`, representative `get`, representative `set`, and `reset defaults` when appropriate).
4. If hardware is attached, confirm the boot log contains the retained `STS` records, one `CFG` line, one complete `HDR_PART` sequence, and raw-cycle sample rows.
5. When changing the operator-facing serial contract, update this README and `Docs/IMPLEMENTATION.md` in the same change.

For the dedicated external-main-clock validation workflow, see `Docs/external_clock_test_plan.md`.

For a field-by-field guide to `CFG`/`HDR_PART`/`SMP` records and analysis interpretation, see `Docs/Pendulum_Data_Record_Guide.md`.

---

## Runtime Serial Interface

The firmware uses one serial stream for four line families:

- `CFG` - session/config metadata for host recovery
- `HDR_PART` - segmented declaration of sample columns currently emitted by the device
- `SMP` - raw-cycle sample rows matching the latest complete `HDR_PART` sequence
- `STS` - compact boot, configuration, and optional PPS telemetry

`CFG` key names are emitted from `CFG_KEY_*` constants in `PendulumProtocol.h`. Current builds default to compact keys (`pv`, `nhz`, `st`, `ss`, `asv`, `hm`, `em`, ... ) to reduce flash and wire bytes.

### Active emit mode (important)

Although both DERIVED and CANONICAL schemas are implemented, current firmware is hard-pinned to:

- `ACTIVE_EMIT_MODE = EMIT_MODE_CANONICAL` in `Nano.Every/src/PendulumProtocol.h`

Operationally this means:

- schema declarations are emitted as `SCH` rows
- swing samples are emitted as `CSW` rows
- PPS samples are emitted as `CPS` rows

`CFG` / `STS cfg` still advertise both derived and canonical metadata keys for parser compatibility and tooling introspection.

For mode semantics and switching instructions, see `Docs/Emit_Mode_Guide.md`.

### Serial routing (`Serial` vs `Serial1`)

Default routing is compile-time macro based (see `Nano.Every/src/SerialParser.h`):

- `DATA_SERIAL` defaults to `Serial1`
- `CMD_SERIAL` defaults to `Serial1`

To route debug or interactive work to USB CDC `Serial`, override these macros at build time (or in a local board-specific override header) so both firmware command input and telemetry output use the same channel expected by your host tools.

Important:

- `CMD_SERIAL` controls where command input (`help/get/set/reset/emit`) is read from.
- `DATA_SERIAL` controls where `CFG/HDR_PART/SMP` (derived mode) or `SCH/CSW/CPS` (canonical mode) plus `STS` are written.
- Splitting command/data channels is supported but easy to misconfigure; keep them unified unless there is a specific integration need.

### Retained CLI Surface

The command parser exposes the core tuning commands:

- `help` or `?` - list the available top-level commands
- `help <command>` - show usage/details for one command
- `help tunables` - list all retained tunables, current values, examples, and validation notes
- `get <param>` - print the current value of one tunable
- `set <param> <value>` - update a tunable, normalize dependent values, emit optional tuning telemetry, and save to EEPROM
- `reset defaults` - restore the firmware's compiled defaults to the live tunables, reset runtime PPS state, and rewrite EEPROM without consulting the old EEPROM contents
- `emit meta` - emit `STS` metadata snapshots plus `CFG` and the segmented header declaration (`HDR_PART`) immediately on demand
- `emit startup` - replay startup boot/config records (`STS`, `CFG`, and header declaration) without rebooting

### Retained STS Records

The reduced firmware keeps these `STS,PROGRESS_UPDATE,...` payload families:

- `rstfr` - reset-cause snapshot from `RSTCTRL.RSTFR`
- `build` - build identity (`git`, dirty bit, UTC, `fw`, board, MCU, clock, baud)
- `schema` - serial contract versions (`sts`, sample schema, EEPROM schema)
- `flags` - supported compile-time runtime modes compiled into the image
- `mem` - SRAM telemetry (`free_now`, retained `free_min`, `phase=boot|periodic`), with `free_min` tracked from continuous loop sampling and emitted at boot + periodic cadence
- `<param>=<value>` tunables snapshots emitted as three parameter-list lines:
  - line 1: `ppsFastShift`, `ppsSlowShift`, `ppsBlendLoPpm`, `ppsBlendHiPpm`, `ppsLockRppm`
  - line 2: `ppsLockMadTicks`, `ppsUnlockRppm`, `ppsUnlockMadTicks`, `ppsLockCount`, `ppsUnlockCount`
  - line 3: `ppsHoldoverMs`, `ppsStaleMs`, `ppsIsrStaleMs`, `ppsCfgReemitDelayMs`, `ppsAcquireMinMs`
- `cfg` - explicit sample-stream metadata (`protocol_version`, `nominal_hz`, `sample_tag`, `sample_schema`, `adj_semantics_version`, `hdr_mode`, `fw`)
- `pps_cfg` - PPS validator/reference thresholds
- `pps_freshness` - names the two stale-PPS freshness tunables (`ppsStaleMs`, `ppsIsrStaleMs`)

Optional families remain available behind intentional compile-time flags:

- `TUNE_CFG`, `TUNE_WIN`, `TUNE_EVT` when `PPS_TUNING_TELEMETRY=1`
- `PPS_BASE` when `ENABLE_PPS_BASELINE_TELEMETRY=1`
- `mem_warn` when `ENABLE_MEMORY_LOW_WATER_WARN_STS=1` and `free_now <= MEMORY_LOW_WATER_WARN_BYTES`

### Raw-Cycle-Only Sample Format

The Nano emits one active raw-cycle sample schema (`raw_cycles_hz_v7`).
The wire-contract source of truth is `Nano.Every/src/PendulumProtocol.h`:
- `SAMPLE_SCHEMA_ID` defines the active schema id.
- `SAMPLE_SCHEMA` defines the authoritative reconstructed sample field order.
- `ADJ_SEMANTICS_VERSION` defines adjusted-field interpretation/versioning.

| Tag family | Fields |
|------------|--------|
| `CFG`      | `protocol_version=1,nominal_hz=<ticks/sec>,sample_tag=SMP,sample_schema=raw_cycles_hz_v7,adj_semantics_version=2,hdr_mode=segmented_v1,fw=<human_version>` |
| `HDR_PART` | segmented literal columns from `SAMPLE_SCHEMA_HDR_PARTS` in `PendulumProtocol.h` |
| `SMP`      | values for the fields named by the latest complete `HDR_PART` sequence |

Parser rule of thumb: `HDR_PART` is transport/readability segmentation, while canonical `SMP` field serialization is defined by `SAMPLE_SCHEMA` / `CsvField` in `PendulumProtocol.h`. Persist `sample_schema`, `adj_semantics_version`, and `hdr_mode` with each run so host logic can apply version-correct interpretation.

Field meanings:

- `tick` / `tock` - unblocked pendulum half-swing durations in raw TCB0 cycles
- `tick_block` / `tock_block` - beam-block durations in raw TCB0 cycles
- `tick_adj` / `tock_adj` / `tick_block_adj` / `tock_block_adj` - authoritative PPS-adjusted component/sub-interval durations in nominal-clock-equivalent ticks (`nominal_hz` from `CFG`)
- `tick_total_adj_direct` / `tock_total_adj_direct` - authoritative direct PPS-adjusted full half-swing intervals (preferred period-level observables)
- `tick_total_adj_diag` / `tock_total_adj_diag` - direct-composite diagnostics bitmask (`bit0=crossed PPS`, `bit1=missing PPS scale`, `bit2=degraded fallback`, `bit3=crossed >1 PPS boundary`)
- `tick_total_f_hat_hz` / `tock_total_f_hat_hz` - row-level disciplined frequency context in ticks per second (diagnostic/context only)
- `gps_status` - `0=no PPS`, `1=acquiring`, `2=locked`, `3=holdover`
- `holdover_age_ms` - discipliner holdover age in milliseconds
- `dropped` - cumulative lost IR/PPS events due to ring overflow
- `adj_diag` - compact adjustment diagnostics bitmask (`bit0=tick crossed PPS`, `bit1=tick_block crossed`, `bit2=tock crossed`, `bit3=tock_block crossed`, `bit4=missing PPS scale`, `bit5=degraded fallback`, `bit6=crossed >1 PPS boundary`)
- `adj_comp_diag` - packed per-component degradation flags for `tick`, `tick_block`, `tock`, `tock_block` (3 bits per slot: missing/degraded/multi-boundary)
- `pps_seq_row` - PPS sequence for the row’s final interval closure
- For optional `PPS_BASE` telemetry, `ub` is the authoritative full unlock-breach bitmask; named `ua*` fields in optional `TUNE_EVT` are convenience columns and may represent a subset in older logs.

Canonical diagnostic bit tables:

### `adj_diag` (component-adjustment diagnostics)

| Bit | Mask (hex/dec) | Name | Meaning when set |
|---|---:|---|---|
| 0 | `0x01` / 1 | `ADJ_DIAG_TICK_CROSSED` | `tick` interval crossed a PPS boundary |
| 1 | `0x02` / 2 | `ADJ_DIAG_TICK_BLOCK_CROSSED` | `tick_block` interval crossed a PPS boundary |
| 2 | `0x04` / 4 | `ADJ_DIAG_TOCK_CROSSED` | `tock` interval crossed a PPS boundary |
| 3 | `0x08` / 8 | `ADJ_DIAG_TOCK_BLOCK_CROSSED` | `tock_block` interval crossed a PPS boundary |
| 4 | `0x10` / 16 | `ADJ_DIAG_MISSING_SCALE` | At least one component used missing-scale handling |
| 5 | `0x20` / 32 | `ADJ_DIAG_DEGRADED_FALLBACK` | At least one component used degraded fallback |
| 6 | `0x40` / 64 | `ADJ_DIAG_MULTI_BOUNDARY` | At least one component crossed >1 PPS boundary |
| 7 | `0x80` / 128 | *(reserved)* | Must be `0` in current schema |

### `*_total_adj_diag` (direct-total diagnostics)

| Bit | Mask (hex/dec) | Name | Meaning when set |
|---|---:|---|---|
| 0 | `0x01` / 1 | `DIRECT_ADJ_DIAG_CROSSED` | Direct total interval crossed a PPS boundary |
| 1 | `0x02` / 2 | `DIRECT_ADJ_DIAG_MISSING_SCALE` | Missing-scale handling occurred in direct total adjustment |
| 2 | `0x04` / 4 | `DIRECT_ADJ_DIAG_DEGRADED_FALLBACK` | Degraded fallback occurred in direct total adjustment |
| 3 | `0x08` / 8 | `DIRECT_ADJ_DIAG_MULTI_BOUNDARY` | Direct total interval crossed >1 PPS boundary |
| 4–7 | `0x10`..`0x80` | *(reserved)* | Must be `0` in current schema |

### `lg` / `ub` (discipliner lock/unlock masks)

Shared bit positions; interpretation differs by field:
- `lg` = passing criteria (good/pass)
- `ub` = breach criteria (bad/breach)

| Bit | Mask (hex/dec) | Metric key | `lg` (lock pass mask): set means… | `ub` (unlock breach mask): set means… |
|---|---:|---|---|---|
| 0 | `0x01` / 1 | applied error ppm | `applied_err_ppm < lock_R_threshold` | `applied_err_ppm > unlock_R_threshold` |
| 1 | `0x02` / 2 | applied MAD ticks | `applied_mad_ticks < lock_MAD_threshold` | `applied_mad_ticks > unlock_MAD_threshold` |
| 2 | `0x04` / 4 | slow error ppm | `slow_err_ppm < lock_R_threshold` | `slow_err_ppm > unlock_R_threshold` |
| 3 | `0x08` / 8 | slow MAD ticks | `slow_mad_ticks < lock_MAD_threshold` | `slow_mad_ticks > unlock_MAD_threshold` |
| 4 | `0x10` / 16 | fast/slow agreement (`r_ppm`) | `r_ppm < lock_R_threshold` | `r_ppm > unlock_R_threshold` |
| 5 | `0x20` / 32 | anomaly flag | sample is anomaly-free | anomaly present / recent anomaly flagged |
| 6–7 | `0x40`..`0x80` | *(reserved)* | Must be `0` in current schema | Must be `0` in current schema |

Example:

```text
STS,PROGRESS_UPDATE,schema,sts=4,sample=raw_cycles_hz_v7,eeprom=4,adj_semantics_version=2,hdr_mode=segmented_v1
STS,PROGRESS_UPDATE,cfg,protocol_version=1,nominal_hz=16000000,sample_tag=SMP,sample_schema=raw_cycles_hz_v7,adj_semantics_version=2,hdr_mode=segmented_v1,fw=0.0.0-dev
CFG,protocol_version=1,nominal_hz=16000000,sample_tag=SMP,sample_schema=raw_cycles_hz_v7,adj_semantics_version=2,hdr_mode=segmented_v1,fw=0.0.0-dev
HDR_PART,1,4,tick,tick_block,tock,tock_block
HDR_PART,2,4,tick_adj,tick_block_adj,tock_adj,tock_block_adj
HDR_PART,3,4,tick_total_adj_direct,tick_total_adj_diag,tock_total_adj_direct,tock_total_adj_diag
HDR_PART,4,4,tick_total_f_hat_hz,tock_total_f_hat_hz,gps_status,holdover_age_ms,dropped,adj_diag,adj_comp_diag,pps_seq_row
```

Raw fields remain capture source truth. Component `*_adj` fields are authoritative for sub-interval analysis, while `*_total_adj_direct` fields are authoritative for full half-swing/period analysis (`adj_semantics_version` declares this split); `tick_total_f_hat_hz` / `tock_total_f_hat_hz` remain row context/diagnostics.

---

## Tunables

All retained tunables are available through the runtime `get` / `set` path and persist through `EEPROMConfig`. The authoritative metadata lives in `TunableRegistry`, so use `help tunables` on the device for the runtime-generated list and treat `Nano.Every/src/TunableRegistry.cpp` plus `Nano.Every/src/Config.h` as the source of truth for names, validation, examples, and compiled defaults.

The retained tunables are the active PPS discipliner controls:

- `ppsFastShift`, `ppsSlowShift`
- `ppsBlendLoPpm`, `ppsBlendHiPpm`
- `ppsLockRppm`, `ppsLockMadTicks`, `ppsLockCount`
- `ppsUnlockRppm`, `ppsUnlockMadTicks`, `ppsUnlockCount`
- `ppsHoldoverMs`, `ppsStaleMs`, `ppsIsrStaleMs` (`ppsStaleMs` = queued/main-loop PPS sample freshness, `ppsIsrStaleMs` = ISR-edge freshness)
- `ppsCfgReemitDelayMs`, `ppsAcquireMinMs`

Example commands:

```text
get ppsFastShift
set ppsFastShift 3
set ppsSlowShift 8
set ppsLockRppm 175
set ppsLockMadTicks 600
set ppsHoldoverMs 60000
reset defaults
```

Deprecated CLI aliases `ppsLockJppm` and `ppsUnlockJppm` are still accepted as backwards-compatible synonyms for `ppsLockMadTicks` and `ppsUnlockMadTicks`, but all canonical output now uses the unit-correct MAD-ticks names.

EEPROM note: schema version 4 persists only the active PPS tunables. Older saved layouts are no longer migrated; if EEPROM still contains an older record, the firmware falls back to compiled defaults until the next successful `set` or `reset defaults` rewrites schema 4.

---

## PPS Processing Summary

1. **Capture and validation**
   Each PPS edge is captured in hardware, backdated onto the TCB0 timeline, and queued by `PendulumCapture`. The main loop classifies the resulting intervals with `PpsValidator` as `OK`, `GAP`, `DUP`, or `HARD_GLITCH`.

2. **Frequency disciplining**
   Accepted PPS samples feed `FreqDiscipliner`, which maintains fast/slow estimates plus the `FREE_RUN`, `ACQUIRE`, `DISCIPLINED`, and `HOLDOVER` runtime states.

3. **Active timebase selection**
   `DisciplinedTime` chooses the effective ticks-per-second value that host-side tools should use when converting raw cycle counts into time units.

4. **Swing reconstruction and reporting**
   `SwingAssembler` produces `FullSwing` records, and `PendulumCore` emits each sample as raw cycle counts plus correction/gps/drop metadata.

For detailed EWMA/state-machine/export-mode tuning guidance (including `ENABLE_PROFILING`, lock/unlock masks, ACQUIRE vs DISCIPLINED vs HOLDOVER behavior, and metrology grace), see `Docs/PPS_Discipliner_Guide.md`.

---

## Supported Compile-Time Modes

The supported compile-time flags are:

For a define-by-define reference (defaults, constraints, and behavior), see `Docs/Config_Defines_Guide.md`.

- `MAIN_CLOCK_HZ` - semantic firmware main clock rate used by runtime math and telemetry; must match `F_CPU`
- `USE_EXTCLK_MAIN` - on ATmega4809/Nano Every, perform a one-shot boot-time handoff to driven `EXTCLK` on **D2 / PA0**
- `USE_ARDUINO_TIMEBASE` - switch `PlatformTime` to Arduino `millis()` / `delay()` instead of the TCB0-derived clock
- `DISABLE_ARDUINO_TCB3_TIMEBASE` - disable Arduino's TCB3 interrupt source when running the custom TCB0 timebase
- `PPS_TUNING_TELEMETRY` - enable optional `TUNE_*` STS telemetry
- `ENABLE_PPS_BASELINE_TELEMETRY` - enable optional `PPS_BASE` telemetry
- `ENABLE_MEMORY_TELEMETRY_STS` - enable `mem` STS telemetry emission and SRAM low-water tracking
- `MEMORY_TELEMETRY_PERIOD_MS` - cadence for periodic `mem` STS telemetry emission (when `ENABLE_MEMORY_TELEMETRY_STS=1`)
- `ENABLE_MEMORY_LOW_WATER_WARN_STS` / `MEMORY_LOW_WATER_WARN_BYTES` - optional one-time `mem_warn` low-SRAM warning controls
- `ENABLE_PERIODIC_FLUSH` / `FLUSH_PERIOD_MS` - enable and tune periodic serial flushing
- `LED_ACTIVITY_ENABLE` / `LED_ACTIVITY_DIV` - toggle the onboard LED after successful serial writes
- `FW_VERSION` - human-oriented firmware release/version string emitted as `fw` metadata in `STS build` and `STS/CFG cfg`

Versioning semantics:
- `fw` is a human release identifier for operators/tooling logs
- `protocol_version`, `STS_SCHEMA_VERSION`, and `sample_schema` remain wire-contract compatibility identifiers
- `GIT_SHA`, `BUILD_UTC`, `BUILD_DIRTY` - build metadata injected into boot telemetry

### External main clock (`USE_EXTCLK_MAIN`)

- **On by default in current `Config.h` (`USE_EXTCLK_MAIN=1`).**
- To retain internal-clock behavior for a given build, set `USE_EXTCLK_MAIN=0`.
- When enabled, the firmware performs a **boot-time-only** handoff to `EXTCLK` before serial/timer initialization.
- The external source must be a **driven digital clock**, already present on **D2 / PA0** before boot.
- D2 / PA0 is therefore **not available as GPIO** in this mode.
- `MAIN_CLOCK_HZ` must match `F_CPU`, and the **physical external clock frequency must also match them**.
- If a valid external clock is not present when selected, recovery may require reset/reflash.
- Nano Every / ATmega4809 builds should use **Registers Emulation = None (ATMEGA4809)** when building in the Arduino IDE.

---

## Accuracy & Units

- **Raw cycle counts** = raw TCB0 ticks at the configured `MAIN_CLOCK_HZ`
- **Host-side converted values** = raw cycle counts scaled by the appropriate disciplined ticks-per-second estimate after capture
- `tick_total_f_hat_hz` / `tock_total_f_hat_hz` are integer ticks-per-second estimates suitable for `raw_ticks / applied_hz`
- `r_ppm` and `j_ticks` are no longer emitted in the `raw_cycles_hz_v7` derived schema

## Estimated long-term timing accuracy

1. **Base resolution**
   `T_tick = 1 / MAIN_CLOCK_HZ` (for the default 16 MHz Nano Every build this is `62.5 ns`)
2. **Noise per PPS measurement**
   Quantization plus GPS PPS jitter yields about `26.9 ns` RMS combined measurement noise in the default 16 MHz build; scale the quantization term accordingly if `MAIN_CLOCK_HZ` changes.
3. **Noise after slow smoothing**
   With the default slow shift (`ppsSlowShift = 8`), the slow estimator heavily suppresses one-second PPS noise.
4. **Long-term implication**
   Under continuous PPS lock, pendulum timing should stay in the low-microsecond-per-hour class, while holdover/free-run behavior is bounded by the local oscillator and current discipliner state.

---

## Build Notes

- `CaptureInit.cpp` documents the EVSYS routing quirks on the ATmega4809.
- Ring sizes are power-of-two and currently:
  - swing rows: `8`
  - IR edge ring: `32`
  - PPS ring: `8`
  - PPS scale ring: `8`
- CSV lines are currently capped at `384` bytes (`CSV_LINE_MAX`).
- In the default custom-timebase configuration, `PlatformTime.cpp` disables Arduino's TCB3 timebase interrupt so the firmware relies on the coherent TCB0-derived clock instead.
- `StatusTelemetry` emits both the raw toolchain `f_cpu` and the semantic firmware `main_clock_hz`; downstream tools should treat `main_clock_hz` / `nominal_hz` as the runtime contract.

Additional subsystem documentation:

- `Docs/Emit_Mode_Guide.md` - CANONICAL vs DERIVED contract and switching.
- `Docs/PPS_Discipliner_Guide.md` - EWMA model, lock/unlock, and tuning workflow.
- `Docs/Capture_Timebase_Architecture.md` - EVSYS/TCB design and shared timeline projection.
- `Docs/adr/0001-memory-and-telemetry-budget.md` - memory/telemetry architectural tradeoffs.

---

## License

MIT. See `LICENSE`.
