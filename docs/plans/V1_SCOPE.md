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
64 Windows-1252 bytes and index names admit 63. Table definitions may
span linked pages on first and later tables, including populated and indexed
schemas. It supports multi-page initial rows, explicit/generated AutoIncrement
IDs, and up to 32 scalar indexes per table, including Date, Binary, fixed/variable
Text and GUID.
Indexes have one to ten components and can span multiple levels. Independent
Memo/OLE columns can coexist with numeric indexes and generated IDs; the payload
columns themselves cannot be indexed. Each payload column has separate ownership
and availability maps. Definitions and map rows can span multiple pages; files
are bounded by map-reference capacity and the caller's resource budget.
The plural relationship APIs admit enforced constraints with one to ten ordered
scalar components and independently enabled cascade updates/deletes,
including multiple parents or children, chains, cycles, self-references,
shared foreign physical indexes and unrelated tables in any order. Parent keys
admit every supported scalar index type, including AutoIncrement parents, and
require a unique index with exactly matching column order. The first eligible
ascending logical name selects the parent tree, including nonprimary and nullable
unique indexes. Descending-only parents generate a shared ascending tree with
the same null policy; EXP-0286/0287 record native observations and 18 creation
plus 84 lifecycle comparisons. Without the corresponding cascade option, mutations
reject changing or removing a null parent while null child keys remain, and permit
deleting the sole null self-reference.
Only all-null child insertion is exempt from requiring a matching parent;
partial-null tuples require exact matches. Endpoint
types must agree, with differing Text/Binary widths and mixed fixed/variable Text
admitted. Boolean null assignments store False, including indexed keys. Existing ordinary ascending FK indexes
are reused while retaining declared aliases; other child index forms need a
separate foreign tree. Both endpoint aliases count toward the 32-logical-index
limit; a self-reference consumes two slots. Relationship rows and all three
system indexes can span multiple pages, with graph size bounded by per-table
index capacity and the caller's resource budget. Nullable keys and a separate
child primary are admitted. Other columns retain generated IDs, Text/Memo
options, independent Memo/OLE maps and definition/property chains. The singular
APIs retain their non-cascading two-ordered-table Long bounds. EXP-0288/0289 record scalar eligibility and 38 creation plus
498 mutation comparisons. EXP-0290/0291 record ordered composite observations
and 276 accepted comparisons: twelve creations, 142 successful mutations and
122 refusals. EXP-0292 adds the native self-reference creation boundary:
identical complete parent/child field vectors are refused, while individual
components may overlap. EXP-0293 accepts the corrected guard and self/shared-index
graph supplement: twelve creations, 32 successful mutations and 28 refusals.
The final candidate also reproduces all 276 main-suite outputs byte-for-byte,
bringing the combined finite evidence to 348 comparisons.
EXP-0294–0296 add cascade update/delete creation and mutation evidence:
eighteen creation pairs and 148 mutation pairs (104 successes, 44 refusals),
plus two matching creation refusals. Complete actual property collections,
rows, indexes, counters, maps, payloads and unrelated system/catalog bytes are
compared. The final report independently replays from its complete archived
inputs. The final AutoIncrement marker guard reproduces all 166 accepted images
byte-for-byte and both creation refusals; its regression and source identities
are retained in the EXP-0296 supplement. Existing-database relationship create/drop and atomic replacement are now
implemented through `edit_schema`; their new differential coverage is recorded
separately below.

Schema names use the database’s observed General, Nordic, traditional Spanish,
Dutch, Cyrillic or Greek collation for ordering and duplicate detection. Defined
Windows-1252 bytes serve the first four; Cyrillic uses 1251 and Greek uses 1253.
Stored names retain their exact bytes, including accepted whitespace; the CLI
converts Unicode names strictly to the database code page. Creation uses General. Unsupported controls, leading ASCII spaces and `. ! [ ]` or backtick
are refused. Relationship names share the 63-byte usable index-name limit.
Other name encodings and index key types remain restricted; relationship
forms are described under existing schema edits.
Empty OLE payloads store null. Text/Memo columns can independently
allow present-empty values, including later indexed tables and chained column
properties. Required column constraints are encoded and enforced on initial rows,
insertion and replacement, including Boolean and AutoIncrement exceptions and
empty Binary/OLE normalization. Read-only validation checks Required nulls and
named Boolean property framing. EXP-0283/0284 cover native discovery and
72 creation plus 204 mutation comparisons, including 70 expected refusals.
Fixed Text retains its exact-width input contract. Creation persists disabled
empty-value properties. Legacy mutation preserves absent and partial Boolean
property semantics: empty Text/Memo is refused only when AllowZeroLength is
explicitly false, independently of Required. EXP-0285 accepts 252 same-input
mutation pairs, including 26 refusals, across Text, FixedText and Memo.
Existing-table schema changes and table/relationship dropping are available
through the schema-edit API described below. EXP-0239 adds explicit, negative and
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

