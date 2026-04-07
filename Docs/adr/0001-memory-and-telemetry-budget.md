# ADR 0001: Memory and Telemetry Budget Strategy

## Status
Accepted

## Context

Target platform (ATmega4809 / Nano Every) has constrained SRAM and limited serial bandwidth headroom under ISR-driven capture load.

Firmware must preserve:
- deterministic capture behavior
- bounded queue growth
- stable main-loop fairness
- wire-contract continuity

## Decision

Use explicit compile-time budget controls and small, power-of-two rings as baseline:

- swing row ring: `RING_SIZE_SWING_ROWS = 8`
- IR edge ring: `RING_SIZE_IR_SENSOR = 32`
- PPS ring: `RING_SIZE_PPS = 8`
- PPS scale ring: `PPS_SCALE_RING_SIZE = 8`

Keep line formatting bounded:

- `CSV_LINE_MAX = 384`
- bounded status payload wrapping and static assertions for header/message fit

Use profile-based optional telemetry defaults:

- `ENABLE_PROFILING=0` baseline for lower overhead
- optional diagnostics (`TUNE_*`, `PPS_BASE`, memory telemetry cadence) enabled/disabled by compile-time flags with profile-dependent defaults

## Consequences

Positive:
- predictable SRAM footprint
- reduced risk of capture drops due to foreground starvation
- bounded serial formatting pressure

Tradeoffs:
- less out-of-box diagnostics in low-overhead profile
- deeper troubleshooting may require profiling-enabled builds
- schema/telemetry additions require memory budget re-check

## Operational guidance

When adding telemetry or sample fields:
1. re-check static SRAM impact (rings + format buffers + telemetry workspaces),
2. verify line-length limits and related static assertions,
3. run representative burst tests for queue pressure,
4. review whether profiling defaults should change.

## Revisit triggers

- New always-on STS families
- Sample schema expansion
- Increased ISR-side buffering
- Additional runtime caches/workspaces
