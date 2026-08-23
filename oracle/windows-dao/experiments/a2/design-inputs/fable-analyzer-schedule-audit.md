# A1 analyzer vs. acquisition-schedule audit (`a1_model.py` @ `9470382`)

Read-only audit. Nothing in the repository or the bundle was modified.

## 0. Scope, inputs, bounded work

- Analyzer: `oracle/windows-dao/scripts/a1_model.py`, `a1_analysis.py` (main @ `9470382`; identical to the run-12 producer commit `9470382…` for these files).
- Schedule: `oracle/windows-dao/scripts/a1/A1.Worker.ps1:493-568` (`Add-A1UntilTarget`, `Invoke-A1Replica`) + plan `checkpoint_design` / `tables.row_algorithm`.
- Data: run-12 bundle `windows-dao-a1-bundle-947038265f…-20260821T132025Z-a1-gh32486063559-1`. Read: all 213 page indexes (hash lists only), `observations/replica-0{1,2}.json`, and exactly **55 page blobs** — the 38 distinct page-1 blobs of replica 1, the same 38 for replica 2 (byte-identical, so cache hits), and the tag-`0x05` pages referenced from page 1 (15136, 16352) at four checkpoints. No holdout (replica 3) bytes were opened; replica-3 facts below come from its page index only. Every blob was SHA-256-rechecked against its name.
- Script: `scratchpad/audit.py` (session scratchpad; not in the repo).

Classification key used in every row:
- **genuine-rule** — faithful to a plan clause and satisfiable by the schedule; fires only on real data disagreement.
- **schedule-mismatch** — the worker's preregistered checkpoint arithmetic makes the predicate unsatisfiable (or only accidentally satisfiable) regardless of Jet's layout.
- **layout-assumption** — the predicate encodes an assumption about Jet's byte layout/polarity that the plan does not state; run-12 shows the assumption is false.
- **bug** — implementation diverges from the plan clause it cites.

## 1. Schedule facts (worker + run 12)

Worker arithmetic (`A1.Worker.ps1`):

| Op | Lines | Rule | Run-12 replica 1 (= replica 2 = replica 3, from indexes) |
|---|---|---|---|
| `E0`,`E0R`,`*_IDLE_REOPEN` | 511-514 | checkpoint with no mutation | 20, 20, 1562, 17764 pages |
| `D_GROW_0128` | 531-539 | create D, `baseline = Get-A1ClosedPageCount` **after create**, grow to `baseline+128` | 23 → **151** (overshoot 0), 1,024 rows |
| `D_DROP` | 516-519 | `TableDefs.Delete`, no growth | **151** (file does not shrink), 0 rows |
| `D_REGROW_0128` | 531-539 | create D again, **new** baseline after create, `+128` | 151 → **279**, 2,048 rows |
| `L_REL_nnnn` | 541-555 | baseline taken once at L creation (546); target `baseline + nnnn` | baseline 281; 345 … 1561; steps of 8 pages per 64 rows |
| `L_DELETE_ALTERNATING` | 521-524 | delete even Ids | 1561 → **1562** (+1 page), 1,285 pages changed |
| `L_REINSERT_SAME` | 526-529 | reinsert even Ids | 1562, 1,283 pages changed |
| `P_ABS_nnnnn` | 557-565 | absolute file page count | 4096, 8192, 12288, **16481** (overshoot 1) |
| `H_REL_nnnn` | 541-555 | baseline at H creation = 16481; `+nnnn` | 16548 … 17764 (overshoot 3 throughout) |

Every plan-declared growth observable (`d_growth_rule`, `relative_growth_rule`, `absolute_growth_rule`, overshoot recording) is satisfied exactly; the worker is not the defect.

## 2. Observed page-1 facts (descriptive only; no format claim)

