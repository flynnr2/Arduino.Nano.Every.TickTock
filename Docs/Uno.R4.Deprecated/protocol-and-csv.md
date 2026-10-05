# Protocol and CSV schema

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

The shared [wire contract](../Protocol_Wire_Contract.md) is implemented by
`Nano.Every/src/PendulumProtocol.h` and its byte-for-byte identical Uno copy,
`Uno.R4.Deprecated/src/PendulumProtocol.h`. The receiver accepts protocol version **3** and
reports STS schema version **5**. Deploy matching Nano and Uno firmware together;
earlier capture formats are unsupported.

## Wire records

| Tag   | Payload                             | Purpose                             |
| ----- | ----------------------------------- | ----------------------------------- |
| `CFG` | `key=value` pairs                   | Session contract and firmware ID    |
| `SCH` | `<tag>,<schema_id>,<csv_fields>`    | Complete capture schema declaration |
| `CSW` | Nine unsigned 32-bit decimal fields | Completed swing capture             |
| `CPS` | Eight unsigned decimal fields       | PPS capture and health context      |
| `STS` | `<status_code>[,<payload...>]`      | Status and command acknowledgements |

The required CFG keys are `pv`, `nhz`, `cst`, `css`, `cpt`, `cps`, and `fw`. Their expected values are `3`, a nominal counter frequency of at least 1000 ticks/s, `CSW`, `canonical_swing_v2`, `CPS`, `canonical_pps_v1`, and the nonempty firmware version string. A 16 MHz example is:

```text
CFG,pv=3,nhz=16000000,cst=CSW,css=canonical_swing_v2,cpt=CPS,cps=canonical_pps_v1,fw=0.0.0-dev
```

The receiver accepts captures only after a valid complete CFG and both exact SCH declarations. Identical metadata replays preserve its counters and latest samples. Conflicting metadata halts the recording. An incomplete or invalid initial contract keeps the receiver waiting while it requests `emit startup` asynchronously, every 500 ms initially and every five seconds after 6.5 seconds. Metadata replay does not replay missed captures.

## Capture schemas

`SCH,CSW,canonical_swing_v2,...` declares:

```text
seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing
```

`SCH,CPS,canonical_pps_v1,...` declares:

```text
seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps
```

Every edge timestamp is on the Nano's shared free-running 32-bit counter. The Nano has already projected captures onto that counter and compensated input-filter delay. Subtract timestamps modulo 2³² to obtain durations. `cap16`, `latency16`, and `now32` retain capture context; they are not alternate timebases. GPS status values are `0=NO_PPS`, `1=ACQUIRING`, `2=LOCKED`, and `3=HOLDOVER`.

Swing and PPS sequences wrap modulo 2³². The first observed value is a join point. Forward gaps indicate missing serial records; backward changes are reported as a restart or reorder and prompt metadata replay. `drop_ir`, `drop_pps`, and `drop_swing` are cumulative Nano counters. Their increases indicate Nano-side loss even when sequences are contiguous; a counter reset establishes a new baseline. PPS calibration and duration calculations are performed by the receiver's display estimator or offline analysis. Raw captures are stored unchanged.

## CSV storage

The Uno stores PCSW, PCPS, UNO diagnostics, and STS diagnostics as a four-file set. PCSW and PCPS headers use their declared field order plus `temperature_C,humidity_pct,pressure_hPa`. The wire tag is not a CSV column. Environment values are sampled by the Uno when each capture is logged.

A cold Append or Archive start selects a new four-file set if the target already exists, so a different firmware contract cannot join an older recording. Remount recovery can resume the selected set after checking for partial tails and size limits. The UNO diagnostic file records the validated protocol, nominal frequency, firmware ID, and both schema identifiers.
