# AGENTS.md

## Premise

`jet3-rs` is a clean-room, original Rust implementation of the unencrypted
Microsoft Access 97 / Jet 3 database format. Production code has no runtime
dependency on Microsoft Access, DAO, ODBC, Java, or native C libraries.
Microsoft DAO on Windows is an optional black-box test oracle only.

Never study or adapt implementation code from MDB Tools, mdbtools-pure-rs,
Jackcess, UCanAccess, or any other MDB implementation. Every format fact used
in `crates/jet3` cites a `SRC-`/`EXP-` entry in `docs/PROVENANCE.md`; add the
entry with (or before) the code that relies on it. Never claim compatibility
from self-validation; only a DAO differential result can.

Scope for v1 is defined in `docs/plans/V1_SCOPE.md`. Work is tracked in
GitHub issues; there is no phase plan to follow.

## Everyday commands

`just` lists recipes. `just ready` (fmt, clippy, tests, docs, quick
acceptance) is the pre-PR check. Use focused checks while iterating; run
`just ready` once on the final candidate.

## Rules for `crates/jet3` (the parser)

These apply to the library crate only.

- `unsafe` is forbidden; no panics; malformed input returns structured errors.
- Bound allocations and work against untrusted input; stream where practical.
- Keep format constants and checked binary decoding in typed low-level
  modules, out of high-level operations.
- No source file over 800 lines; split modules before they get there.
- Add a focused test for each invariant, boundary, and corruption path.
  Don't write tests for unthinkable regressions.
- Preserve unrelated data during updates.

## Rules for everything else

`jet3-cli`, `jet3-testkit`, `tools/`, `scripts/`, `oracle/`, and CI are
ordinary code. Use normal crates (`sha2`, `serde_json`, etc.), keep it simple,
and do not apply the parser's threat model to test harnesses, build scripts,
or JSON emitters. No build-identity attestation, durability proofs, or
resource budgeting outside the parser.

## DAO verification

- Group related schemas, boundaries, mutations and controls into reproducible
  suites. Record inputs, source revision, provider environment and results.
  Preregistration, separate plan PRs and per-run approval are not required.
- Fix harness and implementation failures and rerun as needed. Keep failed
  results alongside subsequent runs; do not overwrite historical outcomes or
  weaken semantic comparisons to obtain a pass.
- Record accepted format observations and differential outcomes additively in
  `docs/PROVENANCE.md`. State the tested scope and any remaining failures.
- Local Windows VM and hosted DAO runs may both establish differential
  evidence when they retain reproducible inputs and complete comparisons.
  Opening a file alone and Rust self-validation do not establish compatibility.
- Never commit MDB bytes, provider binaries, VM disks or credentials. Keep
  these outside the repository; see `docs/LOCAL_WINDOWS_VM.md`.

## Change discipline

- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`,
  `perf:`, `build:`, `chore:`, `ci:`.
- Group implementation, tests, validation fixes and documentation into coherent
  deliverables. Avoid tiny plan/outcome PRs. Squash-merge completed, reviewed PRs.
- Review is for correctness. Repeated adversarial review rounds on non-parser
  code are out of scope.
- Prefer GPT-6 Sol with high reasoning for independent review and Windows
  differential testing. Use subagents only when useful, with at most two active
  at once; keep implementation ownership and VM access explicit.
- `docs/PROVENANCE.md` is additive-only. Other docs may be edited or deleted
  freely; git history is the archive.
