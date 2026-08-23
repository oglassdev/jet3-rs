# Implementation plan: current state to v1 completion

Status: directive plan, written 2026-08-23. It is an execution document for
agents, not a contract; the binding contracts remain `AGENTS.md`,
`docs/PROVENANCE.md`, `docs/validation/ACCEPTANCE.md`,
`docs/validation/EVIDENCE.md`, and `docs/validation/DAO_PROVIDER_BLOCKER.md`.
Where this plan and a contract disagree, the contract wins and the plan is
amended by a PR. Plan amendments never edit a preregistered experiment plan.

"v1 complete" means: every in-scope capability in
`docs/validation/support-matrix.json` is `implemented` and at its
`required_verification` level (`dao_differential` for 18 entries,
`independent_check` for 3) from commit-bound evidence on one exact clean
release commit, and `./scripts/acceptance.sh full` exits zero.

## 0. How to use this document

- Executor: a sol session (gpt-5.6-sol) working one phase step at a time.
- Reviewer: a *different* fresh session than the author, every PR, no
  exceptions (Section 6.2). The reviewer re-derives; it never re-reads the
  author's summary as proof.
- Human: decides every go/no-go gate in Section 2 and every escalation in
  Section 6.4. Until the human answers, sol waits.
- Read order before starting any phase: `AGENTS.md`; the phase's "binding
  inputs" list; the last three `EXP-` entries in `docs/PROVENANCE.md`.
- Never derive a fact from this plan when the cited file exists: open the
  file. This plan can be stale; the ledger and the code cannot be.

## 1. Current state snapshot (2026-08-23, `main` at `7bdaf73`)

### 1.1 Rust reader (`crates/jet3`)

| Stage (`docs/architecture/SEMANTIC_READER.md`) | State | Provenance |
| --- | --- | --- |
| 0 bounded opening (`database.rs`, `database_header.rs`, `header.rs`, `candidate.rs`, `commit_state.rs`) | implemented, internal only | `SRC-0004`, `SRC-0005`, `SRC-0013` |
| 1 page classification (`page_kind.rs`) | experimental; byte-zero tags `00`–`05` only | `SRC-0020`, `OBS-0002` |
| 2 allocation (`allocation.rs`, `allocation_traverse.rs`) | detached type-0/type-1 record and type-`05` bitmap decoding; format-neutral bounded chain traversal over caller-supplied pages; **map location, raw-reference following, extended page base return structured `Unsupported`** | `SRC-0020` |
| 3–6 catalog, table definitions, rows, values | not started; blocked on physical evidence | none |

Supporting code: `jet3-testkit` (canonical JSON/snapshot value types,
`classifier_snapshot.rs`), `jet3-cli` (diagnostics), `fuzz/` with nine
registered targets (`fuzz/targets.json`: `binary_cursor` … `allocation`),
`tests/manifest.json` with 236 manifested cases (G2 needs ≥300 meaningful
cases), `tools/validation/*` (repository contract, release-evidence overlay
validator, adapters — all adapters `disabled` or `forbidden` in
`docs/validation/evidence-policy.json`).

Support matrix: `format.header_and_version`, `format.pages_allocation_usage`,
`transactions.copy_on_write_atomic_publish`, and
`safety.malformed_input_bounds_and_limits` are `partial`/`internal_only`;
every other in-scope entry is `not_started`/`unverified`. No capability may
move without the evidence named in Section 5.

### 1.2 DAO oracle experiments (all hosted on `windows-2022`, x86
`DAO.DBEngine.36`, `dao360.dll` `03.60.9765.0`, SHA-256
`4cc28a5b…4d79ac`)

