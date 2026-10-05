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

### `ENABLE_EXTCLK_HANDOFF_DIAG_STS` (default: `ENABLE_DIAGNOSTIC_TELEMETRY`, currently `1`)
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

### `ENABLE_DIAGNOSTIC_TELEMETRY` (default: `1`)
Enables the optional boot diagnostic replay and supplies defaults for individual
diagnostic controls. Required protocol/schema replay remains available when this
is `0`. Individual overrides are still subject to their enclosing call-site gates.

### `ENABLE_ENV_SENSORS`, `ENABLE_ENV_SHT4X`, `ENABLE_ENV_BMP280` (defaults: `0`)
Reserved build flags for environmental sensor integration; no sensor acquisition
is implemented in the current Nano source. Each accepts only `0` or `1`.
`ENABLE_ENV_SENSORS=1` and `ENABLE_DIAGNOSTIC_TELEMETRY=1` are rejected together
by the compile-time flash-budget policy.

### `ENABLE_PROFILING` (default: `0`)
Performance/telemetry profile selector:
- `1`: diagnostics-first profile (richer optional telemetry defaults)
- `0`: lower-overhead profile (leaner optional telemetry defaults)

### `DUAL_PPS_PROFILING` (default: `1`)
Accepts only `0` or `1`. Dual-path capture snapshots and `DUAL_PPS_EDGE` matching
are compiled only when both this flag and `ENABLE_PROFILING` are `1`.
It does not change EVSYS routing: PB0 still feeds TCB1 and PD0 feeds TCB2.
Meaningful same-edge comparisons require the same PPS signal on both inputs.

### `ENABLE_TCB_LATENCY_DIAG` (default: `0`)
Defaults to `1` only when both `ENABLE_DIAGNOSTIC_TELEMETRY` and
`ENABLE_PROFILING` are `1`. Enables capture latency traces and foreground summaries.

Related controls:

| Define                               | Default  | Behavior                                                                                       |
| ------------------------------------ | -------- | ---------------------------------------------------------------------------------------------- |
| `ENABLE_TCB_LATENCY_TRACE_ALL`       | `0`      | Trace every capture when latency diagnostics are enabled; otherwise trace threshold crossings. |
| `TCB_LATENCY_SPIKE_THRESHOLD_CYCLES` | `160U`   | Latencies at or above this value are spikes; allowed range `1..65535`.                         |
| `TCB_LATENCY_SUMMARY_PERIOD_MS`      | `5000UL` | Foreground latency-summary cadence.                                                            |
| `TCB_LATENCY_TRACE_RING_SIZE`        | `16U`    | Trace ring depth; must be a nonzero power of two.                                              |

### `PPS_TUNING_TELEMETRY` (default: `0`)
Enables optional tuning telemetry record families:
- `TUNE_CFG`
- `TUNE_WIN`
- `TUNE_EVT`

Defaults to `1` only when both `ENABLE_DIAGNOSTIC_TELEMETRY` and
`ENABLE_PROFILING` are `1`; otherwise `0`.

### `PPS_TUNE_WIN_SIZE` (default: `24U`)
Window length used by PPS tuning telemetry workspace/ring statistics.

### `ENABLE_PPS_BASELINE_TELEMETRY` (default: `0`)
Enables optional compact PPS-only baseline telemetry (`PPS_BASE`).
Defaults to `1` only when both diagnostic telemetry and profiling are enabled.

### `ENABLE_CLOCK_DIAG_STS` (default: `ENABLE_DIAGNOSTIC_TELEMETRY`, currently `1`)
Enables optional boot clock-diagnostic `STS` records.

### `ENABLE_RESTART_BREADCRUMBS` (default: `ENABLE_DIAGNOSTIC_TELEMETRY`, currently `1`)
Enables retained restart breadcrumb capture/formatting used for previous-boot health snapshots.
- `1`: keep retained previous-boot breadcrumbs (`PREV_BOOT` path) and runtime heartbeat/flag updates enabled
- `0`: disable retained restart breadcrumb logic; API remains available but resolves to no-op stubs and reports no retained bytes

