# Engineering Backlog (UNO R4 WiFi)

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

| ID      | Priority | Area        | Proposal                                                                                           | Risk/Tradeoff                                                            | Effort | Status                   |
| ------- | -------- | ----------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------ | ------------------------ |
| UNO-001 | Medium   | serial      | Non-blocking line assembly using `available()`                                                     | Reduces stalls on incomplete lines; requires parser state handling       | M      | Implemented              |
| UNO-002 | Medium   | memory      | Reduce `String` use on hot paths (`snprintf` + fixed buffers)                                      | Lowers fragmentation risk; increases manual buffer management complexity | M      | Proposed                 |
| UNO-004 | Low      | sensors     | Consider `Wire.setClock(400000)` and a BMP280 address override (`Wire.begin()` is already present) | Better portability; may need board-specific validation                   | S      | Proposed                 |
| UNO-005 | Low      | reliability | Validate the existing 8-second watchdog for unattended recovery (`WDT.h` builds)                   | Must avoid false resets during normal long operations                    | M      | Board validation pending |

## Acceptance Criteria Template

For each backlog item before implementation:

- Define expected behavioral change.
- Define observable validation method.
- Define rollback/safety condition.