| Campaign | Entries | Result |
| --- | --- | --- |
| A1 `DAO-A1-ALLOCATION-MAPS-001` | `EXP-0037`–`EXP-0039` | hosted lane proved; `no_scientific_outcome` (analyzer/acquisition contract mismatch, disclosed in `EXP-0040`) |
| A2 `DAO-A2-ALLOCATION-MAPS-001` | `EXP-0040`–`EXP-0043` | decisive record layer **downgraded** (`EXP-0043`: 1,935 plan-literal starts survive); closed |
| A3 `DAO-A3-ALLOCATION-MAPS-001` + R2–R5 | `EXP-0044`–`EXP-0051` | **independently validated**: `global_map.record` = page 1, interval `[1915,2048)`, `set_means_not_in_use`, predicts holdout. Conversion, extended base, TDEF pointer pair: `no_outcome`. No capability moved. |
| A4 `DAO-A4-ROW-ANCHORED-MAPS-001` | `EXP-0052` (unmerged, PR #72, branch `origin/codex/a4-plan` at `4286129`) | preregistration under adversarial review; pass 4 verdict DO-NOT-MERGE with open P4-B1, P4-B2, P4-B3, P4-S1, P4-S2 (`/private/tmp/sol-a4-review.md`) |

What A3 established that Rust may rely on (only via `EXP-0051`, and only as
"narrow independently validated experimental input", never as a layout
claim): one global allocation record on page 1 for the D checkpoints, LSB-first
bitmap polarity `set_means_not_in_use`. What A3 did **not** establish: where
any table's map rows are, how type-1 slots reference tag-`05` pages, the
extended page base, any TDEF/catalog/row/value fact. A4 exists to supply
exactly those (approved scope: `/private/tmp/a4-scope-approved.md`, also
copied byte-for-byte into the A4 branch under
`oracle/windows-dao/experiments/a4/design-inputs/`).

### 1.3 Differential program

Decision recorded in `/private/tmp/sol-diff-proposal.md`: **Option 3**
(end-to-end semantic traversal; DAO and Rust emit the same canonical
schema/rows/values JSON) is the advancement path for every
`dao_differential` capability; **Option 1** (checkpoint consequence
differential) is a companion stress lane for `format.pages_allocation_usage`.
Nothing of either exists yet: protocol 1.0 still rejects `rust_read_dao`,
`dao_open_rust`, `dao_verify_rust_update`
(`oracle/windows-dao/scripts/validate_protocol.py`), and
`dao_differential_v1` is disabled in `evidence-policy.json`.

## 2. Dependency-ordered phases

Every phase has the same skeleton. Phases may not overlap unless marked
"parallel-safe". "PR" means one pull request against `main` from one
worktree; each PR gets its own reviewer session.

Common acceptance commands (run on every PR before requesting review):

```sh
just ready                                 # fmt, clippy, tests, docs, quick
python3 -B -m pytest oracle/windows-dao/tests tools/tests fuzz/tests -q
python3 tools/validate_repository_contract.py
python3 tools/reconcile_tests.py
git diff --check
```

Common sol guardrails (apply to every phase; Section 6.3 has the full list):
additive provenance only; never edit a hash-pinned plan; derive every number
from the cited bytes or plan and show the derivation in the PR body; never
emit a boolean that is not computed from data; exact-ceiling accept and
one-over reject tests for every bound.

### P0 — Close the A4 preregistration (PR #72)

- Goal: a merged, immutable A4 base plan that survives a fifth adversarial
  pass with zero blocking findings.
- Binding inputs: `/private/tmp/sol-a4-review.md` pass 4 (P4-B1, P4-B2,
  P4-B3, P4-S1, P4-S2, with the exact replacement text in each);
  `/private/tmp/a4-scope-approved.md` (600,000,000 work-unit cap is approved
  scope; 700M is not); `EXP-0043`–`EXP-0051` for the A3 calibration bytes.
- Deliverables (all on branch `codex/a4-plan`, squash nothing, additive
  commits): revised `a4-row-anchored-maps.plan.json`,
  `derivation-candidates.schema.json`, `analysis-report.schema.json`,
  `dry-run-report.schema.json` (+ new reachability-transcript schema),
  `test_a4_plan_contract.py`, README and `EXP-0052` text re-pinned to the
  new plan SHA-256. `name_length_endianness` becomes the candidate's
  `endianness` (P4-B2 repair), work total restated from the corrected term
  table, ceiling restored to exactly 600,000,000.
- Acceptance checks: common commands; additionally
  `python3 -B -m pytest oracle/windows-dao/tests/test_a4_plan_contract.py -q`
  and a recomputation script (committed under
  `oracle/windows-dao/experiments/a4/design-inputs/`) that sums every
  work term from the plan JSON and asserts the printed total; a test that
  accepts 600,000,000 and rejects 600,000,001.
- Reviewer must adversarially verify: (a) sum the 15 terms independently
  from the plan, not from the README; (b) serialize one fixture for each of
  H1 TDEF-MULTIPLE, H2 pre-role terminal, H3 pre-base terminal, H4
  CATALOG-RECORD-MULTIPLE and FIELD-MODEL-MULTIPLE against the new schemas
  and show they validate *without* downstream fields; (c) construct two H1
  candidates with equal `canonical_model_id` and distinct
  `canonical_candidate_id`; (d) check every `fixture_status` literal equals
  `claimed_reachable; execution_required_before_dispatch`.
- Sol failure modes here: copying A3's interval bounds (B5/P3-B5 recurred
  twice) — every bound must cite the A4 grammar term it derives from;
  "fixing" a blocker by editing the reviewer's replacement text into prose
  without changing the schema; raising a cap to make arithmetic fit.
- Go/no-go: human merges PR #72 only after the fifth review pass reports no
  blocking findings. No A4 code may start before merge.

### P1 — A4 lane implementation and dry runs

- Goal: a runnable A4 lane (worker, analyzer, synthetic generator,
  independent validator, workflow) whose pre-dispatch byte-level
  reachability transcript is executed, hash-bound, and disclosed.
- Binding inputs: merged A4 plan and schemas; `EXP-0049` (hosted lane
  rebinding rules); `EXP-0048` (dry-run disclosure shape); `EXP-0050`
  (R5-V01 binding, R5-L01 baselines, R5-T01 timeout); the A3 lane files
  listed in Section 3.3.
- Deliverables, as four PRs in this order, each with its own reviewer:
  1. `feat(a4): analyzer and synthetic generator` —
     `oracle/windows-dao/scripts/a4_analysis*.py`, `a4_generator*.py`,
     `a4_spec.py`, `a4_layers.py`, `a4_model.py`, tests `test_a4_*`.
     The generator parses the schedule and grammars from the plan JSON;
     no hand-typed counts.
  2. `feat(a4): independent validator` — `a4_independent_*.py`, written
     from plan and schemas only; the module must not import any
     `a4_analysis*`/`a4_model` symbol (add a test that asserts this by
     inspecting imports). Tamper suite T1–T5 equivalents from the plan.
  3. `feat(a4): hosted lane` — `run-a4-replica.ps1`, `scripts/a4/A4.*.ps1`,
     `a4_bundle*.py`, `a4_holdout.py`,
     `.github/workflows/windows-dao-a4.yml`, `test_windows_dao_a4_workflow`.
     Follow the rebind recipe in Section 3.3 exactly.
  4. `docs(a4): dry-run disclosure` — executed
     `oracle/windows-dao/experiments/a4/dry-run/` artifacts
     (`a3-calibration-report.json`, `a4-synthetic-report.json`,
     `a4-reachability-transcript.json`, `checksums.sha256`) plus an
     additive provenance entry (next free `EXP-` id; see Section 6.3 rule
     "ids") recording commands, commits, hashes, 40/40 measured survivor
     counts, and `acquisition_authorized = false`.
