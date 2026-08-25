# A4 scope amendment 001 — additive timing correction

- Corrects only the non-normative timing attribution in the immutable
  `a4-scope-amendment-001.md`; it does not amend the approved A4 scope, change
  the 800,000,000-unit ceiling, or supersede any other statement in that note.
- Source: GitHub Actions run `32626186825`, fan-in job `97163239067`.
- Step timestamps: analyzer freeze ran from `07:59:10.457` to `07:59:14.297`
  (approximately 3.84 seconds); analyzer resume ran from `08:01:27.295` to
  `08:01:27.968` (approximately 0.67 seconds); the independent recomputing
  validator ran from `08:01:47.302` to `08:02:04.110` (approximately 16.81
  seconds).
- Timing calibration, non-normative: the retained EXP-0051 analysis report
  records 134,291,460 work units. GitHub Actions run 32626186825, fan-in job
  97163239067, records approximately 3.84 seconds for derivation freeze, 0.67
  seconds for analyzer resume, and 16.81 seconds for the independent
  recomputing validator. Using the slower independent-recomputation observation
  gives `800,000,000 / 134,291,460 * 16.81 ~= 100.1 seconds`. This is coarse
  hosted-run planning evidence only because A3 and A4 charge different work
  mixes; it does not prove A4 runtime. The normative controls remain the
  checked 800,000,000-unit counter, 900-second fan-in timeout, and 2,700-second
  hard campaign timeout.
