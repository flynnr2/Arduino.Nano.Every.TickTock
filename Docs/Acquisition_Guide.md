# Acquisition Guide

The firmware emits captured swing and GPS PPS timestamps on one free-running
TCB0 timeline. This is the canonical acquisition format.

Hosts read `CFG` metadata, validate `SCH` declarations, and record `CSW` and
`CPS` rows. They calculate component durations and calibrate counter time against
PPS in the receiver or offline analysis. Capture projection already compensates
ISR latency and the configured input-filter delay.

- [Protocol_Wire_Contract.md](Protocol_Wire_Contract.md) defines exact tags and schemas.
- [Host_Parser_State_Machine.md](Host_Parser_State_Machine.md) defines readiness and replay.
- [Pendulum_Data_Record_Guide.md](Pendulum_Data_Record_Guide.md) explains interpretation.
- [Clock_Swing_Analysis.md](Clock_Swing_Analysis.md) documents offline analysis.
