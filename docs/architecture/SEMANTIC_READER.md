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
   and the retained page-zero read; and
6. can read a complete page into caller-owned fixed storage and classify only
   its byte-zero tag in page-number context (`SRC-0020`).

Success establishes only this narrow structural envelope. It does not identify
the Jet generation, prove that the input is unencrypted, validate any page
header or payload beyond the experimental byte-zero tag, validate database
allocation state, locate a catalog, or establish compatibility. Unknown and
contextually unsupported tags are retained as successful `Unknown(u8)`
classifications. In
particular, `EXP-0018` observed offset `0x041` under one DAO campaign but
explicitly assigned it no physical meaning and reported an inconclusive
scientific outcome.

The physical layer also has an internal-only detached usage-map decoder based
on `SRC-0020`. It accepts only caller-supplied record or page bytes; it is not
wired into `DatabaseReader` and does not locate, dereference, or traverse a map
inside a database. Consequently, decoding a detached bitmap does not establish
that any database page is globally allocated, owned by a table, or available
for insertion.

## Planned dependency sequence

Each stage consumes only typed output from the stage above it. High-level code
must not reach around these boundaries to decode numeric offsets directly.

| Stage | Intended output | Evidence gate before implementation | Required safety boundary | Present state |
| --- | --- | --- | --- | --- |
| 0. Bounded opening | `DatabaseReader<S>`, captured geometry, retained page zero | Existing `SRC-0004`, `SRC-0005`, and `SRC-0013` | Input, single-read, total-read, page-visit, and total-work limits | Implemented internally; no compatibility claim |
| 1. Page classification | `PageKind` plus a borrowed `ClassifiedPage` over one complete fixed page | `SRC-0020` for byte offset zero and tags `00` through `05`; no other header field or validity rule is claimed | One fixed page per decode; one page visit per source read; one explicit classification work unit | Implemented experimentally/internal-only; unknown tags remain lossless; retained classifier run at commit 0a48b190ffb3211e3e1fd1f0483327b507d15136 over FIX-0001..FIX-0004 (`docs/validation/stage1-classifier-snapshot.json`); not DAO-verified |
| 2. Allocation and usage | Bounded iterators over allocated/owned page references | `SRC-0020` supports detached type-0/type-1 records and complete Jet 3 type-`05` bitmap pages only; map locations, pointer rules, extended bases, and traversal still require physical evidence | Checked arithmetic and item charging for detached decoding; future traversal additionally requires checked page references, cycle detection, chain-depth limits, and bounded visited state | Detached primitives implemented experimentally/internal-only; database traversal remains blocked |
| 3. Catalog bootstrap | Streaming catalog records sufficient to locate user objects | Provenance for catalog root/location, record layout, object kinds, identifiers, and name encoding | Allocation charged before buffers/sets; count and page limits; no recursive traversal | Blocked on physical evidence |
| 4. Table definitions | Immutable typed definitions for columns, indexes, and row sources | Provenance for table-definition pages/records, field types, flags, sizes, and referenced roots | Checked counts/offsets, per-value bounds, cumulative allocation and item work | Blocked on physical evidence |
| 5. Row streaming | A fallible iterator yielding one borrowed or bounded row at a time | Provenance for row directories, deleted/null state, fixed/variable regions, and overflow links | No whole-table collection; row/page/chain limits; cycle rejection; bounded scratch storage | Blocked on physical evidence |
| 6. Value streaming | Typed values plus lossless raw representations where required | Provenance for each physical type, byte order, text/code-page rules, and long-value representation | Per-value and cumulative decoded-byte limits; long values streamed across bounded chains | Blocked on physical evidence |

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
  It must not locate maps, follow type-1 pointers, derive extended-map page
  bases, or interpret catalog and row payloads without new provenance.
- `catalog.rs`: future bootstrap and object-record stream. It consumes only
  pages admitted by the allocation and page-class layers.
- `table_definition.rs`: future immutable schema definitions. It must preserve
  unknown sourced fields required for lossless behavior without assigning
  unsupported meaning.
- `row.rs`: future streaming row directory and row view. It owns row-local
  structural checks but delegates physical values.
- `value.rs` and `long_value.rs`: future bounded scalar and chained-value
  decoders. Text decoding must preserve raw bytes when a lossless conversion
  cannot be proven.

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

- a Jet 3 version discriminator or a physical unencrypted-state check;
- any page-header field or validity rule beyond the experimental byte-zero tags
  recorded in `SRC-0020`;
- allocation/usage map locations, encodings, ownership, or continuation rules;
- the catalog root, catalog record representation, or object-kind encoding;
- table-definition record fields, physical field-type values, or index roots;
- row directories, null maps, fixed/variable field boundaries, deleted-row
  markers, or overflow-row pointers;
- scalar value encodings, text/code-page selection, date/currency/GUID rules;
  or
- Memo/OLE/long-value pointers, fragments, and chain termination.

`SRC-0020` supplies only the narrow Stage 1 byte-zero mapping and is a
reverse-engineered secondary documentation lineage, not independent
corroboration. It does not unblock allocation or later stages. `SRC-0007` names
several physical concepts but expressly publishes none of their binary
encodings. M4's validated bundle does not fill those gaps. M5 may
test a preregistered discriminator hypothesis, but until execution and
independent validation it is not evidence, and even a decisive discriminator
result would not establish page, allocation, catalog, row, or value layouts.

These are research blockers, not validator defects. Acceptance must remain
blocked where the required format evidence, parser, independent check, or DAO
differential result is absent.
