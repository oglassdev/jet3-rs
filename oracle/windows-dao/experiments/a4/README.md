# A4 row-anchored allocation and catalog preregistration

`DAO-A4-ROW-ANCHORED-MAPS-001` is the project-authored successor to A3. It is
preregistered by `EXP-0052` before acquisition. The immutable base plan is
`a4-row-anchored-maps.plan.json`, SHA-256
`ce37c90d3f835de7beb9d1480821d803cb2d0a5ecf0c7c521fd5c8909ec754c2`.

A4 replaces fixed absolute record intervals with row-directory-anchored
locators grounded in `SRC-0020`. It then evaluates four dependency-ordered
layers without allowing a later failure to erase an earlier success:

1. `A4-H1` uses only TDEF lifecycle bytes, syntactic locator decoding, the
   masked record signature, nonoverlap/identity, and target validity to select
   one invariant layout and table-relative locator pair.
2. `A4-H2` re-resolves the separately frozen per-instance bindings and assigns
   their complete rows at every checkpoint to
   owned/in-use and available roles from lifecycle, growth, churn, and idle
   transitions. Row motion is permitted.
3. `A4-H3` tests type-1 zero-slot behavior, exact tag-05 references, bitmap
   bytes `[4,2048)`, and
   `absolute_page = slot_ordinal * 16352 + bit_index`.
4. `A4-H4` locates one allocation-admitted catalog root and structural field
   model from operation deltas and non-name fields, then compares exact strict
   Windows-1252 and UTF-8 name bytes and stored-length equivalence classes.

Every layer has an explicit ordered predicate sequence and `no_outcome`
terminal in the plan. All 40 registered predicate ids have a mandatory
contract with prerequisites, input candidate set, exact pass/fail rule,
terminal/count/status behavior, and a claimed byte-fixture terminal. These
registry labels are not proof. Before hosted dispatch, a real byte-level
reachability harness must construct shared campaigns from exact 2,048-byte
pages, enumerate only the closed grammar, propagate candidates in normative
order, report actual survivor counts and first failure, demonstrate every
claimed-reachable terminal, and assert every unreachable terminal
(`A4-H1-LOCATOR-PAIR-MULTIPLE` is unreachable by construction because the
exact locator holes admit one pair per layout). R4-C01-style
charging counts each unique qualified
page/checkpoint/model identity once across the derivation-replica union.

## Frozen design

Three fresh replicas are required. Replicas 1 and 2 first evaluate every
non-holdout H1--H4 predicate, then canonically freeze and hash all four layer
results before replica 3 is acquired or opened. The later holdout phase runs
H1, H2, H3, H4 root, and H4 fields in order without changing frozen bytes.
Physical table
names rotate across logical roles `T1` through `T4`:

- `A4TAB_A1`
- `A4TAB_B2`
- `A4TAB_C3`
- `A4TAB_É4`

The four physical names are eight Unicode scalar values and, under the required
DAO snapshot conversion, eight strict Windows-1252 bytes each. The snapshot
also produces the strict UTF-8 candidate but compares neither candidate to
physical bytes. `A4TAB_É4` is
the only non-ASCII identifier and has Windows-1252 bytes
`41 34 54 41 42 5f c9 34`; the registered alternate UTF-8 encoding uses
`c3 89` for U+00C9. Catalog records are located without name bytes. Encoding
and stored length are evaluated only after one structural model survives.
Because strict CP1252 has one byte per representable scalar, no identifier
within CP1252 can distinguish byte-count from scalar/code-unit-count for this
question; those observationally equivalent hypotheses are reported as the
single `cp1252_single_byte_per_scalar` class. A host whose `GetACP` result is
not 1252 fails before database creation. `A4IX_ID` is a nonunique index over the existing
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
hashes must match. It reopens with
`workspace.OpenDatabase(path, False, True, "")`. Its collection ordinals are
assigned only after exact scheduled-name filtering; exact BSTR UTF-16 code units
plus strict Windows-1252 and UTF-8 expected bytes are
retained, and validators independently reject duplicate roles, names, or
ordinals and cross-bind every snapshot to its observation, page index,
manifest entry, and actual bytes.

## A3 calibration disclosure

