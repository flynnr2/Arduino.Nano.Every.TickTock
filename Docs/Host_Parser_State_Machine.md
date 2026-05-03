# Host Parser State Machine

This document defines a deterministic host-side ingestion state machine for the firmware serial stream across startup, late attach, and replay/on-demand metadata flows.

It complements `Docs/Emit_Mode_Guide.md` and should be applied together with `CFG`/`STS` metadata parsing.

---

## 1) Deterministic states

Recommended parser states:

- `WAIT_CFG`
  - Ignore data rows.
  - Accept and cache `STS` lines.
  - Transition only after a valid `CFG` is seen.
- `WAIT_SCHEMA`
  - `CFG` is known; `emit_mode` branch is known.
  - Accept only mode-appropriate schema declaration rows:
    - CANONICAL: `SCH`
    - DERIVED: full `HDR_PART` set
- `READY`
  - Schema readiness satisfied for active mode.
  - Accept data rows:
    - CANONICAL: `CSW` and `CPS`
    - DERIVED: `SMP`
- `RECOVER`
  - Entered on malformed or out-of-order contract violations.
  - Drop/ignore data rows until parser regains `CFG + schema` readiness.

### Startup/attach gating rule (hard requirement)

A host MUST begin accepting data rows only after both are true:

1. `CFG` has been parsed successfully, and
2. Mode-specific schema readiness has been reached:
   - CANONICAL: at least one valid `SCH` for each emitted family used by host logic (`CSW`, `CPS`).
   - DERIVED: one complete `HDR_PART` sequence that can be assembled into the active sample header.

Until then, rows that look like samples are ignored as preamble noise.

---

## 2) Replay / on-demand flow

The command plane supports replay without reboot.

### After `emit meta`

Treat emitted lines as metadata refresh:

- Expect replayed `STS` metadata records.
- Expect `CFG` replay.
- Expect mode schema declaration replay (`SCH` or `HDR_PART` set).
- Keep current state, but re-validate cached contract values.

If replayed metadata is identical, remain `READY` with no reset of sample counters.
If any contract-defining value changes (mode/schema id/header composition), perform a contract rollover:

1. Mark prior contract closed.
2. Re-enter `WAIT_SCHEMA` (or `WAIT_CFG` if `CFG` became invalid/missing).
3. Resume data acceptance only after readiness is re-established.

### After `emit startup`

Treat this as startup stream replay in-band:

- Replayed boot/config `STS` are informational.
- Replayed `CFG` + schema declarations are authoritative for current contract.
- Apply the same rollover rule as `emit meta` when contract-defining values differ.

### Duplicate/replayed `STS`, config, and schema rows

- `STS`: idempotent; safe to deduplicate by `(tag,family,payload)` or accept as append-only telemetry.
- `CFG`: idempotent only when values match current contract.
- `SCH` / `HDR_PART`: idempotent only when declaration content matches currently active schema cache.
- Non-identical duplicates are **schema/config change events**, not harmless duplicates.

---

## 3) Mode branch logic

Branch strictly by `emit_mode` from `CFG` (or mirrored `STS cfg`).

### CANONICAL path

Expected order:

1. `CFG ... em=CANONICAL`
2. `SCH,...` declarations (for canonical families)
3. data rows: `CSW,...` and/or `CPS,...`

Acceptance rules:

- Reject/ignore `SMP` while in CANONICAL mode.
- If a new `SCH` changes declared canonical schema mid-stream, pause data acceptance and perform rollover to `WAIT_SCHEMA` then `READY`.

### DERIVED path

Expected order:

1. `CFG ... em=DERIVED`
2. full `HDR_PART` set (all segments needed for complete header)
3. data rows: `SMP,...`

Acceptance rules:

- Reject/ignore `CSW`/`CPS` while in DERIVED mode.
- Any incomplete or mismatched `HDR_PART` assembly keeps parser in `WAIT_SCHEMA`.

---

## 4) Recovery rules

### Malformed line

- If line cannot be tokenized/validated for its tag, increment parse error counter and drop line.
- Stay in current state unless malformed line is a contract line (`CFG`, `SCH`, `HDR_PART`) required for readiness; then move to `RECOVER`.

### Out-of-order line

- Data row before readiness (`CFG + schema`) => drop row, count as out-of-order, remain waiting.
- Wrong-family row for active mode => drop row, count mode violation.

### Schema/config changes mid-stream

Trigger rollover when any of these change:

- `emit_mode`
- schema id/version identifiers
- canonical `SCH` definition affecting consumed families
- assembled DERIVED header text from `HDR_PART`

Rollover algorithm:

1. Emit host-side event `contract_changed` with old/new fingerprints.
2. Freeze data ingestion.
3. Re-enter `WAIT_CFG` or `WAIT_SCHEMA` as appropriate.
4. Resume in `READY` only after fresh readiness criteria pass.

---

## 5) Minimal conformance sequence snippets

Snippets below show the required ordering constraints; payload fields abbreviated with `...`.

### A) Fresh boot

CANONICAL:

```text
STS,...build...
STS,...cfg...
CFG,...em=CANONICAL,...
SCH,...canonical_swing_v1...
SCH,...canonical_pps_v1...
CSW,...
CPS,...
```

DERIVED:

```text
STS,...build...
STS,...cfg...
CFG,...em=DERIVED,...
HDR_PART,1/N,...
...
HDR_PART,N/N,...
SMP,...
```

### B) Late host attach

If attach occurs mid-run and first observed row is data, host waits:

```text
CSW,...              # ignored until readiness
STS,...cfg...        # cached
CFG,...em=CANONICAL,...
SCH,...canonical_swing_v1...
SCH,...canonical_pps_v1...
CSW,...              # first accepted data row
```

Equivalent DERIVED behavior requires a complete `HDR_PART` set before first accepted `SMP`.

### C) On-demand metadata refresh

```text
... READY ingesting data ...
(Host/user issues: emit meta)
STS,...cfg...
CFG,...
SCH,...   or HDR_PART,...
... READY continues (if unchanged) ...
```

If refreshed declarations differ, parser performs rollover and resumes only after new readiness.

---

## 6) Host implementation notes

- Compute a compact **contract fingerprint** from (`emit_mode`, schema identifiers, full declaration text).
- Store the active fingerprint alongside captured rows.
- Segment output files/partitions at fingerprint changes to prevent mixed-contract analytics.
- Keep counters for dropped pre-readiness rows, malformed rows, and mode violations for observability.
