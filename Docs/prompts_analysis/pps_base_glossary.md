# PPS_BASE field glossary

Compact glossary for the current `PPS_BASE` fields emitted by the Nano Every repo.

## Current schema

```text
PPS_BASE,q,m,d,ef,es,eh,pf,ps,pa,c,v,s,r,js,ja,lg,ub,l,cp
```

## Core timing

- `q` — monotonic sample sequence number for the `PPS_BASE` stream.
- `m` — coarse wall-clock timestamp in milliseconds (`millis()`-style).
- `d` — raw observed PPS interval in MCU ticks for one GPS second. This is the full 32-bit PPS-to-PPS interval, not the old 16-bit modulo delta.

## Residuals in ticks

These are signed tick-domain errors for the current PPS interval `d` versus each estimator:

- `ef` — residual versus the fast estimator, in ticks.
- `es` — residual versus the slow estimator, in ticks.
- `eh` — residual versus the applied/blended estimator (`f_hat`), in ticks.

Interpretation:

- positive = observed PPS interval was longer than the estimate
- negative = observed PPS interval was shorter than the estimate
- near zero = that estimator matched this second well

## Residual magnitudes in ppm

These are the absolute sizes of the same residuals, but converted to ppm against the corresponding estimator. They are **not** frequency estimates themselves.

- `pf` — absolute fast-estimator error, in ppm.
- `ps` — absolute slow-estimator error, in ppm.
- `pa` — absolute applied/blended-estimator error, in ppm.

## Quality / state

- `c` — PPS classifier code:
  - `0` = `OK`
  - `1` = `GAP`
  - `2` = `DUP`
  - `3` = `HARD_GLITCH`
- `v` — validator trust flag:
  - `1` = PPS is currently considered valid / usable by live logic
  - `0` = not yet trusted or currently invalid
- `s` — discipliner state code:
  - `0` = `FREE_RUN`
  - `1` = `ACQUIRE`
  - `2` = `DISCIPLINED`
  - `3` = `HOLDOVER`

## Agreement / noise metrics

- `r` — fast/slow estimator disagreement, in ppm.
  - This is effectively `|fast - slow| / slow`, scaled to ppm.
  - Small `r` means the fast and slow estimators agree; large `r` means they are still disagreeing / settling.
- `js` — slow-estimator residual scatter, in ticks.
  - Robust MAD-style noise metric over recent `es` residuals.
- `ja` — applied-estimator residual scatter, in ticks.
  - Robust MAD-style noise metric over recent `eh` residuals.

In practice:

- `r` is a convergence / agreement metric.
- `js` and `ja` are robustness / jitter metrics.
- `ja` is usually the more relevant one for the active estimator because it tracks the applied blend.

## Lock / unlock masks

These are compact bitmasks used by the discipliner.

### `lg` — lock pass mask

Each bit shows a criterion currently passing for lock purposes:

- bit `0` (`1`) — applied error ppm below lock threshold
- bit `1` (`2`) — applied MAD ticks below lock threshold
- bit `2` (`4`) — slow error ppm below lock threshold
- bit `3` (`8`) — slow MAD ticks below lock threshold
- bit `4` (`16`) — fast/slow disagreement `r` below lock threshold
- bit `5` (`32`) — sample is anomaly-free

So:

- `lg = 63` means all current lock gates are passing.
- lower values tell you which gate(s) are still failing.

### `ub` — unlock breach mask

This reuses some of the same bit positions, but here a set bit means an unlock-related breach is present:

- bit `0` (`1`) — applied error ppm above unlock threshold
- bit `1` (`2`) — applied MAD ticks above unlock threshold
- bit `5` (`32`) — anomaly present / recent anomaly flagged

So:

- `ub = 0` means no unlock breach is currently flagged.
- non-zero means one or more unlock conditions are being hit.

## Capture-path diagnostics

- `l` — `latency16`: ISR service latency in timer ticks.
  - This is the elapsed time from the hardware-captured PPS edge (`TCB2.CCMP`) to the later point in the ISR where `TCB2.CNT` is sampled.
  - At 16 MHz, 1 tick = 62.5 ns, so 96 ticks is about 6.0 µs.
  - This is mainly a software-service-latency diagnostic, **not** a direct PPS timestamp-error metric.
- `cp` — raw 16-bit hardware capture value for the PPS event (`cap16` / captured timer count).

## Plain-English intuition

- `d` tells you what actually happened this second.
- `ef`, `es`, `eh` tell you how wrong the fast, slow, and applied estimators were on this second.
- `pf`, `ps`, `pa` express those same errors in ppm magnitude.
- `r` tells you how far apart the fast and slow estimators still are.
- `js` and `ja` tell you how noisy the recent slow/applied residuals have been.
- `lg` tells you which lock conditions are already satisfied.
- `ub` tells you which unlock conditions are currently being breached.
- `l` tells you how late software arrived after hardware capture, not the PPS error itself.

## Change from the older glossary

The current repo no longer emits these older fields:

- `en`
- `ff`
- `fs`
- `fh`
- `j`

They have effectively been replaced or superseded by:

- `pf`, `ps`, `pa` for ppm-domain residual magnitudes
- `js`, `ja` for slow/applied MAD-style residual scatter
- `r` specifically as fast/slow disagreement in ppm
- `lg`, `ub` for compact lock/unlock diagnostics
