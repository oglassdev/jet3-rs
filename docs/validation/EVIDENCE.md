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
