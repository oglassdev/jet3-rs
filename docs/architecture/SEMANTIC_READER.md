# Semantic reader architecture

This document defines the staged path from the current bounded database-opening
boundary to a streaming semantic reader. It is an architecture contract, not
format evidence. A stage may be implemented only from format facts already
recorded in `docs/PROVENANCE.md`; otherwise it remains blocked and fails closed.

## Current boundary

`DatabaseReader` is the only public composition boundary today. It owns a
captured-length `ReadAt` source and, using one caller-owned `ResourceBudget`:

1. enforces the configured input-length policy;
2. recognizes a generic Microsoft-published Jet signature (`SRC-0004`);
3. requires exact 2 KiB page geometry (`SRC-0005`);
4. reads one complete database-header page, which is the first database page
   (`SRC-0013`); and
5. rejects a signature classification that changes between the initial window
   and the retained page-zero read;
6. requires the exploratory Jet 3, unencrypted, no-password opening state from
   `EXP-0056`; and
7. can read a complete page into caller-owned fixed storage and classify only
   its byte-zero tag in page-number context (`SRC-0020`); and
8. exposes a bounded, fallible `owned_pages` iterator from a caller-supplied
   table-definition root (`EXP-0057`); and
9. exposes a bounded, fallible `catalog` cursor that discovers its root without
   assuming an absolute page and preserves raw object-name bytes (`EXP-0058`);
   and
10. follows catalog-supplied table-definition chains into immutable typed
    column, physical-index, logical-index, and minimum relationship metadata,
    classifying but never traversing index roots (`EXP-0059`); and
11. streams table-owned primary rows as borrowed `RowView` values, follows the
    observed overflow pointer form, and exposes validated lossless raw field
    slices (`EXP-0060`); and
12. decodes the closed observed scalar inventory, explicitly selected CP1251 or
    CP1252 text, and inline or externally streamed Memo/OLE values while
    retaining sourced bytes (`SRC-0025`, `EXP-0061`); and
13. traverses a selected physical index iteratively with checked branch, leaf,
    sibling, prefix, key, row, and child references, while exposing isolated
    relationship cascade flags (`EXP-0062`).

Success establishes only this narrow opening envelope. It identifies the
exploratory Jet 3, unencrypted, no-password discriminator tuple, but does not
validate the rest of page zero, any page header or payload beyond the
experimental byte-zero tag, database allocation state beyond table ownership,
automatic database code-page selection, or compatibility. Unknown and contextually unsupported tags are
retained as successful `Unknown(u8)` classifications. `EXP-0056`, `EXP-0058`,
`EXP-0059`, `EXP-0060`, `EXP-0061`, and `EXP-0062` are local development
evidence and do not revise the inconclusive official `EXP-0018` result or
advance a release claim.

The physical layer composes the detached `SRC-0020` usage-map primitives with
the development-only facts in `EXP-0057`. From a caller-supplied table root it
locates the owned-map row, validates its reverse-packed data-page boundary,
follows a bounded prefix of direct type-`05` references, and derives extended
pages from the checked slot-relative base. Catalog discovery considers only
allocation-admitted pages, requires a unique self-identifying `MSysObjects`
candidate, and streams validated active records while rejecting duplicate
object identifiers and invalid table-definition references. Table-definition
decoding follows an iterative, cycle-checked continuation chain; admits the
closed observed type/class combinations; preserves raw names, contexts,
records, and suffix bytes; and validates usage-map, index-root, and
related-TDEF page kinds. Index traversal separately retains raw keys, follows
branch children iteratively, and validates exact depth, sibling, owner,
boundary, and reference invariants. It does not report global allocation state
or select pages for insertion. Row streaming retains one fixed page plus
charged locator scratch, validates row directories and fixed/variable/null
boundaries, skips deleted and hidden storage rows, and follows overflow links
iteratively with owner, kind, cycle, and resource checks. The observations and
implementation remain internal-only and do not establish DAO compatibility.
Value decoding preserves the exact physical field or long-value fragment,
charges decoded output through the same operation budget, requires an explicit
supported text code page, and streams external long values through the row
cursor's single page buffer. It does not infer a database code page or collect
a complete external Memo/OLE value.

