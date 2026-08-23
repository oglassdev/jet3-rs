# Implementation plan: current state to v1 completion

Status: directive plan, written 2026-08-23, revised 2026-08-23 after the
executability review (`docs/plans/design-inputs/` holds the committed copies
of its binding inputs). It is an execution document for agents, not a
contract; the binding contracts remain `AGENTS.md`, `docs/PROVENANCE.md`,
`docs/validation/ACCEPTANCE.md`, `docs/validation/EVIDENCE.md`, and
`docs/validation/DAO_PROVIDER_BLOCKER.md`. Where this plan and a contract
disagree, the contract wins and the plan is amended by a PR. Plan amendments
never edit a preregistered experiment plan.

"v1 complete" means: all 21 in-scope capabilities in
`docs/validation/support-matrix.json` are `implemented` and meet their
`required_verification` level: 17 at `dao_differential` and 4 at
`independent_check` (`database.validate`,
`transactions.copy_on_write_atomic_publish`,
`output.deterministic_configuration`,
`safety.malformed_input_bounds_and_limits`), from commit-bound evidence for
one exact clean release commit, and `./scripts/acceptance.sh full` exits zero.
Recompute this count before relying on it:

```sh
python3 -c "import json,collections; m=json.load(open('docs/validation/support-matrix.json')); print(collections.Counter(c['required_verification'] for c in m['capabilities']))"
```

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
- Binding inputs are committed files with full SHA-256 values. Private temp
  paths (`/private/tmp/...`) are convenience copies only and are never
  binding inputs.

## 1. Current state snapshot (2026-08-23, `main` at `7bdaf73`)

### 1.1 Rust reader (`crates/jet3`)

| Stage (`docs/architecture/SEMANTIC_READER.md`) | State | Provenance |
| --- | --- | --- |
| 0 bounded opening (`database.rs`, `database_header.rs`, `header.rs`, `candidate.rs`, `commit_state.rs`) | implemented, internal only; **no Jet 3 version discriminator and no physical unencrypted-state check** (`SEMANTIC_READER.md` "Exact blockers", first bullet) | `docs/PROVENANCE.md` entries "Microsoft Jet file signatures", "Jet database page size", and "Jet 3 database-header commit region" |
| 1 page classification (`page_kind.rs`) | experimental; byte-zero tags `00`–`05` only | `docs/PROVENANCE.md` entry "Secondary documentation of Jet page, row-slot, and usage-map primitives"; `OBS-0002` |
| 2 allocation (`allocation.rs`, `allocation_traverse.rs`) | detached type-0/type-1 record and type-`05` bitmap decoding; format-neutral bounded chain traversal over caller-supplied pages; **map location, raw-reference following, extended page base return structured `Unsupported`** | `docs/PROVENANCE.md` entry "Secondary documentation of Jet page, row-slot, and usage-map primitives" |
| 3–6 catalog, table definitions, rows, values | not started; blocked on physical evidence | none |

Supporting code: `jet3-testkit` (canonical JSON/snapshot value types,
`classifier_snapshot.rs`), `jet3-cli` (diagnostics), `fuzz/` with nine
registered targets (`fuzz/targets.json`: `binary_cursor`, `binary_writer`,
`jet_header`, `jet3_page`, `raw_jet3_candidate`, `database_opening`,
`commit_state`, `page_classification`, `allocation`), `tests/manifest.json`
with 236 manifested cases (G2 needs ≥300 meaningful cases),
`tools/validation/*` (repository contract, release-evidence overlay
validator, adapters — all adapters `disabled` or `forbidden` in
`docs/validation/evidence-policy.json`).

Support matrix: `format.header_and_version`, `format.pages_allocation_usage`,
`transactions.copy_on_write_atomic_publish`, and
`safety.malformed_input_bounds_and_limits` are `partial`/`internal_only`;
every other in-scope entry is `not_started`/`unverified`. Implementation and
`internal_only` transitions occur only in the phase steps that name their
source/test evidence. Verification above `internal_only` occurs only through
Section 5 and P11's named independent-report adapters.

What the checked evidence tooling permits today (verified against the
source; re-read these lines before P8T):

- `tools/validation/evidence.py` `_validate_release_eligibility`
  (lines 140–159): every `independent_report` and `dao_bundle` reference
  must name the exact current `HEAD` and a clean worktree; the referenced
  file must be inside the repository.
- `tools/validation/evidence.py` lines 316–320: every `dao_bundle` reference
  is then rejected unconditionally ("DAO bundle semantic validation is not
  integrated; DAO evidence fails closed").
- `tools/validation/release_evidence_adapters.py::checked_adapter_spec`
  (lines 47–50): `dao_differential_v1` is intrinsically `unavailable`;
  flipping `evidence-policy.json` to `enabled` alone is rejected.

Consequence: a committed matrix reference cannot name the commit that
contains it. The exact-commit transition therefore needs the tooling
amendment in P8T before any verification state can move (Section 5.5).

### 1.2 DAO oracle experiments (all hosted on `windows-2022`, x86
`DAO.DBEngine.36`, `dao360.dll` `03.60.9765.0`, SHA-256
`4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`)

| Campaign | Entries | Result |
| --- | --- | --- |
| A1 `DAO-A1-ALLOCATION-MAPS-001` | `EXP-0037`–`EXP-0039` | hosted lane proved; `no_scientific_outcome` (analyzer/acquisition contract mismatch, disclosed in `EXP-0040`) |
| A2 `DAO-A2-ALLOCATION-MAPS-001` | `EXP-0040`–`EXP-0043` | decisive record layer **downgraded** (`EXP-0043`: 1,935 plan-literal starts survive); closed |
| A3 `DAO-A3-ALLOCATION-MAPS-001` + R2–R5 | `EXP-0044`–`EXP-0051` | **independently validated**: `global_map.record` = page 1, interval `[1915,2048)`, `set_means_not_in_use`, predicts holdout. Conversion, extended base, TDEF pointer pair: `no_outcome`. No capability moved. |
| A4 `DAO-A4-ROW-ANCHORED-MAPS-001` | `EXP-0052` is TO BE MERGED by PR #72; resolve the full remote PR head at P0 execution | current review verdict is DO-NOT-MERGE; do not record a pass count or mutable review-file hash here. No A4 implementation or acquisition is authorized until the final zero-blocker review is committed byte-for-byte and the human merges PR #72. |

What A3 established that Rust may rely on (only via `EXP-0051`, and only as
"narrow independently validated experimental input", never as a layout
claim): one global allocation record on page 1 for the D checkpoints, LSB-first
bitmap polarity `set_means_not_in_use`. What A3 did **not** establish: where
any table's map rows are, how type-1 slots reference tag-`05` pages, the
extended page base, any TDEF/catalog/row/value fact. A4 exists to supply
exactly those. The approved scope brief is committed on the A4 branch at
`oracle/windows-dao/experiments/a4/design-inputs/a4-scope-approved.md`
(SHA-256 `ead09d9cec961d018ed4845f14d825d2ae8da2d3329f12d6ae9ea2233e4eeeb7`)
and amended by `a4-scope-amendment-001.md` (work ceiling 600,000,000 →
800,000,000, delegate-approved during review pass 6; SHA-256
`770215c2472d8dee823db6c8fc3af75fc44cfd0769802e7f9f486a25131f3b25`).

A4 H4 is limited to the catalog root plus the kind/id/name field-relationship
model; the plan's `hypotheses.deferred` lists "physical column/index
definition, row values, index nodes, relationships, Memo/OLE/long values,
writes, free-space preferences, and preservation" as outside A4. In
particular A4 does **not** establish the catalog-record field that references
a TDEF page; A5 acquires it (P5).

### 1.3 Differential program

Decision recorded in the committed copy
`docs/plans/design-inputs/sol-diff-proposal.md` (SHA-256
`a29528643e6ebe093b05334ca3d1030d97243ca03fdc5fb216c5a3502aaf28e6`):
**Option 3** (end-to-end semantic traversal; DAO and Rust emit the same
canonical schema/rows/values JSON) is the advancement path for every
`dao_differential` capability; **Option 1** (checkpoint consequence
differential) is a companion stress lane for `format.pages_allocation_usage`.
Before P8 begins, P8T records that committed copy and its hash in an additive
provenance entry; until then the decision is a design input and not a ledger
fact (Section 6.4 item 6). Nothing of either option exists yet: protocol 1.0
still rejects `rust_read_dao`, `dao_open_rust`, `dao_verify_rust_update`
(`oracle/windows-dao/scripts/validate_protocol.py`), and
`dao_differential_v1` is `disabled` in `evidence-policy.json` and
intrinsically `unavailable` in the adapter catalog.

## 2. Dependency-ordered phases

Every phase has the same skeleton: goal, binding inputs, deliverables as
numbered PR steps, acceptance checks, reviewer checks, sol failure modes,
go/no-go. Phases do not overlap except as stated in the last paragraph of
Section 7. "PR" means one pull request against `main` from one worktree;
each PR gets its own reviewer session.

Phase order (each arrow is a hard dependency on the merged result of the
predecessor):

```
P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7 → P7H → P7I → P8T → P8 → P9 → P10 → P11
```

Common acceptance commands (run on every PR before requesting review):

```sh
just ready
python3 -B -m pytest oracle/windows-dao/tests tools/tests fuzz/tests -q
python3 tools/validate_repository_contract.py
python3 tools/reconcile_tests.py
git diff --check "$(git merge-base origin/main HEAD)" HEAD
```

Those commands are necessary but not sufficient. Every PR step has its own
command block. A command may name a file created by that same step, but it may
not name a later step's file. No acceptance, reviewer, failure-mode, or
go/no-go subsection incorporates another phase by reference or textual
substitution. "As Pn", "P1–P3 pattern", an ellipsis, and prose such as
"re-run validation" are not acceptance commands.
Campaign phases A5–A9 are each split into five independently reviewed steps:
preregistration; analyzer/generator/independent-validator implementation;
executed dry-run disclosure; hosted acquisition/result-only provenance; and
dependent Rust implementation. A step may begin only after its predecessor's
go/no-go is recorded. Where a command below names a script or CLI subcommand
that does not exist on `main` yet, that script is a deliverable of the same
step and the command is its required interface; if the author chooses a
different interface, this plan is amended with the literal replacement
before the step's PR is merged. The executor never invents an interface.

Common sol guardrails (apply to every phase; Section 6.3 has the full list):
additive provenance only; never edit a hash-pinned plan; derive every number
from the cited bytes or plan and show the derivation in the PR body; never
emit a boolean that is not computed from data; exact-ceiling accept and
one-over reject tests for every bound.

### P0 — Close the A4 preregistration (PR #72)

- Goal: a merged, immutable A4 base plan that survives one more adversarial
  pass with zero blocking findings.
- Binding inputs: the committed scope brief
  `oracle/windows-dao/experiments/a4/design-inputs/a4-scope-approved.md`
  (SHA-256 `ead09d9c…` full value above) and
  `oracle/windows-dao/experiments/a4/design-inputs/a4-scope-amendment-001.md`
  (SHA-256
  `770215c2472d8dee823db6c8fc3af75fc44cfd0769802e7f9f486a25131f3b25`;
  800,000,000 work-unit ceiling is approved scope; any other figure is
  not); a TO-BE-CREATED committed copy of the final zero-blocker review at
  `oracle/windows-dao/experiments/a4/design-inputs/sol-a4-review-final.md`
  (the future review pass that ends with zero blocking findings, copied
  byte-for-byte from the reviewer's file; no intermediate review hash is a
  binding input);
  `EXP-0043`–`EXP-0051` for the A3 calibration bytes. Before the P0 merge,
  record all three full SHA-256 values and the exact full PR head OID in
  the PR body and the additive `EXP-0052` entry.
