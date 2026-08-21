# Re-review of PR #38 at `903db90` — `docs: preregister A2 allocation-map campaign`

Reviewer: Claude Fable 5, read-only. Plan lines refer to
`oracle/windows-dao/experiments/a2/a2-allocation-maps.plan.json` at `903db90`
(SHA-256 `db45bec8…`, matches the test pin and EXP-0040 Artifacts line).
Data checks were run on run-12 replicas 1–2 using page indexes plus 7 page-1
blobs (no holdout bytes); progress-file hashes for runs 11/12 were verified
against `runtime_design.post_33_timing_inputs`.

## Verdict: **REQUEST_CHANGES** (narrow)

17 of the 20 prior findings are resolved as the merger decided, and the
structural redesign (option (a), layered sub-models, D-only polarity, fan-in
receipt, per-replica environments, committed design inputs, explicit legacy
projection, reason↔predicate mappings, strict schemas) is sound. The PR is not
approvable yet because **three of the newly written predicates are falsified by
the pinned run-12 data the plan itself requires the dry run to pass on**, and
one bound set is arithmetically inconsistent. All four are small text fixes,
but since the plan is hash-pinned they must land before merge.

---

## A. New blocking findings (A1 failure class: predicate unsatisfiable by the plan's own data/schedule)

### N1. [Blocking] `global_page_qualification` yields 13 pages on run 12, not the asserted 10, and breaches the 12-page ceiling
- `plan.json:276` — qualify when hash/presence state differs across `E0→D_GROW_0128` **and** `D_GROW_0128→D_DROP`, "absence is a distinct state", ">12 qualifying pages is a resource_bound_breach".
- `plan.json:464` `candidate_bound_assertion` — "assert the observed run-12 D-qualified page count is exactly 10, each submodel is below 12".
- Measured (both replicas): qualifying pages = `{0,1,2,3,6,9,11,13,18,19,20,21,22}` = **13**. Pages 20–22 are absent at E0, present at D_GROW and **rewritten at D_DROP** (the only three D data pages whose hash changes on drop). The "10" in the assertion is the audit's count under an implicit *present-at-E0* filter, which `:275` forbids ("no … E0R-present filter").
- Effect: the retained-A1 dry run emits `resource_bound_breach` and fails its own assertion; acquisition stays BLOCKED by construction.
- Fix (choose one and make `:464` agree): raise `max_qualified_pages_per_submodel` to ≥ 16 and assert "13"; or add a third qualification transition (`D_DROP→D_REGROW_0128` differing) which removes nothing here but should be checked; or qualify on `E0→D_GROW` ∧ `D_GROW→D_DROP` ∧ `D_DROP→D_REGROW` and re-measure. Whatever is chosen, the generator must derive the count from the schedule, not copy it.

### N2. [Blocking] `global_record_end_resolution` requires an all-**zero** suffix; on run 12 the suffix is all-`0xFF` at every D checkpoint
- `plan.json:278` — retain the page-terminal interval only "when every byte after the last D-flipped byte through byte 2047 is zero at every D checkpoint and the zero suffix is at least 16 bytes"; otherwise `global_record_end_not_resolved`.
- `plan.json:465` `record_end_assertion` — "assert one global record survivor with at least 16 zero bytes of slack"; `:487-491` free parameter `record_end_zero_slack_bytes`; `:495` `record_uniqueness_rule` "at least 32 all-D-zero bytes".
- Measured: last D-flipped byte on page 1 is 1954; bytes 1955–2047 are `0xFF` at `E0`, `D_GROW_0128`, `D_DROP` and `D_REGROW_0128` (both replicas). Under the polarity the plan itself selects from D (`set_means_not_in_use`, `:298`), the unallocated tail of a small file is *all ones*, never zero. So the property is absent and the dry-run assertion is false.
- This is exactly the A1 `_inline_extent` polarity mistake re-entering through the tie-break. Fix: state the suffix property polarity-relatively — "every suffix byte decodes to *not-in-use* under the D-selected polarity at every D checkpoint" (i.e. `0xFF` for `set_means_not_in_use`, `0x00` for `set_means_in_use`) — rename the generator parameter `record_end_uniform_slack_bytes`, and re-derive `:465`/`:495`. Note the tie-break is applied *after* polarity selection (`:298`), so the ordering already permits this.

