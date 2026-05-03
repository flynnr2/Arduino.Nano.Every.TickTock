# Pendulum Data Record Guide

Status and scope: interpretation guide for pendulum data records in both CANONICAL and DERIVED modes. `Protocol_Wire_Contract.md` remains the normative emitted-schema source.

## Relationship to `Protocol_Wire_Contract.md`

Use this guide to interpret record meaning and analysis implications. Use `Protocol_Wire_Contract.md` for exact tags, field order, schema IDs, and contract-level parser requirements.

## CANONICAL mode: `SCH` + `CSW`/`CPS`

In CANONICAL mode, hosts receive explicit schema declarations (`SCH`) and should parse:
- `CSW` rows for canonical swing-boundary timing data
- `CPS` rows for canonical PPS-boundary timing data

This mode is the preferred/default runtime path.

## DERIVED mode: `HDR_PART` + `SMP`

In DERIVED mode, hosts receive segmented header declarations (`HDR_PART`) followed by derived `SMP` rows.

This mode is useful when host workflows are optimized around derived interval fields and legacy analysis expectations.

## How hosts should choose which records to consume

- Choose mode handling from metadata (`CFG em=...`).
- Consume only the data families for the active mode.
- Treat inactive-mode metadata as capability/context, not contradiction.

## Common interpretation pitfalls

- Do not treat this guide as schema authority; contract authority is `Protocol_Wire_Contract.md`.
- Do not mix CANONICAL and DERIVED data families in one parser state without explicit contract rollover handling.
- Keep parser behavior aligned with `Host_Parser_State_Machine.md` startup/readiness gating.