- Page 1 is byte-identical across replicas 1 and 2 at every one of the 71 checkpoints (38 distinct blobs each). Idle pairs identical in all replicas.
- The 152-byte change union over the 67 non-idle transitions spans offsets 2…2047: a low cluster (2-3, 10-13, 20, 376, 888, 1400, 1716-1784) and the tail 1915-1921, 1920-2047.
- Tail `[1920,2048)` at `E0` (20 pages) = `00 00 f0 ff ff…`; at `D_GROW` (151 pages) = zeros through 1937, `80 ff…` from 1938; at `D_DROP` = identical to `E0`; at `D_REGROW` (279 pages) zeros through 1953, `80 ff…` from 1954; from `L_REL_1280` (1561 pages) through `P_ABS_12288` the tail is **all zero**; at `P_ABS_16480` (16481 pages) byte 1915 becomes `01`, 1916-1919 = `20 3b 00 00` (15136), 1920-1923 = `e0 3f 00 00` (16352), rest zero, unchanged thereafter.
- Pages 15136 and 16352 carry byte-0 tag `0x05`, header `05 01 00 00`. Their bit-counts across growth: ref 16352 has 16,229 set bits at `P_ABS_16480`, 16,164 at `H_REL_0064`, 14,948 at `H_REL_1280` — set bits **decrease** by ≈ the number of pages appended (1,281 vs 1,283 pages). Ref 15136: 3 set bits (15141-15143) at `P_ABS_16480`, 0 afterwards.
- Consequence used below (observation, not interpretation): in this data a set bit tracks a page that is *not* in use; page growth *clears* bits; the inline tail is entirely zero whenever every representable page is in use.

## 3. Predicate-by-predicate audit

Column legend: **Plan** = clause implemented; **Schedule** = can the worker schedule satisfy it (with arithmetic); **Run 12** = satisfied on replicas 1/2; **Class**.

### 3.1 `derive()` (`a1_model.py:530-570`) — orchestration order

| # | Predicate | Plan | Schedule | Run 12 | Class |
|---|---|---|---|---|---|
| D1 | `idle_pairs_identical()` else `IDLE_VOLATILITY` (532-533) | `hypotheses.page_one[1]`, `checkpoint_design.idle_pairs` | Yes (no mutation between pair members, worker 511-514) | **Pass** (all three pairs, all replicas) | genuine-rule |
| D2 | `len(surviving_record_pages) == 1` else `AMBIGUOUS_RECORD_BOUNDARY` (534-536) | `negative_page_candidate_space` ("exactly one … evaluated and rejected") | Depends on 3.2 | **Fail — set is ∅** (this is the run-12 abort) | see 3.2; the reason label is a **bug** (∅ ≠ "ambiguous"; R2 maps this id to "ambiguous record or inline boundary") |
| D3 | surviving page must be `METADATA_PAGE == 1` (538-539) | `page_one[0]` ("on physical page 1") | Yes | not reached | genuine-rule (label again misreports as ambiguity) |
| D4 | order: interval → type offset → pointers → type1 → inline extent → bases | none (implementation order) | — | — | note: any early abort hides all later predicates; see §4 cascade |

### 3.2 `surviving_record_pages` / `_page_tracks_allocation` (217-240)

