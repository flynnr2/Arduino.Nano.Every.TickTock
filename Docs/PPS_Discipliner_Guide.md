# PPS Discipliner Guide

This document covers the active discipliner model, runtime states, and practical tuning workflow.

Authoritative implementation:
- `Nano.Every/src/PpsEdgeFilter.h`
- `Nano.Every/src/PpsValidator.cpp`
- `Nano.Every/src/FreqDiscipliner.cpp`
- `Nano.Every/src/DisciplinedTime.cpp`
- `Nano.Every/src/Config.h`
- `Nano.Every/src/TunableRegistry.cpp`

---

## 1) Model overview

Raw PPS captures are retained for diagnostics. Calculations use intervals
selected by `PpsEdgeFilter`: an early extra capture is rejected without moving
the measurement candidate. For example, captures at
0 s, 0.4 s, and 1 s retain both raw intervals (0.4 s and 0.6 s), while the
calculation uses the full 1 s interval from 0 s to 1 s.

With an established reference, the existing acceptance tolerance is ±20,000
timer ticks (±1.25 ms at 16 MHz). Startup retains the broader nominal guidance
band and consistency seeding. A capture inside the acceptance window cannot be
identified as spurious by timing alone.

If a capture is later than the window, its interval is rejected and the capture
becomes a provisional recovery candidate. A subsequent acceptable one-second
interval resumes frequency estimation. A freshness timeout resets acquisition
health. Early extras do not enter the health ring or reset lock/unlock streaks.
Late/recovery observations still affect health; actual gaps and freshness
failures retain the existing invalidation and holdover behavior.
Captured edge timestamps are emitted independently of the estimator.


The discipliner maintains three frequency estimates in ticks/second:

- `f_fast`: fast EWMA tracker
- `f_slow`: slow EWMA tracker
- `f_hat`: applied/blended estimate

Conceptually, for an accepted PPS sample `n_k`, each tracker is an EWMA:

```text
f <- f + (n_k - f) / 2^shift
```

The firmware does not apply a signed shift to an integer-Hz error. Each tracker
retains a separate Q16 state `q` (frequency multiplied by 65,536). The exact
update in `FreqDiscipliner.cpp::update_frequency()` is:

```text
error = n_k * 65536 - q
step = floor(abs(error) / 2^shift)
q <- q - step if error < 0, otherwise q + step
f = floor((q + 32768) / 65536)
```

`q` starts at nominal frequency × 65,536 on reset. Fast and slow use
`ppsFastShift` and `ppsSlowShift` respectively. Both update only when PPS is
valid and the accepted sample class is `OK`. Truncating the unsigned error
magnitude makes positive and negative corrections symmetric, while retained
fractions allow small errors to accumulate. Integer-Hz public estimates round
to nearest with half-tick ties upward; rounding does not replace the Q16 state.

So:
- smaller shift => faster adaptation
- larger shift => stronger smoothing

---

## 2) Blend logic (`f_hat`)

The model compares the rounded fast and slow estimates:

- `r_ppm = floor(|f_fast - f_slow| * 1000000 / f_slow)` (zero if `f_slow` is zero).
- At or below `ppsBlendLoPpm`, use slow; at or above `ppsBlendHiPpm`, use fast.
- Between those thresholds, use a linear blend with integer arithmetic:

```text
w = floor((r_ppm - lo) * 65535 / (hi - lo))
f_hat = floor((f_slow * (65535 - w) + f_fast * w) / 65535)
```

