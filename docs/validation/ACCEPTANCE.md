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

G3 consumes no committed `dao_bundle` pointer. `./scripts/acceptance.sh full`
inherits exactly one explicitly supplied overlay root and one expected DAO
manifest hash:

```sh
JET3_RELEASE_EVIDENCE=/absolute/path/to/overlay \
JET3_RELEASE_EVIDENCE_MANIFEST_SHA256=<64-lowercase-hex> \
./scripts/acceptance.sh full
```

`JET3_RELEASE_EVIDENCE` must be an absolute detached directory containing
`release-evidence.json`; `JET3_RELEASE_EVIDENCE_MANIFEST_SHA256` must equal the
recomputed SHA-256 of exactly
`dao-bundle/bundle-manifest.json`. G3 performs no discovery, download, newest
selection, or fallback. An unset, relative, missing, multiply selected, or
hash-mismatched input is `BLOCKED` or `FAIL` as described below, never neutral
or passed.

The G3 gate invokes this literal interface:

```sh
python3 tools/validate_release_evidence.py effective-support \
  --repo-root . \
  --overlay "$JET3_RELEASE_EVIDENCE" \
  --manifest-sha256 "$JET3_RELEASE_EVIDENCE_MANIFEST_SHA256"
```

The command validates the exact clean `HEAD`, overlay and bundle-manifest
closure, runs each requested intrinsically available adapter to completion,
then applies checked policy and joins only enabled outputs to the committed
support-matrix baseline as specified by `EVIDENCE.md`. On
every safe `PASS` or `BLOCKED` resolution it emits one canonical JSON result
validated against the exact-commit schema at
`docs/validation/schema/effective-support-result.schema.json`. That closed
result and its nested shapes, full-catalog requirement, joins, ordering, hash
domains, and serialization are fixed by `EVIDENCE.md`. In particular, every
adapter output includes its evidence id, intrinsic `verification`,
`campaign_id`, positive-integer `hosted_run_id`, and positive-integer
`hosted_run_attempt`, and
`capabilities` contains the full committed catalog rather than only the
selected adapter subset. The G3 stdout artifact and the SHA-256 of its exact
canonical bytes are retained by the ordinary acceptance record; no overlay
payload is copied into the repository.

Exit 0 and `status: PASS` require the complete G3 inventory above, every
required DAO scenario and operation, no `SKIPPED` or `UNSUPPORTED` result, and
every exact-commit matrix capability whose `required_verification` is
`dao_opened` or `dao_differential` at that required effective level. This is
the exact G3-required set frozen in `EVIDENCE.md`; implementation state does not
add or remove members. Capabilities requiring `independent_check` and those
requiring `not_applicable` remain present and fully validated in the result but
do not determine G3 status. Their independent gates and the overall acceptance
result remain separate. In particular, an older P9 independent report is not
refreshed or reused merely because `HEAD` changed: its non-G3 capability stays
at the stored baseline with no detached evidence id while otherwise complete
P10 G3 evidence can pass. Supplying stale evidence still fails the ordinary
exact-commit validation. A valid read-only subset is reported as `BLOCKED`,
never as full G3 PASS. A missing selection is rejected by the G3 wrapper before
the result exists. Disabled policy, a required future write/update leg that is
not yet an authorized release input, or an otherwise valid but incomplete-for-
G3 subset exits 3 with a specific `BLOCKED:` reason; when the inputs permit a
safe resolution, stdout is the schema-valid `BLOCKED` result described above.
Unsafe paths or file types, malformed or non-canonical required JSON, dirty
state, commit/hash/contract mismatch, altered payload, a missing required
contract or selected scenario/branch, a schema-invalid complete snapshot or
opening-failure/expected-projection/operation-failure artifact, unequal
canonical comparison projections, a DAO/Rust projection pair that disagrees
with independently derived semantic intent, an invalid expected operation
failure, empty or mismatched preserve paths, unexpected preservation
difference, adapter-output/result-schema mismatch, or any other
failed executable check
exits 1 as `FAIL` and must not emit a `BLOCKED` effective-support result.
Producer and producer-extension differences are not failures by
themselves: G3 independently validates each complete snapshot, removes exactly
the two schema-declared `/producer` and `/producer_extensions` members, and
compares the canonical projection bytes as fixed by `EVIDENCE.md`.
For successful writes and updates this is a three-way equality: DAO and Rust
projections must each equal the expected projection independently derived from
the manifest-bound declarative scenario input. Agreement between the two
observed producers is not sufficient.

