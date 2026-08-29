# Acceptance gates

The canonical future full-suite command is:

```sh
./scripts/acceptance.sh full
```

It runs from the repository root, creates reports beneath `artifacts/acceptance/`,
and exits zero only when every required gate below passes. It must not silently
skip a gate. A missing tool, Windows DAO provider, evidence bundle, baseline, or
credential is reported as `BLOCKED` and produces a nonzero exit. The summary
includes the git commit, dirty state, commands, durations, pass/fail/blocked
counts, and artifact hashes.

The script may expose faster developer modes, but those modes never constitute
full acceptance. Full release evidence must be produced from a clean tree for
the exact release commit. A cross-platform CI run may aggregate commit-matched
reports, but a report from another commit is stale.

## G0 — scope, provenance, and dependencies

- `#![forbid(unsafe_code)]` applies to every production Rust crate.
- Dependency inspection finds no runtime dependency on DAO, Access, ODBC, Java,
  MDB Tools, another MDB implementation, or native C libraries.
- `cargo deny check` passes for licenses, bans, sources, and advisories.
- Every fixture has an origin, generator/environment, scenario ID, license,
  SHA-256, and reproduction command in a checked manifest.
- Every format assertion used by production code has a source or experiment ID
  in `docs/PROVENANCE.md`.
- The v1 scope and machine-readable support matrix agree; unknown matrix states
  and unsupported claims fail validation.

## G1 — Rust quality and portability

The following pass with warnings denied on the pinned stable toolchain:

```sh
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo doc --workspace --all-features --no-deps
cargo test --workspace --all-targets --all-features
```

Production source files are at most 800 physical lines. Public APIs are
documented, checked decoders return structured errors, and tests assert that
malformed input causes neither panic nor unbounded work. Linux, macOS, and
Windows jobs all pass. The commit-bound record format, CI aggregation, and
explicit local artifact-selection procedure are defined in
[CI_EVIDENCE.md](CI_EVIDENCE.md). Without a downloaded exact-commit aggregate,
local full acceptance reports G1 as `BLOCKED`; local quick checks remain
network-independent and are not release evidence.

## G2 — deterministic and adversarial tests

- At least 300 meaningful deterministic unit, integration, and regression
  cases exist. The manifest records each case's purpose; duplicated parameter
  rows with no distinct invariant count once.
- Property tests cover every binary encoder/decoder and randomized CRUD
  sequences, with failing seeds persisted as regressions.
- Golden tests cover pages, rows, catalog records, indexes, and long values.
- Capacity tests exercise the exact page boundary, one unit below it, and one
  unit above it for every variable-sized physical structure.
- Corruption tests cover every parsed length, count, offset, page reference,
  allocation structure, recursion/chain termination rule, and arithmetic
  boundary.
- Deterministic creation, with timestamps/identifiers supplied explicitly,
  produces byte-identical files across two runs on each platform and matching
  semantic snapshots across platforms.
- Miri passes for applicable library tests on the pinned nightly toolchain.

Counts are generated from a checked test manifest and reconciled with observed
test execution; ignored, filtered, or unexpectedly skipped tests do not count.

## G3 — DAO oracle and differential compatibility

- At least 100 meaningful declarative DAO scenarios pass. The scenario manifest
  identifies the capability and boundary covered by each.
- DAO `CreateDatabase(..., dbVersion30)` generates the authoritative initial
  Jet 3 fixtures.
- The oracle executes schema and CRUD sequences, closes and reopens the file,
  and exports canonical semantic JSON.
- Every supported DAO column type is covered at null, representative, minimum,
  maximum, and relevant size/page boundaries.
- Rust reads DAO-created files with a canonical semantic result identical to
  DAO's result.
- DAO opens every Rust-created file; DAO's canonical result matches the
  declarative input.
- After each Rust update class, DAO reopens the file and reports both the
  intended change and preservation of unrelated schema, rows, indexes,
  relationships, long values, and raw preservation fields.
- Create/drop table, insert/read/update/delete, every supported index form, and
  relationship create/drop each have positive and failure scenarios.
- The exact provider/version/environment and all hashes required by
  `EVIDENCE.md` are present.

Oracle unavailability may make an ordinary CI job neutral, but the Windows DAO
differential job and full release acceptance are `BLOCKED`, not passed.

### P8T detached-overlay consumption

G3 consumes no committed `dao_bundle`. Full acceptance inherits exactly one
absolute detached overlay and one expected manifest hash:

