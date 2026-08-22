# Independent review — PR #50 `codex/a2-independent-validator` (commit 24b6d8c)

Scope: `oracle/windows-dao/scripts/a2_independent_{validator,core,bundle}.py`,
`tests/test_a2_independent_validator.py`, against the frozen A2 plan
(`a2-allocation-maps.plan.json`, SHA 804e84…), R2, README, schemas. Reviewer
read the analyzer (`a2_model.py`, `a2_layers.py`) only to compare derivations.

Real bundle (`a2run4/...-32587946283-1/jet3-a2-bundle`): validator returns
`accepted: true`, exit 0, 2.5 s. Derived `page 1, start 1915, end 2048,
set_means_not_in_use, slack 92`; global qualified pages `[0,1,20,21]`, tdef
`[0,1,23,24]`; tdef `no_tdef_record_candidate` confirmed; conversion
`growth_polarity_disagreement` "confirmed" on legs `L_REL_0896→L_REL_0904` and
`P_ABS_12288→P_ABS_16480` (see H2 — the second leg is spurious).

Tamper results (bundle copied, report edited, manifest entries rehashed):

| # | Tamper | Result |
|---|---|---|
| T1 | `bit_polarity` → `set_means_in_use`, manifest rehashed | rejected `global_record_model_mismatch` |
| T2 | conversion reason → `missing_inline_to_indirect_conversion` with consistent terminal ids / predicate_results | rejected `unsupported_conversion_outcome` |
| T3 | `analysis/derivation-candidates.json` replaced with a contradicting frozen set (start 1700, `set_means_in_use`, survivor_count 7, qualified pages `[5]`); manifest, report and receipt `derivation_candidate_set_sha256`, manifest receipt hash all re-linked | **ACCEPTED, exit 0** |
| T4 | tdef reason → `no_growth_only_pointer_candidate` consistently | rejected `tdef_reason_mismatch` |
| T5 | `predicate_results` for `A2-HOLDOUT-PREDICTION`, `A2-IDLE-EQUALITY`, `A2-D-SET-RELATION` set to `"fail"` (report otherwise decisive) | **ACCEPTED, exit 0** |

Tamper script: `scratchpad/tamper.py` (review session scratchpad).

---

## High

### H1. Record-start uniqueness rests on an un-preregistered predicate that the validator shares with the analyzer (independence + fidelity)
`a2_independent_core.py:136-152` (`_initial_extent_matches`), `:176-181`, docstring `:7-13`.

The plan's record procedure (`record_candidate_procedure.boundary_source`,
`hypotheses.global_map_record_predicate`, `global_record_end_resolution`)
enumerates every `[start,end)` from `{0..2048}` and applies only the D set
relation; it explicitly says "no byte, record, or page equality between
checkpoints is a predicate" and that more than one surviving start emits
`multiple_global_record_boundaries_survive`. The set relation is
translation-invariant for a bitmap, so it cannot fix `start`. Measured on the
real replica 1 / page 1 with the plan-literal relation plus the suffix rule:
**1935 starts survive (0..1934), all `set_means_not_in_use`**.

The validator gets a unique start only by adding: (a) a hard-coded 5-byte
prefix (`GLOBAL_PREFIX_BYTES`), and (b) the requirement that bits
`[0, page_count(E0))` decode in-use and every remaining bit to page end decode
not-in-use at E0 (`_initial_extent_matches`), plus (c) `beyond` tied to
`page_count(D_GROW_0128)` (`:182-184`). None of (a)–(c) is in the plan text;
(b) is a physical-page-count equality predicate of exactly the kind the plan
forbids. The docstring's claim that "the plan fixes a five-byte prefix before an
inline bitmap" is an inference from `inline_boundary_procedure.candidate_source`
(which scopes inline *boundary* candidates, not record delimitation).

The analyzer reaches uniqueness the same way (`a2_model.py:367-392`
`polarity_direction`: tag byte 0 at `start`, u32 base at `start+1..5`, bitmap at
`start+5`, in-use ⊇ `range(base, page_count)` and `page_count ∉ in-use` at E0 /
D_GROW / D_REGROW). So while no code is shared (grep: no imports of
`a2_spec/a2_model/a2_layers/a2_analysis`), the one decision that turns 1935
candidates into the reported `start=1915` is the same unwritten structural
assumption in both tools. The validator therefore confirms the analyzer's
*interpretation*, not the preregistered procedure. Under the plan as written the
record layer is `multiple_global_record_boundaries_survive`, not decisive.

