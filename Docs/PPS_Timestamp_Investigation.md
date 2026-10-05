# Six-cycle capture reconstruction error

Status: historical investigation and fix evidence for the source/build snapshots
identified below. Numerical results and resource sizes are not current-build
guarantees. The maintained projection description is
[Capture_Timebase_Architecture.md](Capture_Timebase_Architecture.md); current
build/check instructions are in [Development_and_Validation.md](Development_and_Validation.md).
The cited `Data/` recordings are local evidence, not included in a fresh checkout.

## Finding

The pre-fix shared capture helper has a branch-dependent sampling separation.
It samples a coherent TCB0 timestamp, returns from that helper, and only then
reads TCB1/TCB2 CNT. On a pending TCB0 overflow it refreshes TCB0 CNT after the
flag test and bookkeeping. That selected TCB0 sample is six cycles closer to
the later capture-counter sample than on the normal path. Subtracting the same
kind of capture age therefore shifts the reconstructed edge **six ticks later**.
At 16 MHz this is **375 ns**. Both IR and PPS use the affected helper.

The 67-day analysis found 6,181 rows with `(cap16 - edge_tcb0) mod 65536 = 2`
against the normal value 8, among 5,820,076 PPS rows. Of the 5,181 rows on the
latency-152 rail, 5,068 had the shifted reconstruction. Those counts are the
analysis inputs to this investigation, not classifications used by the fix.
The latency rail is neither necessary nor sufficient to identify the defect.
The generated instructions provide an independent mechanism for exactly the
observed six-tick change.

## Build provenance and instruction evidence

Baseline source: commit `04749da589dc814974b2d4ff326ee9e3eb7dd333`, before this
working-tree edit. Toolchain: Arduino CLI 1.4.1 (Homebrew), Arduino megaAVR core
1.8.8, `avr-g++` 7.3.0 from package `7.3.0-atmel3.6.1-arduino5`.
Board: `arduino:megaavr:nona4809`, default 16 MHz, `-Os`, LTO.

`Data/67Days/STS.CSV` records ATmega4809, 16 MHz external main clock, TCB0
timebase, shared reads enabled, TCB3 disabled, and build time May 6 2026
11:22:31. These exposed settings match the source defaults. It records
`git=unknown,dirty=unknown`, so the exact original binary/compiler invocation
cannot be authenticated from STS. This is a rebuilt-source proof, supported by
the user's identification of this source as the firmware that produced the run.

The README command initially failed because passing `-flto` to `avr-ar` gives
“two different operation options specified.” Omitting `compiler.ar.extra_flags`
built successfully; the platform already enables compiler/linker LTO. No
Arduino dependency package was modified.

Baseline linked disassembly (`avr-objdump -d -C`) contains:

```text
26ae: lds r16, 0x0A8A     ; initial TCB0 CNT low, latches high
26b2: lds r17, 0x0A8B
26b6: lds r24, 0x0A86     ; INTFLAGS: 3 cycles
26ba: sbrs r24, 0         ; clear flag: 1 cycle
26bc: rjmp 0x2716        ; clear flag: 2 cycles
...                      ; pending-flag diagnostics, then
270e: lds r16, 0x0A8A     ; replacement TCB0 CNT low
2712: lds r17, 0x0A8B
2716: ...                ; common construction/return
...
5682: lds r20, 0x0AAA     ; PPS TCB2 CNT low
```

Only the normal selected sample is followed by the three instructions totaling
`3 + 1 + 2 = 6` cycles. Overflow diagnostics occur before its replacement sample
and do not contribute to the final inter-sample gap. The IR ISR likewise calls
the same helper before its TCB1 CNT read at `0x57f0`.

