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

Before any adapter evidence output, G3 validates the release commit's v1.2
scenario inventory with `complete=True`, exactly matching:

```sh
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory \
  oracle/windows-dao/protocol/v1_2/scenarios.json --complete
```

Any `deferred_requirements` entry is `FAIL` with
`incomplete_scenario_inventory_deferred_requirements`, even while policy is
disabled. Isolated complete fixtures may exercise P8T step 2; the real P8 lane
stays `BLOCKED` until the committed inventory passes complete mode.

Positive reads require independently schema-valid complete DAO and Rust v1.2
snapshots plus the Rust coverage receipt. G3 removes exactly the
schema-declared `/producer` and `/producer_extensions` members, compares
canonical projection bytes, and continues comparing raw/converted values, raw
preservation, ordering, and every other semantic field. The complete source
documents remain separately raw-hash/size bound.

Projection exclusion does not exclude producer validation. The DAO snapshot's
producer kind is exactly `dao`, the Rust snapshot's is exactly `rust`, and both
producer source revisions equal manifest `git_commit` and current clean
`HEAD`. A stale DAO revision, stale Rust revision, or swapped/wrong producer
kind is G3 `FAIL` before comparison.

Coverage observes only registered v1.2 branch ids, includes every required
branch, and includes no forbidden branch. Extra observed branches are allowed
when registered and non-forbidden; exact equality with required branches is
not required. Unregistered and forbidden extras are G3 `FAIL`, while the
pinned coverage schema and its ordering/uniqueness rules remain unchanged.

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

Authorization likewise comes only from the manifest-bound canonical
`dao-bundle/acquisition-authorization.json` and the exact committed
`docs/validation/schema/acquisition-authorization-v1.schema.json`, never from
a PR comment, workflow input, log, or later provenance entry. The manifest
must reference and inventory the record by exact raw SHA-256 and size and must
bind the schema as `contracts.acquisition_authorization_schema`. G3 verifies
the exact release commit and approved decision path/hash/size, stable human
actor and authority, evidence-ready clean/pushed attestation, campaign,
read-only operation, complete scenario scope, provider contract/image, single
dispatch/attempt scope, and the fixed private-retention/no-redistribution
attestations. It requires evidence-ready confirmation no later than human
authorization and authorization strictly before trusted-run start and every
acquisition command.

The authorization record is made after the evidence-ready commit but before
acquisition, so its closed schema forbids future overlay/manifest hashes,
hosted run/attempt identity, run timestamps, and result fields. The later
manifest binds it without self-reference. Missing, invalid, mismatched,
misordered, over-broad, or rights-inconsistent authorization is intrinsic
exit-1 `FAIL` before provider or evidence validation, using the exact round-8
reason codes in `EVIDENCE.md`; disabled policy cannot suppress it.

The round-9 authenticated-authorization amendment supersedes only that
record-shape and timing description. The exact evidence-ready commit contains
the allowed-signers and revoked-keys authority contracts, but no private key
or authorization. A dispatch with a fresh 64-lowercase-hex nonce first creates
one hosted run whose acquisition job is blocked on its protected environment.
The human then signs a canonical record naming exact repository, workflow
path/ref/SHA, YAML job, environment, run id, run attempt, commit, nonce,
campaign, and read scope. The record still contains no future overlay,
manifest hash, result, or acquisition artifact.

G3 requires and inventories both
`dao-bundle/acquisition-authorization.json` and its detached
`dao-bundle/acquisition-authorization.json.sig`, and binds the exact release
commit's `docs/validation/acquisition-authority-v1.allowed_signers` and
`docs/validation/acquisition-authority-v1.revoked_keys` as manifest contracts.
It independently invokes `ssh-keygen -Y verify` with the fixed
`jet3-rs-acquisition-v1@oglassdev` namespace, the signed principal, the signed
time as `verify-time`, and the commit-bound allow/revocation files. JSON actor
or authority text, GitHub actor text, a workflow log, and recomputed overlay
hashes are never authentication. A forged actor, unlisted/revoked signer,
changed signed byte, or nonzero verification is intrinsic `FAIL`.