- Deliverables (all on branch `codex/a4-plan`, additive commits, no
  squash), one PR step:
  1. `docs(a4): close preregistration review` — apply any remaining
     blocking replacement text from the latest review pass verbatim;
     commit `sol-a4-review-final.md`; add the recomputation script
     `oracle/windows-dao/experiments/a4/design-inputs/recompute_a4_work_terms.py`
     that sums every work term from the plan JSON and asserts the printed
     total; re-pin README and `EXP-0052` text to the final plan SHA-256.
- Acceptance checks: common commands, then:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a4_plan_contract.py -q
python3 -B oracle/windows-dao/experiments/a4/design-inputs/recompute_a4_work_terms.py --plan oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json --assert-plan-total --expect-ceiling 800000000 --reject-ceiling 800000001
```

- Reviewer must adversarially verify: (a) sum every work term independently
  from the plan, not from the README, over both derivation replicas; (b)
  serialize one fixture for each of H1 TDEF-MULTIPLE, H2 pre-role terminal,
  H3 pre-base terminal, H4 CATALOG-RECORD-MULTIPLE and FIELD-MODEL-MULTIPLE
  against the schemas and show they validate *without* downstream fields;
  (c) construct two H4 candidates with equal `canonical_model_id` and
  distinct `canonical_candidate_id` and show replica agreement passes,
  then two with distinct model ids and show it fails; (d) check every
  `fixture_status` literal equals
  `claimed_reachable; execution_required_before_dispatch`; (e) confirm the
  committed scope-brief and amendment hashes match the plan's
  `preregistration` binding.
- Sol failure modes here: copying A3's interval bounds (B5/P3-B5 recurred
  twice) — every bound must cite the A4 grammar term it derives from;
  "fixing" a blocker by editing the reviewer's replacement text into prose
  without changing the schema; raising a cap without a committed, approved
  amendment.
- Go/no-go: human merges PR #72 only after a review pass reports no
  blocking findings. No A4 code may start before merge.

### P1 — A4 lane implementation and dry runs

- Goal: a runnable A4 lane (worker, analyzer, synthetic generator,
  independent validator, workflow) whose pre-dispatch byte-level
  reachability transcript is executed, hash-bound, and disclosed.
- Binding inputs: merged A4 plan and schemas (full PR #72 merge OID);
  `EXP-0049` (hosted lane rebinding rules); `EXP-0048` (dry-run disclosure
  shape); `EXP-0050` (R5-V01 binding, R5-L01 baselines, R5-T01 timeout);
  the A3 lane files listed in Section 3.3; the retained `EXP-0051` bundle,
  manifest SHA-256
  `f1a644abae1585d8ed0531f45a0544d3264d2449f6d5973ef2ef0bb3d5fefaab`.
- Deliverables, as four PR steps in this order, each with its own reviewer:
  1. `feat(a4): analyzer and synthetic generator` —
     `oracle/windows-dao/scripts/a4_analysis*.py`,
     `oracle/windows-dao/scripts/a4_generator*.py`, `a4_spec.py`,
     `a4_layers.py`, `a4_model.py`, tests
     `oracle/windows-dao/tests/test_a4_*.py`. The generator parses the
     schedule and grammars from the plan JSON; no hand-typed counts.
  2. `feat(a4): independent validator` —
     `oracle/windows-dao/scripts/a4_independent_*.py`, written from plan
     and schemas only; the module must not import any
     `a4_analysis*`/`a4_model` symbol (add a test that asserts this by
     inspecting imports). Tamper suite T1–T5 equivalents from the plan.
  3. `feat(a4): hosted lane` —
     `oracle/windows-dao/scripts/run-a4-replica.ps1`,
     `oracle/windows-dao/scripts/a4/A4.*.ps1`,
     `oracle/windows-dao/scripts/a4_bundle*.py`,
     `oracle/windows-dao/scripts/a4_holdout.py`,
     `.github/workflows/windows-dao-a4.yml`,
     `oracle/windows-dao/tests/test_windows_dao_a4_workflow.py`. Follow
     the rebind recipe in Section 3.3 exactly.
  4. `docs(a4): dry-run disclosure` — executed
     `oracle/windows-dao/experiments/a4/dry-run/` artifacts
     (`a3-calibration-report.json`, `a4-synthetic-report.json`,
     `a4-reachability-transcript.json`, `checksums.sha256`) plus an
     additive provenance entry (next free `EXP-` id; Section 6.3 rule
     "ids") recording commands, commits, full hashes, the measured
     predicate count equal to the plan's registered predicate count
     (recomputed, not typed), and `acquisition_authorized = false`.
- Acceptance checks: common commands on every step. Step 1 additionally:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a4_analyzer.py oracle/windows-dao/tests/test_a4_generator.py -q
```

  Step 2 additionally:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a4_independent_validator.py -q
```

  Step 3 additionally:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_windows_dao_a4_workflow.py oracle/windows-dao/tests/test_a4_powershell_contract.py -q
```

  Step 4 (dry-run CLI contract; `a4_dryrun.py` with both subcommands is a
  step-4 deliverable; `A3_RETAINED_BUNDLE` is a local read-only copy whose
  manifest hash equals the value above):

```sh
test -d "$A3_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a4_dryrun.py generate --retained-root "$A3_RETAINED_BUNDLE" --output oracle/windows-dao/experiments/a4/dry-run
python3 -B oracle/windows-dao/scripts/a4_dryrun.py verify --retained-root "$A3_RETAINED_BUNDLE" --artifacts oracle/windows-dao/experiments/a4/dry-run
```

  `verify` must reproduce byte-identical artifacts in a second clean
  checkout; the A3 calibration replay must reproduce the H1 page-23 counts
  recorded in the final A4 review (1,872 preserved windows, 1,745,696
  canonical pairs, row-then-page target-valid at 25/25 checkpoints,
  page-then-row at 7/25).
- Reviewer must adversarially verify: re-run `a4_dryrun.py verify` in a
  clean clone and compare hashes; open the transcript and confirm each
  registered predicate entry has a baseline fixture hash, a mutation hash,
  a measured count, an analyzer result and a validator result that *differ
  in source* (analyzer process vs validator process logs); pick three
  predicates at random, mutate the fixture bytes by hand, and confirm the
  predicate flips.
- Sol failure modes: **fabricated reachability** — sol has twice reported
  dry-run reachability that was label playback. Guardrail: the transcript
  is produced only by `a4_dryrun.py`, which must refuse to run if any
  fixture carries a pre-set `accepted`/`valid`/`reachable`/`passed` field;
  the reviewer greps fixtures for those keys. Second failure mode: sharing
  an analyzer pass with the validator (P3-B5 item 3) — the validator must
  read the bundle bytes itself.
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
- Deliverables, one PR step after the human dispatch: workflow dispatch of
  `windows-dao-a4.yml` with `execute_a4_campaign=true` on the exact clean
  pushed commit; local read-only copy of the bundle under the scratchpad;
  one PR `docs: record A4 first hosted result (EXP-nnnn)` that adds the
  result entry and nothing else — no code, no matrix change, whatever the
  outcome of H1–H4.
- Acceptance checks: common commands, then (these CLI forms are required
  deliverables of P1 step 3; `A4_RETAINED_BUNDLE` is the local read-only
  copy):

```sh
test -d "$A4_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a4_bundle.py validate "$A4_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a4_independent_validator.py --bundle-root "$A4_RETAINED_BUNDLE" --recompute-only
python3 -B oracle/windows-dao/scripts/a4_bundle.py verify-timing "$A4_RETAINED_BUNDLE" --accept 2700 --reject 2701
```

  The local re-run must reproduce the manifest and report hashes
  byte-for-byte.
- Reviewer must adversarially verify: the bundle's `plan/` inventory hashes
  equal the merged plan and revision hashes; `holdout-structure-receipt`
  says `page_bytes_exposed_to_analyzer = false`; the entry's layer results
  are copied from the report JSON, not from the workflow summary; the
  `claims` block is all false except
  `descriptive_provider_observation_only`.
- Sol failure modes: treating an infrastructure failure as a scientific
  outcome or vice versa (classification rule: Section 6.4 item 4);
  re-dispatching after editing anything other than workflow glue (any
  analyzer/plan/schema edit after dispatch requires a new experiment id).
- Go/no-go: human reads the result entry. Outcomes: H1–H3 decisive → P3
  may start. H4 decisive → P4 may start after P3. Any layer `no_outcome` →
  that layer's facts go to the next experiment (Section 3.1) and the
  dependent Rust stage stays blocked.

### P3 — Rust Stage 2 completion (allocation owned-page traversal)

- Goal: remove the three `Unsupported` steps in `allocation_traverse.rs`
  (map location, raw-reference following, extended page base) using only
  A4-established facts; `format.pages_allocation_usage` becomes
  `implemented`, verification stays `internal_only`.
- Binding inputs: A4 result entry (H1–H3); `docs/PROVENANCE.md` entry
  "Secondary documentation of Jet page, row-slot, and usage-map primitives";
  the resource contract in `SEMANTIC_READER.md`.
- Deliverables, one PR step `feat(jet3): stage 2 owned-page traversal`:
  `crates/jet3/src/map_location.rs` (TDEF page → two row locators, cites
  the A4 result id on every format-derived constant),
  `crates/jet3/src/usage_map.rs` (row-anchored type-0/type-1 record view
  with checkpoint-independent row bounds from the row directory), extension
  of `allocation_traverse.rs` to follow type-1 slots to tag-`05` pages with
  base `slot_ordinal * 16352 + bit_index` *only if* H3 was decisive;
  `DatabaseReader::owned_pages(table_root)` bounded iterator; tests per
  invariant (exact capacity edge 1,024 bits inline; one-over; zero slot;
  cycle; self-reference; reference beyond captured length); fuzz target
  `usage_map_traverse` registered in `fuzz/targets.json` with manifested
  seeds; `tests/manifest.json` entries;
  `docs/validation/repository-contract.json` assertion-file hashes and
  provenance ids updated; matrix diff exactly
  `format.pages_allocation_usage.implementation: partial → implemented`.
- Acceptance checks: common commands, then:

```sh
cargo fuzz run usage_map_traverse -- -max_total_time=60
./scripts/check-source-size.sh
```

- Reviewer must adversarially verify: every format-derived numeric constant
  in the new modules greps to a provenance id whose entry actually states
  that value; the inline-boundary tests are at 1,024 exactly and 1,025; no
  path reads a page that was not charged to the budget (count `page_visit`
  charges in a test against a 5-page synthetic file).
- Sol failure modes: promoting an A4 calibration number (e.g. page 23,
  offset 1915) into a constant — calibration values are test fixtures, not
  format constants; decoding booleans from a fixture JSON instead of from
  the page bytes.
- Go/no-go: reviewer sign-off plus human confirms the matrix diff is only
  the one `implementation` field named above.

### P4 — Rust Stage 3 minimal catalog bootstrap

- Goal: stream allocation-admitted user object records containing only the
  A4-established raw name bytes plus declared encoding class, object kind,
  and identifier. Do not expose or infer a TDEF-page reference in P4; that
  field is acquired by A5 and consumed in P5. Matrix:
  `schema.catalog_and_table_definitions.implementation: not_started →
  partial` and `verification: unverified → internal_only`; add the exact
  source and manifested test evidence required by `EVIDENCE.md`.
