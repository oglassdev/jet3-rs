# M4R2 canonical-path version/encryption file-prefix campaign

M4R2 is the canonical-path revision of the bounded, descriptive DAO-only M4
experiment. Its experiment ID is `DAO-M4-HEADER-DISCRIMINATOR-003`, and its
preregistration is `EXP-0017`. The original `EXP-0011` plan, the companion-aware
`EXP-0014` plan, and both prior experiment directories remain unchanged
historical records. Execution binds the distinct evidence ref
`refs/heads/codex/m4r2-canonical-paths`.

The revision was required after all M4R1 workers finished but exact bundle-tree
closure rejected the unpublished staging tree: DAO had retained uppercase
`CREATOR.MDB` basenames for the `dbVersion20` creator files while the immutable
M4R1 plan declared lowercase locators. The validation error exposed file names
only; no prefix, candidate set, comparison, or analysis report was published or
inspected. M4R2 changes every condition's creator and reopen database basename
uniformly to `CREATOR.MDB` and `REOPEN.MDB`. It retains M4R1 quiescence and
companion handling and does not change the scientific design.

The declarative plan is `m4-header-discriminator-r2.plan.json`. The copied,
revision-bound schemas constrain the plan, invocation, worker result, operation
log, semantic snapshot, clone log, sample record, analysis report, and complete
bundle. `post-worker-quiescence.schema.json` adds the controller observation
that separates DAO close from confirmed worker exit.

## Unchanged scientific projection

M4R2 retains exactly the original six conditions, six replicas per condition,
36 sample IDs, complete cyclic six-block launch schedule, two fresh x86 workers
per sample, creator/reopen pairing, locale, DAO calls, expected
`Database.Version` labels, and empty-user-schema requirement.

It also retains the 2,048-byte prefix, analyzes only `[0x000,0x600)`, excludes
`[0x600,0x800)`, declares the same 324 comparisons, applies the same three
candidate predicates, and uses the same scientific-outcome transition. A
companion byte may never enter a prefix, comparison, candidate set, occurrence
count, histogram, or inference. The plan records
`companion_bytes_analyzed: false` as an exact contract.

As in M4, a passing bundle can report only descriptive absolute offsets. It
cannot assign physical meaning, establish Rust behavior, change the support
matrix, or claim MDB compatibility.

## Phase and quiescence protocol

Before either worker's first COM call, the declared database path must have no
pre-existing `.ldb` sibling. Each worker then performs the same checked DAO
operation as M4, reads `Database.Version` and the empty user schema while the
database is open, closes and releases every DAO object, runs finalization, and
exclusively observes the closed MDB. The worker retains its exact 2,048-byte
prefix and commits its result before exiting. Post-close `.ldb` absence is not
a worker success condition in M4R2. Every sample uses the preregistered
uppercase database basenames; a casing mismatch fails exact tree closure.

After the bounded child-process runner confirms successful worker exit, the
controller performs one post-worker quiescence observation:

1. Open the MDB with `FileShare.None`, bound it to 1 MiB, and compute its full
   size, SHA-256, prefix SHA-256, and handle identity.
2. Require those bytes to match the worker's post-close MDB observation and
   retained prefix exactly. Drift fails the phase.
3. Derive the only permitted companion locator by replacing the database
   extension with lowercase `.ldb`.
4. Record the companion state as `absent` or `present` without condition-based
   expectations.
5. If present, open the sibling with `FileShare.None`, require an ordinary,
   non-reparse, single-link file, read at most 65,536 bytes, hash it fully, and
   retain it in place as a manifest-bound `companion` artifact.

The controller must never delete, move, truncate, rewrite, or synthesize a
companion to make a phase pass. The 65,536-byte limit is a work bound, not an
assertion about `.ldb` format or expected size. Exceeding it fails the run and
requires a new recorded blocker; it does not permit changing the bound during
execution.

Each phase retains a fixed `<phase>-quiescence.json` artifact. It binds the
worker finish timestamp, controller observation interval, successful exit
wait, exclusive and stable MDB identity, companion state, and any retained
companion identity. Creator quiescence must finish before the controller clone
starts. Reopen quiescence occurs only after the reopen worker finishes.

The existing controller clone remains byte-exact, same-volume,
non-hard-linked, reparse-free, and three-way re-hashed. A persistent creator
companion is not cloned and cannot be used as the reopen companion.

## Bounds and bundle closure

The post-worker MDB observations add two reads per sample. Acquisition is
therefore bounded to 288 database reads and 301,989,888 database bytes. A run
may retain zero through 72 companion artifacts, each at most 65,536 bytes, for
at most 4,718,592 companion bytes and 72 acquisition reads. Each independent
validator pass has the same 72-read and 4,718,592-byte companion ceiling.

There are 579 fixed payload files: the original 507 payloads plus 72
post-worker quiescence documents. Each present companion adds exactly one
payload, so `file_count` must be `579 + present_companion_count`, in the closed
range 579 through 651. The checked validator must derive that count from the
quiescence documents and reject omissions, additions, aliases, hard links,
symlinks, reparses, role substitution, hash or size drift, and absent/present
state inconsistencies.

All original prefix, sample-record, analysis, comparison, byte-visit, worker,
and timeout bounds remain in force. Execution success and scientific outcome
remain separate, and a schema-valid document alone is never evidence. M4R2
evidence exists only after a complete exact-commit bundle passes independent
checked validation.