| # | Predicate | Plan | Schedule | Run 12 | Class |
|---|---|---|---|---|---|
| S1 | page must exist at all 8 probe checkpoints (227-228) | implicit | Yes for pages 0-19 (E0R has 20 pages) | pages 0-19 only | genuine-rule, but note it silently caps the candidate space at the **E0R** page count (20), not "every observed physical page" — pages ≥ 20 can never be candidates. Plan says "every observed physical page". **bug** (narrower than clause) |
| S2 | `grown == regrown` (229) | `page_one[2]` "allocate-free-reallocate ABA" | **No.** `D_GROW` = first closed state ≥ 23+128 = 151 pages; `D_REGROW` = first closed state ≥ 151+128 = 279 pages (worker 534-535, two *different* baselines). Any page that records allocation state must differ between 151-page and 279-page files. Equality is arithmetically impossible for the page the hypothesis is about. | **Fail** for page 1 (A/B/A/C: 17 bytes, then 33 bytes). Only page 6 (2 distinct hashes in 71 cps) passes. | **schedule-mismatch** (documented in the run-12 diagnosis; fixture hides it with 140/140/140, `a1_test_bundle.py:55`) |
| S3 | `grown != dropped` (229) | same | Yes | Pass (page 1) | genuine-rule |
| S4 | `grown != empty` (229) | same | Yes | Pass | genuine-rule |
| S5 | `low_first != low_last` (230) `L_REL_0064` vs `L_REL_1280` | `page_one[3]` | Yes (345 vs 1561 pages) | Pass (pages 0, 1) | genuine-rule |
| S6 | `high_first != high_last` (230) | `page_one[3]` | Yes | Pass (pages 0, 1) | genuine-rule |
| S7 | relaxed check for the record (what S2 *should* have been): `g≠d ∧ rg≠d ∧ g≠e` | — | Yes | pages {0,1,2,3,6,9,11,13,18,19}; ∩ S5 ∩ S6 = **{0, 1}** — i.e. even a corrected ABA leaves two survivors (page 0 also tracks growth), so D2's `== 1` would *still* abort. | **schedule/layout-mismatch** to be preregistered: the page-0 header changes with every growth step; the candidate-space rule needs a record-level predicate, not whole-page hashes |

### 3.3 `_observed_changes` / `_record_interval` (243-285)

| # | Predicate | Plan | Schedule | Run 12 (forced, page 1) | Class |
|---|---|---|---|---|---|
| R1 | changed set over all non-idle transitions non-empty (271-272) | `page_one[0]` "unique observed start and end" | Yes | Pass (152 bytes) | genuine-rule |
| R2 | interval = `[min(changed), max(changed)+1]` (273-279) | plan gives **no** delimiter ("caller-delimited"); PR #27 admits start is observation-derived | Always "satisfied" by construction — the list comprehension has exactly one element whenever R1 holds; `len(intervals) != 1` (280) is unreachable. | yields `[2, 2048)`: header bytes 2-13 + pointer-like fields 376…1784 + tail. Not a record. | **layout-assumption** (min/max of all page changes is not a record boundary) + dead check (**bug**: 275-281 cannot fail) |
| R3 | `end - start >= 5` (283-284) | — | Yes | Pass | genuine-rule (weak) |

### 3.4 `_map_type_offset` (288-309)

| # | Predicate | Plan | Schedule | Run 12 (forced interval) | Class |
|---|---|---|---|---|---|
| T1 | exactly one byte in the interval with column `0x00…0x00,0x01…0x01`, first `0x01` at ordinal > 0 (292-308) | `inline_extent_rule` "the inline-to-indirect conversion" | Satisfiable **only if** Jet converts within the 71 checkpoints; the schedule's max page count (17764) decides that | **Pass**: unique column at **offset 1915, ordinal 40 (`P_ABS_16480`)**. Six other tail/header bytes also step `0 → const≠1` at ordinal 40 (20, 1783, 1916, 1917, 1920, 1921). | genuine-rule |
| T2 | conversion ordinal is then used as the anchor for everything downstream | — | — | ordinal 40 ⇒ anchor = `P_ABS_12288` (ordinal 39) | feeds 3.6/3.7 |

### 3.5 `_pointer_candidates` (371-400)

