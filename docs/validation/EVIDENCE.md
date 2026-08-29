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

The complete literal add/modify path inventory for those categories, including
the round-8 acquisition-authorization schema, is frozen in
`IMPLEMENTATION_PLAN.md` under “P8T step-1 exact-commit decision and read-only
step-2 scope.” No path outside that closed list may change without a new
additive amendment merged and human-approved before implementation begins.

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

The following round-7 command/artifact binding is additive and supersedes any
looser interpretation of `command_ids`. A command id resolves once and only
once to one committed source-closure entry containing its exact role,
`entrypoint_id`, scenario subject, complete transitive source set, and release
commit. The command record must select that same entry, subject, and revision;
its harness-observed exit code is integer zero. An internal artifact `PASS` or
successful child process cannot override a nonzero harness result.

For a positive read, `command_ids` contains exactly one
`dao_source_snapshot_producer` and exactly one
`rust_semantic_snapshot_producer`. The DAO command produces the source MDB and
DAO snapshot. The Rust command produces the Rust snapshot and success coverage
receipt. The latter is the registered `jet3-testkit` producer over the public
`jet3::DatabaseReader` API and its complete transitive closure. A `jet3-cli`
entrypoint may be registered only as its optional driver; it neither replaces
the testkit/public-reader subject nor supplies another producer command.

For an expected opening failure, `command_ids` instead contains exactly one
`dao_source_producer` and exactly one
`rust_opening_rejection_coverage_producer`. The DAO command produces only the
source MDB; the Rust command performs the production public-reader rejection
and produces both the canonical opening-failure and opening-failure coverage
artifacts. DAO or Rust success-snapshot roles and artifacts, a CLI-only
producer, and every role not applicable to that outcome are forbidden.

Every non-null run-generated scenario artifact reference carries the exact
`producer_command_id` and production timestamp. The complete `files` entry
for that retained artifact carries the same linkage. The diagnostic operation
log, to which both commands contribute, carries the exact sorted two-command
producer-id set rather than an unbound or invented third producer. Committed
scenario input is explicitly non-produced and carries no producer id. No
hand-built artifact, inferred filename association, operation-log claim, or
producer metadata without this reference-to-command join can pass.

The same rule applies to every other retained/generated artifact reference in
the manifest and report when that artifact is created during the trusted run.
Only a committed contract/input or the independently acquired pre-run provider
proof may carry an explicit non-produced marker instead; applicability is
derived by the validator, not chosen by the producer. A null, absent, or
sentinel command id on an applicable generated artifact is unbound evidence.

The joined command, artifact reference, file entry, and embedded JSON producer
metadata must agree on artifact role, producer kind, source revision, scenario
id, and source-MDB SHA-256. Each production timestamp lies within its command's
inclusive interval; each command interval lies within both the scenario and
trusted run intervals. The validator derives the exact applicable-artifact
and command sets from the committed expected outcome, then requires bijective
coverage: no missing, duplicate, unrelated, wrong-role, wrong-entrypoint,
wrong-subject, stale, swapped, or otherwise unlinked producer command; every
listed scenario command produces or validates an applicable linked artifact,
and every applicable retained/generated artifact is linked.

Round-7 failures use these stable intrinsic reason codes:
`missing_required_producer_command`, `duplicate_producer_command`,
`wrong_producer_command_binding`, `swapped_artifact_producer_command`,
`unrelated_scenario_producer_command`, `producer_command_nonzero_exit`,
`stale_producer_command_revision`,
`producer_command_outside_trusted_run_interval`, and
`unbound_generated_artifact`. Identity or timestamp disagreement discovered
through an otherwise bound reference is `producer_command_artifact_mismatch`.
These are intrinsic `FAIL` before projection, comparison, policy, or adapter
output.

The following round-8 authorization binding supersedes only the version-1
manifest member and contract counts above. Step 2 adds the committed closed
schema
`docs/validation/schema/acquisition-authorization-v1.schema.json`. The
manifest therefore has exactly the prior eleven members plus
`acquisition_authorization`, and `contracts` has exactly the prior thirteen
members plus `acquisition_authorization_schema`. The manifest member is an
exact artifact reference to the fixed normalized path
`dao-bundle/acquisition-authorization.json`; the schema contract and artifact
reference each bind path, raw lowercase SHA-256, and size. The authorization
file is also a unique row in the complete `files` inventory, with the same
hash and size. It is a pre-run attestation with the same explicit non-produced
status as provider proof, never a generated scenario artifact.

The authorization is closed canonical UTF-8 JSON with sorted keys, compact
separators, direct non-ASCII, no BOM or non-finite values, and exactly one
trailing LF. It has exactly `schema_version`, `document_type`, `decision`,
`git_commit`, `approved_decision`, `actor`, `evidence_ready`,
`authorized_at`, `ordering_attestation`, `scope`, `retention`, and
`redistribution`. Version and document type are integer 1 and
`acquisition_authorization`; `decision` is exactly
`authorize_acquisition`. `approved_decision` has exactly stable `id`,
normalized commit-relative `path`, raw lowercase `sha256`, and `size`; the
adapter loads that file from the selected release commit and recomputes both
values. `actor` has exactly a nonempty stable `identity` and
`authority: "human_release_authority"`.

