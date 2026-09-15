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

The reader and its hosted differential are complete for the recorded inventory.
The optional CLI (#104) and creation module cleanup (#182) are complete.
Creation (#100), updates (#112), their broader DAO inventories (#102/#113),
and roadmap #75 remain open.

### Creation

Creation fits tables within the catalog page capacity, with multi-page initial scalar rows, generated
AutoIncrement IDs, numeric indexes with one/two components and multiple levels,
and one unindexed Memo/OLE column per table. Each table supports up to three
indexes. Relationships are restricted to two
scalar tables with one non-cascading, non-null Long relationship.

Schema/name combinations, index types/counts, relationship forms and inline
allocation remain restricted. Explicit AutoIncrement IDs and empty OLE are
refused. Empty Memo requires an explicit option in a restricted schema.
Existing-table schema changes and table/relationship dropping are absent.

EXP-0154 covers twelve hosted write recipes. EXP-0220 corrects the numeric
sidecar comparison over retained hosted artifacts and adds the three creation
index recipes: deep Long, nullable numeric and multiple indexes. The original
EXP-0214 failure remains recorded separately. EXP-0222 adds eight local DAO
comparisons for five/six-table layouts, multiple indexes on later tables, and
actual catalog capacity: 28 short-named empty tables or 15 with long names
and wider definitions in the tested layouts.

### Updates

Public APIs implement bounded field updates, insertion into populated pages or
one EOF data page, deletion/compaction, last-live-row page release, same-page
scalar row replacement, and multi-level unique Long index maintenance. Rebuilt
trees keep their root, reuse reserved index pages, and append nodes within inline
maps. Indexed EOF insertion publishes data, allocation, table counts and index
changes together. The CLI exposes full-row replacement. Publication is Unix-only.

EXP-0212 covers seventeen hosted update recipes. EXP-0221 adds local DAO
comparisons for indexed insertion/deletion, boundary insertion, native
continuations and duplicate rejection. EXP-0219 identifies the retained index
counter after deletion; Rust now preserves it on deletion and increments its
prior value on insertion. EXP-0216 and EXP-0218 remain historical failed runs.
EXP-0223 adds five tree lifecycle cases and two DAO-compressed input
continuations, including depth-three growth and empty-table reuse. EXP-0224
records native last-live-row release with retained deleted slots.

### Validation

The read-only library and CLI validator walk catalogued user tables with one
shared resource budget: definitions, declared row counts, decoded values,
long-value chains and physical index traversal. Reports state their coverage.
System and non-table contents, orphan pages, relationship constraints, and
index key semantics or row membership remain outside these checks. Catalog
reading follows native overflow records using the shared row-locator grammar
(EXP-0228). Validation success does not establish DAO compatibility.

### Remaining work

- Extend creation beyond current schema/index-key and inline-allocation bounds.
- Extend updates to composite/nonunique/null index keys,
  relationship and long-value targets, free-page/slot reuse and indirect maps.
- Cover remaining DAO inventories, stored-query preservation and broader
  failure/rollback behavior. Local VM and hosted runs may both establish
  evidence; preregistration and per-run approval are not required.
- Extend validation to the remaining integrity checks; resolve deterministic-
  output configuration, currently marked not started in the support ledger.
- Meet all three release gates on a release commit. Evidence covers its recorded
  revisions and finite recipes; no whole-v1 compatibility is claimed.

## Practical acceptance target

Use one inventory database to measure a workable read/write lifecycle:

- `Items`: `Id` Long primary key (explicit IDs), `Name` Text(80), nullable
  `Price` Currency, and `Active` Boolean.
- `Notes`: `Id` Long and `Body` Memo, with retained unrelated rows and payloads.

The target is complete when public library APIs can create the database,
populate Items beyond both a data-page boundary and an index-leaf boundary,
reopen it, and read every row with correct index traversal and lookups. Then
change names, prices and null values, delete scattered rows, insert more rows,
and reopen again. DAO must observe the complete expected schema and contents
at the declared checkpoints. Notes metadata, rows and payload bytes must remain
unchanged through Items mutations. Unsupported requests, validation failures
and resource failures rejected before publication must preserve the original
file byte-for-byte; post-publication sync errors retain their documented
potentially-visible-change semantics.

This is a concrete milestone within v1, not a substitute for the full scope or
release gates. Indexed insertion crosses data-page and index-leaf boundaries;
the complete Items/Notes scenario and broader v1 inventories still require
their recorded DAO comparisons.
