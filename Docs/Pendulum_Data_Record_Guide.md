# Pendulum Data Record Guide

Use the [wire contract](Protocol_Wire_Contract.md) for exact field order, schema
IDs and parser requirements. Captured timestamps are the evidence for all timing
calculations.

## Record interpretation

`CSW` contains five consecutive swing boundaries on the shared TCB0 counter and
cumulative drop counters. `CPS` contains each captured PPS boundary plus capture
and GPS health context. `SCH` declares the field order. `CFG` identifies the
protocol, nominal counter frequency, schemas and firmware version.

The firmware maps capture events onto the shared counter and compensates the
configured input-filter delay. These timestamps are free-running counter values.
The receiver or analysis calculates intervals and PPS-based clock calibration.

With the current sensor polarity, edge0–edge1 and edge2–edge3 are open-beam
intervals; edge1–edge2 and edge3–edge4 are blocked-beam intervals. Full swings
span edge0–edge4, and half swings span edge0–edge2 and edge2–edge4. Differences
must account for unsigned counter wrap.

The analysis suite reports nominal raw durations and PPS-calibrated durations
separately. Calibration requires valid PPS coverage and unambiguous chronology.
Unavailable calibration remains missing. Counter gaps, sequence resets and new
drop-counter increments affect data eligibility.

`STS` contains device health and command diagnostics. Firmware PPS estimation
supports GPS state and optional telemetry; it does not change the captured edge
timestamps. Preserve metadata and raw captures for later analysis.

## Analysis uses

- Beam-block/open interval and half-cycle asymmetry measurements.
- Pendulum rate, stability and jitter.
- Impulse-cycle and clock-family structure using an explicitly selected profile.
- PPS capture quality and oscillator frequency variation.
- Environmental association studies with valid, time-aligned measurements.

Use [Host_Parser_State_Machine.md](Host_Parser_State_Machine.md) for readiness and
replay. Use [Clock_Swing_Analysis.md](Clock_Swing_Analysis.md) for current analysis
settings. Robust outlier exclusion must be labelled, and environmental
correlations alone do not establish causation.
