# Receiver migration to protocol version 3

Status: migration reference for older deployments. The current Nano, Pi receiver,
laptop capture tool, and retained Uno receiver already implement protocol v3.
Use the [wire contract](Protocol_Wire_Contract.md) for the current schema and the
[host parser guide](Host_Parser_State_Machine.md) for consumer recovery policies.
This note describes what changed from older wire formats; it is not a statement
that receiver implementation is still pending.

## Changes required in an older receiver

- Accept `pv=3`; the emitted status schema is `sts=5`.
- Parse `CSW` and `CPS` captures, each declared by a complete `SCH` line.
- Expect `canonical_swing_v2` with nine fields: sequence, five edge timestamps,
  and the three cumulative drop counters. The wire no longer carries the two
  adjustment diagnostics (`adj_diag`, `adj_comp_diag`).
- Keep `canonical_pps_v1` and its existing eight-field layout.
- Read CFG keys `pv`, `nhz`, `cst`, `css`, `cpt`, `cps`, and `fw`. The current
  configuration emits the PPS schema and firmware version. Do not require the
  obsolete CFG keys `st`, `ss`, `asv`, `hm`, and `em`.
- Remove dependencies on mode selection, adjusted samples, segmented header
  assembly and adjustment-semantics metadata.
- Wait for supported CFG and both complete SCH declarations before accepting
  captures. Identical metadata replay alone must not restart a recording.
- Calculate durations and PPS calibration from raw shared-counter timestamps.
  Preserve wrap, sequence and cumulative-drop semantics.

For the retained Uno, keep
[`Nano.Every/src/PendulumProtocol.h`](../Nano.Every/src/PendulumProtocol.h) and
[`Uno.R4.Deprecated/src/PendulumProtocol.h`](../Uno.R4.Deprecated/src/PendulumProtocol.h)
byte-for-byte identical. Receiver parsing lives separately in
`Uno.R4.Deprecated/src/PendulumProtocolReceiver.h` and `NanoComm.cpp`.
The Pi and laptop parsers express the same wire contract in Python; they do not
consume the C++ header at runtime.

## Receiver file formats

Removing wire fields does not require removal of compatibility columns from
all existing CSV formats:

| Consumer                       | PCSW storage                                                                                | PCPS storage                                         |
| ------------------------------ | ------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| Laptop `tools/nano_capture.py` | Nine declared swing fields                                                                  | Eight declared PPS fields                            |
| Pi receiver                    | Nine declared swing fields, empty `adj_diag` and `adj_comp_diag`, then environmental fields | Eight declared PPS fields, then environmental fields |
| Retained Uno receiver          | Declared swing fields, then environmental fields                                            | Declared PPS fields, then environmental fields       |

The Pi's blank compatibility cells mean unavailable, not zero. Do not reinterpret
them as Nano adjustment measurements. Exact filenames, headers and missing-value
rules are owned by the [laptop guide](../tools/README.md),
[Pi data format](../Raspberry.Pi/DATA_FORMAT.md), and
[retained Uno guide](../Uno.R4.Deprecated/README.md).

There is no compatibility negotiation with older protocol versions. Deploy
matching emitter and receiver versions and start a new recording. The laptop
ends its run on an unsupported/changed contract; the Pi pauses and requests fresh
metadata, opening a new session where required. See the host parser guide for
other differences, including unknown tags and Nano reset detection.
