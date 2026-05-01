# Config.h Define Reference

This file documents the compile-time `#define` controls in `Nano.Every/src/Config.h`.
The code remains the source of truth; this guide is a human-readable map.

## Clock and timebase selection

### `USE_ARDUINO_TIMEBASE` (default: `0`)
Selects which runtime wall-clock implementation `PlatformTime` uses:
- `0`: custom TCB0-derived timebase (firmware-managed)
- `1`: Arduino core `millis()` / `delay()` path

Allowed values are only `0` or `1`.

### `USE_EXTCLK_MAIN` (default: `1`)
ATmega4809/Nano Every boot-time mode switch:
- `0`: stay on normal internal/main board clock behavior
- `1`: perform one-shot boot handoff to driven `EXTCLK` on `PA0` (`D2`)

Allowed values are only `0` or `1`.

### `EXTCLK_PRESWITCH_DELAY_MS` (default: `25U`)
Deterministic early-boot delay (milliseconds) before switching to `EXTCLK`.
Used to allow external clock drivers to settle before `CLK_MAIN` handoff.

### `EXTCLK_SOSC_CLEAR_POLL_ITERATIONS` (default: `60000U`)
Bounded loop count used while polling `MCLKSTATUS.SOSC` clear after selecting `EXTCLK`.
This is a fixed-loop bound (no `millis()` dependency) to keep startup deterministic.

### `ENABLE_EXTCLK_HANDOFF_DIAG_STS` (default: `1`)
Controls whether boot clock diagnostics include the optional EXTCLK handoff snapshot fields.
Allowed values are only `0` or `1`.

### `MAIN_CLOCK_HZ` (default: `F_CPU`)
Firmware semantic nominal clock rate used by runtime math and telemetry.
A compile-time `static_assert` requires `MAIN_CLOCK_HZ == F_CPU`.

### `DISABLE_ARDUINO_TCB3_TIMEBASE` (default: `((USE_ARDUINO_TIMEBASE) ? 0 : 1)`)
Controls whether Arduino core TCB3 timebase interrupt use is disabled in custom-timebase mode.

Constraints enforced by `Config.h`:
- Value must be `0` or `1`
- It cannot be `1` when `USE_ARDUINO_TIMEBASE=1`

## Optional telemetry / diagnostics surface

### `ENABLE_PROFILING` (default: `0`)
Performance/telemetry profile selector:
- `1`: diagnostics-first profile (richer optional telemetry defaults)
- `0`: lower-overhead profile (leaner optional telemetry defaults)

### `PPS_TUNING_TELEMETRY` (default: `1`)
Enables optional tuning telemetry record families:
- `TUNE_CFG`
- `TUNE_WIN`
- `TUNE_EVT`

Default is profile-dependent: `0` when `ENABLE_PROFILING=0`, else `1`.

### `PPS_TUNE_WIN_SIZE` (default: `24U`)
Window length used by PPS tuning telemetry workspace/ring statistics.

### `ENABLE_PPS_BASELINE_TELEMETRY` (default: `1`)
Enables optional compact PPS-only baseline telemetry (`PPS_BASE`).

### `ENABLE_CLOCK_DIAG_STS` (default: `1`)
Enables optional boot clock-diagnostic `STS` records.

### `ENABLE_RESTART_BREADCRUMBS` (default: `1`)
Enables retained restart breadcrumb capture/formatting used for previous-boot health snapshots.
- `1`: keep retained previous-boot breadcrumbs (`PREV_BOOT` path) and runtime heartbeat/flag updates enabled
- `0`: disable retained restart breadcrumb logic; API remains available but resolves to no-op stubs and reports no retained bytes

Allowed values are only `0` or `1`.

### `ENABLE_MEMORY_LOW_WATER_WARN_STS` (default: `1`)
Enables one-time low-SRAM warning telemetry (`mem_warn`) when free SRAM drops below threshold.

### `ENABLE_MEMORY_TELEMETRY_STS` (default: `1`)
Enables periodic and boot memory telemetry (`mem`) and low-water tracking.

Default is profile-dependent: `0` when `ENABLE_PROFILING=0`, else `1`.

