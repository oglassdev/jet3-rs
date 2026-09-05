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

Continue database creation under #100. The public API creates up to four empty
user tables within its bounded schema restrictions. Initial-row creation now
accepts one unindexed table with a one-page definition and scalar rows packed
across data pages within the existing inline-map capacity. AutoIncrement,
Memo, and OLE values remain refused by that entry point.

`EXP-0116` records three accepted local replicas of the exact 26-page
`Rows(Id Long)` candidate containing integers -254 through 254 across three
data pages. Earlier exact empty-table and one-page observations remain in the
provenance ledger. These results establish only the pinned candidates, with no
general allocation policy, compatibility claim, or support-matrix movement.

Next, broaden initial-row creation in focused implementation and preregistered
DAO experiment slices, including indexes and long values, and add relationships.
After creation is complete, run the hosted write differential (#102), implement
updates that preserve unrelated data (#112), then run the hosted update
differential (#113). Keep module cleanup (#182) until the creation API settles.