Disabled policy does not short-circuit validation. G3 first checks the explicit
selection and path; the canonical release-evidence path then checks file types,
exact inventory and payload closure, raw hashes and sizes, exact commit,
repeated cleanliness, bound contracts, all required schemas, intrinsic adapter
semantics, and expected-output equality, followed by its closing overlay,
payload, contract, and repository stability checks. Malformed, unsafe,
tampered, stale, dirty, or intrinsically failing evidence exits 1 as `FAIL`
regardless of disabled policy. Only an intrinsically passing selected
`dao_differential_v1` item can reach policy suppression and the named exit-3,
schema-valid `BLOCKED` result. That result has no adapter output or detached
evidence id, retains every stored baseline, and advances no capability. The G3
wrapper and staging path invoke this same validator and may not preflight,
duplicate, or bypass its semantic work.

Provider PASS facts come only from the manifest-bound canonical
`provider_proof` JSON, never from an operation log or workflow prose. G3
validates that closed document against the exact-commit provider-proof schema,
binds it to the manifest campaign, hosted workflow run/attempt, commit,
timestamp interval, and provider-proof command, and requires its hosted image,
x86 process, COM ProgID/CLSID/registration, provider path/version/hash, and
disposable `dbVersion30` activation/create/close/file-observation fields to
match the committed authorized lane exactly. This retained artifact supplements
but does not replace fresh provider proof, exact-commit human authorization,
the pre-mutation boundary, or provider-output retention and redistribution
rules.

The bundle producer does not choose executed source. G3 loads the hash-bound
committed source-closure registry, binds each command's role and indexed argv
entrypoint to one registered entrypoint, and requires exact set equality among
the selected per-role closures, command source paths, and manifest
`executed_sources`. It rejects a required-source omission, extra source,
unregistered or wrong argv entrypoint, declared source unused by any selected
closure, and role mismatch. Every passing scenario names its DAO and Rust
producer commands. At least one scenario-referenced Rust producer must be the
registry's `rust_semantic_snapshot_v1_2` entrypoint with role
`rust_producer`, subject `production_rust_library`, and path
`crates/jet3-testkit/src/lib.rs`. Its closure includes the public `jet3`
library boundary, the testkit semantic-snapshot producer and all of their
committed Rust dependencies. That producer accepts and invokes the public
`jet3::DatabaseReader` API. `jet3-cli snapshot` may drive it only when the
closure also binds the CLI driver sources; CLI paths never replace the testkit
producer or establish the library subject. G3 therefore rejects a CLI-only
closure and a closure missing either side of the public testkit-to-`jet3`
boundary. These checks and their compile/invocation tests prove a commit-bound
public API and source/dependency closure, not a dynamic runtime call graph.

Run identity is unambiguous at every boundary. Manifest `run.campaign_id` and
report/result/adapter-output `campaign_id` are the same nonempty string.
Manifest `run.hosted_run_id` and `run.hosted_run_attempt`, provider-proof,
report, result, and adapter-output fields with those names are the same JSON
positive integers. The obsolete generic `id`, `run_id`, and `attempt` names
are not admitted in these closed contracts, and a digit string cannot satisfy
a hosted run field.

