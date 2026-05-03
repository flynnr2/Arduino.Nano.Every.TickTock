# Emit Mode Guide: CANONICAL vs DERIVED

Status: conceptual mode guide. Exact record schemas and tags are owned by `Protocol_Wire_Contract.md`.

This guide explains why both modes exist and how hosts should decide what to consume.

## Current default

Current firmware defaults to `CANONICAL` emit mode (`ACTIVE_EMIT_MODE` in `PendulumProtocol.h`).

## Mode intent

- **CANONICAL**: preferred/default mode. Hosts consume `SCH` declarations plus `CSW`/`CPS` rows.
- **DERIVED**: compatibility/analysis convenience mode. Hosts consume `HDR_PART` + `SMP` rows.

## Ownership boundaries

- `Protocol_Wire_Contract.md` owns exact schema/tag contracts.
- `Host_Parser_State_Machine.md` owns robust parser-state behavior.
- `Pendulum_Data_Record_Guide.md` owns interpretation guidance across both modes.

## Practical host rule

1. Parse metadata (`CFG`, optional `STS ... cfg`).
2. Branch by active mode (`em`).
3. Accept only mode-appropriate schema + data families.