### N3. [Blocking] `structural_exclusion_rule` rejects the global-map record whenever the A2 churn checkpoint frees a page — which A2's delete rule is designed to do
- `plan.json:280` — "a global-map field may change only with the D set relation or declared post-delimitation global growth transitions … Any byte changing outside its field signature rejects that field".
- `plan.json:153` `delete_rule` — `L_DELETE_ALL` makes L's data pages "eligible to become free"; `:308` enables the churn pointer only if deletion "empties a formerly nonempty page".
- A global allocation map must flip those freed pages at `L_REL_1280→L_DELETE_ALL` and flip them back at `L_REINSERT_SAME`. Run 12 already shows this with the weaker alternating delete (page-1 tail byte 2047 changes on `L_REL_1280→L_DELETE_ALTERNATING`). Under A2 the change is guaranteed larger. As written, the very success of the churn design (`:308`) triggers `structural_field_exclusion_failure` on the global record (`:649-652`), killing every global layer by construction.
- Fix: the global-map field signature must be "changes only on D-relation transitions, declared growth transitions, **and the declared delete/reinsert transitions**", and must be allowed to change on idle-pair neighbours only if the idle pair itself is identical (already enforced by `A2-IDLE-EQUALITY`). The growth-pointer and churn-pointer signatures in the same clause are fine.

### N4. [Blocking] Timeout bounds are mutually inconsistent
- `plan.json:905` `worker_timeout_seconds_per_replica = 2400`; `:906` `fan_in_timeout_seconds = 900`; `:907` `campaign_timeout_seconds = 2700`; `:821` `hosted_wall_clock_target_seconds = 1800`; `plan.schema.json` pins all three as `const`.
- A replica that legitimately uses its 2,400 s ceiling leaves 300 s for fan-in under the 2,700 s campaign bound, while fan-in is allowed 900 s; a campaign can therefore time out on a schedule the plan declares permissible. Either `campaign_timeout ≥ worker + fan_in + setup` (≥ 3,300 s, and say so honestly relative to the 1,800 s target), or keep 2,700 s and lower the worker ceiling to ≤ 1,700 s (still 2.3× the 725 s projection). Also state explicitly that the 1,800 s figure is a *target*, not a bound, since a 2,400 s replica is within bounds.

## B. Status of the 20 prior findings

| # | Prior finding | Status at `903db90` | Evidence |
|---|---|---|---|
| 1 | No record-end delimiter | **Partially resolved** — byte-property tie-break + generator slack proof added, but the property is wrong-polarity (→ N2) | `:278, :465, :487-491, :495` |
| 2 | Enumeration infeasible / circular | **Partially resolved** — hash-only page qualification, 12-page ceiling, O(1) prefix-sum work model (8 units/interval × 50.36 M = 403 M < 500 M ✓), run-12 ceiling assertions; but the asserted count is wrong (→ N1) | `:276-277, :281, :283-285, :464, :898-900` |
| 3 | Conversion/slots on TDEF | **Resolved** (option a): global record carries conversion, slots, inline, polarity cross-check, base; TDEF only pointers; layered sub-models in schema | `:288-291, :301, :306, :310-313, :322, :766-773`; `analysis-report.schema.json` `submodels.global_map.{record,conversion_inline,extended_base}`, `tdef.pointer_pair` |
| 4 | Polarity undecidable | **Resolved** — D alone selects; L/P/H agreement is a separate layer check | `:298-299, :664-667` |
| 5 | Two slots at conversion | **Resolved** — ≥1 at conversion, exactly two by `H_REL_0904`, distinct reasons | `:312, :703-712` |
| 6 | All-or-nothing decisive | **Resolved** — four independent layers, `one_or_more_submodels_predict_holdout`, "an unmet layer never erases another" | `:766-773`; schema `scientific_outcome` enum |
| 7 | Slot-0 flip by accident | **Resolved (downgrade option)** — layer-only no-outcome, second fixture required | `:322, :496` |
| 8 | Fan-in validates holdout pre-freeze | **Resolved** — freeze, then separate structural process with receipt (`holdout-structure-receipt.schema.json`), then open | `:92, :772, :887` |
| 9 | Single environment hash | **Resolved** — per-replica environments, exact vs may-differ fields, manifest carries 3 hashes | `:70-81, :93, :882-886`; manifest `replica_environment_sha256[3]` |
| 10 | Page-1 offset blacklist | **Resolved in form** — page-agnostic transition signatures, no offsets named; but over-constrained (→ N3) | `:280, :544-545` |
| 11 | Calibration constants | OK — now on the global-map fixture | `:493` |
| 12 | Schedule from exploratory data | OK | `:261` |
| 13 | Legacy projection unspecified | **Resolved** — explicit 25-row table + not-applicable predicates | `:332-462` |
| 14 | No reason↔id map | **Resolved** — 34 mappings with layers | `:592-763` |
| 15 | `decisive_report_handling` loose in plan.schema | **Resolved** — `$ref decisiveReportHandling` | `plan.schema.json` |
| 16 | Validator may reject decisive | **Resolved** — gate item + source check + dry-run validator case | `:52, :326, :549` |
| 17 | Inputs unpinned | **Resolved** — four files committed under `design-inputs/`, SHA-256 pinned, byte-identical to the `/private/tmp` originals (verified), test enforces | `:16-37`; `test_a2_plan_contract.py:41-50` |
| 18 | Additivity / clean-room | OK — still additive; design inputs contain no third-party implementation content | — |
| 19 | Wall-clock basis | **Resolved in substance**, arithmetic verified (see C); bound inconsistency remains (→ N4) | `:822-859` |
| 20 | Bounds sanity | OK | `:859` |

