# Evidence and status vocabulary

The support matrix records implementation and verification independently.
This prevents a complete-looking implementation from becoming a compatibility
claim without independent evidence.

## Implementation state

| State | Meaning |
| --- | --- |
| `not_started` | No production implementation is claimed. Scaffolding and test plans may exist. |
| `partial` | Some behavior exists, but the declared capability is incomplete or has known unsupported cases. |
| `implemented` | The declared behavior and documented limits are implemented. This says nothing about interoperability. |
| `out_of_scope_v1` | Intentionally unsupported in v1. It must not be silently accepted as supported. |

## Verification state

Except for `not_applicable`, the states form a cumulative evidence ladder in
the order shown. Advancing a state retains the artifacts required by every
earlier state. The required kind of evidence still depends on the capability.

| State | Meaning |
| --- | --- |
| `unverified` | No current evidence is recorded. |
| `internal_only` | Unit, property, golden, or round-trip tests pass using project code. Not an interoperability claim. |
| `independent_check` | A verifier independent of the writer, or an applicable external standard tool, accepts the artifact. Still not DAO compatibility. |
| `dao_opened` | In addition to applicable independent checks, the recorded DAO environment opened or validated the MDB, but full semantic equivalence was not shown. |
| `dao_differential` | DAO and Rust canonical semantic results agree for the required scenario set and operation, including preservation checks for updates. |
| `not_applicable` | External DAO evidence does not apply, normally for an explicitly out-of-scope item or a Rust-only safety property. A reason is required. |

`dao_opened` is deliberately weaker than `dao_differential`. Neither state may
be inferred from a file being accepted by the Rust reader. `internal_only` and
every higher state require at least one `test` artifact: it must be a test-only
Rust file, name stable scenario IDs from `tests/manifest.json`, map those IDs to
the file's Cargo target or unit-test module, and retain every manifested test
function in the commit-bound blob. Production modules are `source` evidence
even when they contain inline `#[cfg(test)]` code.

## User-facing labels

Tooling derives labels rather than storing them:

- **unsupported**: `out_of_scope_v1`;
- **planned**: `not_started`;
- **experimental**: `partial`, or `implemented` below its required verification
  state;
- **supported**: `implemented` and the entry's `required_verification` is met by
  current, commit-bound evidence.

The project must not use “DAO verified,” “Access compatible,” or equivalent
wording without an evidence bundle meeting the relevant definition above.

## Required evidence bundle

Each DAO run writes an immutable directory identified by git commit and run ID.
The bundle contains:

- scenario input JSON and its SHA-256;
- source MDB SHA-256, output MDB SHA-256, and canonical snapshot SHA-256;
- canonical DAO snapshot and canonical Rust snapshot;
- an operation log and machine-readable pass/fail report;
- git commit and dirty-worktree flag;
- Windows edition/build and architecture;
- DAO/Access/Jet provider identity and exact version;
- PowerShell/runtime version, locale, ANSI/OEM code pages, and time zone;
- oracle source revision and command line; and
- timestamps plus explicit skipped/unsupported cases.

Evidence from a dirty tree may guide development but cannot satisfy a release
gate. Test reports and fixture manifests must refer to stable scenario IDs; a
changed scenario receives a new content hash.

The support matrix may retain a source or test artifact from the immutable
commit where that exact blob entered the implementation. This is lineage
evidence: the validator resolves the recorded commit and verifies the blob
hash. It is not release evidence. An `independent_report`, `dao_bundle`, or
release-gate report must bind the exact clean release commit and cannot be
satisfied by an earlier lineage record.

## Detached release-evidence overlays

A detached overlay is an untrusted inventory presented to the checked
release-evidence validator. It binds its files, contracts, requested adapter,
expected output, and exact commit. Adapter artifact kinds and maximum
verification levels are intrinsic code properties; the checked policy may only
enable, disable, or forbid the closed adapter catalog and cannot relabel an
adapter.

`repository.dirty: false` means that the tracked index and worktree exactly
match the named `HEAD`. The policy permits only untracked acceptance outputs
under `artifacts/acceptance/**`, because the acceptance process itself retains
immutable results there. This is a repeated check of a caller-maintained
quiescent workspace, not a transactional lock against concurrent same-account
mutation.

