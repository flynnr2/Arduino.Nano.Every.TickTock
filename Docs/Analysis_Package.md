# Analysis Package

Status: Historical canonical-v2 reference. Superseded by [Clock and swing analysis](Clock_Swing_Analysis.md). The old CLI is archived as `historical_cli.py` for regression checks; it is not the installed `pendulum-analyze` command.

This document describes only the retained canonical-v2 implementation. Current user and developer documentation is [Clock and swing analysis](Clock_Swing_Analysis.md).

## Purpose

`pendulum_analysis` produces a repeatable canonical v2 analysis bundle from either:

- a run directory containing `PCSW.CSV`, `PCPS.CSV`, `STS.CSV`, and/or `UNO.CSV`
- a direct pendulum CSV file such as `PCSW.CSV`

The package converts canonical swing edge timestamps into neutral interval fields, aligns PPS information when available, computes quality masks, plans analyses by duration and available columns, writes stable output artifacts, and records lineage/provenance.

## Installation

Install from the repository root:

```bash
python3 -m pip install -e .
```

Install test dependencies:

```bash
python3 -m pip install -e ".[test]"
python3 -m pytest
```

The historical entry point is `python -m pendulum_analysis.historical_cli`. The installed `pendulum-analyze` command runs the current suite and does not accept the historical options below.

## CLI

Basic synthetic example:

```bash
python -m pendulum_analysis.historical_cli examples/synthetic/PCSW.CSV --out analysis_test
```

Run-directory example:

```bash
python -m pendulum_analysis.historical_cli Data/EXTCLK.TCB1-SYNC.TCB2-PPS.8D --out analysis_real --clock-profile synchronome
```

Direct canonical swing file:

```bash
python -m pendulum_analysis.historical_cli Data/EXTCLK.TCB1-SYNC.TCB2-PPS.8D/PCSW.CSV --out analysis_real
```

Analyze a PPS file by itself:

```bash
python -m pendulum_analysis.pps.historical_main Data/EXTCLK.TCB1-SYNC.TCB2-PPS.8D/PCPS.CSV --out pps_analysis
```

When the historical CLI discovers `PCPS.CSV` or `CPS.CSV` in a run, it runs this standalone PPS analysis once under `OUTDIR/pcps/`. The canonical report links to that report and treats it as the authoritative PPS interpretation.

The retained PPS implementation is an analysis under `pendulum_analysis/pps/`; its
reports do not retain the old PPS artifact schema. The existing swing pipeline
still uses its internal PPS interpolation/planning helpers. See
[PPS Analysis](PPS_Analysis.md) for its historical command, definitions and settings.

List planner analysis names:

```bash
python -m pendulum_analysis.historical_cli --list-analyses
```

Important options:

| Option                             | Purpose                                                                                            |
| ---------------------------------- | -------------------------------------------------------------------------------------------------- |
| `--out DIR`                        | Required output directory for generated artifacts.                                                 |
| `--target-period SECONDS`          | Nominal full-cycle target; default `2.0`.                                                          |
| `--clock-profile ID`               | Built-in profile id; default `generic`; use `synchronome` for Synchronome-specific interpretation. |
| `--clock-profile-file PATH`        | Explicit profile YAML; highest profile precedence.                                                 |
| `--clock-profile-dir DIR`          | Additional profile directory.                                                                      |
| `--config PATH`                    | Project analysis YAML.                                                                             |
| `--analysis-profile NAME`          | Analysis tier selection.                                                                           |
| `--enable NAME` / `--disable NAME` | Force or suppress planner analyses; may be repeated.                                               |
| `--force-long-run`                 | Plan long-run analyses regardless of duration.                                                     |
| `--nominal-hz HZ`                  | Nominal hardware clock frequency; default `16000000.0`.                                            |
| `--outlier-threshold N`            | Robust outlier threshold; default `6.0`.                                                           |
| `--max-points-per-plot N`          | Deterministic downsampling limit; default `20000`.                                                 |

## Configuration

Configuration resolves in this order, with later layers overriding earlier layers:

1. `pendulum_analysis/defaults.yaml`
2. `analysis_defaults` from the selected clock profile
3. project YAML passed with `--config`
4. explicitly supplied CLI flags

The resolved configuration is written to `metadata/config_resolved.yaml`. Unknown configuration keys are rejected.

Default tier thresholds:

| Tier       | Auto threshold                              |
| ---------- | ------------------------------------------- |
| `core`     | always available when required inputs exist |
| `standard` | 1 day                                       |
| `medium`   | 7 days                                      |
| `long`     | 21 days                                     |
| `full`     | same planner ceiling as long-run analyses   |

## Inputs

Run-directory discovery recognizes canonical file names by role:

- `PCSW.CSV` / `CSW.CSV`: swing timing records
- `PCPS.CSV` / `CPS.CSV`: PPS timing records
- `STS.CSV`: status telemetry when present
- `UNO.CSV`: auxiliary Arduino-side capture when present

Canonical `PCSW` edge timestamps are converted into neutral interval fields such as `open_A`, `block_A`, `open_B`, `block_B`, `half_A`, `half_B`, and `full`. The package does not assign impulse-side meaning unless the selected profile provides that interpretation.

