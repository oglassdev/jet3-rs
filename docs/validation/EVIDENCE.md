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

This subsection supersedes the earlier implication that a release-only
`independent_report` or `dao_bundle`, or the verification level it supplies,
is stored in the committed support matrix. All other vocabulary, evidence
content, cleanliness, and fail-closed requirements above remain in force.

The committed `verification` field in `support-matrix.json` is the
repository-verifiable baseline, not the release result. It records only the
highest level justified without a detached release run. A release validator
derives `effective_verification` for each capability by joining that baseline
with the outputs of enabled intrinsic adapters from one selected detached
overlay. User-facing labels, gate decisions, and release claims use the
effective value. They must never treat an earlier acceptance result or a
detached file as though it had changed the committed matrix. A missing,
disabled, invalid, stale, or incomplete overlay contributes no level and
therefore cannot make a capability supported.

For an in-scope capability the committed baseline is therefore only
`unverified` or `internal_only`; an out-of-scope capability remains
`not_applicable`. Stored matrix evidence is only `source` or `test` lineage.
`independent_report` and `dao_bundle` are detached adapter inputs and are not
valid support-matrix evidence kinds after this amendment. The full verification
vocabulary remains because `independent_check`, `dao_opened`, and
`dao_differential` are valid derived effective results.

Full acceptance selects exactly one overlay root from the
`JET3_RELEASE_EVIDENCE` environment variable. The value must be one absolute
directory path outside the repository. There is no default directory, search,
newest-file rule, network lookup, committed pointer, or fallback. For a DAO
differential run, `JET3_RELEASE_EVIDENCE_MANIFEST_SHA256` must also be exactly
one lowercase SHA-256. The selected overlay must contain
`release-evidence.json` and exactly one file at
`dao-bundle/bundle-manifest.json`; the latter's recomputed hash must equal both
the environment value and its entry in the overlay's complete file inventory.
The acceptance record retains the selected absolute-path argument only in the
private command log, and retains the non-secret overlay SHA-256, DAO manifest
SHA-256, exact commit, adapter outputs, and effective capability results in
the hashed G3 stdout artifact.

The overlay and DAO manifest both name the release commit. The intrinsic
adapter requires those values, the current `HEAD`, every executed Rust/oracle
source binding, and every scenario result to be the same full commit and to
declare `dirty: false`. Before and after adapter execution, the validator
independently requires the tracked index and worktree to equal that `HEAD`;
only untracked `artifacts/acceptance/**` output is ignored. A dirty declaration,
dirty checkout, changed `HEAD`, changed contract, changed overlay, changed
payload tree, or commit mismatch fails closed. This is non-self-referential:
the clean commit contains the contracts and implementation, while the later
detached overlay supplies that already-frozen commit and manifest hash at
acceptance time. No future commit or bundle hash is embedded in the commit it
validates.

Preparation, acquisition, and historical recording are separate boundaries
(`EXP-0064`).

First, one reviewed clean pushed commit must already contain every policy,
allowlist, contract, implementation, executed-source binding, and acceptance
interface needed by the selected adapter. It contains no future run id,
overlay, bundle-manifest hash, or acceptance result. Human authorization names
that exact commit. Second, acquisition and acceptance run from a detached
clean checkout of it; the overlay root and manifest hash are supplied only at
run time, and the acceptance record binds the resulting detached inputs to
that commit. Third, a later additive provenance-only commit may record the
earlier commit, authorization, run, hashes, and result as history. That later
record is not part of, an input to, or evidence for the earlier commit, and an
acceptance result for the earlier commit cannot advance effective verification
at the later record commit's `HEAD`.

Provider proof and human authorization must complete before acquisition. A
failure after the first DAO mutation, or an uncertain failure, remains a
scientific event: retain and record it once and do not redispatch without the
required human decision and additive revision or new experiment.