`evidence_ready` has exactly `confirmed_at`, `clean: true`, and
`pushed: true`. It attests that the authorization actor observed the named
already-existing commit as the reviewed clean pushed evidence-ready commit;
it is not evidence for repository cleanliness, which the adapter still checks
twice. `ordering_attestation` is exactly
`authorized_before_any_dao_mutation`. The adapter requires
`evidence_ready.confirmed_at <= authorized_at < run.started_at` and requires
every acquisition command interval to remain inside the run interval. Thus
authorization precedes even command launch, a stronger checked boundary than
preceding the first DAO mutation. All three authorization times are
calendar-valid uppercase UTC whole seconds.

`scope` has exactly `campaign_id`, `adapter`, `manifest_schema_version`,
`operation`, `scenario_ids`, `provider_contract`, `hosted_image`,
`maximum_dispatches`, and `maximum_attempts`. The adapter and operation are
exactly `dao_differential_v1` and `rust_read_dao`; schema version,
maximum dispatches, and maximum attempts are each integer 1. Campaign,
strictly sorted unique scenario set, provider-contract path/hash/size, and
hosted image must equal the manifest, committed scenario/contract selection,
and provider proof. A bundle cannot broaden the authorized scope, and a
second dispatch or attempt requires a new authorization record and the
scientific-event decision required elsewhere.

`retention` has exactly
`licensed_provider_bytes: "not_retained"`,
`acquisition_bundle: "private_read_only"`, and
`authorization_record: "retained_in_bundle"`. `redistribution` has exactly
`licensed_provider_bytes: "prohibited"`,
`mdb_and_payload_bytes: "prohibited"`, and
`public_record: "metadata_and_hashes_only"`. These are mandatory
attestations, not producer-selected policy labels.

The record intentionally contains no overlay hash, manifest hash, hosted run
id/attempt, run timestamps, or result: those identities do not exist when the
human acts. The later closed manifest binds the pre-run record by raw hash and
size and supplies those later identities without making the evidence-ready
commit self-referential. Missing authorization is
`missing_acquisition_authorization`; malformed/noncanonical bytes, a wrong
decision or actor/authority, a forbidden future-identity member, or schema
failure is `invalid_acquisition_authorization`; commit, campaign, approved
decision, or artifact/contract hash-size disagreement is
`acquisition_authorization_binding_mismatch`; time or pre-run ordering failure
is `acquisition_authorization_ordering_violation`; broadened acquisition scope
is `acquisition_authorization_scope_mismatch`; and wrong retention or
redistribution attestations are `acquisition_authorization_rights_mismatch`.
Each is intrinsic exit-1 `FAIL` before provider, commands, scenario artifacts,
comparison, policy, or adapter output.

The following round-9 authenticated-authorization binding supersedes only the
round-8 authorization record shape, its manifest reference shape, its contract
count, and the assertion that hosted-run identity cannot exist when the human
acts. It retains the exact-commit, approved-decision, scope, ordering,
retention, redistribution, and fail-before-policy requirements above. A
GitHub environment review is an execution gate, but its UI/API response is not
used as a portable signed human-approval receipt. Human origin is instead
authenticated by an OpenSSH detached signature whose public authority is part
of the already-existing evidence-ready commit.

Step 2 additionally adds the exact commit-bound contracts
`docs/validation/acquisition-authority-v1.allowed_signers` and
`docs/validation/acquisition-authority-v1.revoked_keys`. The former is a
nonempty, at most 65,536-byte, at most 32-line, strictly principal-sorted ASCII
OpenSSH allowed-signers file with exactly one principal per line. Principals
match `[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}`. Comments, blank lines, wildcard or
duplicate principals, certificate-authority entries, and key types other than
`ssh-ed25519` are forbidden. Every line's options appear in exactly this order:
`namespaces="jet3-rs-acquisition-v1@oglassdev",valid-after="<UTC>",valid-before="<UTC>"`,
with finite uppercase-UTC whole-second bounds and `valid-after < valid-before`.
The revocation contract is an at-most-65,536-byte, at-most-64-line ASCII
OpenSSH one-Ed25519-public-key-per-line file, possibly empty, with no comments,
duplicates, options, or private material. Both files have LF endings and no
BOM, NUL, or trailing blank line. Both are loaded only from the exact release
commit and bound by path, raw SHA-256, and size as contracts
`acquisition_authority` and `acquisition_authority_revocations`. The closed
manifest therefore has the same twelve top-level members fixed by round 8,
while its `contracts` object has exactly sixteen members: the prior fourteen
plus these two authority contracts.

