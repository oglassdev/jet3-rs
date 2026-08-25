# Proposal: DAO↔Rust differential for `format.pages_allocation_usage`

## Decision boundary

`EVIDENCE.md` permits `dao_differential` only when DAO and Rust canonical
semantic results agree for a required scenario set and operation. DAO opening a
file, DAO-only generation, raw page hashes, or a Rust self-read is insufficient.
DAO has no API that returns Jet allocation or usage maps, so an exact DAO↔Rust
comparison of `allocated_pages: [..]` cannot be manufactured from DAO calls.

The current repository has no implemented differential scenario. Protocol 1.0
reserves `DAO-READ-*`, `DAO-WRITE-*`, and `DAO-UPDATE-*`, but deliberately rejects
their modes until canonical comparison and preservation checks exist. Protocol
1.1 implements only seven `DAO-GEN-*` recipes and DAO-only semantic pairs.
`tests/manifest.json` has allocation unit scenarios but no `DAO-*` scenario. The
`dao_differential_v1` release-evidence adapter is unavailable and disabled,
and ordinary `dao_bundle` evidence currently fails closed.

`EXP-0051` is useful provenance but cannot be relabelled: A3 expressly claims no
Rust correctness or DAO compatibility. It predicts only one global-map record;
conversion, extended base, and TDEF/usage-map layers did not produce models.
Because acquisition is complete, a new scientific question requires a new
experiment ID and plan, not an A3 revision or reinterpretation.

## Option 1 — checkpoint consequence differential

**Canonical result.** Define an `allocation_consequence_snapshot` per closed
checkpoint: checkpoint ID, file page count, changed physical page indices,
DAO-visible table row counts and rolling row hashes, and a fixed truth vector of
preregistered allocation-transition predicates. The DAO producer derives only
file/row/page-churn observables. The Rust producer additionally reads its
allocated-page set and evaluates the same declared predicates. The checked
comparator requires identical observable fields and all required predicates.

**Scenarios.** Reuse the shape, not the evidence, of A3's three role-rotated
fresh replicas and 25 checkpoints: E0/E0R; D grow/drop/recreate/regrow; the L
64..1280 growth ladder, delete/reinsert/idle; P absolute 4096..16480 growth; and
H 64/896/904/idle. Add one-below/exact/one-above representation boundaries once
those boundaries are established. Every checkpoint is closed and reopened;
DAO verifies exact row count/hash and the harness records exact extent/hash
churn before Rust evaluates the allocation predicates.

**Provable.** The Rust allocation interpretation is consistent with the
preregistered provider consequences for these transitions and provider.
Release/reuse, growth, idle stability, and exercised conversion boundaries can
be tested without a DAO allocation API.

**Not provable.** Consequence agreement does not uniquely determine the entire
allocated set, table ownership, insertion availability, unexercised map slots,
or behavior for compaction, another Jet version/provider, or arbitrary MDBs.
Page hash churn is not allocation churn. This option alone should not support
wording such as “exact allocation map verified by DAO.”

**Plan need / effort / risk.** New preregistered experiment and provenance are
required; A3 cannot be amended into a Rust differential. Estimate 3–5
engineer-weeks after the full allocation reader exists. Over-claim risk is high unless
the capability's supported limit is explicitly “consequence-consistent for the
manifested transitions.” Best used as supplemental evidence.

## Option 2 — preregistered independent page-usage oracle

**Canonical result.** A new DAO-driven research campaign freezes, before a
holdout is opened, a complete physical model and a separate oracle producer.
For fresh holdout MDBs it emits sorted
`global_in_use_pages`, `global_free_pages`, and, only if independently learned,
per-table `owned_pages`. Rust emits the identical canonical sets. A second
implementation recomputes the oracle output and rejects model/report tampering.

**Scenarios.** Empty/small inline maps; exact inline capacity boundaries;
inline→indirect conversion; activation of every extended slot; multiple 0x05
pages; growth/delete/reinsert/drop/recreate; idle reopen; multiple tables; and
table/index/long-value cases if ownership is claimed. Use derivation replicas,
a frozen model, an unopened holdout, and fresh later differential fixtures.

**Provable.** Exact set equality for the modeled representations, files, and
pinned provider, if the oracle is genuinely independent and complete.