## C. Timing arithmetic check (finding 19)

- Progress-file SHA-256s at `:822-853` match the six files in the scratchpad (runs 11/12, replicas 1–3).
- Elapsed at `H_REL_0904`: run 11 = 462.513 / 400.440 / 404.881 s; run 12 = 619.412 / 543.108 / 543.107 s ✓ (`:854` quotes 400.440–462.513 and 543.107–619.412).
- Final idle delta (`H_REL_1280→H_IDLE_REOPEN`): run 12 ≈ 35.5 s, run 11 ≈ 26.3 s ✓. 619.412 + 35.453 = 654.865 ✓. 725 − 654.865 = 70.135 ✓.
- Is the A1 prefix a fair upper bound for A2? The A1 elapsed-to-`H_REL_0904` includes 21 snapshot/reread checkpoints A2 drops, so it over-counts capture work; A2 adds `D_RECREATE_EMPTY` (A1 `D_DROP` cost 0.1 s, table create ≈ 1–2 s) and doubles the delete/reinsert rows (A1 run 12: 9.4 s + 12.9 s for 5,120 rows each → ≈ +22 s). The 70 s allowance covers that. 725 × 3.31 = 2,400 ✓; 725 + 900 = 1,625 ✓; 2,700 / 1,625 = 1.66 ✓. The estimate is credible; only the bound relationship is wrong (N4).

## D. Internal-consistency sweep of the revised predicates (satisfiable by the A2 schedule?)

| Predicate | Satisfiable by schedule / run-12 behaviour | Note |
|---|---|---|
| D set relation `:298,:300` | ✅ (run 12: G = pages 20–150, freed at DROP, re-used at REGROW, +128 new) | — |
| Global page qualification `:276` | ⚠️ 13 pages > 12 (N1) | |
| Record-end tie-break `:278` | ❌ suffix is `0xFF` under selected polarity (N2) | |
| Structural exclusion, global field `:280` | ❌ global map must change on `L_DELETE_ALL` (N3) | |
| Structural exclusion, pointer windows `:280` | ✅ | |
| TDEF page qualification `:277` (present at E0, growth change, both churn changes) | ✅ plausible; cannot be checked on A1 data (alternating delete: only page 0 qualifies) — correctly marked not-applicable in the projection `:404-412` | |
| TDEF inclusion-minimal record `:279` | ✅ deterministic | |
| Growth-only / churn-only pointers `:307-308` | hypotheses with named layer outcomes ✅ | "returns exactly" remains a strong assumption, now layer-local |
| Polarity from D `:298` | ✅ (wrong polarity gives empty G) | |
| Conversion over full window `:311` | ✅ (run 12: ordinal 20 = `P_ABS_16480`) | |
| Slot rule `:312` | ✅ (run 12: 2 at conversion, 2 at end) | |
| Inline boundary enumeration `:289-291` | ✅ in form; relies on the corrected polarity-relative suffix (tie to N2) | |
| Base discrimination `:322` | layer-local ✅ | |
| Prefix-sum work model `:281` | ✅ 403 M ≤ 500 M at the 12-page ceiling; re-check if N1 raises the ceiling (16 pages → 537 M > 500 M — raise `max_analysis_work_units` accordingly) | |
| Bounds `:905-907` | ❌ (N4) | |

## E. Minor (non-blocking)

- `:464` and `:465` hard-code run-12 numbers ("exactly 10", "+1") into the hash-pinned plan; after N1/N2 are fixed, prefer "assert the generator-derived count equals the observed count" so a re-measurement does not require an amendment.
- `:276-277` "absence at both endpoints is equality" is good; add the symmetric statement that presence→absence (truncation) is a difference, for completeness.
- `:856` says "2400-second worker ceiling" while README/EXP-0040 earlier text still mentions the frozen 1,800 s in places — re-read both after N4.

## Required before approval
1. Fix N1 (ceiling/assertion vs. measured 13), N2 (polarity-relative suffix), N3 (allow global-map change on declared churn transitions), N4 (consistent timeouts); re-hash the plan, update the test pin and EXP-0040.
2. Re-run the qualification/record-end/exclusion checks against run-12 replicas 1–2 (the script used here opens 7 blobs) and paste the numbers into the dry-run assertions.

No file in the repository was modified by this review.
