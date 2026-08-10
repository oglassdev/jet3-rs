# M5 compact-copy confirmation campaign, revision 3

M5 revision 3 is a bounded, descriptive, DAO-only experiment. It asks the
same scientific question as `DAO-M5-COMPACT-CONFIRM-001`: whether databases
produced by `DBEngine.CompactDatabase` from `DBEngine.CreateDatabase` sources
carry the same bounded-prefix byte values that M4 observes for the matching
documented destination version and encryption state, and whether any position
instead covaries with generation method. It assigns no physical meaning to a
byte, exercises no Rust reader, and establishes no compatibility.

The additive declarative plan is `m5-compact-confirm-r3.plan.json`; its stable
experiment ID is `DAO-M5-COMPACT-CONFIRM-003`. The original plan and README
remain immutable. `EXP-0019` records this revision, `SRC-0019` records the
numeric `dbDecrypt` API value, and the required revised M4 input is
`DAO-M4-HEADER-DISCRIMINATOR-003` under `EXP-0018`.

## Preregistration timing and revision boundary

This revision was recorded after the independently validated M4R2 result in
`EXP-0018`. That timing is explicit: M5R3 may bind the exact M4R2 manifest and
carry forward the operational uppercase-path correction, but it may not tune
the factorial, samples, schedule, analyzed ranges, comparisons, predicates, or
outcomes in response to the observed M4 candidate set. Those scientific parts
remain semantically identical to the original M5 and M5R2 preregistrations.

Any later change after M5R3 execution requires another additive plan and
provenance entry. The M4R2 result and all earlier M5 plans remain immutable.

## Execution gate

The checked execution gate remains `BLOCKED` until all of the following exist
and pass:

- a checked M5 controller and isolated source, compact, and verify workers;
- checked M5 analysis and a complete-bundle validator;
- the licensed x86 DAO host bound to the exact clean pushed producer commit;
  and
- all schema, contract, corruption, resource-bound, and evidence-root checks.

The M4 input requirement is already satisfied by the read-only `EXP-0018`
bundle with manifest SHA-256
`0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d`.
The controller must revalidate that complete bundle and exact hash before any
M5 COM call.

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

All four database locators use uppercase basenames uniformly:
`SOURCE.MDB`, `COMPACT-INPUT.MDB`, `COMPACTED.MDB`, and `VERIFY.MDB`. This is
the exact-path operational correction recorded after `EXP-0016`; it is applied
to every condition and does not change a DAO call, factor, comparison, or
outcome rule. Manifest and tree closure remain case-sensitive.

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

M5 revision 3 proves no ability to read, create, update, validate, encrypt,
decrypt, convert, or interoperate with an MDB file. A complete, independently
validated exact-commit DAO bundle yields bounded provider observations only.
No compacted file is compatibility evidence, and no M5 claim exists until the
complete published bundle passes the checked validator without weakening any
evidence gate.