- Binding inputs: A4 H4 result; P3 API.
- Deliverables, one PR step `feat(jet3): stage 3 catalog bootstrap`:
  `crates/jet3/src/catalog.rs` and, before either file would exceed 800
  physical lines, `crates/jet3/src/catalog_record.rs`; `CatalogError`
  nesting the Stage 2 error; fuzz target `catalog_parsing` (named by G5)
  registered with manifested seeds; tests per invariant (record count
  ceiling accept/one-over; zero-length name; name length beyond record;
  unknown kind literal rejected; duplicate identifier); manifest entries;
  `jet3-cli list-objects` using only the public boundary;
  `docs/validation/repository-contract.json` re-pinned.
- Acceptance checks: common commands, then:

```sh
cargo fuzz run catalog_parsing -- -max_total_time=60
./scripts/check-source-size.sh
```

- Reviewer must adversarially verify: the P3 reviewer checks (constants
  cite provenance; budget charges counted) on the new modules; names are
  retained as raw bytes plus the A4 equivalence class; no UTF-8 conversion
  is claimed lossless unless H4 discriminated it; no field of the public
  record type refers to a page number or TDEF.
- Sol failure modes: inventing a catalog-record offset for the TDEF reference
  because a later stage "will need it" (Section 6.4 item 6); failing to charge
  a decoded record against the P3 budget; adding an uncited format constant.
- Go/no-go: reviewer sign-off plus human confirms the matrix diff contains
  only the implementation, verification, source, and manifested-test evidence
  named above.

### P5 — A5 table-definition experiment → Rust Stage 4

- Goal: acquire physical provenance for the catalog-object-to-TDEF
  reference, the TDEF record layout, column definitions (type code, size,
  flags, fixed/variable class, ordinal), and index definitions (fields,
  direction, unique/primary/required flags, root page); then implement
  `table_definition.rs` and `index_definition.rs` from those facts.
- Binding inputs: Section 3.1 A5 design; A4 result entry; P4 API;
  `EXP-0049`/`EXP-0050` lane rules; the merged A4 lane as the rebind
  source (Section 3.3).
- Type inventory rule (binds A5, A7, and the G3 scenario count): the
  preregistered A5 plan contains the closed list of exact DAO
  `DataTypeEnum` literals and numeric values in scope. It separately lists
  field attributes. If "Replication ID" is represented by a GUID type plus
  an attribute, it is one type case with two attribute cases, not a second
  type. The A5 work-term count, A7 value inventory, protocol schema, and
  G3 scenario count must all derive from that same checked list; no prose
  count in this plan is authoritative.
- Deliverables, five PR steps, each with its own reviewer and recorded
  go/no-go before the next starts:
  1. `docs(a5): preregistration` — `oracle/windows-dao/experiments/a5/`
     plan JSON, schema family (copied from A4 and re-frozen with a
     before/after hash table), README, `test_a5_plan_contract.py`,
     additive `EXP-` entry; Section 3.2 checklist complete.
  2. `feat(a5): analyzer, generator, independent validator` —
     `a5_analysis*.py`, `a5_generator*.py`, `a5_spec.py`, and
     `a5_independent_validator.py`, with focused tests and an import-isolation
     test proving the validator imports no producer module.
  3. `feat(a5): hosted lane and dry-run disclosure` — `a5_worker.ps1`,
     `a5_bundle.py`, `a5_dryrun.py`, the workflow contract tests, generated
     workflow, deterministic dry-run artifacts, and an additive disclosure
     entry with `acquisition_authorized = false`.
  4. `docs: record A5 first hosted result (EXP-nnnn)` — validate the retained
     hosted bundle with both validators and change only `docs/PROVENANCE.md`.
  5. `feat(jet3): stage 4 table definitions` —
     `crates/jet3/src/table_definition.rs`,
     `crates/jet3/src/index_definition.rs`, fuzz target
     `table_definition_parsing`, tests, manifest entries, contract re-pin.
     Matrix: `schema.catalog_and_table_definitions.implementation: partial →
     implemented`; both index-definition capabilities change
     `implementation: not_started → partial` and `verification: unverified →
     internal_only`, with exact source and manifested-test evidence. No tree
     traversal is implemented in this step.
- Acceptance checks: common commands on every step, plus the block for that
  step only.

  Step 1:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a5_plan_contract.py -q
python3 -B oracle/windows-dao/experiments/a5/design-inputs/recompute_a5_work_terms.py --plan oracle/windows-dao/experiments/a5/a5-table-definitions.plan.json --assert-plan-total --assert-scope-ceiling --reject-one-over
```

  Step 2:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a5_analysis.py oracle/windows-dao/tests/test_a5_generator.py oracle/windows-dao/tests/test_a5_independent_validator.py oracle/windows-dao/tests/test_a5_import_isolation.py -q
```

  Step 3:

```sh
test -d "$A4_RETAINED_BUNDLE"
python3 -B -m pytest oracle/windows-dao/tests/test_a5_workflow.py oracle/windows-dao/tests/test_a5_dryrun.py -q
python3 -B oracle/windows-dao/scripts/a5_dryrun.py generate --retained-root "$A4_RETAINED_BUNDLE" --output oracle/windows-dao/experiments/a5/dry-run
python3 -B oracle/windows-dao/scripts/a5_dryrun.py verify --retained-root "$A4_RETAINED_BUNDLE" --artifacts oracle/windows-dao/experiments/a5/dry-run
```

  Step 4:

```sh
test -d "$A5_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a5_bundle.py validate "$A5_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a5_independent_validator.py --bundle-root "$A5_RETAINED_BUNDLE" --recompute-only
python3 -B oracle/windows-dao/scripts/a5_bundle.py verify-timing "$A5_RETAINED_BUNDLE" --accept 2700 --reject 2701
```

  Step 5:

```sh
cargo fuzz run table_definition_parsing -- -max_total_time=60
./scripts/check-source-size.sh
```

- Reviewer checks: step 1 independently recomputes every work term and
  serializes one early-terminal fixture per schema; step 2 proves producer and
  validator disagree on a deliberately altered candidate and verifies import
  isolation; step 3 reproduces the artifact inventory and hashes in a second
  clean checkout; step 4 recomputes the retained manifest, report, timing, and
  predicate tally without editing code or the matrix; step 5 traces every
  column type code to the A5 result and proves index roots are typed but never
  followed.
- Sol failure modes: widening the preregistration after acquisition starts;
  using analyzer output as validator truth; dispatching before the disclosed
  dry run; editing anything but provenance in step 4; treating index
  definitions as traversal support.
- Go/no-go: human approval follows each step's independent review. Step 3
  requires the immutable step-1 plan and step-2 tools; hosted dispatch follows
  the merged step-3 disclosure; step 5 requires the merged step-4 result.

### P6 — A6 row experiment → Rust Stage 5

- Goal: physical provenance for the row directory, deleted/lookup flags,
  null map, fixed/variable regions, variable-offset table,
  overflow/continuation pointers, and the page free-space field; then
  `row.rs` streaming iterator.
- Binding inputs: Section 3.1 A6 design; A5 result entry; P5 API.
- Deliverables, five independently reviewed PR steps: (1)
  `docs(a6): preregistration` with the A6 plan, schemas, README, contract test,
  recomputation script, and additive provenance entry; (2)
  `feat(a6): analyzer, generator, independent validator` with `a6_*` modules,
  focused tests, and import isolation; (3) `feat(a6): hosted lane and dry-run
  disclosure` with worker, bundle, dry-run, workflow tests, deterministic
  artifacts, and `acquisition_authorized = false`; (4) `docs: record A6 first
  hosted result (EXP-nnnn)`, provenance only after both validators pass; (5)
  `feat(jet3): stage 5 rows` — `crates/jet3/src/row.rs`,
  `crates/jet3/src/row_directory.rs`, fuzz target `row_parsing`, tests
  (page-edge −1/0/+1 for every variable-sized structure; deleted and
  lookup rows excluded/included as the result entry states; continuation
  cycle and self-reference rejected), manifest entries, contract re-pin.
  Matrix: `rows.streaming_read → implemented`/`internal_only`;
  `values.null_fixed_variable → partial`.
- Acceptance checks: common commands on every step, plus:

  Step 1:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a6_plan_contract.py -q
python3 -B oracle/windows-dao/experiments/a6/design-inputs/recompute_a6_work_terms.py --plan oracle/windows-dao/experiments/a6/a6-rows.plan.json --assert-plan-total --assert-scope-ceiling --reject-one-over
```

  Step 2:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a6_analysis.py oracle/windows-dao/tests/test_a6_generator.py oracle/windows-dao/tests/test_a6_independent_validator.py oracle/windows-dao/tests/test_a6_import_isolation.py -q
```

  Step 3:

```sh
test -d "$A5_RETAINED_BUNDLE"
python3 -B -m pytest oracle/windows-dao/tests/test_a6_workflow.py oracle/windows-dao/tests/test_a6_dryrun.py -q
python3 -B oracle/windows-dao/scripts/a6_dryrun.py generate --retained-root "$A5_RETAINED_BUNDLE" --output oracle/windows-dao/experiments/a6/dry-run
python3 -B oracle/windows-dao/scripts/a6_dryrun.py verify --retained-root "$A5_RETAINED_BUNDLE" --artifacts oracle/windows-dao/experiments/a6/dry-run
```

  Step 4:

```sh
test -d "$A6_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a6_bundle.py validate "$A6_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a6_independent_validator.py --bundle-root "$A6_RETAINED_BUNDLE" --recompute-only
python3 -B oracle/windows-dao/scripts/a6_bundle.py verify-timing "$A6_RETAINED_BUNDLE" --accept 2700 --reject 2701
```

  Step 5:

```sh
cargo fuzz run row_parsing -- -max_total_time=60
./scripts/check-source-size.sh
```

- Reviewer checks: step 1 independently recomputes the grammar and early
  terminals; step 2 falsifies producer/validator agreement and proves import
  isolation; step 3 reproduces dry-run bytes in a second checkout; step 4
  recomputes manifest, result, timing, and predicate tallies; step 5 proves the
  iterator holds at most one row page at a time and charges every continuation
  hop.
- Sol failure modes: acquiring outside the immutable plan; trusting producer
  booleans; retrying a scientific hosted failure; editing code in the result
  PR; buffering the full row chain or omitting cycle charges.
- Go/no-go: human approval after each independent review; step 3 requires
  steps 1–2 merged, hosted dispatch requires step 3 merged, and step 5 requires
  the step-4 result merged.

### P7 — A7 value experiment → Rust Stage 6

- Goal: physical provenance for every type in the A5 closed type list at
  null/min/representative/max (G3 bullet 4), Text code pages, and Memo/OLE
  long-value pointers (inline vs single-page vs multi-page chains and
  termination); then `value.rs`, `long_value.rs`, `text.rs`.
- Binding inputs: Section 3.1 A7 design; A5 closed type list; A6 result
  entry; P6 API.
- Deliverables, five independently reviewed PR steps: (1)
  `docs(a7): preregistration` with plan, schemas, README, contract test,
  recomputation script, closed type list, and additive provenance; (2)
  `feat(a7): analyzer, generator, independent validator` with `a7_*` modules,
  focused tests, and import isolation; (3) `feat(a7): hosted lane and dry-run
  disclosure` with worker, bundle, dry-run, workflow tests, artifacts, and
  `acquisition_authorized = false`; (4) `docs: record A7 first hosted result
  (EXP-nnnn)`, provenance only; (5) `feat(jet3): stage 6 values` —
  `crates/jet3/src/value.rs`,
  `crates/jet3/src/long_value.rs`, `crates/jet3/src/text.rs` (raw bytes
  retained always), fuzz target `long_values`, tests, manifest entries,
  contract re-pin. Matrix: `values.all_dao_jet3_table_types`,
  `values.null_fixed_variable`, `values.code_pages_lossless_raw`,
  `values.date_currency_binary_guid_replication`,
  `values.memo_ole_multi_page → implemented`/`internal_only`.
