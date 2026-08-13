# M5 set-reference successor preregistration

`DAO-M5-SET-REFERENCE-001` is a new bounded, descriptive DAO-only experiment.
It succeeds the terminally failed M5R7 family; it is not M5R8 and does not edit,
reinterpret, or make `EXP-0033` pass.

M5R7 required one stable M4 byte for every matched condition and analyzed
offset. Its complete acquisition was discarded when the immutable M4 input
contained more than one value for condition `V20-E` at offset `0x4F0`.
Selecting one observation, deleting that offset, or loosening the old validator
after acquisition would have been a post-hoc redesign. This successor instead
defines set-valued M4 references before any new M5 acquisition.

## Question

For each fresh compacted database, matched documented destination condition,
and absolute offset in `[0x000,0x600)`, is the compacted byte a member of the
complete set of bytes observed by the validated M4R2 acquisition at that same
condition and offset?

The question is descriptive. Membership does not identify a field or establish
that a byte is a version, encryption, generation, checksum, or page-header
value. It cannot establish Rust compatibility.

## Immutable inputs and new acquisition

The only M4 input is the independently validated `EXP-0018` bundle for
`DAO-M4-HEADER-DISCRIMINATOR-003`, bound by manifest SHA-256 in the plan. The
bundle remains read-only and retains its inconclusive outcome.

`EXP-0033` retained no M5R7 database, prefix, record, comparison, or report.
This successor therefore requires a complete new acquisition. It preserves the
documented-legal 36-condition factorial, three replicas, three rotated blocks,
and source/compact/verify worker separation. `SRC-0014`, `SRC-0015`,
`SRC-0016`, `SRC-0018`, and `SRC-0019` govern only the DAO API controls and
labels. They assign no meaning to MDB bytes.

The operational implementation must retain the fail-closed R7 protections:
fresh databases and workers, controller-owned exact byte clones, post-worker
exclusive quiescence checks, bounded companions, and no companion bytes in
analysis. This preregistration does not authorize reuse of R7 artifacts or
silently bind the old controller to the new scientific design.

## Set-valued M4 reference

For one M4 condition and absolute offset, the reference set is the sorted set of
distinct unsigned byte values from all six creator prefixes and all six reopen
prefixes. Exactly twelve validated observations are required.

- A singleton means the twelve M4 observations agree.
- A larger set preserves M4 variation without choosing a representative.
- An empty or incomplete set is an analysis failure.
- No unstable offset may be removed, including `0x4F0`.

Each of the three new compacted-database prefixes per M5 condition is primary
data. Its byte is checked for membership in the complete reference set for the
condition's matched M4 destination condition. Verify-phase prefixes are
integrity controls and cannot replace missing compact observations.

The complete design evaluates 36 conditions × 3 replicas × 1,536 offsets, or
165,888 memberships. It covers 36 × 1,536 condition-offset reference units.
Reference sets contain at most 256 byte values by construction.

## Fixed outcomes

A complete valid acquisition has exactly one of two scientific outcomes:

- `reference_sets_contain_all_compact_observations` when every compact byte is
  present in its matched M4 set; or
- `compact_observations_extend_reference_sets` when at least one is absent.

Failure to bind or validate M4, construct every complete reference set, acquire
all successor samples, or validate the successor bundle produces no scientific
outcome. It is not converted into either result.

The report may retain cardinality counts and exact condition/offset/value
occurrences within `[0x000,0x600)`. `[0x600,0x800)` remains excluded under
`SRC-0013`. The report may not attach physical names or semantics to offsets.

## Execution gate

Execution remains `BLOCKED`. Before any DAO acquisition, a later exact commit
must provide and independently review:

1. checked controller, worker, record, and bundle schemas for this successor;
2. checked set-reference analysis and an independent complete-bundle validator;
3. corruption, identity, path, arithmetic, and resource-bound tests; and
4. exact clean pushed commit and licensed x86 DAO-host bindings.

Any change after acquisition begins requires a new plan and provenance entry.
The current machine-readable plan is
`oracle/windows-dao/experiments/m5s1/m5-set-reference.plan.json`; `EXP-0034`
records its timing, hashes, limits, and claim boundary.