EXP-0277/0278 establish CP1252 name ordering, collisions, whitespace and byte
boundaries. Twelve creation pairs and 48 mutation pairs cover accented names,
column properties, a Long relationship, seeded catalogs and all 32 indexes.
Complete values, schema, traversal/Seek, physical keys, allocation and preserved
system storage agree. Eight constraint refusals preserve the Rust inputs;
57 CLI name refusals create no output. The seeded writers may choose different
object IDs and catalog row locators while storing equal catalog key bytes.
Other name encodings remain separate work.

EXP-0279/0280 add later and nullable unique parent selection, existing child
index reuse and native aliases sharing a physical tree. Forty creation pairs and
72 mutation pairs match complete schema, values, traversal/Seek, physical keys,
counters and allocation. Sixteen Rust refusals preserve the whole input. Native
orphan refusals retain the observed finite counter side effect. Memo replacement
may choose a different valid payload page while preserving complete values,
ownership and unrelated storage. That comparison exercises at most two
relationships per database.

EXP-0281/0282 extend creation to larger graphs within the per-table logical-index
limits. Twenty complete creation pairs cover up to 32 relationships, branched
relationship catalogs, 31-child graphs and 15 self-references. Eighty lifecycle
pairs cover shared keys, cycles, capacity boundaries and payload changes: 60
successes and 20 expected refusals. Actual property-collection getters were
compared across all 170 initial/output images, alongside complete values,
indexes, counters, allocation and unrelated storage. This is finite evidence;
these runs do not cover other key types, cascades or existing-schema relationship edits.

EXP-0286/0287 add descending-only parent indexes, nullable parent mutation
boundaries and physical-order self-reference checks. Eighteen creation pairs
and 84 same-input lifecycle pairs (40 successes, 44 refusals) compare complete
DAO getters, rows, traversal/Seek, raw keys, schema, maps and retained counters.
Native refusals may change exact historical counters and a header byte; Rust
refusals preserve their entire inputs. Generated parent trees share the existing
relationship counter rule while retaining declared descending indexes.

EXP-0288/0289 extend the finite relationship comparisons to all twelve supported
scalar key types, compatible Text/Binary widths and fixed/variable Text, 255-byte
keys, Boolean-null normalization and explicit IEEE signed zeros. Thirty-eight
creation pairs and 494 mutation pairs (350 successes, 144 refusals) compare full
DAO getters, rows, traversal/Seek, raw keys, counters, maps and system storage.
FixedText inputs use explicit width padding. Rust refusals preserve their entire
inputs; native refusal effects are checked separately. Composite keys, cascades
and relationship schema edits are outside those runs.

### Existing schema edits

`edit_schema` and `jet3-cli schema` apply one atomic operation to an existing
file: table creation/rename/drop, column append/rename/drop, Required and
AllowZeroLength changes, index creation/rename/drop/replacement, or
relationship creation/drop/replacement. Index and relationship replacement
retain the original when the replacement fails. Enforced relationship edits
include ordered scalar/composite keys, shared indexes and cascade settings
within the existing creation bounds. Unenforced relationships (EXP-0301) are
catalog rows only: any existing columns, no indexes, cascades or key checks,
and row writes ignore them while enforced relationships on the same tables stay
checked. Access join types (inner, left, right, both) are stored in the
attributes of either form and never affect integrity; database creation accepts
join types but not unenforced relationships. Parents referenced by an enforced
relationship from another table cannot be dropped; dropping a table otherwise
removes its relationships. Indexed columns and columns named by any
relationship cannot be dropped.

`DatabaseReader::relationship_catalog` lists every catalogued relationship with
its raw and decoded attributes. One-to-one (`dbRelationUnique`) relationships,
the undocumented attribute 65536 and other unknown bits are read, validated as
uninterpreted and preserved, but row writes to their tables and dropping those
tables refuse. DAO refuses enforced relationships over Memo/OLE keys,
non-unique parents or mismatched types, cascades on unenforced relationships and
the Inherited attribute on local tables; Rust refuses them too. DAO cannot
rename a relationship or change its attributes after creation, so neither can
Rust; replacement is the supported change. EXP-0302 accepts 50 DAO pairs (a join-type
creation, 40 edits and nine byte-exact refusals) over these forms, five
separately checked refusals, raw preservation of all accepted edits, reader
agreement for 292 relationships and byte-identical replay of the accepted
outputs of #371 through #381.

