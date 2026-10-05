# Capture latency: rails, shoulders and interrupt contention

Status: recording-specific investigation, using the data and build evidence
identified below. Its latency populations are not guaranteed for later builds.
The cited recording and generated figure are local `Data/` artifacts and are
not included in a fresh checkout. Current projection semantics belong to
[Capture_Timebase_Architecture.md](Capture_Timebase_Architecture.md).

## Measured result: 20260917_noswings

The 63-cycle baseline and 82-cycle reread rail are real. The large shoulder
ending at 89 cycles is also present away from TCB0 overflow. It should not be
interpreted as the overflow waiting ramp. The ramp is a separate, much smaller
population tied tightly to the wrap boundary.

Source: `Data/20260917_noswings/PCPS.CSV`, 267,013 captures. SHA-256:
`00acea82dba43dc431c9a4302a38ea281a3cdac0708ec2d3ca22e1db6b59e8fb`.
The capture lasts about 3.09 days. There are no sequence discontinuities or
`now32 - edge_tcb0 != latency16` inconsistencies. `PCSW.CSV` has only its
header, and the retained status/log files contain no per-edge TCB1 latency
trace. Missing completed swings do not prove that no isolated TCB1 edges fired.

![PPS latency conditioned on overflow phase](../Data/20260917_noswings/latency_investigation/latency_shape.png)

Define signed edge phase as
`phi = ((edge_tcb0 + 32768) mod 65536) - 32768`.
Zero is reconstructed TCB0 counter wrap, not ISR entry. Negative phase means
the capture happened before wrap. Use this shared timer phase, not raw `cap16`,
which has its own counter offset.

| Feature              | Observed evidence                                        | Interpretation                                                         |
| -------------------- | -------------------------------------------------------- | ---------------------------------------------------------------------- |
| Baseline             | 63 cycles, 218,784 captures (81.94%)                     | Capture to the CNT sampling instant, not the full ISR                  |
| Reread rail          | 82 cycles; clear rail at approximately phase −69 to −2   | Pending overflow causes a second counter pair, sampled 19 cycles later |
| Waiting ramp         | Lower edge approximately `135 - phi` from phase 0 to +72 | TCB0 runs first; progressively less waiting remains for later captures |
| Return to baseline   | 63 cycles at phase +72                                   | The effective overflow exclusion interval has ended                    |
| Background shoulders | Away from wrap, values extend through 89 and stop there  | Short interrupt masks / other service delays, not the TCB0 ramp        |
| Upper tail           | 190 captures at 90–146 cycles, all at phase −72 to +68   | Overflow interaction, sometimes combined with an additional delay      |

“Away” here excludes the deliberately generous window −150 to +180 cycles:
265,734 records are away and 1,279 are inside. The 82-cycle histogram bin has
396 captures, but only 212 are inside this window; the other 184 belong to
the background distribution. Thus selecting `latency16 == 82` does not
uniquely select the reread path. Its **horizontal structure versus phase**
identifies the rail much more clearly than the histogram spike does.

At phase −1 the measured latency is 136–137, and at phase 0 it is 135–136.
Therefore “137 down to 63” is a reasonable description of the boundary
cluster, but **135 at phase zero** is the more useful fitted lower-edge guide.
It is not a ceiling: the 146-cycle maximum is at phase +5, and a 145-cycle
capture is at +22. Entry delays, interrupted instruction length and overlapping
masked sections can move records above the simple guide.

## Why these are three different shapes

`latency16 = CNT - CCMP` measures elapsed timer ticks up to a read inside the
capture ISR. It excludes the remainder of that ISR, including queue writes
and the return sequence. Both capture ISRs use the same paired-read helper
in `Nano.Every/src/PendulumCapture.cpp`.

1. **No pending overflow and no blocking:** the first counter sample yields
   the baseline. Small entry variation broadens it beyond exactly 63.
2. **Capture ISR entered before overflow service:** the overflow flag is found
   set after the first pair, and the pair is reread. The two capture-counter
   samples are 19 cycles apart. If the overflow happens after the flag check,
   there is no reread; “overflow during the ISR” alone is insufficient.