`dao_differential_v1` owns the meaning and maximum level
`dao_differential`; policy cannot relabel it. Its manifest shape is fixed by
`docs/validation/schema/dao-differential-v1-manifest.schema.json`. The adapter
must parse and hash the following committed contracts from the named release
commit: that manifest schema,
`docs/validation/schema/dao-differential-v1-report.schema.json`,
`oracle/windows-dao/protocol/v1_2/scenarios.schema.json`,
`oracle/windows-dao/protocol/v1_2/scenarios.json`,
`oracle/windows-dao/protocol/v1_2/branch-registry.json`,
`oracle/windows-dao/protocol/v1_2/canonical-semantic-snapshot.schema.json`,
`oracle/windows-dao/protocol/v1_2/coverage-receipt.schema.json`, and
`oracle/windows-dao/protocol/v1_2/preservation-diff.schema.json`. The
manifest's `contracts` object contains exactly those eight path/SHA-256/size
bindings under the keys `manifest_schema`, `report_schema`,
`scenario_schema`, `scenario_inventory`, `branch_registry`, `snapshot_schema`,
`coverage_receipt_schema`, and `preservation_diff_schema`; a copied contract
blob cannot substitute for the blob at that path in the release commit.

The manifest is one closed canonical JSON object with exactly
`schema_version`, `run`, `git_commit`, `dirty`, `provider`, `contracts`,
`executed_sources`, `commands`, `report`, `files`, and `scenarios`.
`schema_version` is `1.0.0`; `git_commit` is one full lowercase 40-hex commit;
and `dirty` is `false`. `run` has exactly a nonempty `id`, UTC-second
`started_at` and `completed_at` values in `YYYY-MM-DDTHH:MM:SSZ` form, and the
hosted `run_id` and `attempt`, each a positive integer. The adapter proves that
completion follows start and that every subordinate timestamp lies within
that closed interval.

`provider` has exactly `prog_id`, `architecture`, `dll_file_version`,
`dll_sha256`, `windows`, `powershell`, `locale`, `ansi_code_page`,
`oem_code_page`, and `time_zone`. `prog_id`, `architecture`, provider version,
and provider hash must equal the pinned provider contract. `windows` has
exactly nonempty `edition`, `version`, and `build` strings plus `architecture`;
`powershell` has exactly nonempty `edition` and `version` strings; the two code
pages are positive integers; and all other scalar strings are nonempty.

`executed_sources` is a nonempty array strictly sorted by UTF-8 `path`, with
no duplicate path. Each entry has exactly `path`, `sha256`, `size`, and
`roles`; `path` is a normalized repository-relative POSIX path, `size` is a
nonnegative integer, and `roles` is a nonempty, unique, sorted subset of
`dao_producer`, `rust_producer`, `bundle_builder`, and
`independent_validator`. The adapter reads every path from `git_commit`,
recomputes its size and SHA-256, and rejects an absent, extra, dirty, or
worktree-sourced binding. `commands` is a nonempty array in execution order.
Each command has exactly a unique `id`, `source_revision`, `working_directory`,
`argv`, `source_paths`, `started_at`, `completed_at`, and `exit_code`.
`source_revision` equals `git_commit`; `argv` is a nonempty array of strings,
not a shell-reparsed string; `source_paths` is a nonempty unique sorted array
whose members all resolve to `executed_sources`; and advancement requires
every exit code to be zero. The adapter checks timestamps and the command/source
closure rather than trusting a producer's success field.

`files` is the complete inventory of every regular non-manifest file below
`dao-bundle/`, strictly sorted by normalized relative POSIX `path` and without
duplicates. Each entry has exactly `path`, lowercase `sha256`, and nonnegative
integer `size`. Paths are nonempty, contain no empty, `.` or `..` segment, are
not absolute, and use `/` only. Symlinks, hard-linked aliases, non-regular
files, unlisted payloads, listed-but-missing payloads, and case-folded path
collisions are rejected. Every artifact reference anywhere else in the
manifest has exactly the same `path`, `sha256`, and `size` as its one `files`
entry. `report` is one non-null artifact reference to canonical JSON validated
against the committed report schema. The report is a closed object with
exactly `schema_version`, `run_id`, `git_commit`, `dirty`, `started_at`,
`completed_at`, and `scenarios`. Its first six values equal the corresponding
manifest values. Its `scenarios` array is strictly id-sorted and unique; each
closed result has exactly `id`, `operation`, `expected_outcome`,
`observed_outcome`, `error_class`, `status`, and `reason`.
`expected_outcome` is `success` or `expected_error` and equals the committed
scenario; `observed_outcome` is `success`, `error`, `skipped`, or
`unsupported`; `error_class` is null exactly for observed success and is a
nonempty stable identifier otherwise; `status` uses the manifest's four
status literals; and `reason` has the same null/non-null rule as
`status_reason`. Each result must equal its manifest scenario identity,
operation, status, and reason. The adapter independently derives the observed
outcome and error class from the validated producer artifacts and operation
exit data, then requires canonical report-byte equality rather than trusting
the report.

