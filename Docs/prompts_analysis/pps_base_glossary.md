# PPS_BASE field glossary

Compact glossary for the main `PPS_BASE` fields.

## Core timing

- `q` — monotonic sample sequence number for the `PPS_BASE` stream.
- `m` — coarse wall-clock timestamp in milliseconds (`millis()`-style).
- `d` — raw observed PPS interval in MCU ticks for one GPS second.
- `en` — error versus nominal clock: `d - 16000000` ticks.

## Frequency estimates

- `ff` — fast frequency estimate, in ticks per PPS second.
- `fs` — slow frequency estimate, in ticks per PPS second.
- `fh` — current applied/blended estimate (`f_hat`), in ticks per PPS second.

## Residuals

- `ef` — residual versus fast estimate: `d - ff`.
- `es` — residual versus slow estimate: `d - fs`.
- `eh` — residual versus applied/blended estimate: `d - fh`.

## Quality / state

- `c` — PPS classification / validator decision code.
- `v` — validity flag; `1` means usable by the live logic, `0` means rejected / not yet trusted.
- `s` — discipliner state code (for example free-run, acquire, disciplined, holdover depending on firmware mapping).

## Stability / jitter

- `j` — robust jitter metric in ticks: a MAD-style scatter measure of recent residuals around `fh`, not raw ISR latency.
- `r` — signed rate/frequency correction metric already used by the discipliner (`R_ppm`-style field in the repo’s existing representation).

## Capture-path health

- `l` — `lat16`: PPS ISR entry latency in timer ticks. At 16 MHz, 1 tick = 62.5 ns, so 96 ticks is about 6.0 µs.
- `cp` — captured timer/count value associated with the PPS event, if present in the firmware build.

## Plain-English intuition

- `d` tells you what happened this second.
- `fh` tells you what the firmware currently thinks one true second looks like in MCU ticks.
- `eh` tells you how wrong that current estimate was on this second.
- `j` tells you how noisy the recent residuals are after subtracting `fh`.
- `l` tells you how late software arrived after hardware capture, not the PPS timing error itself.
