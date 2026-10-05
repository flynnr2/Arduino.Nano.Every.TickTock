# PPS latency at swing completion

Status: historical investigation of the 169-byte `FullSwing` implementation and
its interrupt-mask fix. The current reduced `FullSwing` has six uint32 values
(24 bytes); preserve the measurements below as evidence for their original
snapshot. See [Implementation_Overview.md](Implementation_Overview.md) for current
ownership and [Development_and_Validation.md](Development_and_Validation.md) for
current checks. The cited `Data/` recording is not distributed with the repository.

## Result

The rare PPS latency spikes in `Data/67Days` are caused by
`swingAssemblerTryPeekOldest()` copying a 169-byte `FullSwing` while global
interrupts are disabled. The timing match is exact: the generated AVRxt
critical section is 1,171 CPU cycles, while the measured maximum rises by
1,171 cycles from the 97-cycle normal mode to 1,268 cycles.

The fix keeps only the swing-ring index snapshot inside the atomic block and
copies the selected row after interrupts have been restored. The generated
critical section is now 15 cycles. Swing row retry and retirement semantics are
unchanged.

## Evidence from the 67-day capture

The retained `STS.CSV` identifies the relevant runtime configuration:

- ATmega4809 / Nano Every at 16 MHz from external main clock
- canonical `CSW` and `CPS` emission
- `ppsTuning=0` and `ppsBase=0`
- periodic flush and periodic serial diagnostics disabled

`PCPS.CSV` contains 5,820,076 PPS rows. Of these, 132 have `latency16 > 485`
cycles. The excursions range from 486 to 1,268 cycles, with a mean of 899.4
cycles. The maximum is 79.25 us at 16 MHz. The common normal value is 97 cycles
(6.0625 us).

All 132 excursions occur 0.697-0.990 ms after a `PCSW.edge4_tcb0` terminal
swing edge. Normal rows retain the expected capture/timestamp relation,
including `(cap16 - edge_tcb0) mod 65536 = 8`; intervals following the spikes
remain normal. The phase lock to `edge4`, rather than to serial or periodic
telemetry cadence, identifies the swing-completion path.

No reported queue-drop counters increased over the run:

- maximum `PCPS.drop_pps`: 0
- maximum `PCSW.drop_ir`: 0
- maximum `PCSW.drop_pps`: 0
- maximum `PCSW.drop_swing`: 0

These counters cover firmware ring insertion failures. They do not detect a
hardware capture register being overwritten by a second edge before the ISR
reads the first capture, so they cannot by themselves prove that every
electrical edge was captured.

## Responsible execution path

The foreground loop processes PPS records before swing records, then follows
this sequence:

1. `swingAssemblerProcessEdges()` consumes the terminal IR event (`edge4`).
2. It finishes interval adjustment and copies the completed row into
   `swing_buf` through `swing_push()`.
3. It returns to `pendulumLoop()`.
4. `swingAssemblerTryPeekOldest()` copies that row to its local `FullSwing`.
5. The local row is formatted and transmitted, then retired after a successful
   send.

The copy in step 2 is ordinary foreground code and does not mask interrupts.
The old copy in step 4 was inside `ATOMIC_BLOCK(ATOMIC_RESTORESTATE)`. PPS or IR
captures arriving during it were latched by hardware, but their ISRs could not
run until the entire row copy and compiler-hoisted field loads had completed.

The generated pre-fix code contains:

```text
cli
lds   swing_tail
lds   swing_head
...
ldi   r19, 0xA9       ; sizeof(FullSwing) = 169
copy_loop:
ld    r0, Z+
st    X+, r0
dec   r19
brne  copy_loop
...                   ; compiler-hoisted consumers of the copied row
out   SREG, saved
```

For a nonempty queue this is 786 executed instructions after expanding the
169-iteration copy loop. Using the ATmega4809's AVRxt instruction timings, the
interrupt-disabled path is 1,171 cycles, or 73.1875 us at 16 MHz. This exactly
accounts for the observed maximum:

```text
97-cycle normal service + 1,171-cycle delayed service = 1,268 cycles
```

