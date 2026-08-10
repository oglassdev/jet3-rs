# M5 compact-copy confirmation campaign

M5 is a bounded, descriptive, DAO-only experiment. It asks whether databases
produced by `DBEngine.CompactDatabase` from `DBEngine.CreateDatabase` sources
carry the same bounded-prefix byte values that M4 (`EXP-0011`) observes for the
matching documented destination version and encryption state, and whether any
position instead covaries with the generation method. It does not assign
meaning to any position, extend either DAO protocol, exercise Rust, or
establish compatibility.

The declarative plan is `m5-compact-confirm.plan.json`; the stable experiment
ID is `DAO-M5-COMPACT-CONFIRM-001`. The provenance entries are `SRC-0018` for
the `CompactDatabase` call contract and `EXP-0012` for this preregistration.
`SRC-0014` supplies the creation and version/encryption API values, `SRC-0015`
the `Database.Version` result contract, `SRC-0016` the compact-copy
version/encryption controls and the rule that no compacted file is
compatibility or physical-layout evidence without a separately checked
experiment, and `SRC-0013` the excluded commit region.

## Preregistration

This plan was recorded on 2026-08-10, **before any M4 execution**. No M4 sample
exists, so no M4 byte result can have influenced the M5 conditions, schedule,
analysis window, comparison topology, predicates, or outcome rules. Any change
made after M4 executes requires a new plan file and a new provenance entry
recording what changed, why, and when; it may not be edited into this file.

## Execution gate

The checked execution gate is `BLOCKED`. Unlike M4, M5 has no checked
controller, isolated phase workers, analysis implementation, or complete-bundle
validator, and none of its claims may be made until those exist and pass. The
declared blocking requirements are:

- a checked M5 controller and isolated phase workers;
- a checked M5 analysis and complete-bundle validator;
- one complete passing M4 bundle, bound by its bundle-manifest SHA-256;
- a provenance entry recording the numeric API value of `dbDecrypt`; and
- the Windows DAO host bound to the exact clean pushed producer commit.

`SRC-0018` records that the `CompactDatabase` page carries no numeric API
values, and `SRC-0014` records numeric values only for `dbVersion20`,
`dbVersion30`, `dbVersion40`, and `dbEncrypt`. The controller therefore may not
compute or pass an option sum for any `dbDecrypt` condition until a new ledger
entry records that value. The plan states this as an open provenance
requirement rather than assuming a value.

## Design

Every sample creates its own source through the M4 creation path, then compacts
it. The documented-legal factorial has 36 conditions:

- source version `dbVersion20`, `dbVersion30`, or `dbVersion40` (`SRC-0014`);
- source encryption omitted or `dbEncrypt` (`SRC-0014`);
- destination version `dbVersion20`, `dbVersion30`, or `dbVersion40`, limited
  to versions the same as or later than the source version, which is the
  documented restriction in `SRC-0016` and `SRC-0018`; and
- compact encryption option omitted, `dbEncrypt`, or `dbDecrypt`.

That gives six legal source/destination version pairs (`20-20`, `20-30`,
`20-40`, `30-30`, `30-40`, and `40-40`), two source encryption states, and
three compact encryption options: 6 x 2 x 3 = 36. Condition IDs have the form
`S<source version><source encryption>-D<destination version>-<option>`, for
example `S30U-D40-ENC`. Each condition records the M4 condition it is matched
against (`matched_m4_condition_id`), derived from the destination version and
the documented destination encryption state.

Documented destination encryption state follows `SRC-0018`: `dbEncrypt` gives
an encrypted destination, `dbDecrypt` an unencrypted one, and omitting an
encryption constant preserves the source state. Supplying both constants is
documented as equivalent to omitting both, so that redundant combination is
excluded. `dbVersion10`, `dbVersion11`, and `dbVersion120` are excluded because
they lie outside the M4 factorial this experiment confirms. The destination
locale argument and the password argument are always omitted, and no database
password is ever set, so the plan contains no credential material. The
documented destination encryption state is an API-level expectation only; M5
performs no on-disk encryption check, because no ledger entry describes how
encryption is represented in a file.

Each condition has three replicas, for 108 samples. Three replicas are the
minimum useful count: two can only show agreement or disagreement, while three
can separate a singleton run-specific outlier from agreement across replicas.
Three same-host replicas are not statistical or cross-environment proof. The
schedule uses three blocks; block `b` launches all 36 conditions in canonical
order rotated left by `12*(b-1)`, so every block contains the complete
factorial and every condition occupies three distinct within-block positions.

Each sample runs three fresh x86 worker processes with distinct deterministic
worker run IDs, `(process_id, started_at_utc)` identity tuples, and nonces.
Global worker ordinals are deterministic: source `3*launch_ordinal-2`, compact
`3*launch_ordinal-1`, verify `3*launch_ordinal`. Every worker independently
records its PowerShell version, DAO ProgID, provider CLSID, and server SHA-256
before COM:

1. `source`: call `CreateDatabase` with the declared option sum, read the exact
   `Database.Version` and empty user-schema snapshot, close it, prove its
   `.ldb` is absent, and retain the closed-file metadata and 2,048-byte prefix.
2. `compact`: after a controller-owned handoff, call `CompactDatabase` with the
   cloned closed input path, a destination path that does not yet exist and is
   not the input path, an omitted locale, the declared option sum, and an
   omitted password. Never open the input or the destination. Prove both
   `.ldb` files are absent and retain the destination's closed-file metadata
   and 2,048-byte prefix.
