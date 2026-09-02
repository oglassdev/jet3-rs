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
reviewed changes: multiple-index implementation, definition continuation
placement, extended names, public creation, initial rows, relationships, and
safe filesystem publication. `EXP-0093` records an accepted three-replica
multiple-index result for the four exact first-create arms. It observes one map
page at `root+1`, the catalog `LvProp` page at `root+2`, and one index root per
physical ordinal at `root+3+i`. That overturns the composer's current deduced
one-index order, which places the first root before `LvProp`.

Two remaining format gaps each need their own preregistered DAO validation
before the planner can widen: where a definition continuation page lands
(#151), then catalog name keys for bytes above `0x7E` (#152). `EXP-0094`
contains the SHA-256-pinned #151 preregistration. `EXP-0095` records its
canonical `no_outcome`: every replica reached capture of the 69-field arm, but
that arm failed the combined 2-KiB geometry/64-page bound and could not be
retained. No continuation placement was observed, and the 70- and 140-field
DAO table appends were not attempted. The SHA-256-pinned `EXP-0098` successor
keeps the exact scenarios and questions, raises completed
checkpoints to 256 pages, records exact raw length/divisibility/derived pages
and the failed predicate before enforcement, records all three ordered copied
arm identities before table append, and permits only uninterpreted recovery
salvage through 512 pages. It cannot run until its exact preregistration commit
is merged and a new explicit human run decision is made. #150 is
evidence-complete. Its bounded implementation slice is unblocked but must
correct the existing one-index page order while adding multiple physical and
logical records, name-sorted logical order, and the observed primary, unique,
ordinary, ascending, and descending forms. It cannot infer arbitrary schemas
or behavior above three indexes. #102 remains the separate hosted write
differential after database creation is complete.

`EXP-0096` is the SHA-256-pinned #152 preregistration. `EXP-0097` records its
canonical `no_outcome`: all 2,358 DAO name attempts reported creation and the
bounded DAO metadata agreed, but every replica failed catalog-key decoding at
the rejection checkpoint with `checkpoint reject: a catalog row has malformed
identity fields`. Under the preregistered all-or-`no_outcome` rule, the defined
arms cannot be promoted post hoc and establish no catalog-name mapping or
weights. #152 remains open and evidence-blocked; another acquisition requires
a separately pinned successor and renewed explicit human authorization.