- Acceptance checks: common commands; `python3 -B
  oracle/windows-dao/scripts/a4_dryrun.py generate --replace-existing` then
  `… verify` reproduces byte-identical artifacts; the A3 calibration replay
  runs against a local read-only copy of the retained `EXP-0051` bundle
  (manifest SHA-256 `f1a644ab…fefaab`) and reproduces the H1 page-23 counts
  in review pass 4 (1,872 windows, 1,745,696 pairs, 1 masked-signature
  survivor per layout, 0 page→row and 1 row→page target-valid pairs).
- Reviewer must adversarially verify: re-run `a4_dryrun.py verify` in a
  clean clone and compare hashes; open the transcript and confirm each of
  the 40 entries has a baseline fixture hash, a mutation hash, a measured
  count, an analyzer result and a validator result that *differ in source*
  (analyzer process vs validator process logs); pick three predicates at
  random, mutate the fixture bytes by hand, and confirm the predicate flips.
- Sol failure modes: **fabricated reachability** — sol has twice reported
  dry-run reachability that was label playback. Guardrail: the transcript
  is produced only by `a4_dryrun.py`, which must refuse to run if any
  fixture carries a pre-set `accepted`/`valid`/`reachable` field; the
  reviewer greps fixtures for those keys. Second failure mode: sharing an
  analyzer pass with the validator (P3-B5 item 3) — the validator must read
  the bundle bytes itself.
- Go/no-go: human authorizes dispatch only after (a) the disclosure entry
  merged on `main`, (b) `windows-dao-hosted.yml` re-proved the `windows-2022`
  image and provider hash within the last 7 days, (c) the dispatch commit is
  exact, clean, and pushed.

### P2 — A4 hosted acquisition and result

- Goal: one retained, independently validated A4 bundle, recorded
  additively as the A4 result entry.
- Binding inputs: everything in P1; `EXP-0051` as the template for the
  result entry; `EXP-0042` "prior failed dispatches" as the template for
  recording infrastructure failures.
- Deliverables: workflow dispatch of `windows-dao-a4.yml` with
  `execute_a4_campaign=true` on the exact clean pushed commit; local
  read-only copy of the bundle under the scratchpad; one PR
  `docs: record A4 first hosted result (EXP-nnnn)` that adds the result
  entry and, if any of H1–H4 is decisive, nothing else — no code, no
  matrix change.
- Acceptance checks: local re-run of producer validation and the
  independent validator over the downloaded bundle reproduces the manifest
  and report hashes byte-for-byte; `campaign_elapsed_seconds ≤ 2700`.
- Reviewer must adversarially verify: the bundle's `plan/` inventory hashes
  equal the merged plan and revision hashes; `holdout-structure-receipt`
  says `page_bytes_exposed_to_analyzer = false`; the entry's layer results
  are copied from the report JSON, not from the workflow summary; the
  `claims` block is all false except
  `descriptive_provider_observation_only`.
- Sol failure modes: treating an infrastructure failure as a scientific
  outcome or vice versa; re-dispatching after editing anything other than
  workflow glue (any analyzer/plan/schema edit after dispatch requires a
  new experiment id). Hosted failures beyond plain infrastructure
  (download, runner image drift, timeout) are an escalation (Section 6.4).
- Go/no-go: human reads the result entry. Outcomes: H1–H3 decisive → P3
  may start. H4 decisive → P4 may start. Any layer `no_outcome` → that
  layer's facts go to the next experiment (Section 3.1) and the dependent
  Rust stage stays blocked.

### P3 — Rust Stage 2 completion (allocation owned-page traversal)

- Goal: remove the three `Unsupported` steps in `allocation_traverse.rs`
  (map location, raw-reference following, extended page base) using only
  A4-established facts; `format.pages_allocation_usage` becomes
  `implemented`, verification stays `internal_only`.
- Binding inputs: A4 result entry (H1–H3); `SRC-0020`; the resource
  contract in `SEMANTIC_READER.md`.
- Deliverables: `crates/jet3/src/map_location.rs` (TDEF page → two row
  locators, cites the A4 result id on every constant),
  `usage_map.rs` (row-anchored type-0/type-1 record view with
  checkpoint-independent row bounds from the row directory), extension of
  `allocation_traverse.rs` to follow type-1 slots to tag-`05` pages with
  base `slot_ordinal * 16352 + bit_index` *only if* H3 was decisive;
  `DatabaseReader::owned_pages(table_root)` bounded iterator; tests per
  invariant (exact capacity edge 1,024 bits inline; one-over; zero slot;
  cycle; self-reference; reference beyond captured length); fuzz target
  `usage_map_traverse` registered in `fuzz/targets.json` with manifested
  seeds; `tests/manifest.json` entries; `repository-contract.json`
  assertion-file hashes and provenance ids updated.
- Acceptance checks: common commands; `cargo fuzz run usage_map_traverse
  -- -max_total_time=60`; `scripts/check-source-size.sh` (≤800 lines).
- Reviewer must adversarially verify: every numeric constant in the new
  modules greps to a provenance id whose entry actually states that value;
  the inline-boundary tests are at 1,024 exactly and 1,025; no path reads a
  page that was not charged to the budget (count `page_visit` charges in a
  test against a 5-page synthetic file).
