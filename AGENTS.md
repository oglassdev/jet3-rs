# AGENTS.md

## Premise

This repository is a clean-room, original Rust implementation of the
un-encrypted Microsoft Access 97 / Jet 3 database format. Production code must
not depend on other MDB implementations, Microsoft Access, DAO, ODBC, Java, or
native C libraries at runtime. DAO is an optional Windows-only test oracle, not
a product dependency.

Do not study or adapt implementation code from MDB Tools, mdbtools-pure-rs,
Jackcess, UCanAccess, or any other MDB implementation. Record every format
source, experiment, observation, and fixture origin in the provenance records.
Never claim compatibility from a self-read or self-validation result; only an
independent DAO result can move a capability to DAO-verified.

Production Rust must keep `unsafe` forbidden, reject malformed input with
structured errors, bound allocations and work, stream where practical, and
avoid panics. Keep format constants and checked binary operations out of
high-level operations. No production source file may exceed 800 lines.

## Everyday commands

`just` wraps the common tasks; run `just` to list recipes. `just ready`
(fmt-check, clippy, tests, docs, `acceptance.sh quick`) is the green-able
pre-publish check; `just accept` is the full release contract below.

```sh
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
cargo test --workspace --all-targets --all-features --locked
RUSTDOCFLAGS="-D warnings" cargo doc --workspace --all-features --no-deps --locked
./scripts/acceptance.sh quick
./scripts/acceptance.sh full
```

The `full` acceptance command is the stable project contract. Its required
gates and current bootstrap status are defined under `docs/validation/`. It
must report `BLOCKED` until every required gate is actually wired and present.

## Change discipline

- Add provenance before or with code that relies on new format knowledge.
- Add focused tests for every invariant, boundary, and corruption path.
- Preserve unrelated semantic data during updates.
- Prefer typed offsets and checked decoding over scattered numeric offsets.
- Split modules before they approach 800 lines; avoid global mutable state and
  C-shaped APIs.
- Keep generated fixtures reproducible and identify their generator and
  environment.
- Use conventional commit messages such as `feat:`, `fix:`, `test:`, `docs:`,
  `refactor:`, `perf:`, `build:`, and `chore:`.

Binding contracts live in `docs/plans/IMPLEMENTATION_PLAN.md`,
`docs/PROVENANCE.md`, and `docs/validation/`. Amendments are additive: add a
revision plan and provenance entry; never edit a preregistered plan or ledger
history.

## Evidence campaigns

- Local or VM DAO runs are diagnostics unless the preregistration names that
  environment. Before official acquisition, run only contracts and synthetic
  dry runs locally. This restriction applies to official evidence campaigns,
  not to the explicitly exploratory development lane below.
- Before dispatch, require a merged plan and disclosure, fresh provider proof,
  an exact clean pushed commit, and recorded human authorization. Capture the
  hosted run and attempt ids.
- An honest independently validated `no_outcome` is a valid result; never
  optimize for a favorable finding.
- A failure after the first DAO mutation, or an uncertain failure, is
  scientific under `IMPLEMENTATION_PLAN.md` Section 6.4. Record it once; do not
  redispatch or change scientific inputs without the required human decision
  and additive revision or new experiment.
- Keep retained evidence read-only. Derive provenance from validated report
  JSON, not workflow summaries, and never commit or redistribute MDB or
  provider bytes.

## Exploratory Windows development

The local VM workflow in `docs/plans/VM_FIRST_DEVELOPMENT.md` is the default
format-discovery loop. Its outputs must declare `development_only = true` and
may be regenerated, revised, or discarded without campaign authorization.
They cannot advance the support matrix, satisfy a release gate, or substantiate
a compatibility claim.

Exploratory results may guide implementation after the format fact and its
reproduction scenario are recorded concisely in provenance. Keep the licensed
Windows/DAO installation, credentials, VM disks, generated MDB files, and raw
outputs outside the repository. Official acquisition and release evidence
remain subject to the evidence-campaign rules above.

## CI and review efficiency

- Overlap independent work, but never weaken bindings, holdout isolation,
  independent recomputation, exact-head review, path filters, or required
  checks.
- Use focused checks while iterating; run full phase acceptance once on the
  final candidate. Any tracked change invalidates exact-head review.
- Inspect the active step and recent durations before calling CI stalled.
  Cancel only superseded runs.
- Local acceptance, hosted CI, and review may overlap on immutable inputs. A
  post-merge rerun need not block handoff when its tree equals the reviewed
  head and no contract requires merge-commit evidence; disclose its status.

## Glossary

- **G0–G8** — the release acceptance gates (`docs/validation/ACCEPTANCE.md`).
  Fail closed: anything missing reports `BLOCKED` with a nonzero exit.
- **G1 aggregate** — commit-bound cross-platform (Linux/macOS/Windows) evidence
  bundle, validated by `tools/ci_evidence.py verify-aggregate`.
- **M0–M5/M5S1 and A1–A9** — Windows DAO evidence campaigns, not product
  milestones. Revisions are additive and never overwrite base plans.
- **Infrastructure failure / scientific event** — the fail-closed boundary in
  `IMPLEMENTATION_PLAN.md` Section 6.4. Once the first DAO mutation may have
  occurred, treat failure as scientific unless the plan clearly says otherwise.
- **Semantic reader stages 0–6** — the dependency-ordered reader plan
  (`docs/architecture/SEMANTIC_READER.md`); each stage gates on recorded
  provenance, not on any specific experiment.
- **SRC-nnnn / EXP-nnnn** — provenance ledger entries in `docs/PROVENANCE.md`
  for format sources and experiments; additive-only, never rewritten.
- **Preregistration** — an immutable, SHA-256-pinned experiment plan committed
  before data acquisition; changes require a new revision plan file.
- **DAO oracle** — licensed Microsoft DAO on Windows used as a black-box
  behavioral oracle; the only path to `dao_differential` verification.
- **Support matrix** — the capability catalog
  (`docs/validation/support-matrix.json`); its schema pins capability ids and
  required verification levels as constants.
- **Evidence worktree** — a detached, clean checkout at the exact evidence
  commit; fixes happen in a separate worktree on the PR branch.
