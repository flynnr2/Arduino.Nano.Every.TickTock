# Short-run logging and OLED investigation — 12 September 2026

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

## Sources and scope

Read-only review of `/Users/richardflynn/git/Arduino.Pendulum.Timer/Data/20060912_shortrun`.
All eight source CSV files match the SHA-256 hashes in the existing analysis package's
analysis_20260912_anfebruy/provenance.json (a historical generated output, not a repository file). No source files were renamed or edited.
The directory name is preserved as supplied. The UNO logs identify the running build
as 11 September 2026, 09:43:38; this predates the OLED restoration in the current code.

The stream sequence numbers in each UNO startup record match the corresponding first
measurement records, supporting this pairing:

| Run | Swing / PPS                 | Diagnostics         | Recorded measurements                                 |
| --- | --------------------------- | ------------------- | ----------------------------------------------------- |
| 1   | PCSW.CSV / PCPS.CSV         | UNO.CSV / STS.CSV   | 752 swings, 1,503 PPS captures; about 25 minutes      |
| 2   | S0000001.CSV / P0000001.CSV | UNO1.CSV / STS1.CSV | 39,300 swings, 78,586 PPS captures; about 21 h 50 min |

## Main finding: both environmental sensors failed together

| Run | First BMP280 read failure         | SHT4x first failure | Both offline by |
| --- | --------------------------------- | ------------------- | --------------- |
| 1   | uptime 1,165,785 ms (19 min 26 s) | 1,166,703 ms        | 1,168,864 ms    |
| 2   | uptime 3,286,728 ms (54 min 47 s) | 3,287,435 ms        | 3,289,533 ms    |

Run 2's first failure is logged at 11 September 2026 11:32:32 UTC (12:32:32 BST).
In both runs, both sensors transition through degraded/stale to offline within a few
seconds. There are no subsequent ready/recovery transitions. In run 2, all three
environmental fields are missing from swing sequence 3,679 onward: 37,655 records,
with no valid environmental readings returning. This is explicitly missing data,
not a sensor value merely staying numerically constant.

BMP280, SHT4x and the OLED share the UNO's Wire/I²C bus. This makes a shared bus,
power or wiring fault the leading hypothesis for an OLED that stopped changing while
serial logging continued. A driver/bus-state problem or memory corruption could also
affect that shared path. The logs do not identify which device or electrical/software
condition initiated the failure, and they do not record the OLED failure time.

## Memory evidence

| Run | Sampled free-RAM changes (uptime ms → bytes)                            | Lowest recorded minimum |
| --- | ----------------------------------------------------------------------- | ----------------------- |
| 1   | 10,060 → 6,231; 60,321 → 5,187; 70,354 → 4,215                          | 4,215 bytes             |
| 2   | 10,072 → 6,219; 3,283,407 → 6,095; 4,518,279 → 5,051; 4,528,340 → 4,079 | 4,079 bytes             |

All 149 run-1 and 7,821 run-2 memory-state records are `ok`. The warning threshold is
4,000 bytes and critical threshold 2,000 bytes. Run 2 eventually has only 79 bytes
above the warning threshold, but stays at 4,079 for approximately 20.56 hours.
The later approximately 2 KB reduction occurs after the sensor failures; it cannot
explain their onset. A smaller 124-byte reduction occurs about 3.3 seconds before
the first sensor failure; the logs do not attribute that allocation.

This is limited headroom, not evidence of a continuing leak or recorded exhaustion.
`MemoryMonitor` samples the gap between its current stack position and `sbrk(0)`.
It does not measure deepest transient stack use, largest allocatable heap block,
heap fragmentation or memory corruption. The minimum is the minimum of these
samples, not a hardware high-water measurement. Thus memory-related failure is not
ruled out by the `ok` label.

## Capture and service continuity

- Each of the four measurement files has consecutive sequences throughout: zero
  internal gaps, duplicates or reversals. All exported Nano drop counters stay zero.
  The interval between the two separate runs is not counted as continuous coverage.
- Every recorded PPS has GPS state LOCKED. This alone is not an independent check
  of pulse timing accuracy.
- All eight files have consistent CSV field counts and complete newline-terminated
  tails; no malformed rows were found.
- Memory telemetry continues to the end. The largest gap between successive free-RAM
  records is 10.112 seconds in run 1 and 10.119 seconds in run 2, close to its normal
  ten-second cadence. A persistent whole-main-loop hang is inconsistent with this.
- Run 2 contains 1,309 periodic HTTP listener retry messages, without a later network
  disconnect transition. These recurring messages alone do not establish a fault.
- The watchdog startup record says it was disabled because `WDT.h` was unavailable.
- There are no OLED attempt/completion/error counters or backlog/service heartbeat
  records. OLED refresh is skipped while ingestion has pending work; it remains a
  possible alternative explanation, but the logs cannot establish display starvation.

## Filename correction

The previous code used unrelated patterns: PCSW/PCPS for initial canonical files,
S/P plus seven digits for subsequent measurement files, and unpadded UNO/STS numbers
for diagnostics. These are segment names, not trustworthy session identifiers.

New automatically allocated segments preserve the stream name and use a four-digit
suffix: `PCSW0001.CSV`, `PCPS0001.CSV`, `PEND0001.CSV`, `UNO0001.CSV`, `STS0001.CSV`.
These fit FAT 8.3. Initial base names and date-based daily names remain supported.
Existing legacy files are recognized and preserved under archive/fresh-start policy;
measurement numbering skips occupied legacy numbers as well as new names.
Diagnostics retain their bounded ring policy, so their suffix is not a session ID
and can diverge from measurement suffixes after independent rotations.

## Next diagnostic step

Inspect the shared I²C wiring, power and connections around the three peripherals.
On the next instrumented run, record OLED refresh attempts/completions and periodic
I²C device reachability alongside sensor health, without performing a scan on every
refresh. This would distinguish skipped drawing from a device/bus communication
failure. No OLED recovery behaviour or electrical cause is claimed fixed here.