| # | Predicate | Plan | Schedule | Run 12 (whole page 1) | Class |
|---|---|---|---|---|---|
| P1 | every 4-byte offset in the record, both layouts (379-384) | `tdef_pointer_rule` | Yes | 2 × 2045 windows | genuine-rule |
| P2 | decoded page ∈ `[1, page_count)` at **every** checkpoint from `L_REL_0064` to the end (386-390) | "valid in-file page reference" (PR text; plan says only "four-byte offset") | Only windows whose value is small at `L_REL_0064` (345 pages) can ever qualify; any pointer that legitimately points above page 345 early, or is 0 when nothing is free, is excluded. | only **3** offsets valid under each layout: {2, 3, 12/13} — page-1 header fields | **layout-assumption** (validity-at-every-checkpoint is not in the plan and prunes the space to header noise) |
| P3 | `used` = changes on some `GROWTH_TRANSITIONS` (L/P/H ladders) and on no churn transition (391-394) | "distinguished by growth versus alternating delete/reinsert" | Satisfiable | `used` = {3,13} / {2,3,12}: these are header counters cycling 262→261→259→257→262 etc. | genuine-rule mechanically; the survivors are artefacts of P2 |
| P4 | `free` = changes on a churn transition and on **no** growth transition (395-396) | same clause + `delete_rule` "no data page is intentionally emptied" | **No.** The plan's delete rule is designed so that deleting even Ids frees no page; a page-level free pointer therefore has nothing to record on `L_REL_1280→L_DELETE_ALTERNATING`, and any field that *does* change on churn (row counters, free-space bookkeeping) also changes on growth. In run 12 the delete actually grew the file by one page (1561→1562) and page 1 changed exactly one byte (2047); reinsert changed page 1 not at all. | **`free` = ∅ under both layouts** ⇒ `combinations = 0` ⇒ `survivors = 0` ⇒ `NO_SURVIVING_MODEL` (`a1_analysis.py:197`) | **schedule-mismatch** (plan-level: the churn the schedule performs cannot exercise a free-*page* pointer) |
| P5 | `GROWTH_TRANSITIONS` excludes `D_*`, `L_IDLE_REOPEN→P_ABS_04096`, `L_REINSERT_SAME→L_IDLE_REOPEN` (79) | — | — | the P-table creation + 2,534-page growth transition is invisible to "used" | note |

### 3.6 `_active_slots` / `_type1_slots` (403-452)

| # | Predicate | Plan | Schedule | Run 12 (type_offset 1915, interval end 2048) | Class |
|---|---|---|---|---|---|
| A1 | slot array = `[type_offset+1, end)` in 4-byte slots; trailing partial region must be zero (407-412) | `type1_rule` "inactive four-byte slots are exactly zero" | Yes | 33 slots, partial region empty; Pass | genuine-rule |
| A2 | `low_phase` = indirect checkpoints with ordinal ≤ `L_IDLE_REOPEN` (36) must be non-empty (429-432) | `type1_rule` "L uses the low observed slot" | **Only if conversion occurs at ordinal ≤ 36, i.e. before the file exceeds 1,562 pages.** The worker never pushes the file past ~1.6k pages before the P ladder; conversion at any P/H checkpoint makes `low_phase` empty. | **Fail**: conversion at ordinal 40 ⇒ `low_phase = []` ⇒ `NO_SURVIVING_MODEL` | **schedule-mismatch** (fixture forces `CONVERSION_CHECKPOINT = "L_REL_0512"`, `a1_test_bundle.py:47`) |
| A3 | exactly one active slot at `low_phase[-1]` (433-436) | same | unreachable per A2 | n/a — at the first indirect checkpoint **both** slots are already active (15136, 16352) | schedule-mismatch (the L-only phase never sees an indirect map) |
| A4 | exactly two active at `FINAL`, `high == low+1` (437-442) | "H uses the next observed slot" | Yes | Pass: slots 0 and 1 | genuine-rule |
| A5 | no other active slot at any indirect checkpoint; each reference ∈ `[1,count)` and byte 0 == `0x05` (443-451) | `type1_rule` | Yes | Pass (15136, 16352 both tag `0x05` at all indirect checkpoints) | genuine-rule |

### 3.7 `_inline_extent` (312-368)