ATmega4809 uses the AVRxt instruction timings: see Microchip's
[device instruction-set summary](https://onlinedocs.microchip.com/oxy/GUID-4E9DA219-611B-4772-B5D3-9ED908198864-en-US-16/GUID-F4E2E38F-3239-43F8-BE4D-5997AB32E97F.html)
and [LDS cycle table](https://onlinedocs.microchip.com/oxy/GUID-0B644D8F-67E7-49E6-82C9-1B2B9ABE6A0D-en-US-23/GUID-42EBE7FA-6359-4986-827E-28F00A3B2FAF.html).

## Correction and rollover reasoning

The shared helper now reads the two complete counters in one volatile assembly
block: TCB0 low, TCB0 high, TCBn low, TCBn high. Four consecutive `LDS`
instructions guarantee the same sampling separation regardless of surrounding
compiler register allocation or branches. Each timer's low-byte read latches
its high byte, preserving a coherent 16-bit read.

The helper checks TCB0 INTFLAGS **after** this pair. If a wrap is pending, it
increments its local overflow epoch and reads **both counters again** with the
same instruction block. Diagnostic bookkeeping follows the selected pair.
It leaves the pending flag and software overflow count for the normal overflow
ISR, avoiding a double increment of shared state.

- An already pending overflow selects the fresh pair in the incremented epoch.
- A wrap during the first counter pair or before the flag read also selects a
  fresh pair in the incremented epoch, including a wrap between low/high reads.
- A wrap after a clear flag read leaves the earlier, correct pair selected.
- Capture-counter rollover is handled by 16-bit modular `CNT - CCMP`.
- Overflow epoch rollover and backdating use unsigned 32-bit arithmetic, so
  the full 32-bit timestamp can wrap normally.

The capture counter's low byte is sampled two `LDS` instructions, or six timer
ticks, after TCB0's low byte. A fixed `+6` in **32-bit arithmetic** aligns
`now32` to that later sample before subtracting raw `latency16`. This value is
an instruction-timing constant, not a correction based on latency 152, captured
phase, or historical data. Both timers are configured for CLKDIV1.

This sampling separation is distinct from the fixed startup phase difference
between the independent TCB counters. `CNT - CCMP` cancels the capture timer's
startup phase naturally; its value must not be calibrated from `cap16-edge32`.
The new firmware can have a different fixed absolute timestamp phase from the
old firmware, because it also removes the old sampling skew. PPS intervals
and within-run IR intervals do not depend on a constant timestamp origin.
Fresh captures should establish the new diagnostic latency/phase baseline.

## Validation

Run the production-math host test and linked-instruction check:

```sh
arduino-cli compile --fqbn arduino:megaavr:nona4809 \
  --build-path /private/tmp/pps-check Nano.Every
python3 tools/capture_reconstruction/check_reconstruction.py \
  --build-dir /private/tmp/pps-check
```

The test extracts and compiles the actual production C++ reconstruction branch
and arithmetic, replacing hardware reads with a clock model. It runs
**5,500,164 cases** over all 65,536 counter phases, pending versus serviced
wraps, timer offsets 0/-118/65530, capture ages 0/1/151/65000 ticks, varying flag
read/branch timing, and a flag asserted at TOP one tick before wrap. It includes
432 capture-counter wraps between paired low-byte samples, 288 TCB0 wraps
after the flag sample, and 168 crossings of the full 32-bit timestamp boundary.
All passed. The script also verifies both initial/retry four-LDS sequences,
with distinct result registers, for both TCB1 and TCB2 in the linked firmware.

| Configuration                                               | Flash bytes | Static SRAM bytes | Result                                                                   |
| ----------------------------------------------------------- | ----------: | ----------------: | ------------------------------------------------------------------------ |
| Pre-fix defaults                                            | 46,240      | 3,665             | Fits                                                                     |
| Fixed defaults                                              | 46,386      | 3,665             | Fits; math and both ISR instruction checks pass                          |
| Fixed `ENABLE_PROFILING=1`, diagnostics default             | 51,261      | 4,345             | Compiles/links; exceeds 49,152-byte board flash; instruction checks pass |
| Fixed `ENABLE_PROFILING=1`, `ENABLE_DIAGNOSTIC_TELEMETRY=0` | 42,365      | 3,531             | Fits; both ISR instruction checks pass, including dual-PPS code          |

These sizes describe the capture-fix snapshot; concurrent unrelated edits may
change the final integrated build size. The full diagnostics profile is not a
flashable result. The default capture fix adds 146 flash bytes and no static
SRAM in the controlled comparison.

The host model is not a cycle-accurate ATmega4809 peripheral simulator. Neither
these tests nor linked assembly replace hardware validation of the counter
latches, external PPS path, and actual ISR delay distribution. No hardware was
flashed during this investigation. A fresh PPS/IR run should confirm that the
branch-dependent six-cycle phase split disappears and that wrap-adjacent
intervals agree with ordinary intervals.

The existing operating limits remain: capture service age must stay below
65,536 ticks (4.096 ms at 16 MHz), and an unserviced overflow must not persist
through a second timer wrap. A one-bit overflow flag and 16-bit capture age
cannot recover information already lost beyond those limits.
