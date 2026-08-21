# Review of PR #38 — `docs: preregister A2 allocation-map campaign`

Head: `2b3013792acc17fd7077380cb4c8895b05f3cf38` (branch `sol/a2-preregistration`).
Reviewer: Claude Fable 5, read-only. Plan line numbers refer to
`oracle/windows-dao/experiments/a2/a2-allocation-maps.plan.json` at that commit;
`PROVENANCE.md` lines are absolute in the branch file.

## Verdict: **REQUEST_CHANGES**

The PR is a large, honest improvement over A1: the D arithmetic is finally
record-level and strictly-greater, conversion is searched over the full window,
the churn checkpoint can actually free pages, both polarities and slot counts
are free parameters, every Abort needs a predicate id, decisive retention is a
manifest-schema requirement, and the dry-run gate is in the execution gate. The
run-12 diagnosis disclosure (finding 5.2) and the campaign-behaviour clause
(finding 3.1) are both addressed in substance.

It cannot be approved as a preregistration yet because the plan still contains
predicates and bounds that its **own** schedule and rules make unsatisfiable —
the A1 failure class — and one structural mis-assignment (conversion/slot
machinery attached to a TDEF record when the only converting record observed is
the global map). Since the plan is hash-pinned and "amendments are additive",
these must be fixed before merge, not after; a merged but unsatisfiable plan
would force an immediate R2.

Findings are numbered; **[Blocking]** items must change before approval.

---

## A. Internal consistency — predicates unsatisfiable by the plan's own rules

### 1. [Blocking] `stable_endpoint_rule` guarantees `multiple_global_record_boundaries_survive` for any record whose end is not touched by a D transition
- `plan.json:117` (boundary source = all 2,049 boundaries), `:122` ("neither endpoint is required to change … unresolved endpoints produce the multiple-record-boundaries outcome"), `:135` (D set relation), `:261` (terminal_disambiguation).
- The D set relation is a statement about bits that *flip* between E0/D_GROW/D_DROP/D_REGROW. In run 12 those flips occupy bytes 1922–1954 of a tail record that runs to 2047. Every interval `[s, e)` with the same start and any `e ≥ 1955` decodes to the same released/reallocated set, so **every such `e` survives** and, by the plan's own rule, the outcome is `multiple_global_record_boundaries_survive`. No other delimiter is preregistered (no length field, no "maximal/minimal interval" rule, no all-zero-suffix closure rule for the *global* record — `:129` applies only to the inline boundary inside the TDEF record).
- This is diagnosis item 2 re-encoded: A1 used min/max of changes (too tight), A2 enumerates everything (too loose) — neither resolves an unwitnessed endpoint. Preregister a deterministic tie-break that is a property of the bytes, e.g. "the unique interval that satisfies the relation and whose extension by one byte in either direction breaks the relation or leaves the boundary byte set equal to zero at every D checkpoint", or commit to "end = 2048 when the surviving intervals share a start and differ only by end and the suffix is zero at all D checkpoints". Whatever is chosen, the synthetic generator must show exactly one survivor on a schedule-derived fixture **with slack after the last flipped byte** (the `anchor_fill_state` idea applied to record ends).

### 2. [Blocking] Record enumeration is infeasible under the plan's own bounds → `resource_bound_breach` by construction
- `plan.json:125` `interval_ceiling_per_page = 2,098,176`; `:298` `max_record_candidates = 16,777,216`; `:300` `max_analysis_work_units = 500,000,000`; `:118-119` candidate page space = union of all observed pages, qualification defined *by* interval enumeration.
- Run-12 arithmetic: 10 pages change hash across the D transitions (0,1,2,3,6,9,11,13,18,19). 10 × 2,098,176 = 20.98 M intervals > 16.78 M. Decoding an interval as a page set is Θ(length); Σ lengths over all intervals of one page = 1.43 × 10⁹ byte-ops, × 5 D checkpoints × 2 polarities ≫ 5 × 10⁸. Even an O(1)-per-interval prefix-sum analyzer needs ≈ 2 M units per page per polarity per checkpoint pair.
- Also circular: `:119` says a page "qualifies for interval enumeration only if at least one candidate interval can encode …" — you must enumerate to know whether to enumerate.
- Fix: preregister a cheap page-level qualification (hash differs across at least the E0→D_GROW and D_GROW→D_DROP transitions), a per-page record-candidate bound the generator computes from the schedule, and a work model (state explicitly that the relation is evaluated with per-byte flip prefix sums in O(1) per interval). Then have the retained-A1 dry run **assert** the ceilings are respected on run-12 data.

