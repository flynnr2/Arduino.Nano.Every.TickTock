# STS Debugging Instrumentation & Telemetry Guide

This guide is for engineers who are new to the repo and need to quickly understand **what STS emits**, **how to enable it**, and **which fields matter for debugging**.

---

## 1) What STS is

All STS messages are sent as CSV lines on the data serial stream with this shape:

```text
STS,<STATUS_CODE>,<payload>
```

For debug/telemetry, the status code is typically `PROGRESS_UPDATE`, and `<payload>` starts with a family token such as `gps_decision`, `gps_health1`, `court2`, etc.

---

## 2) Compile-time switches that control STS debug output

The main toggles are in `Nano.Every/src/Config.h`:

- `ENABLE_STS_GPS_DEBUG` (default `1`): master switch for GPS STS diagnostics (`gps_*`, health, decision lines).
- `ENABLE_STS_GPS_SNAP` (default `1`): enables anomaly-triggered forensic snapshot dump (`gps_snap_dump`, `gps_snap1`, `gps_snap2`).
- `ENABLE_STS_GPS_DEBUG_VERBOSE` (default `1`): verbose payload mode (minimal vs full).
- `STS_DIAG` (default `2`): courtroom diagnostics level (`0=off`, `1=summaries`, `2=event-level`).
- `STS_DIAG_COURT_PERIOD_MS` (default `10000`): cadence for periodic `court*` summary emission.
- `PPS_TUNING_TELEMETRY` (default `0`): optional tuning telemetry families (`TUNE_CFG`, `TUNE_WIN`, `TUNE_EVT`).
- `ENABLE_ISR_DIAGNOSTICS` (default `1`): tracks ISR timing/latency counters surfaced through health/court telemetry.

### 2.1 Which `#define`s to turn on/off for common use cases

Use this as the quick “what do I flip?” matrix.

| Goal | `ENABLE_STS_GPS_DEBUG` | `ENABLE_STS_GPS_SNAP` | `ENABLE_STS_GPS_DEBUG_VERBOSE` | `STS_DIAG` | `PPS_TUNING_TELEMETRY` | `ENABLE_ISR_DIAGNOSTICS` |
|---|---:|---:|---:|---:|---:|---:|
| **Minimal production logging** (lowest STS volume) | `0` | `0` | `0` | `0` | `0` | `1` |
| **Routine field monitoring** (recommended baseline) | `1` | `0` | `0` | `1` | `0` | `1` |
| **General debug run** (good first deep dive) | `1` | `1` | `1` | `2` | `0` | `1` |
| **Wrap/backstep/capture-path investigation** | `1` | `1` | `1` | `2` | `0` | `1` |
| **Threshold tuning session** | `1` | `0` or `1` | `1` | `1` or `2` | `1` | `1` |

### 2.2 What each switch controls in practice

- Set `ENABLE_STS_GPS_DEBUG=0` to suppress most `gps_*` runtime debug families (decision/health/per-edge forensics). Keep this `1` whenever you need PPS diagnostics.
- Set `ENABLE_STS_GPS_SNAP=1` only when you want anomaly-triggered ring-buffer dump lines (`gps_snap_dump/gps_snap1/gps_snap2`). Turn it off if bandwidth or parser simplicity is more important.
- Set `ENABLE_STS_GPS_DEBUG_VERBOSE=0` for concise payloads; set to `1` for richer per-line context during investigations.
- Set `STS_DIAG` by required depth:
  - `0`: no courtroom summaries/events
  - `1`: periodic `court1..court5` summaries
  - `2`: adds event-level courtroom diagnostics (recommended for overnight root-cause hunts)
- Set `PPS_TUNING_TELEMETRY=1` only during lock/unlock threshold work; otherwise keep `0` to avoid extra telemetry volume.
- Keep `ENABLE_ISR_DIAGNOSTICS=1` unless you are doing strict overhead experiments; disabling it removes ISR duration telemetry used by `gps_health3`/`court2` style analysis.

### 2.3 Example override block

If you need an explicit “courtroom-level + verbose + snapshots” build:

```cpp
#define ENABLE_STS_GPS_DEBUG 1
#define ENABLE_STS_GPS_SNAP 1
#define ENABLE_STS_GPS_DEBUG_VERBOSE 1
#define STS_DIAG 2
#define PPS_TUNING_TELEMETRY 0
#define ENABLE_ISR_DIAGNOSTICS 1
```

If you need a quieter routine monitoring build:

