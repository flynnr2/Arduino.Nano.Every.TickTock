# Capture & Timebase Architecture

This document explains the internal free-running timebase and how IR/PPS events are projected onto it.

Primary references:
- `Nano.Every/src/CaptureInit.cpp`
- `Nano.Every/src/PendulumCapture.cpp`
- `Nano.Every/src/PlatformTime.cpp`
- `Nano.Every/src/ClockSource.cpp`

---

## 1) Why a shared internal timeline exists

The firmware needs pendulum edges and PPS edges represented in one monotonic domain for robust interval reconstruction and correction.

Architecture:
- `TCB0`: free-running counter + overflow extension (shared timeline)
- `TCB1`: IR capture timer
- `TCB2`: PPS capture timer

ISR logic maps captured local timer events back to shared `TCB0` time.

---

## 2) EVSYS and channel routing

`CaptureInit` configures EVSYS generators/users so hardware routes input edges into capture timers with minimal jitter:

- PB0 path -> TCB1 (IR)
- PD0 path -> TCB2 (PPS)

The routing uses ATmega4809 channel/port mapping behavior noted in source comments.

`DUAL_PPS_PROFILING=1` enables same-edge comparison support only together with
`ENABLE_PROFILING=1`; it does not reroute these inputs. For that diagnostic,
both inputs must receive the same PPS signal. Normal pendulum capture retains
the separate IR/PPS wiring above.

---

## 3) TCB capture configuration details

TCB1 (IR):
- capture mode
- event capture enabled
- filter enabled (`TCB_FILTER_bm`)
- edge polarity controlled for alternating edge capture flow

TCB2 (PPS):
- capture mode
- event capture enabled
- no `TCB_FILTER_bm` in active configuration

Implication:
- TCB1 and TCB2 have intentionally different front-end filtering behavior.
- Shared-timeline projection removes the configured filter delay; raw local
  capture registers still include it.

---

## 4) Projection to shared timeline

In ISR paths:
- hardware capture latches local timer edge (`cap16`)
- ISR also samples current timer state (`now`/counter context)
- firmware reconstructs/backs out edge timing onto shared 32-bit `TCB0` timeline

That shared-domain projection enables:
- consistent swing assembly
- consistent PPS interval handling
- coherent diagnostics across both paths

The ISR samples TCB0 and the capture counter with four consecutive `LDS`
instructions. A pending overflow selects a fresh **pair** of samples in the
next epoch. A fixed instruction-derived six-tick alignment places `now32` at
the capture-counter sampling instant before backdating. Re-reading only TCB0
would give the two paths different sampling separations. The reasoning and
compiled-code regression checks are in
[PPS_Timestamp_Investigation.md](PPS_Timestamp_Investigation.md).

The projection also subtracts the input filter delay derived from the same
`CaptureEvctrl.h` settings used at initialization, reset, and edge rearming:

```text
latency16 = uint16_t(CNT - CCMP)
edge32 = now32 - latency16 - captureFilterDelayTicks(EVCTRL)
```

The ATmega4808/4809 [noise canceler specification, section 21.3.3.3](https://onlinedocs.microchip.com/oxy/GUID-4E9DA219-611B-4772-B5D3-9ED908198864-en-US-16/GUID-EFF28848-6659-4E55-8871-C908410EDDCC.html)
requires four equal consecutive samples and adds four system-clock cycles.
All three timers must use `CLKDIV1` (compile-time checked), making this four
TCB0 ticks when `TCB_FILTER_bm` is set and zero otherwise. Both IR polarities
share the filter setting. The correction is in the existing reconstruction
helper, which executes in ISR context; there is no later analysis correction.

Starting with the 2026-09-21 filter-projection change, queued edges, canonical
timestamps and diagnostic `edge32`/`delta_ext` use this
corrected projection. Default IR timestamps are four ticks earlier than in
previous firmware; PPS timestamps and IR-to-IR durations are unchanged.
`CCMP`/`cap16`, `CNT`, `now32`, and `latency16` remain raw. Consequently,
`now32 - edge32 = latency16 + filter_delay` modulo 2^32. The correction handles
timestamp wrap by unsigned subtraction and precedes PPS-span selection.
It removes only the specified filter delay, not sensor or other input-path
latency. Keep the firmware build identity with recordings to distinguish old
and new semantics; the serial column layouts are unchanged. Do not apply a
second filter correction downstream or in a calibration that already includes it.

Completed swing rows are owned by the foreground loop; ISRs only publish to
the separate capture queues. The completed-row copy therefore runs with
interrupts enabled, after a short atomic queue-index snapshot. See
[PPS_Swing_Latency_Investigation.md](PPS_Swing_Latency_Investigation.md) for the
ownership proof and the measured reduction in interrupt blocking.

---

## 5) Relationship to platform wall-clock

`PlatformTime` uses either:
- custom timebase flow, or
- Arduino `millis()` flow (`USE_ARDUINO_TIMEBASE=1`)

When using custom-timebase mode, `DISABLE_ARDUINO_TCB3_TIMEBASE` can disable Arduino core TCB3 timebase ISR usage.

This keeps runtime timing ownership coherent with the firmware’s custom capture architecture.

---

## 6) Clock source variants

Internal clock path:
- standard board internal/main clock behavior

External main clock path (`USE_EXTCLK_MAIN=1`):
- boot-time-only handoff to `EXTCLK` on D2/PA0
- external source must already be present before boot
- source frequency must match `F_CPU` and `MAIN_CLOCK_HZ`

Switching is not intended dynamically after startup.

---

## 7) Troubleshooting checklist

- Missing/unstable PPS:
  - verify physical PPS edge and polarity
  - verify EVSYS routing and TCB2 capture enable
  - inspect stale-PPS tunables and `gps_status`

- IR edge anomalies:
  - verify sensor polarity assumptions
  - verify TCB1 filter/edge expectations for your sensor path

- External clock boot failures:
  - confirm driven clock is present before reset
  - confirm frequency match to build contract
  - fallback by rebuilding with `USE_EXTCLK_MAIN=0` if required