`acquisition_authorization` is now a closed object containing exactly
`document`, `signature`, and `verification_command_id`. `document` is the
round-9 exact artifact reference to
`dao-bundle/acquisition-authorization.json`; `signature` is an exact artifact
reference to
`dao-bundle/acquisition-authorization.json.sig`; and both are unique rows in
the complete `files` inventory with equal path/hash/size linkage. The ASCII
armored SSHSIG is at most 16,384 bytes, has no BOM or NUL, and ends in exactly
one LF. `verification_command_id` resolves to exactly one non-scenario command
with role `acquisition_authorization_verifier`, the registered step-2
validator entrypoint and source closure, zero harness exit, and the same
document/signature hashes. No producer or bundle assertion can substitute for
the adapter's independent signature verification.

The canonical JSON document now has exactly `schema_version`,
`document_type`, `decision`, `authorization_nonce`, `repository`, `workflow`,
`hosted_run`, `git_commit`, `approved_decision`, `actor`, `evidence_ready`,
`authorized_at`, `ordering_attestation`, `scope`, `retention`, and
`redistribution`. Existing round-8 members keep their meanings except that
`actor` has exactly `principal` and
`authority: "human_release_authority"`. `authorization_nonce` is 64 lowercase
hex characters supplied as the dispatch input. `repository` is exactly
`oglassdev/jet3-rs`. `workflow` has exactly normalized commit-relative `path`,
full `ref`, 40-lowercase-hex `sha`, stable YAML `job`, and protected
`environment`; its path, job, and environment are exactly
`.github/workflows/windows-dao-p8-read.yml`, `acquire_read_evidence`, and
`jet3-dao-acquisition`, and `sha` equals `git_commit`. `hosted_run` has exactly
positive-integer `id` and `attempt` plus calendar-valid UTC-whole-second
`created_at`. These values name the already-created blocked run, not a future
manifest, overlay, result, or acquisition artifact.

The manifest `run` object is correspondingly extended with exactly
`authorization_nonce`, `repository`, `workflow_path`, `workflow_ref`,
`workflow_sha`, `acquisition_job`, `environment`, and `created_at`, in addition
to its round-8 fields. Every value must equal the signed document and the
trusted hosted-run identity retained by the acquisition controller;
`workflow_sha` equals the manifest commit and current clean `HEAD`. The
authorization principal is not accepted merely because the JSON names it:
the adapter passes the exact canonical document bytes on standard input to

```sh
ssh-keygen -Y verify \
  -f docs/validation/acquisition-authority-v1.allowed_signers \
  -r docs/validation/acquisition-authority-v1.revoked_keys \
  -I <exact-signed-principal> \
  -n jet3-rs-acquisition-v1@oglassdev \
  -O verify-time=<authorized_at-as-YYYYMMDDHHMMSSZ> \
  -s dao-bundle/acquisition-authorization.json.sig
```

Only exit zero is authenticated approval. The validator supplies every
argument without a shell, verifies the authority files' closed grammar first,
and accepts the principal only through the exact allowed-signers match at the
signed time and the exact non-revoked Ed25519 key. A changed actor, authority,
run, attempt, nonce, repository, workflow, commit, time, or scope changes the
signed bytes; copying the old signature therefore fails. A signature made by
an unlisted or revoked key also fails.

The dispatch sequence is exact. P8's later acquisition preparation commit,
not P8T step 2, must freeze its workflow and environment configuration. A
human generates a fresh nonce, dispatches that exact commit once, and the
acquisition job waits on the protected environment before a runner can execute
any repository or DAO command. After observing repository, workflow, run id,
attempt, commit, job, environment, and nonce, the human signs the canonical
record off-run with `ssh-keygen -Y sign -f <authority-private-key> -n
jet3-rs-acquisition-v1@oglassdev acquisition-authorization.json` and supplies
the record and signature as environment secrets. The private key may be
hardware/agent backed but cannot be exportable to the runner.
Approval releases that already-created job; environment secrets are then
available, and its first repository-controlled command must be the registered
authorization verifier. The command starts no earlier than `authorized_at`,
finishes before every provider/acquisition command starts, and is retained in
the manifest. The adapter re-verifies the signature and requires
`evidence_ready.confirmed_at <= created_at <= authorized_at < run.started_at`,
the verification command wholly inside the run, and its completion strictly
before every DAO or Rust acquisition command. This authenticated signed-time
and exact committed command order prove the authorization existed before any
acquisition command; `authorized_before_any_dao_mutation` remains the exact
ordering attestation.

The exact run id, attempt, nonce, repository, workflow, commit, job,
environment, campaign, and scenario scope make one authorization usable for
one dispatch attempt only. A rerun has a different attempt and a new dispatch
has a different run id and nonce, so either requires a new signed record.
Copying a valid record/signature to another run, attempt, nonce, repository,
workflow, job, environment, commit, or campaign is a binding failure before
provider or scenario validation. The private signing key never enters the
repository, GitHub secrets, runner, overlay, or retained bundle.

