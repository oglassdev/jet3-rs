# Requirement traceability

[`traceability-ids.json`](traceability-ids.json) is the authoritative registry
of requirement IDs, requirement text, acceptance gates, and required evidence.
Validators and manifests must load that file rather than embedding their own
copies of the ID vocabulary.

## Test and scenario identifiers

Use prefixes that preserve the evidence boundary:

- `UT-`, `IT-`, `PROP-`, `GOLD-`, `CORR-`, and `REG-` for Rust tests;
- `DAO-GEN-`, `DAO-READ-`, `DAO-WRITE-`, and `DAO-UPDATE-` for oracle
  scenarios;
- `FUZZ-`, `MUT-`, and `BENCH-` for fuzz, mutation, and benchmark records.

A checked Rust inventory entry includes its traceability IDs, distinct
invariant, fixture hashes, and expected result. Runtime discovery and ignored
state belong to the separate commit/dirty-bound test observation; pass/fail
execution belongs to acceptance-gate evidence. Parameterization counts as
multiple cases only when the inventory explains the different invariant or
boundary exercised.
