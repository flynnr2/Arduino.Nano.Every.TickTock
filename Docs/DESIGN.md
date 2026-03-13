# Design Notes

This document consolidates the universal strategy, disturbance references, and measurement guidance that were previously duplicated in TODO.md and FunctionalityComparison.md.

## Universal Pendulum Timer Strategy

Design goal: a timer that can handle **any** pendulum/drive type, blending a quiet short-term reference (pendulum) with a long-term stable reference (GPS PPS).

### High-Level Architecture

1. **Sensing & Timestamping**
   - IR/optical gate at mid-arc; capture zero-crossings (both directions).
   - Hardware timebase: free-running counter (TCB0) disciplined by GPS PPS using a long-τ EMA so local ticks are quiet but correct long-term.
   - Record: timestamp, swing direction, peak amplitude proxy (e.g., max light change), and any event pins (e.g., remontoire/kick coil).

2. **Front-End Robust Estimator (per update window)**
   - Build period estimates from consecutive crossings.
   - Use **median-of-means (MoM)**: split K swings into M blocks, mean each block, then median across blocks.
   - Output: mean period, beat error, variance, amplitude stats, residuals.

3. **Automatic Disturbance Classifier**
   - Odd/even test → flags per-swing asymmetry.
   - Periodogram/autocorr of residuals → detects periodic kicks or gear ripple.
   - Impulse detector → outlier rate & amplitude correlation.
   - Drift slope → mainspring roll-off.
   - Beat-error origin test (see below).
   - Produces a **profile** used to select presets.

4. **GPS Blending Timescale**
   - Maintain phase/frequency estimate of pendulum vs local seconds.
   - Blend with GPS using **very low bandwidth** (τ from noise observed):
     - Constant-force → τ ≈ 1–3 h
     - Fusee → τ ≈ 30–90 min
     - Periodic/remontoire or per-swing EM → τ ≈ 2–6 h (plus notches)
     - Hipp/random → τ ≈ 3–8 h (and robust estimators)

5. **Estimator/Control Core (Selectable)**
   - Mode A: I-only frequency integrator (default).
   - Mode B: 2nd-order digital PLL for per-swing EM or explicit phase control.
   - Mode C: Compact Kalman [phase, freq, drift, amplitude] for Hipp/random or amplitude-coupled drives.
   - All modes take the same MoM output, so you can swap at runtime.

6. **Adaptive Suppression of Known Disturbances**
   - Synchronous averaging: window length = integer multiple of detected periodicities.
   - Auto-notch: track strongest deterministic periods and suppress them.
   - Odd/even de-bias: average odd/even periods when per-swing pull is detected.
   - Gating: exclude short intervals around detected tensioning/impulse events.

7. **Outputs & Diagnostics**
   - Rate (ppm, s/day), beat error, Allan-like stability over τ = 10 s, 100 s, 1000 s.
   - Disturbance report: detected periods, odd/even imbalance, outlier rate.
   - Flags: “Periodic-kick”, “Hipp-like”, “Gear-ripple”, “Mainspring drift”, “Sensor offset”.

## Presets & Filters (Auto-Selected by Classifier)

| Profile Detected                             | Windowing (K swings)         | GPS Blend τ | Filter Core     | Extra Suppression                                 |
|----------------------------------------------|------------------------------|-------------|-----------------|---------------------------------------------------|
| Constant-force / weight / good fusee         | 30–120 s MoM                 | 1–3 h       | I-only          | Optional notch at gear ripple                     |
| Mainspring drift (no fusee)                  | 60–180 s MoM                 | 2–4 h       | I-only + drift   | Linear drift removal over run                     |
| Per-swing EM asymmetry                       | 20–100 swings MoM + odd/even | 3–6 h       | 2nd-order PLL   | Odd/even pairing; cap per-update phase correction |
| Periodic kick / remontoire (30 s / 60 s)     | N×period (N=2–3) windows     | 2–6 h       | I-only          | Synchronous averaging + auto-notch                |
| Hipp / random impulses                       | 1–5 min robust MoM           | 3–8 h       | Kalman          | Amplitude tracking; gating around impulses        |

## Drive-Type Disturbance Reference

