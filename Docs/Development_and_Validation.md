# Development setup and validation

Status: operating guide for the current combined repository. Run commands from
the repository root. Source and tests define behaviour; passing host tests does
not establish electrical correctness, physical timing accuracy or Pi durability.

## Choose the environment

Analysis and the laptop capture tool support Python 3.9 or newer. The Pi package
requires Python 3.11 or newer. Use Python 3.11 or newer for the whole repository.
The root package's test extra does not install the Pi dependencies.

For a new analysis-only environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest tests/test_suite.py tests/test_recording_collection.py
```

These focused tests use the supported suite and collection reader. They do not
run all retained historical-analysis or firmware regressions. The tests at the
top level of `tests/` can be run together on a POSIX shell with
`python -m pytest tests/test_*.py`; several compile Nano C++ helpers and skip
when `c++` is unavailable.

For the complete Python environment, use a Python 3.11+ interpreter to create
the virtual environment, activate it, and install both packages:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]' -e 'Raspberry.Pi[test]' 'Pillow>=10,<13'
python -m pytest
```

Do not recreate an existing environment with a different Python version in
place; create another environment directory if necessary. On Windows use
`python` instead of `python3` and `.venv\Scripts\Activate.ps1` for PowerShell
activation. The complete validation workflow is intended for macOS/Linux:
Pi regressions include POSIX pseudo-terminals, filesystem locks and shell
checks, and the Uno runner invokes a C++ compiler. Windows capture support does
not imply that all repository tests run natively on Windows.

The Pi hardware dependency group is for deployment, not required for host demo,
replay or web tests. Pillow enables OLED image tests that otherwise skip. The
historical long-run PPS regression also skips unless `PPS_67DAYS_DIR` points at
the external recording. A skip is not a successful execution of that check.

Dependencies have ranges rather than a repository lock file. Retain
`python --version` and `python -m pip freeze` with a validation result when
reproduction matters; a future install may resolve different versions.

## Automated validation

The [repository workflow](../.github/workflows/validation.yml) runs on pull
requests and updates to `main`. It covers the complete Python suite on Python
3.11 and 3.14, the deprecated Uno host runner on 3.11, all four dashboard checks
on Node 24, documentation links and table alignment, and an actual Nano Every
build with Arduino CLI 1.4.1 and megaAVR core 1.8.8. The board job also checks
capture reconstruction and linked instructions. These are host checks; physical
acceptance remains a separate obligation.

Each Python job saves its interpreter version, resolved dependency versions and
test results as workflow artifacts. The Nano job saves its toolchain and
flash/SRAM report and includes the build report in the workflow summary. Review
memory growth on firmware changes: the known working 2026-09-30 baseline uses
43,073 of 49,152 flash bytes and 2,068 of 6,144 static RAM bytes with these flags.
Remaining SRAM is not a measured stack high-water mark. Actions are pinned to
commit IDs; update pins deliberately and rerun validation.

The Python 3.9 analysis/capture compatibility promise is separate from the
whole-repository Python 3.11 minimum. The full CI matrix does not certify every
older interpreter or perform the external 67-day historical-data regression.

## Fresh-checkout analysis example

The checked-in `examples/synthetic` directory contains both PCPS and PCSW:

```sh
python -m pendulum_analysis.suite examples/synthetic --out analysis_test/synthetic
```

Open `analysis_test/synthetic/report.html`. Expect `summary.json` with
`complete: true`, report tables and figures. This short synthetic recording
exercises loading and report generation; missing coverage or unavailable
long-duration statistics are legitimate results. A constant synthetic signal
can also produce a plotting warning about nonpositive values on a logarithmic
axis. It is not physical acceptance data.

`Data/67Days` and other local `Data/` recordings cited in investigations are
ignored and are not included in a fresh checkout. Use your own recording for
those investigations. Use a fresh output directory when inputs or settings
change: generation overwrites matching names but does not remove obsolete
outputs. See [analysis semantics](Clock_Swing_Analysis.md).

## Existing test entry points

Run the Python suite above for broad coverage. For a relevant smaller check:

```sh
python -m pytest tests/test_nano_capture.py tests/test_capture_protocol.py
python -m pytest tests/test_pps_firmware.py tests/test_firmware_parity.py tests/test_csv_formatter.py
python -m pytest tests/raspberry_pi
```

Nano helper tests require a host C++11 compiler available as `c++`. They use
production code with hardware substitutes. They do not compile the complete
AVR sketch or exercise GPIO timing. Read pytest's skip summary before reporting
coverage.