## Outputs

The output directory is organized by namespace and artifact type:

```text
OUTDIR/
  report/
    canonical_report.md
    manifest.json
  pcsw/
    plots/
    csv/
    summary/
  pcps/
    report.html
    report.md
    summary.json
    plots/
    csv/
  combined/
    plots/
    csv/
    summary/
  metadata/
    config_resolved.yaml
    provenance.json
    hashes.json
    warnings.txt
    mask_audit.md
    mask_audit.csv
    analysis_lineage.md
    analysis_lineage.csv
```

The artifact registry assigns stable IDs and namespace-local paths for the canonical swing analysis. The standalone PPS package owns everything under `pcps/`, including its HTML and Markdown reports, plots, CSV exports, and machine-readable summary.

## Profiles

Built-in profiles live in `pendulum_analysis/profiles/` and are packaged with the Python distribution. This is the canonical profile location.

Profile lookup precedence:

1. `--clock-profile-file`
2. `--clock-profile-dir`
3. repository-root `profiles/` if it exists
4. bundled `pendulum_analysis/profiles/`

The repository no longer keeps a duplicate root `profiles/` copy. Create a local or project-specific profile directory only when overriding or adding profiles outside the package.

Built-in profile ids:

- `generic`
- `synchronome`
- `ato`
- `brillie`
- `eureka`
- `longcase_weight`
- `mantle_spring`

Only `synchronome` currently asserts clock-family phase and harmonic semantics. Stub profiles are descriptive and deliberately conservative.

## Generic Analyses

Core and generic analyses include:

- input provenance and hashes
- data loading, validation, warnings, and quality summaries
- basic interval statistics
- residual derivation
- robust filtering/masks
- standard plots
- Allan deviation
- FFT spectrum and expected-period matching
- relationship summaries
- environmental correlations when environmental columns exist
- medium/long-run daily, time-of-day, rolling stability, rolling Allan, lag-correlation, diurnal, changepoint, and investigation-next outputs when duration and dependencies allow

The planner records every analysis as `RUN`, `SKIP`, `WARN`, or `FORCED` with reasons in the quality summary and manifest-linked results.

## Synchronome-Specific Analyses

When `--clock-profile synchronome` is selected, the package enables Synchronome interpretation where input columns support it:

- 15-swing primary phase fold
- 30-event secondary phase model
- impulse-cycle harmonic expectations with a 30 s fundamental
- tick/tock and block-interval diagnostics
- one-sided impulse interpretation
- Synchronome jitter localisation outputs

These interpretations are profile-driven; they are not inferred from generic data alone.

## Masks

The package writes both CSV and Markdown mask audits. Important mask concepts:

- `valid`: rows that pass basic parsing and finite-value checks
- `dropped`: rows excluded because required timing values are missing or invalid
- `analysis`: primary rows used for statistical summaries after validity and robust outlier rules
- `robust_summary`: rows used for robust descriptive statistics
- `structure_diagnostic`: rows retained for structural diagnostics even when not used for primary rate summaries

Use the generated `metadata/mask_audit.md` and `metadata/mask_audit.csv` for run-specific row counts. The package source owns the exact mask definitions.

## Lineage And Metadata

Each run writes:

- `metadata/provenance.json`: input manifest, exact command, and report provenance
- `metadata/hashes.json`: SHA256 hashes for inputs and materialized artifacts
- `metadata/analysis_lineage.md` and `.csv`: per-artifact data lineage
- `report/manifest.json`: artifact registry with IDs, paths, sources, sections, and analysis names

These files are generated outputs, not repository documentation.

## Expected Workflow

1. Capture raw serial output and preserve the raw stream.
2. Split or export canonical `PCSW.CSV` and, when available, `PCPS.CSV`.
3. Run `python -m pendulum_analysis.historical_cli` with the correct historical clock profile.
4. Read `report/canonical_report.md` first.
5. Inspect `metadata/warnings.txt`, `metadata/config_resolved.yaml`, `metadata/mask_audit.md`, and `metadata/analysis_lineage.md` before comparing runs.
6. Commit source/config/profile changes, not generated analysis output directories.

## Adding Analyses

Add new analyses in the implementation, then update this document only if the user/developer workflow changes.

Historical planner names are exposed by:

```bash
python -m pendulum_analysis.historical_cli --list-analyses
```

New analyses should declare required roles, required capabilities, tier, outputs, and skip reasons through the planner/module system so users do not need to infer behavior from report text.

## Adding Profiles

Add a YAML profile with:

- `id`
- `display_name`
- optional nominal timing facts
- `phase` metadata when a real event-cycle model is known
- `capabilities`
- `harmonic_families` when expected spectral structure is justified
- `annotations.fft` for report annotations
- `notes`

Use `--clock-profile-file` while developing a profile, then move stable built-ins to `pendulum_analysis/profiles/`.

## Testing

Run the package tests:

```bash
python3 -m pytest
```

For documentation checks:

```bash
python3 scripts/doc_audit.py
```