### `SAMPLE_DIAGNOSTIC_DETAIL` (default: `2` when profiling on / `1` when profiling off)
Sample emission detail policy:
- `2` (`full`): emit all per-row provenance fields
- `1` (`reduced`): preserve schema width but zero higher-cost/low-frequency provenance fields

Implementation note:
- Sample formatting must avoid `%llu`/`printf`-style 64-bit specifier dependence on AVR toolchains; use explicit integer-to-decimal rendering for `uint64_t` fields in `SMP` output paths.

### `MEMORY_LOW_WATER_WARN_BYTES` (default: `256U`)
Low-SRAM threshold (bytes) used by `ENABLE_MEMORY_LOW_WATER_WARN_STS`.

### `MEMORY_TELEMETRY_PERIOD_MS` (default: `5000UL`)
Periodic memory telemetry emission cadence in milliseconds.

Default is profile-dependent: `10000UL` when `ENABLE_PROFILING=0`, else `5000UL`.

## Serial/IO behavior

### `CLI_ALLOW_MUTATIONS` (default: `1`)
Controls mutating CLI commands:
- `1`: `set` and `reset defaults` are enabled
- `0`: command surface is read-only for mutation operations (`help`, `get`, and `emit` remain available)

### `ENABLE_PERIODIC_FLUSH` (default: `0`)
If enabled, main loop periodically flushes `DATA_SERIAL`.

### `FLUSH_PERIOD_MS` (default: `250UL`)
Flush interval in milliseconds when periodic flush is enabled.

### `ENABLE_PERIODIC_SERIAL_DIAG_STS` (default: `0`)
Enables periodic serial diagnostics summary records.

### `SERIAL_DIAG_PERIOD_MS` (default: `5000UL`)
Periodic cadence for serial diagnostics when `ENABLE_PERIODIC_SERIAL_DIAG_STS=1`.

### `LED_ACTIVITY_ENABLE` (default: `1`)
Enables onboard LED activity indication after successful serial writes.

### `LED_ACTIVITY_DIV` (default: `1`)
Power-of-two divider for LED activity toggling frequency.

### `STARTUP_SERIAL_SETTLE_MS` (default: `1200UL`)
Startup delay (ms) after setup to let serial consumers attach before startup emission.

### `STARTUP_FULL_REPLAY_RETRY_DELAY_MS` (default: `3000UL`)
One-shot backup delay before automatic full startup replay retry (`emit startup` equivalent behavior).
Used only as a bounded fallback if host tooling may have missed the initial startup burst.

### `DATA_SERIAL` / `CMD_SERIAL` (default: `Serial1` / `Serial1`)
Compile-time serial routing macros (declared in `SerialParser.h`):
- `DATA_SERIAL`: output stream for telemetry and sample rows
- `CMD_SERIAL`: input stream for CLI commands

These can be overridden for USB `Serial` debugging workflows, but command and data streams should generally remain aligned unless a split-channel integration is intentional.

## Foreground scheduling / fairness

### `PPS_PROCESS_BUDGET_PER_LOOP` (default: `4U`)
Maximum queued PPS captures processed per main-loop pass.
Used to prevent PPS burst draining from starving other loop work.

### `SWING_PROCESS_BUDGET_PER_LOOP` (default: `2U`)
Maximum queued swing records processed per main-loop pass.
Used to keep loop fairness under bursty edge-capture conditions.

## Build identity define

### `FW_VERSION` (default: `"0.0.0-dev"`)
Human-facing firmware release/version string emitted as `fw` in boot metadata (`STS build`) and configuration metadata (`STS cfg` + `CFG`).

### `GIT_SHA` (default: `"unknown"`)
Build identity string injected into telemetry when not overridden by build tooling.

---

## Versioning semantics

- `fw` is for human release labeling and operational traceability.
- `protocol_version`, `STS_SCHEMA_VERSION`, and `sample_schema` define wire-contract compatibility and parser expectations.

## Notes

- `Config.h` also contains many `constexpr` defaults (ring sizes and runtime tunable defaults). Those are constants, not `#define` controls.
- If this guide and code ever diverge, treat `Nano.Every/src/Config.h` as canonical.
- Maintenance checklist: whenever `Config.h` changes, re-verify default values, allowed ranges, and profile-dependent formulas in this guide.