`scenarios` is nonempty, strictly sorted by `id`, and unique by `id`. Every
entry has exactly `id`, `content_sha256`, `capability_ids`, `operation`,
`scenario_input`, `source_mdb`, `output_mdb`, `baseline_snapshot`,
`dao_snapshot`, `rust_snapshot`, `coverage_receipt`, `preservation_diff`,
`operation_log`, `command_ids`, `started_at`, `completed_at`, `status`, and
`status_reason`. Capability ids and command ids are nonempty unique sorted
arrays and must resolve to the committed matrix and top-level commands.
`operation` is exactly one of `rust_read_dao`, `dao_open_rust`, or
`dao_verify_rust_update` and must equal the committed scenario operation.
`scenario_input` and `operation_log` are always non-null artifact references;
the input is the canonical complete committed scenario object, and its
recomputed projection hash equals `content_sha256`. Scenario timestamps use
the run timestamp form and bounds. The operation log is a nonempty UTF-8 file
ending in one LF. It is retained and hash/size checked for diagnosis, but no
pass fact is trusted from its prose; the command records and independently
parsed artifacts supply those facts.

The schema represents diagnostic outcomes with `status` in `PASS`, `FAIL`,
`SKIPPED`, or `UNSUPPORTED`. `status_reason` is null only for `PASS` and is a
nonempty string otherwise. For `PASS`, artifact nullability is fixed by
operation: `rust_read_dao` requires `source_mdb`, both producer snapshots, and
the coverage receipt, while `output_mdb`, `baseline_snapshot`, and
`preservation_diff` are null; `dao_open_rust` requires `output_mdb`, both
snapshots, and the coverage receipt, while `source_mdb`, `baseline_snapshot`,
and `preservation_diff` are null; `dao_verify_rust_update` requires non-null
source and output MDBs, baseline and both post-update snapshots, coverage
receipt, and preservation diff. A non-`PASS` entry may leave an artifact that
was not produced null so the failed run remains diagnosable, but it can never
contribute an adapter output or effective verification. The report and
operation log remain mandatory even then. MDB artifact references bind their
complete bytes; the adapter rejects a same-file source/output alias for an
update.

Every complete DAO or Rust snapshot document is first independently parsed,
validated against the committed snapshot schema, and checked for its producer,
scenario, source revision, database identity, ordering, typed-value, raw-byte,
and model-integrity rules. Only after both complete documents pass does the
adapter require their declared `comparison_projection` to be exactly
`["/producer", "/producer_extensions"]`, remove exactly those two top-level
members from each in-memory document, serialize both projections with the
protocol's canonical UTF-8 JSON rules, and require projection-byte equality.
The complete source-document hashes remain bound by the manifest. No other
field is ignored: in particular `raw_hex`, converted `value`,
`raw_preservation`, ordering metadata, and every non-extension property remain
in the byte comparison.

The coverage receipt schema is the exact stabilized P8 shape: a closed object
with `protocol_version`, `document_type`, `scenario_id`, `source_revision`,
`database_sha256`, `allocated_set_sha256`, `outcome`, `error_class`, and
`branches`. A success receipt has `outcome: success`, null `error_class`, a
lowercase SHA-256 allocated-set digest, and a unique branch array; an opening
failure has `outcome: opening_failure`, null allocated-set digest, one of the
three registered opening error classes, and exactly the three ordered opening
branch ids fixed by that schema. The adapter additionally requires branches
to be strictly UTF-8 sorted, rejects every id outside the committed registry,
requires all scenario `required_branches` and none of its
`boundary.forbidden_branches`, and binds the database hash to `source_mdb` for
a read or to `output_mdb` for a write/update. Its source revision equals the
release commit. Missing receipt data is never inferred from the snapshot.

