# Evidence and status vocabulary

The support matrix records implementation and verification independently. An
implemented capability is not a compatibility claim without DAO differential
evidence.

## Implementation state

| State | Meaning |
| --- | --- |
| `not_started` | No production implementation is claimed. |
| `partial` | Some behavior exists, but the capability is incomplete. |
| `implemented` | The documented behavior and limits are implemented. |
| `out_of_scope_v1` | The capability is intentionally unsupported in v1. |

## Verification state

| State | Meaning |
| --- | --- |
| `unverified` | No evidence is recorded. |
| `internal_only` | Project tests pass; this is not interoperability evidence. |
| `independent_check` | An independent verifier accepts the artifact. |
| `dao_opened` | The recorded DAO environment opened or validated the MDB. |
| `dao_differential` | DAO and Rust canonical semantic results agree. |
| `not_applicable` | External verification does not apply. |

Only `implemented` capabilities with `dao_differential` verification may be
described as DAO-compatible. Evidence references in the support matrix point
to the checked source, test, report, or DAO bundle that supports the recorded
state.

Each release leg retains its validated DAO differential bundle. The bundle
records the scenario input, canonical DAO and Rust snapshots, comparison
result, provider environment, fixture hashes, and release commit. The protocol
validator checks its machine-readable canonical snapshot document.

`docs/PROVENANCE.md` remains the clean-room ledger for every technical source,
observed behavior, experiment, and fixture origin. Every format constant in
`crates/jet3` cites the applicable `SRC-` or `EXP-` entry there.
