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
    SerialParser.*       -> command parsing plus HDR/sample/STS emission
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
5. On boot the firmware emits boot metadata (`STS,...` plus `CFG,...`) and one `HDR,...` sample header line.
6. Type `help` to inspect the retained CLI surface.

## Reproducible Build / Test Notes

For repeatable local builds, prefer `arduino-cli` over ad-hoc IDE state:

```bash
arduino-cli compile \
  --fqbn arduino:megaavr:nona4809 \
  --build-path build/nano-every \
  Nano.Every
```

Suggested verification workflow after a firmware change:

1. Run the compile command above and archive the emitted binary plus a short boot log.
2. Verify the runtime CLI still responds as expected (`help`, representative `get`, representative `set`, and `reset defaults` when appropriate).
3. If hardware is attached, confirm the boot log contains the retained `STS` records, one `CFG` line, one `HDR` line, and raw-cycle sample rows.
4. When changing the operator-facing serial contract, update this README and `Docs/IMPLEMENTATION.md` in the same change.

For the dedicated external-main-clock validation workflow, see `Docs/external_clock_test_plan.md`.

---

## Runtime Serial Interface

The firmware uses one serial stream for four line families:

- `CFG` - session/config metadata for host recovery
- `HDR` - names the sample columns currently emitted by the device
- `SMP` - raw-cycle sample rows matching the latest `HDR`
- `STS` - compact boot, configuration, and optional PPS telemetry

### Retained CLI Surface

The command parser exposes the core tuning commands:

- `help` or `?` - list the available top-level commands
- `help <command>` - show usage/details for one command
- `help tunables` - list all retained tunables, current values, examples, and validation notes
- `get <param>` - print the current value of one tunable
- `set <param> <value>` - update a tunable, normalize dependent values, emit optional tuning telemetry, and save to EEPROM
- `reset defaults` - restore the firmware's compiled defaults to the live tunables, reset runtime PPS state, and rewrite EEPROM without consulting the old EEPROM contents

### Retained STS Records

The reduced firmware keeps these `STS,PROGRESS_UPDATE,...` payload families:

- `rstfr` - reset-cause snapshot from `RSTCTRL.RSTFR`
- `build` - build identity (`git`, dirty bit, UTC, board, MCU, clock, baud)
- `schema` - serial contract versions (`sts`, sample schema, EEPROM schema)
- `flags` - supported compile-time runtime modes compiled into the image
- `<param>=<value>` tunables snapshots emitted as three parameter-list lines:
  - line 1: `ppsFastShift`, `ppsSlowShift`, `ppsBlendLoPpm`, `ppsBlendHiPpm`, `ppsLockRppm`
  - line 2: `ppsLockMadTicks`, `ppsUnlockRppm`, `ppsUnlockMadTicks`, `ppsLockCount`, `ppsUnlockCount`
  - line 3: `ppsHoldoverMs`, `ppsStaleMs`, `ppsIsrStaleMs`, `ppsCfgReemitDelayMs`, `ppsAcquireMinMs`
- `cfg` - explicit sample-stream metadata (`nominal_hz`, `sample_tag`, `sample_schema`)
- `pps_cfg` - PPS validator/reference thresholds
- `pps_freshness` - names the two stale-PPS freshness tunables (`ppsStaleMs`, `ppsIsrStaleMs`)

Optional families remain available behind intentional compile-time flags:

- `TUNE_CFG`, `TUNE_WIN`, `TUNE_EVT` when `PPS_TUNING_TELEMETRY=1`
- `PPS_BASE` when `ENABLE_PPS_BASELINE_TELEMETRY=1`

### Raw-Cycle-Only Sample Format

The Nano emits a single raw-cycle sample schema:

| Tag family | Fields |
|------------|--------|
| `CFG`      | `nominal_hz=<ticks/sec>,sample_tag=SMP,sample_schema=raw_cycles_hz_v2` |
| `HDR`      | `tick`, `tock`, `tick_block`, `tock_block`, `f_inst_hz`, `f_hat_hz`, `gps_status`, `holdover_age_ms`, `r_ppm`, `j_ticks`, `dropped` |
| `SMP`      | values for the fields named by the latest `HDR` line |