## Planned dependency sequence

Each stage consumes only typed output from the stage above it. High-level code
must not reach around these boundaries to decode numeric offsets directly.

| Stage | Intended output | Evidence gate before implementation | Required safety boundary | Present state |
| --- | --- | --- | --- | --- |
| 0. Bounded opening | `DatabaseReader<S>`, typed supported format, captured geometry, retained page zero | `SRC-0004`, `SRC-0005`, `SRC-0013`, and exploratory `EXP-0056` | Input, single-read, total-read, page-visit, and total-work limits | Implemented internally for Jet 3, unencrypted, no-password inputs; no structural-validity or compatibility claim |
| 1. Page classification | `PageKind` plus a borrowed `ClassifiedPage` over one complete fixed page | `SRC-0020` for byte offset zero and tags `00` through `05`; no other header field or validity rule is claimed | One fixed page per decode; one page visit per source read; one explicit classification work unit | Implemented experimentally/internal-only; unknown tags remain lossless; retained classifier run at commit 0a48b190ffb3211e3e1fd1f0483327b507d15136 over FIX-0001..FIX-0004 (`docs/validation/stage1-classifier-snapshot.json`); not DAO-verified |
| 2. Allocation and usage | Bounded iterators over allocated/owned page references | `SRC-0020` supplies detached type-0/type-1 and type-`05` shapes; exploratory `EXP-0057` supplies table-map locators, direct reference semantics, and the slot-relative extended base | Checked references and arithmetic; exact item/read/visit/depth charging; cycle and self-reference detection; pre-charged bounded visited state | Implemented internally for owned pages from a caller-supplied table-definition root; malformed directories, null-slot violations, cycles, self-references, arithmetic overflow, and out-of-capture references fail closed; no catalog discovery, global allocation state, write allocation, DAO verification, or compatibility claim |
| 3. Catalog bootstrap | Streaming catalog records sufficient to locate user objects | Exploratory `EXP-0058` supplies a dynamic root discriminator, the minimal active-record fields, table-definition references, and raw name/code-page context | Allocation charged before buffers/sets; exact count and page limits; no recursive traversal | Implemented experimentally/internal-only; active records stream from allocation-admitted pages, raw names remain lossless, malformed directories/records/references and duplicate identifiers fail closed; no table-definition decoding, DAO verification, or compatibility claim |
| 4. Table definitions | Immutable typed definitions for columns, indexes, and referenced roots | Exploratory `EXP-0059` supplies TDEF chains, counts, column records, definition-only index records, and minimum relationship references | Checked counts/offsets/references; iterative cycle-bounded chains; cumulative allocation and item work; index roots classified but not traversed | Implemented experimentally/internal-only; unknown sourced bytes remain lossless; individual cascade semantics, DAO verification, and compatibility remain open |
| 5. Row streaming | A lending `RowCursor` yielding one borrowed `RowView` at a time | Exploratory `EXP-0060` supplies row directories, deleted/hidden state, fixed/variable/null boundaries, and the observed overflow pointer | No whole-table collection; one retained row page; charged locator scratch; row/page/chain limits; owner/kind/cycle rejection | Implemented experimentally/internal-only for direct rows, short variable layouts, and the observed one-variable wide layout; wider multi-variable rows, value interpretation, DAO verification, and compatibility remain open |
| 6. Value streaming | Typed values plus lossless raw representations where required | `SRC-0025` supplies CP1251/CP1252 mappings; exploratory `EXP-0061` supplies scalar byte order and the observed long-value forms | Per-value and cumulative decoded-byte limits; long values streamed across bounded chains | Implemented experimentally/internal-only for the closed type inventory, explicitly selected CP1251/CP1252, and observed inline/single-page/chained long values; automatic code-page selection, DAO verification, and compatibility remain open |
| 7. Index traversal and relationships | Ordered lossless keys, row locators, typed relationship options, and checked node metadata | Exploratory `EXP-0062` supplies branch/leaf layout, boundary bitmap, sibling/child links, supported single-field key shapes, and isolated cascade bytes | Iterative breadth-first traversal; checked references and uniform leaf depth; cycle, repeat, self-link, allocation, item, page, work, and depth limits | Implemented experimentally/internal-only; composite and unsupported key bytes remain lossless; GUID key bytes, write allocation, DAO verification, and compatibility remain open |

