# Config.h Define Reference

This file documents the compile-time `#define` controls in `Nano.Every/src/Config.h`.
The code remains the source of truth; this guide is a human-readable map.

## Clock and timebase selection

### `USE_ARDUINO_TIMEBASE` (default: `0`)
Selects which runtime wall-clock implementation `PlatformTime` uses:
- `0`: custom TCB0-derived timebase (firmware-managed)
- `1`: Arduino core `millis()` / `delay()` path

Allowed values are only `0` or `1`.

### `USE_EXTCLK_MAIN` (default: `0`)
ATmega4809/Nano Every boot-time mode switch:
- `0`: stay on normal internal/main board clock behavior
- `1`: perform one-shot boot handoff to driven `EXTCLK` on `PA0` (`D2`)

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

### `PPS_TUNING_TELEMETRY` (default: `1`)
Enables optional tuning telemetry record families:
- `TUNE_CFG`
- `TUNE_WIN`
- `TUNE_EVT`

### `ENABLE_PPS_BASELINE_TELEMETRY` (default: `1`)
Enables optional compact PPS-only baseline telemetry (`PPS_BASE`).

### `ENABLE_CLOCK_DIAG_STS` (default: `1`)
Enables optional boot clock-diagnostic `STS` records.

### `ENABLE_PENDULUM_ADJ_PROVENANCE` (default: `1`)
Adds compact PPS-adjustment provenance in sample output (`pps_seq_row` in `HDR`/`SMP`).

### `ENABLE_MEMORY_LOW_WATER_WARN_STS` (default: `1`)
Enables one-time low-SRAM warning telemetry (`mem_warn`) when free SRAM drops below threshold.

### `ENABLE_MEMORY_TELEMETRY_STS` (default: `1`)
Enables periodic and boot memory telemetry (`mem`) and low-water tracking.

### `MEMORY_LOW_WATER_WARN_BYTES` (default: `256U`)
Low-SRAM threshold (bytes) used by `ENABLE_MEMORY_LOW_WATER_WARN_STS`.

### `MEMORY_TELEMETRY_PERIOD_MS` (default: `5000UL`)
Periodic memory telemetry emission cadence in milliseconds.

## Serial/IO behavior

### `ENABLE_PERIODIC_FLUSH` (default: `0`)
If enabled, main loop periodically flushes `DATA_SERIAL`.

### `FLUSH_PERIOD_MS` (default: `250UL`)
Flush interval in milliseconds when periodic flush is enabled.

### `LED_ACTIVITY_ENABLE` (default: `1`)
Enables onboard LED activity indication after successful serial writes.

### `LED_ACTIVITY_DIV` (default: `1`)
Power-of-two divider for LED activity toggling frequency.

### `STARTUP_SERIAL_SETTLE_MS` (default: `1200UL`)
Startup delay (ms) after setup to let serial consumers attach before startup emission.

## Build identity define

### `GIT_SHA` (default: `"unknown"`)
Build identity string injected into telemetry when not overridden by build tooling.

---

## Notes

- `Config.h` also contains many `constexpr` defaults (ring sizes and runtime tunable defaults). Those are constants, not `#define` controls.
- If this guide and code ever diverge, treat `Nano.Every/src/Config.h` as canonical.