Key validity and revocation are explicit. Rotation adds the successor key and
finite validity interval in a new reviewed evidence-ready commit. Revocation
adds the public key to that commit's revocation file and removes it from
allowed signers. A revocation learned after a run is created invalidates every
not-yet-acquired run under the old commit: cancel it and prepare a new commit;
completed evidence remains historically bound to the authority contract that
was effective at its signed time, unless an additive provenance/policy
decision quarantines it after compromise. The offline validator never fetches
or silently substitutes newer key material.

Round-9 adds stable intrinsic reason codes
`missing_acquisition_authorization_signature`,
`invalid_acquisition_authority_contract`,
`acquisition_authorization_signature_invalid`,
`acquisition_authorization_run_binding_mismatch`, and
`acquisition_authorization_verification_command_mismatch`; the round-8 codes
remain for their unchanged classes. These failures exit 1 and form zero
adapter output. If the exact `ssh-keygen -Y verify` capability is absent,
`acquisition_authorization_verifier_unavailable` is exit-3 `BLOCKED`, never
PASS. Forged actor or attacker-key signatures use
`acquisition_authorization_signature_invalid`; replay across a run, attempt,
or nonce uses `acquisition_authorization_run_binding_mismatch`.

The following round-9 committed-read-allowlist binding supersedes only prior
new-schema and manifest-contract counts and adds exactly
`docs/validation/dao-differential-v1-read-allowlist.json` and
`docs/validation/schema/dao-differential-v1-read-allowlist.schema.json` to the
step-2 literal inventory. It changes no matrix, policy, overlay schema,
workflow, acquisition, Rust, testkit, CLI, write/update, or P10 path.

The allowlist is a closed canonical document with exactly integer
`schema_version: 1`, string
`document_type: "dao_differential_v1_read_allowlist"`, and `capabilities`.
Each capability entry has exactly `capability_id` and `scenarios`; each
scenario entry has exactly `scenario_id` and nonempty `branch_ids`.
Capability entries, scenario entries, and branch ids are unique and strictly
UTF-8 sorted by their literal ids. The document is at most 65,536 bytes,
canonical UTF-8 JSON with sorted keys, compact separators, direct non-ASCII,
integer-only numbers, no BOM or NUL, and exactly one trailing LF. Identifiers
are exact catalog keys, never patterns. Any id containing `*`, `?`, `[`, `]`,
`{`, `}`, `(`, `)`, `|`, `^`, `$`, backslash, or whitespace, or equal to
case-insensitive `all`, is rejected; no glob or regex engine is invoked.

The manifest binds the schema and document as exact contracts
`read_allowlist_schema` and `read_allowlist`, respectively, using their fixed
commit-relative paths, raw SHA-256, and size. They are loaded from the exact
clean release commit, not from the overlay. Together with the round-9
authority contracts, the closed `contracts` object therefore has exactly
eighteen members: the prior sixteen plus these two.

The step-2 committed document has `capabilities: []`. Empty is valid but
authorizes nothing. After all otherwise applicable intrinsic selected-evidence
checks succeed, it returns exit 3 `BLOCKED`, reason `read_allowlist_empty`, and
zero adapter outputs before policy. Intrinsic malformed or mismatched selected
evidence still exits 1 first. Synthetic complete fixtures may use a nonempty
document solely to exercise validation. P8 step 4, not P8T, may populate the
real file only in a new reviewed, human-approved, clean pushed evidence-ready
commit; it may name only facts already committed in that same tree and cannot
name future run, overlay, manifest, output, or evidence identities. The later
manifest's raw binding is consequently not self-referential, and the
allowlist edit alone advances no capability.

The exact step-2 empty document is 92 bytes with raw SHA-256
`5bf2d681e7368c0d96493f0e33d9e7a7a822fe9ab6318ae38f594d7642d003ae`.

For each entry in a nonempty allowlist, `capability_id` is one literal P8
read-advancement candidate, exists in the committed full support catalog, and
is `implemented` at the release commit. Its scenario-id set equals exactly all
complete committed protocol-v1.2 `rust_read_dao` scenarios that name the
capability in `capability_ids`; omission and addition both fail. Each selected
scenario is produced through the registered public `jet3::DatabaseReader`
library/testkit subject closure, never a CLI-only subject. Its allowlisted
branch set is registry-known, includes every required branch, excludes every
forbidden branch, and equals the retained coverage receipt's observed branch
set exactly. A scenario repeated under multiple capabilities has one identical
branch set in every entry.

The manifest and report scenario-id set equals the union of all allowlisted
scenario ids. Adapter outputs form a subset only of the full support catalog;
within the read allowlist they require exact equality: exactly one output for
each allowlisted capability, no other output, and exact equality between each
output's `scenario_ids` and that entry's scenario ids. The overlay expected
output remains byte-exact with the independently recomputed adapter output.
An intersection, proper subset, superset, duplicate, missing entry, or extra
entry is not allowlist satisfaction.