### 3. [Blocking] Conversion, slot, inline-boundary and extended-base machinery is bound to the TDEF record, but run 12 shows that behaviour only on the global-map record — and the global search is forbidden from using L/P/H transitions
- `plan.json:136` global search "only from the D … checkpoints and never from L, P, or H transitions"; `:128` inline boundary "for each surviving TDEF record"; `:144` slots "within the TDEF candidate interval"; `:143` conversion window; `:154` base formulas; `analysis-report.schema.json:28` model: only `tdef_record` carries `conversion_*`, `active_slot_count_at_conversion`, `inline_boundary`, slots, base.
- Observed (audit §2): the `0x00→0x01` step at 1915, the two slots → 15136/16352, the 16352-bit extended maps and the growth-clears-bits flips are all on **page 1's tail record**, i.e. the same record whose D set relation A2 calls the global map. A2's `run12_calibration_case` (`:176`) takes those global-record values and requires the generator to emit them as TDEF-record behaviour. The synthetic dry run will therefore pass on a fixture describing a structure nobody has observed, while on real data the TDEF search must find a per-table record with two active 16352-page slots at conversion — for table H (≤1,283 pages) under `slot_relative_expected_0_16352` that is one slot at most, so `unexpected_slot_activation_count` (`:144`) is the predicted outcome **by construction**.
- Fix: either (a) let the global-map record be the subject of the conversion/slot/base model (search it with D for delimitation and with L/P/H for conversion — the prohibition at `:136` must then be narrowed to "delimit only with D"), and keep the TDEF record to the two pointer predicates; or (b) if a per-table two-slot TDEF map is a genuine hypothesis, say so, give it its own slot precondition derived from table size, and do **not** feed global-map calibration values into its generator case. Either way the model schema needs separate `global_map` and `tdef` sub-models with their own decisive/no-outcome reasons (see 6).

### 4. [Blocking] `bit_polarity_rule` cannot be evaluated as written
- `plan.json:134` requires agreement between "the D release/reallocation set relation **and** the declared L/H growth transitions". The D relation is evaluated on the global record (`:136`), which L/P/H transitions may not touch; the L/H growth flips are evaluated on the TDEF record (`:139`). If no TDEF record survives, polarity is undecidable and the cascade order is unspecified. (Note that the D relation alone already selects polarity: under the wrong polarity `G` is empty because growth frees nothing — preregister that and make L/H agreement a *check*, not a precondition.)

### 5. [Should fix] "Exactly two active slots at conversion" is a decisive precondition tied to one run-12 accident
- `plan.json:144`, `analysis-report.schema.json:28` `active_slot_count_at_conversion: const 2`. Two slots are active at conversion only when conversion happens at the first checkpoint past 16,352 pages (`P_ABS_16480`). The free parameter `slot_activation_at_conversion ∈ {0,1,2}` (`:172`) will therefore generate two out of three values that are no-outcomes by construction, and any earlier conversion (a legitimate Jet behaviour the plan says it does not assume, `:143`) kills the decisive path. Preregister "≥ 1 active at conversion, exactly two by `H_REL_0904`" (or equivalent) so the precondition does not encode the conversion point.

### 6. [Should fix] Decisive requires a conjunction of seven independent hypotheses; no layered outcome
- `plan.json:229`. Global set relation ∧ unique global record ∧ TDEF record ∧ growth pointer ∧ churn pointer (with "returns exactly" at reinsert, `:140`) ∧ two-slot conversion ∧ unique base formula. Each has a named no-outcome; none has a partial decisive. Given A1's history, preregister independently decidable sub-models (global-map record; TDEF pointer pair; extended-map base) each with its own holdout prediction and report field, so one unmet hypothesis does not zero the whole campaign. The schema already has room (`surviving_model` could become three nullable sub-models).

### 7. [Should fix] Slot-0 base discrimination relies on leftover free pages being reused at `H_REL_0064`
- `plan.json:104, :154, :178`. In run 12 the discriminating event was three free pages (15141-15143) happening to be reused on `P_ABS_16480→H_REL_0064`. A2's schedule changes the free-page population (full L delete/reinsert), so the event is not guaranteed. Add a deterministic slot-0 flip source after conversion (e.g. a small drop/recreate or a short `L_REL` step after `P_ABS_16480`) or downgrade `insufficient_base_discrimination` from "decisive precondition" to a sub-model no-outcome (see 6).

### 8. [Should fix] `fan_in_rule` validates replica 3 before the freeze
- `plan.json:53`: "validates all three independently uploaded replica artifacts; it derives … from replicas 1 and 2 … and only then may open replica 3". Validation of replica 3 (hash inventory, schema, reconstruction) *is* opening it. State that replica-3 structural validation runs in a separate step/process that writes only pass/fail, after the candidate-set hash is persisted, or accept that structural validation may precede the freeze but never reads page bytes into the analyzer process.