Page classification must precede allocation parsing: allocation logic cannot
use an inferred page type. Allocation must precede catalog traversal: merely
finding bytes that resemble a catalog record cannot establish ownership or a
valid page reference. Table definitions must precede rows, and rows must expose
validated field boundaries before any value decoder runs.

## Proposed module boundaries

Names below are reserved architectural roles, not authorization to create empty
or speculative modules.

- `database.rs`: public operation boundary and stage composition. It owns no
  physical constants.
- `page_kind.rs`: experimental byte-zero page classification and the borrowed
  complete-page view. Every format constant cites `SRC-0020`; the module may
  not inspect another header byte without new provenance.
- `allocation.rs`: detached usage-map record and extended-map-page primitives.
- `map_location.rs` and `usage_map.rs`: provenance-bound table-map locators and
  allocation-free reverse-packed row delimiting.
- `allocation_traverse.rs`: fixed-memory owned-page traversal, including direct
  type-`05` references, slot-relative bases, checked page bounds, and cycle
  limits. It must not interpret catalog or row payloads.
- `catalog.rs`: bounded bootstrap and object-record stream. It consumes only
  pages admitted by the allocation and page-class layers and owns no physical
  record offsets.
- `catalog_record.rs`: provenance-bound catalog directory and record decoding,
  typed identifiers/references, object classification, and lossless raw names.
- `column_definition.rs`: closed observed column types, storage classes, raw
  database-code-page names, and lossless column records.
- `table_definition.rs`: iterative TDEF-chain composition, immutable schema
  output, raw header/suffix retention, and referenced-page classification.
- `index_definition.rs`: physical/logical index records and typed, lossless
  relationship references.
- `relationships.rs`: allocation-free relationship inventory over logical
  indexes, including raw and isolated cascade-option bytes.
- `index_tree.rs`, `index_tree_page.rs`, and `index_tree_rows.rs`: bounded
  iterative index traversal, allocation-free page-layout validation, lossless
  keys, child/sibling references, and row locators validated against their
  owned data-page directories.
- `row_directory.rs`: provenance-bound data-page ownership and reverse-packed
  row delimiting, including deleted, overflow-pointer, and hidden-storage flags.
- `row.rs`: lending row cursor, iterative overflow resolution, validated row
  layout, and lossless raw field views. It delegates physical value decoding.
- `value.rs`: lossless scalar and short-value decoding from validated row
  fields, including exact Currency scale, OLE Automation day counts, and GUID
  display-byte ordering.
- `text.rs`: explicitly selected CP1251/CP1252 conversion with raw bytes beside
  decoded Unicode and structured rejection of undefined mapping bytes.
- `long_value.rs`: bounded inline decoding and lending external Memo/OLE
  fragment streaming with owner, directory, length, termination, cycle, and
  resource checks.

Modules must be split before 800 production lines. Format constants and checked
binary operations stay in their physical layer; orchestration receives typed
values. Production crates retain `#![forbid(unsafe_code)]`.

## Resource contract

One `ResourceBudget` is created by the caller for the complete public operation
and mutably borrowed through every layer. A nested parser must never construct
or reset a budget.

| Resource dimension | Charged before | Typical consumers |
| --- | --- | --- |
| Input bytes | accepting the captured source | database opening |
| Single and total read bytes | every source access | all physical layers |
| Page visits | every page traversal, including repeated references | classification, allocation, catalog, rows, long values |
| Chain depth | following a continuation edge | allocation, overflow rows, long values |
| Item work | count-controlled iteration | maps, slots, catalog entries, columns, rows |
| Allocation bytes | reserving vectors, sets, strings, or scratch buffers | visited state, definitions, bounded decoded data |
| Decoded value bytes | producing one value and cumulative value output | scalar and long-value decoding |
| Total work units | algorithmic work not already represented by another charge | validation and bounded searches |

