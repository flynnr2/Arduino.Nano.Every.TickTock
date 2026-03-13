# Arduino Pendulum Timer (Nano Every)

High‑precision pendulum timing on a single **Arduino Nano Every (ATmega4809)**, disciplined by GPS.  
This little beast does IR beam edge capture (tick/tock), GPS PPS capture, maintains a 32‑bit timebase, and spits out neat CSV lines you can chew on later.  
Now upgraded with **dual‑track PPS smoothing** that outputs a single blended correction so it can react fast *and* stay chill when GPS gets noisy.

---

## Architecture

- **TCB0** – 16‑bit “free‑running” base timer (Periodic Interrupt mode, `CCMP=0xFFFF`). Overflow ISR keeps the high word.
- **TCB1** – IR sensor capture via EVSYS. Alternating rising/falling edges = tick/tock + beam‑block timing.
- **TCB2** – GPS PPS capture via EVSYS. Feeds the dual‑track smoothing logic.
- **EVSYS** – Routes pins to TCB captures:
  - **PB0 → TCB1 CAPT** (IR)
  - **PD0 → TCB2 CAPT** (PPS)  

All the heavy math happens in the main loop (`pendulumLoop()`), not in the ISRs — they just timestamp and queue events.

---

## Hardware & Wiring

- **IR beam sensor** → **PB0** (input)
- **GPS PPS** → **PD0** (input)
- GPS module: Adafruit Ultimate GPS Breakout (MTK3339, ±20 ns jitter claimed)
- Nano Every @ 16MHz (`CLKDIV1`)
- Pull‑ups and conditioning as your sensors/GPS require

---

## Codebase Layout

```
Nano.Every/
  Nano.Every.ino       → tiny wrapper for setup/loop
  src/
    CaptureInit.*      → EVSYS + TCB0/1/2 setup
    PendulumCore.*     → ISRs, rings, PPS smoothing, sample assembly
    SerialParser.*     → command parser, CSV/header output
    EEPROMConfig.*     → tunable storage + CRC
    PendulumProtocol.h → shared wire protocol (tags, fields, tunables)
    Config.h           → defaults (rings, smoothing params, thresholds)
    AtomicUtils.h      → safe shared reads
```

---

## Quick Start

1. Fire up Arduino IDE (or equivalent), select **Nano Every (ATmega4809)**.
2. Clone/untar project, open `Nano.Every.ino`.
3. Flash at 115200bps.  
4. Open Serial Monitor @ 115200.  
5. Type `help` — bask in the list of commands.
6. You’ll start seeing `HDR` and data lines tagged by units (e.g. `16Mhz`, `uSec`).

---

## Serial Line Interface

### Line Tags

- `HDR`   — CSV header/meta (sent on start & when units change)
- `16Mhz` — data sample in raw cycles
- `nSec`  — data sample in nanoseconds
- `uSec`  — data sample in microseconds
- `mSec`  — data sample in milliseconds
- `STS`   — status/diagnostic

### CSV Schema

Data lines report instantaneous and blended corrections:

| Tag                  | tick_* | tock_* | tick_block_* | tock_block_* | corr_inst_ppm | corr_blend_ppm | gps_status | dropped_events |
|----------------------|--------|--------|--------------|--------------|---------------|----------------|------------|----------------|
| HDR                  | tick_* | tock_* | tick_block_* | tock_block_* | corr_inst_ppm | corr_blend_ppm | gps_status | dropped_events |
| 16Mhz/nSec/uSec/mSec | value  | value  | value        | value        | value         | value          | value      | value          |

Unit suffix `*` depends on mode:
- `RawCycles`: `tick_cycles, tock_cycles, tick_block_cycles, tock_block_cycles`
- `AdjustedMs`: `tick_ms, ...`
- `AdjustedUs`: `tick_us, ...`
- `AdjustedNs`: `tick_ns, ...`

Notes:
- `corr_inst_ppm` = instantaneous PPS correction (ppm, int; scaled by 1,000,000)
- `corr_blend_ppm` = blended PPS correction after fast/slow smoothing
- `gps_status`: 0 = no PPS, 1 = acquiring, 2 = locked, 3 = holdover
- `dropped_events`: PPS/IR samples dropped

Example (AdjustedUs):
```
HDR,tick_us,tock_us,tick_block_us,tock_block_us,corr_inst_ppm,corr_blend_ppm,gps_status,dropped_events
uSec,492023,491994,1256,1248,-1400,-875,2,0
```

### Commands

- `help` — list commands  
- `help tunables` — list tunables  
- `get <param>` — read tunable  
- `set <param> <value>` — set tunable (auto-saved to EEPROM)  
- `stats` — buffer fill, drops, truncation  