- Sol failure modes: promoting an A4 calibration number (e.g. page 23,
  offset 1915) into a constant — calibration values are test fixtures, not
  format constants; decoding booleans from a fixture JSON instead of from
  the page bytes.
- Go/no-go: reviewer sign-off plus human confirms the matrix diff is only
  `implementation: partial → implemented`.

### P4 — Rust Stage 3 minimal catalog bootstrap

- Goal: stream user object records (name bytes + declared encoding class,
  kind, identifier, referenced TDEF page) from the allocation-admitted
  catalog root; `schema.catalog_and_table_definitions` → `partial`.
- Binding inputs: A4 H4 result; P3 API.
- Deliverables: `crates/jet3/src/catalog.rs` (+ `catalog_record.rs` if
  >800 lines), `CatalogError`, fuzz target `catalog_parsing` (named by G5),
  tests, manifest entries, `jet3-cli list-objects` using only the public
  boundary.
- Acceptance, reviewer, failure modes: as P3. Additional reviewer check:
  names are retained as raw bytes plus the A4 equivalence class; no UTF-8
  conversion is claimed lossless unless H4 discriminated it.
- Go/no-go: as P3.

### P5 — A5 table-definition experiment → Rust Stage 4

- Goal: physical provenance for column definitions (type code, size, flags,
  fixed/variable class, ordinal), index definitions (fields, direction,
  unique/primary flags, root page), and the TDEF record layout; then
  `table_definition.rs`.
- Binding inputs: Section 3.1 A5 design; A4 result; P4 API.
- Deliverables: A5 plan family under `oracle/windows-dao/experiments/a5/`
  (preregistration entry, R-revisions, dry-run disclosure, result entry —
  four provenance entries minimum), lane rebound per Section 3.3, then
  Rust `table_definition.rs`, `index_definition.rs`, fuzz target
  `table_definition_parsing`, tests, manifest entries.
- Acceptance/reviewer/failure modes: P1–P3 pattern. Matrix:
  `schema.catalog_and_table_definitions` → `implemented`;
  `indexes.primary_unique_non_unique` and
  `indexes.composite_ascending_descending` → `partial` (definitions only,
  no tree traversal).
- Go/no-go: human, after the A5 result entry.

### P6 — A6 row experiment → Rust Stage 5

- Goal: row directory, deleted/lookup flags, null map, fixed/variable
  regions, variable-offset table, overflow/continuation pointers; then
  `row.rs` streaming iterator.
- Deliverables: A6 family; `row.rs`, `row_directory.rs`, fuzz target
  `row_parsing`; `rows.streaming_read` → `implemented`/`internal_only`;
  `values.null_fixed_variable` → `partial`.

### P7 — A7 value experiment → Rust Stage 6

- Goal: every DAO Jet 3 table type at null/min/representative/max (G3
  bullet 4): Boolean, Byte, Integer, Long, Currency, Single, Double,
  Date/Time, Text (with code page), Binary, Memo, OLE (long-value chain),
  GUID, replication id; long-value inline vs single-page vs multi-page
  chains and termination.
- Deliverables: A7 family; `value.rs`, `long_value.rs`, `text.rs` (raw
  bytes retained always), fuzz target `long_values`; all five `values.*`
  entries → `implemented`/`internal_only`.

### P8 — Differential read program (Option 3, read legs)

- Goal: `DAO-READ-*` scenarios, the shared snapshot contract, the
  `dao_differential_v1` adapter, and the first exact-commit `dao_bundle`
  — moves every read capability to `dao_differential`.
- Binding inputs: Section 5; `EVIDENCE.md` bundle list; `ACCEPTANCE.md`
  G3; `oracle/windows-dao/protocol/v1_1`.
- Deliverables: protocol `v1_2` (snapshot schema, scenario inventory,
  `rust_read_dao` mode enabled), `crates/jet3-testkit` snapshot producer
  bound to the public `DatabaseReader` API only, `jet3-cli snapshot`,
  `tools/validation/release_evidence_adapters.py::dao_differential_v1`
  (fail closed on: missing scenario, missing coverage-receipt branch,
  dirty tree, commit mismatch), policy flip to `enabled`, workflow
  `windows-dao-differential.yml`, matrix + evidence pointers.
- Go/no-go: human; Section 5.5 lists the exact preconditions.

### P9 — Writer: create, insert/update/delete, index maintenance,
relationships (Rust) + independent structural verifier

- Goal: `database.create_empty`, `schema.create_drop_tables`,
  `rows.insert_update_delete`, `indexes.crud_maintenance`,
  `relationships.create_drop_preserve_metadata`,
  `output.deterministic_configuration` implemented; independent structural
  verifier (`tools/validation/` Python, written from provenance, not from
  the Rust reader) passes on all Rust output (G4).
- Binding inputs: all A4–A7 results; if writer needs facts not observed
  (free-space preference, index B-tree node layout, `MSysRelationships`
  rows), run A8 (index trees + relationships) and A9 (allocator/writer
  consequences, Option 1 shape) first — Section 3.1.
- Deliverables: `writer/` modules under 800 lines each; `atomic.rs` already
  provides publication; `independent_writer_v1` adapter enabled; G4 fault
  injection tests per stage.

### P10 — Differential write/update legs (Option 3 legs 2 and 3)

- Goal: `DAO-WRITE-*` (DAO opens Rust files; canonical result equals
  declarative input) and `DAO-UPDATE-*` (Rust mutates DAO file; DAO reports
  intended change and preservation of unrelated schema, rows, indexes,
  relationships, long values, raw-preservation fields) → every writer
  capability to `dao_differential`.

### P11 — Release gates G2, G5, G6, G7, G8