Stable failures are `invalid_read_allowlist` for schema, canonical bytes,
ordering, uniqueness, literal-id, or wildcard failure;
`read_allowlist_contract_mismatch` for fixed-path/hash/size/reference failure;
`read_allowlist_membership_mismatch` for capability eligibility or
implementation, scenario closure, library subject, branch registry/required/
forbidden scope, repeated-scenario disagreement, or observed-branch
inequality; and `read_allowlist_adapter_output_mismatch` for output-set or
per-capability scenario-set inequality. Each exits 1 `FAIL` with zero adapter
output before policy. The exact focused accept/reject commands and fixture
semantics are frozen in `IMPLEMENTATION_PLAN.md`.

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

Before validating any scenario artifact, consulting policy, or emitting any
adapter evidence output, the intrinsic adapter must validate the release
commit's protocol-v1.2 `scenarios.json` with the same complete semantics as
`validate_document_path(..., complete=True)`, equivalently:

```sh
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory \
  oracle/windows-dao/protocol/v1_2/scenarios.json --complete
```

Any `deferred_requirements` entry is intrinsic `FAIL` with reason code
`incomplete_scenario_inventory_deferred_requirements`; policy cannot suppress
it and no adapter output is formed. P8T step 2 may implement and test this
path only with an isolated repository fixture whose inventory passes complete
mode. The real P8 lane remains `BLOCKED` until the committed inventory has no
deferred requirement and passes the exact command above.

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

The producer members excluded from comparison remain binding identity fields.
For every positive pair, the DAO snapshot must have `producer.kind: "dao"`,
the Rust snapshot must have `producer.kind: "rust"`, and each snapshot's
`producer.source_revision` must equal both manifest `git_commit` and the
current clean `HEAD`. These checks occur before projection; stale DAO or Rust
revisions and swapped or otherwise wrong producer kinds are intrinsic `FAIL`.

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

For coverage membership, this paragraph supersedes only the preceding
extra-branch rejection: observed branches form a subset of the frozen v1.2
branch registry, contain every scenario `required_branches` entry, and are
disjoint from the scenario boundary's `forbidden_branches`. Additional
registered, non-forbidden observed branches are valid. An unregistered or
forbidden observed branch remains intrinsic `FAIL`; the pinned coverage schema
and its uniqueness and ordering constraints remain byte-for-byte unchanged.

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

The following round-10 acyclic-report amendment supersedes only the report
field `manifest_sha256` and any implication that a manifest-inventoried report
can contain the raw hash of the manifest that inventories it. The read-leg
version remains 1. The closed report instead has exactly `schema_version`,
`campaign_id`, `hosted_run_id`, `hosted_run_attempt`, `git_commit`, `dirty`,
`manifest_projection_sha256`, `scenarios`, and `status`. The replacement hash
is lowercase 64-hex and the closed schema rejects the former
`manifest_sha256` member. It binds the stable manifest identity and all
non-report manifest content without creating a raw hash cycle. `campaign_id`,
`hosted_run_id`, `hosted_run_attempt`, `git_commit`, and `dirty` still equal
the manifest exactly.

The manifest projection is computed from the parsed final manifest as follows.
First require `report` to be one closed artifact reference and save its
normalized `path`. Deep-copy the manifest, delete the top-level `report`
member, and remove exactly one `files` entry whose `path` equals the saved
report path. Zero or multiple matching entries fail normal report-reference or
inventory validation. Preserve every other value and every array order. Encode
that projection as UTF-8 JSON with no BOM, recursively sorted object keys,
compact `,` and `:` separators, direct non-ASCII characters, no non-finite
numbers, and exactly one trailing LF. `manifest_projection_sha256` is SHA-256
of those exact bytes. The projection is an in-memory hash domain, not a
retained artifact and not a second manifest schema.

Creation is therefore acyclic: finalize the trusted run and every non-report
payload and manifest record; construct and hash the report-free manifest
projection; create and canonicalize the report with that projection digest;
hash the report and add its exact `report` reference plus its one matching
complete-`files` row; then canonicalize and hash the full manifest and publish
that raw hash through the selected overlay. The full manifest continues to
inventory and raw-hash every non-manifest payload, including the report.

Validation first binds the selected raw manifest hash, validates the manifest
schema and complete raw file inventory including the report, and validates the
report schema. It then recomputes the projection from the full manifest and
compares the digest before report semantic recomputation, command/scenario
joins, policy, or output. A digest mismatch is intrinsic exit-1 `FAIL` with
stable reason `report_manifest_projection_mismatch` and zero adapter output.
Adapter evidence outputs and the effective-support result retain their raw
`manifest_sha256`: they are formed only after the manifest is final and are
outside the manifest's `dao-bundle/` payload inventory, so they do not
participate in the cycle. Only
`docs/validation/schema/dao-differential-v1-report.schema.json` changes shape;
the manifest and effective-support schemas, exact overlay selection,
complete-inventory/raw-hash rules, exact-commit bindings, disabled policy, and
read-only operation remain unchanged.

