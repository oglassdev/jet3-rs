# Validation

Words such as *works*, *supported* and *compatible* require DAO differential
evidence; they are not synonyms for "our reader accepts our bytes." Scope,
deliverables and exclusions are in [V1_SCOPE.md](../plans/V1_SCOPE.md);
accepted format facts and experiment outcomes are in `docs/PROVENANCE.md`.

## Support matrix

[support-matrix.json](support-matrix.json) records one implementation state,
one verification state and evidence paths per capability.
`schema/support-matrix.schema.json` fixes the capability ids;
`python3 tools/validate_contract.py` checks the matrix.

| Implementation | Meaning |
| --- | --- |
| `not_started` | No production implementation is claimed. |
| `partial` | Some behavior exists, but the capability is incomplete. |
| `implemented` | The documented behavior and limits are implemented. |
| `out_of_scope_v1` | Intentionally unsupported in v1. |

| Verification | Meaning |
| --- | --- |
| `unverified` | No evidence is recorded. |
| `internal_only` | Project tests pass; not interoperability evidence. |
| `independent_check` | An independent verifier accepts the artifact. |
| `dao_opened` | The recorded DAO environment opened or validated the MDB. |
| `dao_differential` | DAO and Rust canonical semantic results agree. |
| `not_applicable` | External verification does not apply. |

## Claim rules

1. A round trip through code under test is internal evidence only.
2. A capability is **supported** only when it is `implemented` and a DAO
   differential run on the released commit matches for it.
3. Missing or invalid DAO evidence is a failure, never an implicit pass.
4. Local VM and hosted runs use the same standard: retain inputs, source
   revision, provider environment, complete comparisons and failed attempts.
   Historical reports stay unchanged; a later result states its own scope.

## Release gates

1. `just ready` passes on the release commit.
2. A validated DAO differential bundle exists for each release leg (read,
   write, update). Check a bundle's canonical snapshot document with
   `python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py document <file>`.
3. Every format constant in `crates/jet3` cites its `SRC-` or `EXP-` entry in
   `docs/PROVENANCE.md`.

Fuzzing, benchmarks, platform tests and dependency checks run in CI but are not
additional release gates.