Column deletion retains the surviving storage IDs and existing row bytes.
Public column ordinals remain dense positions in the live definition. Appended
fields read as null on old rows (False for Boolean); AutoIncrement appends
backfill 1 through N and continue at N+1. Required and AllowZeroLength edits
preserve existing null/empty values and constrain subsequent assignments.
Read-only validation can therefore report an old value that violates a newly
set Required property. Removed index/payload pages are released without
zeroing their contents, and unused map rows become tombstones.

EXP-0297 records native layouts, dependency refusals and prospective property
behavior. EXP-0298 accepts 44 native/candidate schema pairs (38 edits and six
byte-atomic Rust refusals), complete DAO snapshots of all 88 outputs, raw checks
of all 132 input/output images and five successful native continuation lineages.
Independent review and `just ready` passed on the final production source.
In-place column type/size and index-option assignments are refused by DAO;
index options can be changed by atomic replacement, while column conversion is
outside this API. Wider preservation/release gates remain open.

### Column and table text properties

Creation, `CreateColumn`/`CreateTable` and `SetColumnProperties`/`SetTableProperties`
store column DefaultValue, ValidationRule, ValidationText and Description and
table ValidationRule and ValidationText as opaque database-code-page text of 1 to 2,048 bytes
without NUL. The library reads them through `DatabaseReader::table_properties`.
One lossless LvProp model serves reads, creation and every property edit, retaining
unknown blocks, records and dictionary names byte-for-byte. Property payloads
above 1,776 bytes are chained, as DAO stores them. EXP-0299 records the native
layouts; EXP-0300 records the storage limit and the DAO acceptance: 42 creation
pairs, 36 edit/refusal pairs and byte-identical replay of the earlier schema-edit
and property outputs. One placement-dependent LvProp available-map difference
(`c21`) is retained.

Jet expressions are not parsed or evaluated. DAO refuses malformed expressions on
assignment; Rust stores what the caller supplies. Defaults are never applied: rows
store the supplied values, including explicit nulls, as DAO does. While a table or
any of its columns stores a nonempty ValidationRule, inserts, row replacements,
field updates, cascaded child updates, AutoIncrement column backfills and
creation with initial rows are refused with the file unchanged. Clearing the rule restores writes. Binary, OLE and GUID
columns refuse validation properties, and new AutoIncrement columns refuse
expressions that DAO would drop. Access-layer properties such as Format, Caption
and InputMask are preserved but cannot be authored.

General, Nordic, traditional Spanish, Dutch, Cyrillic and Greek database headers
and column contexts are interpreted (EXP-0309). Existing-database row and schema
edits maintain the corresponding Text, catalog-name and relationship keys.
Undefined code-page bytes are refused without replacement. Other sort orders
remain read-only; new database creation uses General. In-place type/size changes
and Memo/OLE indexes remain outside v1 writes.
EXP-0309/0310 covers the existing six-locale inventory: 9,042 native Text keys,
1,374 complete catalog-name keys, 72 same-input edit/refusal pairs and six native
continuation pairs. Complete properties, values, ordered index traversals, keys,
ownership, payloads and unrelated storage compare. Twelve Rust refusals retain
their entire inputs; native header/primary-counter residues are checked exactly.
The recorded allocation-placement differences remain explicit. Broader locale
choices, including East Asian encodings, are outside this delivery.

New catalog objects, including replacement relationships, use the existing
deterministic zero-date writer policy. Surviving object timestamps are retained.

### Updates

Public APIs implement bounded field updates, insertion into populated pages or
one EOF data page or a released target-table page, deletion/compaction,
last-live-row page release, row replacement with stable logical locators, independent Memo/OLE
payload mutation, and multi-level index maintenance. A table may
have up to 32 indexes with one to ten Boolean, Byte, Integer, Long, Currency,
Single, Double, Date, Binary, fixed/variable Text or GUID components, including mixed
directions, duplicates and null policies. Text keys use the six observed
single-byte collations, retaining stored row bytes while ignoring trailing ASCII
spaces in keys. Spanish CH/LL pairs contract; the recorded locale expansions and
accent weights retain their native ordering. Other collations remain outside
indexed mutation. Fixed Text values must
contain exactly the declared number of bytes. Rebuilt trees keep their roots,
reuse reserved index pages, and append
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
IDs and allocation state. Single-field updates support null, Boolean, variable
Text/Binary and Memo/OLE edits while retaining unassigned field bytes and payload
descriptors. A payload-only field edit does not reassign a referenced parent key.
The CLI exposes both field updates and full-row replacement. Publication
supports Unix and Windows; Windows flushes the file before publication without
a separate directory-sync guarantee.