### 9. [Should fix] Three matrix jobs, one `environment_sha256`
- `bundle-manifest.schema.json:12` has a single `environment_sha256`; `replica-observation.schema.json:13` and `environment.schema.json` bind `windows_version`, `python_version`, `provider` per job. Independent jobs may land on different runner images (A1's `PROVEN_IMAGES` allows two). Preregister per-replica environment documents plus an explicit cross-job identity rule (provider hash, prog id, architecture, PowerShell major must match; host image may differ and is recorded per replica), or the fan-in fails on the first heterogeneous matrix.

## B. Fitted-to-run-12 checks

### 10. [Should fix] `header_exclusion_source` is a page-1 offset blacklist derived from run 12, over-broad, and applied only to page 1
- `plan.json:123-124`. Excludes 2..13, 20 and the **entire** 376..1784 span (1,409 bytes) although run 12 changed only 376, 888, 1400 and 1716-1784 in it; the exclusion is the one place where A2 does fit the candidate space to run-12 bit offsets, and it makes page 1 the only page with a special rule (the inverse of "page 1 is not privileged"). Replace with a structural rule that applies to every page (e.g. "exclude any window whose bytes change on a transition in which the candidate record's set relation does not change", or "exclude windows intersecting bytes changed across an idle pair's neighbours"), or justify each excluded range by a preregistered decoding (what the bytes are), not by "behaved control-like".

### 11. [Note] `run12_calibration_case` constants (`plan.json:176`, `dry-run-report.schema.json:25`) are explicitly non-evidential and mapped by checkpoint identity, which is acceptable — **provided** finding 3 is fixed so that they calibrate the record they were observed on.

### 12. [Note] L targets 896/904/1024/1088 and the `schedule_minimization_basis` (`:104`) are justified by run-12 page-1 transitions. Choosing checkpoints from exploratory data is legitimate preregistration; no predicate encodes those offsets. OK.

## C. Dry-run contract vs. audit §5 checklist

| Checklist item | Status | Where |
|---|---|---|
| D_REGROW strictly > D_GROW, no equality, generator makes relation true | ✅ | `:85, :135, :177` |
| D_DROP no truncation | ✅ | `:85, :135` |
| Churn satisfiable by generator / delete rule guarantees a freed page | ✅ (plus precondition outcome) | `:76, :140, :177` |
| Conversion ordinal free parameter over every ordinal + never | ✅ | `:171` — but see 5 (slot precondition still depends on it) |
| Slot activation {0,1,2} | ✅ | `:172` |
| Candidate page union, not E0R-present | ✅ | `:118` |
| Record source independent of change envelope **with a real delimiter** | ❌ | finding 1 |
| Header bytes excluded by rule, not by offsets | ⚠️ | finding 10 |
| Dead Abort checks removed / reachability | ✅ | `:180` |
| Polarity two-valued, chosen by flips | ⚠️ | finding 4 |
| Inline boundary independent of anchor fill | ✅ | `:128-130, :179` |
| Slot-0 base discrimination guaranteed by schedule | ⚠️ | finding 7 |
| Pointer validity only at named checkpoints | ✅ | `:102, :145` |
| Distinct reason ids + predicate ids | ✅ (mapping table missing, see 14) | `:194-261` |
| Synthetic decisive end-to-end + single perturbations | ✅ | `:178-181` |
| Run-12 dry run terminates in a named state | ✅ | `:165` — but the named state is probably wrong until 1-3 are fixed (resource bound / record multiplicity will fire first) |
| "Every equality generator-producible" test | ✅ | `:177, :189` |
| Delete A1 fixture constants | declined (A1 immutable); replaced by source-contract check `:184` | acceptable |

Two contract gaps beyond the table:

### 13. [Should fix] The A1 legacy projection is unspecified
- `plan.json:161-165`, `dry-run-report.schema.json:13` `explicit_a1_legacy_projection`. A1 has no `D_RECREATE_EMPTY` and has `L_DELETE_ALTERNATING` where A2 has `L_DELETE_ALL`. Preregister the checkpoint-id mapping table (and which A2 predicates are `not_applicable` under it); otherwise the projection is a post-hoc choice.

### 14. [Low] No preregistered reason↔predicate-id mapping
- `plan.json:194-226` vs `:230-260`. 29 ids, 29 reasons, but `A2-HEADER-EXCLUSION` and `A2-D-SET-RELATION` have no reason and `terminal_disambiguation` covers only page/record cardinality. A1's lesson (`PLAN_REASONS`) was to pin the map in the plan.

## D. Decisive-report retention (finding 3.1)

- ✅ Schema-level: `bundle-manifest.schema.json:17-19, :23-26` — `campaign_failed: false`, `analysis_report_retained: true`, `bundle_status` enum with `anyOf` tying `one_joint_model_predicts_holdout ↔ decisive_pending_independent_validation`, `independent_validation_status: const not_independently_validated`. Good.
- 15. [Low] `plan.schema.json` leaves `decisive_report_handling` as `{"type":"object"}`; pin its four keys and the `bundle_status` const so the hash-pinned plan cannot drift from the manifest schema.
- 16. [Low] Nothing yet forbids the *controller* path from calling an A1-style validator on the report; `:266` says it in prose. Add to `execution_gate.blocking_requirements` an item "a2_contract_validator_accepts_decisive_reports" with a test that a decisive synthetic report passes `validate-document`.

## E. Provenance / AGENTS.md

### 17. [Should fix] Origin cites ephemeral `/private/tmp` files with no hash pins
- `plan.json:12-14`, `PROVENANCE.md:2973-2986`, `:3107-3109` ("no … diagnosis is committed"). The design's stated inputs — the diagnosis, this reviewer's two reports — are not in the repository and carry no SHA-256. AGENTS.md requires every source/experiment/observation to be recorded in provenance; an unpinned local path is not a record. They contain no third-party implementation material, so committing them under `docs/validation/` or `oracle/windows-dao/experiments/a2/inputs/` is clean-room safe; at minimum pin their SHA-256 in the plan and the entry.

### 18. [OK] Additivity, clean-room, claims
- New directory, new entry, no edits to A1 plan/schemas/ledger history ✅. `external_mdb_implementations_used: false` ✅. Claims block ✅. `acquisition_started: false`, gate `BLOCKED`, dry-run status `not_run_preregistration_only` (`PROVENANCE.md:3071`) ✅ honest. Test pins plan hash and EXP-0040 ✅ (`test_a2_plan_contract.py:29-33`), though most other tests are prose `assertIn`s and would not catch a semantic regression — acceptable for a hash-pin test.

## F. Practical goals

### 19. [Should fix] Wall-clock: estimate equals the target; no campaign-level headroom; projection basis is pre-#33
- `plan.json:272-275`, `PROVENANCE.md:3038-3049`. Target 1,800 s, estimate 1,740 s, campaign timeout 2,700 s. The stated goal was ≤ 30 min **with ≥ 3× headroom**; 3.33× exists only on the per-replica worker bound (540 s vs 1,800 s), not on the campaign. The 540 s figure is extrapolated from the run-9 trace, whose cost was dominated by the per-page PowerShell snapshot loop removed in #33; run 12 (post-#33) still took 1,467-1,537 s per replica, i.e. growth/DAO-reread work dominates now. A2's subset does cut that (page-checkpoints 605k → 124k; DAO reread rows ≈ 4.8 M → 0.98 M from run-12 indexes) but the ~17.8k pages of inserts are unchanged. Either re-derive the projection from run-12 per-checkpoint progress records, or preregister a measured gate: first dispatch is a single-replica timing job, and the matrix is authorised only if it completes under 600 s.
- Parallel replicas as matrix jobs ✅ (`:52, :270`). Analyzer dry-run-verifiable before acquisition ✅ (`:156-192`, gate `:27`).

### 20. [Low] Bounds sanity against run 12: `max_changed_hash_entries_per_replica` 65,536 vs A1's 20,701 over 71 checkpoints (A2's 25 sparser checkpoints change more pages per transition but stay well under); `max_logical_checkpoint_read_bytes` 2 GiB vs ≈ 254 MB projected; `max_inserted_rows` 524,288 vs ≈ 150 k. OK.

---

## Summary of required changes before approval

1. Preregister a deterministic record-end resolution (finding 1) and a feasible, bounded enumeration with page-level qualification and a work model (finding 2); have the run-12 dry run assert both.
2. Re-assign the conversion/slot/inline/base model to the record that exhibits it, or make the TDEF two-slot hypothesis explicit with its own preconditions and calibration (finding 3); make polarity decidable from the D relation alone (finding 4).
3. Decouple the two-slot precondition from the conversion point (5); preregister layered sub-model outcomes (6) and a deterministic slot-0 flip source (7).
4. Fix the fan-in/holdout ordering wording (8) and per-replica environment identity (9).
5. Replace the page-1 offset blacklist with a page-agnostic rule (10); specify the A1 legacy projection table (13).
6. Pin the design inputs by hash or commit them (17); re-derive or gate the wall-clock projection (19).

Low items (14, 15, 16, 20) can ride along. No file in the repository was modified by this review.