- Goal: ≥300 manifested cases; G5 ten-minute fuzz per target with
  256 MiB/5 s malformed-corpus limits; G6 ≥90 % line / ≥80 % region
  coverage and ≥85 % mutation score with survivor ledger
  (`docs/validation/G6_EVIDENCE.md`); G7 Criterion baselines through
  100,000 rows; G8 cross-platform aggregate (`tools/ci_evidence.py
  verify-aggregate`), clean consumer project, release artifacts.
- Go/no-go: `./scripts/acceptance.sh full` exits zero from a clean tree on
  the release commit; human tags the release.

## 3. Experiment roadmap

### 3.1 Campaign sequence and what each must learn

Each campaign is a new experiment id, new base plan, freeze-before-holdout,
three role-rotated replicas, independent validator written from the plan,
and a claims block that is all-false except
`descriptive_provider_observation_only`. Every campaign reuses the hosted
lane via Section 3.3.

| Campaign | Unblocks | Must learn (decisive layers) | DAO operations added at checkpoints |
| --- | --- | --- | --- |
| A4 (EXP-0052) | Stage 2 close, Stage 3 minimum | H1 TDEF→map-row locators; H2 row identity/role; H3 tag-1 slot→tag-05 reference + base; H4 catalog root, kind/id/name field model | `CreateDatabase(dbVersion30)`, one-at-a-time `CreateTableDef`/`Append`, `CreateField` (Long, Text), `CreateIndex` nonunique, `TableDefs.Delete`, recreate, 32-row batches, delete-all, reinsert, idle reopen; canonical DAO schema snapshot every checkpoint |
| A5 | Stage 4 | TDEF record layout: column count, per-column type code/size/flags/ordinal/fixed-or-variable, variable-column index; index count, per-index field list and direction, unique/primary/required flags, root page reference; name → column binding | one table; append one field per checkpoint for each of the 13 DAO Jet 3 types with boundary sizes (Text 1/255, Binary 255); `CreateIndex` primary / unique / nonunique / composite ascending+descending; `Indexes.Delete`; field rename via new TableDef |
| A6 | Stage 5 | row directory entry encoding incl. deleted/lookup bits; row header (column count, null map position/polarity, variable-offset table); fixed region order; row motion on update; overflow/continuation link and termination; page free-space field | rows of fixed-only, variable-only, mixed, all-null, max-size (page-edge −1/0/+1); `Edit`/`Update` growing and shrinking a row; `Delete` without compact; `CompactDatabase` explicitly **excluded** |
| A7 | Stage 6 | scalar encodings per type (byte order, Date/Time double epoch, Currency i64 scale, GUID layout, Boolean null/false/true), Text code page at CP1252 plus one pinned second ANSI page, Memo/OLE long-value pointer (inline / single page / multi-page chain), chain termination and length | per-type min/max/representative/null rows; Memo at 1, 64, 2,036, 4,096, 65,536 bytes; OLE binary patterns; `AppendChunk` |
| A8 | `indexes.*` traversal + `relationships.*` | index B-tree page layout, entry encoding per key type, leaf/branch links, root from A5; `MSysRelationships` rows and attributes | keys at every A5 type; ordered inserts vs reverse vs random; `CreateRelation` with/without cascade flags; `Relations.Delete` |
| A9 (Option 1 shape) | writer allocation decisions, `rows.insert_update_delete`, `indexes.crud_maintenance` | allocation consequence predicates for grow/delete/reinsert/drop/recreate; free-space selection observables; usage-map row growth into indirect form | reuse A3/A4 schedule machinery with Rust-produced mutations inserted between DAO checkpoints (the first campaign where Rust touches a file) |

Rule: A5–A9 may not be preregistered until the previous campaign's result
entry is merged, because each plan's candidate grammars must cite the
facts the previous result established (A4's H1 page-23 locator example is
the model). If a layer returns `no_outcome`, the next campaign carries that
layer forward as its first hypothesis; do not widen a running plan.

### 3.2 Preregistration checklist (per campaign, before any code)

1. Scope brief approved by the human and committed byte-for-byte under
   `design-inputs/` with its SHA-256 in the plan.
2. Plan JSON with: `experiment_id`, 25-checkpoint schedule, row algorithm,
   candidate grammars per hypothesis, ordered predicate registry with
   evaluation rule / status semantics / `exact|minimum|allowed_ranges`
   count contract / claimed fixture, freeze rule per stage, work-term table
   whose sum is recomputed by a committed script, bounds with
   accept-at-ceiling/reject-one-over tests, `claims` block, dry-run honesty
   clause, implementation-rebinding source rule.
3. Schema family copied from the previous campaign and re-frozen with
   before/after hash table in the provenance entry (R5-V02 shape).
4. Focused contract test; repository contract passes.
5. Adversarial review passes until zero blocking findings (expect 3–5).
6. Merge; then P1-style implementation; then executed dry-run disclosure;
   then human dispatch authorization.

### 3.3 Hosted-lane rebind recipe (A3 → A4 → A5 …)

Source lane: `.github/workflows/windows-dao-a3.yml` (746 lines),
`oracle/windows-dao/scripts/run-a3-replica.ps1`,
`oracle/windows-dao/scripts/a3/{A3.Worker,A3.PageStore,A3.Progress,Download-A3Artifact}.ps1`,
`a3_bundle*.py`, `a3_holdout.py`, `a3_analysis*.py`,
`a3_independent_*.py`, `oracle/windows-dao/tests/test_windows_dao_a3_workflow.py`.

Copy, then change **only** these, and nothing else without a provenance
entry explaining why (`EXP-0049` is the precedent):