Tables participating in enforced, ordered scalar/composite
relationships support these row mutations, including multiple constraints,
self-references, nullable foreign keys and Memo/OLE payloads. All reciprocal
records and the relationship catalog must agree; existing orphan keys and
damaged indexes are refused. Nullable unique parent keys may contain multiple
nulls, but a null child blocks changing or removing a null parent unless that
child reference is updated or removed in the same operation. Ordinary logical aliases
may share one physical tree, which is maintained once. Every child key with at
least one non-null component must occur in its parent. Explicitly assigning a
referenced parent field is refused even when its value is unchanged unless cascade
updates are enabled. A full-row
self replacement excludes its own child reference from this assignment guard
when the parent physical tree precedes the foreign tree; other referencing rows
still block it without cascade updates (EXP-0292). Self-linked insertions and full-row replacements check
the resulting rows. When the foreign physical index precedes
the parent index, the child key must also exist before the operation (EXP-0286).
This follows physical update order for both declared and generated indexes.
Self-deletion checks the remaining rows. Shared foreign
physical indexes are updated once. Cascade updates assign matching child tuples;
cascade deletes remove matching children recursively. Selection uses original
complete tuples, including partial-null and all-null keys. Explicit root foreign-key
assignments take precedence during full-row self replacement. The complete
connected result must satisfy every enforced relationship, including shared-key
constraints. All affected rows, indexes, payloads and allocation maps publish in
one atomic file replacement; a pre-publication failure preserves the entire source.
EXP-0294/0295 record the native option flags, chain/shared-key behavior, null-tuple
selection, explicit self-replacement precedence and retained index counters.
EXP-0296 accepts the complete 166-pair candidate comparison. Ten successful
operations have bounded selected payload descriptor/allocation differences;
cascade deletion can clear freed payload bytes that DAO leaves stale. Complete
payload values, ownership/reachability, all unassigned descriptors/maps and
system/catalog bytes remain checked. Refused Rust operations preserve every
input byte while native refusal bookkeeping is checked separately.

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

The read-only library and CLI validator walk catalogued user and system tables with one
shared resource budget: definitions, declared row counts, decoded values,
long-value chains and physical index traversal. Every leaf reference must name
a distinct live logical row in its table. Supported scalar key schemas also
check row/key equality, complete key inventory, null policies, uniqueness and
branch bounds; reports count verified and uninterpreted indexes separately.
All catalogued table definitions, table/index/payload ownership maps and their
metadata must remain disjoint from incompatible owners and globally free pages.
Availability maps must be subsets of their own ownership maps; every traversed
index page must belong to its physical index. Overflow storage and live
Memo/OLE fragments must be uniquely reachable through the proper table or
column, including rejection of hidden orphan rows and unreferenced payloads.
Enforced ordered scalar/composite relationships check reciprocal
metadata and cascade options, parent uniqueness and every child key with a non-null component.
Each composite relation has a complete, uniquely numbered central row per component. Self-references and
multiple constraints are checked separately, including shared foreign indexes.
Unenforced relationships must name existing tables and columns; their keys are
not checked. Unsupported relationship catalog rows are counted explicitly;
complete endpoint inventory is checked only when every central row is interpreted. Known endpoints
must still occur exactly once when other forms are present.
User-table catalog properties decode Required and AllowZeroLength records and
reject malformed or unsupported framing. Other property values remain opaque.
Non-table contents, unreferenced file
pages and unsupported index key schemas remain outside these checks. Catalog
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

EXP-0264 establishes fixed Text keys using the existing English-US/CP1252
collation. EXP-0265 accepts 23 pairs (46 captures) for widths 1, 8, 32 and 255:
creation, field/full-row changes, deletion/reinsertion, native successors,
Rust edits to native inputs and unique Text refusals. Complete values, schema,
traversal/Seek, physical keys, counters and Notes preservation agree. Rust
refusals leave the complete file unchanged; DAO failed insertion can advance a
historical index counter while preserving rows. Fixed Text callers supply the
exact declared width. Other collations and empty/all-space Text are outside
this comparison.

