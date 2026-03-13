# Implementation Guide

## Scope & Terminology
This document complements the architecture summary in the README by explaining how the firmware turns EVSYS-routed captures into pendulum statistics and PPS-disciplined timing corrections.  It assumes familiarity with the hardware wiring and CSV output described there and focuses on how the source implements those behaviors.  If this text and code ever diverge, treat the implementation as authoritative.

Within the swing reconstruction pipeline
- **tick** is the beam-unblocked interval between the rising and next falling IR edges
- **tock** is the following unblocked interval
- **tick_block** and **tock_block** are the durations where the bob blocks the beam on each side of the swing
These four measurements are accumulated by the `process_edge_events()` state machine as it converts alternating edge types into complete `FullSwing` records on the shared 32-bit timebase maintained by TCB0.  GPS PPS timestamps share that timebase and flow through parallel buffering to keep pendulum and PPS data coherent.

## Architectural Overview
The Nano Every uses three TCB units connected through the event system:
- **TCB0** runs as the free-running reference
- **TCB1** captures IR edges on PB0
- **TCB2** captures PPS on PD0
These are configured in `evsys_init()` and the TCB initializers. The codebase reflects this split, with ISR-heavy timing logic in `PendulumCore.cpp`, setup helpers in `CaptureInit.cpp`, and the serial protocol in `SerialParser.cpp` as outlined in the README’s layout section.

## Timing Methodology
### Swing Reconstruction Pipeline
IR edges arrive via the EVSYS-fed TCB1 ISR, which timestamps each capture, toggles the expected edge polarity, and enqueues the event without heavy processing to keep latency low.  The main loop later drains those events: `process_edge_events()` walks through a five-state machine that marks the start of a swing on a rising edge, accumulates tick/tock block durations, and publishes a `FullSwing` into the swing ring buffer once both halves are complete.  This separation allows ISR-side work to stay deterministic while the main loop handles swing assembly and downstream reporting.

### PPS Capture Pipeline
The PPS ISR on TCB2 mirrors the IR path by projecting each capture back into the TCB0 timeline and pushing it into a dedicated ring buffer.  `process_pps()` consumes those timestamps, clamps raw intervals to reject obviously bad periods, and feeds them through a Hampel outlier filter, optional median-of-three, and dual EWMA smoothers (fast and slow) to balance responsiveness with noise rejection.Each iteration also updates cached quality metrics, blend weights, and the active PPS denominator so that pendulum conversions and corrections share a smoothed timebase.

## Software Design Choices
### Event System & Interrupts
Using EVSYS keeps capture latency predictable by hardware-routing PB0 and PD0 edges into TCB1/TCB2 without CPU polling.  The ISRs immediately translate captures into the TCB0 domain, enforcing a single 32-bit timeline, and their annotated cycle budgets show the emphasis on bounded runtime and minimal arithmetic inside interrupt context.  A comment block in `PendulumCore.cpp` documents why backdating timestamps inside the ISR is preferred over reading compare registers in the main loop, highlighting jitter and coherency benefits.

### Main-Loop Responsibilities
`pendulumLoop()` orchestrates the high-level workflow: it processes serial commands, updates PPS filters, reconstructs swings, converts them into the requested units, attaches correction metrics, and streams samples over the data port.  Keeping all heavy math in the main loop (instead of the ISRs) aligns with the README’s guidance and lets the firmware absorb serial and filtering work without jeopardizing capture determinism.

### Serial Communication & Telemetry
The command parser maintains a registry of help text, supports `get`/`set` for tunables, and prints a tunable catalog so operators can inspect or adjust smoothing behavior over the serial link.  Data and status lines follow the CSV schema summarized in the README, so downstream tools can rely on consistent headers and unit tags when pendulum samples are emitted.

## Calculation & Filtering Decisions
### PPS Conditioning & Blending
Raw PPS deltas are clamped to a plausible window before entering the Hampel filter, preventing runaway corrections when the GPS hiccups.  The optional median-of-three smooths any surviving spikes, then dual EWMAs track fast vs. slow behavior. A quality metric (`R` in ppm) and jitter metric (`J` in ppm) drive a Q16 blend weight that mixes the two denominators, letting the firmware emphasize the fast path when drift is high and fall back to the slow path when the PPS is quiet.  Instantaneous and blended correction factors are reported each loop for visibility into both tracks.