---

## Tunables

All can be set via serial and saved to EEPROM. Defaults in `Config.h`.

| Param                  | Type   | Default    | Notes
|------------------------|--------|------------|-------------------------------------------------------------------------
| `dataUnits`            | enum   | raw_cycles | Output units: `raw_cycles`, `adjusted_ms`, `adjusted_us`, `adjusted_ns`
| `correctionJumpThresh` | float  | 0.002      | **Compatibility-only / no-op** (retained for CLI+EEPROM+status round-trip)
| `ppsFastShift`         | uint8  | 3          | Short‑term EWMA shift (lower = faster)
| `ppsSlowShift`         | uint8  | 8          | Long‑term EWMA shift (higher = smoother)
| `ppsHampelWin`         | uint8  | 7          | **Compatibility-only / no-op** (retained for CLI+EEPROM+status round-trip)
| `ppsHampelKx100`       | uint16 | 300        | **Compatibility-only / no-op** (retained for CLI+EEPROM+status round-trip)
| `ppsMedian3`           | bool   | 1          | **Compatibility-only / no-op** (retained for CLI+EEPROM+status round-trip)
| `ppsBlendLoPpm`        | uint16 | 50         | Below this drift, prefer slow smoother
| `ppsBlendHiPpm`        | uint16 | 200        | Above this drift, fully fast smoother
| `ppsLockRppm`          | uint16 | 200        | Max drift to declare lock
| `ppsLockJppm`          | uint16 | 2000       | Legacy-named MAD threshold (ticks) to declare lock
| `ppsUnlockRppm`        | uint16 | 500        | Drift to unlock
| `ppsUnlockJppm`        | uint16 | 160        | Jitter to unlock
| `ppsLockCount`         | uint8  | 30         | Consecutive good PPS required to lock
| `ppsUnlockCount`       | uint8  | 3          | Consecutive bad PPS before unlock
| `ppsHoldoverMs`        | uint16 | 60000      | PPS gap to enter holdover

Example:
```
set ppsFastShift 3
set ppsSlowShift 8
set ppsHampelWin 7
set ppsHampelKx100 300
set ppsMedian3 1
set correctionJumpThresh 0.002
```

---


## GPS PPS Processing (Live Behavior)

Implementation note: the source code is authoritative for runtime behavior; this section summarizes the currently compiled path for operators.

### Intuition: Two Questions the Firmware Must Answer

Each GPS PPS pulse should arrive exactly **1 second apart**, which corresponds to:

    16,000,000 timer ticks at 16 MHz

But real measurements vary slightly due to GPS noise and MCU timing effects.  
The firmware therefore evaluates two different properties of the PPS signal:

| Metric | Question Answered                              |
|--------|------------------------------------------------|
| **J**  | *Is the PPS signal stable enough to trust?*    |
| **R**  | *How far off is the MCU clock from true time?* |

These two metrics allow the system to distinguish **PPS quality problems** from **normal oscillator drift**.

---

### J — Short‑Term PPS Jitter

`J` measures how much the PPS interval changes **from second to second**.  
It is computed using a **median absolute deviation (MAD)** style statistic over recent intervals.

Example of a **good PPS signal**:

```
16000002
15999999
16000001
16000000
```

Intervals barely change → **J is small**.

Example of a **bad PPS signal**:

```
15998900
16001200
15999400
16000900
```

Large variation → **J is large**.

If J exceeds configured thresholds the PPS signal is considered unreliable.

Typical logic:

```
if J > lockJ:
    PPS not trustworthy → remain in ACQUIRING
```

MAD is used instead of standard deviation so that **occasional glitches do not destabilize the system**.

---

### R — Long‑Term Frequency Error

`R` measures the **average frequency error of the MCU clock** relative to GPS.

If the measured PPS interval is:

```
n_k = 16,000,240 ticks
```

then the MCU clock is running **+240 ticks/sec fast**, or:

```
+15 ppm frequency error
```

That error is reported as **R**.

Example of a **stable but offset PPS sequence**:

```
16000240
16000239
16000241
16000240
```

Intervals are consistent → **J small**  
But the clock is fast → **R ≈ +15 ppm**

This is **perfectly valid PPS** and the discipliner can correct for it.

---

### Why Both Metrics Are Needed

Consider two PPS streams:

Stable but offset (good):

```
16000240
16000239
16000241
16000240
```

Noisy PPS (bad):

```
15998900
16001200
15999400
16000900
```

Both might average near 16 000 000 ticks, meaning **R ≈ 0**, but the second stream has huge **J** and must be rejected.

Thus:

| Metric | Purpose                                 |
|--------|-----------------------------------------|
| **J**  | determines whether PPS can be trusted   |
| **R**  | determines the MCU frequency correction |

---

### PPS Validation and Classification

Each PPS interval is classified by `PpsValidator`:

- `OK`
- `GAP`
- `DUP`
- `HARD_GLITCH`

Startup seeding establishes the reference interval and allows the validator to tolerate small errors while rejecting pathological ones.

---

### Dual‑Track PPS Discipliner

Once PPS samples are accepted, the discipliner estimates MCU frequency using two EWMAs:

| Track               | Purpose                           |
|---------------------|-----------------------------------|
| **Fast (`f_fast`)** | reacts quickly during acquisition |
| **Slow (`f_slow`)** | low‑noise long‑term estimate      |

The active correction (`f_hat`) blends these using R‑based thresholds:

| Condition             | Behaviour            |
|-----------------------|----------------------|
| |R| < `ppsBlendLoPpm` | prefer slow smoother |
| |R| > `ppsBlendHiPpm` | prefer fast smoother |
| between thresholds    | weighted blend       |

This allows the system to **lock quickly but remain stable once disciplined**.

---

### State Machine

The discipliner exposes four states:

```
NO_PPS → ACQUIRING → LOCKED
   ↑         ↑          ↓
   └─────────┴──────────┴──→ HOLDOVER
```

Transitions depend on:

- `R` and `J` thresholds
- consecutive good/bad PPS counts
- PPS presence/absence

---

### Frequency Correction Applied to Pendulum Measurements

Once locked, the discipliner produces a calibrated timer frequency estimate:

```
f_hat ≈ F_CPU * (1 + R / 1e6)
```

Pendulum measurements are scaled using this estimate so that:

- MCU oscillator drift is removed
- measurements track **true SI seconds derived from GPS**

The output fields reflect this:

| Field            | Meaning                             |
|------------------|-------------------------------------|
| `corr_inst_ppm`  | instantaneous PPS correction        |
| `corr_blend_ppm` | blended correction used for scaling |

---

Compatibility note: `ppsHampelWin`, `ppsHampelKx100`, `ppsMedian3`, and `correctionJumpThresh` remain in CLI/EEPROM/status for backward compatibility but are currently **no‑op in the live discipliner path**.

## Accuracy & Units

- **RawCycles** = TCB0 ticks @ 16MHz
- **Adjusted** = scaled using PPS smoothing (fast during acquisition, slow when locked)
- PPS correction fields are scaled ×1e6 → µppm ints

## Estimated long‑term timing accuracy

1. **Base resolution**  
   The Nano Every runs at 16MHz, so the free‑running timer advances in steps of

    T_tick = 1 / 16,000,000 = 62.5 ns

2. **Noise per PPS measurement**  
   - Quantization of one timer tick ⇒ standard deviation  
     sigma_q = 62.5 ns / sqrt(12) ≈ 18 ns
   - GPS PPS jitter ≈ 20ns (rms)  
   - Combined measurement noise  
     sigma_meas = sqrt(18^2 + 20^2) ≈ 26.9 ns

3. **Noise after slow smoothing**  
   The slow PPS smoother uses an EWMA with shift=8 → α=1/256.  
   Thus the RMS error of the calibrated 1‑s interval is  
   sigma_out = 26.9 ns * sqrt((1/256) / (2 - 1/256)) ≈ 1.2 ns

4. **Fractional frequency error**  
   epsilon = sigma_out / 1 s ≈ 1.2 × 10^-9 (about 1.2 ppb)

5. **Accumulated time error over extended periods**  
   For a duration T, the expected timing error is epsilon * T:
   - For 1 hour, error ≈ 1.2 × 10^-9 * 3600 ≈ 4.3 µs
   - For 1 day, error ≈ 1.2 × 10^-9 * 86,400 ≈ 0.10 ms

   Summing per‑tick quantization noise over N=T seconds adds about  
   18 ns * sqrt(N) ≈ 1 µs per hour, so the total error remains ≈5µs per hour.

**Conclusion:**  
With continuous GPS lock and the provided smoothing, the pendulum timer should maintain long‑term accuracy on the order of **±5µs per hour (≈0.1ms per day)**, equivalent to roughly **1ppb** frequency precision.


---

## Build Notes

- EVSYS mapping on ATmega4809 is weird — see `CaptureInit.cpp` comments.
- Rings: IR=64, PPS=16 (power-of-two ring sizes).
- (naked) ISRs avoid anything uncessary (i.e. `Serial.print`) like the plague.
- CSV lines capped at 256 bytes.

---

## License

MIT. See `LICENSE`.

---