The Pi dashboard also has four standalone Node.js checks, outside pytest:

```sh
node tests/raspberry_pi/test_charts.cjs
node tests/raspberry_pi/test_correlation.cjs
node tests/raspberry_pi/test_live.cjs
node tests/raspberry_pi/test_phase.cjs
```

They use Node's built-in modules and DOM substitutes; no npm installation is
required. They do not replace visual browser or physical Pi checks. Node 24.19.0
was used during the 2026-09-30 documentation verification; the repository does
not declare a minimum Node version.

The deprecated Uno has a separate runner that compiles every `*_test.cpp` and
runs its standalone `*_test.py` checks. A root pytest run is not a substitute:

```sh
python tests/uno_r4/run_host_tests.py
```

It requires `c++` with C++11 support, uses temporary build directories and needs
no board or credentials. See the [Uno guide](../Uno.R4.Deprecated/README.md)
for board libraries, compilation and historical acceptance evidence.

## Nano board build

The capture investigations used Arduino CLI 1.4.1 and Arduino megaAVR core
1.8.8 for `arduino:megaavr:nona4809`. Install the core if absent, then record the
actual CLI/core versions:

```sh
arduino-cli core update-index
arduino-cli core install arduino:megaavr@1.8.8
arduino-cli version
arduino-cli core list
arduino-cli compile \
  --fqbn arduino:megaavr:nona4809 \
  --build-property compiler.cpp.extra_flags="-Os -ffunction-sections -fdata-sections -flto" \
  --build-property compiler.c.extra_flags="-Os -ffunction-sections -fdata-sections -flto" \
  --build-property compiler.elf.extra_flags="-Wl,--gc-sections -flto" \
  --build-path build/nano-every \
  Nano.Every
```

Core installation needs network access; compiling with an installed core is a
separate action from uploading. No serial port or board is needed to compile.
Record the flash/SRAM report and selected compiler version, especially after
changing diagnostic build flags. Historical memory figures apply to their
named snapshots, not every subsequent build.

The default firmware requires an external clock on D2/PA0 at the build's
`F_CPU` before boot. The default board configuration used here is 16 MHz.
For an internal-clock build, append `-DUSE_EXTCLK_MAIN=0` to the C++ extra-flags
value above. For the deprecated Uno UART connection, append
`-DDATA_SERIAL=Serial1 -DCMD_SERIAL=Serial1`; USB `Serial` is the current default.
These are build selections, not runtime commands. Preserve the other flags
when overriding the property. See [configuration](Config_Defines_Guide.md) and
[wiring](Wiring.md) before running a binary.

For capture projection changes, the existing reconstruction checker exercises
production arithmetic and verifies read-pair instructions in a linked build:

```sh
python tools/capture_reconstruction/check_reconstruction.py --build-dir build/nano-every
```

It locates `avr-objdump` through the build's compilation database. It is a host
model and linked-instruction check, not a peripheral simulator. Historical
investigations retain their original counts and sizes; this check's current
output describes the code actually built.

## Deployment and hardware checks

The Pi installer offers a read-only prerequisites check:

```sh
bash Raspberry.Pi/deploy/install.sh --check
```

Use [Pi installation](../Raspberry.Pi/INSTALL.md) for deployment, service
configuration and updates, and [hardware acceptance](../Raspberry.Pi/HARDWARE_TESTS.md)
for power, input levels, shared I²C, GPS/chrony, sustained acquisition, storage
pressure and reboot testing. Demo/replay, host tests and board compilation do
not prove those results. Do not mark unchecked physical acceptance items done
because a software regression passed.

## Documentation verification

Run the existing audit:

```sh
python scripts/doc_audit.py
```

The audit checks tracked and non-ignored untracked documentation links, selected
stale paths, duplicate content and generated artifacts in that file inventory.
Ignored recordings, environments and OS files do not make a clean source audit
fail. Known deployment paths in inline code are excluded from missing-file
checks; actual Markdown links to those paths are still checked. It does not
compare prose with code or validate section anchors or image links.

Check raw Markdown table alignment separately, or apply the mechanical fix:

```sh
python scripts/markdown_tables.py
python scripts/markdown_tables.py --write
```

The checker preserves cell contents and alignment colons, aligns the column
separators, and skips fenced code examples. Both documentation commands run in CI.
Use the [documentation review checklist](README.md#documentation-governance)
to check ownership, current behaviour, examples, history and formatting.
