# Protocol Wire Contract (Normative)

## Source of truth

[Nano.Every/src/PendulumProtocol.h](../Nano.Every/src/PendulumProtocol.h) owns
record tags, schema IDs, field order, version constants and shared data models.
The Nano and receiving Uno must use byte-for-byte identical copies of this header.
`SerialParser.cpp` and `StatusTelemetry.cpp` own serialization. Receiver-specific
validation for the retained Uno belongs in its `PendulumProtocolReceiver.h`.
The Python consumers implement this same wire layout in
[`tools/nano_capture.py`](../tools/nano_capture.py) and
[`Raspberry.Pi/pendulum_pi/protocol.py`](../Raspberry.Pi/pendulum_pi/protocol.py);
consumer recovery policies are documented separately in the
[host parser guide](Host_Parser_State_Machine.md).

The firmware emits captured swing and PPS boundaries on a shared free-running
TCB0 counter. Duration calculation and PPS calibration belong to the receiving
host or offline analysis.

## Record families

| Tag   | Grammar                               | Purpose                                                |
| ----- | ------------------------------------- | ------------------------------------------------------ |
| `CFG` | `CFG,<key>=<value>(,<key>=<value>)*`  | Capture contract and firmware identity                 |
| `STS` | `STS,<status_code>[,<payload...>]`    | Boot, status, command replies and optional diagnostics |
| `SCH` | `SCH,<tag>,<schema_id>,<csv_fields>`  | Complete declaration of one capture record schema      |
| `CSW` | `CSW,<nine unsigned integer values>`  | Five edges of a completed swing and drop counters      |
| `CPS` | `CPS,<eight unsigned integer values>` | One PPS capture and its health/capture context         |

`CFG` and both `SCH` declarations are emitted at startup, delayed metadata replay,
optional automatic startup replay, and on demand. The commands `emit meta` and
`emit startup` replay metadata without restarting acquisition.

`STS` codes are `OK`, `UNKNOWN_COMMAND`, `INVALID_PARAM`, `INVALID_VALUE`,
`INTERNAL_ERROR`, and `PROGRESS_UPDATE`. Payloads commonly start with a family,
but command errors and progress messages may contain free text. Preserve the
payload without assuming every status has key/value fields.

## Swing records

`SCH,CSW,canonical_swing_v2,...` declares this exact field order:

```text
seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing
```

All nine fields are unsigned 32-bit integers (`0..4294967295`). `seq` increments for each assembled
swing, including rows lost to a full completed-swing ring. Adjacent swings share
the previous `edge4_tcb0` as the next `edge0_tcb0`.

Edges are projected onto TCB0 at capture time. Projection compensates capture
age, aligns the timer reads, and subtracts the configured input-filter delay
(four ticks for the default IR input). No host filter-delay correction is needed.
See [Capture_Timebase_Architecture.md](Capture_Timebase_Architecture.md).

`drop_ir`, `drop_pps`, and `drop_swing` are cumulative capture/assembly drop
counters. A failed serial emission leaves the oldest completed swing pending
for retry. A full swing ring drops a newly completed row and increments
`drop_swing`. Serial loss must also be checked through sequence continuity.

## PPS records

`SCH,CPS,canonical_pps_v1,...` declares this exact field order:

```text
seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps
```

`edge_tcb0` is the reconstructed shared-counter capture timestamp. `cap16` is
the local captured 16-bit counter, `latency16` its modular capture age, and
`now32` the shared timestamp aligned to the capture-counter read. The default
PPS input has no filter delay. `gps_status` is `0=NO_PPS`, `1=ACQUIRING`,
`2=LOCKED`, or `3=HOLDOVER` (an 8-bit enum restricted to these four values).
`cap16` and `latency16` are unsigned 16-bit integers (`0..65535`);
`seq`, `edge_tcb0`, `holdover_age_ms`, `now32`, and `drop_pps` are unsigned
32-bit integers (`0..4294967295`). The wire contains decimal ASCII values, not
the in-memory struct order or binary widths. Capture values are nonnegative
integer tokens without signs, decimal points or scientific notation.

Each queued raw capture is emitted before classification. GPS state and holdover
age therefore describe the preceding processing state; a row does not prove
that its interval was accepted. Rejected extra captures remain visible. Failed
PPS sends are not retried; `drop_pps` counts capture-ring insertion failures,
not serial losses.

The counter timestamps and sequence numbers wrap modulo 2^32. At 16 MHz a full
counter wrap takes about 268.4 seconds. Reconstruct chronology from ordered
records and sequence continuity; the shared counter is not UTC. `gps_status`
describes Nano PPS health, not the Pi's chrony/gpsd status or UTC accuracy.
See [Pi timekeeping](../Raspberry.Pi/TIMEKEEPING.md) for the separate host time
sources and the current limit on associating captures with UTC.

## Version constants

| Constant                       | Value                |
| ------------------------------ | -------------------- |
| `PROTOCOL_VERSION`             | `3`                  |
| `STS_SCHEMA_VERSION`           | `5`                  |
| `CANONICAL_SWING_SCHEMA_ID`    | `canonical_swing_v2` |
| `CANONICAL_PPS_SCHEMA_ID`      | `canonical_pps_v1`   |
| `PPS_TUNING_SEMANTICS_VERSION` | `2`                  |

Increment the protocol version for breaking record or metadata changes, the
status version for incompatible status payload changes, and a record's schema
ID whenever its field order or meaning changes incompatibly.

## CFG records and keys

| Key   | Meaning                                     |
| ----- | ------------------------------------------- |
| `pv`  | Protocol version (`3`)                      |
| `nhz` | Nominal timer frequency in ticks per second |
| `cst` | Swing tag (`CSW`)                           |
| `css` | Swing schema ID (`canonical_swing_v2`)      |
| `cpt` | PPS tag (`CPS`)                             |
| `cps` | PPS schema ID (`canonical_pps_v1`)          |
| `fw`  | Firmware version string                     |

At nominal 16 MHz, the configuration record has this form (`<version>` is the
build's `FW_VERSION`):

```text
CFG,pv=3,nhz=16000000,cst=CSW,css=canonical_swing_v2,cpt=CPS,cps=canonical_pps_v1,fw=<version>
```

`STS,PROGRESS_UPDATE,cfg,...` mirrors the same key/value payload.
`STS,PROGRESS_UPDATE,schema,sts=5,eeprom=<version>,css=canonical_swing_v2,cps=canonical_pps_v1`
reports the status, EEPROM and capture schema versions. Optional `STS ... build`
adds Git revision, build time, board and clock details.

Hosts must validate `CFG` and both `SCH` declarations before accepting data.
Replay of identical metadata is idempotent. A changed contract requires a new
capture session or explicit parser recovery. Unknown extra CFG keys may be
preserved as metadata. See [Host_Parser_State_Machine.md](Host_Parser_State_Machine.md).

## Wire records versus recording files

`SCH` declares the Nano's `CSW`/`CPS` fields only. It does not require every
receiver's CSV file to have identical columns. The laptop capture tool writes
those fields directly to `PCSW.CSV` and `PCPS.CSV`. The Pi appends environmental
columns and retains empty `adj_diag`/`adj_comp_diag` compatibility columns in
`PCSW.CSV`; those values are absent from protocol v3. The retained Uno appends
its environmental fields to the capture schema. File schemas and missing-value
semantics belong to the [laptop tool guide](../tools/README.md) and
[Pi recording format](../Raspberry.Pi/DATA_FORMAT.md), not this wire contract.