The preservation-diff schema is a closed canonical JSON object with exactly
`protocol_version`, `document_type`, `scenario_id`, `source_revision`,
`before_database_sha256`, `after_database_sha256`, `before_snapshot_sha256`,
`after_snapshot_sha256`, `preserve_paths`, `comparisons`, and `status`.
Its `protocol_version` is `1.2.0`, `document_type` is
`semantic_preservation_diff`, `scenario_id` names the update scenario, and
`source_revision` equals the release commit.
`preserve_paths` is the scenario's unique, strictly UTF-8-sorted JSON-pointer
array. `comparisons` has the same cardinality and order, and each closed entry
has exactly `path`, nullable `before_sha256`, nullable `after_sha256`, and
`outcome`, where `outcome` is `equal`, `different`, `missing_before`, or
`missing_after`; a hash is null exactly for its corresponding missing outcome.
The two non-null hashes are SHA-256s of the canonical JSON value selected by
that pointer. `status` is `PASS` exactly when every comparison is `equal` and
otherwise is `FAIL`. For an update, the adapter recomputes the whole report
from the complete DAO baseline and post-update documents, binds their full
artifact hashes and the source/output MDB hashes, and requires canonical
report-byte equality and `PASS`. Read and write legs supply no preservation
artifact. A read-only P8 subset may therefore pass its explicitly requested
capabilities, but full G3 remains `BLOCKED` on every absent required
write/update scenario; the adapter may not treat a missing future leg,
baseline, preservation report, or preserve path as an empty or skipped check.

After those shape checks, the adapter recomputes, rather than trusts, exact
manifest and payload closure; provider and clean-commit bindings; scenario
hashes, complete per-capability sets, operations and result statuses; every
artifact hash and size; snapshot projections; source/output MDB identities;
coverage; and update preservation.

One adapter evidence item covers one capability and names its complete
scenario subset. Its computed output is a closed object with exactly
`evidence_id`, `adapter`, `capability_id`, `verification`, `commit`,
`manifest_sha256`, `scenario_ids`, and `status`. `evidence_id` is the overlay
item's string matching
`^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$`; `capability_id` is a matrix-id
string with the same grammar; `adapter` is a string matching
`^[a-z][a-z0-9_]*_v[1-9][0-9]*$`; `verification` is exactly the adapter's
intrinsic string from `internal_only`, `independent_check`, `dao_opened`, or
`dao_differential`; `commit` is the full lowercase 40-hex release commit;
`manifest_sha256` is the lowercase SHA-256 of the exact raw
`dao-bundle/bundle-manifest.json` bytes; `scenario_ids` is the nonempty,
unique, strictly UTF-8-sorted complete subset derived from the committed
scenario inventory, with each string matching
`^(?:DAO-(?:GEN|READ|WRITE|UPDATE)|UT|IT|PROP|GOLD|CORR|REG)-[A-Z0-9][A-Z0-9_-]*$`;
and `status` is exactly `PASS`. The output exists only
after every applicable manifest, report, complete-snapshot, projection,
coverage, and preservation check above succeeds. The overlay's
`expected_output` must have exactly those fields and equal the computed object
after canonical JSON serialization. Duplicate evidence ids, duplicate
capability outputs, a capability absent from the committed matrix, a scenario
omitted from its required set, or a requested verification different from the
adapter's intrinsic level is rejected.

The `effective-support` result contract is frozen at
`docs/validation/schema/effective-support-result.schema.json`; step 2 must add
that schema and validate every emitted result against the copy in the exact
release commit. The result is a closed object with exactly `schema_version`,
`git_commit`, `dirty`, `overlay_sha256`, `manifest_sha256`,
`adapter_outputs`, `capabilities`, and `status`. `schema_version` is the JSON
integer `1` (a boolean is not an integer); `git_commit` is one full lowercase
40-hex commit and equals the validated overlay, manifest, and current clean
`HEAD`; `dirty` is exactly `false`; both hashes are lowercase 64-hex strings;
`adapter_outputs` and `capabilities` are arrays; and `status` is the string
`PASS` or `BLOCKED`. `manifest_sha256` has the exact raw-file
meaning above. `overlay_sha256` is SHA-256 over the exact bytes read from the
selected regular `release-evidence.json`, including its original JSON
whitespace and final-byte state; it is not a hash of a canonical
reserialization, an inventory, or the overlay directory.

