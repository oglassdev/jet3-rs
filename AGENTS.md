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

## DAO experiments

- Preregister each experiment: commit a SHA-256-pinned plan before acquiring
  data. If the plan is wrong, fix it in the next experiment; do not stack
  revision files.
- Record the outcome once as an additive `EXP-` entry derived from the
  validated report JSON. An honest `no_outcome` is a valid result.
- A failure after the first DAO mutation is a scientific result, not an
  infrastructure retry. Do not redispatch without a human decision.
- Never commit MDB bytes or provider binaries. Local VM runs
  (`docs/LOCAL_WINDOWS_VM.md`) are for discovery; only hosted preregistered
  runs feed the support matrix.

## Change discipline

- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`,
  `perf:`, `build:`, `chore:`, `ci:`.
- One PR, one deliverable. Prefer a 500-line PR over a 5,000-line one.
- Review is for correctness. Repeated adversarial review rounds on non-parser
  code are out of scope.
- `docs/PROVENANCE.md` is additive-only. Other docs may be edited or deleted
  freely; git history is the archive.