The retained A3 bundle cited by `EXP-0051` was inspected read-only as a design
and calibration input. Its manifest SHA-256 is
`f1a644abae1585d8ed0531f45a0544d3264d2449f6d5973ef2ef0bb3d5fefaab`;
its analysis report SHA-256 is
`7587389e4323171aff9b9efcd46bcd5fc8e2ec8273116e8a0360965e4e11faeb`;
and its frozen derivation-set SHA-256 is
`ec7c8d27cc46ef9dfdc8214d025cd2d6493ab089f00fc35dbf0ccb9899cdcc0a`.
It is never A4 evidence.

The re-derived byte slices and decoder arithmetic are retained at
`design-inputs/a3-calibration-receipt.json`, SHA-256
`788605e1aeca015d88319ef78b3ae34adbec04527efaa11b79f5663474169d3e`.

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
  distinguish that predicate measurement from final-layer cardinality, retain
  phase-specific terminal candidates without downstream choices, and store
  zero final models for a derivation terminal. H1 separately hashes its
  replica-invariant model and physical instance bindings; the latter use the
  exact preregistered lifecycle ranges.
- R4-C01-style charging counts union-qualified work once, even when both
  derivation replicas expose it.
- Canonical locator enumeration examines 4,090 raw window identities and at
  most 4,167,722 raw pairs per qualified TDEF page. Across 16 pages the raw
  pair bound is 66,683,552. On retained A3 page 23, the plan recomputes 1,872
  preserved windows per layout, 3,491,392 raw nonoverlapping pairs, and a
  3,495,482-unit raw-window/pair charge before one structural pair survives
  under each layout. Only row-then-page is target-valid: page-then-row is valid
  at 7/25 checkpoints, while row-then-page resolves page 24 rows 0/1 at 25/25.
