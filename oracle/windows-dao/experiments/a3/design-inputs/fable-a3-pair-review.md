# A3 pair review — PR #54 (analyzer/generator/dry-run) and PR #53 (independent validator)

Reviewed in worktree `review/a3-pair` (both branches merged with main, HEAD b501f19).
Binding text: `oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json` (EXP-0044),
`a3-allocation-maps-r2.plan.json` (EXP-0045), `README.md`.
All paths below are relative to `oracle/windows-dao/`.

## Verdicts

| PR | Verdict | One-line reason |
|---|---|---|
| **#54** analyzer / generator / dry-run (`codex/a3-analyzer`) | **DO NOT MERGE** | The synthetic dry-run report asserts coverage it never produced (claims all 34 predicates reached; the sweep actually reaches 6), three registered predicates are dead by construction, and the analyzer carries several unpreregistered decisions (0x05-page bitmap offset, conversion-failure attribution, fill-level inline boundary, replica-agreement-by-list-equality) of exactly the A2 H1 class. |
| **#53** independent validator (`codex/a3-validator`) | **DO NOT MERGE** | Independence is genuine (see §2), but the validator rejects the analyzer's own all-layers-decisive fixture (`frozen_set_recomputation_mismatch`, confirmed by run), disagrees with the analyzer in 13 more sweep cases, has three false-rejection paths for correct reports (reason ordering, holdout_opened flag, record-candidate count), emits POINTER-VALIDITY outside the preregistered window, never emits three registered predicates, and reports `tamper_results: rejected=true` for T1–T5 as a constant it never executes. |

Several of the disagreements are not implementation bugs but **plan gaps** both lanes filled differently. Those need an additive R3 revision before either PR can be made to agree honestly (listed in §6). Do not "fix" them by making one implementation copy the other — that is how A2's H1 got in.

## How this was checked

- Read every line of both implementations against the plan prose and R2 sequences.
- Ran all A3 suites: `python3 -m unittest tests.test_a3_analyzer tests.test_a3_dryrun tests.test_a3_independent_validator tests.test_a3_plan_contract` → **40 passed, 21.6 s** (pytest is not installed; suites are unittest). Passing suites do not exercise any of the findings below — the validator suite uses a hand-authored EXP-0042-shaped fixture with no conversion/base layer, and the dry-run suite checks labels, not outcomes.
- Built a scratchpad harness (`scratchpad/xval.py`, `sweep.py`) that turns every `iter_parameter_cases()` generator fixture into a full schema-valid A3 bundle, runs `a3_analysis.build_analysis` to produce the frozen set + report, then runs `a3_independent_validator` (full and `--recompute-only`) on that bundle. Results are the basis for every "confirmed by run" note.

---

## 1. Rule-by-rule: deviations and unwritten assumptions