EXP-0266 establishes named per-column AllowZeroLength properties and empty-value
storage. EXP-0267 accepts 23 pairs (46 captures) across first/later mixed tables,
a 35-column table with chained properties, and unique empty Text keys. Creation,
insert/update/delete, external-payload clearing, native successors and Rust edits
to native inputs preserve complete values, schema, physical indexes, allocation,
column properties and unrelated Notes. Empty Text/Memo requires the column's
option; empty OLE stores null. Disallowed empty values and duplicate unique keys
are refused before Rust publication. DAO may change internal bookkeeping on a
failed write; Rust refusal preserves the whole file. Other property grammars,
Required/default/validation options and other collations remain outside this batch.

EXP-0268 establishes two-word foreign-index bookkeeping and enforced Long
relationship mutations. EXP-0269 accepts 34 lifecycle/continuation pairs
(68 successful captures), plus 24 native refusal captures. Nullable FK changes,
equal assignments, branched indexes, Memo/OLE replacements, ordered parent/child
deletion and Rust edits to native successors preserve complete values, schema,
keys, counters, allocation, logical locators and unrelated Notes. Fourteen Rust
refusals preserve their complete input. Multiple, composite, cascading and
self-referencing relationships remain outside this comparison.

EXP-0270/0271 adds richer two-table relationship creation: a separate child
primary, generated IDs, nullable foreign keys, independent payloads and column
options, including chained definitions/properties and map rows on multiple pages.
Ninety successful captures cover creation, insert/replace/delete, native
successors and Rust mutations of native controls. Complete values, captured DAO
schema properties, traversal/Seek, physical keys, per-lineage counters and
ownership agree. Thirty-six DAO refusals return the expected errors and 36 Rust
refusals preserve the whole input. Broader relationship forms remain open.

EXP-0272 checks preservation of four saved QueryDefs on six native-created
relationship databases before and after Rust and DAO row mutations. Complete
SQL, properties, parameters and dates remain unchanged, as do every covered
MSysQueries/MSysObjects page and its membership. The 90 stage/successor captures
include duplicate Rust labels; there are two meaningful mutation lineages per
source. Thirty-six DAO refusal captures retain the same query definitions and
storage, and 36 Rust refusal records preserve their whole input. The suite
retains the full relationship, schema, value, index and allocation comparisons
from EXP-0271. Query execution and action/crosstab/DDL queries are not covered.

EXP-0303/0304 extends preservation to eleven QueryDefs, adding union, crosstab,
update, append, delete, make-table and DDL forms. Their exact catalog kind/flag
pairs are admitted without interpreting or executing the queries. Six native
sources cover wide rows, four independent Memo/OLE columns and sparse
AutoNumber schemas through deletion, refill, repeated growth, shrinkage,
complete release and reinsertion, followed by native continuations. Complete
query SQL, parameters, properties and dates, database/table/field custom
properties, unrelated system/user storage and surviving unassigned row bodies
remain intact. This includes Text keys crossing the 255-byte encoded-key bound.
Native duplicate and Required-column failures and explicit rollback controls
are checked independently. Payload-schema refusals retain a header marker;
AutoNumber duplicate refusals also advance the generator, including after
rollback. Rejected relationship creation leaves the same six
catalog/header bytes changed even after explicit rollback; these files remain
terminal controls that fail strict validation. Rust refusals remain byte-exact.
The finite observations add no live-slot-reuse or multi-hop overflow support;
the rejected EXP-0276 representation remains excluded.

EXP-0273/0274 extends mutation comparisons to five graph shapes, a shared-FK
physical index, atomic self-references and Memo growth/shrink/Null transitions,
each replicated twice. The portable evaluator compares 92 complete Rust/DAO
snapshots, raw keys and locators, both prefix words, allocation and payload
reachability, and unchanged schema/properties/system catalogs. It derives 28
byte-exact Rust constraint refusals and eight additional native refusal results.
Native successors pass from both lineages. The finite cases exercise at most
two simultaneous constraints on an endpoint; relationship creation/drop and
composite/cascading relationships remain separate work.

### Remaining work

EXP-0275 accepts 30 initial relationship-graph creation pairs and 168 native
lifecycle stage captures, with 136 expected refusals and 564 successful native
requests. Multiple-parent/child, shared-index, self-reference, AutoNumber and
27-table catalog-boundary cases retain complete values, schema, properties,
keys, allocation and the recorded counter behavior. Referenced chain-middle
rows use native FK/Memo field edits: DAO rejects a full-row edit that reassigns
their unchanged primary key. That comparison exercises at most two constraints
per database.

- Extend creation to remaining schema/index-key combinations and unenforced
  relationships (available through schema edits).
- Extend updates to remaining index key types/collations, one-to-one relationships,
  additional payload/schema combinations, broader
  data-page/live-slot reuse and multi-hop row growth.
- Cover remaining DAO inventories, additional saved-query/object forms and
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