Fix: this cannot be fixed in the validator alone. Either (i) a new additive plan
revision (R3 + provenance entry) preregisters the tag/base/bitmap layout and the
E0-extent anchoring as the start-resolution rule, after which the validator
should implement that rule verbatim (reading the u32 base, not assuming 0), or
(ii) the validator must report `global_record_not_unique` for the real bundle.
Until (i) lands, this validator's acceptance must not move
`global_map.record` to independently validated.

### H2. Frozen derivation candidate set is never inspected — the freeze artifact can be anything (fail-closed)
`a2_independent_bundle.py:493-500` (hash linkage only); no parse anywhere.

`decision_rules.freeze_rule` makes `analysis/derivation-candidates.json` the
artifact proving layers were fixed before holdout access. The validator only
checks that report/receipt quote its SHA-256. Tamper T3 replaced it with a set
naming a different record (`start 1700`, opposite polarity, survivor count 7,
qualified pages `[5]`) and the bundle was **accepted**. A bundle whose frozen set
contradicts its report is exactly the "holdout altered a layer" scenario the
freeze exists to exclude.

Fix: load it with `load_json`, schema-check if a schema exists (otherwise check
the fixed shape), and require `layers.global_map_record.model`,
`derivation_survivor_count`, `no_outcome_reason`/`terminal_predicate_id` per
layer, and `qualified_pages` to equal both the report and the independent
recomputation; any mismatch is a discrepancy.

### H3. Conversion-layer "confirmation" is a different predicate from the plan's and fires on the conversion itself (fidelity; accepts a mislabelled no-outcome)
`a2_independent_core.py:231-257` (`growth_polarity_violations`),
`a2_independent_validator.py:178-213`.

Plan `hypotheses.polarity_cross_check`: growth "must flip newly allocated pages
from not-in-use to in-use under that polarity". The validator never checks that
newly allocated pages flip to in-use; it flags a leg if *any* bit in
`[start+5, end)` goes in-use→not-in-use, on every `L_/H_/P_` leg, with no regard
to the record's representation. On the real bundle the two flagged legs are:

- `L_REL_0896→L_REL_0904`: bytes 2044–2047 go `00→e0/ff` (a genuine reverse
  flip, 24 bits) — legitimate.
- `P_ABS_12288→P_ABS_16480`: the tag byte at 1915 goes `0→1` and the u32 base
  becomes 14848 — this is the inline→indirect conversion; the 9 "reverse flips"
  are pointer bytes reinterpreted as bitmap bits.

Because every run that converts produces a spurious violation on the conversion
leg, the validator would "confirm" `growth_polarity_disagreement` for
essentially any converting dataset, including one where the analyzer's true
terminal was a conversion/slot/inline-boundary predicate but the report was
mislabelled. Note also the plan says the global-map bitmap can only cover
`(2048-1920)*8 = 1024` pages inline; legs `L_REL_0768→0896` (1053→1181 pages)
show zero flips, so "newly allocated pages flip to in-use" is unevaluable
inline there and the validator silently treats that as fine.

Fix: implement the plan predicate — for each declared leg where the record is
still inline (tag/representation unchanged), require the page ordinals
`[page_count(left), page_count(right))` that fall within bitmap capacity to be
not-in-use before and in-use after; stop at the first leg where the
representation changes; report the first violating leg and compare it with what
the report/candidate set records (the report currently carries no leg, which
should be a separate discrepancy: the reason is unrecomputable without it).

## Medium

### M1. Non-terminal predicate results are not checked (fail-closed)
`a2_independent_validator.py:322-331` only requires terminal predicates to be
`fail`. Tamper T5 set `A2-HOLDOUT-PREDICTION`, `A2-IDLE-EQUALITY`, and
`A2-D-SET-RELATION` to `fail` in a report that claims
`decisive_predicts_holdout`; accepted. `predicate_registry.reporting_rule`
("exactly one registered predicate … per Abort") implies every non-terminal
predicate must not be `fail`. Fix: require every registered id to appear exactly
once, `fail` iff it is a terminal id, and `A2-HOLDOUT-PREDICTION` to be
`pass` when any layer is decisive.