Failed publication retains its uniquely created private stage for inspection
and never recursively deletes a pathname. Publication is presently available
only under a quiescent, current-account-owned POSIX parent with protected
ancestor permissions and atomic no-replace support. Windows publication fails
closed until equivalent trusted-parent handle and ACL enforcement exists.

The production policy currently enables no evidence adapters. Consequently,
the overlay validator and staging foundation cannot advance any support-matrix
verification state or substantiate a compatibility claim yet.

### P8T exact-commit amendment

For the P8T read-leg schema, the historical bundle description above is
specialized as follows. Its run ID is `campaign_id`; the positive-integer
hosted workflow identity is recorded separately as `hosted_run_id` and
`hosted_run_attempt`. Its source MDB SHA-256 and canonical artifact SHA-256s
are required according to the committed operation and expected outcome; the
read-only schema has no output MDB. Its canonical artifacts are DAO and Rust
success snapshots, or the exact structured Rust opening-failure artifact
required for an expected negative read.

This additive amendment implements detached exact-commit effective verification
without changing the committed support matrix. Acceptance selects exactly one
absolute overlay root and one expected lowercase DAO-manifest SHA-256; it
performs no search, download, newest selection, or fallback. The selected
`release-evidence.json`, its inventory, the exact raw
`dao-bundle/bundle-manifest.json`, every payload and executed source, and
current `HEAD` must bind the same clean full commit. Cleanliness is checked
before and after intrinsic validation, excluding only untracked
`artifacts/acceptance/**`.

`dao_differential_v1` is intrinsically available in P8T step 2 only for the
already-committed protocol-v1.2 `rust_read_dao` leg. The closed manifest and
report use **read-leg schema version 1** and admit exactly the operation enum
`["rust_read_dao"]`. `dao_open_rust`,
`dao_verify_rust_update`, and every write/update operation are rejected as
`unsupported_operation_for_schema_version`. They cannot be represented as a
passing expected error, ignored field, extension, or compatibility evidence.

Step 2 adds only:

- `dao-differential-v1-manifest.schema.json`,
  `dao-differential-v1-report.schema.json`, and
  `effective-support-result.schema.json`;
- `dao-provider-v1-contract.schema.json`,
  `dao-provider-proof-v1.schema.json`, the committed provider contract,
  `dao-differential-v1-source-closure.schema.json`, and its committed
  registry;
- protocol-v1.2 `coverage-receipt.schema.json` and
  `opening-failure.schema.json`; and
- the validators and focused tests needed to enforce these read contracts.

No expected-post-state, generic operation-failure, preservation-diff,
write-operation, output/update-MDB, writer/update source-closure, writer
producer, or writer command belongs to this schema version. A file, contract
key, manifest field, command role, or scenario operation for one of those
future concerns is an unexpected artifact and fails intrinsic validation.

The manifest is a closed object with exactly `schema_version`, `run`,
`git_commit`, `dirty`, `provider_proof`, `contracts`,
`executed_sources`, `commands`, `report`, `files`, and `scenarios`.
`schema_version` is integer 1, `git_commit` is one lowercase 40-hex commit,
and `dirty` is false. `run` has exactly string `campaign_id`,
positive-integer `hosted_run_id` and `hosted_run_attempt`, and
`started_at`/`completed_at`.

The thirteen-entry `contracts` object has exactly `manifest_schema`,
`report_schema`, `scenario_schema`, `scenario_inventory`,
`branch_registry`, `snapshot_schema`, `coverage_receipt_schema`,
`opening_failure_schema`, `provider_contract_schema`,
`provider_contract`, `provider_proof_schema`, `source_closure_schema`,
and `source_closure`. Each reference binds exact commit-relative path, raw
SHA-256, and size. Every contract is loaded from the release commit; no overlay
copy can substitute for it.

`files` is the unique, strictly normalized-POSIX-path-sorted complete
inventory of every regular non-manifest file beneath `dao-bundle/`, with
exact `path`, lowercase `sha256`, and nonnegative `size`. Symlinks,
special files, traversal, duplicates, unreferenced MDB/snapshot/failure/
coverage files, missing files, or hash/size drift fail. The manifest itself is
selected separately and its exact raw hash must equal the environment value,
overlay inventory value, and recomputation.

