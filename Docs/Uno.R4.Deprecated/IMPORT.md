# Display Repository Import

The Uno receiver was consolidated into `Arduino.Pendulum.Timer` on 2026-09-26.

Source repository: [flynnr2/Arduino.Pendulum.Timer.Display](https://github.com/flynnr2/Arduino.Pendulum.Timer.Display).
Source commit: [7f82a0961299f28db00adc5e682e49f8e6b37609](https://github.com/flynnr2/Arduino.Pendulum.Timer.Display/commit/7f82a0961299f28db00adc5e682e49f8e6b37609)
(“Update receiver for capture-only protocol v3”). The source working tree was
clean at import. This is a source snapshot; earlier Git history remains in that
repository. No source-repository files or remote archive settings were changed.

## File Mapping

| Source location | Destination                      |
| --------------- | -------------------------------- |
| `Uno.R4/`       | `Uno.R4.Deprecated/`             |
| `tests/`        | `tests/uno_r4/`                  |
| `Docs/`         | `Docs/Uno.R4.Deprecated/`        |
| `TODO.md`       | `Docs/Uno.R4.Deprecated/TODO.md` |

The source README's setup instructions are maintained in
[Uno.R4.Deprecated/README.md](../../Uno.R4.Deprecated/README.md). Documentation paths and repository
ownership statements were updated for the consolidated tree. Imported Markdown
tables were aligned without changing their content.

The firmware files retain their source contents except for the host-test-only
include in `Uno.R4.Deprecated/src/SDLogger.cpp`: the test runner now locates
`sd_logger_host_stubs.h` through its include path. This does not change a board
build. The main sketch was renamed from `Uno.R4.ino` to
`Uno.R4.Deprecated.ino` to match its Arduino sketch directory. Test file paths
were adjusted for the new component and test directories.

The source and destination MIT licenses are identical; the existing root
[LICENSE](../../LICENSE) covers the imported code. The obsolete standalone HTML
sketch summary remains in the source archive because it describes superseded
CSV formats and HTTP endpoints. Generated builds, local secrets and machine files
were not imported.

## Maintenance

Maintain the system in this repository. The Uno implementation is deprecated
and retained for reference; new receiver work targets `Raspberry.Pi/`.
The canonical protocol
owner is [Protocol_Wire_Contract.md](../Protocol_Wire_Contract.md). The Nano and
Uno copies of `PendulumProtocol.h` must remain byte for byte identical.

Historical validation reports under this directory describe the original board
and toolchain runs; importing them does not constitute a new hardware validation.

## Consolidation Validation

- Main Python test suite: 152 passed, 1 skipped.
- Uno host regression runner: all checks passed after the directory rename.
- Renamed sketch compiled with UNO R4 core 1.6.0: 171,308 bytes flash and
  19,124 bytes static RAM, leaving 13,644 bytes for stack/heap. No board was flashed.
- All 55 imported firmware files matched the source snapshot, allowing only the
  documented host-test include change and the sketch filename change.
- Nano/Uno protocol headers and the source/destination MIT licenses matched.
- Documentation links, references, duplicate-content checks and Markdown table
  alignment passed, including newly imported files. The full documentation audit
  still reports pre-existing local macOS `.DS_Store` files; these were not removed.