```sh
JET3_RELEASE_EVIDENCE=/absolute/path/to/overlay \
JET3_RELEASE_EVIDENCE_MANIFEST_SHA256=<64-lowercase-hex> \
./scripts/acceptance.sh full
```

G3 performs no discovery or fallback and invokes only:

```sh
python3 tools/validate_release_evidence.py effective-support \
  --repo-root . \
  --overlay "$JET3_RELEASE_EVIDENCE" \
  --manifest-sha256 "$JET3_RELEASE_EVIDENCE_MANIFEST_SHA256"
```

The canonical validator binds the exact clean `HEAD`, overlay raw hash,
bundle-manifest raw hash, complete file inventory, contracts, provider proof,
executed-source closures, commands, report, and scenario artifacts. A missing
selection is `BLOCKED`; relative/unsafe paths, special files, malformed input,
dirty state, stale commit, hash/size/closure mismatch, or intrinsic semantic
failure are `FAIL`.

P8T step 2's `dao_differential_v1` manifest/report is read-leg schema version
1. Its only operation is `rust_read_dao`. `dao_open_rust`,
`dao_verify_rust_update`, and every write/update operation or artifact are
rejected as `unsupported_operation_for_schema_version`, not accepted,
skipped, or treated as a passing expected failure.

Positive reads require independently schema-valid complete DAO and Rust v1.2
snapshots plus the Rust coverage receipt. G3 removes exactly the
schema-declared `/producer` and `/producer_extensions` members, compares
canonical projection bytes, and continues comparing raw/converted values, raw
preservation, ordering, and every other semantic field. The complete source
documents remain separately raw-hash/size bound.

The three committed negative reads pass only when Rust rejects with the exact
committed class: `encrypted_database`, `unsupported_version`, or
`password_protected`. Each requires the source MDB, a canonical separate
`rust_opening_failure` artifact, and matching `opening_failure` coverage
receipt bound to the same scenario, commit, source hash, outcome, and class.
DAO/Rust success snapshots are null. Success, wrong class, missing or
mismatched failure/receipt, forbidden snapshot, `SKIPPED`, or
`UNSUPPORTED` is G3 `FAIL`.

Provider facts come only from the manifest-bound structured proof and committed
contract, never logs. G3 checks hosted image, x86 COM registration/CLSID,
provider path/version/hash, and disposable `dbVersion30` probe; provider-proof
age relative to manifest start passes through 604800 seconds and fails at
604801 or when future-dated. Fresh proof, exact-commit human authorization,
pre-mutation boundary, retention, and redistribution restrictions remain
binding.

Executed sources come from the committed hash-bound source-closure registry.
G3 requires exact union equality with command source lists and
`executed_sources`, correct roles and indexed argv entrypoints, and rejects
missing, extra, unused, or CLI-only source claims. P8 owns adding the actual
PR-#92 Rust snapshot producer, public `DatabaseReader` closure, and optional
CLI driver before acquisition; P8T step 2 does not run nonexistent Rust/CLI
producer targets.

The validator emits the closed canonical
`effective-support-result.schema.json` result on every safe PASS/BLOCKED
resolution. It records overlay/manifest hashes, exact commit and run identity,
all enabled adapter outputs, and every committed matrix capability exactly once
in strict id order with stored/effective verification and detached evidence
ids. G3 derives its required capability set from exact matrix entries requiring
`dao_opened` or `dao_differential`; entries with other requirements remain
mandatory in the full catalog but do not change that G3 predicate.

Intrinsic validation precedes policy. The canonical path completes path/type,
inventory, raw hash/size, exact-commit, repeated-cleanliness, contract,
provider, schema, read-semantic, expected-output, and closing-stability checks
before consulting policy. Thus malformed or tampered selected evidence exits 1
`FAIL` despite disabled policy. Only complete intrinsic PASS may reach the
unchanged disabled-policy exit 3 `BLOCKED`, with no adapter output, evidence
id, or advancement.

After P8 enables policy, a complete explicit read allowlist may yield a passing
read-subset adapter result. Full G3 still exits 3 `BLOCKED` because read-leg
schema version 1 has no write/update contracts or operations. The specific
blocker is `future_write_update_contract_required`; absence cannot be empty
coverage, `SKIPPED`, G3 PASS, or a compatibility claim. Before P10 can add
those legs, the human-approved **P10 exact write/update contract gate** in
`IMPLEMENTATION_PLAN.md` must freeze a version extension, declarative intent,
structured failures, non-vacuous preservation, public writer/update source
closures, and executable schemas/tests/commands.

