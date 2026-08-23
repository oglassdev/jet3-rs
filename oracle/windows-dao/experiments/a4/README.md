# A4 row-anchored allocation and catalog preregistration

`DAO-A4-ROW-ANCHORED-MAPS-001` is the project-authored successor to A3. It is
preregistered by `EXP-0052` before acquisition. The immutable base plan is
`a4-row-anchored-maps.plan.json`, SHA-256
`6604b4866b26e3077f351909f7cf85839da7ff75a11600320b21d67d2e98c21c`.

A4 replaces fixed absolute record intervals with row-directory-anchored
locators grounded in `SRC-0020`. It then evaluates four dependency-ordered
layers without allowing a later failure to erase an earlier success:

1. `A4-H1` selects one table-definition page and exactly two page/row locators
   per logical table under one of the two frozen four-byte layouts.
2. `A4-H2` re-resolves both complete rows at every checkpoint and assigns their
   owned/in-use and available roles from lifecycle, growth, churn, and idle
   transitions. Row motion is permitted.
3. `A4-H3` tests type-1 zero-slot behavior, exact tag-05 references, bitmap
   bytes `[4,2048)`, and
   `absolute_page = slot_ordinal * 16352 + bit_index`.
4. `A4-H4` tests one allocation-admitted catalog root and a minimal field model
   for object kind, identifier lifecycle, and name bytes/encoding.

Every layer has an explicit ordered predicate sequence and `no_outcome`
terminal in the plan. All 40 registered predicate ids occur exactly once in
the reachability reconciliation, and the R4-S01-style survivor table covers
every layer predicate. R4-C01-style charging counts each unique qualified
page/checkpoint/model identity once across the derivation-replica union.

## Frozen design

Three fresh replicas are required. Replicas 1 and 2 derive and canonically
freeze all candidates before replica 3 is downloaded or opened. Physical table
names rotate across logical roles `T1` through `T4`:

- `A4TAB_A1`
- `A4TAB_B2`
- `A4TAB_C3`
- `A4TAB_É4`

The four names have equal character length. `A4TAB_É4` is the only non-ASCII
identifier and is pinned to Windows ANSI code page 1252, hexadecimal bytes
`41 34 54 41 42 5f c9 34`. A host whose `GetACP` result is not 1252 fails
before database creation. `A4IX_ID` is a nonunique index over the existing
`Id` long field and is only a catalog object-kind perturbation; A4 makes no
physical index-layout claim.

The 25 closed, quiescent, nonadaptive checkpoints are:

`EMPTY`, `EMPTY_R`, `T1_CREATE_ID`, `T1_ADD_TEXT`, `T1_ADD_INDEX`,
`T2_CREATE`, `T2_DROP`, `T2_RECREATE`, `T3_CREATE`, `T4_CREATE`,
`T1_REL_0064`, `T1_REL_0512`, `T1_REL_0768`, `T1_REL_1280`,
`T1_DELETE_ALL`, `T1_REINSERT_SAME`, `T1_IDLE_R`, `T3_ABS_04096`,
`T3_ABS_08192`, `T3_ABS_12288`, `T3_ABS_16480`, `T4_REL_0064`,
`T4_REL_0896`, `T4_REL_0904`, `T4_IDLE_R`.

Only one listed logical schema operation occurs between closed checkpoints.
Every checkpoint retains a physical page index and a canonical DAO schema
snapshot of non-system TableDefs, fields, indexes, attributes, row counts, and
rolling row hashes. The schema snapshot is read-only, and the before/after MDB
hashes must match.

## A3 calibration disclosure

The retained A3 bundle cited by `EXP-0051` was inspected read-only as a design
and calibration input. Its manifest SHA-256 is
`f1a644abae1585d8ed0531f45a0544d3264d2449f6d5973ef2ef0bb3d5fefaab`;
its analysis report SHA-256 is
`7587389e4323171aff9b9efcd46bcd5fc8e2ec8273116e8a0360965e4e11faeb`;
and its frozen derivation-set SHA-256 is
`ec7c8d27cc46ef9dfdc8214d025cd2d6493ab089f00fc35dbf0ccb9899cdcc0a`.
It is never A4 evidence.

Concrete calibration values are preregistered in the plan. In retained replica
1, tag-01 page 24 has two rows; row 0 moves from `[1915,2048)` through
`L_REL_0512` to `[1911,2048)`, `[1895,2048)`, `[1847,2048)`, and finally
`[1843,2048)`. This explains why A3's frozen absolute interval stopped covering
the complete row. A3's first polarity violation was pages 1021--1023 at that
frozen 1,024-bit edge. The same retained data provides type-1 prefixes on pages
1, 26, and 28 and tag-05 references including 14848 and 16352. A3 did not test
the conversion/base model on holdout, so none of these observations satisfies
an A4 predicate.

## Binding, bounds, and honesty

The base plan incorporates A3's later machinery fixes from the start:

- R5-V01-style binding requires every evidence document to carry both
  `plan_sha256` and `revision_plan_sha256`. Until a revision exists, both equal
  the base-plan hash pinned above. A future pre-acquisition revision must be
  additive, becomes the governing revision, and is retained with the entire
  revision chain.
- R4-S01-style survivor counts retain measured multiplicity for `MULTIPLE`,
  zero for `NONE`, one for a single failed or decisive model, and zero for an
  inapplicable layer.
- R4-C01-style charging counts union-qualified work once, even when both
  derivation replicas expose it.
- R5-T01-style timing measures from hosted attempt start through successful
  manifest creation. Exactly 2,700 seconds is accepted; 2,701 is rejected
  before manifest creation and produces diagnostics rather than a successful
  evidence bundle.
- The dry-run honesty clause requires executed reachability transcripts,
  independent replica-3 overshoots, full analyzer/validator agreement, genuine
  tamper execution, and exact-bound/one-over cases. Hand-authored or constant
  rejection results fail the gate.

The approved scope brief is copied byte-for-byte at
`design-inputs/a4-scope-approved.md`, SHA-256
`ead09d9cec961d018ed4845f14d825d2ae8da2d3329f12d6ae9ea2233e4eeeb7`.

## Schemas and status

The A3 evidence-schema family is copied and retagged under this directory for
minimal worker/analyzer/validator rebinding. A4 additionally defines
`dao-schema-snapshot.schema.json`. No analyzer, validator, worker, or workflow
implementation is part of this preregistration change, and acquisition remains
`BLOCKED` until the plan's execution gates are satisfied and disclosed
additively.

The claims block is fail-closed: only
`descriptive_provider_observation_only` is true. A4 claims no Rust correctness,
DAO compatibility, product support, support-matrix advancement, general Jet
behavior, exact allocation-set equality, physical column/index layout, row
value layout, long-value behavior, writer behavior, or unexercised slot/base
behavior.
