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

Creation is deterministic by default: the same ordered request and library
version produce identical MDB bytes, independently of destination paths and
successful resource limits. Public API tests cover empty files, multi-page
catalogs and definitions, generated IDs, composite indexes, Memo/OLE payloads
and relationship metadata. This is an internal output guarantee; it adds no DAO
compatibility claim. No separate output configuration is required.

Creation packs tables with multi-page system catalogs and catalog indexes
with inline and indirect allocation maps. Table and column names admit
64 ASCII bytes and index names admit 63. Table definitions may
span linked pages on first and later tables, including populated and indexed
schemas. It supports multi-page initial rows, explicit/generated AutoIncrement
IDs, and up to 32 scalar indexes per table, including Date, Binary, variable
Text and GUID.
Indexes have one to ten components and can span multiple levels. Independent
Memo/OLE columns can coexist with numeric indexes and generated IDs; the payload
columns themselves cannot be indexed. Each payload column has separate ownership
and availability maps. Definitions and map rows can span multiple pages; files
are bounded by map-reference capacity and the caller's resource budget. Relationships remain restricted to two scalar tables with one
non-cascading, non-null Long relationship.

Schema/name combinations, index key types and relationship forms remain
restricted. Empty OLE is refused. Empty Memo requires an
explicit option in a restricted schema. Existing-table schema changes and
table/relationship dropping are absent. EXP-0239 adds explicit, negative and
wrapping AutoIncrement IDs to the finite writer comparisons.

EXP-0154 covers twelve hosted write recipes. EXP-0220 corrects the numeric
sidecar comparison over retained hosted artifacts and adds the three creation
index recipes: deep Long, nullable numeric and multiple indexes. The original
EXP-0214 failure remains recorded separately. EXP-0222 adds eight local DAO
comparisons for five/six-table layouts, multiple indexes on later tables, and
the former single-page catalog capacity: 28 short-named empty tables or 15 with
long names and wider definitions in those layouts. EXP-0236 adds six
multiple-Memo/OLE creation pairs and six native continuation pairs. These cover four payload
columns with three numeric indexes and 205/213 rows, generated IDs on a later
table, and map-capacity cases with six payload columns and zero/one index or five
payload columns and two/three indexes. Complete payloads, nulls, schema, index
traversal and unrelated Notes pages match the declared expectations.

EXP-0241 establishes native multi-page catalog and access-control maps, and branched catalog
indexes. EXP-0244 adds twelve creation comparisons: five/six-table controls,
40 and 110 short-named tables, 30 long-named tables with 32 columns, and 40
long-named tables. Complete DAO user schema, rows, traversal and seeks match;
raw checks cover every catalog/ACE row locator, index tree and allocation map.
EXP-0249 establishes carry in the 16-bit creation counter and native ASCII
name boundaries. Creation writes the observed closed-empty/reopen-per-create
history without wrapping the counter; actual allocation and resource limits
bound table count. A 64-byte index name failed native Seek, so creation
admits 63 bytes for index names and 64 for table/column names.

EXP-0247 adds six creation and six native continuation pairs at logical definition
lengths 2,048/2,049, 4,088/4,089 and 6,128/6,129 bytes. These combine first/later
tables, 64/96 columns, zero/three indexes, up to 205 initial rows, generated IDs
and independent Memo/OLE payloads. Complete schema, values, payloads, traversal
and seeks match DAO, with unrelated Notes pages preserved. Exact-capacity
definitions retain an empty terminal page; longer chains use the existing
2,040-byte continuation payload and shared resource budget.

EXP-0249 establishes the creation counter's 16-bit carry and native name
boundaries. EXP-0251 adds 40 paired comparisons (80 captures): ten cases in
two replicas, each with native insertions on both outputs. These include
128/255/256 tables and 64-byte table/column names with 63-byte index names.
Complete schema, values, traversal/Seek, catalog rows and physical indexes
match. Native catalog overflow rows retain their logical index locators.

### Updates