The instruction timing source is Microchip's
[AVR Instruction Set Manual](https://onlinedocs.microchip.com/oxy/GUID-0B644D8F-67E7-49E6-82C9-1B2B9ABE6A0D-en-US-23/GUID-BA59618D-4850-490B-B176-0BCC3D9438A1.html).
The ATmega4808/4809 data sheet identifies this MCU as an AVRxt device.

## Why other paths do not explain these spikes

- Canonical row formatting and serial writes occur later in the foreground
  loop with interrupts enabled. They can delay main-loop work, but cannot add
  capture-to-ISR latency.
- PPS baseline and tuning telemetry were disabled in the retained build
  configuration.
- PPS validation, frequency discipline, and adjustment run in foreground code
  without an interrupt-disabled region of this size.
- Swing retirement masks interrupts only around byte-sized head/tail work.
- Swing diagnostic counters use short atomic 32-bit reads or increments.
- `swing_push()` copies the same large structure, but runs with interrupts
  enabled.

## Fix and ownership proof

`swing_buf`, `swing_head`, and `swing_tail` have one execution owner: the
foreground loop. `swingAssemblerProcessEdges()` is the only producer, and
`PendulumCore` is the only consumer. Capture ISRs publish `EdgeEvent` and
`PpsCapture` records into separate capture rings; they do not read or write the
swing ring.

The corrected peek therefore does the following:

1. Atomically compare `swing_tail` with `swing_head` and snapshot
   `swing_tail`.
2. Restore interrupts.
3. Copy `swing_buf[tail_snapshot]` into the caller's output.

No ISR can modify the selected slot during step 3. No other foreground work
can run concurrently, and this same caller does not retire the slot until after
the copy and successful emission. If emission fails, the row remains pending
exactly as before.

Post-fix disassembly shows the nonempty atomic path contains only eight
instructions:

```text
cli
lds   swing_tail
lds   swing_head
cp
brne
lds   swing_tail
ldi   peeked, 1
out   SREG, saved
```

This is 15 AVRxt cycles (0.9375 us at 16 MHz), after which the 169-byte copy
runs with interrupts enabled. The change therefore reduces this path's maximum
isolated interrupt-mask contribution by 1,156 cycles. It does not impose a
global `latency16` ceiling: ISR priority/stacking and other interrupt-disabled
paths can also delay service. The 97-cycle mode belongs to the historical
firmware and must be remeasured after the separate capture-path changes.

## Capture and queue safety bounds

For the recorded nominal event spacings, the old maximum delay ended well
before the next expected physical event:

- The PPS period is about one second, over 12,600 times the 79.25 us maximum,
  so the next nominal PPS edge cannot overwrite this capture.
- The shortest adjacent IR edge interval in `PCSW.CSV` is 361,710 cycles
  (22.607 ms), over 285 times the largest observed PPS service latency.
- TCB0 overflows every 65,536 cycles (4.096 ms), so at most one TCB0 overflow
  can become pending during the old critical section.
- The IR event ring holds 32 entries, the PPS ring holds 8, and the completed
  swing ring holds 8. A single delayed service interval cannot exhaust these
  capacities at the observed input rates.

The zero drop counters show that the firmware reported no IR, PPS, or completed
swing ring overflow in the 67-day run. They do not rule out one-deep hardware
capture overwrite. The observed physical edge spacing makes overwrite from
this specific 79.25 us delay implausible, but an unrecorded extra electrical
edge or glitch cannot be excluded from these files alone. The fix reduces the
service-delay risk; it does not change ring capacity, overflow policy, capture
math, or wire schemas.

## Build verification

Both binaries were built for `arduino:megaavr:nona4809` with the same Arduino
megaAVR 1.8.8 toolchain and inspected after link-time optimization.

| Measurement               | Before       | After        |
| ------------------------- | -----------: | -----------: |
| `sizeof(FullSwing)`       | 169 bytes    | 169 bytes    |
| nonempty peek atomic path | 1,171 cycles | 15 cycles    |
| time at 16 MHz            | 73.1875 us   | 0.9375 us    |
| sketch flash              | 46,386 bytes | 46,250 bytes |
| global SRAM               | 3,665 bytes  | 3,665 bytes  |

The fixed firmware compiles successfully. In controlled builds from the same
working tree, moving the copy out of the atomic block reduces linked flash by
136 bytes; SRAM usage is unchanged.

### Reproducing the linked-code check

Build into a temporary directory, disassemble the linked ELF, and print the
critical section from `cli` through the matching SREG restore:

```bash
arduino-cli compile \
  --fqbn arduino:megaavr:nona4809 \
  --build-path /private/tmp/pendulum-swing-cycle-check \
  Nano.Every

AVR_OBJDUMP=/Users/richardflynn/Library/Arduino15/packages/arduino/tools/avr-gcc/7.3.0-atmel3.6.1-arduino7/bin/avr-objdump
"$AVR_OBJDUMP" -d -C -S \
  /private/tmp/pendulum-swing-cycle-check/Nano.Every.ino.elf |
awk '
  /uint8_t tail_snapshot = 0U/ { armed = 1 }
  armed && /^[[:space:]]*[0-9a-f]+:/ && /[[:space:]]cli([[:space:]]|$)/ { inside = 1 }
  inside && /^[[:space:]]*[0-9a-f]+:/ { print; count++ }
  inside && /[[:space:]]out[[:space:]]+0x3f/ { print "instruction_count=" count; exit }
'
```

For the fixed source this prints nine encoded instructions. The displayed
`rjmp` is the empty-queue branch and is not executed on the nonempty path, so
that path executes the other eight. Its AVRxt timing sum is
`1 + 3 + 3 + 1 + 2 + 3 + 1 + 1 = 15` cycles for `cli`, three `lds`, `cp`,
taken `brne`, `ldi`, and `out`. The `ldi ...,0xA9` and 169-byte copy loop must
appear after the printed `out`; if either appears before it, the regression has
returned. Package versions can place `avr-objdump` in a different
`avr-gcc/.../bin` directory, but do not affect the check.

For the archived pre-fix listing, the 169-byte copy loop costs 1,013 cycles:
168 taken iterations at six cycles plus the five-cycle final iteration. The
remaining generated instructions between `cli` and SREG restore cost 158
cycles, for the measured 1,171-cycle total.

## Hardware validation remaining

The assembly and recorded timing establish the cause and the deterministic
interrupt-mask contribution from this path. A hardware run should still
confirm the electrical/runtime result:

1. Build and flash the corrected Nano Every firmware with canonical PPS rows.
2. Exercise PPS phases across swing completion, ideally by varying PPS phase or
   running long enough to sample the naturally drifting phase.
3. Measure the new baseline and compare PPS latency conditioned on phase after
   `edge4`. Confirm the former 1,171-cycle swing-copy contribution and its
   486-1,268-cycle shoulder are absent. Individual samples may still exceed the
   160-cycle diagnostic threshold because unrelated interrupt activity can
   overlap them.
4. Confirm reported `drop_ir`, `drop_pps`, and `drop_swing` remain zero and
   swing sequence continuity is preserved under serial backpressure. Where
   practical, monitor the PPS input electrically or use a source with an edge
   count so a one-deep hardware capture overwrite is independently detectable.

Because the historical excursions occurred only 132 times in about 67 days,
an ordinary short soak may not encounter the adverse phase. A controlled
phase sweep is the stronger validation.