Legend: **A** = analyzer (#54), **V** = validator (#53). "Confirmed" = reproduced by the harness.

### 1.1 Extended-base layer — HIGH, confirmed divergence

- **Plan gap:** `hypotheses.extended_base_rule` says "evaluate the preregistered base formulas against the allocation bitmaps on the 0x05 pages" but never states where the bitmap starts on a 0x05 page, its bit order, or what "predicts that flip direction" means operationally.
- **A** assumes a 4-byte header: `scripts/a3_layers.py:34-35` (`EXTENDED_HEADER_BYTES = 4`, `EXTENDED_BITS = 16352`), read at `:313`.
- **V** assumes a 1-byte header: `scripts/a3_independent_core.py:627`, `:654-655`, `:668-669` (`_bit_set(page, 1, …)`).
- **Result (confirmed):** on the analyzer's own `calibration_parameters()` fixture the analyzer reports `extended_base` decisive (`referenced_page_relative`); the validator recomputes `A3-BASE-NONE` and rejects the bundle with `frozen_set_recomputation_mismatch`. Same in every other decisive sweep case (slots_1/2, both polarities, fill_*, slack_*, start_0/1, base_0/1, anchor_tag_0, representation_None). This contradicts the "zero discrepancies" premise; the earlier acceptance must have been on a fixture with no decisive base layer.
- Further semantic divergence even once the offset agrees: **A** `a3_layers.py:320-336` requires the set of *newly in-use* bits across both slots to equal `range(page_count(left), page_count(right))` exactly and ignores in-use→free flips; **V** `a3_independent_core.py:653-674` rejects any flip outside the appended range in either direction and additionally requires the formula to have been discriminated at the `P_ABS_16480→H_REL_0064` slot-0 leg specifically (`:663-664`, `:677`). Neither is the plan's text; both are unpreregistered predicates.
- **V** also emits `A3-POINTER-VALIDITY` from inside the base layer for any post-conversion checkpoint whose referenced page is absent or not 0x05 (`core.py:645-648`), including checkpoints **outside** `pointer_validity_checkpoints`. The plan says "checkpoints outside the named window are never validity failures". Confirmed: conversion_7…conversion_19 → A `A3-BASE-NONE`, V `A3-POINTER-VALIDITY`. **A** instead silently drops such references (`a3_layers.py:308-309`), which is a different unwritten choice.
- **Fix:** R3 must pin the 0x05-page bitmap layout (header length, LSB-first, capacity) and define the formula-survival predicate operationally. Then both lanes implement that text; V must drop the out-of-window validity failure; A must stop silently skipping.

### 1.2 Conversion classification — HIGH, confirmed divergence

- **Plan gap:** `type1_conversion_predicate` defines `conversion_checkpoint` as "the earliest valid indirect checkpoint after at least one valid inline predecessor" and requires a single monotone transition, but does not say which of `missing_inline_to_indirect_conversion` / `multiple_inline_to_indirect_conversions` applies when the window starts indirect, or when a `neither` checkpoint breaks monotonicity.
- **A** `a3_layers.py:183-191`: no indirect at all → NONE; otherwise any non-monotone shape *including "indirect from the first window checkpoint"* → MULTIPLE.
- **V** `a3_independent_core.py:372-384`: counts adjacent inline→indirect transitions; zero → NONE; >1 → MULTIPLE; otherwise non-monotone → MULTIPLE only if an inline appears after the transition, else NONE.
- **Confirmed:** conversion_1…6, conversion_21…23, start_1915, anchor_tag_1, representation_{D_REGROW…L_REL_0064, P_ABS_16480…H_REL_0064, H_REL_0064…H_REL_0896, H_REL_0896…H_REL_0904} → A `A3-CONVERSION-MULTIPLE`, V `A3-CONVERSION-NONE`.
- **Fix:** R3 defines attribution for (a) all-indirect window, (b) `neither` before first indirect, (c) `neither`/inline after first indirect. V's reading ("no inline predecessor ⇒ no conversion") is closer to the text.

### 1.3 Global record start/end/polarity

- **Five-set D relation, highwater, sentinel, capacity bounds:** both match the text (`a3_model.py:275-300`; `core.py:102-136`).
- **A adds an unwritten requirement:** tag must be 0 at **all five** D checkpoints (`a3_model.py:329`); the plan requires tag 0 only at E0, D_GROW_0128, D_REGROW_0128. V checks only the three anchors (`core.py:102-106`). Low practical impact, but it is the H1 pattern. Fix: drop `:329`.
- **End resolution:** both evaluate only `end = 2048` and use the ≥16-byte polarity-relative uniform suffix after the last D-flipped byte (`a3_model.py:303-311`, `core.py:139-154`). A scans flips from `start+5`, V from `start` (tag/base bytes included). Equivalent unless the base changes across D checkpoints. Low; pick one in R3.
- **Plan-literal edge** (both): the plan allows a start to survive via "some end in (start+5, 2048]" and then demands the page-terminal end; both collapse this to "end 2048 only", so a start that satisfies the D relation only at a shorter end is reported as `A3-D-SET-RELATION`/`A3-GLOBAL-RECORD-END` rather than per the text's "emitted only when … no (start, polarity) satisfies the D set relation". Low; R3 should say explicitly that only end 2048 is enumerated for the D relation.
- **Multiplicity vs polarity ordering — MEDIUM, divergent, both off R2.** R2 `global_map.record` order is …END, POLARITY-NONE, POLARITY-MULTIPLE, PAGE-MULTIPLE, RECORD-MULTIPLE.
  - **A** `a3_analysis.py:241-262`: PAGE-MULTIPLE (any two pages with survivors, regardless of per-page start counts) → POLARITY-MULTIPLE → RECORD-MULTIPLE.
  - **V** `core.py:225-240`: PAGE-MULTIPLE (only if every page has exactly one start) → RECORD-MULTIPLE → PAGE-MULTIPLE again → POLARITY-MULTIPLE.
  - Example: one page, two starts with different polarities → A `A3-POLARITY-MULTIPLE`, V `A3-GLOBAL-RECORD-MULTIPLE`. Example: page X two starts + page Y one start → A PAGE-MULTIPLE, V RECORD-MULTIPLE.
- **`A3-POLARITY-NONE` is dead in both** (`a3_analysis.py:255-256` after `models` non-empty; `core.py:235-236` after `common` non-empty). The D relation failing for both polarities is already `A3-D-SET-RELATION`, so the plan's `no_unique_bit_polarity` is unreachable as written. R3 must either define a distinct trigger or retire the id; the dry-run must not claim it is reached (see §3).

### 1.4 Replica agreement — MEDIUM, divergent

- Plan: "A start survives only if all predicates pass in both derivation replicas" (intersection) and `replica_disagreement` as a registered reason.
- **A** `a3_analysis.py:234-239` requires the **complete per-page candidate lists and the internal evidence flags** (`layout/anchor/relation/suffix`) to be identical across replicas, else `A3-REPLICA-DISAGREEMENT`. Replica 1 = {S1,S2}, replica 2 = {S1} is a plan-literal unique survivor S1 but A reports disagreement. Evidence-flag equality is an unpreregistered predicate on internal state.
- **V** `core.py:213-224` intersects candidate sets (plan-literal), but for the no-survivor reason uses `all(not seen[...])` across replicas.
- **V** in the conversion (`core.py:359-413`) and TDEF (`:519-534`) layers returns the **first replica's** failure without consulting the second: replica 1 `SLOT-ACTIVATION` + replica 2 decisive → V `SLOT-ACTIVATION`, A (`a3_analysis.py:198-211` `_pair`) `A3-REPLICA-DISAGREEMENT`. Same for the churn precondition (`core.py:519-526` vs A `_pair`).
- Not reachable in the sweep only because `generate_synthetic_bundles` returns byte-identical replicas (§3).
- **Fix:** R3 pins: survivors = intersection; disagreement = replicas reach different terminal predicates or different survivor sets after intersection is empty/non-singleton; no internal-flag comparison.

### 1.5 Polarity cross-check and stop rule

- Required-page formation, vacuous pass, violation = left in-use or right not-in-use, first-violating leg/page, evaluated-legs excluding the stop leg: **both match the text** (`a3_layers.py:129-156`; `core.py:252-289`), and agree on EXP-0042 (leg 3, page 1021, 3 legs).
- **V** treats a leg whose tags are equal but nonzero as `representation_change_stop` (`core.py:261`); the plan stops only when the right tag **differs** from the left. **A** skips such legs and continues (`a3_layers.py:139-140`). Only reachable with a tag ∉ {0,1} on both ends; Low, but V should match the text.
- **A** `a3_layers.py:152-153` raises `A3-POINTER-VALIDITY` for a required page ≥ 65536 **inside** `polarity_cross_check`; `a3_analysis.py:282` calls it outside any `try`, so the Abort would become a campaign abort that erases every layer. Unreachable because `View` caps page_count at 65536 (`a3_model.py:110`), so it is dead code, but it is a latent fail-open-to-wrong-layer path. V clamps at 65536 silently (`core.py:270`). R3 should state the ≥65536 rule is vacuous under the 65536 page bound or remove it.

### 1.6 Indirect layout, slots, pointer-validity window

- Tag-1, slot-0 `[start+1,start+5)`, slot-1 `[start+5,start+9)`, zero suffix, activation count 0/1/2, "two by H_REL_0904": both match (`a3_layers.py:112-120, 275-289`; `core.py:309-318, 386-390`).
- **Shared unwritten assumption:** activation for a global slot is searched only at checkpoints whose record tag is 1 (`a3_layers.py:198-201`; `core.py:364`). The plan's `pointer_validity_rule` says "earliest checkpoint in the complete 25-checkpoint order where the corresponding u32 slot … is nonzero" with no tag condition; taken literally, a nonzero base at E0 activates slot-0 at ordinal 0. The implementations' reading is sensible but must be written into R3.
- **Shared unwritten relaxation:** `growth_only_pointer_predicate` / `delete_reinsert_only_pointer_predicate` require the referenced page's byte zero to be 0x05 "when active"/"whenever active"; both implementations check 0x05 only at `pointer_validity_checkpoints` at/after activation (`a3_layers.py:365-374` via `validate_references`; `core.py:434-453`). A pointer to a non-0x05 page during L growth passes both. R3 must reconcile the two rules.
- **A** orders POINTER-VALIDITY after SLOT-FINAL and before inline boundary in the conversion layer — matches R2 position 6. OK.

### 1.7 Inline boundary and suffix — MEDIUM, shared forbidden source + divergent reasons

- Plan `inline_boundary_procedure.candidate_source`: enumerate **every** b in `{start+5,…,end}`; "do not derive candidates from the last nonzero byte, fill level, or an anchor checkpoint".
- **A** `a3_layers.py:242-250` computes one `required_boundary` from `page_count − base` at the inline checkpoints (i.e. fill level) and tests only that b; **V** `core.py:393-396` does the same arithmetic. They agree on the number, but both derive the candidate from a forbidden source, and the text as written would (with a uniform not-in-use suffix) let every b ≥ that value survive → `multiple_inline_boundary_candidates`. The plan's own `inline_boundary_rule` ("recover the same boundary in all [fill] variants") shows the intent is the minimal extent; R3 must say so.
- **A** raises `A3-INLINE-SUFFIX` when the single candidate fails only on suffix bytes (`a3_layers.py:259-269`), i.e. before `INLINE-BOUNDARY-NONE`; R2 pins INLINE-SUFFIX at position 9 after NONE/MULTIPLE (flagged conflict #3). **V** only ever emits `INLINE-BOUNDARY-NONE` (`core.py:398-402`). Reason divergence on any suffix-dirty fixture.
- **`A3-INLINE-BOUNDARY-MULTIPLE` is dead in both** (single candidate). **`A3-INLINE-SUFFIX` is dead in V.**

### 1.8 TDEF ordered stages — MEDIUM, divergent and off R2

- Stage order (precondition → growth windows → churn windows → records → multiplicity) is implemented in both (`a3_layers.py:448-481`; `core.py:516-558`), and the page-before-record disambiguation matches `terminal_disambiguation` in both.
- **A** `a3_layers.py:457-468` raises `A3-STRUCTURAL-EXCLUSION` or `A3-POINTER-VALIDITY` **before** `GROWTH-POINTER-NONE` / `CHURN-POINTER-NONE` whenever a shaped window was excluded for that reason and none survive. R2 `tdef.pointer_pair` puts POINTER-VALIDITY (9) and STRUCTURAL-EXCLUSION (10) **after** POINTER-MULTIPLE (8). **V** never flags either: a window failing structure/validity is simply not a candidate, so V reports `GROWTH-POINTER-NONE`. Divergent terminal on real data with a non-0x05 or unstable pointer.
- **`A3-STRUCTURAL-EXCLUSION` is never emitted by V at all** (string absent from `a3_independent_core.py`/`a3_independent_validator.py`). For the global layers, **A**'s `global_structural_valid` (`a3_layers.py:159-169`) is vacuous: every one of the 24 consecutive transitions is in its allowed set (it even allows changes on idle pairs themselves), so it can never return False. The plan's structural rule for the global record is also effectively vacuous once idle equality holds, so this is mainly a dead-predicate/dry-run honesty problem (§3).
- **A** rejects overlapping growth/churn windows (`a3_layers.py:431`); **V** allows them as long as offsets differ (`core.py:498`). "Distinct" is undefined in the plan; Low.
- **A** turns a page that is absent at any checkpoint into a campaign-wide `A3-SNAPSHOT-RECONSTRUCTION` abort (`View.page` `a3_model.py:126-129`, used unconditionally by `_window_values :357-358`, `tdef_models :424`, `global_start_candidates :318`, `global_structural_valid :167`). The plan says "absence is an explicit state" and qualification explicitly admits presence→absence. **V** treats absence as `None` and rejects the candidate (`core.py:127-129`, `:429`, `:491`). Medium: a legitimately-shrunk file makes A erase every layer and V produce a normal terminal.

### 1.9 Freeze rule, holdout exception, predicate projection

- Canonical frozen bytes, schema-order serialisation, hash recorded before holdout, parsed field-for-field comparison with the `holdout_prediction_failure`-only exception: **A** `a3_spec.py:323-416` and **V** `a3_independent_validator.py:162-194` both implement the text; A also validates the canonical byte form (`a3_spec.py:395`), V requires `raw == canonical_json_bytes(frozen)` (`a3_independent_bundle.py:537`). OK.
- Report-level holdout exception (pass iff any decisive; fail iff none decisive and some holdout failure; else not_applicable): **A** `a3_spec.py:436-447`, **V** `a3_independent_validator.py:453-458`. Agree.
- R2 statuses: both project "pass" **positionally** — every predicate listed before the terminal in the R2 sequence is pass (`a3_spec.py:245-257`; `validator.py:440-450`) even if the code never evaluated it (e.g. A's REPLICA-DISAGREEMENT raised at candidate comparison marks D-SET-RELATION/END/POLARITY-* as pass). R2 says "reached and evaluated". They agree, so Low, but the projection is a label, not a measurement.
- **Divergence on campaign terminals:** when a layer's terminal is a campaign predicate (`A3-IDLE-EQUALITY`, `A3-SNAPSHOT-RECONSTRUCTION`, `A3-RESOURCE-BOUND`), **A** marks every predicate of that layer `not_applicable` (`a3_spec.py:253-257` returns `set()`); **V** marks all of them `pass` (`validator.py:445-450` never breaks) and hard-codes SNAPSHOT/RESOURCE as `pass` (`:431-435`). V also keeps deriving layers after idle inequality, so it always rejects an idle-volatile report with `frozen_set_recomputation_mismatch` instead of agreeing with it. Medium: V cannot independently validate any campaign-terminal report.

### 1.10 Holdout evaluation semantics — MEDIUM

- **Shared, plan-schema-sanctioned but fragile:** holdout "prediction" for `global_map_record` requires `zero_suffix_slack_bytes` to be byte-identical (`a3_layers.py:484-487` via dataclass equality; `validator.py:202-208`). The slack is a function of the last D-flipped byte, i.e. of the achieved page count, which the plan explicitly allows to overshoot differently per replica (EXP-0042 measured 1955/92 vs A1 run-12 1954/93). A correct record/polarity model can therefore fail holdout on overshoot alone. Same for `slot_reference_pages` in the conversion model (plan `type1_rule` makes that explicit, so it is a plan design decision, but it should be acknowledged in R3 as a prediction of exact page numbers).
- **Divergent:** **A** `predicts_tdef` (`a3_layers.py:509-515`) re-derives on the holdout page and requires the frozen model to be the **unique** survivor (any multiplicity abort → failure); **V** `predict_tdef` (`validator.py:250-306`) re-checks the frozen model's predicates only. Likewise **A** `predicts_conversion` re-runs the whole derivation. The plan says evaluate "without refit, candidate addition…"; uniqueness-on-holdout is an unwritten extra predicate in A.
- **A** opens replica 3 whenever derivation did not campaign-abort, even when no layer has a model (`a3_analysis.py:372-375`) and writes `holdout_opened_after_freeze: true`; **V** requires that flag to equal `any(holdout_evaluated)` (`validator.py:417`). An all-no-outcome report (every layer terminal at derivation) is therefore **always rejected** by V with `holdout_opened_flag_mismatch`. Not hit in the sweep only because the synthetic TDEF layer is always decisive.

### 1.11 Bounds / work ceilings

- Both bound JSON size, page blobs, page bytes, qualified pages ≤16, candidates, models, work units (`a3_model.py:63-94, 181-208`; `a3_independent_bundle.py:200-222`, `core.py:73-75`). Ceilings are taken from the plan, not hand-typed. OK.
- **A** `analysis_work_units` is a flat per-page charge (`enumerate_intervals` = 2,098,176 × 8 per qualified page, `a3_model.py:76-80`) plus a few counters; the actual searches (`pointer_windows`, `terminal_suffix_slack`, `_bits_*`) are not metered through the prefix-difference model the plan describes, and `pointer_windows` scans 2045 offsets × 2 layouts × 25 checkpoints with no charge. The reported number is therefore a ceiling proof, not a measurement; the "bounds accept exact ceiling / reject one over" dry-run assertion is a label (§3). Low.
- **V** `verify_report_bounds` (`validator.py:462-469`) requires `record_candidates_examined == (|global|+|tdef|) × 2,098,176`. **A** skips TDEF enumeration when the churn precondition fails (`a3_layers.py:450-455`), so any `A3-CHURN-PRECONDITION` report is rejected with `record_candidate_count_mismatch`. Medium false-rejection path.

---

## 2. Independence of the validator (PR #53)

- Import graph: `a3_independent_validator.py` → `a3_independent_bundle.py`, `a3_independent_core.py`; those import only stdlib (`hashlib, json, os, re, dataclasses, datetime, pathlib, typing`). No `a3_spec/a3_model/a3_layers/a3_analysis/a3_generator/a3_dryrun/a2_*/protocol_validation` import or file read anywhere (grep over the three files and the test). **Confirmed independent.**
- Own JSON-schema checker (`a3_independent_bundle.py:68-169`) covers every keyword actually used by the 11 A3 schemas (`type, const, $ref, minimum, maximum, required, properties, additionalProperties, pattern, minLength, maxLength, minItems, maxItems, items, enum, uniqueItems, anyOf, format`). Verified by keyword census.
- Plan binding: the plan is not pinned by a constant in the validator; it is pinned transitively — R2 is pinned by `R2_SHA256` (`validator.py:41`) and R2's `original_plan.sha256` must equal the supplied plan's hash (`:85`). Acceptable; state it in the disclosure.
- Not a re-encoding: all four layers, qualification, cross-check, predicate statuses, and holdout predictions are recomputed from page bytes before the frozen set and report are compared (`validator.py:481-498`). `recompute_report_layers` copies frozen reasons only for layers that already matched the recomputation. OK.
- Test fixture (`tests/test_a3_independent_validator.py`) is hand-authored (start 1700, counts list) and not derived from the generator. OK — but it contains no conversion, base, or decisive TDEF layer, which is why none of §1.1–1.2 were caught.

## 3. Analyzer dry-run honesty (PR #54) — HIGH

The synthetic dry-run report is not an honest record of what ran.

1. **Predicate reachability is asserted by label.** `registry_reachability()` (`scripts/a3_dryrun.py:367-376`) emits `status: "reached"` and `perturbation: "single_<reason>_perturbation"` for all 34 ids from the registry alone; no perturbation fixture exists. The synthetic report then hard-codes `terminal_predicate_ids = list(PREDICATE_IDS)` and `predicted_terminal_states = required_cases` (`:490-491`), and `validate_dry_run_report` (`a3_spec.py:511-512`) checks those lists against the same plan lists — a tautology. **Measured:** the real sweep reaches **6 of 34** terminals: `A3-CONVERSION-MULTIPLE, A3-BASE-NONE, A3-CONVERSION-NONE, A3-SLOT-ACTIVATION, A3-GLOBAL-PAGE-NONE, A3-GLOBAL-RECORD-NONE`. The plan's `abort_reachability_rule` says dead, unproducible or omitted predicate results **fail** the dry run; `test_each_registered_predicate_has_one_reachability_case` (`tests/test_a3_dryrun.py:35-39`) only checks the labels.
2. **`anchor_fill_state` is never used by the generator** (`a3_generator.py:205` validates it; nothing reads it). `fill_empty/partial/full` produce byte-identical bundles, so "inline boundary anchor-fill independent" is untested. The `record_uniqueness_rule` perturbations (wrong tag, truncated base, base above page_count, missing highwater page, in-use sentinel, extra start, shorter ends), the `polarity_cross_check_rule` violation fixtures (first-page/later-page violation, full/partial/empty intersections), the `base_discrimination_rule` no-flip fixture, the `slot_rule` "final count other than two" case, and the `bounds_sanity_basis` "exact ceiling / one over" cases are **not generated**; their assertion strings (`a3_dryrun.py:492-503`) are literals.
3. **Replica 3 is replica 1 renumbered** (`a3_generator.py:272-275`), so every "decisive_predicts_holdout" in the sweep is byte-identity, not prediction. It also hides the slack/slot-page fragility of §1.10 and all of §1.4.
4. **Per-case outcomes are recorded but never asserted** (`a3_dryrun.py:398-405`): the transcript lists terminals per case, but nothing checks e.g. that `slots_0 → A3-SLOT-ACTIVATION` or that any `first_violating_leg/page` is carried.
5. **The replay report claims synthetic parameter coverage** — `_coverage(calibration)` (`:435-442`) with all 24 conversion ordinals, slot counts, polarities, fills, slacks is attached to the EXP-0042 replay report (`:469`) which exercised none of them. `validate_dry_run_report` skips that check for replay source kinds.
6. **Replay T3/T5 are token demonstrations** (`:307-314`, `:323-335`): T3 is a polarity flip of the A2 frozen document compared against the recomputed model (no hash relinking), T5 flips the first predicate status. They are not the plan's T3/T5 but are labelled as such in `assertions`.
7. Holdout restriction in replay is **honoured**: `RetainedDerivationReplica` refuses replica ≠ 1,2 (`:109-110`), page-index paths are confined to the replica's directory (`:127-128`), and page digests are restricted to that replica's own indexes (`:152-155`); `observations/replica-03.json` is never read. OK. Minor: `input_page_blob_count` is captured before the cross-check and TDEF derivation (`:275`) and undercounts.
8. Schedule arithmetic (`a3_generator_schedule.py`) is plan-derived (batches, thresholds, overshoot, strict regrowth). OK. Layout choices (global page 1, tdef page 2, growth offset 0, churn offset 2044) are hard-coded but are fixture layout, not counts.

## 4. Fail-closed gaps

**Validator (#53)**
- `tamper_results` is the constant `TAMPER_RESULTS` (`validator.py:49-55`, used at `:525`) in every verdict including `accepted=true`. The plan's `acceptance_rule` requires all T1–T5 variants to be rejected **before** `accepted=true` may be emitted; the report asserts rejections it did not perform. Either run the tamper suite at runtime on derived variants of the bundle or report `tamper_results` as "not executed by this run" and leave the suite to a separately provenanced test artefact — but do not print `rejected: true`.
- False rejections of correct reports: reason ordering (`validator.py:408-414` expects layer order; analyzer emits sorted, `a3_analysis.py:409`; the schema does not fix an order), `holdout_opened` flag (§1.10), record-candidate count (§1.11), campaign terminals (§1.9). Fail-closed in direction, but they make independent validation of several legitimate outcomes impossible.
- Broad `except (KeyError, TypeError, ValueError, IndexError, OSError, OverflowError)` → `malformed_bundle` (`validator.py:586-590`) is fail-closed but discards the cause; fine for the verdict, hostile for diagnosis.
- `_expected_predicate_statuses` hard-codes `A3-SNAPSHOT-RECONSTRUCTION`/`A3-RESOURCE-BOUND` as `pass` (`:433-434`) — it never verifies the analyzer actually stayed under bounds except the three report counters.

**Analyzer (#54)**
- `a3_analysis.py:282` (`polarity_cross_check` outside `_pair`) and `:281` (`global_structural_valid`) can raise Aborts that become campaign aborts and erase all four layers, contradicting "an unmet layer never erases another layer's result". Currently dead (§1.5) but structurally wrong.
- `build_analysis` `:363-366` and `:390-391`: any Abort during holdout evaluation (including `A3-REPLICA-DISAGREEMENT` from `_validate_inputs` on the holdout, or a holdout snapshot problem) overwrites every layer's derivation terminal with the campaign reason; `compare_frozen_to_report` then fails and the run exits 1 with **no report at all**. Fail-closed, but a decisive derivation is lost rather than reported with the holdout failure attributed.
- `holdout_structurally_validated_after_freeze: True` is a literal (`:404`); the receipt validator only checks binding fields.
- `predicts_*` swallow all Aborts except SNAPSHOT/RESOURCE into `False` (`a3_layers.py:490-515`) — acceptable, but a holdout `A3-POINTER-VALIDITY` is then reported as `holdout_prediction_failure`, losing the registered reason.
- Optimistic default: `qualify_tdef_pages` silently `continue`s on pages absent at E0 (correct per plan); `_extended_bits` silently skips bad references (§1.1).

## 5. Test-suite results

`python3 -m unittest tests.test_a3_analyzer tests.test_a3_dryrun tests.test_a3_independent_validator tests.test_a3_plan_contract` → **Ran 40 tests, OK (21.6 s)**. The EXP-0042 replay test ran (bundle present). None of the above findings is covered by a test; the cross-implementation harness is what exposed §1.1–1.2.

Harness evidence (scratchpad `xv/`): analyzer all-decisive baseline → validator `accepted=false, ["frozen_set_recomputation_mismatch"]`; `--recompute-only` diff: `global_map_extended_base` V = `A3-BASE-NONE`, A = model `referenced_page_relative`; the other three layers, qualified pages and cross-check transcript match. Full-sweep disagreement table: 45 cases, 13 conversion-layer mismatches (MULTIPLE vs NONE), 13 base-layer BASE-NONE vs POINTER-VALIDITY, 17 base-layer decisive vs BASE-NONE, 2 agree-no-outcome, the rest agree.

## 6. What must go into an additive R3 before either PR can be correct

1. 0x05 extended-page bitmap layout (header length, LSB-first, capacity) and the operational formula-survival predicate; whether in-use→free flips count.
2. Conversion failure attribution for all-indirect / `neither`-interrupted windows.
3. Replica agreement = intersection of survivors; definition of `replica_disagreement`; no internal-state comparison.
4. Inline boundary: state that the candidate is the minimal extent explaining `page_count` (or keep "enumerate all" and define why longer b do not "explain"); order of INLINE-SUFFIX vs NONE/MULTIPLE (R2 flag #3).
5. Polarity-vs-multiplicity order (R2 flag #1) and the trigger, if any, for `no_unique_bit_polarity`.
6. TDEF: where STRUCTURAL-EXCLUSION / POINTER-VALIDITY sit relative to GROWTH/CHURN-POINTER-NONE (R2 puts them last); reconcile "0x05 whenever active" with the validity window; define "distinct" windows.
7. Slot activation counted only at tag-1 checkpoints.
8. `no_outcome_reasons` ordering in the report; `holdout_opened_after_freeze` semantics when no layer has a model; `record_candidates_examined` when a layer aborts before enumeration.
9. Whether holdout prediction requires slack equality / tdef uniqueness.
10. Page absence at a later checkpoint is a candidate rejection, not `unreconstructable_snapshot`.

## 7. Fix list by PR

**#54**
- `a3_dryrun.py`: derive `terminal_predicate_ids`, `predicted_terminal_states`, and `predicate_reachability` from the analyzer outputs of explicit perturbation fixtures; fail if any registered id is unreached; assert each case's expected terminal; wire `anchor_fill_state` into the generator; generate the record-uniqueness, cross-check-violation, no-flip-base, bounds-edge fixtures the plan enumerates; give replica 3 independent overshoot; drop synthetic coverage from the replay report.
- `a3_layers.py`: after R3 — base-layer header/predicate; conversion attribution; move TDEF structural/validity after POINTER-MULTIPLE; INLINE-SUFFIX order; treat absence as rejection (`a3_model.py:126-137` needs an `Optional` page accessor); remove `a3_model.py:329`.
- `a3_analysis.py`: intersection-based replica agreement; do not open the holdout when no layer has a model; keep derivation terminals when the holdout step aborts; catch Aborts from `:281-282`.

**#53**
- `a3_independent_core.py`: base-layer header/predicate per R3; conversion attribution per R3; no validity failures outside the window; cross-replica disagreement for conversion/TDEF/churn; emit STRUCTURAL-EXCLUSION / INLINE-SUFFIX / INLINE-BOUNDARY-MULTIPLE per R3 or document them as unreachable; tag-change stop only on differing tags.
- `a3_independent_validator.py`: execute T1–T5 at runtime (or stop printing `rejected: true`); compare `no_outcome_reasons` as a set (or per R3 order); `holdout_opened` and record-count rules per R3; campaign-terminal projection consistent with R2 (all `not_applicable` for that layer) and accept idle-volatile reports by skipping layer derivation when idle fails.