`adapter_outputs` contains every and only computed `PASS` output from the
selected overlay. Its entries have the exact eight-field shape above, are
strictly UTF-8 sorted by `evidence_id`, and are unique by both `evidence_id`
and `capability_id`; every entry's `commit` and `manifest_sha256` equal the
top-level values. Thus one selected overlay cannot supply two adapter outputs
for one capability. A diagnostic `FAIL`, `SKIPPED`, or `UNSUPPORTED` scenario
is retained only in the bound manifest/report and never appears as an adapter
output.

`capabilities` is not an adapter subset. It contains every and only capability
in the exact committed `docs/validation/support-matrix.json` catalog, once
each, strictly sorted by UTF-8 `id` rather than by the matrix's presentation
order. Omission, duplication, an extra id, or order drift is an invalid result.
Each entry is a closed object with exactly `id`, `stored_verification`,
`effective_verification`, and `evidence_ids`. `id` uses the dotted lowercase
capability grammar. All three named scalar values are strings, and
`evidence_ids` is an array of strings using the evidence-id grammar above.
`stored_verification` equals that matrix entry and is one of `unverified`,
`internal_only`, or `not_applicable` under this amendment.
`effective_verification` uses the full matrix vocabulary: `unverified`,
`internal_only`, `independent_check`, `dao_opened`, `dao_differential`, or
`not_applicable`.

For an in-scope capability, the resolver orders the first five verification
values exactly as listed and sets `effective_verification` to the maximum of
the stored baseline and the one validated adapter output for that capability,
if present. `not_applicable` is outside that order: an out-of-scope capability
has both verification fields set to `not_applicable` and cannot have an
adapter output. `evidence_ids` contains detached overlay evidence identities
only, never stored source/test paths or scenario ids. It is `[]` when no
adapter output exists for the capability and otherwise is the one-element
array containing that output's `evidence_id`; the array is therefore always
unique and strictly UTF-8 sorted. The union of all nonempty `evidence_ids`
equals the `adapter_outputs` evidence-id set exactly.

The result status is `PASS` only when the complete G3 inventory and every
required operation are present, every selected adapter output is `PASS`, and
every applicable implemented capability meets its required effective level.
`BLOCKED` means that all selected bytes and every check that can safely run
are valid, but a non-evidence prerequisite is unavailable (including the
commit-bound policy disabling the otherwise available adapter) or the valid
adapter result is an explicitly incomplete subset such as P8's read-only
lane. For a disabled adapter, `adapter_outputs` is empty, every capability
retains its stored baseline, every `evidence_ids` array is empty, and the
result is `BLOCKED`; production policy therefore cannot advance effective
verification in step 2. A missing selection is rejected by the G3 wrapper
before a result object can be formed. Malformed JSON, unsafe paths or file
types, duplicate keys, non-finite numbers, resource-limit violations, dirty
state, closure/hash/commit/contract mismatch, or any failed executable
manifest, report, snapshot, projection, coverage, preservation, or adapter
check is `FAIL` with exit 1 and must not be represented as `BLOCKED` or as a
schema-conforming effective-support result.

Canonical result bytes are UTF-8 without a BOM, with object keys sorted by
Unicode code point, arrays left in the semantic orders fixed above, no
insignificant whitespace, non-ASCII characters emitted directly, JSON strings
escaped as required by the serializer, non-finite numbers forbidden, and
exactly one trailing LF after the top-level object. In implementation terms
this is the existing release-evidence canonicalizer: `ensure_ascii=False`,
`allow_nan=False`, `sort_keys=True`, and separators `,` and `:` followed by
`"\\n"`. The retained G3 stdout SHA-256 covers exactly those emitted canonical
result bytes, including that one LF.

The committed differential decision for this amendment is
`docs/plans/design-inputs/p8t-exact-commit-differential-decision.md`, SHA-256
`2fee5d73fae113c6f0833a38ce2c5af3a81447a7a826f96fa8522ccc768a7198`.
It selects end-to-end semantic traversal for capability advancement and keeps
checkpoint consequences as supplemental allocation stress evidence. The
production Rust library is the subject of the resulting capability evidence;
CLI commands are optional producers or diagnostics and never substitute for
the library path exercised by a manifested scenario.

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