- Work is bounded per reachable fail-fast terminal path. The largest stated
  path is H4 at 694,378,226 units (both derivation replicas charged for every
  occurrence-dependent term) under the 800,000,000-unit ceiling set by
  `A4-SCOPE-AMENDMENT-001` (`design-inputs/a4-scope-amendment-001.md`, which
  supersedes the approved brief's 600,000,000 figure). Its timing attribution
  is corrected additively by
  `design-inputs/a4-scope-amendment-001-timing-correction.md`, SHA-256
  `49139e945641bf09dfd9969634c8af2e584559707ab89bf02384eef07eab2a8d`.
  The ceiling is classified `conservative_upper`: only its
  checked comparator is tested
  (800,000,000 accepted, 800,000,001 rejected) as a unit test outside the 40
  byte fixtures, and the byte-level `A4-RESOURCE-BOUND` terminal is reached by
  a 67,200-entry changed-hash one-over campaign; mutually exclusive row-count
  and row-length maxima are not summed.
  Type-1 rows admit at most 508 complete slots, type-0 rows 16,248 bits, and
  tag-05 pages 16,352 bits. H4 separately charges the encoding-union scan and
  all bounded name/length tuple attempts. A complete row is at most 2,036
  bytes. Five eight-byte table operations and two seven-byte field/index
  operations admit at most 1,850 occurrence identities per derivation
  replica (3,700 in total); nine deduplicated pattern/operation scans charge
  18,324 byte starts per replica. One shared integer-field endianness gives
  the 165,888 inner grammar. All 1,850 operation-record occurrences per
  replica are required structural inputs, giving the two-replica tuple term
  `2 * 1,850 * 165,888 = 613,785,600`.
- One producer read plus independent analyzer and validator reads cost at most
  1,317,011,456 bytes per replica, below the 2 GiB logical-read bound; no
  analyzer/validator pass is shared. Candidates are globally capped at
  4,096 bytes each (per-operation occurrence evidence lives in a separate
  hashed `analysis/h4-occurrence-evidence.json` of at most 1,048,576 bytes
  holding two replica groups in replica order, referenced by SHA-256 from
  each replica-qualified structural binding), their full array at
  16,781,313 bytes, and concrete bounded transcript schemas keep the complete
  frozen JSON below 23,800,000 bytes.
- H4 identity is split exactly as H1's: `canonical_model_id` hashes only the
  replica-invariant model (deltas, widths, endianness, table/field/index kind,
  lifecycle
  relation, and for the encoded model the structural model id plus
  equivalence class) and `canonical_candidate_id` hashes the model plus its
  replica-qualified physical bindings. Replica agreement compares model ids,
  so the same scientific tuple over different physical evidence agrees; a
  decisive candidate binds both replicas' fourteen operation bindings.
- R5-T01-style timing measures from hosted attempt start through successful
  manifest creation. Exactly 2,700 seconds is accepted; 2,701 is rejected
  before manifest creation and produces diagnostics rather than a successful
  evidence bundle.
- Timing calibration is non-normative. The retained EXP-0051 analysis report
  records 134,291,460 work units. GitHub Actions run `32626186825`, fan-in job
  `97163239067`, records approximately 3.84 seconds for derivation freeze,
  0.67 seconds for analyzer resume, and 16.81 seconds for the independent
  recomputing validator. Using the slower independent-recomputation observation
  gives `800,000,000 / 134,291,460 * 16.81 ~= 100.1 seconds`. This is coarse
  hosted-run planning evidence only because A3 and A4 charge different work
  mixes; it does not prove A4 runtime. The normative controls remain the
  checked 800,000,000-unit counter, 900-second fan-in timeout, and 2,700-second
  hard campaign timeout.
- The dry-run honesty clause requires executed byte-level reachability transcripts,
  independent replica-3 overshoots, full analyzer/validator agreement, genuine
  tamper execution, and exact-bound/one-over cases. Hand-authored or constant
  rejection results fail the gate. The canonical
  `dry-run/a4-reachability-transcript.json` artifact and synthetic dry-run
  report bind its bytes, producer identities, and additive provenance entry;
  its schema fixes all 40 entries positionally in registry order. All registry
  fixtures remain claimed and not yet executed until that gate.
- An executed reference reachability harness (draft PR #74, branch
  `fable/a4-dryrun`, built by a different agent against the review-pass-4
  plan) is recorded as a design input only; the 17 plan ambiguities it
  surfaced (AMB-01..AMB-17) are each resolved by a stated decision in the
  plan's `harness_ambiguity_resolutions` and the normative text it cites.

The approved scope brief is copied byte-for-byte at
`design-inputs/a4-scope-approved.md`, SHA-256
`ead09d9cec961d018ed4845f14d825d2ae8da2d3329f12d6ae9ea2233e4eeeb7`.
`design-inputs/recompute_a4_work_terms.py` independently sums every registered
work term, checks the latest H4 total, and exercises the 800,000,000 /
800,000,001 comparator boundary from the immutable plan JSON.

## Schemas and status

The evidence-schema family is A4-specific: frozen layers retain complete
stage-discriminated canonical candidate arrays, H4 has independent root and
structural, and encoding results; structural candidates carry per-operation
compatible-occurrence bitmaps and reference the hashed occurrence-evidence
document, and final encoded candidates select one occurrence per operation
under one encoding-length equivalence class. Catalog-record predicates require
one record for each of the seven listed operations: the five
`TableDefs.Append` instances, `T1_ADD_TEXT`, and `T1_ADD_INDEX`. The field and
index operations participate in structural kind, identifier, exact-name, and
stored-length evaluation, while remaining catalog object perturbations that
claim no physical column-definition or index-node layout. The
analysis report contains A4 row-directory, locator, transition, reference/
bitmap, catalog-root, and catalog-field transcripts. A4 additionally defines
`dao-schema-snapshot.schema.json` and
`reachability-transcript.schema.json`. No analyzer, validator, worker, or workflow
implementation is part of this preregistration change, and acquisition remains
`BLOCKED` until the plan's execution gates are satisfied and disclosed
additively.

The claims block is fail-closed: only
`descriptive_provider_observation_only` is true. A4 claims no Rust correctness,
DAO compatibility, product support, support-matrix advancement, general Jet
behavior, exact allocation-set equality, physical column/index layout, row
value layout, relationships, Memo/OLE/long-value behavior, writer/update or
preservation behavior, free-space preference, DAO-exposed physical oracle,
`dao_differential`, or unexercised slot/base behavior.
