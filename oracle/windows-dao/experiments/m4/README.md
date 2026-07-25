# M4 version/encryption file-prefix campaign

M4 is a bounded, descriptive, DAO-only experiment. It asks whether byte
positions in the first 1,536 bytes of freshly created databases covary with
Microsoft-documented creation-version and encryption inputs. It does not assign
meaning to any position, extend either DAO protocol, exercise Rust, or establish
compatibility. The stable experiment ID and filenames retain
`header-discriminator` only as identifiers; that wording assigns no physical
meaning to the retained prefix.

The declarative plan is `m4-header-discriminator.plan.json`.
`plan.schema.json` constrains the input, `sample-record.schema.json` constrains
one paired creator/reopen record, and `analysis-report.schema.json` constrains
the bounded descriptive output. Per-phase inputs, results, operation logs, and
open-database semantic observations use `invocation.schema.json`,
`worker-result.schema.json`, `operation-log.schema.json`, and
`snapshot.schema.json`. Controller handoffs and the complete retained tree use
`clone-log.schema.json` and `bundle-manifest.schema.json`. Schemas reject
unknown fields. Retained artifact locators are campaign-output-relative and
traversal-free. Invocation `repository_root`, `stage_root`, and `output_root`
are bounded recorded absolute paths because those runtime roots are outside
the private stage. Execution requires canonical drive-rooted Windows paths;
portable retained validation determines the recorded path flavor and rejects
relative, dot-segment, noncanonical, NUL-containing, or reparse-bearing roots.

This plan is not executable yet and its checked execution gate is `BLOCKED`.
JSON Schemas cannot enforce its relational invariants, and no checked M4
validator, runner, or analysis implementation exists. Execution is blocked
until checked tooling reconstructs and verifies
the exact plan projection, worker and provider identities, immutable
invocations/logs/snapshots, clone relationships, bundle tree, 324-comparison
topology, candidate predicates, and scientific-outcome state machine. A
schema-valid document alone is never M4 evidence.

## Design

The factorial has six conditions:

| Condition | DAO version option | API value | Encryption option | Expected `Database.Version` |
| --- | --- | ---: | --- | --- |
| `V20-U` | `dbVersion20` | 16 | omitted | `2.0` |
| `V20-E` | `dbVersion20` | 16 | `dbEncrypt` (2) | `2.0` |
| `V30-U` | `dbVersion30` | 32 | omitted | `3.0` |
| `V30-E` | `dbVersion30` | 32 | `dbEncrypt` (2) | `3.0` |
| `V40-U` | `dbVersion40` | 64 | omitted | `4.0` |
| `V40-E` | `dbVersion40` | 64 | `dbEncrypt` (2) | `4.0` |

Each condition has six independent replicas, for 36 samples. All samples use
the checked `;LANGID=0x0409;CP=1252;COUNTRY=0` locale and contain no user
schema. Six blocks use a complete cyclic schedule. Every condition therefore
occurs once in every within-block launch position, while every block contains
the complete factorial. Each paired sample has a stable sample ID and two
separate database paths. The controller must bind the exact clean producer
commit, checked private origin/ref, environment record, and provider binary
identity before either DAO call.

Each sample has two paired phases executed by two separate fresh x86 worker
processes. Their deterministic worker run IDs, `(process_id, started_at_utc)`
identity tuples, and nonces must differ. A bare PID may be reused by Windows
after a worker exits and is not by itself a uniqueness failure. Global worker
ordinals are deterministic: creator `2*launch_ordinal-1`, reopen
`2*launch_ordinal`. Every worker independently records its PowerShell version,
DAO ProgID, provider CLSID, and server SHA-256 before COM:

1. `creator`: call `CreateDatabase` with the exact declared option sum, read
   the returned object's exact `Database.Version` and empty user-schema
   snapshot, close it, prove its `.ldb` is absent, and retain the closed-file
   metadata and 2,048-byte prefix.
2. `reopen`: after a controller-owned handoff, open a separate exact byte clone
   through DAO without requesting any schema or content mutation, read the
   exact `Database.Version` and empty user-schema snapshot, close it, prove its
   `.ldb` is absent, and retain the same bounded observations again.