The provider proof is a canonical, inventory-bound closed JSON artifact. It
binds campaign, hosted run/attempt, exact commit, timestamp interval, hosted
image, x86 process, DAO COM ProgID/CLSID/registration, provider
path/version/hash, and disposable `dbVersion30` activation/create/close/file
observation to the committed provider contract. PASS facts never come from an
operation log or workflow prose. Freshness uses integer UTC seconds from the
selected committed image proof's `completed_at` to manifest
`run.started_at`: ages 0 through 604800 pass; 604801, negative age, or another
clock fails. Fresh provider proof, exact-commit human authorization,
pre-mutation boundary, retention, and redistribution rules remain mandatory.

Every command is a closed record binding id, role, registered `entrypoint_id`,
argv, exact source paths, source revision, start/completion timestamps, and
exit code. The adapter derives membership from the hash-bound committed
source-closure registry and requires exact equality among selected entrypoint
closures, command source paths, and `executed_sources`. Wrong indexed argv
entrypoint, missing or extra source, unused source, role mismatch, or CLI-only
substitution fails. P8, not P8T, adds the real PR-#92 Rust semantic-snapshot
producer and public `jet3::DatabaseReader` closure before acquisition; the
read-leg schema and validator can be tested at P8T's base without inventing a
nonexistent producer target.

Every timestamp uses uppercase UTC whole seconds and is calendar-valid. Run,
command, and scenario completion may equal start but never precede it; every
subordinate interval is within the inclusive run interval. Run identity and
commit agree exactly across manifest, provider proof, report, adapter output,
and effective result. Digit strings do not satisfy integer run fields.

Each manifest scenario is a closed object with exactly `id`,
`content_sha256`, `capability_ids`, `operation`, `scenario_input`,
`source_mdb`, `dao_snapshot`, `rust_snapshot`,
`rust_opening_failure`, `coverage_receipt`, `operation_log`,
`command_ids`, `started_at`, `completed_at`, `status`, and
`status_reason`. Arrays are nonempty, unique, and strictly UTF-8 sorted where
the schema declares them. `operation` is exactly `rust_read_dao` and must
equal the committed scenario inventory. `scenario_input`, `source_mdb`,
`coverage_receipt`, and `operation_log` are always non-null exact artifact
references. The input is the canonical complete committed scenario and its
recomputed content hash must agree. The operation log is diagnostic only.

A positive read requires non-null DAO and Rust complete success snapshots,
null `rust_opening_failure`, a `success` coverage receipt, report
`observed_outcome: success`, and scenario/report `status: PASS`. Each
complete snapshot is first independently validated against the committed
closed v1.2 snapshot schema, including producer, scenario, database identity,
ordering, typed values, raw bytes, and model integrity. The adapter then
requires `comparison_projection` to equal
`["/producer","/producer_extensions"]`, removes exactly those two top-level
members, canonicalizes the remaining full documents, and requires byte
equality. `raw_hex`, converted `value`, `raw_preservation`, ordering, and
all other non-extension model fields remain compared. Both original complete
documents remain independently raw-hash/size bound.

The three committed negative reads are `encrypted_database`,
`unsupported_version`, and `password_protected`. Each requires null DAO and
Rust snapshots, a non-null canonical `rust_opening_failure`, and matching
`opening_failure` coverage receipt. The opening artifact is a closed
seven-field object with exactly `protocol_version: "1.2.0"`,
`document_type: "rust_opening_failure"`, scenario id, release source
revision, source-MDB SHA-256, `outcome: "opening_failure"`, and the exact
committed error class. It is canonical UTF-8 JSON without BOM or non-finite
numbers, sorted compact keys, and exactly one trailing LF. It is never
validated as a success snapshot.

The failure artifact and coverage receipt must agree on scenario, revision,
source hash, outcome, and error class. The report has
`expected_outcome: expected_error`, `observed_outcome: error`, the exact
class, `status: PASS`, and null reason. A successful open, wrong class,
identity mismatch, missing pair, forbidden semantic snapshot, `SKIPPED`, or
`UNSUPPORTED` is intrinsic `FAIL`, never accepted negative coverage.

The coverage receipt is the closed PR-#92 shape with exactly
`protocol_version`, `document_type`, `scenario_id`, `source_revision`,
`database_sha256`, `allocated_set_sha256`, `outcome`, `error_class`,
and `branches`. Success has a non-null allocated-set digest and null error;
opening failure has null allocated-set digest, the committed class, and exactly
the schema-fixed ordered opening branches. Every receipt binds the source MDB,
release commit, scenario required branches, and forbidden branches; unknown,
missing, duplicate, extra, or reordered branches fail.

