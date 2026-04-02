# Pendulum CSV Requirements and Semantics Draft

## 1. Purpose

`pendulum.csv` is the **primary scientific and operational record** for pendulum analysis.

Its purpose is to support:

- direct end-user diagnosis of pendulum behaviour,
- component-level analysis of asymmetry and drive effects,
- accurate clock rating and stability analysis,
- offline refinement and reinterpretation using `pendulum.csv` alone.

`STS` is a **device-diagnostic and validation channel**. It is not the primary analysis artifact for pendulum behaviour, and exact replay of STS-derived results is not the main requirement for `pendulum.csv`.

---

## 2. Design intent

`pendulum.csv` should be:

- **self-contained**: sufficient on its own for downstream pendulum analysis,
- **end-user-facing**: immediately usable without requiring STS,
- **diagnostically faithful**: preserving the physical structure of each swing,
- **metrologically strong**: providing the best practical adjusted timing estimates,
- **offline-extensible**: containing enough provenance for later reconstruction or refinement.

The file should not require the embedded device to do all possible heavy analysis in real time. The device should emit the best practical first-pass estimates plus enough metadata to support stronger offline analysis.

---

## 3. Core philosophy

The governing principles are:

### 3.1 Raw components are sacred
Raw component timings are the immutable observational base and must always be preserved.

These raw values are the closest record of what the sensor and capture path observed.

### 3.2 Adjusted components should be conservative and diagnostically faithful
Adjusted component fields should improve timing accuracy while avoiding artificial distortion of the internal geometry of the swing.

They should preserve physically meaningful relationships within the row as much as possible.

### 3.3 Adjusted totals should chase best absolute corrected time
Adjusted half-swing and full-swing totals should prioritize the best available corrected estimate for rating, stability, and timekeeping analysis.

### 3.4 Provenance matters
Each row should carry enough metadata and diagnostics that offline tooling can verify, reinterpret, or improve the row using `pendulum.csv` alone.

---

## 4. Primary scientific goals

The file must support the following primary uses.

### 4.1 Component-level pendulum diagnosis
The user must be able to study each component of the swing individually, including:

- beam-block intervals,
- non-block intervals,
- tick-side versus tock-side behaviour,
- component-level asymmetry,
- evidence of drive, escapement, train, or geometry effects.

### 4.2 Accurate rating and timing analysis
The user must be able to derive reliable adjusted totals for:

- half-swing timing,
- full-swing timing,
- rolling rate estimates,
- drift and stability over time.

### 4.3 Offline reinterpretation from `pendulum.csv` alone
A later analyst must be able to use the raw values plus row metadata to:

- recompute derived totals,
- audit adjusted values,
- form alternative correction models,
- quantify the effect of diagnostic classes or adjustment modes.

This requirement does not mean exact STS replay is mandatory. It means `pendulum.csv` must stand on its own as the analysis record.

---

## 5. Field classes and authority

The fields in `pendulum.csv` fall into four conceptual classes.

## 5.1 Raw observational fields
These are authoritative for what was directly measured.

Examples:
- `tick`
- `tick_block`
- `tock`
- `tock_block`

### Meaning
These are the direct captured durations in raw clock cycles.

### Authority
These are the authoritative source for raw observational truth.

### Requirement
They must always be present and preserved exactly.

---

## 5.2 Adjusted component fields
These are authoritative for first-pass corrected component analysis, but remain subordinate to raw values plus provenance for deeper offline work.

Examples:
- `tick_adj`
- `tick_block_adj`
- `tock_adj`
- `tock_block_adj`

### Meaning
These are the device’s best practical corrected estimates of the corresponding raw components.

### Authority
These are the authoritative **operational** adjusted component values for end users.

### Requirement
They should:
- improve over raw for clock/frequency error,
- remain coherent with row context,
- avoid creating artificial component asymmetry or false internal structure.

### Interpretation
They are intended to be directly usable, but not necessarily the only possible corrected interpretation offline.

---

## 5.3 Adjusted total fields
These are authoritative for best corrected timing of larger interval objects.

Examples:
- `tick_total_adj_direct`
- `tock_total_adj_direct`

### Meaning
These are the best available corrected estimates of the full half-swing totals.

### Authority
These are the authoritative adjusted total intervals for rating, stability, and period-level analysis.

### Requirement
They should prioritize absolute corrected timing quality, even where their construction differs from simpler component-sum approximations.

### Interpretation
They may be more authoritative for total timing than reconstructed sums of adjusted subcomponents.

---

## 5.4 Context, quality, and provenance fields
These describe the correction context and diagnostic status of the row.

Examples:
- `f_inst_hz`
- `f_hat_hz`
- `gps_status`
- `holdover_age_ms`
- `r_ppm`
- `j_ticks`
- `dropped`
- `adj_diag`
- `tick_total_adj_diag`
- `tock_total_adj_diag`
- `pps_seq_row` when present