The following round-10 pre-acquisition-bootstrap amendment supersedes only the
implication that `effective-support --repo-root --overlay
--manifest-sha256` can perform the first authorization check. At that boundary
neither overlay nor manifest exists: they can be finalized only after the
authorized acquisition. Step 2 therefore adds `authorization-preflight` as a
second subcommand of the same thin `tools/validate_release_evidence.py`
entrypoint. It accepts no overlay or manifest argument, emits no adapter or
effective-support result, and performs no provider, DAO, Rust, network, or
acquisition operation. The existing `effective-support` subcommand remains the
only later detached-evidence resolver.

The preflight's process environment contains the signed pair only as strict
standard Base64 in the fixed variables
`JET3_ACQUISITION_AUTHORIZATION_JSON_B64` and
`JET3_ACQUISITION_AUTHORIZATION_SSHSIG_B64`. They are environment secrets, not
paths. No value or decoded byte may enter argv, standard input of the
preflight process, stdout, stderr, an exception, or a subprocess environment.
The preflight deletes both variables from its process environment immediately
after bounded reads and gives `ssh-keygen` a newly constructed allowlisted
environment that contains neither variable. Its argv is exactly:

```text
["python3", "-B", "tools/validate_release_evidence.py",
 "authorization-preflight", "--repo-root", ABSOLUTE_CLEAN_WORKTREE,
 "--private-staging-parent", ABSOLUTE_PRIVATE_PARENT]
```

The workflow invokes that argv without interpolation of either secret. The
preflight reads the trusted controller values `GITHUB_REPOSITORY`,
`GITHUB_WORKFLOW_REF`, `GITHUB_SHA`, `GITHUB_RUN_ID`,
`GITHUB_RUN_ATTEMPT`, `GITHUB_JOB`, `JET3_AUTHORIZATION_NONCE`, and
`JET3_CAMPAIGN_ID` directly from its environment and compares them with the
closed signed document, approved decision and allowlist, the fixed workflow
path/job/environment, and clean `HEAD`; it accepts no command-line override for
any binding. In particular, `GITHUB_WORKFLOW_REF` must equal
`<repository>/<signed-workflow-path>@<signed-workflow-ref>`, `GITHUB_JOB` must
equal `acquire_read_evidence`, and the signed environment must equal
`jet3-dao-acquisition`. Missing, empty, non-ASCII, whitespace-containing,
noncanonical-padded, or otherwise invalid standard Base64 fails before any
filesystem creation. Decoded JSON is limited to 24,576 bytes and decoded
SSHSIG to 16,384 bytes; the encoded values are rejected before decoding above
32,768 and 21,848 ASCII bytes respectively. Re-encoding each decoded value
must reproduce the environment string exactly. The JSON bytes must then pass
the round-9 closed schema and exact canonical-byte check, and the process UTC
time must be no earlier than signed `authorized_at`. The preflight reads the
two authority contracts as bounded blobs from exact clean `HEAD`, validates
their closed grammar, and writes byte-identical private temporary copies
inside the exclusive child; it never trusts a mutable overlay or alternate
authority path. The no-shell verifier invocation is
`subprocess.run(..., shell=False)` with exactly the round-9 arguments except
that `-f` and `-r` name those two exact temporary commit-blob copies and `-s`
names the unpublished no-follow SSHSIG path. The verifier receives only those
decoded JSON bytes on its standard input. Its stdout and stderr are both
connected directly to the null device; they are never captured, buffered,
parsed, or forwarded. Status output is one fixed
`<PASS|FAIL|ERROR|BLOCKED>: reason` line and never includes a secret, decoded
bytes, a hash, a path, a principal, or verifier output.

`ABSOLUTE_PRIVATE_PARENT` is an existing controller-created, per-job private
directory. Both arguments must be absolute and lexically normalized. Every
existing component from the filesystem anchor through the parent is inspected
without following the final component and must be a real directory, never a
symbolic link or Windows reparse point; normalized real path and
case-normalized identity must equal the supplied path. On POSIX the parent
must be owned by the process and grant no group/other permission. On Windows
the parent must be the exact normalized `RUNNER_TEMP` directory and relies on
its controller-provisioned per-job ACL; the Python implementation does not
claim that POSIX mode bits audit a Windows ACL. From the already-validated
signed run id and attempt, the preflight derives the single child name
`jet3-dao-bundle-<run-id>-<attempt>`. That target must not exist under any
spelling, and creation uses an exclusive operation; an existing file,
directory, link, junction/reparse point, normalized alias, or case alias is a
hard failure, never a reusable staging area. The case/alias scan is bounded to
4,096 parent entries; exceeding the bound is unsafe staging rather than
unbounded work.