All counts and sizes derived from input are converted with checked integer
operations and charged before allocation or iteration. Traversals are
iterative. Cycles are ordinary malformed-input errors, not termination by
stack exhaustion. A streaming API may retain one fixed page, one bounded row,
and explicitly charged traversal state; it may not collect a table merely to
provide iteration.

## Error contract

Every stage gets a non-exhaustive structured error enum that retains its
immediate typed source error and bounded numeric context. The intended nesting
is:

```text
DatabaseOpenError
DatabasePageError
  -> PageClassificationError
    -> AllocationError
      -> CatalogError
        -> TableDefinitionError
          -> RowError
            -> ValueError
```

This is causal nesting, not a requirement that every high-level error wrap all
lower layers. Errors distinguish malformed data, unsupported-but-recognized
structure, resource rejection, arithmetic/conversion failure, and I/O failure.
They must not copy attacker-controlled strings or byte ranges into diagnostics.
Unknown tags and flags retain only bounded numeric values and positions.
Panics, silent truncation, saturating acceptance, and generic string-only
errors are forbidden.

## Evidence gates

A physical stage advances only through all applicable gates:

1. Record the public source, controlled observation, or preregistered experiment
   in `docs/PROVENANCE.md` before or with the first dependent constant.
2. Preserve raw observations separately from interpretations. A repeated byte
   pattern or successful Rust self-read is not a format fact.
3. Add focused exact-boundary and corruption tests for every length, count,
   offset, reference, tag, flag, chain, and termination rule.
4. Add a dedicated registered fuzz target and manifested synthetic seeds for
   each parser named by G5. Fuzzing proves robustness only, not correctness.
5. Compare semantic output against an independently generated DAO snapshot at
   the exact clean pushed commit before changing a capability to DAO-verified.
6. Use an independent structural verifier for writer output; the reader can
   never validate its own writer into correctness.

Microsoft DAO is an optional Windows test oracle, never a production
dependency. No implementation code from another MDB reader may be inspected or
adapted. A black-box comparison cannot silently become physical-layout
evidence.

## Exact blockers

The current provenance does not establish any of the following:

- any additional database-header field or validity rule beyond the narrow
  exploratory opening discriminator in `EXP-0056`;
- any page-header field or validity rule beyond the experimental byte-zero tags
  recorded in `SRC-0020`;
- global allocation-map location and semantics, allocation choices for writes,
  and the meaning of available-map bits beyond the observed table behavior;
- catalog fields beyond the minimal active-record subset in `EXP-0058`, and
  semantics for catalog object kinds other than the observed table kind;
- table-definition fields beyond the `EXP-0059` records, unsupported
  type/class combinations, GUID key bytes, composite component boundaries,
  and index write/allocation rules beyond the `EXP-0062` traversal facts;
- the meaning of row-directory bit `0x2000`, wider layouts with more than one
  variable column, overflow representations beyond the observed slot-plus-u24
  pointer, or row insertion/update allocation rules;
- automatic database code-page selection, code pages beyond explicitly
  selected CP1251/CP1252, or proof that the diagnostic CP1251 database option
  changes Jet 3 physical bytes; and
- long-value storage forms beyond the observed inline, single-page, and
  slot-plus-u24 chained representations, universal inline/external thresholds,
  or write allocation rules.

`SRC-0020` is a reverse-engineered secondary documentation lineage, not
independent corroboration. `EXP-0057` through `EXP-0061` supply only the
narrow, development-only Stage 2 through Stage 6 facts listed above.
`SRC-0025` pins two public byte mappings but does not establish which code page
a database selected. `SRC-0007` names several physical concepts but expressly
publishes none of their binary encodings. The
independently validated A3 result does not fill the remaining gaps, and no
local exploratory result establishes DAO compatibility or release evidence.

These are research blockers, not validator defects. Acceptance must remain
blocked where the required format evidence, parser, independent check, or DAO
differential result is absent.
