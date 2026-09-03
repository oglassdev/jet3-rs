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

The two format gaps that needed preregistered DAO validation before the planner
could widen now have bounded evidence: definition-continuation placement
(#151) and catalog name keys for bytes above `0x7E` (#152).
`EXP-0094` contains the first SHA-256-pinned #151 preregistration. `EXP-0095` records its
canonical `no_outcome`: every replica reached capture of the 69-field arm, but
that arm failed the combined 2-KiB geometry/64-page bound and could not be
retained. No continuation placement was observed, and the 70- and 140-field
DAO table appends were not attempted. The SHA-256-pinned `EXP-0098` successor
kept the exact scenarios and questions while raising completed checkpoints to
256 pages. `EXP-0100` records its valid `no_outcome`: all three producers
completed all four checkpoints without recovery, but the analyzer found one
continuation page in the 2,046-byte `zero` control where the preregistration
required zero. The diagnostic one-, one-, and two-continuation chains are not
promotable under the all-or-`no_outcome` decision. `EXP-0102` preregisters the
next successor with the established `Alpha(Id Long)` 66-byte, zero-continuation
control and the unchanged 2,075- and 4,105-byte wide targets. `EXP-0103`
records its single authorized run as a valid `no_outcome`: all three producers
completed every checkpoint and baseline without recovery, but every analyzer
replica reported `one appended page 22 is unattributed`. No continuation count
or placement diagnostic is promoted. `EXP-0104` preregisters a successor with
the exact same producer, schemas, counts, and bounds. It changes only the
analysis rule: every appended page must still have a decoded page-role record,
but a replica-stable explicit `unassigned` record is reported with its raw tag
and decoded global-map free status instead of forcing `no_outcome` when the
global map marks it free. An in-use `unassigned` page or globally-free definition
page remains
`no_outcome`. Every globally-free page's decoded role and owners describe only
retained bytes and establish no current owner, purpose, reuse history, or
semantic role. Every catalog `LvProp` referenced appended LVAL page must be
globally in use; a decoder-labeled but unreferenced LVAL page may be globally
free and remains a bounded retained-byte observation. A referenced globally
free LVAL page is `no_outcome`. Page-zero and catalog-root correlation remains
observational; the exact user-table/root resolution is already enforced. The `EXP-0103`
diagnostic is design input only and promotes no page-role fact. `EXP-0105`
records the exact merged successor run as accepted with all five questions
answered identically across three complete replicas. The exact chains are
`[20]`, `[20, 68]`, and `[20, 219, 218]`, with logical chunk lengths `[66]`,
`[2048, 27]`, and `[2048, 2040, 17]`. All definition pages are globally in use.
The wide arms also retain globally free `unassigned` tag-9 pages and globally
free decoder-labeled LVAL ranges unreferenced by catalog `LvProp`; those labels
establish no current owner, purpose, reuse history, or semantic role. Issue
#151 is evidence-complete for the exact preregistered shapes; the result does
not establish general placement, allocation policy, or chains longer than two
continuation pages. #150 is evidence-complete. The bounded implementation now
corrects the one-index page order, supports up to three physical and logical
indexes, and refuses continuation placement because DAO's allocation policy is
still underdetermined. Issue #178 preregisters exact indexed and compact
one-continuation null-`LvProp` candidates against fresh same-schema controls.
It can answer only those two construction questions. #102 remains the separate
hosted write differential after database creation is complete.

`EXP-0096` is the first SHA-256-pinned #152 preregistration. `EXP-0097` records
its canonical `no_outcome`: all 2,358 DAO name attempts reported creation and
the bounded DAO metadata agreed, but every replica failed catalog-key decoding
at the former `reject` checkpoint under the all-arm decoder. The successor
renames that checkpoint `controls`. The defined arms cannot be promoted post hoc
and establish no catalog-name mapping or weights.
`EXP-0099` preregistered a successor that left the 41 defined-byte arms
unchanged, validated U+007F and the five undefined-slot Unicode values only at
the BSTR/DAO metadata boundary, and never decoded that controls arm. It also
required exact non-ASCII JSON transport and compared exact question-bearing
collation facts across replicas while retaining incidental locators separately.
`EXP-0101` records the authorized successor as `accepted`: all 123 defined
bytes and all six exact forms were observed, the exact primary and secondary
nibble results and composition predicates agreed across three replicas, and all
seven metadata-only controls were accepted at the Unicode BSTR/DAO metadata
boundary. This resolves #152's bounded six-form evidence question, but does not
unblock general planner widening. Only singleton positions, repeat, and each
byte's registered adjacent defined neighbor were tested, and secondary
observations were sometimes noncompositional. Any implementation derived now
must retain the current blanket rejection or fail closed to exact evidenced
contexts and contexts supported solely by a positive composition result for
that tested byte or pair. Arbitrary names, more than two non-ASCII bytes, and
untested pairs or contexts need more evidence. No general collation, writer
correctness, public compatibility, or support-matrix movement is established.
