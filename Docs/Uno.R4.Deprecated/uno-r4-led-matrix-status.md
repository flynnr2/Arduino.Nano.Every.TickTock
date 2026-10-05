# Uno R4 12x8 Status Matrix Behavior

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

> This page describes the **12x8 built-in LED matrix**. The separate external OLED is documented in `oled-display.md`.

## 1) Current 12x8 layout (latest)

The matrix is now used as a **6-slot status panel** (not the older 4-quadrant view).
Each slot is a **2x2 logical block**.

- **Nano** slot origin: `(1,1)`
- **SD** slot origin: `(5,1)`
- **Backlog** slot origin: `(9,1)`
- **PPS** slot origin: `(1,5)`
- **WiFi** slot origin: `(5,5)`
- **Upload** slot origin: `(9,5)`

This places three status blocks across the top row and three across the bottom row. The Upload slot is currently disabled in runtime snapshots; its block and glyph still appear in the startup test.

## 2) Startup sequence

`statusDisplayBegin()` initializes the matrix and schedules the orientation test (enabled by default). `statusDisplayService()` advances it one frame per deadline through `serviceStartupOrientationTest()`, without blocking ingestion. Active fault overrides postpone animation, and ingestion pressure may delay frames.

Default timing: each step holds for **750 ms** (`LED_MATRIX_STARTUP_TEST_STEP_MS`).

Sequence:

1. Show Nano slot.
2. Show SD slot.
3. Show Backlog slot.
4. Show PPS slot.
5. Show WiFi slot.
6. Show Upload slot.
7. Show all six slots together.
8. Show letter glyphs one-by-one: `S`, `N`, `B`, `P`, `W`, `U`.
9. Clear display.

If startup test macros are disabled, this sequence is skipped.

## 3) Per-slot state rendering

Each slot maps health state to a pattern:

- **Good:** solid 2x2 block (always on).
- **Degraded:** 2x2 block blinks **slowly** (`750 ms` phase toggle).
- **Fault:** 2x2 block blinks **fast** (`250 ms` phase toggle).
- **Disabled:** nothing shown for that slot.

## 4) Fault letters (fullscreen override)

When fault override is enabled (default), one fullscreen letter is shown for the selected active fault:

- **`S` = SD fault**
- **`N` = Nano fault**
- **`B` = Backlog fault**
- **`P` = PPS fault**
- **`W` = WiFi fault**

| Letter | Meaning                    | Runtime condition that causes it                                                                                                                                                                     |
| ------ | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **S**  | SD fault                   | SD health is `Missing` or `Fault`. `Mounted` is good; `Recovering` is degraded only.                                                                                                                 |
| **N**  | Nano / serial ingest fault | No latest swing/sample status, unknown age, or latest swing/sample age **≥ 10s**. Age `<5s` is good; `5–10s` is degraded.                                                                            |
| **B**  | Backlog fault              | Backlog pressure persists long enough: queue depth **≥6**, Serial1 bytes **≥48**, or partial-line stale **≥1s**, asserted after **≥3s** pressure; clears only after stable low-pressure for **≥5s**. |
| **P**  | PPS/GPS fault              | No latest PPS status, unknown PPS age, PPS age **≥5s**, or GPS status `NO_PPS`. `LOCKED` is good. Fresh `ACQUIRING`/`HOLDOVER` is degraded.                                                          |
| **W**  | WiFi fault                 | WiFi is neither HTTP-ready STA nor AP-running, and not in transition/hold states (`TryingSTA`, `TryingAP`, `Backoff`, `APHold`, `STAQuietHold`).                                                     |

### Fault selection order and cycling

Priority order:

1. `S` (SD)
2. `N` (Nano)
3. `B` (Backlog)
4. `P` (PPS)
5. `W` (WiFi)

By default, multiple active faults cycle every **1500 ms** (`LED_FAULT_CODE_HOLD_MS`).

A recently-cleared fault can remain visible during a linger window (`LED_FAULT_CLEAR_LINGER_MS`, default **3000 ms**).

## 5) Heartbeat pixel

When new rows are logged, heartbeat advances and is rendered on the bottom row (`y=7`) to indicate logging activity.

## 6) Backlog-critical draw skip

If `LED_SKIP_WHEN_BACKLOG_CRITICAL == 1` (default) and backlog percentage is `>= LED_BACKLOG_CRITICAL_PCT` (default `80`), `statusDisplayService()` returns early and skips drawing.

This is intentional to protect ingest/logging performance under critical backlog pressure.

## 7) Default compile-time knobs affecting behavior

- `ENABLE_LED_MATRIX_STATUS = 1`
- `LED_STATUS_UPDATE_MS = 250`
- `LED_MATRIX_FLIP_X = 0`
- `LED_MATRIX_FLIP_Y = 0`
- `LED_MATRIX_STARTUP_TEST_ENABLED = 1`
- `LED_MATRIX_STARTUP_TEST_STEP_MS = 750`
- `LED_MATRIX_STARTUP_TEST_GLYPHS = 1`
- `LED_FAULT_OVERRIDE_ENABLED = 1`
- `LED_FAULT_CYCLE_ACTIVE = 1`
- `LED_FAULT_SHOW_ONLY_HIGHEST_PRIORITY = 0`
- `LED_FAULT_CODE_HOLD_MS = 1500`
- `LED_FAULT_CLEAR_LINGER_MS = 3000`
- `LED_HEARTBEAT_ENABLED = 1`
- `LED_SKIP_WHEN_BACKLOG_CRITICAL = 1`
- `LED_BACKLOG_CRITICAL_PCT = 80`