### GPS State Machine & Holdover
`process_pps()` also maintains a GPS state with hysteresis: it detects long gaps (holdover), counts stable pulses before declaring lock, and uses drift/jitter quality metrics during acquisition and lock tracking.  The exported `gps_status` currently reflects (`NO_PPS`, `ACQUIRING`, `LOCKED`, `HOLDOVER`) so downstream logs can distinguish true loss of PPS from a holdover interval.  These transitions back-feed the blend logic by forcing weights toward the slow path when locked and the fast path when still acquiring, stabilizing downstream timing while PPS quality changes.

### Unit Conversion & Reporting
When swings are popped from the ring, the firmware converts each duration into the active units by dividing by the blended PPS denominator (nanoseconds, microseconds, milliseconds, or raw ticks) and packages them with correction ppm values, GPS status, and drop counters before transmission.  The conversion helpers reuse `pps_delta_active`, ensuring that pendulum metrics stay consistent with the filtered PPS timeline.  The emitted CSV matches the header and tag scheme documented in the README so downstream logs can mix instantaneous and blended corrections safely.

## Configuration & Tunables
Key tunables live in `Config.h` with serial accessors in `SerialParser.cpp`, tying configuration to their algorithm stages:

- **`correctionJumpThresh`**  
  **Default:** `0.002` (fractional)  
  **Influences:** Exported in STS tunables telemetry as `jump_ppm`; retained as a configurable fractional threshold.

- **`ppsFastShift` / `ppsSlowShift`**  
  **Default:** `3` / `8`  
  **Influences:** Control the fast and slow EWMA responsiveness inside `process_pps()`; lower shifts react faster at the expense of noise.

- **`ppsHampelWin`, `ppsHampelKx100`, `ppsMedian3`**  
  **Default:** `7`, `300`, `true`  
  **Influences:** Configure the outlier filter window, MAD threshold, and optional median-of-three smoothing ahead of the EWMAs.

- **`ppsBlendLoPpm` / `ppsBlendHiPpm`**  
  **Default:** `50` / `200` ppm  
  **Influences:** Define the hysteresis band for mixing fast vs. slow PPS denominators; lower drift sticks to slow, higher drift shifts toward fast.

- **`ppsLockRppm`, `ppsLockJppm`, `ppsUnlockRppm`, `ppsUnlockJppm`, `ppsLockCount`, `ppsUnlockCount`**  
  **Default:** `250`, `80`, `500`, `160`, `5`, `3`  
  **Influences:** Gate the GPS state machine’s lock/unlock decisions using drift and jitter thresholds plus consecutive-count hysteresis.

- **`ppsHoldoverMs`**  
  **Default:** `1500` ms  
  **Influences:** Declares holdover when PPS gaps exceed this duration, preventing stale PPS from skewing denominators.

- **`dataUnits`**  
  **Default:** `raw_cycles`  
  **Influences:** Selects the output conversion mode for tick/tock metrics and the associated CSV tags.

Operators can enumerate and adjust these tunables through the `help tunables`, `get`, and `set` commands managed by the serial parser, allowing experimentation without recompilation.  Changes may be persisted using the EEPROM configuration helpers invoked during setup.

## Source Cross-Reference & Extension Points
For deeper dives, start with:

* `PendulumCore.cpp` for swing reconstruction, PPS filtering, and ISR annotations that show how timing logic stays deterministic.
* `CaptureInit.cpp` for the event system wiring that binds PB0 and PD0 edges to their TCB capture units.
* `SerialParser.cpp` for the serial command surface that exposes runtime control and tunable accessors.\
* `Config.h` for centralized defaults and compile-time tunables referenced throughout the firmware.

Together with the README’s architecture and serial interface sections, these modules outline where to add features such as alternative smoothing stages or new telemetry fields while keeping interrupts lean and leveraging the shared PPS-calibrated timeline.
