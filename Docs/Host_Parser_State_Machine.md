# Host Parser State Machine

This document describes the common readiness requirements and the implemented
recovery policies of the laptop capture tool and Pi acquisition service for the
[wire contract](Protocol_Wire_Contract.md). The state names below are conceptual;
the consumers do not expose one shared state-machine implementation. The retained
Uno has its own parser in `Uno.R4.Deprecated/src/NanoComm.cpp`.

## Common readiness requirements

| State         | Behavior                                                                                            |
| ------------- | --------------------------------------------------------------------------------------------------- |
| `WAIT_CFG`    | Preserve available raw evidence; await a supported CFG contract                                     |
| `WAIT_SCHEMA` | Await complete, supported CSW and CPS SCH declarations                                              |
| `READY`       | Accept valid capture rows, subject to the consumer's sequence policy                                |
| `RECOVER`     | Pause structured capture while restoring metadata, or end the laptop run on a fatal contract change |

Data acceptance requires supported `CFG` and valid `SCH` declarations for both
capture families. Both consumers can cache schemas before CFG. Neither
backfills pre-readiness captures into structured files. Valid rows have the
expected field count and decimal unsigned values within the
[wire field ranges](Protocol_Wire_Contract.md#pps-records). Both Python parsers
limit each capture value to ten digits.

`emit meta` emits mirrored `STS ... cfg`, standalone `CFG`, and both `SCH`
declarations. `emit startup` additionally replays boot/status metadata. The
Python consumers use standalone CFG/SCH for readiness; they do not require the
status-schema record. Identical metadata replay alone is idempotent. No consumer
should silently reinterpret existing CSV columns after a contract change.

## Consumer recovery policies

The Pi service is designed to rejoin a continuous deployment. The laptop tool
owns one output directory and stops on an incompatible or changed contract.
These distinctions are observable behaviour, not alternative interpretations of
the wire schema.

| Input or event                                   | Laptop capture tool                               | Pi acquisition service                                                               |
| ------------------------------------------------ | ------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Identical CFG and SCH replay                     | Preserve readiness and files                      | Preserve readiness, session and sequence tracking                                    |
| Supported CFG changes `nhz` or `fw`              | End run with an error                             | Open a new session, cache new CFG, await both schemas                                |
| Added or changed extra CFG keys                  | Preserve metadata; continue in same files         | Any dictionary change opens a new session and requires schemas again                 |
| Unsupported protocol or complete SCH declaration | End run with an error                             | Reject and clear readiness; start a new session if previously ready                  |
| Malformed CFG or SCH                             | Clear readiness; await valid CFG and both schemas | Clear readiness; start a new session if previously ready                             |
| Malformed CSW, CPS or STS                        | Count and skip; keep readiness                    | Count and skip; keep readiness                                                       |
| Unknown tag or ordinary text                     | Count as malformed and skip; keep readiness       | Unknown tag clears readiness; recognized command text is ignored after raw retention |
| Duplicate capture sequence                       | Write row unchanged                               | Count and exclude from structured captures                                           |
| Forward sequence gap                             | Write row unchanged                               | Record missing count and accept current row                                          |
| Backward/reordered sequence after readiness      | Write row unchanged                               | Open a new session and require fresh metadata; triggering row stays raw only         |
| Serial disconnect                                | End run; no reconnect                             | Recover and reopen serial connection; rejoin with metadata                           |
| Receiver queue overflow                          | No independent receive queue                      | Record loss and open a new session requiring fresh metadata                          |

The Pi interprets a modular sequence step of zero as a duplicate, `1..2^31-1`
as forward progress (including normal wrap), and `2^31..2^32-1` as a restart or
reorder. This is detected independently for CSW and CPS; a reset is not guaranteed
to be recognized if its first observed sequence looks forward. A Nano reset that
replays identical metadata alone does not rotate either consumer's recording.
The laptop preserves reset evidence in the original sequence/drop counters;
the Pi rotates once a backward sequence is observed.

Pi recognized text is an empty line or a line starting with `get:`, `set:`,
`reset:`, `ERROR:`, `help`, or `Usage:`. Other human help lines may therefore
trigger recovery. The laptop validates STS codes against the six wire tokens;
the Pi requires only a nonempty status code and preserves the payload.

## Framing and metadata limits

| Detail                           | Laptop capture tool                                                 | Pi acquisition service                                                                                 |
| -------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Text decoding                    | UTF-8; unsupported text is counted/skipped                          | ASCII; rejects NUL and embedded CR/LF                                                                  |
| CFG nominal frequency            | `1..4294967295` ticks/s                                             | `1000..4294967295` ticks/s; at most ten digits                                                         |
| CFG extra values                 | Nonempty values required                                            | Empty values allowed for extra keys                                                                    |
| Oversized input                  | More than 4096 bytes before LF clears readiness and is counted once | Framer emits bounded 2048-byte fragments; fragments are counted and skipped without clearing readiness |
| Incomplete final line            | Retained raw and counted as malformed                               | Retained as a fragment when transport closes                                                           |
| Initial serial line              | Parse if complete and valid                                         | Treat first line as a fragment even if apparently complete                                             |
| Metadata requests in serial mode | Immediately, then at about 3 and 6 seconds; fail at 10 seconds      | Immediately while unready, then every 5 seconds without a readiness deadline                           |
| Passive/replay operation         | `--passive` sends nothing and has no readiness deadline             | File replay sends nothing and uses file metadata                                                       |
| Reconnect delay                  | Not implemented                                                     | About 2 seconds between serial open attempts                                                           |

The laptop's 4096-byte check excludes LF but includes any preceding CR. The Pi
framer checks LF before its size bound: a line ending with LF as byte 2048 is a
complete frame; 2048 non-LF bytes begin discarded fragments until the next LF.
Pi queue capacity is configurable; a full queue can lose raw bytes as well as
structured rows. Pi raw retention also depends on logging/storage availability.
See [Pi recording guarantees](../Raspberry.Pi/DATA_FORMAT.md) for those limits.

## Example sequence

```text
STS,...build...
STS,...schema...
STS,...cfg...
CFG,pv=3,nhz=16000000,cst=CSW,css=canonical_swing_v2,cpt=CPS,cps=canonical_pps_v1,fw=<version>
SCH,CSW,canonical_swing_v2,seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing
SCH,CPS,canonical_pps_v1,seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps
CSW,...
CPS,...
```

A host attaching during a run may initially see data before metadata. Preserve
those lines as raw input and request `emit meta` to reach readiness. The bundled
capture tool requests this replay automatically unless `--passive` is selected.

## Bundled laptop capture tool

The [Nano capture tool](../tools/README.md) writes received bytes to
`wire_raw.log` before parsing, including malformed input and original line
endings. It counts malformed and pre-readiness rows and continues waiting.
Unknown tags are counted and skipped. Valid data rows must have the declared
field count and unsigned integer values within the firmware fields' ranges.

The policies and deadlines in the tables above apply throughout the run. After
a fatal error, start another recording in a new directory; the tool neither
changes output schemas nor creates sessions itself. The old reference logger and
ingest example have been replaced by this single entry point.

Persist protocol/schema IDs, nominal frequency and firmware identity alongside
each recording. Check sequence gaps and cumulative drop-counter changes as well
as host parse-error counts; no single counter reports every possible loss.

## Implementation and verification owners

- [`tools/nano_capture.py`](../tools/nano_capture.py): framing, readiness,
  fatal contract changes, output files and serial request deadline.
- [`Raspberry.Pi/pendulum_pi/protocol.py`](../Raspberry.Pi/pendulum_pi/protocol.py):
  decoding, contract comparison, framing and modular sequence classification.
- [`Raspberry.Pi/pendulum_pi/service.py`](../Raspberry.Pi/pendulum_pi/service.py):
  recovery, session boundaries, raw retention and sequence acceptance.
- [`Raspberry.Pi/pendulum_pi/transport.py`](../Raspberry.Pi/pendulum_pi/transport.py):
  metadata retries, reconnects and bounded receive queue.

Existing checks are in `tests/test_nano_capture.py`,
`tests/test_capture_protocol.py`, and `tests/raspberry_pi/test_acquisition.py`.
They complement code inspection; hardware reconnect and logging acceptance are
separate checks in [Pi hardware tests](../Raspberry.Pi/HARDWARE_TESTS.md).