### M2. tdef no-outcome reasons collapsed to one (fidelity)
`a2_independent_validator.py:258-266`. The plan distinguishes
`no_growth_only_pointer_candidate`, `no_delete_reinsert_only_pointer_candidate`,
`legacy_churn_precondition_not_met`, `multiple_pointer_models_survive` and
`no_tdef_record_candidate`. The validator derives only
`no_tdef_record_candidate` whenever no model survives, so it confirms that label
even when the preregistered terminal would be e.g. `no_growth_only_pointer_candidate`
(zero growth windows on every page). It also never evaluates the churn
precondition (`delete_reinsert_only_pointer_predicate`: nonempty L data page at
`L_REL_1280` and DAO reread of zero rows at `L_DELETE_ALL`), which is recorded
in the observation documents. Fix: derive the reason in the plan's order
(precondition → growth windows → churn windows → record → multiplicity) and
compare that.

### M3. Pointer validity applied at every checkpoint, not the plan's window (fidelity, over-strict)
`a2_independent_core.py:274-278`, `:302-305`, `:325-328`. Plan
`pointer_validity_rule`: tag/in-range validity only at
`transition_coverage.pointer_validity_checkpoints`, at/after activation, for
nonzero references. The validator requires byte-0 == 0x05 for every nonzero
reference at all 25 checkpoints, so a correct pointer pair can be rejected and
the validator would then confirm a `no_tdef_record_candidate` that the plan
does not support. Also `_reference_valid` accepts any page index in the
current checkpoint's hash list, not "candidate_page_space" (union over
derivation replicas) — minor but different.

### M4. Page-terminal suffix `_last_d_flip` scans from `start`, including the tag/prefix bytes
`a2_independent_core.py:160-166`. The plan says "every byte after the last
D-flipped byte"; fine. But `slack = 2048-last_flip-1` and the check that the
suffix is uniformly the not-in-use byte at every D checkpoint is evaluated only
on `[last_flip+1, 2048)`; bytes between `start+5` and `last_flip` are not
required to be consistent with the E0 extent except via `_initial_extent_matches`
(E0 only). Acceptable, but document that the suffix rule is the only byte-level
constraint on D_GROW..D_REGROW bytes.

### M5. "Separately provenanced" — no provenance entry, README line, or `just`/CI wiring
Commit 24b6d8c touches only the four files. `decisive_report_handling
.independent_validation_rule` requires a *separately provenanced* validator.
There is no `EXP-`/`SRC-` entry in `docs/PROVENANCE.md`, no mention in the A2
README, and nothing runs it in CI. Its acceptance cannot move a capability
until a provenance entry records its sources (plan, R2, schemas, EXP-0040/41)
and the H1 assumption explicitly.

## Low

- `a2_independent_validator.py:95-100` `record_candidates_examined` is
  recomputed as `(|global|+|tdef|)·2,098,176` and must equal the report — good,
  but `candidate_models_examined` and `analysis_work_units` are not bounded
  against `bounds.max_candidate_models` / `max_analysis_work_units`.
- `a2_independent_bundle.py:306` `root.is_symlink()` after `resolve()` is
  always false (dead check; harmless).
- `a2_independent_bundle.py:364-372` environment cross-replica identity is
  checked, but `provider_sha256`/`producer_commit` in the manifest are not bound
  to anything outside the bundle (not fixable here; note for the provenance entry).
- `SchemaChecker` covers every keyword the A2 schemas use (`$ref, anyOf, const,
  enum, type, required, additionalProperties:false, items, min/max*, pattern,
  uniqueItems, format`); `type` given as a list would raise `TypeError` →
  fail closed. OK.
- `main()` catches all exceptions into `accepted:false` — good; `recompute`'s
  narrower catch is fine because the outer one backstops it.
- Tests cover only synthetic micro-cases of core primitives; there is no test
  running the whole validator on a tampered bundle (T3/T5 would have been caught
  by one).

## What is sound
- No shared code or imports with the analyzer family; qualification
  (absence-as-state, union page space, both D transitions, tdef E0-presence +
  growth-or + both churn legs) matches the plan text exactly.
- Polarity is selected from D alone; `D_RECREATE_EMPTY` release is read as part
  of "after D_DROP" (stricter, defensible).
- Holdout prediction is a true recomputation on replica 3 at the frozen
  (page,start,polarity); tdef decisive path deliberately unsupported (fails
  closed). Replica intersection, no majority vote.
- Bundle closure, content-addressed blobs, snapshot reconstruction hashes,
  changed-page lists, D growth arithmetic, receipt `result: pass` (schema const),
  idle-pair equality — all enforced. T1/T2/T4 correctly rejected.

## Verdict
Do not treat acceptance by this validator as independent validation yet.
Blocking: H1 (needs a plan revision, not a code change), H2, H3, M1. After
those, re-run on the real bundle and add a tampered-bundle test.