After all in-memory, repository, authority-contract, run-binding, and verifier-
availability checks pass, the preflight exclusively creates the child with
mode 0700 and an unpublished `.authorization.tmp` directory with mode 0700.
It opens
`acquisition-authorization.json` and
`acquisition-authorization.json.sig` with create-exclusive/no-follow semantics
and mode 0600, writes with checked short-write loops, flushes and file-fsyncs,
re-reads them through bounded regular-file/no-follow handles, and requires
byte-for-byte identity with the two decoded values. It similarly writes and
fsyncs the exact commit-blob authority copies as `.authority.allowed_signers`
and `.authority.revoked_keys` with mode 0400. It invokes the exact
commit-bound `ssh-keygen -Y verify` command frozen above, with the temporary
document bytes on standard input, only after those checks. Nonzero verification
publishes nothing. On zero, it deletes the two public authority copies,
fsyncs the temporary directory where supported, and requires its entry set to
equal exactly the document and signature. It then atomically renames the
complete temporary directory to `<child>/dao-bundle`, retaining directory mode
0700. It sets both files to mode 0400 on POSIX and the read-only attribute
under the inherited private parent ACL on Windows, without changing their
bytes, flushes the containing directory when directory fsync is supported, and
performs a final bounded no-follow re-read and byte-identity check. File fsync
and same-filesystem atomic rename are mandatory on every platform; unsupported
directory fsync on Windows is recorded internally but is not misreported as
file durability.

Only after the final re-read does preflight exit 0 with
`PASS: acquisition_authorization_preflight_passed`. Those exact authenticated
bytes remain at the two fixed `dao-bundle/` paths. Later acquisition may add
new files only with no-replace operations and may never open either retained
file for writing. Manifest construction hashes and sizes those same path
objects; later `effective-support` independently re-reads and verifies the
same raw bytes and SSHSIG. Copying, recanonicalizing, decoding again, or
substituting a semantically equal JSON document is forbidden.

Every failure before publication removes only the exclusively created child;
every failure after rename first removes the published `dao-bundle` and then
that child. Cleanup walks the closed allowlist `.authorization.tmp`,
`dao-bundle`, `.authority.allowed_signers`, `.authority.revoked_keys`,
`acquisition-authorization.json`, and `acquisition-authorization.json.sig`;
it uses no caller-supplied descendant and refuses any unexpected entry, link,
or reparse point. It may clear the read-only state only on those four exact
files immediately before deletion. If absence cannot be proved, exit 2
`ERROR: acquisition_authorization_cleanup_failed`; the job must still stop and
its controller finalizer removes the exact child before any retry. Other
materialization/fsync/rename failures are exit 2
`ERROR: acquisition_authorization_materialization_failed`. Missing, malformed,
and oversized secret transport are exit-1 `FAIL` with, respectively,
`missing_acquisition_authorization_secret`,
`malformed_acquisition_authorization_secret`, and
`oversized_acquisition_authorization_secret`. Unsafe/aliased staging and an
existing target are exit-1 `FAIL` with
`unsafe_acquisition_authorization_staging_path` and
`acquisition_authorization_staging_target_exists`. A changed temporary or
retained byte is exit-1 `FAIL` with
`acquisition_authorization_retained_bytes_mismatch`; nonzero `ssh-keygen`
remains exit-1 `acquisition_authorization_signature_invalid`; and unavailable
exact verify capability remains exit-3 `BLOCKED` with
`acquisition_authorization_verifier_unavailable`. Existing schema, authority,
run-binding, and ordering reason codes retain their round-9 meanings. Every
nonzero exit publishes no usable authorization and makes every provider, DAO,
Rust, or acquisition command unreachable.

The new implementation module is
`tools/validation/acquisition_authorization.py`; separating bounded secret
handling and transactional staging keeps the existing resolver and intrinsic
adapter focused and below the production-file size limit. The source-closure
registry adds one global, non-scenario
`acquisition_authorization_verifier` entry for the exact
`authorization-preflight` argv. It must enumerate the thin entrypoint, this
module, and every actually imported repository module transitively, with no
glob, working-tree discovery, optional application CLI, workflow helper, or
unregistered executable. Its external executable set is exactly `git` for the
bounded clean-HEAD/commit-blob checks and the already-frozen `ssh-keygen -Y
verify`; Python and its standard-library modules are identified as runtime
dependencies, not repository sources. The later manifest command
record binds this exact entrypoint/source closure, the two retained raw hashes,
zero exit, and the pre-acquisition interval. Bootstrap and materialization are
non-acquisition bookkeeping and must finish successfully before that record or
any provider/acquisition record can exist.

The following round-11 timing-boundary amendment supersedes only the round-10
permission for preflight process time to equal signed `authorized_at`. The
registered `authorization-preflight` command captures one calendar-valid
uppercase UTC whole second at command entry and retains it as
`preflight_started_at`; it does not resample or round a later instant to make
the check pass. The empty-authority sentinel and, for a nonempty authority,
the provisioned-authority grammar gate retain their existing precedence. After
those gates but before staging creation, verifier invocation, publication, or
any acquisition edge, preflight requires
`authorized_at < preflight_started_at`. Its eventual manifest command
record must bind that exact retained value as both `run.started_at` and the
registered authorization-verifier command's `started_at`; therefore the later
resolver requires
`authorized_at < run.started_at == authorization-preflight.started_at`.
Verifier completion remains no earlier than that start and strictly before
the first provider, DAO, Rust, or other acquisition command.

