# Dual-PPS Analysis Prompt

Use only these two CANONICAL files as inputs:

- `PCPS.CSV` = TCB2 “true PPS” reference path
- `PCSW.CSV` = TCB1 capture path with PPS mapped onto the IR/swing record

Do **not** rely on `STS.CSV` for adjustment values. Treat `PCPS.CSV` as the sole timing reference.

## Goal

Produce a concise but rigorous **dual-PPS test analysis** that evaluates:

1. how closely the raw TCB1 and TCB2 paths agree,
2. how well a `PCPS.CSV`-derived offline adjustment normalizes TCB1,
3. whether any patterns appear in the four component intervals inside each `PCSW.CSV` row.

## Canonical interpretation

### `PCPS.CSV`
Treat each consecutive valid PPS edge pair as one 1-second TCB2 reference interval:

- `pps_ref[k] = edge_tcb0[k+1] - edge_tcb0[k]`

Use only consecutive valid rows / sequences.

### `PCSW.CSV`
Each row contains five aligned edges and therefore two 1-second totals plus four components:

- `sw_A = edge2_tcb0 - edge0_tcb0`
- `sw_B = edge4_tcb0 - edge2_tcb0`

Component intervals:

- `c01 = edge1 - edge0`
- `c12 = edge2 - edge1`
- `c23 = edge3 - edge2`
- `c34 = edge4 - edge3`


### Alighnmnent
PCPS.CSV contains PPS rising edges only. PCSW.CSV contains both rising and falling edges in canonical swing form. Therefore cross-file alignment to PCPS must be done using the rising-edge positions in PCSW only, i.e. edge0, edge2, and edge4. The falling edges (edge1, edge3) are used for internal component decomposition, not direct cross-path PPS alignment.

## Data hygiene

Before analysis:

- exclude malformed rows,
- exclude non-consecutive `PCPS.CSV` intervals,
- exclude `PCSW.CSV` rows marked dropped / invalid,
- report exactly how many intervals/rows were excluded and why.

## Required analyses

### 1) Raw matched-second comparison
For matched 1-second intervals, compare TCB1 vs TCB2 in raw cycles.

Compute for:

- TCB1 `sw_A` vs matched TCB2 second
- TCB1 `sw_B` vs matched TCB2 second
- combined TCB1 seconds vs TCB2

For each, report:

- `n`
- mean delta cycles (`TCB1 - TCB2`)
- median delta cycles
- std delta cycles
- MAD or robust sigma
- min / max
- p05 / p95

### 2) Direct edge-offset stability
Compare aligned absolute PPS edges across paths:

- `d0 = PCSW.edge0 - PCPS.edge(k)`
- `d2 = PCSW.edge2 - PCPS.edge(k+1)`
- `d4 = PCSW.edge4 - PCPS.edge(k+2)`

For each offset series, report:

- `n`
- mean offset cycles
- median offset cycles
- std cycles
- p05 / p95

Explain whether the path difference looks like a mostly fixed offset, jitter, or both.

### 3) Offline adjustment methodology from `PCPS.CSV`
Build an offline reference frequency / cycle estimate from `PCPS.CSV` only.

Use at least one sensible smoother, preferably a robust one, such as:

- rolling median,
- EWMA,
- or another clearly explained method.

Define:

- nominal `F0 = 20,000,000` cycles/sec
- `f_hat[k]` derived only from `PCPS.CSV`

Then adjust intervals with:

- `x_adj = x * F0 / f_hat[k]`

State clearly which second each adjusted `PCSW` interval is mapped to.

### 4) Adjusted TCB1 second-level results
Using the `PCPS`-derived `f_hat`, adjust TCB1 second-level totals and report:

- raw mean / median / std vs nominal
- adjusted mean / median / std vs nominal
- any reduction in spread from raw to adjusted

If adjusted errors are reported, define them explicitly as:

- `err_cycles = adjusted_cycles - 20,000,000`

### 5) Component-level analysis inside `PCSW.CSV`
For the four components:

- `c01`
- `c12`
- `c23`
- `c34`

report both raw and adjusted distributions:

- `n`
- mean cycles
- median cycles
- std cycles
- p05 / p95

Also compute normalized within-second proportions:

- `pA1 = c01 / sw_A`
- `pA2 = c12 / sw_A`
- `pB1 = c23 / sw_B`
- `pB2 = c34 / sw_B`

Summarize whether any component appears systematically noisier, biased, or structurally different.

### 6) Pattern checks
Look for obvious structure in:

- raw `TCB1 - TCB2` deltas over time
- edge-offsets over time
- component residuals / proportions over time
- histograms of deltas / offsets

If any periodic, quantized, or drift-like structure is visible, call it out briefly.

## Required outputs

Provide:

1. a short narrative summary,
2. clear tables for the main statistics,
3. explicit definitions of every metric used,
4. a brief methodology note describing exactly how `PCPS.CSV` was used to adjust `PCSW.CSV`.

## Preferred emphasis

Prioritize these questions:

- Do the raw TCB1 and TCB2 paths agree closely?
- Does `PCPS`-derived offline adjustment improve TCB1 as expected?
- Do the four `PCSW` components reveal any hidden path-specific pattern?

Keep the write-up concise, but do not omit important caveats or pairing assumptions.
