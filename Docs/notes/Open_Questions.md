# TODO

Status: exploratory / historical note. Not a normative source of current firmware behaviour.

These retained questions describe earlier planning, not a current delivery
backlog. For the maintained documentation map and review checklist, see
[the documentation index](../README.md). The exploratory architecture and
measurement discussion remains in `Universal_Pendulum_Timer_Strategy.md`.

## Next Steps
- [ ] Bench-validate the default PPS acquire/lock/holdover tunables against longer real-world logs.
- [ ] Audit any downstream log parsers against the current `SCH` / capture / `STS` schema.
- Documentation ownership and duplication are now covered by the
  [documentation review checklist](../README.md#documentation-governance).
- Hardware checklists now exist for the
  [Pi](../../Raspberry.Pi/HARDWARE_TESTS.md) and
  [retained Uno](../Uno.R4.Deprecated/test-checklist.md). Their presence does not
  establish that outstanding physical checks have passed.

## Open Questions
- Which comparison-matrix features are actually in scope for the next firmware milestone?
- Should future work add more operator-facing guidance for holdover behavior and stale-PPS diagnostics?