Field meanings:

- `tick` / `tock` - unblocked pendulum half-swing durations in raw TCB0 cycles
- `tick_block` / `tock_block` - beam-block durations in raw TCB0 cycles
- `f_inst_hz` - latest PPS interval estimate in ticks per second
- `f_hat_hz` - active disciplined frequency estimate in ticks per second
- `gps_status` - `0=no PPS`, `1=acquiring`, `2=locked`, `3=holdover`
- `holdover_age_ms` - discipliner holdover age in milliseconds
- `r_ppm` - fast/slow disagreement metric in ppm
- `j_ticks` - robust jitter metric as MAD residual ticks
- `dropped` - cumulative lost IR/PPS events due to ring overflow

Example:

```text
STS,PROGRESS_UPDATE,schema,sts=1,sample=raw_cycles_hz_v2,eeprom=4
STS,PROGRESS_UPDATE,cfg,nominal_hz=16000000,sample_tag=SMP,sample_schema=raw_cycles_hz_v2
CFG,nominal_hz=16000000,sample_tag=SMP,sample_schema=raw_cycles_hz_v2
HDR,tick,tock,tick_block,tock_block,f_inst_hz,f_hat_hz,gps_status,holdover_age_ms,r_ppm,j_ticks,dropped
SMP,7872371,7871904,20105,19968,15999978,15999984,2,0,3,1,0
```

The Nano does **not** convert raw cycle counts into wall-clock units on-device. Downstream tools should ingest the raw counts and use `f_hat_hz` as the disciplined denominator for corrected time conversion, e.g. `corrected_seconds = raw_ticks / f_hat_hz`.

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

---

## Supported Compile-Time Modes

The supported compile-time flags are:

- `MAIN_CLOCK_HZ` - semantic firmware main clock rate used by runtime math and telemetry; must match `F_CPU`
- `USE_EXTCLK_MAIN` - on ATmega4809/Nano Every, perform a one-shot boot-time handoff to driven `EXTCLK` on **D2 / PA0**
- `USE_ARDUINO_TIMEBASE` - switch `PlatformTime` to Arduino `millis()` / `delay()` instead of the TCB0-derived clock
- `DISABLE_ARDUINO_TCB3_TIMEBASE` - disable Arduino's TCB3 interrupt source when running the custom TCB0 timebase
- `PPS_TUNING_TELEMETRY` - enable optional `TUNE_*` STS telemetry
- `ENABLE_PPS_BASELINE_TELEMETRY` - enable optional `PPS_BASE` telemetry
- `ENABLE_PERIODIC_FLUSH` / `FLUSH_PERIOD_MS` - enable and tune periodic serial flushing
- `LED_ACTIVITY_ENABLE` / `LED_ACTIVITY_DIV` - toggle the onboard LED after successful serial writes
- `GIT_SHA`, `BUILD_UTC`, `BUILD_DIRTY` - build metadata injected into boot telemetry

### External main clock (`USE_EXTCLK_MAIN`)

- **Off by default.** Existing internal-clock behavior is unchanged unless `USE_EXTCLK_MAIN=1`.
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
- `f_inst_hz` / `f_hat_hz` are integer ticks-per-second estimates suitable for `raw_ticks / f_hat_hz`
- `r_ppm` is reported in ppm and `j_ticks` is reported in raw ticks

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
- Ring sizes are power-of-two: IR `64`, PPS `16`.
- CSV lines are capped at 256 bytes.
- In the default custom-timebase configuration, `PlatformTime.cpp` disables Arduino's TCB3 timebase interrupt so the firmware relies on the coherent TCB0-derived clock instead.
- `StatusTelemetry` emits both the raw toolchain `f_cpu` and the semantic firmware `main_clock_hz`; downstream tools should treat `main_clock_hz` / `nominal_hz` as the runtime contract.

---

## License

MIT. See `LICENSE`.
