# A3 allocation-map preregistration

`DAO-A3-ALLOCATION-MAPS-001` is the project-authored successor to the closed A2
campaign. It is preregistered as `EXP-0044` on 2026-08-22. Acquisition has not
started. This lane contains the plan, A3-bound schemas, and hash-pinned design
input pointers only; it contains no analyzer, independent validator, worker, or
workflow implementation.

Before acquisition, additive revision `DAO-A3-ALLOCATION-MAPS-001-R2` was
recorded as `EXP-0045` to pin the previously unstated campaign and per-layer
predicate evaluation sequence and status projection. Acquisition has not
started, so this additive revision is permitted by the base plan's amendment
rule. The immutable revision file is
`oracle/windows-dao/experiments/a3/a3-allocation-maps-r2.plan.json`, SHA-256
`3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1`.
It preserves the base plan and explicitly flags four supplied positions whose
ordering appears to conflict with its operational prose: global record end
before polarity resolution, polarity cross-check before conversion/slot/inline
evaluation, inline suffix after boundary ambiguity, and extended-base pointer
validity after the base terminals.

The actual `EXP-0042` polarity cross-check outcome is a violation on leg 3,
`L_REL_0512` to `L_REL_0768`, first at page 1021; the later tag-change leg is
never reached. On EXP-0042-like data, the `global_map_conversion_inline` and
`global_map_extended_base` layers are terminal at leg 3 by construction; only
`global_map_record` and `tdef_pointer_pair` can reach holdout.

The immutable plan is
`oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json`, SHA-256
`b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1`.

A3 keeps A2's 25 checkpoints, role bindings, row algorithm, page capture,
bounds, parallel matrix/fan-in design, layered outcomes, freeze/holdout order,
and decisive-report retention. The A2 worker and workflow may later be rebound
only for the A3 experiment id, plan path, document/artifact names, and schema
bindings. Both must reject any selected plan whose `experiment_id` is not
exactly `DAO-A3-ALLOCATION-MAPS-001`.

The new plan explicitly registers the disclosed inline global-map layout: a
one-byte zero tag, little-endian u32 base, and least-significant-bit-first
bitmap. It also registers the tag-1 indirect layout with slot-0 at bytes
`[start+1,start+5)`, slot-1 at `[start+5,start+9)`, and a zero suffix. The
observed EXP-0042 prefix was `01 | 00 3A 00 00 | E0 3F 00 00`, giving u32
references 14848 and 16352. Record starts must satisfy the E0,
`D_GROW_0128`, and `D_REGROW_0128` highwater/sentinel predicates as well as
A2's D relation, polarity, and page-terminal suffix rules. The L/P/H polarity
cross-check tests only newly appended pages representable by both inline
snapshots and stops at the first violation or before the first tag change.
These representations were observed in the `EXP-0042` design-input bundle, so
A3 is a prediction test on three new replicas, not a rediscovery.

`derivation-candidates.schema.json` freezes qualified pages, four derivation
layers, and the polarity-cross-check transcript. The analyzer report must match
the parsed frozen document field-for-field; hash linkage alone is insufficient.
Every one of the 34 registered predicate ids appears exactly once. Applicable-
layer predicates retain their literal registry layer, and the report-level
holdout predicate passes whenever any layer is decisive even when another
layer records a holdout terminal. TDEF no-outcomes and pointer-validity windows
have explicit evaluation order.

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
  `9bcb4b3c7ca2b43abd44a38200042312156d14552908c6d00ec9a25b24178349`.
