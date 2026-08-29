# v1 scope (2026-08-29)

This document replaces the phase plan (`IMPLEMENTATION_PLAN.md`, removed;
see git history) and is the only planning document. Feature work is tracked
as GitHub issues.

## v1 deliverables

1. **Read-only Jet 3 reader** (`crates/jet3`): open an unencrypted Access 97
   file, enumerate tables and columns, stream rows, decode every Jet 3 value
   type, and traverse indexes. Malformed input yields structured errors with
   bounded work.
2. **One DAO differential run**: the Rust reader and DAO each produce a
   canonical semantic snapshot for the shared scenario inventory
   (`oracle/windows-dao/protocol/`); the snapshots are compared, and the
   result is recorded in `docs/PROVENANCE.md`.
3. **Support matrix** (`docs/validation/support-matrix.json`): per-capability
   status set from that run. Nothing is called "supported" without it.

## Release gates

- `just ready` is green on the release commit.
- One validated DAO differential bundle exists for the release commit.
- Every format constant in `crates/jet3` cites a provenance entry.

`scripts/acceptance.sh full` and the G0–G8 gate set in
`docs/validation/ACCEPTANCE.md` are to be reduced to the three gates above
(tracked as an issue).

## Explicitly out of v1

- Writer, in-place update, and their differential legs.
- Exact-commit build attestation, evidence overlays, and release-evidence
  adapters beyond the single DAO bundle.
- Repository-contract / traceability policing tools.
- Forms, reports, VBA, macros, queries, passwords, encryption, replication
  semantics, multi-user locking, Jet 4, ACCDB, crash recovery.

## Immediate follow-ups

- Reduce the semantic snapshot adapter (PR #92) to: traverse with the real
  reader, emit canonical JSON via `serde_json`, hash with `sha2`. Drop the
  hand-rolled SHA-256, `build.rs` identity checks, staging/durability layers,
  and output budgeting.
- Collapse `acceptance.sh full` to the three release gates and delete the
  repository-contract and traceability validators and their CI jobs.
- Retire unused experiment lanes under `oracle/windows-dao/experiments/`.
