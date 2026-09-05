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

## Current checkpoint

The bounded reader and its hosted differential are complete for the recorded
inventory. Creation and existing-file updates are implemented in restricted
forms; #100, #102, #112, #113 and roadmap #75 remain open. The optional CLI
(#104) and creation module cleanup (#182) are complete.

### Creation and write evidence (#100, #102)

Creation supports up to four tables with initial scalar rows across pages,
bounded one/two-component numeric indexes (including descending, nullable and
multi-level trees), generated AutoIncrement IDs, and one unindexed Memo/OLE
column per table. The first table supports up to three indexes, later tables
one. Schema/name combinations and allocation remain restricted to the existing
encoders and inline maps. Explicit AutoIncrement IDs and empty OLE are refused.
Empty Memo requires an explicit option in the restricted Long-ID/Memo schema;
EXP-0210 records six accepted local candidate/control pairs and their native
continuations, not hosted support for that option.

Relationship creation supports two scalar tables with one non-cascading,
non-null Long relationship and its parent/child indexes. General relationship
mutation, existing-table schema changes, and table/relationship dropping remain
unimplemented.

EXP-0154 establishes the twelve hosted write recipes through reviewed analysis
of retained artifacts; EXP-0142 preserves the original comparator failure.
The grouped deep-Long, nullable numeric and multiple-index expansion ran once
under EXP-0213. EXP-0214 is **no_outcome**: all three DAO captures completed,
but string-valued Double fields in index sidecars failed comparison against
canonical numeric values. This does not extend hosted support or complete #102.
A separate reviewed analysis of the retained artifacts is the next validation
step; preserve the original result and plan.

### Existing-file updates and evidence (#112, #113)

Public APIs implement bounded field updates, insertion into populated pages or
one EOF page, deletion/compaction, sole physical-row page release, same-page
scalar row replacement, and unique Long index maintenance in an isolated leaf.
Publication is Unix-only; unsupported layouts return structured errors.

EXP-0212 records seventeen hosted update recipes with 34 matched complete
DAO/Rust snapshots and independent preservation checks. It retains EXP-0204's
thirteen cases and adds sole-row release, growth/shrinkage with scalar null,
Text/Binary/Boolean transitions, and replacement beside a retained tombstone.
The support matrix adds this evidence while keeping row mutation and index CRUD
maintenance **partial**. Hosted index maintenance evidence covers the earlier
unique Long key replacements and indexed non-key update, not indexed insertion
or deletion.

The grouped local indexed insertion/deletion experiment ran once under
EXP-0215. EXP-0216 is **no_outcome**: all 96 captures were retained, but native
DAO deletion controls retained distinct-key counts that failed the frozen
analyzer's count predicate. Logical-row diagnostics are not accepted subset
results. Review the retained native count observations before proposing a
successor analysis; this local result moves no hosted support state.

### Remaining work

- Resolve the two retained-data comparison questions above under distinct
  reviewed plans, without repeating acquisition automatically.
- Extend creation beyond current schema/index-key and inline-allocation bounds.
- Extend updates to broader indexed layouts, relationships, long values,
  free-page/slot reuse and indirect allocation, with unrelated-data preservation.
- Cover remaining hosted inventories, including indexed insertion/deletion,
  stored-query preservation and failure/rollback behavior.
- Meet all three release gates on a release commit. Current evidence binds its
  recorded revisions and finite recipes; no whole-v1 compatibility is claimed.