- Acceptance checks: common commands on every step, plus:

  Step 1:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a7_plan_contract.py -q
python3 -B oracle/windows-dao/experiments/a7/design-inputs/recompute_a7_work_terms.py --plan oracle/windows-dao/experiments/a7/a7-values.plan.json --assert-plan-total --assert-scope-ceiling --reject-one-over
```

  Step 2:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a7_analysis.py oracle/windows-dao/tests/test_a7_generator.py oracle/windows-dao/tests/test_a7_independent_validator.py oracle/windows-dao/tests/test_a7_import_isolation.py -q
```

  Step 3:

```sh
test -d "$A6_RETAINED_BUNDLE"
python3 -B -m pytest oracle/windows-dao/tests/test_a7_workflow.py oracle/windows-dao/tests/test_a7_dryrun.py -q
python3 -B oracle/windows-dao/scripts/a7_dryrun.py generate --retained-root "$A6_RETAINED_BUNDLE" --output oracle/windows-dao/experiments/a7/dry-run
python3 -B oracle/windows-dao/scripts/a7_dryrun.py verify --retained-root "$A6_RETAINED_BUNDLE" --artifacts oracle/windows-dao/experiments/a7/dry-run
```

  Step 4:

```sh
test -d "$A7_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a7_bundle.py validate "$A7_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a7_independent_validator.py --bundle-root "$A7_RETAINED_BUNDLE" --recompute-only
python3 -B oracle/windows-dao/scripts/a7_bundle.py verify-timing "$A7_RETAINED_BUNDLE" --accept 2700 --reject 2701
```

  Step 5:

```sh
cargo fuzz run long_values -- -max_total_time=60
./scripts/check-source-size.sh
```

- Reviewer checks: step 1 derives the work count from the checked type list;
  step 2 proves validator independence with altered scalar and long-value
  candidates; step 3 reproduces dry-run bytes; step 4 independently validates
  every retained type/boundary and timing; step 5 confirms every converted
  value retains lossless raw hex and every long-value limit derives from the
  A7 result.
- Sol failure modes: letting a prose type count override the checked list;
  producer/validator shared logic; unrecorded hosted retry; result PR code
  edits; guessed code pages or long-value limits.
- Go/no-go: human approval after each independent review; step 3 requires
  steps 1–2 merged, hosted dispatch requires step 3 merged, and step 5 requires
  the step-4 result merged.

### P7H — Jet 3 opening discriminator and unencrypted-state prerequisite

- Status: TO BE CREATED after a human-approved scope brief committed under
  `oracle/windows-dao/experiments/a-opening/design-inputs/`. P8 is blocked
  until this phase is merged.
- Goal: acquire physical provenance for the Jet 3 version discriminator
  and the fail-closed unencrypted-state check named by
  `docs/architecture/SEMANTIC_READER.md` ("Exact blockers", first
  bullet), then complete `database.open` and `format.header_and_version`
  without accepting Jet 4, ACCDB, passworded, or encrypted input.
- Binding inputs: the approved scope brief; the `docs/PROVENANCE.md` entries
  "Microsoft Jet file signatures", "Jet database page size", and "Jet 3
  database-header commit region"; the merged A7 campaign's worker/bundle interfaces. DAO
  operations: `CreateDatabase` with
  `dbVersion30` versus `dbVersion40`, with and without `dbEncrypt`, with
  and without a database password, at fresh and reopened checkpoints; the
  observer decodes only the preregistered header predicates.
- Deliverables, five independently reviewed PR steps: (1)
  `docs(a-opening): preregistration` with plan, schemas, README, contract
  test, recomputation script, scope brief, and additive provenance; (2)
  `feat(a-opening): analyzer, generator, independent validator` with
  `a_opening_*` modules, tests, and import isolation; (3)
  `feat(a-opening): hosted lane and dry-run disclosure` with worker, bundle,
  dry-run, workflow tests, deterministic artifacts, and
  `acquisition_authorized = false`; (4) `docs: record A-opening first hosted
  result (EXP-nnnn)`, provenance only; (5) `feat(jet3): opening
  discriminator` — changes confined to the Stage 0 modules listed in Section
  1.1, manifested
  corruption/boundary tests (one-byte discriminator flips; encrypted and
  passworded fixtures from the result bundle rejected with structured
  errors), and a registered `database_opening` fuzz corpus update. Every
  future path and command is fixed in the approved scope brief before
  implementation begins.
- Acceptance checks: common commands on every step, plus:

  Step 1:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a_opening_plan_contract.py -q
python3 -B oracle/windows-dao/experiments/a-opening/design-inputs/recompute_a_opening_work_terms.py --plan oracle/windows-dao/experiments/a-opening/a-opening.plan.json --assert-plan-total --assert-scope-ceiling --reject-one-over
```

  Step 2:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a_opening_analysis.py oracle/windows-dao/tests/test_a_opening_generator.py oracle/windows-dao/tests/test_a_opening_independent_validator.py oracle/windows-dao/tests/test_a_opening_import_isolation.py -q
```

  Step 3:

```sh
test -d "$A7_RETAINED_BUNDLE"
python3 -B -m pytest oracle/windows-dao/tests/test_a_opening_workflow.py oracle/windows-dao/tests/test_a_opening_dryrun.py -q
python3 -B oracle/windows-dao/scripts/a_opening_dryrun.py generate --retained-root "$A7_RETAINED_BUNDLE" --output oracle/windows-dao/experiments/a-opening/dry-run
python3 -B oracle/windows-dao/scripts/a_opening_dryrun.py verify --retained-root "$A7_RETAINED_BUNDLE" --artifacts oracle/windows-dao/experiments/a-opening/dry-run
```

  Step 4:

```sh
test -d "$A_OPENING_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a_opening_bundle.py validate "$A_OPENING_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a_opening_independent_validator.py --bundle-root "$A_OPENING_RETAINED_BUNDLE" --recompute-only
python3 -B oracle/windows-dao/scripts/a_opening_bundle.py verify-timing "$A_OPENING_RETAINED_BUNDLE" --accept 2700 --reject 2701
```

  Step 5:

```sh
cargo fuzz run database_opening -- -max_total_time=60
./scripts/check-source-size.sh
```

- Reviewer checks: step 1 independently recomputes every plan term and
  serializes each early rejection; step 2 proves import isolation and
  producer/validator disagreement on a flipped discriminator; step 3
  reproduces the artifact hashes; step 4 recomputes the retained manifest,
  result, and timing; step 5 feeds the result bundle's Jet 4, encrypted, and
  passworded files to `jet3-cli` and shows structured rejection rather than a
  panic or generic `Unsupported` fallthrough.
- Sol failure modes: deriving the discriminator from another MDB
  implementation's documentation (prohibited source); accepting a file
  because the byte-zero tags classify.
- Matrix effect after the step-5 PR: `database.open` and
  `format.header_and_version` become `implemented`/`internal_only`. Their
  verification does not advance here.
- Go/no-go: human approval after each independent review; step 3 requires
  steps 1–2 merged, hosted dispatch requires step 3 merged, and step 5 requires
  the step-4 result merged.

### P7I — A8 index/relationship experiment and Rust read traversal

- Goal: merge the A8 result, implement bounded `index_tree.rs` traversal
  and relationship metadata reading, and make both index read capabilities
  and the relationship read path `implemented`/`internal_only` before any
  differential read scenario exists.
- Binding inputs: A7 result entry; the P5 index-definition API; the P6/P7
  row/value APIs; Section 3.1 A8 design.
- Deliverables, five independently reviewed PR steps: (1)
  `docs(a8): preregistration` with plan, schemas, README, contract test,
  recomputation script, and provenance; (2) `feat(a8): analyzer, generator,
  independent validator` with `a8_*` modules, tests, and import isolation; (3)
  `feat(a8): hosted lane and dry-run disclosure` with worker, bundle, dry-run,
  workflow tests, artifacts, and `acquisition_authorized = false`; (4)
  `docs: record A8 first hosted result (EXP-nnnn)`, provenance only; (5)
  `feat(jet3): index traversal and relationships` —
  `crates/jet3/src/index_tree.rs`, `crates/jet3/src/relationships.rs`,
  fuzz target `index_traversal`, tests (leaf/branch link cycle and
  self-reference rejected; depth ceiling accept/one-over; every key type
  from the A5 list), manifest entries, contract re-pin. Matrix:
  `indexes.primary_unique_non_unique`,
  `indexes.composite_ascending_descending.implementation: partial →
  implemented`; relationships changes `implementation: not_started →
  partial` and `verification: unverified → internal_only`, with source and
  manifested-test evidence. The relationships write path remains absent.
- Acceptance checks: common commands on every step, plus:

  Step 1:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a8_plan_contract.py -q
python3 -B oracle/windows-dao/experiments/a8/design-inputs/recompute_a8_work_terms.py --plan oracle/windows-dao/experiments/a8/a8-index-relationships.plan.json --assert-plan-total --assert-scope-ceiling --reject-one-over
```

  Step 2:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a8_analysis.py oracle/windows-dao/tests/test_a8_generator.py oracle/windows-dao/tests/test_a8_independent_validator.py oracle/windows-dao/tests/test_a8_import_isolation.py -q
```

  Step 3:

```sh
test -d "$A_OPENING_RETAINED_BUNDLE"
python3 -B -m pytest oracle/windows-dao/tests/test_a8_workflow.py oracle/windows-dao/tests/test_a8_dryrun.py -q
python3 -B oracle/windows-dao/scripts/a8_dryrun.py generate --retained-root "$A_OPENING_RETAINED_BUNDLE" --output oracle/windows-dao/experiments/a8/dry-run
python3 -B oracle/windows-dao/scripts/a8_dryrun.py verify --retained-root "$A_OPENING_RETAINED_BUNDLE" --artifacts oracle/windows-dao/experiments/a8/dry-run
```

  Step 4:

```sh
test -d "$A8_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a8_bundle.py validate "$A8_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a8_independent_validator.py --bundle-root "$A8_RETAINED_BUNDLE" --recompute-only
python3 -B oracle/windows-dao/scripts/a8_bundle.py verify-timing "$A8_RETAINED_BUNDLE" --accept 2700 --reject 2701
```

  Step 5:

```sh
cargo fuzz run index_traversal -- -max_total_time=60
./scripts/check-source-size.sh
```

- Reviewer checks: step 1 independently recomputes grammar and terminal
  reachability; step 2 proves validator independence on altered branch/leaf
  links; step 3 reproduces dry-run hashes; step 4 recomputes retained results;
  step 5 tests descending composite traversal against the A8 ordered inserts,
  not an assumed sort, and proves cycle/depth bounds.
- Sol failure modes: widening the plan after freeze; shared producer/validator
  logic; undisclosed hosted retry; code in the result PR; implementing
  relationship writes or inventing key ordering outside A8 evidence.
- Go/no-go: human approval after the A8 result entry and Rust review. P8T
  and P8 stay blocked until this phase is merged.

### P8T — Exact-commit evidence tooling amendment

P8 is BLOCKED on a human-approved amendment to the binding validation
contract, because the checked tooling (Section 1.1) cannot accept a
`dao_bundle` that names the commit containing its own reference. This phase
changes only tooling and contract text; it advances no verification state.