3. **TCB0 already servicing, or selected first from pending interrupts:** the
   capture is latched in hardware but service waits. With an otherwise fixed
   completion time, every cycle later that the edge arrives removes one
   cycle of latency. This produces the slope of −1.

The alternative bottom-right chart plots **sample phase = edge phase +
latency16**. The diagonal waiting ramp becomes a horizontal band near +135:
captures at many different phases converge on the same counter sampling
time after TCB0 service. The reread rail stays diagonal because those captures
are serviced immediately with a fixed extra read cost.

No modal-projection deviation chart is needed for this investigation.
The projection offset is constant at 65512 modulo 65536 for every record.
The plotting tool leaves all timestamps unchanged and uses no projection
normalization.

## What explains the background shoulders?

A build of repository revision `f534acd` with Arduino megaAVR 1.8.8 and the
default Nano Every configuration shows:

| Foreground operation                       | Executed cycles from CLI through SREG restore | Relevance                                      |
| ------------------------------------------ | --------------------------------------------- | ---------------------------------------------- |
| Read `pps_seen` atomically                 | 14                                            | A short, repeatedly executed source of waiting |
| Normal `platformMillis()` counter snapshot | 26                                            | Matches the outer shoulder scale: 63 + 26 = 89 |

The first is `CLI; four LDS; OUT`. The second is `CLI; seven LDS; SBRS
(not skipping); RJMP; OUT`: `1 + 21 + 1 + 2 + 1 = 26` cycles. The wrap-pending
branch takes longer, so that timing is specifically the ordinary snapshot.
There are also queue-index atomic sections and serial interrupts.

These masks produce a range of **remaining** wait times, not one extra fixed
latency rail. Superimposed waits of different lengths naturally make steps
and shoulders in the histogram. This is a strong explanation for the
background's scale and cutoff, but the dataset does not tag the blocked
operation. Assigning every smaller shoulder to a specific function requires
instrumentation or a controlled build comparison. A 26-cycle masked interval
is not an exact 26-cycle added delay for every capture: entry alignment and
the location of the edge within the interval also matter.

The source recording identifies its Nano build as Sep 11 2026 10:18:55,
`git=unknown,dirty=unknown`. The rebuilt assembly therefore supports mechanism
and timing comparisons; it is not proof of the exact binary that produced
the recording. TCB3 timebase, PPS tuning, PPS baseline telemetry and periodic
serial diagnostics were disabled according to recorded startup flags.

## What remains unexplained, and how to resolve it

After excluding the wrap window, the broad shoulder has these approximate
levels. These bands summarize visible changes in density; they are not
classifiers for individual blocking functions.

| Latency band | Captures | Mean captures per integer latency |
| ------------ | -------: | --------------------------------: |
| 69–75        | 3,273    | 467.6                             |
| 76–79        | 1,194    | 298.5                             |
| 80–89        | 1,927    | 192.7                             |

The 80–89 band alone contributes about 193 captures per latency bin. Shorter
blocking operations can add another population at lower delays, producing
successively higher shelves toward the baseline. A fixed-duration mask with
capture arrival spread across its execution produces approximately uniform
remaining waits. Several such masks produce a stepped sum. Instruction
alignment and nonuniform arrival phase blur the endpoints.

Additional **executed normal-loop paths**, including out-of-line jumps back
to SREG restoration, support this interpretation:

| Candidate path in rebuilt assembly       | CLI through SREG restore | Relation to the shoulders                           |
| ---------------------------------------- | -----------------------: | --------------------------------------------------- |
| Empty PPS queue check                    | 12 cycles                | Short waiting component                             |
| Empty IR queue check                     | 13 cycles                | Runs even without IR edges                          |
| Atomic PPS-seen snapshot                 | 14 cycles                | Another short component                             |
| Empty completed-swing peek               | 16 cycles                | Scale close to the step around 79–80 = 63 + 16–17   |
| Normal clock snapshot                    | 26 cycles                | Scale of the outer 89-cycle endpoint                |
| Main-loop breadcrumb heartbeat increment | 26 cycles                | A second normal-loop contributor at that same scale |

