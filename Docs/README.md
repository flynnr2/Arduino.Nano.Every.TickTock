# Documentation Index

Status: Canonical documentation map.

This directory is the entry point for maintained project documentation. Implementation files remain the final authority for behavior; these documents define the maintained contracts, operating guides, and historical boundaries.

## Start Here

- Complete system layout: [repository README](../README.md)
- Minimal laptop serial capture: [Nano capture tool](../tools/README.md)
- Development environments, builds and checks: [Development_and_Validation.md](Development_and_Validation.md)
- Deprecated Uno receiver setup and documentation: [Uno.R4.Deprecated/README.md](../Uno.R4.Deprecated/README.md)
- Raspberry Pi receiver and quickstart: [Raspberry.Pi/README.md](../Raspberry.Pi/README.md)
- Pi OS Lite setup checklist: [Raspberry.Pi/SETUP_CHECKLIST.md](../Raspberry.Pi/SETUP_CHECKLIST.md)
- Pi deployment: [Raspberry.Pi/INSTALL.md](../Raspberry.Pi/INSTALL.md)
- Pi settings, defaults and reload rules: [Raspberry.Pi/CONFIGURATION.md](../Raspberry.Pi/CONFIGURATION.md)
- Pi HTTP routes and command lifecycle: [Raspberry.Pi/HTTP_API.md](../Raspberry.Pi/HTTP_API.md)
- Pi live views and measurement interpretation: [Raspberry.Pi/OBSERVATORY.md](../Raspberry.Pi/OBSERVATORY.md)
- Pi wiring, voltage domains and confirmed photogate: [Raspberry.Pi/WIRING.md](../Raspberry.Pi/WIRING.md)
- Pi recording format and recovery: [Raspberry.Pi/DATA_FORMAT.md](../Raspberry.Pi/DATA_FORMAT.md)
- Pi GPS/PPS UTC and chrony deployment plan: [Raspberry.Pi/TIMEKEEPING.md](../Raspberry.Pi/TIMEKEEPING.md)
- Pi hardware acceptance: [Raspberry.Pi/HARDWARE_TESTS.md](../Raspberry.Pi/HARDWARE_TESTS.md)
- Uno source import: [Uno.R4.Deprecated/IMPORT.md](Uno.R4.Deprecated/IMPORT.md)
- Protocol v3 migration context and consumer formats: [Receiver_Protocol_v3.md](Receiver_Protocol_v3.md)
- Acquisition responsibilities: [Acquisition_Guide.md](Acquisition_Guide.md)
- Wire protocol and emitted records: [Protocol_Wire_Contract.md](Protocol_Wire_Contract.md)
- Pendulum record semantics: [Pendulum_Data_Record_Guide.md](Pendulum_Data_Record_Guide.md)
- Clock and swing analysis, phase charts and validation: [Clock_Swing_Analysis.md](Clock_Swing_Analysis.md)
- Capture latency rails, shoulders and contention: [Capture_Latency_Shape.md](Capture_Latency_Shape.md)
- Historical analysis contracts: [Analysis_Package.md](Analysis_Package.md), [PPS_Analysis.md](PPS_Analysis.md)
- Firmware architecture: [Implementation_Overview.md](Implementation_Overview.md)
- Host parser behavior: [Host_Parser_State_Machine.md](Host_Parser_State_Machine.md)
- Command interface: [Command_Interface_Contract.md](Command_Interface_Contract.md)
- Compile-time configuration: [Config_Defines_Guide.md](Config_Defines_Guide.md)
- Wiring: [Wiring.md](Wiring.md)

## Canonical Owners