| Drive / Impulse type                                                       | Dominant disturbance you’ll see                                                                       | What to measure & windowing                                               | GPS blending / loop filter                                               | Extra tricks that help                                                                 |
|----------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|--------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| Plain mainspring (barrel), no fusee                                        | Slow torque roll-off over hours/days → slow frequency drift; some barrel periodicity (once per turn) | Average period over 30–120 s windows; track frequency + drift (aging)     | I-only loop with very long τ (hours) or 2-state Kalman (freq + drift)    | Very slow feed-forward vs barrel angle if sensed; notch gear periodicity if visible    |
| Mainspring + fusee                                                         | Flattened torque → less secular drift; residual gear ripple                                          | Shorter windows OK (10–60 s)                                              | Low-bandwidth PI/PLL or I-only; Kalman optional                          | Notch or median-of-means to suppress known gear periodicities                          |
| Weight drive (gravity)                                                     | Constant torque; gear-train ripple, escapement asymmetry, temp effects                               | Windows spanning several gear periods (30–120 s)                          | PI/PLL with very low bandwidth; I-only also works                        | Synchronous averaging or notch filters for gear ripple; temp feed-forward              |
| Electromagnetic impulse every swing (one-sided or timing-biased)           | Cycle-by-cycle phase pulling; possible AM→FM; line-frequency noise                                   | Ignore single swings; pair odd/even swings; window 20–100 swings          | 2nd-order digital PLL (milli-Hz BW) or Kalman (phase + freq states)      | Keep impulses symmetric; separate amplitude loop; cap per-window correction            |
| Periodic free-swing with 30 s kick (Synchronome-style)                     | Stepwise top-ups → small periodic phase modulation at 30 s                                           | Average over multiples of 30 s; median-of-means across 2–3 such blocks    | I-only on frequency, τ in hours; optionally subtract known periodic term | Synchronous detection at 30 s to cancel deterministic phase wiggle                     |
| Hipp toggle / GPO 36 (random-interval impulses when amplitude falls)       | State-dependent, non-uniform impulses; amplitude-frequency coupling                                  | Long windows (1–5 min) with robust estimators; track amplitude            | Kalman with states [phase, freq, amplitude]; GPS weight very low         | Close loop on amplitude separately to linearize frequency                             |
| Electro clocks that re-tension a spring periodically (E. Howard, Rempe)    | Remontoire-like sawtooth drive every N sec/min → periodic FM                                         | Average over integer multiples of tensioning period; store event phase    | PI/PLL with notch at remontoire period (and 2nd harmonic)                | Timestamp tensioning events; exclude short windows around them                         |

## Measuring Amplitude and Q

### Measuring Amplitude

| Sensor Type                       | Method                                                                                                                                     | Notes                                                                                            |
|-----------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| Optical (beam-break / reflective) | Measure time between a defined light threshold on each side of mid-arc. This “half-period arc time” can be mapped to angular displacement. | Works best if you position the beam off-center so the beam is only interrupted over a known arc. |
| Magnetic pickup                   | Use induced voltage shape to determine when bob is near coil center and when it leaves.                                                    | Requires good coil alignment; signal can be noisy.                                               |
| Hall effect sensor                | Place sensor offset from midline; detect time bob passes at both offsets.                                                                  | Calibrate distance to angle.                                                                     |
| High-speed photodiode array       | Direct measurement of position vs. time.                                                                                                   | Overkill unless you need very precise motion profiling.                                          |

**Calculation:**
1. For a simple optical gate at mid-arc:
   - Measure the time inside the beam for each swing.
   - Knowing bob speed near the center (from period), convert to distance traveled in the beam.
   - Use geometry to compute maximum angular displacement.
2. Store amplitude history alongside period data.

### Measuring Q

**Definition:**
The quality factor (Q) measures how underdamped the pendulum is — the ratio of stored energy to energy lost per cycle.

**Direct decay method (best accuracy):**
1. Let the pendulum swing freely (no drive) until amplitude decays noticeably.
2. Record amplitude per swing: A1, A2, ...
3. Fit an exponential decay:
   A_n = A_0 × exp(-n × δ)
   where δ is the logarithmic decrement.
4. Estimate Q ≈ π / δ.