Time identity is equally closed. Every timestamp location enumerated by
`EVIDENCE.md`, including provider-contract `image_proofs[].completed_at`, uses
its one calendar-valid uppercase-UTC, whole-second grammar and is compared as
integer UTC seconds. Run, command, and scenario completion may equal start at
that resolution; completion before start and subordinate endpoints outside the
inclusive run interval fail. Provider-proof freshness uses only the validated,
report-bound manifest `run.started_at`: the selected image proof passes when
its integer age is inclusively `0..604800`, fails stale at `604801`, and fails
as future-dated below zero. Acceptance must not substitute its local clock or
any other recorded timestamp for that comparison reference.

The three committed negative `rust_read_dao` scenarios are passing scenarios
only when Rust performs the expected rejection. Each requires the source MDB,
the manifest's separate `rust_opening_failure` artifact validated against
`oracle/windows-dao/protocol/v1_2/opening-failure.schema.json`, and its PR #92
`opening_failure` coverage receipt. Both artifacts are bound to the same
source-MDB hash, release revision, scenario id, opening-failure outcome, and
exact committed error class. `dao_snapshot` and `rust_snapshot` are null, as
are the other inapplicable snapshot fields, and no DAO semantic snapshot is
retained or compared. The report must say
`expected_outcome: expected_error`, `observed_outcome: error`, the same error
class, `status: PASS`, and null reason. A successful open, mismatched class,
identity mismatch within the failure pair, missing failure pair, retained DAO
or Rust snapshot, otherwise forbidden artifact, `SKIPPED`, or `UNSUPPORTED`
result is a failed evidence check, never an accepted negative or a neutral
result. The opening-failure JSON must have the canonical bytes fixed by
`EVIDENCE.md`, and its manifest reference binds those exact bytes, SHA-256, and
size independently of the success snapshot schema.

Expected failures of write/update create, drop, CRUD, index, and relationship
operations use the separate generic
`oracle/windows-dao/protocol/v1_2/operation-failure.schema.json`; read opening
failures remain on their focused schema. A passing expected failure must match
the committed operation input, release revision, operation kind, and exact
error class. A failed write leaves no MDB or success artifact. A failed update
leaves no output, proves raw before/after source hashes equal, retains baseline
and DAO/Rust snapshots of the unchanged source, and completes nonempty
preservation comparisons. Success, another error, `SKIPPED`, `UNSUPPORTED`, a
nonzero evidence wrapper exit, output mutation, a missing required artifact, or
any forbidden artifact is G3 `FAIL`.

Every successful write/update also carries the canonical artifact validated by
`oracle/windows-dao/protocol/v1_2/expected-semantic-projection.schema.json`.
Its input hash, canonical projection bytes/hash, schema identity, derivation
command, exact source closure, and standalone import isolation are bound and
independently recomputed without any Rust reader/writer result, DAO observation,
MDB, or operation log as derivation input.

For update preservation, producer-exclusion semantics apply before comparing
preserved paths, but do not weaken artifact identity. The preservation report
binds the exact raw full-document hashes of the DAO baseline, DAO post-update,
and Rust post-update snapshots plus the hash of the common canonical
post-update projection. Thus DAO and Rust producer fields may differ while the
validator still recomputes one unambiguous preservation result from the exact
three retained documents.
Every update scenario, including an expected operation failure, has at least
one inventory `preserve_paths` member and at least one comparison. The new
preservation-diff schema sets `minItems: 1` on both of its arrays, and G3
requires the inventory array to be nonempty plus exact set and order equality
among inventory paths, report paths, and comparison paths; empty, missing,
duplicate, extra, or reordered paths fail.

P8T step 2 deliberately leaves `dao_differential_v1` disabled. Its expected
`./scripts/acceptance.sh full` result is therefore nonzero `BLOCKED`: without
the two selection variables, G3 names the missing explicit overlay; with a
structurally and semantically valid selected test overlay, G3 reaches and
passes the available intrinsic adapter before naming the disabled-policy
reason. A tampered or dirty selected overlay remains `FAIL`. G3 must no longer stop
at an intrinsically unavailable adapter or the support-matrix validator's
unconditional `dao_bundle` rejection. No P8T step changes a capability's
stored or effective verification.

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