Allowed values are only `0` or `1`.

### `ENABLE_MEMORY_LOW_WATER_WARN_STS` (default: `ENABLE_DIAGNOSTIC_TELEMETRY`, currently `1`)
Enables one-time low-SRAM warning telemetry (`mem_warn`) when free SRAM reaches or falls below the threshold.
The sampling/emission path also requires `ENABLE_MEMORY_TELEMETRY_STS=1`.

### `ENABLE_MEMORY_TELEMETRY_STS` (default: `0`)
Enables periodic and boot memory telemetry (`mem`) and low-water tracking.

Defaults to `1` only when both diagnostic telemetry and profiling are enabled.

### `SAMPLE_DIAGNOSTIC_DETAIL` (default: `2` when profiling on / `1` when profiling off)
Retained configuration flag, validated as `1` (`reduced`) or `2` (`full`).
The current capture and serialization paths do not reference it, so changing it
does not change emitted fields or zero provenance values.

### `MEMORY_LOW_WATER_WARN_BYTES` (default: `256U`)
Low-SRAM threshold (bytes) used by `ENABLE_MEMORY_LOW_WATER_WARN_STS`.

### `MEMORY_TELEMETRY_PERIOD_MS` (default: `10000UL`)
Periodic memory telemetry emission cadence in milliseconds.

Default is profile-dependent: `10000UL` when `ENABLE_PROFILING=0`, else `5000UL`.

## Serial/IO behavior

### `CLI_ALLOW_MUTATIONS` (default: `1`)
Controls mutating CLI commands:
- `1`: `set`, `reset defaults`, and `repair eeprom` are enabled
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
The delay is measured from startup-output readiness. Any command activity or an
explicit startup replay request suppresses this automatic full replay.

### `DATA_SERIAL` / `CMD_SERIAL` (default: `Serial` / `Serial`)
Compile-time serial routing macros (declared in `SerialParser.h`):
- `DATA_SERIAL`: output stream for telemetry and sample rows
- `CMD_SERIAL`: input stream for CLI commands

The default uses the Nano's USB connector for both data and commands, including
the [laptop capture workflow](../tools/README.md). Override both macros to
`Serial1` for a UART connection on D1/TX and D0/RX. Command and data streams
should generally remain aligned unless a split-channel integration is intentional.

## Foreground scheduling / fairness

### `PPS_PROCESS_BUDGET_PER_LOOP` (default: `4U`)
Maximum queued PPS captures processed per main-loop pass.
Used to prevent PPS burst draining from starving other loop work.

### `SWING_PROCESS_BUDGET_PER_LOOP` (default: `2U`)
Maximum queued swing records processed per main-loop pass.
Used to keep loop fairness under bursty edge-capture conditions.

## Build identity define

### `FW_VERSION` (default: `"0.0.0-dev"`)
Human-facing firmware release/version string emitted as `fw` in `CFG`, mirrored
`STS cfg`, and optional `STS build`. See [Protocol_Wire_Contract.md](Protocol_Wire_Contract.md).

### `GIT_SHA` (default: `"unknown"`)
Build identity string injected into telemetry when not overridden by build tooling.

---

## Versioning semantics

- `fw` is for human release labeling and operational traceability.
- `PROTOCOL_VERSION`, `STS_SCHEMA_VERSION`, and the capture schema IDs define wire-contract compatibility and parser expectations.

## Notes

- `Config.h` also contains many `constexpr` defaults (ring sizes and runtime tunable defaults). Those are constants, not `#define` controls.
- If this guide and code ever diverge, treat `Nano.Every/src/Config.h` as canonical.
- Maintenance checklist: whenever `Config.h` changes, re-verify default values, allowed ranges, and profile-dependent formulas in this guide.
