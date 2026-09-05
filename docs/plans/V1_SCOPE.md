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

Continue database creation under #100. Initial-row creation accepts up to
four tables with scalar rows across data pages, bounded Long indexes, and
one unindexed Memo/OLE column per table. Long indexes now grow through
multiple branch/leaf levels; existing schema and inline-map bounds still apply. One AutoIncrement column per table accepts
explicit generation requests and persists its last generated ID, including
when indexed; explicit IDs and empty long payloads remain refused. EXP-0136
records the underlying DAO state observations.

`create_database_with_relationship_rows` adds two populated scalar tables
with one non-null Long relationship, a parent primary index and a child
foreign index. EXP-0134 records exact local candidate acceptance, full index
and payload readback, and matched valid-child insertion, orphan rejection
and duplicate-parent rejection on separate DAO copies. The provenance ledger
records the other bounded writer observations. These establish only the
pinned candidates and finite probes, with no general compatibility claim
or hosted support movement.

EXP-0146 records bounded acceptance of all three multi-level candidates from
separately pinned analysis of the retained files; EXP-0140 preserves the
original decoder refusal and `no_outcome`. Recognize the newly observed control
branch header value in the reader, then continue broader index keys, null
semantics and allocation beyond inline maps. After creation is complete, run the hosted write differential
(#102), implement updates preserving unrelated data (#112), then run the
hosted update differential (#113). Keep module cleanup (#182) until the
creation API settles.