The report is a closed canonical object with exactly `schema_version`,
`campaign_id`, `hosted_run_id`, `hosted_run_attempt`, `git_commit`,
`dirty`, `manifest_sha256`, `scenarios`, and `status`.
`schema_version` is integer 1. Its scenarios contain exactly `id`,
`operation`, `expected_outcome`, `observed_outcome`, `error_class`,
`status`, and `reason`, sorted uniquely by id. The adapter recomputes the
report from committed inventory and validated artifacts; it never trusts
producer PASS prose. Only complete PASS read scenarios contribute evidence.

One adapter evidence output covers one capability's complete committed read
subset and has exactly `evidence_id`, `adapter`, `capability_id`,
`verification`, `commit`, `campaign_id`, `hosted_run_id`,
`hosted_run_attempt`, `manifest_sha256`, `scenario_ids`, and `status`.
It is emitted only after complete intrinsic PASS and exact equality with the
overlay item's expected output. Duplicate ids/capabilities, missing required
read scenarios, wrong intrinsic level, wrong run/commit/hash identity, or
unexpected operation fail.

The effective-support result is a closed object with exactly
`schema_version`, `campaign_id`, `hosted_run_id`,
`hosted_run_attempt`, `git_commit`, `dirty`, `overlay_sha256`,
`manifest_sha256`, `adapter_outputs`, `capabilities`, and `status`.
It is canonical UTF-8 JSON with sorted object keys, semantic array orders,
compact separators, direct non-ASCII, no non-finite values, and one trailing
LF. `overlay_sha256` hashes the exact raw selected `release-evidence.json`
bytes; `manifest_sha256` hashes the exact raw manifest bytes.

`capabilities` contains every committed matrix capability exactly once,
strictly UTF-8 sorted by id, not merely the adapter subset. Each closed entry
has exactly `id`, `stored_verification`, `effective_verification`, and
`evidence_ids`. Stored in-scope values are only `unverified` or
`internal_only`; out-of-scope is `not_applicable`. Effective verification
is the bounded maximum of stored baseline and at most one enabled passing
adapter output. Evidence ids contain detached adapter ids only. Disabled
intrinsic results never appear in outputs, evidence ids, stored state, or
effective advancement.

G3 derives its required set from every exact-commit matrix entry whose
`required_verification` is `dao_opened` or `dao_differential`; full-catalog
reporting remains mandatory for all other entries. A read-only P8 subset may
intrinsically pass its explicit allowlist, but full G3 remains `BLOCKED`
because schema version 1 deliberately has no write/update contracts or legs.
Missing future contracts are reported as
`future_write_update_contract_required`; they are never empty coverage,
`SKIPPED`, G3 PASS, or a compatibility claim.

Policy is evaluated only after the canonical path completes file-type,
inventory, raw hash/size, contract, provider, commit, cleanliness, schema,
intrinsic semantic, expected-output, and closing stability checks. Malformed,
unsafe, stale, dirty, mismatched, or intrinsically failing selected evidence is
`FAIL` with exit 1 even when policy is disabled. Only an intrinsic PASS may be
suppressed by the unchanged disabled policy to exit 3 and a schema-valid
`BLOCKED` result with no adapter output. Missing selection is rejected by the
G3 wrapper before a result is formed.

Before P10 implementation or acquisition, the **P10 exact write/update contract
gate** in `IMPLEMENTATION_PLAN.md` requires a separately human-approved exact
amendment/preregistration and manifest/adapter version extension. The read-leg
schema cannot be reused for write verification.

## Clean-room evidence ledger

`docs/PROVENANCE.md` is the required ledger for every technical source,
observed behavior, experiment, and fixture origin. An entry records:

- a stable ID, date, author, and public source citation or experiment protocol;
- what was learned, without importing implementation code;
- fixture/scenario IDs and hashes;
- environment and exact commands needed to reproduce the observation; and
- any license or redistribution restriction.

MDB Tools and other implementations may appear only as licensed black-box test
or performance oracles. Their code, derived pseudocode, implementation details,
and data structures are prohibited sources.