```cpp
#define ENABLE_STS_GPS_DEBUG 1
#define ENABLE_STS_GPS_SNAP 0
#define ENABLE_STS_GPS_DEBUG_VERBOSE 0
#define STS_DIAG 1
#define PPS_TUNING_TELEMETRY 0
#define ENABLE_ISR_DIAGNOSTICS 1
```

---

## 3) Startup metadata STS families (always read these first)

These lines establish run identity and schema before interpreting any other telemetry:

- `rstfr,...` reset cause flags.
- `build,...` firmware identity (`git`, dirty flag, UTC build stamp, board/mcu/clock/baud, cfg version).
- `schema,...` schema versions for GPS/health/snapshot/court families.
- `flags,...` diagnostic feature flags compiled into firmware.
- `tun1,...` and `tun2,...` active tunables / threshold summary.
- `pps_cfg,...` PPS validator thresholds and seed/reseed constants.
- `tcb_clk,...` timer clock register snapshot.

**Tip:** If you ingest historical STS files, always key your parser behavior off `schema` + `flags` + `tun1`/`pps_cfg` from that same file.

---

## 4) Runtime STS telemetry families (what each one means)

## 4.1 Core decision stream

### `gps_decision`
Primary per-edge decision log from the PPS validator/discipliner path.

Key fields:
- `state`: discipliner state (`FREE_RUN`/`ACQUIRE`/`DISCIPLINED`/`HOLDOVER`)
- `pps_valid`: whether validator has a trusted lock-quality reference
- `cls`: sample classification (`OK`, `SOFT`, `HARD_GLITCH`, `GAP`, etc.)
- `lockN`: current validator good-streak
- `dt32`: full 32-bit PPS interval ticks
- `dt16`: 16-bit modulo interval telemetry
- `R_ppm`, `J`: quality metrics from discipliner
- `reason`: human-oriented reason code for current classification/decision

---

## 4.2 GPS edge forensics (immediate edge context)

### `gps1`
Per-edge reconstruction and expected-interval context.

Typical fields:
- sequencing: `edge_seq`, `log_seq`, `pps`
- startup/prime context: `prime`, `exp_valid`, `exp_init`, `steady`
- classification support: `w`, `lr`, `rr`
- interval comparison: `dc` (dt16/mod), `dm` (dt32), `exp`, `e`, `mad_e`
- soft diagnostics: `soft_ticks`

### `gps2`
Per-edge wrap/coherent/latency context.

Typical fields:
- anomaly flags: `hg`, `bs`, `so`
- extension/coherent counters: `wrap`, `coh=seen|applied`
- capture/latency internals: `lat`, `cap`, `cnt`

---

## 4.3 Rolling health summaries

### `gps_health1`
Windowed health (short-term quality snapshot):
- `R`, `J`, `br_mad`, `br_max`
- 10-edge counters: `w10`, `lr10`, `so10`, `hg10`, `gap10`, `bs10`

### `gps_health2`
Longer-running cumulative counters and logging health:
- cumulative anomaly counts: `hg_cnt`, `gap_cnt`, `so_cnt`, `warm_so_cnt`, `bs_cnt`
- sequence counters: `edge_gap_cnt`, `edge_seq`, `log_seq`
- output integrity counter: `trunc_cnt`

### `gps_health3`
ISR/latency stress indicators:
- `maxisr_tcb0`, `maxisr_tcb1`, `maxisr_tcb2`
- `lat16_max`, `lat16_wr`

### `gps_health_d1` and `gps_health_d2`
Higher-detail periodic diagnostics combining classifier + discipliner + seed telemetry.

Useful fields include:
- `ok/gap/dup/hg`, `pps_valid`, `disc`
- `f_fast`, `f_slow`, `f_hat`
- `R_ppm`, `mad_ticks`, `hold_ms`
- `dt16`, `dt32`, `br`
- seeding source/status: `seed_ph`, `seed_cnt`, `seed_cand`, `s_near1`, `s_near2`, `s_rst`, `reseed`, `ref_src`

---

## 4.4 Courtroom diagnostics (`STS_DIAG`)

When `STS_DIAG > 0`, periodic `court1..court5` summaries emit every `STS_DIAG_COURT_PERIOD_MS`.

### `court1`
Pipeline pressure and gross anomaly totals:
- `pps_isr`, `pps_proc`, `pps_backlog_max`
- `gap_total`, `hard_total`
- `dup_sus`, `miss_sus`

