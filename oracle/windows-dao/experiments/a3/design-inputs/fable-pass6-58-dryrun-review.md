# Independent review — Pass 6, PR #58 (A3 dry-run harness)

Verbatim copy of the `#58` section and Pass-6 verdict line of the reviewer's working file `/private/tmp/fable-59-60-review.md` (2026-08-23), committed so the EXP-0048 pointer is durable. The `#56` section is omitted: that lane is being fixed separately.

# Pass 6 — 2026-08-23: pre-execution review of #58 `origin/fable/a3-dryrun` (37b7c9c) and #56 `origin/fable/a3-rebind` (06a5173)

## #58 — A3 dry-run harness + committed `dry-run/` artefacts — MERGE (two non-blocking fixes recommended)

Rebased on current `origin/main` (d0dec6b, includes #59–#63). Diff: harness modules (`a3_dryrun*.py`, rebuilt `a3_generator*.py`), 8 artefacts under `experiments/a3/dry-run/`, tests. No plan, schema, ledger, analyzer, or validator source touched (a 2-line test rename `_payloads`→`payloads`).

**Regenerability — VERIFIED.** Re-ran `a3_dryrun.py --commit 24c3b1a6… --workspace … --output …` against the retained EXP-0042 bundle (manifest SHA-256 `9e1dac53…` present locally). Result: `sweep: 100 fixtures, 0 failing; reachability 31/31; pair agreed 100/100`; replay `input_page_blob_count = 81`, `result = pass`. Of the 7 checksummed artefacts, 5 are byte-identical to the committed ones (`a3-pair-agreement`, `a3-reachability-transcript`, `a3-sweep-checks`, `a3-synthetic-cases`, `exp-0042-replay-transcript`); `a3-synthetic-report.json` and `exp-0042-replay-report.json` differ only in `recorded_utc` (content equal after stripping it), which is why their two `checksums.json` entries differ on re-run. 100 fixtures materialised on disk; the validator ran as a separate process per fixture.

**Reachability from execution — PASS.** `CaseResult.executed_terminals` (`a3_dryrun.py:117–124`) is built from the analyzer's produced report: `terminal_predicate_ids`, rows with `status == "fail"`, and per-layer `terminal_predicate_id`. `reachability_transcript` (`:259–287`) marks an id `reached` only if its designated fixture actually executed it, and the 3 R3 unreachable ids are `asserted_nonterminal` only when no fixture reached them (`UNEXPECTEDLY_REACHED` otherwise). `assess_case` (`:191`) fails a case whose designated predicate was not executed. Nothing is derived from the registry or labels.

**`tamper_suite_not_applicable` reading — plan-grounded.** R3 `dry_run_honesty_clause.pair_acceptance_gate` requires identical per-layer terminals/models/cross-check transcripts/34 statuses for every fixture but `accepted=true` only "on the all-layers-decisive fixture". Plan `tamper_cases` T1–T4 each mutate the decisive global-record model, so on a fixture with no such model the suite has no subject; the validator raises `tamper_suite_not_executable` only after `validate_bundle` (recompute, frozen-set, report layers, `validate_predicates` over all 34 statuses, bounds) has passed (`a3_independent_validator.py:795–796`). The harness accepts that code only when the analyzer's frozen set has no global model and it is the validator's sole code (`a3_dryrun_pair.py:190–196`). The explicit `compare_pair` checks cover terminals, models, survivor counts, cross-check transcript, and campaign terminal; the 34-status identity is enforced indirectly by the validator's own `validate_predicates` on the verdict path rather than compared field-by-field in the harness — acceptable, but see fix 2.

**Independence — PASS.** Harness imports `a3_analysis`/`a3_model`/`a3_layers`/`a3_spec` (analyzer side) and invokes the validator only as a subprocess CLI on identical on-disk bytes; no `a3_independent_*` import anywhere in `a3_dryrun*.py` or the generator. `a3_generator*.py` import only `a3_spec` (plan loader) and `protocol_validation`, never analyzer logic; `a3_dryrun_cases.py` builds expected outcomes from parameters and the plan, and the generator does not read expectations (cases import the generator, not the reverse). `RetainedDerivationReplica` (`a3_dryrun_replay.py:85–110`) refuses any replica other than 1/2 and hash-checks every observation and page index against the manifest, so the 81-blob replay cannot open replica 3.

**Sweep checks — computed, not constants** (`sweep_checks`, `a3_dryrun.py:233–256`): exact-ceiling/one-over (`record_candidates_examined == 67,141,632` at 16+16 and `A3-RESOURCE-BOUND` at 17), anchor-fill boundary invariance, calibration prefix, all-layers-decisive, partial-outcome retention, axis completeness, pair agreement. All derived from produced reports.

**Replica-3 independence (R3 `replica_3_independent_overshoot`) — data PASS, check weaker than clause.** Recomputed from the generator on baseline, sixteen_qualified_pages, conversion_never, and opposite-polarity fixtures: replica 3's overshoot differs from *both* derivation replicas at ≥1 checkpoint in each of D, L, P, H (e.g. `L_REL_0904` 3 vs 0/0, `H_REL_0904` 3 vs 0/3 … and `P_ABS_04096` 3 vs 1/1). However the committed `replica_overshoot` samples only four checkpoints and `replica_3_overshoot_independent` compares whole dicts, so the check would still pass if replica 3 differed in only one phase; in the committed sample replica 3 equals replica 2 at `L_REL_1280` and `H_REL_0904` on ~every case. The clause holds on the real fixtures; the check does not prove it.

Tests: `test_a3_*` on the branch — 62 OK.

Non-blocking fixes (can follow in the disclosure PR):
1. **Commit stamping.** The artefacts carry `analyzer_commit = 24c3b1a6…` but were generated by 37b7c9c's code, which also changed `a3_dryrun.py` (`bit_polarities` ordering in `_coverage`). Checking out 24c3b1a6 and re-running would not reproduce `a3-synthetic-report.json`. Split code changes from regeneration commits so the stamped commit contains the generating code, or stamp the code-only commit explicitly.
2. **Tighten `replica_3_overshoot_independent`** to the clause: record the full per-checkpoint overshoot and require, per phase D/L/P/H, a checkpoint where replica 3 ≠ replica 1 and ≠ replica 2. Optionally add an explicit 34-status comparison to `compare_pair` using the validator's verdict document so the gate's "identical 34 predicate statuses" is asserted directly rather than via the validator's own pass.


## Pass-6 verdict (#58)

- **#58: MERGE** (follow-ups: commit stamping; tighten `replica_3_overshoot_independent` to per-phase).
