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
- `gps_status`: 0 = no PPS, 1 = acquiring, 2 = locked, 3 = holdover, 4 = bad jitter
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
| `correctionJumpThresh` | float  | 0.002      | Lock threshold (fractional, e.g. 0.002 = 2000 ppm)
| `ppsFastShift`         | uint8  | 3          | Short‑term EWMA shift (lower = faster)
| `ppsSlowShift`         | uint8  | 8          | Long‑term EWMA shift (higher = smoother)
| `ppsHampelWin`         | uint8  | 7          | Hampel window size (odd, 5–9 recommended)
| `ppsHampelKx100`       | uint16 | 300        | Hampel outlier threshold (k × 100)
| `ppsMedian3`           | bool   | 1          | Apply median‑of‑3 after Hampel (0/1)

Example:
```
set ppsFastShift 3
set ppsSlowShift 8
set ppsHampelWin 7
set ppsHampelKx100 300
set ppsMedian3 1
set correctionJumpThresh 0.002
saveConfig
```

---

## Dual‑Track PPS Smoothing

The GPS PPS is good, but not *perfect*. To avoid chasing every twitch while still tracking real drift, we smooth it two ways at once:

1. **Short‑term track** — responds in seconds, cleans up jitter, drives immediate time scaling.  
2. **Long‑term track** — averages over minutes, immune to brief noise, used for holdover and slow retuning.

### How It Works

1. ISR timestamps each PPS edge (ticks since last PPS).  
2. Clamp interval to a sane range.  
3. **Hampel filter**: rolling median + MAD, replace outliers beyond *k × MAD*.  
4. Optional **median‑of‑3**.  
5. **Fast EWMA** on clean data → `pps_delta_fast` (τ ≈ 8s).  
6. **Slow EWMA** on fast output → `pps_delta_slow` (τ ≈ 256s).  
7. Compute:
   - `corr_inst_ppm` from raw delta
   - `corr_blend_ppm` from blended fast/slow smoothing
8. Lock declared when inst vs slow is within `correctionJumpThresh` for N PPS ticks.

Why?  
- Fast track: reacts quickly to real drift.  
- Slow track: holds rock‑steady during GPS burps.  
- Together: smooth ride, no over‑steer.

## PPS Fast/Slow Blending & GPS Lock Tracking

This firmware now uses **two parallel PPS smoothers** and a **state machine** to improve stability and responsiveness of timing calculations.

### Overview
The GPS PPS signal is filtered and smoothed in two ways:
- **Fast EWMA** (`pps_delta_fast`) — responds quickly to real frequency changes (e.g., temperature drift, trim adjustments).
- **Slow EWMA** (`pps_delta_slow`) — low-noise, long-term average for stable scaling.

Each PPS tick, the firmware:
1. Measures the difference between **fast** and **slow** smoothers (**R**, in ppm).
2. Estimates PPS jitter using a Hampel filter MAD value (**J**, in ppm).
3. Runs a **state machine** to decide if GPS is:
   - `NO_PPS` — no pulse detected.
   - `ACQUIRING` — PPS present but not yet stable.
   - `LOCKED` — PPS is stable and low-jitter.
   - `BAD_JITTER` — PPS present but jitter exceeds threshold.
   - `HOLDOVER` — PPS lost, using last known rate.

4. Computes a **blend weight** between the fast and slow smoothers:
   - Near 0 → fully slow.
   - Near 1 → fully fast.
   - The weight increases as drift (R) grows.
   - When `LOCKED`, weight is forced to slow.
   - When `ACQUIRING`, weight is forced to fast.

5. Caches a blended PPS denominator (`pps_delta_active`) that is used in **all time conversions** (`ticks_to_us_pps`, `ticks_to_ns_pps`).

### Benefits
- **Fast reaction** to genuine frequency shifts.
- **Low jitter** during stable operation.
- Smooth transitions — no abrupt jumps in output timing.
- Clearer `gpsStatus` reporting for downstream logging.

### Key Tunables
These are adjustable via config or EEPROM:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ppsBlendLoPpm` | 5 ppm | Below this drift, prefer slow smoother. |
| `ppsBlendHiPpm` | 200 ppm | Above this drift, fully fast smoother. |
| `ppsLockRppm` | 50 ppm | Max drift to declare lock. |
| `ppsLockJppm` | 20 ppm | Max jitter to declare lock. |
| `ppsUnlockRppm` | 200 ppm | Drift to unlock. |
| `ppsUnlockJppm` | 100 ppm | Jitter to unlock. |
| `ppsUnlockCount` | 3 | Consecutive bad PPS before unlock. |
| `ppsHoldoverMs` | 1500 ms | PPS gap to enter holdover. |

### State Machine Diagram

```
NO_PPS → ACQUIRING → LOCKED → BAD_JITTER ↘
   ↑         ↑          ↓         ↑      HOLDOVER
   └─────────┴──────────┴─────────┘
```

*(Transitions depend on R/J thresholds and PPS presence.)*

---

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
- Rings: IR=64, PPS=16 (power‑of‑two for mask magic).
- ISRs avoid `Serial.print` like the plague.
- CSV lines capped at 128 bytes.

---

## License

MIT. See `LICENSE`.

---
