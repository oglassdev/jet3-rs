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

Before acquisition, additive revision `DAO-A3-ALLOCATION-MAPS-001-R3` was
recorded as `EXP-0046` to pin the layer semantics that two independent
implementations (analyzer PR #54, validator PR #53) filled differently, as
enumerated by the joint review committed at
`design-inputs/fable-a3-pair-review.md` (SHA-256
`70b9717d3b3387cbd2d4f1ceec3c8deff4f7706563af07eb2c5e77a6c05eab65`). Acquisition has not
started. The immutable revision file is
`oracle/windows-dao/experiments/a3/a3-allocation-maps-r3.plan.json`, SHA-256
`bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a`; it binds the
base plan and R2 hashes and inherits R2's sequences unchanged. R3 pins, with
one rule each: the extended 0x05-page bitmap layout (bytes `[4,2048)`, 16352
LSB-first bits, slot-relative and referenced-page formulas, discriminator leg
`[P_ABS_16480, H_REL_0064]`, self-in-use and `page_count` sentinel content
predicates), under which the EXP-0042 derivation replicas leave the unique
survivor `slot_relative_expected_0_16352` and refute a 1-byte header;
conversion attribution by class-change count; per-replica evaluation with
model-only comparison for `replica_disagreement`; the minimal-extent inline
boundary `b*` (a disclosed departure from the base enumeration, whose
survivor set is upward-closed) with `A3-INLINE-SUFFIX` evaluated after
boundary selection; the
global record stage order with end 2048 only, three tag anchors, and page
multiplicity on any two pages; TDEF signature-only candidacy with validity and
structural stability on the surviving model; tag-1-only global slot
activation; report reason order, holdout opening only when a model exists,
and enumerated-page candidate counts; structural holdout agreement for slack
versus exact prediction of slot reference pages and of `b*` (both
overshoot-dependent by design), with the cross-check walk and quiet inline
suffix re-checked on the holdout; and page absence as candidate rejection.
`A3-POLARITY-NONE`, `A3-INLINE-BOUNDARY-NONE`, and
`A3-INLINE-BOUNDARY-MULTIPLE` are declared unreachable by construction (under
R3-G02 every inline-phase checkpoint already passed the frozen-end capacity,
so `b* <= end` and the reduced-capacity decode passes); `A3-STRUCTURAL-EXCLUSION` is reachable
only on `tdef.pointer_pair`. On EXP-0042-like data the conversion layer is
terminal at leg 3 and the extended-base layer is therefore inapplicable. The
dry-run honesty clause requires executed fixture transcripts for all 31
reachable predicates, a replica 3 with independent overshoot, and full-sweep
analyzer/validator agreement recorded in retained companion documents.

Before acquisition, additive revision `DAO-A3-ALLOCATION-MAPS-001-R4` was
recorded as `EXP-0047` to pin three items surfaced by the executed full-sweep
analyzer/validator pair gate (PR #58, `dry-run/a3-pair-agreement.json` on
`origin/fable/a3-dryrun`). Acquisition has not started. The immutable revision
file is `oracle/windows-dao/experiments/a3/a3-allocation-maps-r4.plan.json`,
SHA-256
`939ce3ceef035b9da0e4527f1ffd9ddd6b21e23f088f867c56172f84650332ea`; it binds
the base, R2, and R3 hashes and inherits their rules unchanged. R4-S01 pins
`derivation_survivor_count` as the count of survivors actually found in
derivation replica 1: every MULTIPLE terminal carries its multiplicity, every
NONE terminal carries 0, a model or a single-survivor terminal carries 1, and
the table is written out for every terminal of every layer. R4-B01 replaces
the A1 run-12 carry-over blob bound (55, with its 13-page
`candidate_bound_assertion`) by the derived ceiling 1800 = 2 derivation
replicas x 25 checkpoints x (16 + 16 qualified + 4 referenced pages), and
pins the EXP-0042 replay re-measurement: global pages `{0,1,20,21}`, TDEF
pages `{0,1,23,24}`, exactly 81 distinct blobs (50 global, 71 TDEF, pages 0
and 1 shared). R4-B02 is the only schema edit in any A3 revision:
`dry-run-report.schema.json` `input_page_blob_count.maximum` 55 -> 1800
(SHA-256 `f88c1f9bf131352311d3e77e70f95d84d015b60c3d50cce40ceed668b390a593` ->
`e7b054543529f4b2ac38cda7ae15fac80cf20bd6745f4fcd43cec02eabc9f13d`),
permitted because the dry-run report is a non-evidential calibration artifact;
every evidence schema is byte-identical. R4-C01 supersedes the
`record_candidates_examined` sentences of R3-G08/R3-G03: the field and the
work units count each union qualified page once across derivation replicas,
which is the only reading consistent with `combined_record_candidate_bound`,
`bounds.max_record_candidates`, the analysis-report schema maximum
(67,141,632 = 32 x 2,098,176) and the `prefix_sum_work_model` arithmetic; the
exact 16+16 ceiling is accepted, and the validator must also enforce
`bounds.max_record_candidates`. The TDEF u24 divergence in the same pair gate
is an analyzer defect against existing text, not a plan gap, and is left to
the analyzer lane.

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

Execution status (`EXP-0048`): the pre-acquisition dry run has been executed
under R4 and disclosed. The synthetic sweep (100 fixtures, twelve sweep checks,
result `pass`), the executed reachability transcript (31 of 31 reachable ids,
3 asserted nonterminal), the analyzer/independent-validator pair gate (100 of
100 agreeing, `accepted=true` with T1–T5 rejected on every fixture holding a
frozen global-record model), and the EXP-0042 derivation-only replay (81
blobs, result `pass`) are retained under `dry-run/` with SHA-256 values in
`dry-run/checksums.json`. The execution gate remains `BLOCKED` on
worker/workflow rebinding (PR #56, open blocking findings), an exact clean
pushed commit designated for acquisition, and a licensed x86 DAO host.

Design-input pointers are themselves pinned by the plan and `EXP-0044`:

- `design-inputs/a2-preregistration-pointer.md`, SHA-256
  `8f16e79686620e254b0ba98de4d7cb21611f84a3e9b5c84d9fd6428987f51632`;
- `design-inputs/a2-independent-review-pointer.md`, SHA-256
  `2e89bb60aa5ac99d8f384836c75ce54c078817564d579d5411acd3bba8daae3b`;
- `design-inputs/exp-0042-bundle-pointer.md`, SHA-256
  `9bcb4b3c7ca2b43abd44a38200042312156d14552908c6d00ec9a25b24178349`.