- Goal: define and test a non-self-referential relationship among (1) the
  clean release commit; (2) the detached `dao_bundle` overlay generated for
  that commit; (3) `dao_differential_v1`'s intrinsic semantic validation
  result; and (4) the effective verification state reported for each
  support-matrix capability.
- Binding inputs: `EVIDENCE.md` "Detached release-evidence overlays";
  `DAO_PROVIDER_BLOCKER.md` "Requirements for future release evidence";
  `tools/validation/evidence.py`; `tools/validation/release_evidence_adapters.py`;
  `docs/plans/design-inputs/sol-diff-proposal.md` (hash in Section 1.3).
- Deliverables, two PR steps:
  1. `docs(validation): exact-commit evidence amendment` — an amendment to
     `docs/validation/EVIDENCE.md` and `docs/validation/ACCEPTANCE.md` plus
     an additive provenance entry that also records the committed
     differential decision copy and its SHA-256. The amendment must state
     whether verification is stored in `support-matrix.json` or derived at
     acceptance time, how an external overlay is selected, how its manifest
     SHA-256 and exact commit are bound without embedding the future commit
     hash into that commit, and how `./scripts/acceptance.sh full` consumes
     it. This step also amends this implementation plan with every file,
     schema, command, and expected result selected by the approved design.
     Human approval of both amendments is the go/no-go for step 2.
  2. `feat(validation): dao_differential_v1 overlay path` — implement the
     approved mechanism in the exact scope merged by step 1.
     `tools/validation/evidence.py` and
     `tools/validation/release_evidence_adapters.py` are known minimum inputs,
     not a scope lock; the approved design may also name support, overlay,
     policy, acceptance-runner, gate-script, or support-matrix schema files.
     Replace the unconditional `dao_bundle` rejection and change the
     adapter's intrinsic availability only as the amendment specifies. Add
     focused tests for a complete overlay, a missing scenario, an altered
     snapshot byte, a commit mismatch, a dirty tree, and a missing required
     branch; `evidence-policy.json` stays `disabled` in this step. Step 2 does
     not begin until the amended exact scope is merged.
- Acceptance checks: common commands, then:

```sh
python3 -B -m pytest tools/tests -q
python3 tools/validate_repository_contract.py
./scripts/acceptance.sh full
```

  The expected `full` result for step 2 is still nonzero `BLOCKED`; it
  must newly reach and exercise the adapter path (log shows the adapter's
  own missing-overlay or disabled-policy reason) instead of failing on the
  present unavailable-adapter or unconditional-DAO-bundle blocker.
- Reviewer must adversarially verify: construct an overlay for the
  reviewer's own clean checkout commit and show the six test cases behave
  as named; confirm no code path reads verification state from any file
  other than the one the amendment names; confirm the policy is still
  `disabled`.
- Sol failure modes: relabeling an adapter's maximum level in policy (the
  level is intrinsic, `EVIDENCE.md`); storing a future commit hash in a
  committed file; making `full` print `PASS` for a gate that the amendment
  has not yet satisfied.
- Go/no-go: human approves the amendment text (step 1) and then the
  implementation (step 2). Amend this plan (Section 5.5) with the resulting
  exact commands before P8 resumes.

### P8 — Differential read program (Option 3, read legs)

- Goal: `DAO-READ-*` scenarios, the shared snapshot contract, the
  `dao_differential_v1` adapter enabled, and the first exact-commit
  `dao_bundle` overlay accepted by the P8T mechanism; the P8 read
  allowlist of capabilities moves to `dao_differential`.
- Binding inputs: Section 5; the merged P8T amendment; `EVIDENCE.md` bundle
  list; `ACCEPTANCE.md` G3; `oracle/windows-dao/protocol/v1_1`; the A5
  closed type list.
- Read advancement allowlist: written explicitly in the P8 step-4 PR after
  P7H and P7I merge. It may contain only capabilities that are
  `implemented` at that time from this set: `database.open`,
  `format.header_and_version`, `format.pages_allocation_usage`,
  `schema.catalog_and_table_definitions`, `values.all_dao_jet3_table_types`,
  `values.null_fixed_variable`, `values.code_pages_lossless_raw`,
  `values.date_currency_binary_guid_replication`,
  `values.memo_ole_multi_page`, `rows.streaming_read`,
  `indexes.primary_unique_non_unique`,
  `indexes.composite_ascending_descending`. No wildcard or phrase such as
  "every read capability" authorizes a matrix edit.
- Deliverables, four PR steps:
  1. `feat(protocol): v1_2 scenario inventory and snapshot schema` —
     `oracle/windows-dao/protocol/v1_2/scenarios.schema.json`,
     `scenarios.json`, `canonical-semantic-snapshot.schema.json`,
     `branch-registry.json`, `validate_protocol_v1_2.py` with `schemas`
     and `inventory` subcommands, `rust_read_dao` mode enabled in the v1_2
     validator only (Section 5.1, 5.2).
  2. `feat(jet3-testkit): canonical snapshot producer` — snapshot producer
     bound to the public `DatabaseReader` API only, `jet3-cli snapshot`,
     `coverage-receipt.json` emission, tests against the v1_2 schema.
  3. `feat(oracle): windows-dao-differential lane` — DAO-side snapshot
     producer (PowerShell, generated from `scenarios.json`),
     `.github/workflows/windows-dao-differential.yml`,
     `oracle/windows-dao/tests/test_windows_dao_differential_workflow.py`,
     Section 3.3 invariants; dry run over synthetic inputs disclosed in an
     additive entry.
  4. `docs(evidence): first exact-commit read bundle` — hosted run on the
     exact clean commit; overlay published per the P8T mechanism; policy
     flip to `enabled` with the adapter tests from P8T; the explicit read
     allowlist matrix transition; additive provenance entry.
- Acceptance checks: common commands on every step, plus the block for that
  step only.

  Step 1:

```sh
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py schemas
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory oracle/windows-dao/protocol/v1_2/scenarios.json
python3 -B -m pytest oracle/windows-dao/tests/test_protocol_validation.py -q
```

  Step 2:

```sh
cargo test --package jet3-testkit --locked
cargo test --package jet3-cli snapshot --locked
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py schemas
```

  Step 3:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_windows_dao_differential_workflow.py oracle/windows-dao/tests/test_protocol_validation.py -q