P8T itself keeps `dao_differential_v1` disabled. Without a selected overlay,
full acceptance names the missing explicit selection and exits 3. With a
complete intrinsically passing test overlay, it must finish the intrinsic read
adapter before naming disabled policy and exiting 3. Neither path may report
intrinsic unavailability, the old unconditional stored-`dao_bundle` rejection,
effective advancement, or G3 PASS.

## G4 — independent writer verification and atomic updates

- The Rust reader is never the only verifier of Rust output.
- A writer test is incomplete until the independent structural verifier passes;
  an interoperability claim additionally requires DAO evidence.
- Atomic-update fault injection covers every stage: private-copy creation,
  mutation, validation, file fsync, directory/metadata handling where
  supported, and rename/publication.
- At every injected failure the original is byte-identical and valid, or the
  fully verified replacement is published; no partial result is exposed.
- Recovery behavior and platform-specific rename guarantees are documented and
  tested. Concurrent multi-user locking and Jet-equivalent in-place recovery
  remain out of scope.

## G5 — fuzzing and resource limits

Separate fuzz targets exist for database opening, catalog parsing, table
definition parsing, row parsing, index traversal, and long values.

- Each target passes a 60-second deterministic smoke run in ordinary CI.
- Full acceptance runs each target for at least 10 minutes with the checked
  seed corpus plus generated malformed corpus.
- No crash, panic, hang, sanitizer finding, or unbounded allocation is allowed;
  every discovered issue is minimized and checked into regression fixtures.
- Each parser accepts an explicit `ResourceLimits` policy. Before allocation or
  traversal it checks file-derived lengths, counts, page numbers, chain depth,
  decoded value size, and total work.
- Malformed-corpus tests run per case with a 5-second wall-clock timeout and a
  256 MiB peak-RSS ceiling, unless a stricter checked limit applies. Exceeding
  either fails acceptance.
- File-size-proportional work is benchmarked on adversarial inputs at successive
  sizes; unexplained superlinear growth fails unless the algorithm and bound
  are documented.

The numeric product defaults live in the public limit policy and its tests;
changing them requires a documented security/performance rationale.

## G6 — coverage and mutation testing

- `cargo llvm-cov` reports at least 90% line coverage and 80% region/branch
  coverage for core format code.
- No core module is excluded merely to improve the percentage. Generated code,
  CLI presentation code, and platform-only oracle glue may be reported
  separately with documented exclusions.
- Mutation testing covers checked encoding/decoding, allocation, row packing,
  and index code.
- Core mutation score is at least 85% after removing tool-confirmed equivalent
  or unreachable mutants. Every survivor is listed with owner, rationale,
  risk, and disposition; survivors affecting a format or safety invariant block
  release.

## G7 — performance and regression control

Criterion benchmarks cover open, catalog load, table scan, indexed lookup,
database creation, insertion, update, deletion, Memo/OLE access, and semantic
verification. Datasets range from tiny fixtures through at least 100,000 rows.

Reports record throughput, latency distribution, peak RSS, and output size.
Checked baselines include hardware, OS, toolchain, scenario, and fixture hashes.
An unexplained regression greater than 15% in median latency, throughput, peak
memory, or output size fails the dedicated performance gate. Noise is measured
with repeated samples; an approved baseline update states the reason.

MDB Tools may be compared as a licensed black box for reads. DAO may be compared
on Windows where meaningful. Comparative results never weaken correctness,
bounds-checking, or clarity gates.

## G8 — reproducibility, CI, and release

- CI includes Linux, macOS, and Windows tests; formatting, Clippy, docs,
  coverage, Miri, fuzz smoke, benchmark compilation, and dependency checks.
- A Windows DAO job runs when the declared provider is provisioned and emits
  the evidence bundle. Required release evidence cannot be waived.
- Fixture regeneration from a clean checkout produces the manifest's hashes,
  except fields explicitly normalized and justified in provenance.
- Release artifacts contain API/format documentation, examples, the support
  matrix, provenance, oracle environment report, coverage/mutation/fuzz
  summaries, benchmark baseline comparison, and the acceptance summary.
- A clean consumer project proves that the production library runs without any
  external MDB software installed.