The environment-gated acquisition job receives the signed pair only after the
run exists and the environment is approved. Its first repository-controlled
command is the registered authorization verifier; no provider, DAO, or Rust
acquisition command may start until that command exits zero. G3 independently
checks the signature, exact command/source binding, and the strict order
`evidence_ready.confirmed_at <= run.created_at <= authorized_at <
run.started_at <= verification start <= verification completion < first
acquisition-command start`. A copied approval cannot authorize a rerun or a
second dispatch because run id, attempt, and unique nonce are signed and must
equal the manifest. Missing verifier capability is `BLOCKED`; forged,
misbound, replayed, revoked, or misordered selected evidence is `FAIL`, using
the exact round-9 reason codes in `EVIDENCE.md`, before disabled policy.

P8T step 2 only implements this validator and synthetic fixtures. It neither
adds nor modifies a workflow, environment, secret, acquisition command, or
GitHub state. P8 must freeze and separately approve the actual protected
workflow/environment wiring before the first dispatch.

G3 also loads the exact clean release commit's canonical
`docs/validation/dao-differential-v1-read-allowlist.json` through
`docs/validation/schema/dao-differential-v1-read-allowlist.schema.json` and
requires manifest contracts `read_allowlist` and `read_allowlist_schema` to
bind their fixed paths, raw SHA-256 values, and sizes. Overlay copies,
wildcards, patterns, `all`, and prose aliases cannot authorize a read.

The P8T step-2 allowlist is empty. It validates as a contract but authorizes no
output: after otherwise applicable intrinsic validation, G3 exits 3 `BLOCKED`
with `read_allowlist_empty` and zero adapter outputs. P8 step 4 may replace it
only through a separately reviewed and human-approved clean pushed commit that
names exact already-committed library capability, complete scenario, and
registered branch memberships. This creates no future-hash self-reference and
does not itself change matrix or effective verification.

For a nonempty allowlist, G3 requires each capability to be an exact eligible
implemented P8 read capability; its scenarios to equal every complete
committed `rust_read_dao` scenario naming it; and each scenario's branch ids to
equal the observed coverage set while containing all required and no forbidden
branch. Shared scenarios have identical branch sets. The manifest/report
scenario set equals the union of these entries. Adapter outputs remain a
subset of the full support catalog but equal the allowlist exactly: one per
capability, none extra, with exact per-capability scenario ids and a
library/testkit rather than CLI-only subject. Violations use the stable
`invalid_read_allowlist`, `read_allowlist_contract_mismatch`,
`read_allowlist_membership_mismatch`, or
`read_allowlist_adapter_output_mismatch` exit-1 reasons in `EVIDENCE.md`, form
zero adapter output, and cannot be suppressed by disabled policy.

Executed sources come from the committed hash-bound source-closure registry.
G3 requires exact union equality with command source lists and
`executed_sources`, correct roles and indexed argv entrypoints, and rejects
missing, extra, unused, or CLI-only source claims. P8 owns adding the actual
PR-#92 Rust snapshot producer, public `DatabaseReader` closure, and optional
CLI driver before acquisition; P8T step 2 does not run nonexistent Rust/CLI
producer targets.

G3 additionally enforces the round-7 artifact-to-command join. Each command id
selects one unique committed role/entrypoint/scenario subject and exact source
closure at clean `HEAD`; every applicable generated artifact and its complete
file-inventory row name the producing command. Producer kind/revision,
scenario/source-MDB identity, production time, and artifact role agree across
the command, reference, inventory, and embedded artifact. Harness exit is zero,
artifact production occurs within the command interval, and command intervals
occur within the scenario and trusted run intervals.

A positive read has exactly the DAO source/snapshot producer and the Rust
semantic-snapshot producer. The Rust role is the registered `jet3-testkit`
producer using public `DatabaseReader`; an optional CLI entrypoint is only a
driver and CLI-only evidence fails. An expected opening failure has exactly the
DAO source producer and the production Rust opening-rejection/coverage
producer, with success-snapshot roles and artifacts absent. The diagnostic
operation log binds the same exact two command ids. G3 rejects missing,
duplicate, wrong, swapped, unrelated, nonzero, stale, out-of-interval, or
unbound commands/artifacts before comparison or policy. The stable reason-code
vocabulary is the round-7 list in `EVIDENCE.md`; a hand-built or filename-only
artifact is always `unbound_generated_artifact`.

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