```

  Step 4:

```sh
python3 tools/validate_repository_contract.py
./scripts/acceptance.sh full
```

  For step 4 the adapter's read-allowlist subset report must PASS, while
  `full` is expected to keep G3 `BLOCKED` until the write/update legs exist
  (P10). The PR body retains the adapter/G3 logs and every remaining
  blocker; it may not relabel a partial G3 run as a gate PASS.
- Reviewer must adversarially verify: regenerate one DAO snapshot and one
  Rust snapshot for a scenario chosen at random and diff the canonical
  bytes; alter one byte of a retained Rust snapshot and show the adapter
  rejects the overlay; confirm every `capability_ids` value exists in the
  matrix and every `required_branches` value exists in the registry; confirm
  the matrix diff equals the allowlist exactly.
- Sol failure modes: a Rust self-read presented as agreement; scenarios
  marked `skipped` counted as covered; the allowlist widened to a
  capability whose scenarios are incomplete.
- Go/no-go: human, per step; step 4 requires the P8T mechanism merged and
  the exact clean candidate commit pushed.

### P9 — Writer (Rust) and independent structural verifier

- Goal: `database.create_empty`, `schema.create_drop_tables`,
  `rows.insert_update_delete`, `indexes.crud_maintenance`,
  `relationships.create_drop_preserve_metadata` (write path), and
  `output.deterministic_configuration` implemented; an independent
  structural verifier (`tools/validation/independent_writer.py`, written
  from provenance, not from the Rust reader) passes on all Rust output
  (G4); `independent_writer_v1` adapter made available and enabled with
  tests.
- Binding inputs: all A4–A9 result entries and the P8 read API. A8 (P7I)
  and A9 are mandatory predecessors, not conditional work. A9 contains
  DAO-only mutations (Section 3.1); Rust-produced files first enter an
  oracle in P10.
- Deliverables, six PR steps:
  1. `docs(a9): preregistration` — plan, schemas, README, contract test,
     recomputation script, scope brief, and additive provenance.
  2. `feat(a9): analyzer, generator, independent validator` — `a9_*`
     modules, focused tests, and import isolation.
  3. `feat(a9): hosted lane and dry-run disclosure` — worker, bundle,
     dry-run, workflow tests, deterministic artifacts, and an additive
     `acquisition_authorized = false` disclosure.
  4. `docs: record A9 first hosted result (EXP-nnnn)` — validate the retained
     bundle independently and change only `docs/PROVENANCE.md`.
  5. `feat(jet3): writer` — `crates/jet3/src/writer/*.rs`, each under 800
     lines; allocation decisions cite the A9 result entry; `atomic.rs`
     publication reused; G4 fault-injection tests per stage; fuzz target
     `allocator`; matrix `implementation` changes for the six capabilities
     above (verification unchanged).
  6. `feat(validation): independent writer verifier` —
     `tools/validation/independent_writer.py` with `--self-test`, adapter
     availability change for `independent_writer_v1`, policy `enabled` with
     accept/reject tests; exact-commit `independent_writer_report` overlay
     per the P8T mechanism.
- Acceptance checks: common commands on every step, plus:

  Step 1:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a9_plan_contract.py -q
python3 -B oracle/windows-dao/experiments/a9/design-inputs/recompute_a9_work_terms.py --plan oracle/windows-dao/experiments/a9/a9-writer-allocation.plan.json --assert-plan-total --assert-scope-ceiling --reject-one-over
```

  Step 2:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_a9_analysis.py oracle/windows-dao/tests/test_a9_generator.py oracle/windows-dao/tests/test_a9_independent_validator.py oracle/windows-dao/tests/test_a9_import_isolation.py -q
```

  Step 3:

```sh
test -d "$A8_RETAINED_BUNDLE"
python3 -B -m pytest oracle/windows-dao/tests/test_a9_workflow.py oracle/windows-dao/tests/test_a9_dryrun.py -q
python3 -B oracle/windows-dao/scripts/a9_dryrun.py generate --retained-root "$A8_RETAINED_BUNDLE" --output oracle/windows-dao/experiments/a9/dry-run
python3 -B oracle/windows-dao/scripts/a9_dryrun.py verify --retained-root "$A8_RETAINED_BUNDLE" --artifacts oracle/windows-dao/experiments/a9/dry-run
```

  Step 4:

```sh
test -d "$A9_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a9_bundle.py validate "$A9_RETAINED_BUNDLE"
python3 -B oracle/windows-dao/scripts/a9_independent_validator.py --bundle-root "$A9_RETAINED_BUNDLE" --recompute-only
python3 -B oracle/windows-dao/scripts/a9_bundle.py verify-timing "$A9_RETAINED_BUNDLE" --accept 2700 --reject 2701
```

  Step 5:

```sh
cargo test --package jet3 writer --locked
cargo fuzz run allocator -- -max_total_time=60
./scripts/check-source-size.sh
```

  Step 6:

```sh
python3 -B tools/validation/independent_writer.py --self-test
python3 -B -m pytest tools/tests -q
python3 tools/validate_repository_contract.py
./scripts/check-source-size.sh
```

- Reviewer must adversarially verify: the verifier imports nothing from
  `crates/`; before writer work, step 1 independently recomputes the A9 work
  table, step 2 proves validator independence, step 3 reproduces dry-run
  hashes, and step 4 recomputes retained results. For each step-5
  fault-injection stage the original file is byte-identical after failure; a
  Rust-written file with one flipped allocation bit fails the step-6 verifier.
- Sol failure modes: changing the A9 plan after freeze; letting Rust mutate an
  acquisition file; undisclosed hosted retry; code edits in the result PR;
  using the Rust reader as the only check of Rust output; copying free-space
  choices from a prohibited source instead of the A9 result.
- Go/no-go: human approval after every independent review. Step 3 requires
  steps 1–2 merged; hosted dispatch requires step 3 merged; writer step 5
  requires the step-4 A9 result; verifier step 6 requires the reviewed writer.

### P10 — Differential write/update legs (Option 3 legs 2 and 3)

- Goal: `DAO-WRITE-*` (DAO opens Rust files; canonical result equals
  declarative input) and `DAO-UPDATE-*` (Rust mutates DAO file; DAO reports
  intended change and preservation of unrelated schema, rows, indexes,
  relationships, long values, raw-preservation fields); the P10 write
  allowlist of capabilities moves to `dao_differential`; G3 PASS.
- Binding inputs: P8 protocol and lane; P9 writer; `ACCEPTANCE.md` G3.
- Write/update advancement allowlist: written explicitly in the step-3 PR;
  may contain only `implemented` capabilities from `database.create_empty`,
  `schema.create_drop_tables`, `rows.insert_update_delete`,
  `indexes.crud_maintenance`,
  `relationships.create_drop_preserve_metadata`, plus any read capability
  not advanced in P8.
- Deliverables, three PR steps:
  1. `feat(protocol): v1_2 write and update scenarios` — `DAO-WRITE-*` and
     `DAO-UPDATE-*` entries with `expected_snapshot_sha256` and
     `preserve_paths`; `dao_open_rust` and `dao_verify_rust_update` modes
     enabled in the v1_2 validator; `tools/verify_preservation_diff.py`
     with tests.
  2. `feat(oracle): differential write/update lane` — lane extension and
     dry run disclosed additively.
  3. `docs(evidence): exact-commit full differential bundle` — hosted run,
     overlay, allowlist transition, provenance entry.
- Acceptance checks: common commands on every step, plus:

  Step 1:

```sh
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory oracle/windows-dao/protocol/v1_2/scenarios.json
python3 -B -m pytest oracle/windows-dao/tests/test_protocol_validation.py tools/tests/test_verify_preservation_diff.py -q
./scripts/acceptance.sh full
```

  Step 1 expects `full` to remain nonzero with G3 `BLOCKED` because no
  write/update hosted bundle exists.

  Step 2:

```sh
python3 -B -m pytest oracle/windows-dao/tests/test_windows_dao_differential_workflow.py oracle/windows-dao/tests/test_protocol_validation.py -q
python3 -B oracle/windows-dao/scripts/differential_dryrun.py verify --inventory oracle/windows-dao/protocol/v1_2/scenarios.json --artifacts oracle/windows-dao/protocol/v1_2/dry-run
./scripts/acceptance.sh full
```

  Step 2 expects `full` to remain nonzero with G3 `BLOCKED` because the
  exact-commit hosted overlay and matrix transition do not exist.

  Step 3:

```sh
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory oracle/windows-dao/protocol/v1_2/scenarios.json
python3 -B -m pytest oracle/windows-dao/tests/test_windows_dao_differential_workflow.py tools/tests -q
python3 tools/validate_repository_contract.py
./scripts/acceptance.sh full
```

  For step 3 `full` must report G3 PASS for the complete required
  inventory (≥100 scenarios, every G3 bullet). Other gates may remain
  `BLOCKED` until P11; the PR body lists them.
- Reviewer checks: step 1 proves every new scenario hash and branch id and
  flips one byte inside a `preserve_paths` region to show the diff reports it;
  step 2 reproduces dry-run artifacts and shows DAO modes remain confined to
  the new validator; step 3 independently selects one read, write, and update
  scenario, compares canonical bytes, verifies the complete allowlist, and
  confirms G3 is PASS only for the complete required inventory.
- Sol failure modes: counting a preservation leg without running the diff;
  accepting `skipped`; treating step 1 or 2's expected G3 blocker as a pass;
  widening the matrix beyond the explicit allowlist; redispatching a
  scientific failure.
- Go/no-go: human approval after each review; step 2 requires step 1 merged;
  hosted step 3 requires the disclosed step-2 dry run and human dispatch.

### P11 — Release gates G2, G5, G6, G7, G8 and the independent-check capabilities

- Goal: complete G2 and G5–G8 and close the four independent-check
  capabilities. Implement the public Rust `database.validate` operation and
  complete the remaining atomic-publication and malformed-input behavior;
  retain exact-commit independent reports (via the P8T mechanism) for
  `database.validate`, `transactions.copy_on_write_atomic_publish`,
  `output.deterministic_configuration`, and
  `safety.malformed_input_bounds_and_limits`; enable only the code-owned
  adapters that validate those report kinds; and move exactly those four
  entries to `independent_check`. Complete at least 300 reconciled
  meaningful cases, ten-minute G5 fuzz campaigns per registered target with
  the 256 MiB/5 s malformed-corpus limits, G6 coverage/mutation thresholds
  and survivor ledger (`docs/validation/G6_EVIDENCE.md`), G7 checked
  Criterion baselines through at least 100,000 rows, and the exact-commit
  G8 cross-platform aggregate (`tools/ci_evidence.py verify-aggregate`),
  consumer project, and release artifacts.
- Binding inputs: P10 merged; `ACCEPTANCE.md` G2, G5–G8; `tools/run_g6_coverage.py`.
- Deliverables, five PR steps:
  1. `feat(jet3): release-safety completion` — public
     `database.validate`, remaining copy-on-write atomic-publication behavior,
     and remaining malformed-input bounds, with focused manifested tests.
     Complete `database.validate`,
     `transactions.copy_on_write_atomic_publish`, and
     `safety.malformed_input_bounds_and_limits` to `implemented`;
     `output.deterministic_configuration` is already implemented by P9.
  2. `test: G2 manifest to ≥300 meaningful cases` — add only meaningful
     manifested cases and make `reconcile_tests.py --minimum-meaningful` the
     checked count interface.
  3. `test: G5/G6 campaigns and survivor ledger` — add the checked registered-
     target release runner, retained malformed-corpus report, exact-commit
     coverage and mutation envelopes, mutation runner, and disposition every
     survivor in `docs/validation/G6_EVIDENCE.md`.
  4. `perf: G7 Criterion baselines` — add the 100,000-row cases, retained
     resource metrics, normalized approved baseline, exact-commit candidate
     bundle, and comparison report.
  5. `docs(evidence): G8 aggregate and independent-check reports` — add the
     cross-platform aggregate, clean-consumer and release-artifact reports,
     four explicit report schemas/validators and intrinsic adapters
     (`database_validate_v1`, `copy_on_write_atomic_publish_v1`,
     `deterministic_configuration_v1`, `malformed_input_bounds_v1`), and move
     exactly the four named matrix entries to `independent_check`. Each report
     names its exact scenario ids and release commit. If the semantics cannot
     share an adapter, they remain separate as named here.
  Harness tooling for steps 2–4 may be prepared after P4 (Section 7); their
  evidence runs occur only at the release-candidate commit.
- Acceptance checks: common commands on every step, plus:

  Step 1:

```sh
cargo test --package jet3 database_validate --locked
cargo test --package jet3 copy_on_write_atomic_publish --locked
cargo test --package jet3 malformed_input_bounds_and_limits --locked
./scripts/check-source-size.sh
```

  Step 2:

```sh
python3 tools/reconcile_tests.py --minimum-meaningful 300
python3 tools/validate_repository_contract.py
```

  Step 3:

```sh
python3 -B fuzz/tools/run_release_campaigns.py --targets fuzz/targets.json --seconds-per-target 600 --max-rss-mib 256 --max-input-seconds 5 --output artifacts/g5-fuzz-report.json
python3 -B fuzz/tools/run_release_campaigns.py --verify-report artifacts/g5-fuzz-report.json
python3 tools/run_g6_coverage.py --expected-commit "$(git rev-parse HEAD)" --output artifacts/g6-coverage.json --timeout-seconds 3600
python3 tools/validate_g6_evidence.py coverage artifacts/g6-coverage.json
python3 tools/run_g6_mutation.py --expected-commit "$(git rev-parse HEAD)" --output artifacts/g6-mutation.json --survivor-ledger docs/validation/G6_EVIDENCE.md
python3 tools/validate_g6_evidence.py mutation artifacts/g6-mutation.json
```

  Step 4:

```sh
cargo bench --manifest-path benches/Cargo.toml --benches --locked
benches/scripts/capture_metadata.sh artifacts/benchmarks/environment.json
benches/tests/test_capture_metadata.sh
python3 benches/scripts/normalize_criterion.py --criterion-root benches/target/criterion --resources artifacts/benchmarks/resource-metrics.json --bundle-output artifacts/benchmarks/candidate-bundle --metadata artifacts/benchmarks/environment.json --raw-artifact-path artifacts/benchmarks/raw-bundle/raw-measurements.json
python3 benches/scripts/compare_baseline.py benches/baselines/normalized-approved.json artifacts/benchmarks/candidate-bundle/comparison-input.json --output artifacts/benchmarks/comparison.json
```

  Step 5:

```sh
python3 tools/ci_evidence.py verify-aggregate artifacts/ci/aggregate.json --expected-commit "$(git rev-parse HEAD)"
python3 tools/validation/verify_clean_consumer.py artifacts/release/consumer-report.json --expected-commit "$(git rev-parse HEAD)"
python3 tools/validation/verify_release_artifacts.py artifacts/release/artifact-report.json --expected-commit "$(git rev-parse HEAD)"
python3 tools/validation/independent_checks.py validate --kind database_validate --report artifacts/independent/database-validate.json --expected-commit "$(git rev-parse HEAD)"
python3 tools/validation/independent_checks.py validate --kind copy_on_write_atomic_publish --report artifacts/independent/copy-on-write-atomic-publish.json --expected-commit "$(git rev-parse HEAD)"
python3 tools/validation/independent_checks.py validate --kind deterministic_configuration --report artifacts/independent/deterministic-configuration.json --expected-commit "$(git rev-parse HEAD)"
python3 tools/validation/independent_checks.py validate --kind malformed_input_bounds --report artifacts/independent/malformed-input-bounds.json --expected-commit "$(git rev-parse HEAD)"
python3 tools/validate_repository_contract.py
./scripts/acceptance.sh full
```

- Reviewer checks: step 1 corrupts each validator boundary and interrupts
  every publish stage; step 2 recomputes the meaningful count; step 3
  recomputes campaign duration/limits, coverage percentages, mutation score,
  and survivor dispositions from retained artifacts; step 4 reruns one
  100,000-row case and the 15% comparison; step 5 verifies the aggregate,
  consumer, release inventory, four report semantics, four intrinsic adapters,
  exact matrix diff, and exact release commit independently.
- Sol failure modes: padding the manifest with non-meaningful cases; shortening
  a ten-minute campaign; reporting coverage or benchmarks from a dirty tree;
  leaving a mutation survivor undispositioned; using one generic independent
  report for incompatible semantics; moving a fifth matrix entry; accepting a
  report for a different commit.
- Go/no-go: all four named matrix entries are
  `implemented`/`independent_check`, every other in-scope entry is
  `implemented` at its required verification, and
  `./scripts/acceptance.sh full` exits zero from a clean tree at the exact
  release commit. Only then may the human tag the release.

## 3. Experiment roadmap

### 3.1 Campaign sequence and what each must learn

Each campaign is a new experiment id, new base plan, freeze-before-holdout,
three role-rotated replicas, independent validator written from the plan,
and a claims block that is all-false except
`descriptive_provider_observation_only`. Every campaign reuses the hosted
lane via Section 3.3.

| Campaign | Unblocks | Must learn (decisive layers) | DAO operations added at checkpoints |
| --- | --- | --- | --- |
| A4 (EXP-0052) | Stage 2 close, Stage 3 minimum | H1 TDEF→map-row locators; H2 row identity/role; H3 tag-1 slot→tag-05 reference + base; H4 catalog root, kind/id/name field model (**not** the TDEF-page reference) | `CreateDatabase(dbVersion30)`, one-at-a-time `CreateTableDef`/`Append`, `CreateField` (Long, Text), `CreateIndex` nonunique, `TableDefs.Delete`, recreate, 32-row batches, delete-all, reinsert, idle reopen; canonical DAO schema snapshot every checkpoint |
| A5 | Stage 4 | catalog-record → TDEF page reference; TDEF record layout: column count, per-column type code/size/flags/ordinal/fixed-or-variable, variable-column index; index count, per-index field list and direction, unique/primary/required flags, root page reference; name → column binding | one table; append one field per checkpoint for each type in the A5 closed type list with boundary sizes (Text 1/255, Binary 255); `CreateIndex` primary / unique / nonunique / composite ascending+descending; `Indexes.Delete`; field rename via new TableDef |
| A6 | Stage 5 | row directory entry encoding incl. deleted/lookup bits; row header (column count, null map position/polarity, variable-offset table); fixed region order; row motion on update; overflow/continuation link and termination; page free-space field | rows of fixed-only, variable-only, mixed, all-null, max-size (page-edge −1/0/+1); `Edit`/`Update` growing and shrinking a row; `Delete` without compact; `CompactDatabase` explicitly **excluded** |
| A7 | Stage 6 | scalar encodings per type (byte order, Date/Time double epoch, Currency i64 scale, GUID layout and replication-id attribute, Boolean null/false/true), Text code page at CP1252 plus one pinned second ANSI page, Memo/OLE long-value pointer (inline / single page / multi-page chain), chain termination and length | per-type min/max/representative/null rows; Memo at 1, 64, 2,036, 4,096, 65,536 bytes; OLE binary patterns; `AppendChunk` |
| A-opening (P7H) | `database.open`, `format.header_and_version` completion | Jet 3 version discriminator; physical unencrypted-state and no-password check | `CreateDatabase` `dbVersion30` vs `dbVersion40`, with/without `dbEncrypt`, with/without `NewPassword`; fresh and reopened checkpoints |
| A8 (P7I) | index reading and relationship metadata | index B-tree page layout, entry encoding per key type, leaf/branch links, root from A5; `MSysRelationships` row and attribute layout | DAO-only keys at every A5 type; ordered inserts vs reverse vs random; DAO `CreateRelation` with/without cascade flags; `Relations.Delete` |
| A9 (P9) | writer allocation and free-space decisions | allocation consequences for grow/delete/reinsert/drop/recreate; free-space selection observables; usage-map growth into indirect form | DAO performs every mutation. The observer decodes only preregistered consequences from fresh DAO-produced files. No Rust code opens or mutates an A9 acquisition file. |

Rule: A5–A9 and the opening campaign may not be preregistered until the
previous campaign's result entry is merged, because each plan's candidate
grammars must cite the facts the previous result established (A4's H1
page-23 locator example is the model). If a layer returns `no_outcome`, the
next campaign carries that layer forward as its first hypothesis; do not
widen a running plan.

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
   before/after hash table in the provenance entry (R5-V02 shape). These
   schema edits are named file-by-file in the preregistration PR and are
   in scope under Section 6.4 item 2.
4. Focused contract test; repository contract passes.
5. Adversarial review passes until zero blocking findings (expect 3–6).
6. Merge; then implementation; then executed dry-run disclosure; then
   human dispatch authorization.

### 3.3 Hosted-lane rebind recipe (A3 → A4 → A5 …)

Source lane: `.github/workflows/windows-dao-a3.yml`,
`oracle/windows-dao/scripts/run-a3-replica.ps1`,
`oracle/windows-dao/scripts/a3/{A3.Worker,A3.PageStore,A3.Progress,Download-A3Artifact}.ps1`,
`oracle/windows-dao/scripts/a3_bundle*.py`,
`oracle/windows-dao/scripts/a3_holdout.py`,
`oracle/windows-dao/scripts/a3_analysis*.py`,
`oracle/windows-dao/scripts/a3_independent_*.py`,
`oracle/windows-dao/tests/test_windows_dao_a3_workflow.py`. From A5 on, the
source lane is the previous campaign's merged lane.

Copy, then change **only** these, and nothing else without a provenance
entry explaining why (`EXP-0049` is the precedent):

1. Experiment id, plan path, revision-chain paths and hashes, document and
   artifact names (`windows-dao-aN-*`), evidence types (`dao_aN_*`).
   The contract job must refuse any plan whose `experiment_id` is not
   exactly the new id.
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
| 3 | `catalog.rs`, `catalog_record.rs` | A4 H4 (root, kind/id/name only) | P4 | `catalog_parsing` | `schema.catalog_and_table_definitions` → `partial` |
| 4 | `table_definition.rs`, `index_definition.rs` | A5 (incl. catalog→TDEF reference) | P5 | `table_definition_parsing` | `schema.catalog_and_table_definitions` → `implemented`; `indexes.primary_unique_non_unique`, `indexes.composite_ascending_descending` → `partial` |
| 5 | `row.rs`, `row_directory.rs` | A6 | P6 | `row_parsing` | `rows.streaming_read` → `implemented`; `values.null_fixed_variable` → `partial` |
| 6 | `value.rs`, `long_value.rs`, `text.rs` | A7 | P7 | `long_values` | five `values.*` → `implemented` |
| 0 completion | Stage 0 modules (Section 1.1) | opening campaign | P7H | `database_opening` (existing) | `database.open`, `format.header_and_version` → `implemented` |
| index traversal | `index_tree.rs`, `relationships.rs` | A8 | P7I | `index_traversal` | two `indexes.*` → `implemented`; `relationships.*` → `partial` |
| writer | `writer/*.rs` | A4–A9 | P9 | existing + `allocator` | writer capabilities → `implemented` |
| validate | `validate.rs` | reader stages 0–6 | P11 | existing targets | `database.validate` → `implemented` |

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

### 5.1 Scenario inventory contract

TO BE CREATED by P8 step 1:
`oracle/windows-dao/protocol/v1_2/scenarios.schema.json`,
`oracle/windows-dao/protocol/v1_2/scenarios.json`, and
`oracle/windows-dao/protocol/v1_2/branch-registry.json`. Each scenario
entry has exactly these fields: `id`, `content_sha256`, `capability_ids`,
`boundary`, `operation`, `generator_recipe`, `required_branches`,
`expected_snapshot_sha256`, and `preserve_paths`. Fields that do not apply
are present as `null` or an empty array exactly as the schema specifies; no
conditional pseudo-fields are used. `capability_ids` values must exist in
`docs/validation/support-matrix.json`. `required_branches` values must exist
in the versioned closed branch registry in the same protocol directory. Any
semantic scenario edit requires a new `content_sha256`; changing an `id`
requires a new scenario.

`content_sha256` is SHA-256 over the canonical UTF-8 JSON serialization of the
closed scenario object with the `content_sha256` member omitted. The validator
constructs that projection, recomputes the hash, and rejects a mismatch. Tests
cover one valid entry, a semantic-field edit without rehashing, a hash-only
edit, and alternate JSON whitespace/key order that canonicalizes to the same
projection.

Prefix rule from protocol 1.0: `DAO-READ-*` (`rust_read_dao`),
`DAO-WRITE-*` (`dao_open_rust`), `DAO-UPDATE-*` (`dao_verify_rust_update`).
Minimum set for G3 (≥100 scenarios) grouped by capability:

- `DAO-READ-OPEN-*` (`database.open`, `format.header_and_version`): fresh
  empty; after compact-free growth; largest supported size; Jet 4,
  encrypted, and passworded rejection (negative scenarios).
- `DAO-READ-ALLOC-*` (`format.pages_allocation_usage`): small inline; inline
  capacity −1/0/+1; delete/reinsert reuse; drop/recreate; idle reopen;
  inline→indirect; each extended slot boundary; multiple tables.
- `DAO-READ-SCHEMA-*`: every type in the A5 closed list, every index form,
  relationships.
- `DAO-READ-ROWS-*`, `DAO-READ-VALUES-*`: per type null/min/representative/
  max/page-boundary; Memo/OLE single- and multi-page; code pages.
- `DAO-WRITE-*` mirrors of each read scenario, produced by Rust.
- `DAO-UPDATE-*`: insert/update/delete/create-table/drop-table/index
  add-drop/relationship add-drop, each with a preservation leg.
- Failure scenarios for each create/drop/CRUD/index/relationship form.

P8's read advancement allowlist and P10's write/update allowlist are the
only matrix-transition authorities for `dao_differential`; each is an
explicit list of capability ids in its PR.

### 5.2 Snapshot contract

TO BE CREATED by P8 step 1:
`oracle/windows-dao/protocol/v1_2/canonical-semantic-snapshot.schema.json`.
The schema fixes every object and value field without ellipses, rejects
unknown fields, defines raw-byte preservation (lossless raw hex alongside
any converted form), and defines canonical ordering for: table names;
column ordinals; index names and composite field ordinals; relationships;
nulls; duplicate values; and tables with no primary key. Rows are ordered
by a schema-defined canonical tuple over all lossless typed values plus an
explicitly defined stable tiebreaker present in both DAO and Rust
snapshots; no physical "row id" is assumed unless a preregistered source
establishes it and both producers emit it. Both producers serialize with
`jet3-testkit::canonical_json` ordering rules and are tested against the
same schema, while semantic extraction remains independent. DAO never
emits allocation internals; Rust additionally emits
`coverage-receipt.json`, bound to the source MDB SHA-256, containing only
branch ids from the closed registry and the allocated-set digest (Option 3
requirement).

### 5.3 `dao_differential_v1` adapter (`tools/validation/release_evidence_adapters.py`)

Fail closed unless: exact clean commit per the P8T mechanism; provider
identity matches the pinned hash; every required scenario id for each
allowlisted capability present with both snapshots and equal canonical
bytes; coverage receipt lists every branch the scenario inventory marks
`required_branches`; update legs have a `preservation_diff` result from
`tools/verify_preservation_diff.py` with zero unexpected differences; no
scenario `skipped`. Maximum verification level is intrinsic to the adapter
(`dao_differential`); availability is an intrinsic code property changed
only in P8T step 2; `evidence-policy.json` flips `status` to `enabled` only
in P8 step 4, the same PR that first relies on the adapter's tests (accept a
complete overlay; reject one missing scenario; reject one altered snapshot
byte).

### 5.4 Legs

1. Read leg (P8): DAO generates `dbVersion30` file → close/reopen → DAO
   snapshot; Rust snapshot + receipt → compare.
2. Write leg (P10): Rust creates file from declarative input → DAO opens,
   snapshots → compare with expected.
3. Update leg (P10): DAO generates → Rust mutates via `atomic_update` → DAO
   reopens, snapshots → compare intended change; preservation diff over
   `preserve_paths`.

### 5.5 Exact-commit evidence publication prerequisite

P8 is BLOCKED on the P8T amendment. Today `tools/validation/evidence.py`
requires every `dao_bundle` reference to name the exact current `HEAD`
(lines 140–159) and then rejects every `dao_bundle` unconditionally (lines
316–320), and `dao_differential_v1` is intrinsically `unavailable`. A PR that
adds a matrix reference changes `HEAD`, so no committed reference can
satisfy the current check; this is consistent with `EVIDENCE.md` ("must
bind the exact clean release commit") and `DAO_PROVIDER_BLOCKER.md`
("reference that exact-commit bundle from the support matrix and acceptance
record") only once a non-self-referential binding exists.

Until the P8T amendment is merged and an overlay for the exact clean
candidate commit passes it, no matrix verification state may advance. After
it is merged, a capability moves to `dao_differential` only in a P8 step-4
or P10 step-3 PR that: (1) names the capability in the explicit allowlist;
(2) adds the scenario inventory rows and matching test-only Rust files in
`tests/manifest.json`; (3) binds the overlay by the approved mechanism with
its manifest SHA-256; (4) shows the enabled adapter's passing report; and
(5) adds a provenance entry for the bundle. For P8, retain a separate adapter
report whose explicit read-capability subset is PASS;
`./scripts/acceptance.sh full` must report G3 `BLOCKED` until P10. For P10
step 3, the adapter report covers the complete required inventory and
`./scripts/acceptance.sh full` must report G3 PASS. Never describe a subset
adapter result as `full` or G3 PASS.
Earlier bundles (M1, A3, A4 …) are design inputs only. A verification-state
change in any other kind of PR is a Section 6.4 escalation and must be
reverted.

## 6. Process rules

### 6.1 Worktrees and sessions

- One worktree per branch/PR (`git worktree add
  ~/.herdr/worktrees/access97-rs/<branch>`); never `cd` to another tree.
- One fresh session per task: diagnose → fix, review, dry-run, hosted
  precondition, hosted monitor, post-run are separate sessions. Tear the
  worktree down after merge.
- Stash is shared: never bare `git stash`; prefer a WIP commit.
- Evidence work happens in a detached, clean checkout at the exact
  evidence commit; fixes happen in a separate worktree on the PR branch.

### 6.2 Reviewer ≠ author

Every PR is reviewed by a session that did not write it, using the review
template in Section 6.5, and the review is appended to
`/private/tmp/sol-<topic>-review.md`; when a review becomes a plan input it
is copied byte-for-byte under the relevant `design-inputs/` directory and
hash-pinned (P0 is the model). A review that only restates the PR
description is invalid; it must contain the reviewer's own recomputed
numbers, executed commands with output, and at least one attempted
falsification per blocking claim. Verdict literals: `MERGE`,
`MERGE-AFTER-NITS`, `DO-NOT-MERGE`. The human merges; sol never merges.

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
  count, and size (2,700/2,701 s; 800,000,000/800,000,001 A4 units; 1,024/
  1,025 bits; 800/801 lines).
- **Source size:** no production source file may exceed 800 physical
  lines. Split a module before an edit would make it 801 lines. The checked
  boundary (`scripts/check-source-size.sh`) accepts 800 and rejects 801.
- **Format constants:** every format-derived numeric constant or assertion
  cites the provenance entry that states or derives it. Language
  constants, collection indices, test-only calibration values, and generic
  resource arithmetic do not receive spurious format citations.
- **Schema hash cycles:** never put a plan hash inside a schema `const`;
  pin it in checked code (`EXP-0050` R5-V01 rationale).
- **REST download helper, 900 s fan-in:** hosted fan-in uses the retrying
  Actions-API helper and must fit 900 s; if it cannot, the fix is to make
  fan-in cheaper, not to raise the bound (PR #68 precedent).
- **No claim words.** Never write "verified", "compatible", "supported",
  "DAO verified" outside the evidence vocabulary; result entries' `claims`
  blocks stay all-false except the descriptive flag.
- **Matrix edits** happen only in the PR steps that name them (P3, P4, P5.5,
  P6.5, P7.5, P7H.5, P7I.5, P8.4, P9.5, P10.3, P11.5) and only for the
  exact fields those steps name.
- **No `unsafe`; no panics.**
- **Scope lock.** A PR does one deliverable from one phase step. Anything
  discovered outside it becomes a note in the PR body, not a change.

### 6.4 Escalate to the human and stop

Stop, write the question in the PR body or the session transcript, and
wait, when any of these occurs:

1. Plan-text ambiguity: two readings of a preregistered plan lead to
   different code (`EXP-0045` precedent — an additive revision is needed).
2. An evidence-schema change that is not named file-by-file in the current
   phase step, not covered by a recorded human scope approval, or not
   accompanied by before/after hashes and focused old/new rejection tests.
   A schema change explicitly listed in an approved phase step may proceed
   only within that PR's scope lock.
3. A verification-state advancement that is not the exact capability/state
   transition named by the current phase step, lacks prior human
   go/no-go, or lacks exact-clean-commit evidence accepted by the intrinsic
   adapter. Merely implementing code never authorizes verification
   movement.
4. A hosted failure after acquisition begins, or any failure whose class is
   uncertain. Infrastructure failures are limited to provider/image drift
   before the first DAO mutation, hosting/API download or extraction
   failure, runner loss, and setup timeout before the first DAO mutation.
   Analyzer exceptions, producer/validator disagreement, unexpected
   predicate terminals, scientific work/resource bound rejection, and any
   timeout after the first DAO mutation are scientific events. Record the
   run once and never re-dispatch without a human decision; ambiguity is
   scientific for stop/escalation purposes.
5. A reviewer and author disagree after one exchange.
6. A dependency on a fact not in `docs/PROVENANCE.md`.
7. A need to touch a file outside the PR's scope lock.

### 6.5 Session prompt templates

Substitute every angle-bracket field before sending a prompt. After
substitution, send the fenced text unchanged. If any field cannot be filled
from committed inputs, stop under Section 6.4.

Fix session:

```text
Worktree <absolute path>, branch <name>, task: <one numbered PR step from IMPLEMENTATION_PLAN.md §<phase>>.
Read AGENTS.md, docs/plans/IMPLEMENTATION_PLAN.md §<phase>, and these committed binding inputs: <exact paths with full hashes>.
Review instructions: <absolute review path>, SHA-256 <full hash>. The review need not be committed unless the phase makes it a plan/provenance input.
Allowed files: <exact paths or bounded globs>. Forbidden files: <exact paths>.
Apply exactly the replacement text in review findings <ids>; do not reinterpret it. Before editing, recompute the review-file SHA-256 and stop if it differs.
Recompute every format-derived number with <committed script and exact command>; include command and output in the PR body.
Run exactly:
just ready
python3 -B -m pytest oracle/windows-dao/tests tools/tests fuzz/tests -q
python3 tools/validate_repository_contract.py
python3 tools/reconcile_tests.py
git diff --check "$(git merge-base origin/main HEAD)" HEAD
Then run the phase-specific commands: <literal commands copied from the phase step>.
Stop and ask if §6.4 applies. Open a draft PR titled '<type>(<scope>): <summary>' and reply with the URL and the exact command results.
```

Review session:

```text
Adversarial review of PR <n> at immutable head <full sha> against IMPLEMENTATION_PLAN.md §<phase> and its literal reviewer checklist: <checklist text>.
Use a fresh read-only checkout. Do not read the PR description until after your own recomputation.
Run the phase's common and phase-specific acceptance commands. For each material claim, attempt one concrete falsification by executing code or decoding bytes; record command and output.
Report findings as B<n> (blocking), S<n> (should-fix), or N<n> (nit), each with exact replacement text.
Append pass <k> to /private/tmp/sol-<topic>-review.md. Edit no repository file.
End with exactly one verdict: MERGE, MERGE-AFTER-NITS, or DO-NOT-MERGE.
```

Dry-run session:

```text
Execute the preregistered dry run for <campaign> at clean commit <full sha> with this literal plan command: <exact generate command>.
Retained input: <absolute read-only bundle path>; expected manifest SHA-256: <full hash>. Verify the hash before use and open no holdout input.
Produce artifacts only through <harness path>. Refuse every fixture containing accepted, valid, reachable, or passed result fields.
Run this literal verification command in a second clean checkout: <exact verify command>. Compare the complete artifact inventory and hashes byte-for-byte.
Write the additive disclosure entry at the next unreserved EXP id. Record commands, commits, full hashes, <expected count>/<expected count> measured predicate results, and acquisition_authorized=false. Advance no capability.
```

Hosted precondition session:

```text
Check only these preconditions for <campaign>, then stop for human dispatch: disclosure entry <EXP id> is merged on main; .github/workflows/windows-dao-hosted.yml run <run id> proved the required image and full dao360.dll hash within seven days; git status --porcelain is empty; and test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" passes.
Record every command and result. Do not dispatch.
```

Hosted monitor session, started only after the human supplies a run id:

```text
Monitor hosted run <run id> for <campaign>. Do not re-dispatch. On failure, classify it using IMPLEMENTATION_PLAN.md §6.4(4), record the run id, attempt, failed step, and retained artifact identities in <notes path>, then stop for human review. On success, report artifact ids and hashes; make no repository edit.
```

Post-run session:

```text
Download bundle and validation artifacts for run <run id> to <absolute scratch path> and make the retained copy read-only.
Run these literal local commands: <producer validation command>; <independent validator command>. Reproduce the manifest and report hashes exactly.
Write the additive result entry at the next unreserved EXP id using EXP-0051's fields: environment, timing, artifact ids/sizes/full hashes, layer results copied from analysis-report.json, every registered predicate id accounted for, prior failed attempts, claims block, and 'no capability advances'.
Change only docs/PROVENANCE.md. Open draft PR 'docs: record <campaign> first hosted result (EXP-<id>)' and report the URL plus command results.
```

## 7. Effort (elapsed, one executor + one reviewer + human gates)

| Phase | Estimate | Basis |
| --- | --- | --- |
| P0 close A4 plan | 1–2 days | A3 needed R2–R5 over ~8.5 h; A4 is on pass 6 |
| P1 A4 lane + dry runs | 3–5 days | A3 analyzer/validator/lane were PRs #53–#64 over ~2 days with parallel authors |
| P2 A4 hosted + result | 0.5–2 days | A3: five infra failures then a 22-minute run |
| P3 Stage 2 close | 3–5 days | three new modules, fuzz target, contract re-pins |
| P4 Stage 3 minimal catalog | 3–4 days | |
| P5 A5 + Stage 4 | 2–3 weeks | largest grammar (closed type list × boundaries × index forms) |
| P6 A6 + Stage 5 | 2–3 weeks | |
| P7 A7 + Stage 6 | 2–3 weeks | long-value chains dominate |
| P7H opening campaign | 1–2 weeks | small predicate set; full lane cycle |
| P7I A8 + index traversal | 2–3 weeks | |
| P8T evidence tooling amendment | 1 week | contract text + adapter path + six tests |
| P8 read differential | 2–3 weeks | adapter foundation exists after P8T |
| P9 A9 + writer + independent verifier | 6–10 weeks | proposal estimate |
| P10 write/update legs | 3–4 weeks | |
| P11 release gates + `database.validate` | 2–3 weeks | G6/G7 tooling partly exists (`tools/run_g6_coverage.py`) |
| **Total** | **~8–10 months elapsed** | serial by construction: each campaign depends on the previous result |

Parallel-safe work is limited to P11's fuzz/coverage/benchmark harness
tooling (steps 2–4 scaffolding, not their evidence runs) after P4. No
scientific campaign, dependent Rust stage, matrix advancement, protocol
schema work, or evidence-tooling amendment overlaps its provenance-producing
predecessor.