Between phases, after the creator worker exits, the controller treats the
closed creator MDB as immutable and copies it byte-for-byte to the declared
reopen path. It records equal source/destination sizes and SHA-256 values,
requires same-volume but distinct `(volume serial, file index)` identities
with link count one, rejects reparses on both paths, re-hashes the immutable
source after cloning, and requires the pre-clone source, post-clone source, and
destination hashes to remain equal. It then rechecks the clone plus all
commit/environment/provider bindings before launching the reopen worker or
permitting its first COM call. The creator MDB is never reopened by the second
worker. Publication uses a same-volume directory rename so the recorded file
identities remain meaningful.

Each phase record binds its exact invocation, operation log, semantic snapshot,
and worker result by safe relative path and SHA-256. DAO version and empty
user-schema observations are recorded only while the database is open.
Database size/hash, prefix identity, close completion, and `.ldb` absence are
separate post-close file observations. The controller clone record likewise
binds its detailed clone log by path, SHA-256, start time, and completion time.
The bundle manifest closes the tree over all 507 expected payload files and
rejects omissions, additions, symlinks, and reparses.

Both labels must equal the condition's documented expectation. Any mismatch,
open failure, or size drift outside the declared limits fails the sample. The
reopen phase requests no semantic mutation, but byte drift remains an observed
outcome rather than an automatic validity conclusion. A passing label is an
API observation, not a claim that a byte contains a version value.

## Analysis boundary

At most 2,048 prefix bytes are retained per phase. Only the half-open interval
`[0x000, 0x600)` may enter comparisons. The complete interval
`[0x600, 0x800)` is excluded because `SRC-0013` documents it as live Jet 3
commit state; the same interval remains excluded for every condition so the
experiment does not bootstrap a cross-version interpretation. Full-prefix
hashes may bind artifact identity, but excluded bytes must not enter a
difference bitmap, candidate set, histogram, or inference.

Allowed comparisons are:

- paired `creator` versus `reopen` observations for one sample;
- within-condition replica variation;
- version contrasts matched on encryption mode, replica, and phase; and
- encryption contrasts matched on version option, replica, and phase.

The report may retain only absolute candidate offsets and occurrence counts
inside `[0x000, 0x600)`. It may not label a candidate as a version field,
encryption flag, header member, checksum, key, page tag, or any other physical
construct. Run-specific and provider-specific bytes remain possible
confounders even after all six replicas agree.

Execution success and scientific outcome are separate. A complete, valid run
uses `execution_status: pass`, while `scientific_outcome` may be
`candidate_offsets_observed`, `no_candidates_observed`, or `inconclusive`.
Zero candidate sets are valid. Neither an empty nor inconclusive result may be
rewritten as a failed execution, and a successful execution does not make a
candidate meaningful.

## API sources and excluded controls

`SRC-0014` documents the `CreateDatabase` inputs, and `SRC-0015` documents the
`Database.Version` result contract. Their constants and strings are DAO API
values, not assumed MDB encodings.

`SRC-0016` documents `CompactDatabase` version/encryption controls. That second
generation path is deliberately excluded from primary M4. It may support a
future independently checked conversion experiment, but mixing compacted
copies into this factorial would confound creation method with the two factors
under study.

## Bounds and claims

The plan limits each database to 1 MiB, all 72 retained creator/clone database
artifacts to 72 MiB, and acquisition to six full-file reads per pair: creator
post-close observation, creator clone/hash, destination verification,
post-clone source re-hash, reopen pre-COM verification, and reopen post-close
observation. That is at most 216 reads and 216 MiB. Each independent
bundle-validation pass has its own ceiling
of 72 reads and 72 MiB; repeated validator invocations do not share a mutable
budget. The 72 phase prefixes are limited to 144 KiB, analyzed offsets to
1,536, declared comparisons to 324 and 995,328 byte visits, sample records to
64 KiB, the analysis report to 16 MiB, and 72 workers to 120 seconds each. IDs
and relative locators are bounded. Implementations must additionally reject
duplicate JSON keys, BOMs, duplicate IDs/ordinals/paths/worker identity
tuples/nonces, symlinks or reparses in evidence roots, unexpected files,
partial schedules, unequal clone identities or hashes, hard-linked
source/destination files, and arithmetic overflow. Bare PID reuse is permitted
only when the recorded start time differs.

M4 assigns no physical byte meaning and proves no ability to read, create,
update, validate, encrypt, decrypt, or interoperate with an MDB file. In
particular, encrypted conditions are experiment controls outside the product's
unencrypted scope.
