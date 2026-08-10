# M5 compact-copy confirmation campaign, revision 2

M5 revision 2 is a bounded, descriptive, DAO-only experiment. It asks the
same scientific question as `DAO-M5-COMPACT-CONFIRM-001`: whether databases
produced by `DBEngine.CompactDatabase` from `DBEngine.CreateDatabase` sources
carry the same bounded-prefix byte values that M4 observes for the matching
documented destination version and encryption state, and whether any position
instead covaries with generation method. It assigns no physical meaning to a
byte, exercises no Rust reader, and establishes no compatibility.

The additive declarative plan is `m5-compact-confirm-r2.plan.json`; its stable
experiment ID is `DAO-M5-COMPACT-CONFIRM-002`. The original plan and README
remain immutable. `EXP-0015` records this revision, `SRC-0019` records the
numeric `dbDecrypt` API value, and the required revised M4 input is
`DAO-M4-HEADER-DISCRIMINATOR-002` under `EXP-0014`.

## Preregistration timing and revision boundary

This revision was preregistered after the bounded blocker observation in
`EXP-0013`, but before any revised-M4 byte result. The observation that the
licensed provider can leave a canonical 64-byte Jet 2 companion file justifies
only the orchestration revision below. It may not tune the factorial, samples,
schedule, analyzed ranges, comparisons, predicates, or outcomes. Those parts
remain semantically identical to the original M5 preregistration.

Any later change after revised M4 execution requires another additive plan and
provenance entry. Neither the original M4 failure nor a future revised-M4
result may be edited into this plan.

## Execution gate

The checked execution gate remains `BLOCKED` until all of the following exist
and pass:

- a checked M5 controller and isolated source, compact, and verify workers;
- checked M5 analysis and a complete-bundle validator;
- one complete passing `DAO-M4-HEADER-DISCRIMINATOR-002` bundle, bound by its
  bundle-manifest SHA-256 and used read-only;
- the licensed x86 DAO host bound to the exact clean pushed producer commit;
  and
- all schema, contract, corruption, resource-bound, and evidence-root checks.

`SRC-0019` records `dbDecrypt` as numeric API value 4. The revision therefore
records the previously blocked option sums, using `SRC-0014` for destination
version constants and `SRC-0019` for `dbDecrypt`. This is an API fact only and
assigns no meaning to any MDB byte.

## Unchanged scientific design

The documented-legal factorial remains 36 conditions:

- source version `dbVersion20`, `dbVersion30`, or `dbVersion40`;
- source encryption omitted or `dbEncrypt`;
- a destination version equal to or later than the source version; and
- compact encryption omitted, `dbEncrypt`, or `dbDecrypt`.

Each condition still has three replicas in three complete rotated blocks, for
108 samples and 324 isolated workers. Every sample still uses a source worker,
a compact worker, and a verify worker, with the same deterministic ordinals,
handoff clones, API calls, DAO version checks, empty-schema observations, and
2,048-byte retained prefixes. The 36 condition definitions and 108 sample
schedule entries are inherited unchanged, apart from filling the preregistered
`dbDecrypt` API values and option sums from `SRC-0019`.

The analysis boundary remains `[0x000, 0x600)`. `[0x600, 0x800)` remains
excluded under `SRC-0013`. The four comparison kinds, their 648 total
occurrences, all three predicates, and every scientific outcome rule remain
unchanged. Revised M4 contributes only its preregistered candidate offset sets
and stable per-condition values through an exact immutable bundle binding.

## Controller-owned post-worker quiescence

The original universal “`.ldb` absent after close” predicate is replaced by a
controller observation after the relevant worker has exited. There are four
fixed quiescence records per sample, one each for the source MDB, compact-input
MDB, compacted MDB, and verify MDB, for exactly 432 records in a complete run.

For each database role, the controller must exclusively reread the ordinary,
single-link, non-reparse MDB with file sharing disabled. Its size, SHA-256, and
retained 2,048-byte prefix must exactly match the corresponding pre-exit
observation. A sharing failure, identity violation, size drift, hash drift, or
prefix drift fails the sample.

The controller derives exactly one canonical sibling `.ldb` locator and
records its state as either `absent` or `present`. Absence is valid. If present,
the companion must be an ordinary, single-link, non-reparse file, must be
exclusively readable with sharing disabled, and must not exceed 65,536 bytes.
Its exact size and SHA-256 are recorded, and the in-place file is retained as a
manifest-bound artifact.

The controller and workers must never delete or copy a companion to make a
sample pass. A noncanonical or unexpected companion path fails the sample.
Companion bytes are protocol evidence only: they are excluded from retained
MDB prefixes, analyzed ranges, comparisons, candidate sets, histograms, and
all scientific inferences.

## Revised resource bounds

The database-artifact ceiling remains 432 files of at most 1 MiB each. The
four controller quiescence rereads add 432 bounded MDB reads, so acquisition is
limited to 1,620 database reads and 1,698,693,120 database bytes. The revised
contract additionally permits at most 432 optional companion artifacts of at
most 65,536 bytes each, for 28,311,552 companion bytes. Acquisition and each
independent validator pass may read at most those 432 companions and
28,311,552 bytes.

Exactly 432 quiescence records are required. Each record is limited to 16,384
bytes, for 7,077,888 bytes total. Existing limits for retained prefixes,
analyzed offsets, comparisons, worker processes, timeouts, database sizes,
sample records, and the analysis report remain unchanged.

A checked implementation must reject missing or duplicate quiescence records,
duplicate or aliased paths, symlinks and reparses, hard links, unexpected
files, invalid companion states, over-limit companions, failed exclusive
reads, MDB drift, hash mismatches, absent manifest bindings for present
companions, and any attempt to route companion bytes into analysis.

## Claims

M5 revision 2 proves no ability to read, create, update, validate, encrypt,
decrypt, convert, or interoperate with an MDB file. A complete, independently
validated exact-commit DAO bundle yields bounded provider observations only.
No compacted file is compatibility evidence, and no M5 claim exists until the
complete published bundle passes the checked validator without weakening any
evidence gate.
