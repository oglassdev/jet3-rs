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

Finish the focused writer experiments before exposing the creation API. The
typed table planner and one-table composer are merged: a described table is
validated through the same table-definition and catalog encoders that write
it, and its pages, catalog row, and access-control rows derive from `EXP-0087`.
The composer still requires a caller-supplied `LvProp` payload in code because
only its framing, not a general grammar, is established. `EXP-0091` now admits
the exact null-`LvProp` plus retained-empty-page construction as the bounded
replacement hypothesis for the next implementation slice.

`EXP-0091` observed DAO accept one exact composed Alpha image with a null
catalog `LvProp` and an empty retained property page. That admits the bounded
construction as the next implementation hypothesis; it does not establish the
same result for arbitrary schemas or permit omitting the retained page. The
remaining experiments and implementation slices proceed in small independently
reviewed changes: multiple indexes, definition continuation placement,
extended names, public creation, initial rows, relationships, and safe
filesystem publication.

Three remaining format gaps each need their own preregistered DAO validation
before the planner can widen, roughly in priority order: the page assignment
for more than one index (#150), which also covers the composite and descending
shapes; where a definition continuation page lands (#151); and catalog name
keys for bytes above `0x7E` (#152). #102 remains the separate hosted write
differential after database creation is complete.
