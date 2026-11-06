# Operations

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

## Wi-Fi lifecycle

- First boot without credentials: AP mode starts for provisioning (`/wifi`).
- With stored credentials: STA connect is attempted first.
- Failed STA connect: a quiet hold followed by a bounded number of STA retries. Exhausting retries disables Wi-Fi work while measurement logging continues. An explicit reconnect can restart attempts.
- Startup uses the same staged connection path as recovery. HTTP waits for a stable usable STA or AP interface; repeated observations of one network generation do not restart that wait.

## Logging lifecycle

- SD must be FAT32 and writable.
- UNO opens the four-file capture set after validated CFG and both SCH declarations, then writes capture CSV headers.
- Optional daily rollover and startup file policy are controlled via logging tunables.
- Startup policy defaults to overwrite. `Append` retains the target; `Overwrite` replaces the explicitly selected startup target. `Archive and start fresh` now preserves existing measurement files in place and selects a new numeric filename. It does not copy large files into `ARCH` during acquisition.
- PCPS, PCSW, UNO and STS travel as one set. Reaching 256 MiB in either measurement file, or 8 MiB combined in UNO/STS, closes all four and starts the next shared suffix, for example `PCPS0001.CSV`, `PCSW0001.CSV`, `UNO0001.CSV`, `STS0001.CSV`. The triggering record belongs to the new set. Completed sets, including diagnostics, are preserved; canonical diagnostics are not a recycling ring. Diagnostic traffic can therefore cause rollover earlier than the measurement-only size estimate.
- Initial continuous canonical sets use `PCPS.CSV`, `PCSW.CSV`, `UNO.CSV`, `STS.CSV`. Daily sets use `PYYMMDD.CSV`, `SYYMMDD.CSV`, `UYYMMDD.CSV`, `TYYMMDD.CSV` respectively, with bounded fallback names before time sync. A day change moves all four to the new date. Size or fresh-start rotation uses a shared numeric suffix. All names fit FAT 8.3. A suffix occupied by any member, or by legacy `P0000001.CSV`/`S0000001.CSV`/`UNO1.CSV`/`STS1.CSV` names, is skipped for the whole set. Selection checks at most eight candidates per attempt and retries later if needed; wrapping after 9999 never permits overwriting an existing set. A cold Append start selects a fresh set when the target exists, even if it is intact; only a remount may resume the selected set after checking for incomplete, oversized or partial-tailed files. Startup overwrite replaces only the four selected base/date targets, not completed numbered sets.
- UNO and STS diagnostics each have a 64-record burst allowance replenished at one record per second. Suppressed events are counted. Fault counters remain in RAM even when diagnostic files are unavailable; the current firmware does not register `/json`.
- A capture-set member that fails to open is retried within the same set while healthy members continue. A short write in any canonical member closes the entire set, preserving its partial tail; a paced retry starts a fresh four-file set. Records arriving during that pause cannot be stored. Card remount resumes the selected set unless a partial tail or size limit requires a fresh set. Partially written rows are never blindly retried; offline analysis must flag partial final rows.
- Genuine card outages cannot be buffered indefinitely. Loss counters describe records not stored while recording is requested; available healthy streams continue where possible. Card recovery must preserve existing files.
- Arduino SD 1.3.0 does not report flush success through `File::flush()`. A successful write is not a guarantee against power-loss or media-level corruption.

## Independent startup and sensor health

- The Nano may start before or after the UNO. The UNO joins complete serial records and requests `emit startup` asynchronously until configuration and schemas arrive. Requests slow to five-second intervals after the initial negotiation window. Metadata replay does not replay measurement history.
- Initial sequence numbers establish a join point, not a count of missing pre-start records. Later forward gaps are counted. Backward sequence changes are reported as restart/reordering and trigger a metadata refresh; sequence wrap is handled separately.
- The LED orientation test runs one frame per service deadline, without delaying ingestion.
- BMP280 and SHT4x initialize and recover independently. Polling performs at most one sensor operation per pass when ingestion has headroom. Failed initialization backs off from one to thirty seconds; three consecutive failed reads schedule reinitialization.
- Cached values expire after three sensor polling periods without a successful read. CSV environmental columns contain `nan` when unavailable. The retained JSON helper uses `null`, but has no registered endpoint. A ten-minute pressure plateau is marked suspect separately from read failure and remains numerically available.
- Wi-Fi modem commands, sensor library operations, and SD calls can still block internally. Their real worst-case latency requires hardware validation; staged scheduling does not provide preemption.

## Reset behavior

- `/reset` rewrites EEPROM-backed UNO settings and Wi-Fi credentials from compiled defaults.
- On boot, older valid UNO EEPROM settings are decoded, sanitized, and rewritten in the current EEPROM format while preserving stored values and using defaults for newly added fields.

## Capture timing

Raw PCSW and PCPS timestamps are on the same free-running counter. The Nano has already applied capture projection and input-filter-delay compensation. The Uno's OLED estimator derives swing durations with unsigned wrap-safe subtraction, qualifies adjacent PPS captures, and uses a causal calibrated frequency while it is fresh. It falls back to `nhz` otherwise. The logger retains raw values for offline analysis.