3. `verify`: after a second controller-owned handoff, open a separate exact
   byte clone of the compacted destination through DAO without requesting any
   schema or content mutation, read the exact `Database.Version` and empty
   user-schema snapshot, close it, prove its `.ldb` is absent, and retain the
   same bounded observations again.

`SRC-0018` documents that the source must be closed and exclusively available,
so the compact worker receives a closed file it never opens. The page does not
document what happens when the destination already exists, so a nonexistent
destination is a controller-side precondition that fails the sample rather than
a documented behavior.

Between phases the controller clones the closed database byte-for-byte, exactly
as M4 does: equal source and destination sizes and SHA-256 values, same volume
but distinct `(volume serial, file index)` identities with link count one, no
reparse points on either path, a re-hash of the immutable source after cloning,
and equality of the pre-clone source, post-clone source, and destination
hashes. The commit, environment, and provider bindings are rechecked before the
next worker is launched or permitted its first COM call. Neither the source nor
the compacted destination is ever reopened by a later worker.

The source and destination `Database.Version` labels must equal the condition's
documented expectations. Any mismatch, call error, pre-existing destination,
absent-`.ldb` failure, or size drift outside the declared limits fails the
sample. A failing label may not be reinterpreted as a format fact; it requires
a new provenance entry.

## Analysis boundary

At most 2,048 prefix bytes are retained per phase. Only the half-open interval
`[0x000, 0x600)` may enter comparisons. The complete interval `[0x600, 0x800)`
is excluded because `SRC-0013` documents it as live Jet 3 commit state; the
same interval remains excluded for every condition so the experiment does not
bootstrap a cross-version interpretation. Full-prefix hashes may bind artifact
identity, but excluded bytes must not enter a difference bitmap, candidate set,
histogram, or inference.

Allowed comparisons total 648:

- `paired_phase`: compacted destination versus its verify clone, 108;
- `within_condition`: all three replica pairs for each of the three retained
  phases in each of the 36 conditions, 324;
- `compact_versus_created_matched`: each compacted destination against the M4
  stable values for the matched created condition, 108; and
- `source_versus_compacted_within_sample`: the created source against the
  compacted destination of the same sample, 108.

Analysis requires one complete passing M4 bundle, bound read-only by its
bundle-manifest SHA-256 at execution time. M5 never mutates M4 evidence.

Three predicates are preregistered before any M5 or M4 byte exists. They are
stated over whatever offsets M4 reports, so they cannot be tuned to M4's
results:

- `M5-CONFIRM-VERSION-AGREEMENT`: at every offset in M4's
  `M4-CANDIDATE-VERSION-PAIRED` set, the compacted byte equals the M4 stable
  value for the matched destination version;
- `M5-CONFIRM-ENCRYPTION-AGREEMENT`: at every offset in M4's
  `M4-CANDIDATE-V30-ENCRYPTION` set, the compacted byte equals the M4 stable
  value for the documented destination encryption state; and
- `M5-COMPACT-ONLY-DIVERGENCE`: offsets inside `[0x000, 0x600)` that are stable
  within every compact condition and differ from the M4 stable value for the
  matched created condition.

The outcome rules are fixed in advance. `compact_matches_created` requires both
confirmation predicates to hold at every bound offset and an empty divergence
set. A nonempty divergence set gives `compact_diverges`. Any within-condition
instability inside the analyzed range, any missing, failed, or unbound M4
bundle, and any empty M4 candidate set for a bound confirmation predicate give
`inconclusive`. Execution success and scientific outcome remain separate: a
complete valid run uses `execution_status: pass` regardless of outcome, and no
outcome may be rewritten as a failed execution.

The report may retain only absolute offsets and occurrence counts inside
`[0x000, 0x600)`. It may not label an offset as a version field, encryption
flag, header member, checksum, key, page tag, or any other physical construct.
Run-specific and provider-specific bytes remain possible confounders even after
all three replicas agree.

## Bounds

The plan limits each database to 1 MiB, retains four databases per sample
(source, compact input, compacted destination, verify clone) for 432 artifacts
and 432 MiB, and bounds acquisition to 11 full-file reads per sample: source
post-close observation; source clone read; compact-input verification;
post-clone source re-hash; compact-worker pre-COM input verification;
destination post-close observation; destination clone read; verify-input
verification; post-clone destination re-hash; verify-worker pre-COM
verification; and verify post-close observation. That is at most 1,188 reads
and 1,188 MiB. Each independent bundle-validation pass has its own ceiling of
432 reads and 432 MiB; repeated validator invocations do not share a mutable
budget. The 324 retained prefixes are limited to 648 KiB, analyzed offsets to
1,536, declared comparisons to 648 and 1,990,656 byte visits, sample records to
64 KiB, the analysis report to 16 MiB, and 324 workers to 180 seconds each,
which allows for a compact copy taking longer than a creation. IDs and relative
locators are bounded. A future checked implementation must additionally reject
duplicate JSON keys, BOMs, duplicate IDs, ordinals, paths, worker identity
tuples, and nonces, symlinks or reparses in evidence roots, unexpected files,
partial schedules, unequal clone identities or hashes, hard-linked
source/destination files, and arithmetic overflow.

## Claims

M5 assigns no physical byte meaning and proves no ability to read, create,
update, validate, encrypt, decrypt, convert, or interoperate with an MDB file.
Encrypted and version-converted conditions are experiment controls outside the
product's unencrypted Jet 3 scope. Per `SRC-0016`, no compacted file may be
treated as compatibility or physical-layout evidence; a complete passing M5 run
yields format observations only. A schema-valid plan alone is never M5
evidence, and no M5 evidence exists until a complete published bundle passes a
checked validator on the exact clean pushed producer commit.
