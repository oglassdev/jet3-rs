# M3 replicated physical-delta campaign

Provenance usage: `EXP-0010`.

M3 is a descriptive DAO-only experiment. It is not a protocol-1.1 extension,
does not add meaningful G3 scenarios, and cannot advance the support matrix.
The exact checked plan is `m3-index-isolation.plan.json`.

## Design

The campaign launches nine separate x86 Windows PowerShell processes. Each
process activates a fresh DAO engine, creates one fresh `dbVersion30`
database, closes and reopens it through DAO, verifies semantic readback, and
exits. Three conditions each have three replicas:

- `E`: the existing checked empty scenario, used only as background variance;
- `B`: the existing checked `dbText(8)` baseline; and
- `I`: the same checked text scenario with the single declared nonunique index.

Launch order is the fixed cyclic schedule `E-B-I`, `B-I-E`, `I-E-B`. It
balances each condition across first, second, and third position. Three
replicas are the minimum useful count: two can show disagreement, while three
can distinguish a singleton run-specific outlier from agreement across all
replicas. Three same-host replicas are not statistical or cross-environment
proof.

The plan declares nine within-condition comparisons and all nine baseline ×
indexed comparisons. The three same-replica baseline/indexed comparisons are
also the paired subset. Empty databases are never compared with text
databases because that would change multiple semantic variables.

## Analysis

Every database is limited to 1 MiB and 512 complete 2-KiB pages; aggregate
database input is limited to 9 MiB. The analysis retains:

- database and ordered page SHA-256 values;
- exact differing-byte bitmaps and differing-page indices for all 18 pairs;
- exact within-cohort variable-byte bitmaps;
- paired and full-cross comparison intersections, unions, and occurrence
  histograms; and
- the exact bitmap where all baseline replicas agree, all indexed replicas
  agree, and the two stable cohort values differ.

The final metric is a candidate absolute-position delta only. Absolute page
indices do not establish logical page identity. Empty-cohort positions are
not subtracted from text/index positions.

## Safety and claims

The controller and every child require the exact clean commit at the checked
private origin ref, the ready x86 environment, and the exact provider
registration/path/hash before COM. Publication reuses the private, durable,
collision-refusing M1 directory publisher, validates the complete private
stage, rechecks runtime/remote/output-parent identity, then performs one
same-volume directory rename. Windows provides no safe managed
parent-directory fsync guarantee, so the claim is atomic visibility, not
immunity from every power-loss/storage failure.

The validator rejects malformed plans, duplicate JSON keys, BOMs, missing or
extra samples/files, reused worker identities/nonces, non-x86 processes,
hash/size/alignment drift, reparses, hard links, bounds violations, semantic
replica drift, and any analysis result it cannot recompute exactly.

M3 assigns no page class, header, catalog field, row, index node, allocation
structure, or stable MDB offset. It establishes no Rust behavior or
compatibility.