### `court2`
Interrupt-masking/coherent-read stress and ISR maxima:
- `cli_max`, `cli_gt65536`, `cli_gt131072`, `cli_unbal`
- `coh_retry`, `coh_seen`, `coh_applied`
- `backstep`
- `isr0_max`, `isr1_max`, `isr2_max`

### `court3`
Gap histogram and large-gap monitors:
- `gap65536`, `gap131072`, `gap_max`
- `gap_hist=<6 bins>`

### `court4`
TCB0 coherent-time gap histogram:
- `tcb0_gap_hist=<4 bins>`

### `court5`
PPS seeding/reseed and reference-source diagnostics:
- `seed_ph`, `seed_cnt`, `seed_cand`
- `s_near1`, `s_near2`, `s_rst`, `reseed`
- `ref_src`, `n_ref`

---

## 4.5 Event-level diagnostics (`STS_DIAG > 1` and/or verbose paths)

### `gap_evt`
Emitted around GAP classifications to preserve local capture context:
- `cls`, `cap_prev`, `cap_cur`, `dc`, `dm`, `lat`, `cnt2`, `edge_seq`

### `WARN,f_hat_suspicious,...`
Safety warning when estimated disciplined tick rate appears implausible.

### `pps_int`
Compact interval telemetry: `dt32`, `dt16`, `f_hat`.

### `tcb0_health`
Low-level timer/coherent-OVF health:
- overflow/wrap counters
- capture pending flags
- coherent counters
- ISR last/max durations

### `pend_health`
Pendulum-side edge integrity:
- total edges and dropped events
- backstep/big/small/wrapish counters
- last bad edge sequence + delta

---

## 4.6 Optional forensic snapshot dump

When `ENABLE_STS_GPS_SNAP=1`, anomaly-triggered burst output may include:

- `gps_snap_dump,reason=...,pps=...` (dump header)
- `gps_snap1,...` (per-slot reconstruction details)
- `gps_snap2,...` (per-slot capture/coherent context + class)

This is ring-buffer replay intended for post-mortem analysis around notable anomalies.

---

## 4.7 Optional threshold tuning telemetry (`PPS_TUNING_TELEMETRY=1`)

- `TUNE_CFG`: active lock/unlock threshold snapshot.
- `TUNE_WIN`: percentile and max summary over a window (R/J quality distributions).
- `TUNE_EVT`: explicit state-transition events with streak and quality values.

---

## 5) Practical triage workflow for newcomers

1. **Confirm run identity first**: parse `build`, `schema`, `flags`, `tun1`, `pps_cfg`.
2. **Check state-machine behavior**: inspect `gps_decision` (`state`, `cls`, `reason`, `pps_valid`).
3. **Check quality/stability**: inspect `gps_health1/2/3` and `gps_health_d1/d2`.
4. **Check timing starvation/wrap risks**: inspect `court2/3/4` and `tcb0_health`.
5. **When anomalies occur**:
   - correlate `gap_evt` with nearby `gps1/gps2` and `court*`
   - if available, parse `gps_snap_dump/gps_snap1/gps_snap2`
6. **Pendulum data sanity**: inspect `pend_health` and main `DAT/*` sample stream for dropped events and scaling anomalies.

---

## 6) Field-family quick index

- **Run metadata:** `rstfr`, `build`, `schema`, `flags`, `tun1`, `tun2`, `pps_cfg`, `tcb_clk`
- **Decision path:** `gps_decision`
- **Per-edge forensic:** `gps1`, `gps2`
- **Rolling health:** `gps_health1`, `gps_health2`, `gps_health3`, `gps_health_d1`, `gps_health_d2`
- **Court summaries:** `court1`, `court2`, `court3`, `court4`, `court5`
- **Event-level extras:** `gap_evt`, `WARN,f_hat_suspicious`, `pps_int`, `tcb0_health`, `pend_health`
- **Snapshot replay:** `gps_snap_dump`, `gps_snap1`, `gps_snap2`
- **Tuning telemetry (optional):** `TUNE_CFG`, `TUNE_WIN`, `TUNE_EVT`

---

## 7) Notes for parser/tool authors

- Treat STS payload as **tokenized key/value CSV**, not fixed-column strict CSV; families have different key sets.
- Use family prefix (`build`, `gps_decision`, `court2`, etc.) as the top-level discriminator.
- Preserve unknown keys for forward compatibility.
- Schema/version values in `schema` + compile flags in `flags` should drive parser expectations.
