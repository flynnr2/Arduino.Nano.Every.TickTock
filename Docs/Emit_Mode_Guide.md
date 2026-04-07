# Emit Mode Guide: CANONICAL vs DERIVED

This document explains how output mode selection works and how to interpret records in each mode.

Authoritative source files:
- `Nano.Every/src/PendulumProtocol.h`
- `Nano.Every/src/SerialParser.cpp`
- `Nano.Every/src/PendulumCore.cpp`
- `Nano.Every/src/StatusTelemetry.cpp`

---

## 1) Current runtime behavior

The firmware currently hard-pins:

- `ACTIVE_EMIT_MODE = EMIT_MODE_CANONICAL`

in `PendulumProtocol.h`. This currently overrides `PENDULUM_EMIT_MODE`.

So, out of the box, the runtime emits canonical rows (`SCH`, `CSW`, `CPS`) rather than derived rows (`HDR_PART`, `SMP`).

---

## 2) Modes and emitted tags

## DERIVED mode

- Header declaration: `HDR_PART`
- Data row: `SMP`
- Schema ID: `raw_cycles_hz_v7`
- Declared in `SAMPLE_SCHEMA` + `SAMPLE_SCHEMA_HDR_PARTS`

Use DERIVED mode when the host consumes reconstructed interval fields (`tick`, `tick_adj`, `*_total_adj_direct`, diagnostics, etc.).

## CANONICAL mode

- Header declaration: `SCH`
- Data rows:
  - `CSW` (canonical swing boundaries)
  - `CPS` (canonical PPS boundaries)
- Schema IDs:
  - `canonical_swing_v1`
  - `canonical_pps_v1`

Use CANONICAL mode when host analysis prefers absolute shared-timeline edge boundaries as source truth.

---

## 3) CFG/STS metadata interpretation

`CFG` and `STS ... cfg` include:

- protocol and schema metadata
- active emit mode (`em`)
- canonical tag/schema keys (`cst/css/cpt/cps` when compact keys are enabled)

Even when CANONICAL mode is active, metadata may include both derived and canonical references so parsers can branch safely.

Host parser rule:
1. Read `CFG` / `STS cfg`.
2. Branch by `emit_mode`.
3. Validate incoming line families accordingly:
   - DERIVED: expect `HDR_PART` then `SMP`
   - CANONICAL: expect `SCH` then `CSW`/`CPS`

---

## 4) Switching modes (current implementation)

Current code-level switch:

1. Edit `Nano.Every/src/PendulumProtocol.h`.
2. Change:
   - `ACTIVE_EMIT_MODE = EMIT_MODE_CANONICAL`
   to desired mode constant.
3. Rebuild and flash.

Note: today `ACTIVE_EMIT_MODE` is a compile-time constant and does not follow a runtime command.

---

## 5) Practical compatibility guidance

- Persist run metadata (`emit_mode`, schema IDs, `protocol_version`, `adj_semantics_version`) with raw logs.
- Do not mix CANONICAL and DERIVED rows in one parser path without explicit mode-aware branching.
- When replaying historical logs, infer parser profile from observed tags plus `CFG` metadata.
