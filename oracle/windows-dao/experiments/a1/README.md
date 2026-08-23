# A1 allocation-map preregistration

`DAO-A1-ALLOCATION-MAPS-001` is a project-authored, DAO-only physical
experiment preregistered as `EXP-0037` on 2026-08-19. No acquisition has
begun. The bound host must provide Python 3.13.x. The 72-checkpoint ceiling corrects the
pre-acquisition design review's inconsistent ceiling of 64: enumerating both
complete 29-point ladders produces exactly 71 checkpoints. This correction
adds headroom only; it does
not add adaptive checkpoints or change either ladder.

The campaign creates three fresh replicas. Replicas 1 and 2 are the derivation
set. Replica 3 is a sealed holdout and may only be evaluated after the complete
joint candidate set has been frozen from replicas 1 and 2. Holdout failure may
not refit, add, remove, or reinterpret a candidate.

Four equal-length table names rotate through disposable (`D`), low-target
(`L`), padding (`P`), and high-target (`H`) roles. Each unindexed table has only
`Id dbLong` and fixed `Payload dbText(240)`. Rows and reread digests follow the
exact algorithms in the plan. Every checkpoint is a closed, quiescent file.
The database is captured as an ordered list of 2,048-byte page hashes; each
distinct page is retained once in a content-addressed store. Companion files
may be recorded only as bounded acquisition diagnostics and never enter the
physical analysis.

The analysis tests only the predeclared pointer encodings, inline boundaries,
type-1 slot rules, and extended-page base formulas. A decisive result requires
exactly one joint model to survive replicas 1 and 2 and predict every applicable
transition in replica 3 without refitting. Ambiguity, missing transitions,
idle volatility, bound violations, unexplained nonzero suffix bytes, or replica
disagreement produce `no_scientific_outcome`.

Even a decisive result is a bounded observation of this DAO campaign. It does
not establish general TDEF, catalog, row, index, or LVAL layouts; unobserved
slots or bases; compaction, encryption, or version behavior; Rust correctness;
or DAO compatibility/support.

## Checked files and commands

- `a1-allocation-maps.plan.json` — immutable scientific and acquisition plan
- `plan.schema.json` — strict plan envelope
- `replica-observation.schema.json` — strict checkpoint/page-hash observation
- `analysis-report.schema.json` — strict bounded analysis result

```text
python oracle/windows-dao/scripts/archive/a1_spec.py validate-plan
python oracle/windows-dao/scripts/archive/a1_spec.py validate-observation REPLICA.json
python oracle/windows-dao/scripts/archive/a1_analysis.py --replica REPLICA1.json --replica REPLICA2.json --replica REPLICA3.json --bundle-root BUNDLE_ROOT --output REPORT.json
```

The execution gate remains blocked until the acquisition, independent bundle
validator, exact clean pushed commit, and licensed x86 host are bound. Changing
the plan after the first acquisition starts requires a new experiment ID and
provenance record.
