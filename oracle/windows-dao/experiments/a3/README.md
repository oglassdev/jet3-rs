# A3 allocation-map preregistration

`DAO-A3-ALLOCATION-MAPS-001` is the project-authored successor to the closed A2
campaign. It is preregistered as `EXP-0044` on 2026-08-22. Acquisition has not
started. This lane contains the plan, A3-bound schemas, and hash-pinned design
input pointers only; it contains no analyzer, independent validator, worker, or
workflow implementation.

The immutable plan is
`oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json`, SHA-256
`08fe1e1336a9567af7530e5db4bb7d0867110c9dd35a07fdba3afb1285ec7750`.

A3 keeps A2's 25 checkpoints, role bindings, row algorithm, page capture,
bounds, parallel matrix/fan-in design, layered outcomes, freeze/holdout order,
and decisive-report retention. The A2 worker and workflow may later be rebound
only for the A3 experiment id, plan path, document/artifact names, and schema
bindings. Both must reject any selected plan whose `experiment_id` is not
exactly `DAO-A3-ALLOCATION-MAPS-001`.

The new plan explicitly registers the disclosed inline global-map layout: a
one-byte zero tag, little-endian u32 base, and least-significant-bit-first
bitmap. Record starts must satisfy the E0, `D_GROW_0128`, and
`D_REGROW_0128` highwater/sentinel predicates as well as A2's D relation,
polarity, and page-terminal suffix rules. The L/P/H polarity cross-check tests
only newly appended pages representable by both inline snapshots and stops
before the first tag change. This representation was observed in the
`EXP-0042` design-input bundle, so A3 is a prediction test on three new
replicas, not a rediscovery.

`derivation-candidates.schema.json` freezes qualified pages, four derivation
layers, and the polarity-cross-check transcript. The analyzer report must match
the parsed frozen document field-for-field; hash linkage alone is insufficient.
Every one of the 34 registered predicate ids appears exactly once, with
`fail` if and only if terminal, and `A3-HOLDOUT-PREDICTION` passes whenever any
layer is decisive. TDEF no-outcomes and pointer-validity windows have explicit
evaluation order.

The independent recomputing validator is a required future A3 artifact. It is
implemented from the plan text without analyzer imports or reads, parses and
compares the frozen set, independently recomputes the holdout result, and must
reject tamper cases T1–T5. Only its separately provenanced acceptance can move
`independent_validation_status`.

The execution gate remains `BLOCKED` on the A3 analyzer, independent validator,
worker/workflow rebinding, pre-acquisition dry-run disclosure, decisive-report
contract validation, exact clean pushed commit, and licensed x86 DAO host.

Design-input pointers are themselves pinned by the plan and `EXP-0044`:

- `design-inputs/a2-preregistration-pointer.md`, SHA-256
  `8f16e79686620e254b0ba98de4d7cb21611f84a3e9b5c84d9fd6428987f51632`;
- `design-inputs/a2-independent-review-pointer.md`, SHA-256
  `2e89bb60aa5ac99d8f384836c75ce54c078817564d579d5411acd3bba8daae3b`;
- `design-inputs/exp-0042-bundle-pointer.md`, SHA-256
  `c999dcb4624e9f945c966d5e621f0f5f5a44fd21cc9e10b470565ed4afc7d706`.