Public APIs implement bounded field updates, insertion into populated pages or
one EOF data page or a released target-table page, deletion/compaction,
last-live-row page release, row replacement with stable logical locators, independent Memo/OLE
payload mutation, and multi-level index maintenance. A table may
have up to 32 indexes with one to ten Boolean, Byte, Integer, Long, Currency,
Single, Double, Date, Binary, variable Text or GUID components, including mixed
directions, duplicates and null policies. Text keys use the observed English-US/CP1252
collation, retaining stored row bytes while ignoring trailing ASCII spaces in keys.
Fixed Text and other collations remain outside indexed mutation. Rebuilt trees keep their roots, reuse reserved index pages, and append
nodes with allocation-map growth. Indexed EOF insertion publishes data, allocation, table counts and index
changes together. Memo/OLE insertion and full-row replacement support null,
inline, single-page and chained payloads. Payload pages are validated against
all live references and their owning column, with separate single/chained
storage pools; deletion releases emptied payload pages for reuse. The encoded
row must still fit the native row-size limit. Growth can retain a hidden storage
slot or move directly to a new one; shrinking can collapse back to the logical
slot. Deletion removes both slots and releases emptied pages. Mutation of
multi-hop overflow chains remains refused. One AutoIncrement
column accepts generated or explicit IDs on insertion; replacement and deletion retain existing
IDs and allocation state. The CLI exposes full-row replacement. Publication
supports Unix and Windows; Windows flushes the file before publication without
a separate directory-sync guarantee.

EXP-0212 covers seventeen hosted update recipes. EXP-0221 adds local DAO
comparisons for indexed insertion/deletion, boundary insertion, native
continuations and duplicate rejection. EXP-0219 identifies the retained index
counter after deletion for present unique Long keys; Rust preserves it on
deletion and increments its prior value on insertion of those keys. EXP-0216 and EXP-0218 remain historical failed runs.
EXP-0223 adds five tree lifecycle cases and two DAO-compressed input
continuations, including depth-three growth and empty-table reuse. EXP-0224
records native last-live-row release with retained deleted slots. EXP-0227
establishes target-table released-page reuse; dense-page updates now recompute
availability, and insertion requires space for the requested row and slot.
EXP-0229 completes the practical Items/Notes lifecycle with eight DAO pairs.
EXP-0230 establishes numeric counters: only insertion of a currently absent,
included key increments them; deletion and key edits retain them, even when
current distinct keys exceed the stored counter. EXP-0232 compares fifteen
numeric lifecycle checkpoints, three native successor pairs and three
native-input continuations, including composite depth-three growth and
compressed Boolean/Currency/Double trees. EXP-0238 adds four Memo/OLE lifecycle
cases, including four mixed payload columns with three numeric indexes and a
generated-ID case. All 28 pairs (56 captures) match complete payloads, schema,
index traversal/Seek, counters and ownership. The cases cover shared-page
partial deletion, null/inline/external transitions, clear/reinsert without file
growth, native continuations on both outputs, and Rust mutation of retained
native controls. Unrelated Notes page hashes remain exact. Twelve rejected
requests preserve the complete Rust input, including AutoIncrement state; these
refusals do not claim to reproduce DAO's counter side effects on failed inserts.
EXP-0239 adds 90 Rust mutations of retained native AutoNumber inputs and two
explicit/wrapping initial creations. DAO continues writing to every candidate
and control; all 184 pairs (368 captures) match complete rows, schema, allocation
state and numeric key/locator records, with Anchor payload pages preserved.
EXP-0242 adds native Windows NTFS publication tests and all eight practical
lifecycle DAO pairs using databases created and mutated entirely on Windows.

EXP-0243/0245 establish Date/negative-zero and Binary key encodings, long-key
shortening, collision behavior, single-variable row boundaries and depth-four
roots. EXP-0246 adds all 35 scalar lifecycle comparisons (70 captures): five
cases through five checkpoints, native writes on both outputs, and Rust edits
to native inputs. Complete values, schema, traversal/Seek, key/locator records,
counters and Notes preservation match. Native deletion can retain a class-one
index root with a single tail child; that bounded shape is now readable and
mutable. Original failed analyzer outcomes remain recorded separately.

EXP-0248 establishes the complete defined CP1252 Text weight map and GUID
framing, with 9,116 native keys checked across original and held-out matrices.
EXP-0250 accepts all seven scalar lifecycle cases, including Text and GUID:
49 paired comparisons (98 captures) cover five checkpoints, native successors
and Rust edits to native inputs. Schema, complete values, traversal/Seek,
physical key/locator records, counters and unrelated Notes preservation match.
Earlier context-dependent COM assignment failures remain recorded separately;
the accepted harness runs each case in a fresh x86 worker.