| # | Predicate | Plan | Schedule | Run 12 (anchor `P_ABS_12288`, bitmap_start 1920) | Class |
|---|---|---|---|---|---|
| I1 | `bitmap_start = type_offset + 1 + 4 < end` (330-332) | implementation: inline record = type byte, 4-byte start page, bitmap | Yes | Pass (1920 < 2048) | layout-assumption (plan never states a 4-byte start-page field; happens to match tail shape at E0) |
| I2 | `explained` = boundaries whose preceding byte is **nonzero at the anchor** and ≥ pointer/slot extents (336-340); ∅ ⇒ `AMBIGUOUS_INLINE_BOUNDARY` | `inline_extent_rule` "explain every final inline bit" | Requires the last inline checkpoint to have set bits in its last bitmap byte. With the schedule, the anchor is whichever checkpoint precedes conversion; its fill level is a function of page count, not of the record. | **Fail — ∅.** The anchor tail `[1900,2048)` is all zero (so are `L_REL_1280`…`P_ABS_12288`). Under the observed polarity (set bit = not in use) a fully used file has a zero inline bitmap. | **layout-assumption** (assumes set bit = allocated) **and schedule-mismatch** (boundary chosen from anchor fill) |
| I3 | all-zero suffix `[boundary,end)` at every inline checkpoint (343-354) | "all-zero suffix" | Yes | vacuous here (`E0`…`D_REGROW` have `ff` bytes up to 2047, so *no* boundary < 2048 has a zero suffix at E0 — if I2 had produced candidates, I3 would eliminate all of them ⇒ `UNEXPLAINED_INLINE_SUFFIX`) | layout-assumption (the "suffix" is the free-bit region of a small file) |
| I4 | inline bits map to pages `< count(anchor)` and `pages ≠ ∅` (356-367) | "explain every final inline bit" | — | unreachable | layout-assumption (same polarity premise) |

### 3.8 `_base_candidates` / `extended_base` / `_extended_bitmaps` (455-527)

