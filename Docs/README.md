# Documentation Map

This directory documents the Nano Every pendulum timer firmware/repo. Code is the final source of truth, but selected documents below are intended to serve as normative contracts for host integrations.

## Start here

- I want to understand the serial stream emitted by the Nano → [`Protocol_Wire_Contract.md`](Protocol_Wire_Contract.md)
- I want to write or debug a host parser → [`Protocol_Wire_Contract.md`](Protocol_Wire_Contract.md) + [`Host_Parser_State_Machine.md`](Host_Parser_State_Machine.md)
- I want to understand the firmware structure → [`Implementation_Overview.md`](Implementation_Overview.md)
- I want to understand capture timing / TCB / EVSYS → [`Capture_Timebase_Architecture.md`](Capture_Timebase_Architecture.md)
- I want to understand PPS disciplining → [`PPS_Discipliner_Guide.md`](PPS_Discipliner_Guide.md)
- I want to understand compile-time flags → [`Config_Defines_Guide.md`](Config_Defines_Guide.md)
- I want to understand CLI commands → [`Command_Interface_Contract.md`](Command_Interface_Contract.md)
- I want to understand pendulum data rows / analysis semantics → [`Pendulum_Data_Record_Guide.md`](Pendulum_Data_Record_Guide.md) and [`Pendulum_CSV_Semantics.md`](Pendulum_CSV_Semantics.md)

## Document status

### Normative-ish contracts
- [`Protocol_Wire_Contract.md`](Protocol_Wire_Contract.md)
- [`Command_Interface_Contract.md`](Command_Interface_Contract.md)
- [`Host_Parser_State_Machine.md`](Host_Parser_State_Machine.md) (recommended robust host behavior derived from the wire contract)

### Implementation guides
- [`Implementation_Overview.md`](Implementation_Overview.md)
- [`Capture_Timebase_Architecture.md`](Capture_Timebase_Architecture.md)
- [`PPS_Discipliner_Guide.md`](PPS_Discipliner_Guide.md)
- [`Config_Defines_Guide.md`](Config_Defines_Guide.md)
- [`Emit_Mode_Guide.md`](Emit_Mode_Guide.md)

### Data/analysis semantics
- [`Pendulum_CSV_Semantics.md`](Pendulum_CSV_Semantics.md)
- [`Pendulum_Data_Record_Guide.md`](Pendulum_Data_Record_Guide.md)

### Design decisions
- [`Memory_and_Telemetry_Budget.md`](Memory_and_Telemetry_Budget.md)

### Notes/history
- [`notes/`](notes/)

### Reference material
- [`datasheets/`](datasheets/)
- [`pdfs/`](pdfs/)

### Prompt material
- [`prompts_analysis/`](prompts_analysis/)
- [`prompts_codex/`](prompts_codex/)

## Ownership boundaries

- `Protocol_Wire_Contract.md` owns emitted record/schema definitions and CFG keys.
- `Emit_Mode_Guide.md` explains why/when CANONICAL vs DERIVED modes matter; it is not the canonical schema source.
- `Host_Parser_State_Machine.md` owns recommended robust host-parser behavior.
- `Implementation_Overview.md` owns source/module architecture, not detailed schema tables.
- `Pendulum_CSV_Semantics.md` owns analysis intent and user-facing CSV interpretation; it does not override the wire contract.

## Maintenance note

When a schema, command, config flag, or emitted record changes, update the owning document in the same PR. Avoid duplicating full schema tables across multiple docs.
