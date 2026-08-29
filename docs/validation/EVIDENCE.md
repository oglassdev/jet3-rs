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

`dao_differential_v1` owns the meaning and maximum level
`dao_differential`; policy cannot relabel it. Its manifest shape is fixed by
`docs/validation/schema/dao-differential-v1-manifest.schema.json`. The adapter
must parse the committed protocol contracts at
`oracle/windows-dao/protocol/v1_2/scenarios.schema.json`,
`oracle/windows-dao/protocol/v1_2/scenarios.json`,
`oracle/windows-dao/protocol/v1_2/branch-registry.json`, and
`oracle/windows-dao/protocol/v1_2/canonical-semantic-snapshot.schema.json`
from the named release commit. It then recomputes, rather than trusts, all of
the following:

- exact bundle-manifest and payload-tree closure, file sizes, and SHA-256s;
- exact provider identity, environment, command, source-revision, and clean
  release-commit bindings required above;
- the scenario `content_sha256`, required scenario set, capability mapping,
  operation, and absence of a skipped or unsupported result;
- schema validity and byte equality of the independently produced canonical
  DAO and Rust snapshots for every applicable read/write/update leg;
- source-MDB binding and complete required-branch coverage in each Rust
  coverage receipt, including rejection of an unregistered branch or bypass;
  and
- for update legs, a checked preservation-diff report with no unexpected
  differences for every declared `preserve_paths` entry.

One adapter evidence item covers one capability and names its complete
scenario subset. Its computed output has exactly `adapter`, `capability_id`,
`commit`, `manifest_sha256`, `scenario_ids`, and `status`; `status` is `PASS`
only after every applicable check above succeeds. The overlay's
`expected_output` must equal that computed object byte-for-byte after canonical
JSON serialization. Duplicate capability outputs, a capability absent from
the committed matrix, a scenario omitted from its required set, or an output
above the adapter's intrinsic level is rejected.

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
