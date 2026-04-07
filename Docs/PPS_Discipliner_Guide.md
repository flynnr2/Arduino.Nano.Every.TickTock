# PPS Discipliner Guide

This document covers the active discipliner model, runtime states, and practical tuning workflow.

Authoritative implementation:
- `Nano.Every/src/FreqDiscipliner.cpp`
- `Nano.Every/src/DisciplinedTime.cpp`
- `Nano.Every/src/Config.h`
- `Nano.Every/src/TunableRegistry.cpp`

---

## 1) Model overview

The discipliner maintains three frequency estimates in ticks/second:

- `f_fast`: fast EWMA tracker
- `f_slow`: slow EWMA tracker
- `f_hat`: applied/blended estimate

For PPS sample `n_k`:

- `err_fast = n_k - f_fast`
- `err_slow = n_k - f_slow`
- `f_fast <- f_fast + (err_fast >> ppsFastShift)`
- `f_slow <- f_slow + (err_slow >> ppsSlowShift)`

So:
- smaller shift => faster adaptation
- larger shift => stronger smoothing

---

## 2) Blend logic (`f_hat`)

The model computes:

- `r_ppm = |f_fast - f_slow| / f_slow * 1e6`

With tunables:
- `ppsBlendLoPpm`
- `ppsBlendHiPpm`

Blend behavior:
- if `r_ppm <= lo`: use slow
- if `r_ppm >= hi`: use fast
- else: linear interpolation between slow and fast

---

## 3) Runtime states

`FreqDiscipliner` state machine:

- `FREE_RUN`
- `ACQUIRE`
- `DISCIPLINED`
- `HOLDOVER`

Transitions (high level):
- valid PPS seen in FREE_RUN => ACQUIRE
- ACQUIRE success criteria met => DISCIPLINED
- DISCIPLINED with stale/invalid PPS => HOLDOVER
- HOLDOVER with valid PPS => ACQUIRE
- HOLDOVER age exceeds `ppsHoldoverMs` => FREE_RUN

---

## 4) Lock/unlock criteria

Lock entry uses:
- frequency agreement/error thresholds (`ppsLockRppm`)
- residual MAD threshold (`ppsLockMadTicks`)
- anomaly gating
- streak requirement (`ppsLockCount`)
- minimum dwell (`ppsAcquireMinMs`)

Unlock from DISCIPLINED uses:
- `ppsUnlockRppm`
- `ppsUnlockMadTicks`
- anomaly gating
- streak (`ppsUnlockCount`)

Telemetry masks (`lg` / `ub`) encode per-metric pass/breach context and should be used when diagnosing why transitions happened.

---

## 5) Export behavior (`DisciplinedTime`)

`DisciplinedTime` maps state -> export mode:

- FREE_RUN -> `NOMINAL`
- ACQUIRE -> `BLEND_TRACK` (or temporary `SLOW_GRACE`)
- DISCIPLINED -> `SLOW_TRACK`
- HOLDOVER -> `SLOW_HOLDOVER`

`SLOW_GRACE` behavior:
- on mild `DISCIPLINED -> ACQUIRE` transition with PPS still valid, the export can temporarily hold a previous slow anchor.
- grace length is tunable via `ppsMetrologyGraceMs`.

This mechanism helps reduce metrology churn during short unlock events.

---

## 6) ENABLE_PROFILING and diagnostics

`ENABLE_PROFILING` influences optional diagnostics defaults:

- when `0`: lower telemetry overhead defaults
- when `1`: richer diagnostics defaults (`TUNE_*`, `PPS_BASE`, etc., depending on build flags)

Recommended use:
- tuning campaigns: profiling-enabled build
- operational steady runs: profiling-off build unless diagnostics are required

---

## 7) Practical tuning workflow

1. Confirm stable PPS input and freshness (`ppsStaleMs`, `ppsIsrStaleMs`).
2. Start from defaults:
   - `ppsFastShift=3`
   - `ppsSlowShift=8`
   - lock/unlock thresholds from current `Config.h`.
3. Observe:
   - lock/unlock chatter
   - `lg` and `ub` masks
   - holdover entry/exit behavior
4. If too twitchy:
   - increase `ppsSlowShift`
   - widen unlock thresholds cautiously
   - increase `ppsUnlockCount`
5. If too sluggish:
   - lower `ppsFastShift` (careful: higher noise sensitivity)
   - reduce `ppsAcquireMinMs` only if justified
6. Validate in all states:
   - ACQUIRE startup
   - DISCIPLINED steady windows
   - HOLDOVER and return from HOLDOVER

---

## 8) State interpretation reminder

- ACQUIRE: converging, not yet trusted as long-term stable
- DISCIPLINED: stable PPS-referenced operation
- HOLDOVER: no valid PPS, using last known-good slow estimate
- FREE_RUN: no PPS discipline active

For downstream analysis, always persist `gps_status` and related diagnostics with interval data.