The round-10 report-binding amendment removes the impossible raw-manifest-hash
cycle. The manifest still inventories and raw-hashes every non-manifest
payload, including the report, and the selected environment/overlay/full
manifest raw hashes still agree exactly. The report binds
`manifest_projection_sha256`, not raw `manifest_sha256`: G3 deep-copies the
parsed full manifest, deletes its top-level `report` reference, removes exactly
the one complete-inventory row at that reference's path, canonicalizes the
remaining object by the exact algorithm in `EVIDENCE.md`, and compares its
SHA-256 with the report. It performs this check after raw manifest, schema,
complete inventory, and report-schema checks but before report semantics,
scenario/command joins, policy, or output. Mismatch is exit-1 `FAIL` with
`report_manifest_projection_mismatch` and zero adapter output. Adapter outputs
and the effective result continue recording the raw manifest SHA-256 after the
manifest is final. This changes no selection, commit, provider, authorization,
allowlist, source, command, disabled-policy, read-only, or future write/update
requirement.

The round-10 authorization-bootstrap amendment makes the first
repository-controlled command implementable before an overlay or manifest
exists. That command is the exact `authorization-preflight` argv frozen in
`EVIDENCE.md`, not `effective-support`. It reads the signed JSON and SSHSIG as
strict bounded Base64 from the two fixed environment-secret variables,
authenticates the exact clean commit and hosted-run binding with the
commit-bound authority files, and atomically retains the decoded bytes in one
exclusive private bundle-staging child. It accepts no overlay or manifest
input and produces no evidence result; successful bootstrap/materialization is
explicitly non-acquisition.

The controller command graph has a hard success edge
`environment release -> authorization-preflight -> provider proof/DAO/Rust
acquisition`. Provider inspection and every DAO or Rust command are absent
from all failure branches and cannot be selected merely by an `always`,
cleanup, or disabled-policy path. Exit 0 is the sole edge past the preflight.
Exit 1 is invalid input/authentication/path with the stable reason from
`EVIDENCE.md`; exit 2 is local materialization or unprovable cleanup `ERROR`;
exit 3 is unavailable-verifier `BLOCKED`. All are nonzero and stop
acquisition. P8 must implement and separately review that command graph; P8T
adds only the preflight validator and synthetic tests, no workflow or command.

The round-11 timing-boundary amendment makes that success edge strictly
post-authorization at whole-second precision. The registered preflight
command's retained entry second must satisfy
`authorized_at < preflight_started_at`; eventual selected evidence must bind
that identical second as both `run.started_at` and the
`authorization-preflight` verifier-command start. Equality with
`authorized_at` is exit-1
`FAIL: acquisition_authorization_ordering_violation`, with no staging child,
verifier invocation, publication, or provider/DAO/Rust/acquisition edge. The
focused boundary fixes `authorized_at` at `2026-08-29T12:00:00Z`: a preflight
entry at that same second is rejected, and entry at
`2026-08-29T12:00:01Z` is the first timing-valid instant. The command may not
sleep, retry, or resample its clock to cross the boundary.

The round-12 receipt amendment makes that exact sampled second available to
the later controller without trusting stdout or process memory. The same
successful preflight transaction creates canonical
`dao-bundle/acquisition-preflight-receipt.json` under the closed
`docs/validation/schema/acquisition-preflight-receipt-v1.schema.json` before
atomic publication with the authorization document and SSHSIG. The receipt
binds PASS status; run id, attempt, nonce, repository, workflow, commit, job,
environment, and campaign; exact start and completion seconds; raw
authorization, signature, allowed-signers, and revoked-keys hashes and sizes;
and the registered verifier command and committed source identity. It contains
no manifest or receipt-self identity, so its final canonical bytes can be
raw-hashed without a cycle. Checked write, fsync, bounded re-read, read-only
publication, final re-read, and rollback apply to the three-file published
bundle. A failure publishes no usable receipt and exposes no acquisition edge;
the exact empty-authority sentinel remains an earlier `BLOCKED` result.