An entry second equal to or earlier than `authorized_at` exits 1 and prints
exactly `FAIL: acquisition_authorization_ordering_violation`. In particular,
the same-second case creates no staging child, invokes no `ssh-keygen`,
publishes no authorization bytes, returns no success edge, and makes every
provider/DAO/Rust/acquisition hook unreachable. The exact boundary fixtures
freeze `authorized_at = 2026-08-29T12:00:00Z`: entry at
`2026-08-29T12:00:00Z` is the rejected same-second case, while entry at
`2026-08-29T12:00:01Z` crosses only the timing check and may continue through
the otherwise-valid synthetic preflight. No sleep, retry, polling loop, clock
advance, or automatic redispatch is permitted; the controller must start a
new command after the signed second if it encounters the rejection.

The round-11 Step 2 test-routing amendment changes test ownership only. The
preflight filesystem/signature boundary is tested in
`tools/tests/test_acquisition_authorization_preflight.py`; manifest shape,
artifact/command joins, authenticated authorization, read allowlisting,
coverage/comparison, and other intrinsic adapter semantics are tested in
`tools/tests/test_dao_differential_manifest.py`; and effective-support
resolution plus DAO-specific G3/acceptance wiring are tested in
`tools/tests/test_dao_effective_support.py`. Their sole shared helper is
`tools/tests/dao_differential_fixtures.py`, limited to direct construction of
isolated repositories and minimal canonical valid inputs. It contains no
generic framework or validation oracle. The exact 65-name and unnamed-domain
routing in `IMPLEMENTATION_PLAN.md` supersedes every earlier reference to
`test_dao_differential_adapter.py`. This organization changes no production
source, evidence contract, acquisition behavior, or compatibility claim.

The following round-10 authority-provisioning amendment supersedes only the
round-9 requirement that P8T step 2's production allowed-signers contract be
nonempty and the round-10 preflight order that read authorization secrets
before proving a real authority was provisioned. It adds no path to the literal
step-2 inventory. Step 2 commits both
`docs/validation/acquisition-authority-v1.allowed_signers` and
`docs/validation/acquisition-authority-v1.revoked_keys` as the exact empty byte
string: each is 0 bytes with raw SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
For this one exact pair, empty is a valid unprovisioned sentinel, not a
malformed OpenSSH contract and not an authority that can match a principal.
The nonempty allowed-signers grammar and the revoked-key grammar above remain
the only provisioned forms.

Both `authorization-preflight` and later `effective-support` load the two
fixed paths as bounded regular-file blobs from exact clean `HEAD` before
reading, decoding, or validating either authorization secret or artifact and
before staging-path inspection, verifier discovery/invocation, materialization,
provider proof, commands, read-allowlist evaluation, policy, or output. When
both blobs equal the exact step-2 sentinel pair, they return exit 3 with fixed
reason
`acquisition_authority_unprovisioned`; preflight prints exactly
`BLOCKED: acquisition_authority_unprovisioned`. It creates no staging child,
forms no adapter/effective result, and makes every authorization, provider,
DAO, Rust, dispatch, and acquisition action unreachable. An empty
allowed-signers blob paired with any nonempty revocation blob, a nonempty blob
that violates the frozen grammar, a missing/special/oversized authority file,
or any authority path/hash/size disagreement remains intrinsic exit-1 `FAIL`
with `invalid_acquisition_authority_contract`. With a valid provisioned
authority, an unlisted or revoked signing key remains exit-1 `FAIL` with
`acquisition_authorization_signature_invalid`; neither case may collapse into
the unprovisioned `BLOCKED` reason.

P8T synthetic tests may replace both sentinels only inside isolated temporary
repositories with known test-only Ed25519 public/private keys and finite test
intervals. Those keys authenticate fixtures only and are not candidates for,
or evidence of, production authority. Every test of secret, staging,
verification, retention, or full-adapter behavior beyond the sentinel gate
must explicitly provision such a synthetic authority first.

Only a later, separately reviewed and human-approved P8 preparation/
evidence-ready commit may transition the production pair. Before any protected
run is created, that commit must replace the allowed-signers sentinel with
exactly one line containing the approved principal, canonical `ssh-ed25519`
public key, namespace, and finite `valid-after`/`valid-before` interval, while
the revoked-keys file remains the exact empty byte string. Its additive
provenance record must name the approving human, the exact principal and public
key, both authority-file raw hashes and sizes, and literal
`revocation_state: active_not_revoked`, plus
`authority_public_key_sha256`. That key hash is lowercase SHA-256 of the
decoded OpenSSH public-key blob selected by the allowed-signers line. The
record must also contain a private-key custody attestation naming the custodian
and custody mechanism and confirmation time, and confirm that private material
is outside the repository, GitHub secrets, runner, overlay, and retained
bundle and is unavailable to the runner. No private material is committed. The
reviewed transition is only authority provisioning: it is not a run
authorization, signature, dispatch, acquisition result, policy enablement,
matrix movement, or compatibility claim. Rotation and revocation remain
additive evidence-ready transitions under the round-9 rules.

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