| Topic                                                               | Owner                                                                                                                      | Status                     |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| Pi recordings, summaries, manifests and receiver recovery           | [Raspberry.Pi/DATA_FORMAT.md](../Raspberry.Pi/DATA_FORMAT.md)                                                              | Canonical                  |
| Pi rotation, archive, retention and export policy                   | [Raspberry.Pi/STORAGE.md](../Raspberry.Pi/STORAGE.md)                                                                      | Canonical                  |
| Pi settings, defaults, validation and reload                        | [Raspberry.Pi/CONFIGURATION.md](../Raspberry.Pi/CONFIGURATION.md)                                                          | Reference                  |
| Pi HTTP requests, responses and access                              | [Raspberry.Pi/HTTP_API.md](../Raspberry.Pi/HTTP_API.md)                                                                    | Reference                  |
| Pi dashboard estimates and live views                               | [Raspberry.Pi/OBSERVATORY.md](../Raspberry.Pi/OBSERVATORY.md)                                                              | Operating guide            |
| Pi UTC/GPS monitoring and OS commissioning                          | [Raspberry.Pi/TIMEKEEPING.md](../Raspberry.Pi/TIMEKEEPING.md)                                                              | Operating guide            |
| Pi installation, service management and receiver wiring             | [Raspberry.Pi/INSTALL.md](../Raspberry.Pi/INSTALL.md), [Raspberry.Pi/WIRING.md](../Raspberry.Pi/WIRING.md)                 | Operating guide            |
| Serial wire records, tags, schema versions, CFG keys                | [Protocol_Wire_Contract.md](Protocol_Wire_Contract.md)                                                                     | Normative                  |
| Pendulum row interpretation and common pitfalls                     | [Pendulum_Data_Record_Guide.md](Pendulum_Data_Record_Guide.md)                                                             | Normative                  |
| Python clock and swing suite, outputs, phase mapping and validation | [Clock_Swing_Analysis.md](Clock_Swing_Analysis.md)                                                                         | Canonical                  |
| High-level firmware module architecture                             | [Implementation_Overview.md](Implementation_Overview.md)                                                                   | Informational              |
| Common readiness and consumer-specific parser policies              | [Host_Parser_State_Machine.md](Host_Parser_State_Machine.md)                                                               | Implemented host contracts |
| Runtime command grammar and acknowledgements                        | [Command_Interface_Contract.md](Command_Interface_Contract.md)                                                             | Normative                  |
| Build-time defines and defaults                                     | [Config_Defines_Guide.md](Config_Defines_Guide.md)                                                                         | Normative                  |
| Capture timer, EVSYS, PPS discipliner details                       | [Capture_Timebase_Architecture.md](Capture_Timebase_Architecture.md), [PPS_Discipliner_Guide.md](PPS_Discipliner_Guide.md) | Informational              |
| Memory/telemetry design decision                                    | [Memory_and_Telemetry_Budget.md](Memory_and_Telemetry_Budget.md)                                                           | Accepted ADR               |
| Development setup, test entry points and build prerequisites        | [Development_and_Validation.md](Development_and_Validation.md)                                                             | Operating guide            |
| Historical material and prompt-removal note                         | [Historical/](Historical/)                                                                                                 | Historical, non-normative  |

## Navigation Rules

- README files orient; they do not own detailed contracts.
- Protocol field definitions live only in [Protocol_Wire_Contract.md](Protocol_Wire_Contract.md).
- Current analysis behavior lives in [Clock_Swing_Analysis.md](Clock_Swing_Analysis.md), implemented in `pendulum_analysis/suite/`. Older analysis guides are historical references.
- Historical material may explain context, but it does not override maintained docs or code.
- Link to an owner instead of copying its tables or explanations.
- Wire declarations do not define every on-disk CSV: laptop, Pi and deprecated
  Uno output formats have separate owners. Link the applicable recording guide.
- Mark plans, implemented software, deployment configuration and physical
  acceptance separately. A software implementation or passing host test is not
  evidence that a device has been commissioned.

## Documentation Governance

When code changes alter public behavior, update the owning document in the same change. Keep implementation details in source, design rationale in docs, and historical context under [Historical/](Historical/).

Before adding a new document, check whether the topic already has an owner. Prefer extending the owner, shortening duplicate material, or linking to the owner. Run the local documentation audit before review:

```bash
python3 scripts/doc_audit.py
```

The audit does not verify prose against code, section anchors, image links or
table alignment, and its tracked-file checks omit new untracked documents.
See [audit limitations and known local findings](Development_and_Validation.md#documentation-verification).
Record actual failures and their disposition; do not describe an audit as
passing when local artifacts or deployment-path false positives remain.

Review each documentation change against this checklist:

- Identify the implemented source of truth and the relevant owner above.
  Use the working tree being delivered, including intentional pending changes.
- For Nano record changes, review the wire contract, shared header consumers,
  host readiness policies and each recording format. Preserve historical schemas
  as historical; do not silently rewrite old evidence.
- For Nano command/tunable changes, review the command contract, configuration
  guide and PPS reference, including acknowledgements, persistence, rollback and
  build-policy gates. Search overviews and Pi command guidance for stale copies.
- For Pi settings/routes/data changes, review CONFIGURATION, HTTP_API and
  DATA_FORMAT, then STORAGE or OBSERVATORY where semantics change. Keep detailed
  rules with their owner and use links in README/installation guidance.
- For deployment changes, review INSTALL, both wiring guides, TIMEKEEPING,
  HARDWARE_TESTS and the current status of plans. Preserve unperformed acceptance
  checks and distinguish OS configuration from implemented monitoring.
- For analysis changes, review Clock_Swing_Analysis and the supported CLI's
  actual options. Keep historical entry points explicitly labelled and ensure
  fresh-checkout examples do not depend on private `Data/` recordings.
- Exercise relevant documented commands with their stated prerequisites.
  Record environment, source revision, local modifications, results and skips;
  use existing focused tests rather than adding tests that merely mirror prose.
- Check local links, section anchors, referenced images and newly added files.
  Align raw Markdown table separators, preserve alignment colons, and use
  leading/trailing pipes with padded cells.
- Update the Unreleased changelog for current delivered behaviour. Historical
  provenance and measured build/test results remain attached to their snapshot.

Documentation-only work must leave implementation, tests, build settings and
deployment scripts untouched. Verify that boundary against the starting working
tree rather than assuming all pre-existing uncommitted changes belong to the
documentation change.
