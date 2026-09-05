# v1 scope (2026-08-29)

This document replaces the phase plan (`IMPLEMENTATION_PLAN.md`, removed;
see git history) and is the only planning document. Feature work is tracked
as GitHub issues.

## v1 deliverables

v1 is a full read/write implementation of unencrypted Access 97 / Jet 3,
delivered in this order:

1. **Reader** (`crates/jet3`): open a file, enumerate tables and columns,
   stream rows, decode every Jet 3 value type, traverse indexes. Malformed
   input yields structured errors with bounded work.
2. **Writer**: create a new database, define tables/columns/indexes, and
   insert rows that DAO opens and reads back identically.
3. **Update**: insert, update, and delete rows in an existing database while
   preserving all unrelated data (including objects we do not interpret).
4. **DAO differential runs**: one per leg (read, write, update). Rust and
   DAO each produce a canonical semantic snapshot for the shared scenario
   inventory (`oracle/windows-dao/protocol/`); the snapshots are compared
   and the result recorded in `docs/PROVENANCE.md`.
5. **Support matrix** (`docs/validation/support-matrix.json`): per-capability
   status set from those runs. Nothing is called "supported" without one.

## Release gates

- `just ready` is green on the release commit.
- A validated DAO differential bundle exists for each leg on the release
  commit.
- Every format constant in `crates/jet3` cites a provenance entry.

`docs/validation/ACCEPTANCE.md` describes these gates and the
`scripts/acceptance.sh` checks that cover them.

## Explicitly out of v1

- Exact-commit build attestation, evidence overlays, and release-evidence
  adapters beyond the DAO bundles.
- Repository-contract / traceability policing tools.
- Forms, reports, VBA, macros, query execution, passwords, encryption,
  replication semantics, multi-user locking, Jet 4, ACCDB, crash recovery.

## Current next step

Continue database creation under #100. Initial-row creation now accepts up to
four tables with scalar rows across data pages, bounded Long indexes, and
one unindexed Memo/OLE column per table. Existing schema, single-leaf and
inline-map bounds still apply. AutoIncrement values and empty long payloads
remain refused.

`EXP-0130` records exact local mixed-table and empty-first candidate acceptance,
including later-table index traversal and Memo/OLE payloads. Earlier exact
individual-writer results remain in the provenance ledger. These observations
establish only the pinned candidates, with no general allocation policy,
compatibility claim or hosted support movement.

`create_database_with_relationship` currently creates two empty tables with
one Long-to-Long relationship, a parent primary index and optional additional
unique Long index, and an initially unindexed child. Populated relationships
and referential-integrity mutations remain unvalidated.

Next, combine relationships with initial rows, including the child foreign
index and initial referential checks. AutoIncrement state needs separate
observation. After creation is complete, run the hosted write differential
(#102), implement updates preserving unrelated data (#112), then run the
hosted update differential (#113). Keep module cleanup (#182) until the
creation API settles.