**Logarithmic decrement from two amplitudes:**
- Choose A_n and A_(n+k) separated by k cycles:
  δ = (1/k) × ln(A_n / A_(n+k))
  Q ≈ π / δ.
- Larger k reduces measurement noise.

**Continuous driving method (non-intrusive, in-service):**
- If drive energy per impulse and steady-state amplitude are known:
  Q ≈ (Energy stored / Energy lost per cycle) × 2π.
- For small damping, Q is roughly proportional to stored energy divided by average drive energy.

### Why Amplitude & Q Matter for Timing

- **Amplitude–Period coupling**:
  Real pendulums exhibit “circular error” — period increases slightly with amplitude.
  This means period drift can come purely from amplitude variation.
- **Q–Stability**:
  - High Q → better short-term stability but more sensitivity to slow environmental drift.
  - Low Q → damps quickly after disturbances but more sensitive to drive irregularities.
- **Control loops**:
  - An inner amplitude control loop can keep amplitude constant.
  - Q monitoring can reveal mechanical issues like friction or dirt.

## Differentiating "Out of Beat" vs "Off-Centre Sensor"

### Core idea
- **Out of beat (mechanical):** asymmetry comes from the escapement. It is largely constant with amplitude (within small-angle limits) and doesn’t flip if you move the sensor.
- **Off-centre sensor (measurement):** asymmetry comes from where/how you detect “center.” It changes with amplitude in a predictable way and flips sign if you mirror/rotate the sensor.

### Quick Tests (with expected outcomes)

| Test                                   | How to perform                                                                 | If it’s Out of Beat                                       | If it’s Off-Centre Sensor                                  |
|----------------------------------------|---------------------------------------------------------------------------------|-----------------------------------------------------------|------------------------------------------------------------|
| Amplitude sweep                        | Vary amplitude (or log during natural decay) and plot beat error vs amplitude. | Beat error ~ constant vs amplitude.                       | Beat error scales ~ 1/amplitude (smaller as amplitude grows). |
| Sensor swap / mirror                   | Move sensor to the mirrored position or rotate the bracket 180°.                | Same sign/magnitude (within noise).                       | Sign flips and/or magnitude changes predictably.           |
| Dual-sensor symmetry                   | Use two sensors placed symmetrically; compute mid-time = average crossing times. | Beat error visible even with mid-time reference.          | Beat error vanishes when using mid-time reference.         |
| Acoustic tick correlation              | Timestamp ticks acoustically; compare tick timing around the mid-plane.         | Ticks are not equidistant about true mid-plane.           | Ticks become equidistant once you re-center the reference. |
| Free-swing baseline                    | Disengage escapement and log half-periods.                                      | Zero beat error when free; returns with escapement.       | Non-zero beat error even when free-swinging.               |
| Odd/Even pairing                       | Compare odd vs even half-periods; offset sensor slightly and repeat.            | Pattern unchanged by small sensor nudges.                 | Pattern tracks sensor offset; can be nulled by centering.  |

### Practical Implementation (universal timer)

1. **Front-end measurements**
   - Timestamp both edges of the optical gate; derive half-periods, mid-time, and an amplitude proxy.
   - (Optional) Add a second optical gate symmetrically placed; compute mid-time = (t₁ + t₂)/2 each swing.

2. **Classification logic**
   - **Amplitude test:** Fit beat-error vs amplitude. If slope ≈ 1/A → sensor offset. If flat → out of beat.
   - **Sensor flip test:** Prompt user to flip/mirror sensor; if sign flips → sensor offset.
   - **Dual-sensor check:** If single-sensor beat error ≠ 0 but dual-sensor mid-time beat error ≈ 0 → sensor offset.

3. **Auto-correction (software)**
   - Estimate the optical center offset (in time) from amplitude scaling or dual-sensor mid-time.
   - **Corrected beat error** = measured beat error − optical-offset term.
   - Report both: raw and geometry-corrected beat error.

4. **Guidance to the user**
   - If classified **out of beat**: advise pallet/crutch adjustment; show live numeric beat error and a centering bar.
   - If **sensor offset**: show “Center Sensor” wizard: nudge until entry time ≈ exit time, or until dual-sensor mid-time beat error ≈ 0.
