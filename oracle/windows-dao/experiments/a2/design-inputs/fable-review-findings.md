# Independent review: PRs #26–#35 (merged to main 2026-08-21)

Reviewer: Claude Fable 5 (read-only; no files edited, no PRs opened).
Base inspected: worktree at `9470382` (PR #34 merge) plus `origin/main`
`04def10` (PR #35) via `git show`. Line numbers refer to those revisions.
Prior context used: `/private/tmp/a1-run12-ambiguity-diagnosis.md` (the
D-ABA whole-page-equality bug is **not** re-reported here; findings below are
the things that diagnosis and the PR reviews did not cover).

Severity scale: **High** = can lose or misrepresent preregistered evidence, or
makes the preregistered decisive outcome unreachable; **Medium** = reporting or
gating inaccuracy that a reader/CI would rely on; **Low** = robustness/hygiene.

---

## 1. A1 worker + page-capture optimizations (#29, #30, #33)

### 1.1 [Low] Closed-file probe: the bounded GC retry only covers the page-count probe, not the snapshot or the companion check
- `oracle/windows-dao/scripts/a1/A1.Worker.ps1:359-371` — `Get-A1ClosedPageCount` retries once after `[GC]::Collect()` on a sharing violation (HRESULT low word 32/33).
- `oracle/windows-dao/scripts/a1/A1.PageStore.ps1:149-151` — `Capture` opens with `FileShare.None` and has no retry; `A1.Worker.ps1:381-393` — `Assert-A1Quiescent` throws immediately if the `.ldb` lingers.
- Before #30 every `Invoke-A1WithDatabase` ran an unconditional `[GC]::Collect(); WaitForPendingFinalizers()` (removed at PR #30 diff lines 416-417), so a late RCW could never reach these call sites. Now a lingering RCW at checkpoint time aborts the whole replica (fail-closed, not wrong data). Run 12 completed, so this is latent, but it is the one place where #30 made the worker *less* tolerant than the frozen design it claims to preserve unchanged.
- Observable-equivalence verdict for the rest of (1): ordered page hashes, `database_sha256`, content-addressed blobs, `changed_page_indices` (including the `null`-sha truncation entries, `A1.PageStore.ps1:204-208` vs pre-#33 lines 419-430), reread order (`ORDER BY Id`, `dbOpenSnapshot`, `A1.PageStore.ps1:487-490`), and the rolling-hash encoding (`A1.PageStore.ps1:271-287`) are byte-for-byte the same computation as before. Digest reuse is gated on exact byte equality (`PageEquals`, `A1.PageStore.ps1:185-191`), so it cannot diverge from a naive rehash short of a SHA-256 collision.

### 1.2 [Low] #30 changed the number of DAO open/close cycles per checkpoint; nothing in the tests proves this is not bytes-observable
- Pre-#30 `Read-A1SemanticTables` opened/closed the database once **per extant table** and once even with zero tables (PR #30 diff lines 609-677). Now it is exactly one open/close per checkpoint (`A1.PageStore.ps1:549-557`).
- The plan's observable is "first closed-file state after a fixed 32-row batch", and the run-12 idle pairs were byte-identical (so an open+scan+close leaves the file unchanged), which makes this empirically safe. But the PR's "observable equivalence" list and the contract tests compare snapshot *computation* against a naive reference, not DAO session count. Worth one sentence in the next provenance entry rather than code.

### 1.3 [Low] #33 startup inventory no longer rehashes prior-replica blobs
- `A1.PageStore.ps1:72-125` — `Inventory` checks canonical name/length only; pre-#33 called `Assert-A1ExistingPageBlob` per blob (PR #33 diff line 290). A same-length corrupt blob left by a killed earlier replica is now caught only by the final `validate-bundle` pass, after two more ~25-minute replicas. Acknowledged in the PR body; recording it here because it shifts a fail-fast to a fail-late.

### 1.4 [Low] PS5 hygiene in the new scripts
- `A1.Progress.ps1:142` assigns the automatic variable `$input`; `A1.Controller.ps1:206` assigns `$matches` (shadows `$Matches`). Both work in PS 5.1 today; PSScriptAnalyzer flags both.
- `A1.Progress.ps1:207-243` `Write-A1ChildFailureDiagnostic` uses `CreateNew` on a label-derived filename. The same label ("A1 complete bundle validation") runs twice during publication (`A1.Controller.ps1:723-740`); a second failure with that label throws "A1 child diagnostic retention also failed", wrapping the real error.

---

## 2. Analyzer (#27 `a1_analysis.py` / `a1_model.py`)

Freeze-before-holdout is correct: `a1_analysis.py:180-204` opens replica 3 only after `sole_model()` returns, and `predicts_holdout` (`a1_model.py:666-679`) is pure membership/equality. Reason mapping to schema identifiers matches R2 exactly (`a1_model.py:44-56`). The problems are predicates that the worker's actual schedule cannot satisfy, in the same class as the documented D-ABA bug.

### 2.1 [High] `_type1_slots` requires the inline→indirect conversion to happen on or before `L_IDLE_REOPEN`; the schedule and the retained data put it in the P ladder
- `a1_model.py:429-435`: `low_phase = [indirect checkpoints with ordinal <= L_IDLE_REOPEN]`; empty → `Abort(NO_SURVIVING_MODEL)`; then exactly one active slot is required at `low_phase[-1]`.
- The schedule (`A1.Worker.ps1:541-566`) grows L to baseline+1280 (~1.5k pages, run 12: ~1,560) and only the P ladder crosses 4,096…16,480 pages. A 2,048-byte page-1 record can hold an inline bitmap covering thousands of pages; if conversion happens anywhere in ordinals 37–40 (`P_ABS_*`) — which is where the run-12 diagnosis found the only monotone `0x00→0x01` column (ordinal 40, `P_ABS_16480`) — `low_phase` is empty and derivation aborts unconditionally, *even after the D-ABA predicate is fixed*.
- The synthetic fixture hides this exactly as it hid D-ABA: `oracle/windows-dao/tests/archive/a1_test_bundle.py:47-49` sets `CONVERSION_CHECKPOINT = "L_REL_0512"` and `INLINE_CAPACITY_PAGES = 320`, i.e. conversion is forced into the L phase.
- Consequence: under the frozen analyzer the decisive path is unreachable whenever Jet's inline capacity exceeds the L-phase page count. Must be part of the follow-up experiment ID (diagnosis §"Required follow-up" item 5 — "prove every analyzer equality is arithmetically possible under the checkpoint generator").

### 2.2 [High] The free-pointer predicate is unsatisfiable under the plan's own delete rule
- `a1_model.py:80-83, 391-396`: a `free` candidate must change across `L_REL_1280→L_DELETE_ALTERNATING` or `→L_REINSERT_SAME` **and** never change across any growth transition.
- Plan `tables.row_algorithm.delete_rule` (`a1-allocation-maps.plan.json:63`): "delete every even Id … preserving every odd Id so no data page is intentionally emptied." A page-level free pointer therefore has no reason to change on churn, and any pointer that does change on churn (e.g. free-space or row-count bookkeeping) almost certainly also changes on growth.
- Run-12 evidence (diagnosis, "Counterfactual record and pointer derivation"): forced whole-page evaluation found **zero** churn-only windows under both layouts; `L_DELETE_ALTERNATING` changed one byte of page 1 and `L_REINSERT_SAME` changed none.
- With `free == ∅`, `candidate_counts` gives `combinations == 0` → `survivors == 0` → `NO_SURVIVING_MODEL` (`a1_analysis.py:197-198`). The analyzer is faithful to the plan here, so this is a preregistration-design defect encoded in code; it still means the decisive outcome is structurally unreachable and belongs in the follow-up plan.

### 2.3 [Medium] `_inline_extent` derives the inline boundary from how full the bitmap happens to be at the anchor checkpoint
- `a1_model.py:333-340`: candidate boundaries are the positions whose *preceding byte is nonzero at the last inline checkpoint*; `a1_model.py:343-354` then keeps the ones with an all-zero suffix. With a "1 = allocated" bitmap and contiguous allocation this selects `last_nonzero_byte + 1` at the anchor, not the record's real end. Because conversion is triggered by the *first checkpoint past capacity* and the P ladder steps by 4,096 pages, the anchor will normally have slack, so the selected boundary is a function of the anchor's page count.
- All three replicas follow the same deterministic schedule, so replicas 1, 2 and the holdout will *agree* on this artefact (`require_unique_boundaries`, `predicts_holdout`), producing a wrong-but-consistent `inline_boundary` in a decisive model. The fixture again aligns things: anchor `L_REL_0064` has 208 pages (`a1_test_bundle.py:55-56`), a multiple of 8.
- Not a no-outcome path; it is a validity risk for a future decisive report and should be addressed by preregistering an explicit boundary source.

### 2.4 [Medium] "No page survives" and "surviving page ≠ 1" are reported as `ambiguous_record_boundary`
- `a1_model.py:534-539`. R2 binds `ambiguous_record_boundary` to the plan prose "ambiguous record or inline boundary" (`a1-allocation-maps-r2.plan.json:72-78`), but zero surviving pages is semantically "zero surviving joint model". EXP-0039 now records the run-12 result under the ambiguity label, and a reader would infer multiple candidate records when in fact none passed the transition predicates (diagnosis item 6). Needs a preregistered disambiguation in the follow-up plan; at minimum the next ledger entry should say which branch fired.

### 2.5 [Low] Holdout-side `ValidationError`s abort without a report
- `a1_analysis.py:203-204`: a binding fault in replica 3 (`_require_bindings`) raises `ValidationError`, which `main()` turns into exit 1 with no report written, so the controller treats it as an analysis failure and the staging is removed (see 3.1). That is fail-closed, but it means a replica-3 binding mismatch discards the two good derivation replicas. Consider whether that is the intended R2 behaviour.

---

## 3. Hosted workflow, controller, bounded process (#29, #31, #32, #34)

### 3.1 [High] A decisive analysis report would be destroyed, contradicting the preregistered R2 retention rule
- `oracle/windows-dao/scripts/archive/a1_contract.py:430-433`: `validate-document` raises for `scientific_outcome == one_joint_model_predicts_holdout`.
- `A1.Controller.ps1:707-711` runs exactly that validator on the fresh report; `Invoke-A1Python` (`A1.Controller.ps1:110-192`) throws on non-zero exit; the campaign `catch` then calls `Remove-A1PrivateStaging` (`A1.Controller.ps1:761`), deleting the staging bundle — analysis report, three observations, 213 page indexes and the page store.
- What #32 retains on that path is `post-worker-a1-analysis-report-validation.failure.json` with a 32 KiB stderr tail and `campaign-error.json` (`A1.Progress.ps1:207-266`) — not the report.
- R2 (`a1-allocation-maps-r2.plan.json:98-103`, EXP-0038 Protocol) preregisters: `analysis_report_artifact: "retained"`, bundle status `decisive_pending_independent_validation`, and "that rejection must not discard the retained analysis report". Nothing in #29–#35 implements this; `bundle-manifest.schema.json` still pins `execution_status` to the constant `"pass"` and has no status field that could carry `decisive_pending_independent_validation`.
- Net effect: the only outcome the experiment exists to produce is the one outcome the lane cannot retain. Since acquisition has started (EXP-0039), fixing this now requires a new experiment ID/plan/entry per the amendment rule — it should be bundled with the analyzer follow-up rather than patched ad hoc.

### 3.2 [Medium] The always-uploaded diagnostics artifact can carry "independently_validated" markers after the validation step has failed
- `.github/workflows/windows-dao-a1.yml:579-581`: `validation-receipt.json` (`"status": "independently_validated"`) is written into `jet3-a1-diagnostics` **before** the step's remaining gates — the git-clean check (598-601) and the campaign-status check (602-607). If either throws, the attestation artifact is not uploaded but the diagnostics artifact (`if: always()`, 651-659) ships a receipt saying validated.
- Likewise `campaign.json` in diagnostics is rewritten to `independently_validated` (625-629) before the attestation upload step; an upload failure leaves diagnostics claiming validated with no attestation.
- The retained bundle itself (uploaded whenever the campaign step succeeded, 631-639) contains the producer-written manifest with `hashes_verified = true`, `inventory_closed = true`, `execution_status = "pass"` (`A1.Controller.ps1:531-535`). Those are the controller's *own* assertions, and they are present even when the independent step fails. The neutral artifact name (#34) helps, but a consumer opening the bundle sees "pass/verified" with no signal that the independent step never passed. Suggest writing the receipt last and writing an explicit `validation-failed.json` marker on the failure path.

### 3.3 [Medium] "Independent" validation is the same code at the same commit in the same job
- The workflow step (`windows-dao-a1.yml:544-597`) imports `a1_bundle.validate_bundle` from the checked-out producer commit, which is the same function the controller already ran twice via `a1_contract.py validate-bundle` (`A1.Controller.ps1:723-740`). It is independent in process, not in implementation or provenance. This matters for §5 wording and for the plan's still-BLOCKED `independent_complete_bundle_validator` gate.

### 3.4 [Low] PS5 pitfalls checked and found handled
- `Start-Process … -PassThru` exit-code loss: fixed by caching `$process.Handle` (`windows-dao-a1.yml:441`) and the explicit `$null -eq $exitCode` check (459-462).
- `Get-Content -Raw` on an empty stderr returning `$null`: wrapped in `[Convert]::ToString` (516-518).
- `ConvertFrom-Json` array unrolling for `PROVEN_IMAGES`: `@(... | ForEach-Object { $_ })` (151) and per-entry property checks (153-165).
- `python -c` quote stripping: replaced by a script file (#34, 583-594). The YAML block scalar de-indents the here-string body so `'@` lands in column 0; confirmed by run 12 reaching `independently_validated`.
- Timeouts (#31): `Invoke-BoundedChildProcess` hard-caps `ReviewedTimeoutCeilingSeconds` at 1,800 (`BoundedProcess.ps1:40-60`), the A1 callers declare 600/900, and `Get-A1CampaignAllowance` (`A1.Controller.ps1:14-25`) clips every launch to the 7,200-second remainder. M1–M5 callers keep the 120-second default.

---

## 4. `ci.yml` path filters (#26)

### 4.1 [Medium] `benchmarks` is gated on a filter that excludes its own inputs
- `.github/workflows/ci.yml:212-239`: the job builds and runs `benches/Cargo.toml`, `benches/tests/**`, `benches/tests/test_capture_metadata.sh` — all under `benches/`.
- The `rust` filter (`ci.yml:34-41`) lists `crates/**`, `fuzz/**`, `Cargo.toml`, `Cargo.lock`, `tests/manifest.json`, `rust-toolchain*`, `.github/workflows/**` — **not** `benches/**`. A PR touching only benchmark sources, baselines, or the capture script skips the benchmark job on the PR and first runs on `main` after merge. ACCEPTANCE.md (line 170) lists benchmark compilation among the evidence jobs.

### 4.2 [Low] `windows-oracle-contracts` can be skipped on a PR that changes a file its tests read
- `oracle` filter (`ci.yml:42-46`): `oracle/**`, `tools/**`, `docs/validation/**`, `.github/workflows/**`.
- `oracle/windows-dao/tests/test_m4_plan_contract.py:561` reads `docs/PROVENANCE.md`; the A1 plan-contract tests pin ledger text too. A provenance-only PR (the shape of #28 and #35 minus their `oracle/` touches) would skip the Windows contract job. Adding `docs/PROVENANCE.md` to the `oracle` filter closes it.
- Other filters checked: fuzz/miri inputs are covered; `deny.toml`/`rustfmt.toml` feed ungated jobs. Skipped jobs satisfy required status checks on GitHub, so these gaps are silent rather than blocking.

---

## 5. Provenance entries (#28 EXP-0038, #35 EXP-0039)

### 5.1 [Medium] EXP-0039 over-states independence of validation
- `docs/PROVENANCE.md` (origin/main) `:2888-2890` "Kind: controlled hosted DAO acquisition with an **independently validated** complete bundle"; `:2903-2904` "campaign status `independently_validated`"; `DAO_PROVIDER_BLOCKER.md` "retaining and independently validating the first complete A1 bundle".
- Per §3.3 the validator is the producer commit's own `a1_bundle.validate_bundle`; the local re-run described at `:2931-2948` uses "the checked producer-commit validators" — the same code. The immutable plan still lists `independent_complete_bundle_validator` under `execution_gate.blocking_requirements`, and EXP-0038 itself warns that invoking `a1_contract.py` does not make a result independently validated. The entry should say "validated by the producer-commit bundle validator in a separate process and a separate local copy" and leave "independent" to the gate vocabulary.

### 5.2 [Low] EXP-0039 does not record that the no-outcome reason is an analyzer/acquisition-contract artefact
- The Observation/Interpretation present `ambiguous_record_boundary` as the retained scientific result without noting that (a) the firing branch was "zero surviving pages" (§2.4), and (b) the D-ABA predicate cannot be satisfied by the relative-regrowth schedule (diagnosis). The entry is not wrong — it claims no physical meaning — but the ledger now needs an additive entry citing the diagnosis so the next preregistration is traceable, and so nobody reads "ambiguous" as a data property.

### 5.3 [Low] EXP-0038 preregisters retention behaviour that the code does not have
- EXP-0038 Protocol (`PROVENANCE.md:2840-2848`) and the R2 plan describe retaining a decisive report with status `decisive_pending_independent_validation`. As of #35 nothing implements it (§3.1). The entry reads as a description of existing behaviour; it should be read as a requirement that is still open.

---

## Summary table

| # | Sev | Area | One line |
|---|-----|------|----------|
| 3.1 | High | Controller/R2 | Decisive report is rejected by `validate-document` and the staging is deleted; R2 retention never implemented |
| 2.1 | High | Analyzer | `_type1_slots` needs conversion ≤ `L_IDLE_REOPEN`; schedule/data put it in the P ladder |
| 2.2 | High | Analyzer/plan | Churn-only free pointer cannot exist under the plan's "no page emptied" delete rule → survivors always 0 |
| 2.3 | Med | Analyzer | Inline boundary = anchor fill level, replica-consistent but page-count dependent |
| 2.4 | Med | Analyzer | Zero surviving pages reported as `ambiguous_record_boundary` |
| 3.2 | Med | Workflow | Diagnostics can carry `independently_validated` receipt/campaign.json after a failed validation step; bundle manifest self-asserts pass |
| 3.3 | Med | Workflow | "Independent" validator is the producer's own code |
| 4.1 | Med | CI | `benchmarks` skipped on `benches/**`-only PRs |
| 5.1 | Med | Provenance | EXP-0039 "independently validated" wording vs BLOCKED gate |
| 1.1–1.4, 2.5, 3.4, 4.2, 5.2, 5.3 | Low | various | robustness, hygiene, disclosure |

No file in the repository was modified by this review.