| # | Predicate | Plan | Schedule | Run 12 | Class |
|---|---|---|---|---|---|
| B1 | six formulas exactly (455-466) | `extended_base_candidates` | — | — | genuine-rule |
| B2 | at conversion, `inline_pages ⊆ covered` (493-500) | "derive only from preregistered allocation transitions" | — | vacuous if `inline_pages` were ∅; unreachable anyway | genuine-rule |
| B3 | on each post-conversion growth transition, `fresh = bits & ~before` (newly **set** bits) must equal the appended page range when `new_bits == delta` (501-524) | same | Under the observed polarity growth **clears** bits (16,229 → 16,164 → 14,948), so `new_bits` is ~0 on every growth transition, `exact` is never true, the `elif` range check is trivially satisfied, and **all six formulas survive** ⇒ `survivors = combinations × 6` ⇒ `MULTIPLE_SURVIVING_MODELS` whenever the earlier layers pass. | unreachable in run 12; structurally non-discriminating on run-12 bitmaps | **layout-assumption** (bit polarity) |
| B4 | formulas `slot_relative_*` vs `referenced_page_relative_*` are discriminated only by slot 0 (slot 1's reference page 16352 equals `1 × 16352`) | — | slot 0's map changes only on `P_ABS_16480→H_REL_0064` (3 bits clear) | — | note for A2: the schedule offers ≤ 1 transition that can separate the two families |

### 3.9 Joint-model layer: `require_unique_boundaries`, `joint_shape`, `candidate_counts`, `sole_model`, `predicts_holdout` (573-679)

| # | Predicate | Plan | Schedule | Run 12 | Class |
|---|---|---|---|---|---|
| J1 | record start/end and inline boundary equal across replicas 1 and 2 (580-583) | `inline_extent_rule` "agree across derivation replicas" | Yes; note replicas are *deterministically identical* (page 1 byte-identical at all 71 checkpoints), so agreement is guaranteed and carries no evidential weight | would pass | genuine-rule, but the replica design yields no independent check of layout assumptions |
| J2 | every used/free offset inside the record (584-595) | same | Yes | would pass | genuine-rule |
| J3 | `joint_shape` equality (598-608, `a1_analysis.py:192`) | `REPLICA_DISAGREEMENT` | Yes | would pass (identical replicas) | genuine-rule |
| J4 | `survivors = (Σ|used|·|free| − |used∩free|) · |bases|`; 0 ⇒ `NO_SURVIVING_MODEL`, >1 ⇒ `MULTIPLE` (619-635, `a1_analysis.py:197-200`) | `decision_rules` | With P4 (`free=∅`) always 0; with B3 always a multiple of 6 | 0 | consequence of P4/B3 |
| J5 | `sole_model`: exactly one `(layout, used, free)` and one base (647-648) | same | see J4 | — | genuine-rule |
| J6 | `predicts_holdout`: equality/membership only, opened after freeze (`a1_analysis.py:201-213`) | `holdout_rule` | Yes | not reached | genuine-rule (correct) |
| J7 | holdout `Abort` other than terminal reasons ⇒ `HOLDOUT_PREDICTION_FAILURE` (206-211) | same | — | — | genuine-rule |

## 4. Abort cascade on run-12 data under the frozen analyzer

What fires, and what would fire next if only the preceding item were repaired (each step evaluated on the real page-1 bytes above):

1. `surviving_record_pages` = ∅ (S2 schedule-mismatch) → **`ambiguous_record_boundary`** ← *retained run-12 result*.
2. Fix S2 (relaxed D predicate) → survivors {0, 1} → D2 `len != 1` → `ambiguous_record_boundary` (S7).
3. Force page 1 → `_record_interval` = `[2, 2048)` (R2 layout-assumption).
4. `_map_type_offset` → (1915, ordinal 40) — passes.
5. `_pointer_candidates` → `free = ∅` (P4) — does not abort here, but dooms J4.
6. `_type1_slots` → `low_phase = []` → `no_surviving_joint_model` (A2).
7. Fix A2 → A4/A5 pass (slots 0/1 → 15136/16352).
8. `_inline_extent` → `explained = ∅` → `ambiguous_inline_boundary` (I2); with a different anchor it would be `unexplained_nonzero_inline_suffix` (I3).
9. Fix I2/I3 → `_base_candidates` keeps all 6 (B3).
10. `candidate_counts` → `survivors = 0` (P4) → `no_surviving_joint_model`; if a free pointer existed, `survivors ≥ 6` → `multiple_surviving_joint_models`.

So under the frozen plan + analyzer there is **no assignment of Jet behaviour** consistent with the run-12 checkpoint arithmetic that reaches the decisive branch: S2 and A2 are arithmetic consequences of the schedule; P4 is a consequence of the plan's own delete rule; I2/B3 are consequences of the observed bit polarity. The holdout/freeze machinery (J6) is sound but never exercised.

## 5. Checklist for the A2 "analyzer dry-run contract"

Mechanical checks the new experiment must include **before** acquisition, each implemented as a test that fails closed. Two fixtures are required: (a) a **schedule-derived synthetic generator** that takes the preregistered `checkpoint_design` + `row_algorithm` and produces page counts by the *same arithmetic as the worker* (relative baselines re-measured after each create, absolute targets, 32-row batches, overshoot), never hand-typed counts; and (b) a **dry run on run-12 replicas 1–2** (page-1 and referenced blobs only; holdout excluded) that must terminate in a preregistered state the plan explicitly predicts.

Schedule arithmetic (must hold for the synthetic generator and be asserted against `observations/replica-0N.json` of run 12):
- [ ] `D_REGROW` page count is derived as `count(D_DROP-after-create) + 128`, i.e. strictly greater than `D_GROW`; **no** predicate may require page-level equality between `D_GROW` and `D_REGROW`. Whatever D predicate replaces ABA must be stated as a record-level set relation (e.g. "bits for the D_GROW pages are cleared at D_DROP and cleared again at D_REGROW, plus additional bits") and the generator must make it *true*.
- [ ] `D_DROP` does not shrink the file; predicates must not assume freed pages are truncated.
- [ ] `L_DELETE_ALTERNATING` may change the file size (run 12: +1 page) and may or may not free a whole page; any churn-only predicate must be demonstrated satisfiable by the generator using only what the delete rule guarantees. If a free-*page* pointer is still a target, the plan must add a checkpoint that provably empties a page (or drop the free-pointer hypothesis).
- [ ] Conversion ordinal is a free parameter of the generator spanning **every** ordinal 1…70 (plus "never"); every downstream predicate (`low_phase`, slot activity, anchor selection) must pass for all of them or the plan must state the exact precondition and the worker must enforce it. Run-12 value: ordinal 40.
- [ ] Slot-activation schedule is a free parameter (0, 1 or 2 slots active at conversion); run 12: both active at conversion, H ladder touches only slot 1.
- [ ] Candidate page space is "every page observed at any checkpoint", not "pages present at E0R" (S1).

Record delimitation (replaces R2):
- [ ] The plan names a finite candidate-record source that is independent of the change envelope (e.g. a preregistered enumeration of `(start, length)` candidates on page 1, or a header-derived directory the plan commits to reading), and the analyzer proves uniqueness among those candidates; `min/max` of all page changes is forbidden.
- [ ] Header bytes (2-13, 20, 376…1784 in run 12) must be excluded from the record or the plan must explain why they belong to it; the dry run must show they are not `used`-pointer candidates.
- [ ] The `len(intervals) != 1` style dead checks are removed or made reachable (contract test: every `Abort` site is reachable by some generator parameterisation).

Polarity / extent (replaces I2-I4, B3):
- [ ] Bit polarity is preregistered as a two-valued parameter and the dry run on run-12 must select **one** value from the D and H transitions (run 12: set bit ⇔ page not in use; growth clears bits). Predicates must be written in terms of "bits that flip on growth", not "bits that become set".
- [ ] The inline boundary must not depend on the anchor's fill level; require the generator to run with anchor fill ∈ {empty, partial, full} and assert the same boundary each time.
- [ ] `_base_candidates` must discriminate formulas using the direction of flips actually produced by growth; the contract must show at least one schedule transition that separates `slot_relative_*` from `referenced_page_relative_*` for **slot 0** (run 12 offers only `P_ABS_16480→H_REL_0064`, 3 bits) — otherwise shrink the candidate set or add a checkpoint.
- [ ] Pointer validity must not be required at every checkpoint from `L_REL_0064`; if a validity filter is kept, its checkpoint set must be preregistered and shown not to prune below the plan's own hypothesis.

Reason reporting:
- [ ] Distinct identifiers (or a preregistered sub-reason field) for "no page passes the transition predicates", "more than one page passes", "record not unique", "inline boundary not unique". The run-12 result must re-classify mechanically under the new mapping.
- [ ] Each `Abort` site carries the predicate id (e.g. `S2`, `A2`) in the report so the next dry run can be diffed against this audit.

Dry-run acceptance criteria (all must hold before any A2 dispatch):
- [ ] Synthetic generator, with *schedule-derived* counts and the run-12-observed parameters (conversion 40, both slots active, set-bit = unused, +1 page on delete), reaches the decisive branch end-to-end; with each single parameter perturbed it reaches exactly the preregistered no-outcome reason.
- [ ] Dry run on run-12 replicas 1–2 terminates in a state the new plan predicts *by name* (either decisive-candidate-set or a specific no-outcome reason tied to a named predicate), reading ≤ a preregistered number of blobs, and the report is retained under the new experiment id as a pre-acquisition calibration artifact, not as evidence.
- [ ] A test asserts that every equality in the analyzer between two checkpoints' bytes is between checkpoints the generator can make equal; any equality the generator cannot produce fails the contract (this single test would have caught S2, A2 and I2).
- [ ] The fixture constants `140/140/140` and `CONVERSION_CHECKPOINT = "L_REL_0512"` in `tests/a1_test_bundle.py` are deleted in favour of generator-derived values.