EXP-0252 establishes native limits of 32 indexes and ten components plus
independent map-page locators. EXP-0253 accepts 25 index lifecycle pairs
(50 captures), including full composite Seek, mixed Text/GUID keys, native
successors and Rust edits to native inputs. Sixteen additional Memo/OLE
creation/native pairs cover eight payload columns, maps on multiple pages,
and an ownership/availability pair split across pages. Full values, physical
keys/locators, ownership, counters and unrelated Notes preservation match.

### Validation

The read-only library and CLI validator walk catalogued user tables with one
shared resource budget: definitions, declared row counts, decoded values,
long-value chains and physical index traversal. Every leaf reference must name
a distinct live logical row in its table. Supported scalar key schemas also
check row/key equality, complete key inventory, null policies, uniqueness and
branch bounds; reports count verified and uninterpreted indexes separately.
System and non-table contents, orphan pages, relationship constraints, and
unsupported index key schemas remain outside these checks. Catalog
reading follows native overflow records using the shared row-locator grammar
(EXP-0228). Validation success does not establish DAO compatibility.

EXP-0254 adds 84 accepted native allocation observations across two replicated
lifecycles. Inline rows can grow beyond 1,024 bits, and indirect bitmap slots
represent 16,352 pages each. Empty availability windows can be smaller than the
owned inventory. Creation and mutations now implement indirect map allocation,
including global bookkeeping for the bitmap pages themselves. EXP-0256 accepts
16 allocation lifecycle pairs (32 captures), including Rust edits to native
files and conversion of widened inline maps into compact indirect rows.
Complete payloads, index contents, allocation state and Notes preservation
match. EXP-0255 fixes native index prefixes that include row-locator bytes;
the original timeout and reader-failure reports remain recorded separately.

EXP-0257/0258 establish multiblock variable-row trailers through 255 variable
columns, including the shared final-boundary/sentinel byte and unchanged old
rows after appending variable columns. EXP-0260/0261 establish complete row
limits of 2,003 bytes for fixed-only rows and 2,012 for variable rows; schema
planning, encoding and decoding apply those limits. Long-value page slots
retain their separate physical capacity.

EXP-0259 accepts 42 lifecycle pairs (84 native captures), covering eight wide
schema families, native successors, Rust edits of native inputs and two
expanded-schema continuations. Complete values, payloads, index traversal/Seek,
physical keys/locators, allocation and unrelated Notes agree. A separate reader
comparison passes all 76 retained discovery captures (372 rows). Untouched old
rows retain their raw bodies; rewritten and inserted rows use the current
schema. This covers appended variable columns with an unchanged fixed prefix.

EXP-0263 accepts 36 overflow lifecycle pairs (72 captures), including Rust
mutations of existing DAO overflow rows and native writes on both outputs.
Growth, equal-size edits, fixed primary-key changes, source collapse, direct
hidden-target relocation, shared-page deletion and released-page reuse preserve
logical locators and complete values. Index traversal/Seek, physical keys,
ownership and unrelated Notes agree. Independent physical checks also cover
ordinary insertion on Rust pages holding logical links. Full-row replacement
can reallocate the selected Memo/OLE storage; unrelated headers and values stay
unchanged, and fixed-field updates preserve the selected payload storage too.
This finite comparison does not establish a universal page-selection policy.

### Remaining work

- Extend creation to remaining schema/index-key combinations and relationship forms.
- Extend updates to remaining index key types/collations, relationship targets,
  additional payload/schema combinations, broader
  data-page/live-slot reuse and multi-hop row growth.
- Cover remaining DAO inventories, stored-query preservation and broader
  failure/rollback behavior. Local VM and hosted runs may both establish
  evidence; preregistration and per-run approval are not required.
- Extend validation to the remaining integrity checks.
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

EXP-0229 completes this milestone: all eight paired checkpoints match DAO,
including dense-page changes, released-page reuse and Notes preservation.
This is a concrete milestone within v1, not a substitute for the full scope,
broader DAO inventories or release gates.
