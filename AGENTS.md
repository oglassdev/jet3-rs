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

Binding contracts live in `docs/PROVENANCE.md`, `docs/validation/ACCEPTANCE.md`,
`docs/validation/EVIDENCE.md`, and `docs/validation/DAO_PROVIDER_BLOCKER.md`.
Amendments are additive: a new revision plan file plus a new provenance entry,
never an edit to a preregistered plan or a rewrite of ledger history.

## Glossary

- **G0–G8** — the release acceptance gates (`docs/validation/ACCEPTANCE.md`).
  Fail closed: anything missing reports `BLOCKED` with a nonzero exit.
- **G1 aggregate** — commit-bound cross-platform (Linux/macOS/Windows) evidence
  bundle, validated by `tools/ci_evidence.py verify-aggregate`.
- **M0–M5, M5S1** — preregistered Windows DAO oracle experiment campaigns
  (`oracle/windows-dao/experiments/`). Evidence campaigns, not product
  milestones; `R` suffixes (M5R2…) are additive plan revisions.
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
