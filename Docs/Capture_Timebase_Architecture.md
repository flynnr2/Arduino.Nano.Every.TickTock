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
- Cross-path delta diagnostics must account for this asymmetry.

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