**Not provable.** DAO itself did not disclose the set. The result is an
experiment-derived physical oracle, not direct DAO semantics, and cannot cover
unmodeled layouts or providers. Table ownership cannot be inferred merely from
global allocation.

**Plan need / effort / risk.** Definitely a new experiment ID/plan; EXP-0051's
missing conversion/base/TDEF results are insufficient. Estimate 8–12+
engineer-weeks for acquisition, model freeze, two analyzers, tamper suite, and adapter.
It may qualify only as `independent_check`, not `dao_differential`: before relying on it, an additive
evidence contract must explicitly accept this operation-specific DAO-side
projection. Do not make this the sole route without that decision.

## Option 3 — end-to-end semantic traversal (recommended)

**Canonical result.** Keep allocation internals out of the shared semantic
snapshot. DAO emits canonical user schema, columns/indexes, rows, and values.
Rust reads the same
closed MDB through Stage 2 allocation traversal and Stages 3–6, then emits the
same canonical JSON. Add common operation metadata such as closed-file page
count and row digest if desired. Retain a separate Rust coverage receipt,
bound to the MDB hash, listing allocation branches and an allocated-set digest;
the evidence adapter verifies required branches were exercised and that no raw
scan/bypass path supplied the semantic rows.

**Scenarios.** Introduce stable `DAO-READ-ALLOC-*` scenarios mapped to
`format.pages_allocation_usage`: small inline; exact boundary −1/0/+1;
delete/reinsert reuse; drop/recreate; idle reopen; inline→indirect; each
extended slot/base boundary; multiple tables; and representative index and
long-value ownership once supported. Generate fresh `dbVersion30` files with
DAO, close/reopen before both snapshots, and use at least role-rotated replicas.
Later add `DAO-UPDATE-ALLOC-*` legs in which Rust reuses/extends space and DAO
verifies the intended rows plus preservation of unrelated schema, rows,
indexes, relationships, long values, and raw-preservation fields.

**Provable.** For the declared read operation, Rust's allocation traversal
supports DAO-equivalent visible semantics across every manifested allocation
representation and boundary. Update legs additionally prove that Rust's
allocation decisions yield DAO-readable intended changes without collateral
semantic damage. This is the ordinary meaning of `dao_differential` in
`EVIDENCE.md`, even though DAO never returns an allocation set.

**Not provable.** Semantic equality cannot prove every extra/free page was
classified exactly, nor Jet's preferred allocation strategy. It proves the
supported operation, not a byte-for-byte implementation identity. Exact-set
claims still require Option 2 or another independent physical oracle.

**Plan need / effort / risk.** The differential inventory needs a new versioned
scenario/snapshot contract and checked adapter, but not a scientific experiment
merely to run a comparison. A new preregistered physical experiment is still
required first for every missing fact (map location, raw-reference/null rules,
extended base, usage ownership); it must be a successor experiment, not an A3
revision. Estimate 6–10 engineer-weeks after those facts and semantic reader Stages 3–6 exist; writer/update legs add roughly 6–10 weeks. Over-claim risk is
low if the support entry says “DAO-differential for manifested semantic read
operations,” not “DAO exposed or verified the exact page set.”

## Recommendation and acceptance shape

Choose Option 3 as the advancement path and use Option 1 as a stress/coverage
companion. Do not block the read capability on a writer, but require update
preservation scenarios before claiming allocator/writer support. Reserve Option
2 for an explicit future exact-set claim.

Before changing the matrix state, the exact clean release commit must contain:

1. Checked scenario JSON and inventory with stable IDs, capability and boundary
   mappings; matching Rust test-only files and entries in `tests/manifest.json`.
2. DAO/Rust snapshots, file hashes, logs, comparison reports, environment,
   provider identity, skips, and commands required by `EVIDENCE.md`.
3. A fail-closed `dao_differential_v1` adapter that recomputes canonical
   equality, verifies allocation-path coverage receipts, checks update
   `preserve_paths` when applicable, and is enabled by checked policy.
4. A `dao_bundle` reference for the exact clean commit. Earlier A3/M1 bundles remain provenance/design inputs only. Missing scenarios, branches, provider,
   artifacts, or exact-commit bindings report `BLOCKED`, never pass.