1. Experiment id, plan path, revision-chain paths and hashes, document and
   artifact names (`windows-dao-aN-*`), evidence types (`dao_aN_*`).
   The contract job must refuse any plan whose `experiment_id` is not
   exactly the new id (A3: workflow line ~92).
2. Checkpoint schedule and the worker's DAO operations, generated from the
   plan JSON, never typed into PowerShell.
3. Baseline capture points (R5-L01 shape) if the new schedule changes the
   relative-target roles.

Keep invariant: `workflow_dispatch` with one boolean input
`execute_aN_campaign` default `false`; `permissions: actions: read,
contents: read`; `concurrency` group per ref with `cancel-in-progress:
false`; jobs `contract` (windows-2022, 15 min, contract tests only — PR #46
lesson), `aN-replica` matrix of three independent jobs (windows-2022 pinned
— PR #47 lesson; `timeout-minutes: 37` ≥ 1,700 s worker ceiling + setup),
`fan-in` (`timeout-minutes: 15` = 900 s plan bound, asserted by the workflow
test). Provider preflight before COM activation: x86 process, prog id,
CLSID, `dao360.dll` path, file version `03.60.9765.0`, SHA-256
`4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`; any
drift fails closed. Artifact download in fan-in uses the REST helper
(`Download-A3Artifact.ps1`: five-attempt retry through the Actions API),
**not** `actions/download-artifact` (three A3 runs failed there — PRs #66,
#67). Freeze order: download replicas 1 and 2 → derive and retain
`analysis/derivation-candidates.json` + completed freeze marker → only then
download replica 3 → spawned holdout process emits the structure receipt →
analyzer resumes from retained frozen state. Campaign timing: read
`run_started_at` from the Actions API; `campaign_elapsed_seconds =
floor(created_utc − run_started_at)`; refuse to write the manifest above
2,700 s; retained-bundle upload only on job success; diagnostics
`if: always()`. Transport ZIP digests are advisory; content hashes are
authoritative (PR #48 lesson). Independent validator runs as a separate
process in the same job and its report is uploaded separately from the
bundle.

Before dispatch, run `windows-dao-hosted.yml` to re-prove the image and
provider (image identity may drift between days — `EXP-0036`, run
`32437968174`).

## 4. Rust reader roadmap by stage

| Stage | Module(s) | Unblocking evidence | Phase | Fuzz target (G5 name) | Matrix effect |
| --- | --- | --- | --- | --- | --- |
| 2 close | `map_location.rs`, `usage_map.rs`, `allocation_traverse.rs` | A4 H1–H3 result entry | P3 | `usage_map_traverse` (new) | `format.pages_allocation_usage` → `implemented` |
| 3 | `catalog.rs`, `catalog_record.rs` | A4 H4 | P4 | `catalog_parsing` | `schema.catalog_and_table_definitions` → `partial` |
| 4 | `table_definition.rs`, `index_definition.rs` | A5 | P5 | `table_definition_parsing` | `schema.catalog_and_table_definitions` → `implemented`; `indexes.primary_unique_non_unique`, `indexes.composite_ascending_descending` → `partial` |
| 5 | `row.rs`, `row_directory.rs` | A6 | P6 | `row_parsing` | `rows.streaming_read` → `implemented`; `values.null_fixed_variable` → `partial` |
| 6 | `value.rs`, `long_value.rs`, `text.rs` | A7 | P7 | `long_values` | five `values.*` → `implemented` |
| index traversal | `index_tree.rs` | A8 | P9 | `index_traversal` | `indexes.*` → `implemented` |
| writer | `writer/*.rs` | A8, A9 | P9 | existing + `allocator` | writer capabilities → `implemented` |

Rules for every stage module (from `SEMANTIC_READER.md`, restated as
checks the reviewer runs):

- Each module consumes only the typed output of the stage above; grep for
  `[u8]` slicing outside the physical module that owns the constant.
- One caller-owned `ResourceBudget`; count charges in tests: page visits,
  chain depth, item work, allocation bytes, decoded bytes.
- Structured error enum per stage nesting the previous stage's error; no
  `String` errors; no attacker bytes in diagnostics.
- Exact-boundary tests: page-edge −1/0/+1 for every variable-sized
  structure; count ceilings accept-at/reject-one-over; cycle and
  self-reference rejection for every chain.
- Verification state stays `internal_only` until Section 5; "implemented"
  is an implementation claim only.

## 5. Differential program

### 5.1 Scenario ids (stable; content hash changes on any edit)

Prefix rule from protocol 1.0: `DAO-READ-*` (`rust_read_dao`),
`DAO-WRITE-*` (`dao_open_rust`), `DAO-UPDATE-*` (`dao_verify_rust_update`).
Inventory file: `oracle/windows-dao/protocol/v1_2/scenarios.json`, each
entry `{id, capability_ids, boundary, operation, generator_recipe,
expected_snapshot_sha256 (write legs), preserve_paths (update legs)}`.
Minimum set for G3 (≥100 scenarios) grouped by capability:

- `DAO-READ-OPEN-*` (`database.open`, `format.header_and_version`): fresh
  empty; after compact-free growth; largest supported size.
- `DAO-READ-ALLOC-*` (`format.pages_allocation_usage`): small inline; inline
  capacity −1/0/+1; delete/reinsert reuse; drop/recreate; idle reopen;
  inline→indirect; each extended slot boundary; multiple tables.
- `DAO-READ-SCHEMA-*`: every column type, every index form, relationships.
- `DAO-READ-ROWS-*`, `DAO-READ-VALUES-*`: per type null/min/representative/
  max/page-boundary; Memo/OLE single- and multi-page; code pages.
- `DAO-WRITE-*` mirrors of each read scenario, produced by Rust.
- `DAO-UPDATE-*`: insert/update/delete/create-table/drop-table/index
  add-drop/relationship add-drop, each with a preservation leg.
- Failure scenarios for each create/drop/CRUD/index/relationship form.

### 5.2 Snapshot contract

`canonical_semantic_snapshot` v1 (schema under `protocol/v1_2/`): database
`{page_count, user_tables[]}`; table `{name_bytes_hex, name_encoding_class,
columns[] {ordinal, name, type, size, flags…}, indexes[] {name, fields[]
{name, descending}, primary, unique, required}, row_count, rows_digest,
rows[] in primary-key or row-id order}`; value encoding per type with
lossless raw hex alongside any converted form; relationships[]. Both
producers serialize with `jet3-testkit::canonical_json` ordering rules.
DAO never emits allocation internals; Rust additionally emits a separate
`coverage-receipt.json` bound to the MDB SHA-256 listing traversed
allocation branches and an allocated-set digest (Option 3 requirement).

### 5.3 `dao_differential_v1` adapter (`tools/validation/release_evidence_adapters.py`)

Fail closed unless: exact clean commit; provider identity matches the
pinned hash; every required scenario id for the capability present with
both snapshots and equal canonical bytes; coverage receipt lists every
branch the scenario inventory marks `required_branches`; update legs have
a `preservation_diff` result from `tools/verify_preservation_diff.py`
with zero unexpected differences; no scenario `skipped`. Maximum
verification level is intrinsic to the adapter (`dao_differential`);
`evidence-policy.json` only flips `status` to `enabled` in the same PR
that adds its tests (accept a complete overlay; reject one missing
scenario; reject one altered byte in a snapshot).

### 5.4 Legs

1. Read leg (P8): DAO generates `dbVersion30` file → close/reopen → DAO
   snapshot; Rust snapshot + receipt → compare.
2. Write leg (P10): Rust creates file from declarative input → DAO opens,
   snapshots → compare with expected.
3. Update leg (P10): DAO generates → Rust mutates via `atomic_update` → DAO
   reopens, snapshots → compare intended change; preservation diff over
   `preserve_paths`.

### 5.5 When the matrix may move

A capability moves to `dao_differential` in the same PR that adds: (1) the
scenario inventory rows and matching test-only Rust files in
`tests/manifest.json`; (2) the exact-commit `dao_bundle` evidence pointer
with manifest SHA-256; (3) the enabled adapter's passing report; (4) a
provenance entry for the bundle. The PR must show
`./scripts/acceptance.sh full` output for G3 as PASS for that capability's
scenarios. Earlier bundles (M1, A3, A4 …) are design inputs only. A
verification-state change in any other kind of PR is a Section 6.4
escalation and must be reverted.

## 6. Process rules

### 6.1 Worktrees and sessions

- One worktree per branch/PR (`git worktree add
  ~/.herdr/worktrees/access97-rs/<branch>`); never `cd` to another tree.
- One fresh session per task: diagnose → fix, review, dry-run, hosted run,
  post-run are separate sessions. Tear the worktree down after merge.
- Stash is shared: never bare `git stash`; prefer a WIP commit.
- Evidence work happens in a detached, clean checkout at the exact
  evidence commit; fixes happen in a separate worktree on the PR branch.

### 6.2 Reviewer ≠ author

Every PR is reviewed by a session that did not write it, using the review
template below, and the review is appended to `/private/tmp/sol-<topic>-
review.md` (or committed under `design-inputs/` when it becomes a plan
input, hash-pinned). A review that only restates the PR description is
invalid; it must contain the reviewer's own recomputed numbers, executed
commands with output, and at least one attempted falsification per
blocking claim. Verdict literals: `MERGE`, `MERGE-AFTER-NITS`,
`DO-NOT-MERGE`. The human merges; sol never merges.

### 6.3 Sol guardrails (apply always)

- **Decode bytes, never trust booleans.** Any `accepted`, `valid`,
  `reachable`, `passed` field in a fixture or intermediate JSON is
  input, not result. Results come from executing the analyzer/validator
  on bytes. Harnesses refuse fixtures carrying result fields.
- **Derive, don't copy.** Every bound, count, offset, and hash is
  recomputed from the cited artifact by a committed script whose command
  and output appear in the PR body. Copied A3 bounds were rejected three
  times in A4 review.
- **Additive provenance only.** New `EXP-` entries at the end of the
  experiments section; never edit a merged entry; never edit a hash-pinned
  plan (make `-rN` revision files). Ids: run `grep -n '^### EXP-'
  docs/PROVENANCE.md | tail -1` on current `main` immediately before
  opening the PR, take the next number, and state it in the PR title; if
  another open PR reserves it, say so in the entry (`EXP-0050` precedent).
- **Exact-ceiling accept / one-over reject** test for every bound, timeout,
  count, and size (2,700/2,701 s; 600,000,000/600,000,001 units; 1,024/
  1,025 bits; 800/801 lines).
- **Schema hash cycles:** never put a plan hash inside a schema `const`;
  pin it in checked code (`EXP-0050` R5-V01 rationale).
- **REST download helper, 900 s fan-in:** hosted fan-in uses the retrying
  Actions-API helper and must fit 900 s; if it cannot, the fix is to make
  fan-in cheaper, not to raise the bound (PR #68 precedent).
- **No claim words.** Never write "verified", "compatible", "supported",
  "DAO verified" outside the evidence vocabulary; result entries' `claims`
  blocks stay all-false except the descriptive flag.
- **No matrix edits** except in the PR kinds named in Sections 2 and 5.5.
- **No 800+ line production files; no `unsafe`; no panics.**
- **Scope lock.** A PR does one deliverable from one phase step. Anything
  discovered outside it becomes a note in the PR body, not a change.

### 6.4 Escalate to the human and stop

Stop, write the question in the PR body or the session transcript, and
wait, when any of these occurs:

1. Plan-text ambiguity: two readings of a preregistered plan lead to
   different code (`EXP-0045` precedent — an additive revision is needed).
2. Any evidence-schema change, including "just adding a field".
3. Any verification-state advancement or any wording that implies one.
4. A hosted run failure that is not plainly infrastructure (download,
   image drift, timeout in setup). Analyzer exceptions, validator
   disagreement, unexpected predicate terminals, and bound rejections are
   scientific events; never re-dispatch to "see if it passes".
5. A reviewer and author disagree after one exchange.
6. A dependency on a fact not in `docs/PROVENANCE.md`.
7. A need to touch a file outside the PR's scope lock.

### 6.5 Session prompt templates

Fix session:

```text
Worktree <path>, branch <name>, task: <one deliverable from IMPLEMENTATION_PLAN.md §<phase>>.
Read AGENTS.md, docs/plans/IMPLEMENTATION_PLAN.md §<phase>, and <binding inputs>.
Apply exactly the replacement text in <review file> findings <ids>; do not reinterpret.
Recompute every number you write with a committed script; paste command + output in the PR body.
Do not edit: <immutable plan paths>, support-matrix.json, any merged EXP- entry.
Run: just ready; pytest oracle/windows-dao/tests tools/tests -q; validate_repository_contract.py.
Stop and ask if §6.4 applies. Open a draft PR titled '<type>(<scope>): <summary>' and reply with the URL.
```

Review session:

```text
Adversarial review of PR <n> (<branch>@<sha>) against IMPLEMENTATION_PLAN.md §<phase> "Reviewer must adversarially verify".
Fresh clone; do not read the PR description until after your own recomputation.
For each blocking claim, attempt to falsify it by executing code or decoding bytes; record command and output.
Report findings as B<n> (blocking), S<n> (should-fix), N<n> (nit) with exact replacement text.
Verdict literal: MERGE | MERGE-AFTER-NITS | DO-NOT-MERGE. Append to /private/tmp/sol-<topic>-review.md pass <k>. Edit no repository file.
```

Dry-run session:

```text
Execute the preregistered dry run for <campaign> at clean commit <sha>: <exact command from the plan>.
Retained input: <local read-only bundle path>, manifest sha256 <hash> — verify the hash before use; open no holdout.
Produce the transcript only through the harness; refuse if any fixture contains accepted/valid/reachable/passed fields.
Then run '<harness> verify' in a second clean clone and confirm byte-identical artifacts.
Write the additive disclosure entry (next free EXP id) with commands, commits, hashes, 40/40 measured counts, acquisition_authorized=false.
```

Hosted-run session:

```text
Precondition check only, then stop for human dispatch: disclosure entry merged on main (cite PR); windows-dao-hosted.yml re-proof within 7 days (cite run id, image, dao360 hash); HEAD exact, clean, pushed (git status --porcelain empty; git rev-parse HEAD == origin/main).
After the human dispatches: poll the run; on infrastructure failure record run id + step in a notes file and stop; never re-dispatch yourself.
```

Post-run session:

```text
Download bundle + validation artifacts for run <id> to the scratchpad (read-only copy).
Re-run producer validation and the independent validator locally; reproduce manifest and report hashes.
Write the additive result entry using EXP-0051 as the template: environment, timing, artifact ids/sizes/hashes, layer results copied from analysis-report.json, predicate tally (all registered ids accounted for), prior failed attempts, claims block, 'no capability advances'.
Open PR 'docs: record <campaign> first hosted result (EXP-nnnn)'. Change no code and no matrix entry.
```

## 7. Effort (elapsed, one executor + one reviewer + human gates)

| Phase | Estimate | Basis |
| --- | --- | --- |
| P0 close A4 plan | 1–2 days | A3 needed R2–R5 over ~8.5 h; A4 is on pass 4 with 3 blockers |
| P1 A4 lane + dry runs | 3–5 days | A3 analyzer/validator/lane were PRs #53–#64 over ~2 days with parallel authors |
| P2 A4 hosted + result | 0.5–2 days | A3: five infra failures then a 22-minute run |
| P3 Stage 2 close | 3–5 days | three new modules, fuzz target, contract re-pins |
| P4 Stage 3 minimal catalog | 3–4 days | |
| P5 A5 + Stage 4 | 2–3 weeks | largest grammar (13 types × boundaries × index forms) |
| P6 A6 + Stage 5 | 2–3 weeks | |
| P7 A7 + Stage 6 | 2–3 weeks | long-value chains dominate |
| P8 read differential + adapter | 2–3 weeks | proposal estimate 6–10 weeks assumed stages 3–6 absent; adapter foundation exists |
| P9 writer + independent verifier (+ A8, A9) | 6–10 weeks | proposal estimate |
| P10 write/update legs | 3–4 weeks | |
| P11 release gates | 2–3 weeks | G6/G7 tooling partly exists (`tools/run_g6_coverage.py`) |
| **Total** | **~6–8 months elapsed** | serial by construction: each campaign depends on the previous result |

Parallel-safe pairs: P3 with P1 step 2; P8 contract/schema work with P5–P7;
P11 fuzz/coverage tooling at any time after P4.