The endpoints set `w` to 0 or 65,535. In `DISCIPLINED`, the applied estimate is
overridden by slow; `HOLDOVER` uses the saved last-good slow estimate. This is
firmware diagnostic state, not the offline phase-regression calibration used by
`canonical_metrology`. For replay requirements and limitations see the
[implementation overview](Implementation_Overview.md#disciplining-stage).

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
- DISCIPLINED with PPS still valid but a sustained unlock breach => ACQUIRE
- DISCIPLINED with invalid PPS => HOLDOVER
- ACQUIRE with invalid PPS => FREE_RUN
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

The actual lock gate is `r_ppm < ppsLockRppm`,
`mad_legacy_ticks < ppsLockMadTicks`, and no recent anomaly, followed by the
streak/dwell checks. The actual unlock gate is `r_ppm > ppsUnlockRppm`,
`mad_legacy_ticks > ppsUnlockMadTicks`, or a recent anomaly, followed by the
unlock streak check. Equality does not pass the strict lock comparisons or
breach the strict unlock comparisons.

The legacy residual history uses applied-estimate residuals during acquisition
and slow-estimate residuals while disciplined. Telemetry masks (`lg` / `ub`)
additionally expose separate slow/applied errors and MADs. They are diagnostic
context, not the exact Boolean state-transition gates; every displayed metric
need not pass for lock, and a displayed breach need not itself cause unlock.

---

## 5) Export behavior (`DisciplinedTime`)

For optional tuning telemetry, `DisciplinedTime` maps state -> export mode.
This diagnostic estimate does not calibrate capture records, and its runtime
state is only allocated when `PPS_TUNING_TELEMETRY=1`:


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

## 9) Firmware regression checks

With a host C++ compiler installed, run:

```sh
python -m pytest tests/test_pps_firmware.py
```

This compiles the production PPS processing function and timing components with
capture, serial, and wall-clock substitutes. Cases cover raw capture retention,
extra edges, repeated anomalies, startup, missing pulses, tolerance endpoints,
32-bit timestamp wrap, and stale recovery. An Arduino Nano Every build remains necessary to check
the target compiler and memory budget; these checks do not exercise hardware.

## Runtime tunable reference

These are Nano settings, persisted in the existing schema 6 slots. The expected
edge gate (20,000 ticks once seeded), health ring length (60 observations), and
validator seeding rules are fixed firmware constants, not holdover tunables.
`ppsUnlockCount` cannot override validator invalidation on a genuine gap or two
hard glitches. Early extras now bypass that validator health accounting while
remaining present in raw telemetry.

| Setting             | Default | Accepted range | Effect                                                   |
| ------------------- | ------- | -------------- | -------------------------------------------------------- |
| ppsFastShift        | 3       | 1–15           | Fast frequency EWMA shift                                |
| ppsSlowShift        | 8       | 1–15           | Slow frequency EWMA shift; must be at least fast         |
| ppsBlendLoPpm       | 50      | 0–20000        | Lower blend threshold                                    |
| ppsBlendHiPpm       | 150     | 1–20000        | Upper blend threshold; must exceed lower                 |
| ppsLockRppm         | 175     | 0–20000        | Frequency agreement required for lock                    |
| ppsUnlockRppm       | 300     | 0–20000        | Unlock threshold; must be at least lock                  |
| ppsLockMadTicks     | 600     | 0–20000        | Residual MAD required for lock                           |
| ppsUnlockMadTicks   | 900     | 0–20000        | Unlock MAD threshold; must be at least lock              |
| ppsLockCount        | 30      | 1–60           | Consecutive qualifying observations for lock             |
| ppsUnlockCount      | 5       | 1–60           | Consecutive unlock breaches                              |
| ppsHoldoverMs       | 60000   | 1–65535        | Maximum discipliner holdover, in milliseconds            |
| ppsStaleMs          | 2200    | 1–30000        | Main-loop PPS capture processing freshness, milliseconds |
| ppsIsrStaleMs       | 2200    | 1–30000        | ISR PPS capture freshness, milliseconds                  |
| ppsCfgReemitDelayMs | 2000    | 1–65535        | One-time metadata re-emission delay after boot           |
| ppsAcquireMinMs     | 60000   | 1–65535        | Minimum acquisition dwell before lock, milliseconds      |
| ppsMetrologyGraceMs | 120000  | 1–86400000     | Optional diagnostic export grace, milliseconds           |

Zero is not a timer-disable value. Lock comparisons are strict: a zero lock
threshold prevents successful lock. The 16-bit holdover and acquire timers have
a 65.535-second ceiling; extending this requires a deliberate persistence-schema
change. The firmware rejects `set ppsMetrologyGraceMs ...` when
`PPS_TUNING_TELEMETRY=0`, because the diagnostic export state does not exist in
that build. Reading the saved value is still supported. It does not control the
Pi's period estimates, holdover, or raw capture timestamps.

Validated setting changes apply to the running discipliner without resetting
its filters. `reset defaults` deliberately resets acquisition as well as saving
defaults. Use `repair eeprom`, not `reset defaults`, to restore a missing copy of
otherwise good saved settings.

## EEPROM diagnosis and repair

`a=sem,b=ok,src=B` means A has a recognizable schema and valid payload checksum
but fails current semantic validation; B supplies the settings. It is not proof
of worn EEPROM. The older command path accepted values that could not be saved
and reported success even on a skipped save; that inconsistency is now removed,
but it does not establish how this particular A record was written.

After installing this firmware, request `emit startup` and preserve its tunable
and EEPROM status records. Send `repair eeprom` once, then request `emit startup`
again. Expect `STS,OK,repair,eeprom`, `a=ok,b=ok`, and a new sequence for the
repaired copy. The repair response also emits the current slot-health record,
including on failure. The valid source slot is untouched. If verification fails, the
command reports an error and the original valid slot remains usable. Repeated
verification failures warrant investigation of supply stability or EEPROM cells;
do not erase the remaining good copy. No live repair is performed by building
or running the host tests.