After preflight exit 0 the controller boundedly reads only the retained receipt
and copies its exact `preflight_started_at` into both `run.started_at` and the
authorization-verifier command `started_at`. It may not parse the unchanged
PASS line, reuse an in-memory value, consult a log, or resample time. G3
independently validates the receipt schema and canonical bytes, raw inventory
reference, producer command, signed/trusted identity, authority and
authorization hash/size joins, completion time, and exact reuse of the start
second before policy or output. Missing receipt state is exit-1
`missing_acquisition_preflight_receipt`; invalid receipt bytes are exit-1
`invalid_acquisition_preflight_receipt`; and altered, forged, or inconsistent
receipt state is exit-1 `acquisition_preflight_receipt_mismatch`. The focused
Step 2 tests include retained-output-only real subprocess handoff,
same-second rejection with no receipt, next-second canonical receipt success,
missing/altered/forged receipt rejection, and mutation after publication.

Step 2 verifies the DAO path through three focused modules, not one aggregate
adapter test file:

```sh
python3 -B -m unittest discover -s tools/tests -p 'test_acquisition_authorization_preflight.py' -v
python3 -B -m unittest discover -s tools/tests -p 'test_dao_differential_manifest.py' -v
python3 -B -m unittest discover -s tools/tests -p 'test_dao_effective_support.py' -v
```

The first owns preflight filesystem/signature behavior, the second manifest
and intrinsic adapter semantics, and the third effective-support resolution
and DAO-specific acceptance wiring. Their only shared fixture module is
`tools/tests/dao_differential_fixtures.py`, whose direct builders contain no
expected-result oracle or generic test framework. The additive routing table in
`IMPLEMENTATION_PLAN.md` assigns every earlier frozen name and domain exactly
once; the former `test_dao_differential_adapter.py` command is superseded.

The later manifest must raw-hash and size the exact retained path objects, and
`effective-support --repo-root --overlay --manifest-sha256` must re-read those
same bytes before any intrinsic output. A byte change, alternate path object,
copy, recanonicalization, pre-existing staging target, symlink/reparse/alias,
partial publication, nonzero verifier, or failed cleanup cannot become G3
input. Thus G3 still consumes the final selected overlay and manifest, but it
is no longer incorrectly named as the pre-acquisition authorization
entrypoint.

The round-10 authority-provisioning amendment adds a gate before that command
graph. P8T step 2 commits both production authority paths as exact zero-byte
sentinels, each with SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
and size 0. After exact-clean-commit and fixed-path/type/bound checks, both
`authorization-preflight` and later G3 recognize only that exact pair as exit
3 `BLOCKED` with `acquisition_authority_unprovisioned`. This check precedes
authorization-secret/artifact reads, staging inspection, verifier discovery,
provider proof, read-allowlist evaluation, policy, or output; it creates no
staging child and has no success edge to the acquisition graph.

Any other empty/malformed authority combination is exit-1
`invalid_acquisition_authority_contract`. Under a valid nonempty synthetic or
later provisioned authority, unlisted and revoked signer fixtures are exit-1
`acquisition_authorization_signature_invalid`, so they cannot be mistaken for
the stable unprovisioned `BLOCKED` state. P8T tests may use known Ed25519 keys
only in isolated temporary repositories. Production provisioning belongs to a
later separately reviewed, human-approved P8 preparation/evidence-ready
commit, whose initial provisioning has exactly one allowed-signers line and an
empty revoked-keys file. It must record the exact principal, public key, finite
validity, literal `revocation_state: active_not_revoked`, decoded-public-key
SHA-256, authority-file hashes/sizes, and private-key custody attestation
without committing private material. That transition alone authorizes no run,
dispatch, acquisition, policy change, matrix movement, or compatibility claim.

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
