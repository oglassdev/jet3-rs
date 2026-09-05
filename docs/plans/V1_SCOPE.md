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

Continue database creation under #100. Public creation supports up to four
empty user tables within bounded schema restrictions. Initial-row creation
currently accepts one table, with scalar rows across data pages, one bounded
ascending Long index, or one unindexed Memo/OLE column. Empty long payloads,
AutoIncrement values, and multi-table initial rows remain refused.

`EXP-0120` records exact local primary/unique/ordinary-duplicate indexed
candidate acceptance. `EXP-0124` records exact Memo/OLE candidates with
inline, single-page, chained and null payloads. These validate only the pinned
candidates; no general allocation policy, compatibility claim or hosted
support movement follows.

`create_database_with_relationship` creates two empty tables with one
Long-to-Long relationship, a parent primary index and optional additional
unique Long index, and an initially unindexed child. `EXP-0118` and `EXP-0122`
record exact local candidate acceptance; populated relationships and
referential-integrity mutations remain unvalidated.

Next, compose initial rows for multiple tables using the existing bounded
per-table writers, then validate those exact candidates. AutoIncrement state
and populated relationships still need separate work. After creation is
complete, run the hosted write differential (#102), implement updates that
preserve unrelated data (#112), then run the hosted update differential
(#113). Keep module cleanup (#182) until the creation API settles.