The empty swing peek is 16 cycles despite having no data to copy: in this
build its empty branch jumps to two register clears and jumps back to the
SREG restore. The breadcrumb increment is `CLI; four LDS; ADIW; two ADC;
four STS; OUT`, also 26 cycles. Both examples show why counting source-level
reads alone misses part of the mask duration.

The step locations are **consistent with this mixture**, not a demonstrated
one-function-per-step assignment. CLI-to-restore totals and added capture
latency are related but not interchangeable: the capture can arrive anywhere
within the mask, and entry resumes at an instruction boundary. In particular,
a 14-cycle section does not imply a separate 77-cycle rail.

The distributions persist through the recording. Dividing by record order
into four equal quarters, the fraction of away-from-wrap captures in each
band is:

| Run quarter | 69–75 cycles | 76–79 cycles | 80–89 cycles | Maximum away from wrap |
| ----------- | -----------: | -----------: | -----------: | ---------------------: |
| 1           | 1.247%       | 0.460%       | 0.792%       | 89                     |
| 2           | 1.207%       | 0.464%       | 0.689%       | 89                     |
| 3           | 1.224%       | 0.439%       | 0.707%       | 89                     |
| 4           | 1.249%       | 0.435%       | 0.712%       | 89                     |

This argues against the shoulder being just a startup episode. It does not
prove uniform random phase relative to each foreground operation.

The most useful next experiment is a controlled TCB2-only comparison:

1. Preserve the current baseline recording and exact firmware binary/build
   configuration. Keep PPS source, clock, logging and interrupt settings fixed.
2. Change one candidate at a time in a diagnostic build: shorten the clock
   snapshot mask, or omit the diagnostic main-loop heartbeat update. Rebuild
   and count the resulting paths. Do not remove atomic protection from shared
   multi-byte state without replacing it with a coherent read strategy.
3. Compare the **away-from-wrap** 80–89 counts per PPS capture, and then the
   smaller shoulder steps. If two 26-cycle contributors exist, removing one
   should reduce the shelf height; it need not lower its 89-cycle endpoint.
4. Re-measure the baseline and reread rail for each build, since compiler
   register allocation can change them. Use the overflow-conditioned view to
   ensure a change in wrap exposure is not mistaken for a shoulder reduction.

No diagnostic firmware changes or flashing were performed in this investigation.
TCB1 measurement and cross-channel contention are deferred at the user's request.
The unresolved question is now the **relative contribution of the foreground
masks**, rather than whether the broad shoulders are the TCB0 waiting ramp.

## Reproduction and verification

```sh
.venv/bin/python tools/capture_reconstruction/plot_latency_shape.py Data/20260917_noswings
arduino-cli compile --fqbn arduino:megaavr:nona4809 --build-path /private/tmp/pendulum-latency-build Nano.Every
```

The plot command writes `latency_investigation/latency_shape.png`, exact
near/away histogram counts, individual boundary records, phase envelopes and
a source-hashed summary. Guides are explicitly recording-specific hypotheses,
not universal firmware thresholds. The input files and historical reports
are preserved. The current firmware built successfully (46,456 bytes flash,
3,670 bytes global SRAM). Charts were visually inspected and headline counts
checked independently against the raw CSV.
The documentation audit reported only existing `.DS_Store` files, which were
left untouched. `git diff --check` passed.

Timing references: Microchip's [AVR instruction summary, AVRxt column](https://onlinedocs.microchip.com/oxy/GUID-0B644D8F-67E7-49E6-82C9-1B2B9ABE6A0D-en-US-23/GUID-BA59618D-4850-490B-B176-0BCC3D9438A1.html)
and [ATmega4808/4809 data sheet, CPUINT](https://ww1.microchip.com/downloads/aemDocuments/documents/MCU08/ProductDocuments/DataSheets/ATmega4808-09-DataSheet-DS40002173C.pdf).