### Meaning
These fields explain the timing context, correction state, and any caveats affecting the row.

### Authority
These are authoritative for interpreting confidence, adjustment mode, and offline reconstruction options.

### Requirement
They must be sufficient to explain why a row or field may be more or less trustworthy, and to support later refinement without external files.

---

## 6. Authoritative meanings by use case

## 6.1 For component diagnosis
The primary analysis objects are:

- raw components,
- adjusted components,
- component-level asymmetries,
- differences between raw and adjusted component behaviour.

For this use, the file should preserve structure and relative relationships within the row.

## 6.2 For rating and stability
The primary analysis objects are:

- adjusted half-swing totals,
- derived full-swing totals,
- rolling averages,
- drift and rate statistics.

For this use, best absolute corrected timing is more important than exact preservation of subcomponent proportionality.

## 6.3 For offline reconstruction
The primary analysis objects are:

- raw fields,
- adjustment context fields,
- diagnostics/provenance,
- any identifiers needed to interpret timing boundaries or row context.

For this use, the goal is not exact replay of STS, but the ability to perform strong offline reinterpretation from `pendulum.csv` alone.

---

## 7. What “best available adjusted estimate” means

For this project, “best available adjusted estimate” means:

> the best practical corrected estimate the system can provide for the interval in question, given the correction context available at logging time, while preserving raw observational truth and enough provenance for later offline refinement.

This definition deliberately does **not** require exact equality to a separate STS-matched gold-standard replay.

That exact equality is a validation benchmark, not the primary production requirement.

---

## 8. Explicit non-goals

The following are not primary goals of `pendulum.csv`:

### 8.1 Exact STS equivalence
`pendulum.csv` does not need to reproduce exactly what a fully matched, omniscient STS-based offline reconstruction would produce in every adversarial test.

### 8.2 Device-centric diagnostics as the main focus
Serial framing faults, PPS validator behaviour, ISR-latency forensics, and similar device issues belong primarily to STS and firmware validation.

### 8.3 Maximum on-device computation at any cost
The device does not need to perform all heavy analysis or all possible reconstructions in real time. Logging robustness and clean export matter more.

---

## 9. Acceptance criteria

The file format and adjustment model should be judged against these criteria.

## 9.1 Raw fidelity
Raw component timings must remain exact and stable as exported.

## 9.2 Internal coherence
Adjusted values within a row must be coherent with the row’s own timing context and diagnostics.

## 9.3 Component diagnostic fidelity
Adjustment should not materially distort physically meaningful within-row relationships, especially those used for:

- asymmetry analysis,
- impulse/drive interpretation,
- block versus non-block diagnosis.

## 9.4 Total timing quality
Adjusted total fields must materially improve corrected period/rating quality relative to raw values.

## 9.5 Offline sufficiency
An analyst using `pendulum.csv` alone must be able to:
- audit exported adjusted values,
- derive alternative corrected interpretations,
- understand the row’s timing quality and caveats.

## 9.6 Operational robustness
The requirements must be achievable without undermining logging stability, row integrity, or real-time capture reliability.

---

## 10. Preferred semantic hierarchy

When there is tension between objectives, the preferred hierarchy is:

1. Preserve raw observational truth.
2. Preserve diagnostically meaningful within-row structure.
3. Provide best practical adjusted component estimates.
4. Provide strongest possible adjusted totals for rating and timing.
5. Support deeper offline reinterpretation from `pendulum.csv` alone.

This ordering reflects the project’s scientific goal: understand the pendulum and the clock movement at component level, while still rating the clock accurately.

---

## 11. Practical interpretation of current findings

The recent dual-path PPS shake-down supports the following interpretation:

- the hardware capture paths are sound,
- raw timing agreement is extremely tight,
- STS remains useful as an adversarial validation benchmark,
- the remaining design work is not about proving the capture path,
- the remaining design work is about deciding what corrected truths `pendulum.csv` should carry and how much provenance it must include.

This means future refinement should focus primarily on:

- semantics of adjusted component fields,
- semantics of adjusted total fields,
- sufficiency of provenance and diagnostics in each row,
- offline interpretability from `pendulum.csv` alone.

---

## 12. Recommended working statement

A concise working statement for the project is:

> `pendulum.csv` is the authoritative, self-contained pendulum-analysis record. It must preserve raw component timings, provide best-available adjusted component and total timings, and include enough metadata and diagnostics to support offline interpretation and refinement from `pendulum.csv` alone. Its primary optimization target is component-level fidelity for asymmetry, impulse, and clock-rate diagnosis, with adjusted totals also supporting accurate clock rating. Exact reproduction of STS-based diagnostic reconstructions is not the primary requirement.

---

## 13. Immediate next-step questions

The next design/spec step is to define, field by field:

- which adjusted fields are authoritative for component diagnosis,
- which are authoritative for rating,
- what provenance is minimally required for offline refinement,
- and what diagnostic flags mean operationally for downstream analysis.
