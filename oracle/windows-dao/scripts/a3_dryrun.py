#!/usr/bin/env python3
"""A3 dry-run orchestrator: EXP-0042 replay, synthetic sweep, executed reachability, pair gate.

A3 rule | implementation
--- | ---
Replay opens replicas 1/2 only | :mod:`a3_dryrun_replay`
Every free parameter generated and analyzed on disk | :func:`run_sweep`
Expected outcome per case asserted against the produced report | :func:`assess_case`
Reachability derived from executed terminals, never the registry | :func:`reachability_transcript`
Analyzer and independent validator agree on identical bytes | :mod:`a3_dryrun_pair`
Schema-valid reports with checksums | :func:`write_reports`
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from protocol_validation import ValidationError, canonical_json_bytes
from a3_dryrun_cases import (
    CONVERSION, DECISIVE, GLOBAL, HOLDOUT_FAILURE, NOT_APPLICABLE, Case, all_cases, outcome_labels, reachability_targets,
)
from a3_dryrun_pair import FixtureRun, run_fixture
from a3_dryrun_replay import run_replay
from a3_generator import GLOBAL_PAGE, SyntheticReplica
from a3_spec import (
    BOUNDS, CHECKPOINT_IDS, EXPERIMENT_ID, LAYER_KEYS, PLAN_SHA256, PREDICATE_IDS, UNREACHABLE_PREDICATE_IDS,
    validate_dry_run_report,
)

SCRIPTS = Path(__file__).resolve().parent
DEFAULT_RETAINED_ROOT = Path(os.environ.get(
    "A3_EXP0042_BUNDLE",
    "/private/tmp/claude-501/-Users-oglass-Development-Misc-access97-rs/"
    "77df2993-62f0-4041-97d5-19885072a109/scratchpad/a2run4/"
    "windows-dao-a2-bundle-1a0585446ac8b0d232ee4c0391cce9d635e7c43a-32587946283-1/jet3-a2-bundle",
))
DEFAULT_OUTPUT = SCRIPTS.parent / "experiments" / "a3" / "dry-run"
GENERATOR_FILES = ("a3_generator.py", "a3_generator_schedule.py", "a3_dryrun_bundle.py", "a3_dryrun_cases.py")
CALIBRATION_PREFIX_HEX = "01003a0000e03f0000"
OVERSHOOT_PHASES = {"D": "D_", "L": "L_REL_", "P": "P_ABS_", "H": "H_REL_"}
REPLAY_ASSERTIONS = {
    "record_1915_2048_not_in_use_slack_92": ("global_record_start_unique", "global_record_end_unique_with_polarity_relative_uniform_slack"),
    "legacy_relation_leaves_1935_starts": ("legacy_relative_d_is_abac",),
    "tag_base_highwaters_29_157_285": ("tag_base_bitmap_layout_decoded", "three_highwater_anchors_enforced"),
    "cross_check_stops_leg_3_page_1021": ("first_violating_leg_and_page_reported", "polarity_cross_check_stops_before_representation_change"),
    "tdef_no_tdef_record_candidate": ("tdef_no_outcome_ordering_exercised",),
    "frozen_set_parsed_and_compared": ("frozen_candidate_set_parsed_and_compared",),
    "every_predicate_id_exactly_once": ("every_predicate_id_exactly_once",),
}
SWEEP_ASSERTIONS = {
    "replica_3_overshoot_independent": ("schedule_and_worker_arithmetic_generated_from_plan",),
    "anchor_fill_boundary_invariant": ("inline_boundary_anchor_fill_independent", "inline_boundaries_from_fixed_enumeration"),
    "exp_0042_prefix_from_generated_bytes": ("exp_0042_calibration_case_non_evidential",),
    "bounds_exact_ceiling_and_one_over": ("bounds_accept_exact_and_reject_one_over",),
    "all_required_terminal_cases_exercised": ("all_required_terminal_cases_exercised",),
    "every_reachable_predicate_reached": ("every_required_reachable_abort_reached_by_single_perturbation",),
    "unreachable_predicates_nonterminal": ("every_abort_has_pinned_reason_mapping",),
    "pair_agreement": ("decisive_layered_report_accepted_by_contract_validator",),
    "all_layers_decisive_recovered": ("all_layers_decisive_model_recovered",),
    "partial_layer_outcome_retained": ("partial_layer_outcome_retained", "base_nondiscrimination_is_layer_local"),
    "parameter_axes_complete": ("conversion_ordinal_parameter_complete", "slot_activation_parameter_complete", "bit_polarity_parameter_complete"),
}


def _sha256_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _overshoot(replica: SyntheticReplica) -> dict[str, int]:
    """Every target-bearing checkpoint's overshoot (actual pages minus threshold)."""
    return {
        row.checkpoint_id: row.target_overshoot_pages
        for row in replica.schedule.checkpoints if row.target_overshoot_pages is not None
    }


def overshoot_independent(overshoot: dict[int, dict[str, int]]) -> bool:
    """R3 replica_3_independent_overshoot: in each of D, L, P, H some checkpoint has
    replica 3's overshoot differing from replica 1's and from replica 2's."""
    if set(overshoot) != {1, 2, 3}:
        return False
    return all(
        any(
            overshoot[3][name] != overshoot[1][name] and overshoot[3][name] != overshoot[2][name]
            for name in overshoot[3] if name.startswith(prefix)
        )
        for prefix in OVERSHOOT_PHASES.values()
    )


def _produced_layers(report: dict[str, Any] | None) -> dict[str, str]:
    """Map the analyzer's per-layer (status, terminal) onto the case catalog's labels."""
    if report is None:
        return {}
    labels = {}
    for name, row in _layer_rows(report).items():
        if row["status"] == "decisive_predicts_holdout":
            labels[name] = DECISIVE
        elif row["status"] == "not_applicable":
            labels[name] = NOT_APPLICABLE
        elif row["terminal_predicate_id"] == "A3-HOLDOUT-PREDICTION":
            labels[name] = HOLDOUT_FAILURE
        else:
            labels[name] = row["terminal_predicate_id"] or "no_outcome"
    return labels


@dataclass
class CaseResult:
    case: Case
    report: dict[str, Any] | None
    analyzer_error: str | None
    pair: dict[str, Any]
    overshoot: dict[int, dict[str, int | None]]
    calibration_prefix_hex: str | None
    report_sha256: str | None
    failures: list[str] = field(default_factory=list)

    @property
    def terminal_ids(self) -> list[str]:
        return [] if self.report is None else list(self.report["terminal_predicate_ids"])

    @property
    def executed_terminals(self) -> list[str]:
        """Terminal ids plus any predicate the analyzer reported as failed (A3-HOLDOUT-PREDICTION
        is projected out of terminal_predicate_ids when another layer is decisive)."""
        if self.report is None:
            return []
        failed = [row["predicate_id"] for row in self.report["predicate_results"] if row["status"] == "fail"]
        layer_terminals = [row["terminal_predicate_id"] for row in _layer_rows(self.report).values() if row["terminal_predicate_id"]]
        return list(dict.fromkeys([*self.terminal_ids, *failed, *layer_terminals]))

    @property
    def produced(self) -> dict[str, str]:
        return _produced_layers(self.report)

    def document(self) -> dict[str, Any]:
        return {
            **self.case.document(),
            "analyzer_error": self.analyzer_error,
            "produced": None if self.report is None else {
                "layers": self.produced,
                "terminal_predicate_ids": self.terminal_ids,
                "executed_terminals": self.executed_terminals,
                "no_outcome_reasons": self.report["no_outcome_reasons"],
                "polarity_cross_check": self.report["polarity_cross_check"],
                "layer_models": {name: _layer_model(self.report, name) for name in LAYER_KEYS},
                "report_sha256": self.report_sha256,
            },
            "replica_overshoot": {str(k): v for k, v in self.overshoot.items()},
            "pair": self.pair,
            "failures": list(self.failures),
        }


LAYER_PATHS = {
    "global_map_record": ("global_map", "record"), "global_map_conversion_inline": ("global_map", "conversion_inline"),
    "global_map_extended_base": ("global_map", "extended_base"), "tdef_pointer_pair": ("tdef", "pointer_pair"),
}


def _layer_rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: report["submodels"][group][key] for name, (group, key) in LAYER_PATHS.items()}


def _layer_model(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    return _layer_rows(report)[name]["model"]


def assess_case(result: CaseResult) -> None:
    """Assert the plan-derived expectation against what the analyzer actually produced."""
    expectation, report = result.case.expectation, result.report
    if report is None:
        result.failures.append(f"analyzer produced no report: {result.analyzer_error}")
        return
    produced = result.produced
    for name, expected in expectation.layers.items():
        if produced[name] != expected:
            result.failures.append(f"{name}: expected {expected}, produced {produced[name]}")
    if expectation.campaign_terminal is not None and expectation.campaign_terminal not in result.terminal_ids:
        result.failures.append(f"campaign terminal {expectation.campaign_terminal} not in {result.terminal_ids}")
    transcript = report["polarity_cross_check"]
    if expectation.representation_change_stop != "unspecified":
        stop = transcript["representation_change_stop"]
        stop = None if stop is None else (stop["left_checkpoint_id"], stop["right_checkpoint_id"])
        if stop != expectation.representation_change_stop:
            result.failures.append(f"representation stop expected {expectation.representation_change_stop}, produced {stop}")
    if expectation.first_violation != "unspecified":
        violation = transcript["first_violating_leg"]
        produced_violation = None if violation is None else ((violation["left_checkpoint_id"], violation["right_checkpoint_id"]), transcript["first_violating_page"])
        if produced_violation != expectation.first_violation:
            result.failures.append(f"first violation expected {expectation.first_violation}, produced {produced_violation}")
    for name, fields in (expectation.model_fields or {}).items():
        model = _layer_model(report, name) or {}
        for key, value in fields.items():
            if model.get(key) != value:
                result.failures.append(f"{name}.{key}: expected {value!r}, produced {model.get(key)!r}")
    if result.case.reaches is not None and result.case.reaches not in result.executed_terminals:
        result.failures.append(f"designated predicate {result.case.reaches} not executed; terminals {result.executed_terminals}")
    if not result.pair["agreement"]:
        result.failures.extend(f"pair: {item}" for item in result.pair["disagreements"])


def _run_case(index: int, workspace: str, commit: str, created_utc: str, keep: bool) -> dict[str, Any]:
    case = all_cases()[index]
    try:
        run: FixtureRun = run_fixture(
            case.case_id, case.parameters, Path(workspace), commit, created_utc,
            expected_rejection=case.expected_validator_rejection, keep=keep,
        )
    except (ValidationError, OSError) as exc:
        return {"index": index, "report": None, "analyzer_error": f"fixture failed: {type(exc).__name__}: {exc}", "pair": {"agreement": False, "disagreements": [f"fixture failed: {exc}"]}, "overshoot": {}, "calibration_prefix_hex": None, "report_sha256": None}
    prefix = None
    if case.case_id == "exp_0042_calibration":
        replica = run.replicas[0]
        digest = replica.ordered_page_sha256["P_ABS_16480"][GLOBAL_PAGE]
        start = case.parameters.global_record_start
        prefix = replica.payloads[digest][start:start + 9].hex()
    return {
        "index": index, "report": run.pair.analyzer.report, "analyzer_error": run.pair.analyzer.error,
        "pair": run.pair.document(), "overshoot": {replica.replica: _overshoot(replica) for replica in run.replicas},
        "calibration_prefix_hex": prefix, "report_sha256": run.report_sha256,
    }


def run_sweep(workspace: Path, commit: str, created_utc: str, jobs: int, keep: bool) -> list[CaseResult]:
    cases = all_cases()
    results: list[CaseResult] = []
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(_run_case, index, str(workspace), commit, created_utc, keep) for index in range(len(cases))]
        for future in futures:
            raw = future.result()
            result = CaseResult(cases[raw["index"]], raw["report"], raw["analyzer_error"], raw["pair"], raw["overshoot"], raw["calibration_prefix_hex"], raw["report_sha256"])
            assess_case(result)
            results.append(result)
            print(f"[{len(results)}/{len(cases)}] {result.case.case_id}: {'ok' if not result.failures else 'FAIL'}", file=sys.stderr)
    return results


def sweep_checks(results: list[CaseResult]) -> dict[str, bool]:
    by_id = {result.case.case_id: result for result in results}
    checks: dict[str, bool] = {}
    checks["replica_3_overshoot_independent"] = all(overshoot_independent(result.overshoot) for result in results)
    boundaries = {
        name: (_layer_model(by_id[name].report, CONVERSION) or {}).get("inline_boundary")
        for name in ("fill_empty", "fill_partial", "fill_full") if by_id[name].report is not None
    }
    checks["anchor_fill_boundary_invariant"] = len(boundaries) == 3 and len(set(boundaries.values())) == 1 and None not in boundaries.values()
    checks["exp_0042_prefix_from_generated_bytes"] = by_id["exp_0042_calibration"].calibration_prefix_hex == CALIBRATION_PREFIX_HEX
    sixteen, seventeen = by_id["sixteen_qualified_pages"], by_id["seventeen_qualified_pages"]
    checks["bounds_exact_ceiling_and_one_over"] = (
        sixteen.report is not None and all(label == DECISIVE for label in sixteen.produced.values())
        and sixteen.report["qualified_page_counts"] == {"global_map": BOUNDS["max_qualified_pages_per_submodel"], "tdef": BOUNDS["max_qualified_pages_per_submodel"]}
        and sixteen.report["record_candidates_examined"] == BOUNDS["max_record_candidates"]
        and "A3-RESOURCE-BOUND" in seventeen.terminal_ids
    )
    checks["all_layers_decisive_recovered"] = all(label == DECISIVE for label in by_id["baseline"].produced.values())
    checks["partial_layer_outcome_retained"] = by_id["no_slot0_flip"].produced.get(GLOBAL) == DECISIVE and by_id["no_slot0_flip"].produced.get("global_map_extended_base") == "A3-BASE-DISCRIMINATION"
    checks["parameter_axes_complete"] = all(f"conversion_{ordinal}" in by_id for ordinal in range(1, len(CHECKPOINT_IDS))) and "conversion_never" in by_id
    checks["pair_agreement"] = all(result.pair["agreement"] for result in results)
    return checks


def reachability_transcript(results: list[CaseResult]) -> dict[str, Any]:
    """Per predicate id: the fixture designated to reach it and what it actually executed."""
    targets = reachability_targets([result.case for result in results])
    by_id = {result.case.case_id: result for result in results}
    reached_anywhere: dict[str, list[str]] = {predicate: [] for predicate in PREDICATE_IDS}
    for result in results:
        for predicate in result.executed_terminals:
            reached_anywhere[predicate].append(result.case.case_id)
    rows = []
    for predicate in PREDICATE_IDS:
        unreachable = predicate in UNREACHABLE_PREDICATE_IDS
        designated = targets.get(predicate)
        executed = designated is not None and predicate in by_id[designated.case_id].executed_terminals
        rows.append({
            "predicate_id": predicate,
            "classification": "unreachable_by_construction" if unreachable else "reachable",
            "designated_fixture": None if designated is None else designated.case_id,
            "executed_terminal_in_designated_fixture": executed,
            "fixtures_reaching": reached_anywhere[predicate],
            "status": ("asserted_nonterminal" if not reached_anywhere[predicate] else "UNEXPECTEDLY_REACHED") if unreachable
            else ("reached" if executed else "NOT_REACHED"),
        })
    reachable = [row for row in rows if row["classification"] == "reachable"]
    return {
        "derived_from": "executed analyzer terminal_predicate_ids and failed predicate_results per fixture",
        "reachable_total": len(reachable),
        "reached_count": sum(row["status"] == "reached" for row in reachable),
        "unreachable_asserted_nonterminal": all(row["status"] == "asserted_nonterminal" for row in rows if row["classification"] != "reachable"),
        "rows": rows,
    }


def _coverage(results: list[CaseResult]) -> dict[str, Any]:
    cases = [result.case for result in results]
    axis = lambda category: [case.parameters for case in cases if case.category == category]  # noqa: E731
    calibration = next(result for result in results if result.case.case_id == "exp_0042_calibration")
    return {
        "conversion_ordinals": sorted({p.conversion_ordinal for p in axis("axis:conversion_ordinal") if p.conversion_ordinal is not None}),
        "conversion_never": any(p.conversion_ordinal is None for p in axis("axis:conversion_ordinal")),
        "slot_activation_counts": sorted({p.slot_activation_at_conversion for p in axis("axis:slot_activation_at_conversion")}),
        "bit_polarities": [p.bit_polarity for p in axis("axis:bit_polarity")],
        "anchor_fill_states": [p.anchor_fill_state for p in axis("axis:anchor_fill_state")],
        "record_end_uniform_slack_bytes": sorted({p.record_end_uniform_slack_bytes for p in axis("axis:record_end_uniform_slack_bytes")}),
        "exp_0042_calibration": None if calibration.report is None else {
            "source_a2_conversion_ordinal": 20, "conversion_checkpoint_id": CHECKPOINT_IDS[calibration.case.parameters.conversion_ordinal],
            "a3_conversion_ordinal": calibration.case.parameters.conversion_ordinal, "indirect_tag": 1,
            "slot_0_reference_page": 14848, "slot_1_reference_page": 16352,
            "indirect_prefix_hex": calibration.calibration_prefix_hex, "bit_polarity": calibration.case.parameters.bit_polarity,
            "delete_page_delta": 1, "scientific_evidence": False,
        },
    }


def _report_base(commit: str, recorded_utc: str) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0", "document_type": "dao_a3_analyzer_dry_run_report", "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256, "analyzer_commit": commit, "recorded_utc": recorded_utc, "holdout_opened": False,
        "scientific_evidence": False, "acquisition_authorized": False, "capability_advancement_authorized": False,
    }


def build_replay_report(replay: dict[str, Any], commit: str, recorded_utc: str) -> dict[str, Any]:
    assertions = ["holdout_never_opened", "no_a3_scientific_outcome_emitted_for_exp_0042_input", "retained_input_blob_bound_respected"]
    for check, names in REPLAY_ASSERTIONS.items():
        if replay["checks"].get(check):
            assertions.extend(names)
    if replay["checks"].get("t3_rejected") and replay["checks"].get("t5_rejected"):
        assertions.append("independent_validator_rejects_t1_through_t5")
    return {
        **_report_base(commit, recorded_utc), "source_kind": "retained_a2_exp_0042_exploratory_derivation_only",
        "source_identity": {"manifest_or_fixture_sha256": replay["bundle_manifest_sha256"], "generator_sha256": None},
        "checkpoint_schedule_source": "explicit_exp_0042_checkpoint_projection",
        "input_page_blob_count": replay["input_page_blob_count"],
        "parameter_coverage": {"conversion_ordinals": [], "conversion_never": False, "slot_activation_counts": [], "bit_polarities": [], "anchor_fill_states": [], "exp_0042_calibration": None, "record_end_uniform_slack_bytes": []},
        "predicted_terminal_states": ["no_tdef_record_candidate", "growth_polarity_disagreement"],
        "terminal_predicate_ids": replay["terminal_predicate_ids"],
        "result": "pass" if all(replay["checks"].values()) else "fail",
        "assertions": sorted(set(assertions)),
    }


def build_synthetic_report(results: list[CaseResult], checks: dict[str, bool], reachability: dict[str, Any], cases_sha256: str, commit: str, recorded_utc: str) -> dict[str, Any]:
    labels: list[str] = []
    terminals: list[str] = []
    for result in results:
        if result.report is None:
            continue
        statuses = {name: row["status"] for name, row in _layer_rows(result.report).items()}
        labels.extend(label for label in outcome_labels(result.case.expectation, result.report["no_outcome_reasons"], statuses) if label not in labels)
        terminals.extend(item for item in result.terminal_ids if item not in terminals)
    assertions = ["holdout_never_opened", "no_a2_analyzer_results_imported"]
    for check, names in SWEEP_ASSERTIONS.items():
        if checks.get(check):
            assertions.extend(names)
    failed = [result.case.case_id for result in results if result.failures]
    passed = not failed and all(value is True for key, value in checks.items() if key != "generating_commit")
    return {
        **_report_base(commit, recorded_utc), "source_kind": "a3_schedule_synthetic",
        "source_identity": {"manifest_or_fixture_sha256": cases_sha256, "generator_sha256": _sha256_files([SCRIPTS / name for name in GENERATOR_FILES])},
        "checkpoint_schedule_source": "hash_pinned_a3_plan_checkpoint_design",
        "input_page_blob_count": 0, "parameter_coverage": _coverage(results),
        "predicted_terminal_states": labels, "terminal_predicate_ids": terminals,
        "result": "pass" if passed else "fail", "assertions": sorted(set(assertions)),
    }


def write_reports(output: Path, documents: dict[str, dict[str, Any]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    checksums = {}
    for name, document in documents.items():
        payload = canonical_json_bytes(document)
        (output / name).write_bytes(payload)
        checksums[name] = hashlib.sha256(payload).hexdigest()
    (output / "checksums.json").write_bytes(canonical_json_bytes({"algorithm": "sha256", "files": checksums}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--retained-root", type=Path, default=DEFAULT_RETAINED_ROOT)
    parser.add_argument("--commit", default=None, help="analyzer commit to stamp (40 hex); default: git HEAD of this checkout")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--keep-bundles", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.commit is None:
        arguments.commit = subprocess.run(
            ["git", "-C", str(SCRIPTS), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    recorded_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    replay = run_replay(arguments.retained_root).document()
    print(f"replay: {'ok' if all(replay['checks'].values()) else 'FAIL'} {replay['checks']}", file=sys.stderr)
    results = run_sweep(arguments.workspace, arguments.commit, recorded_utc, arguments.jobs, arguments.keep_bundles)
    reachability = reachability_transcript(results)
    checks = sweep_checks(results)
    checks["generating_commit"] = arguments.commit
    checks["every_reachable_predicate_reached"] = reachability["reached_count"] == reachability["reachable_total"]
    checks["unreachable_predicates_nonterminal"] = reachability["unreachable_asserted_nonterminal"]
    cases_document = {
        "document_type": "a3_dry_run_case_transcript", "generating_commit": arguments.commit,
        "generating_commit_note": "the commit whose harness, generator, analyzer, and validator sources produced every artefact in this directory",
        "cases": [result.document() for result in results],
    }
    cases_sha256 = hashlib.sha256(canonical_json_bytes(cases_document)).hexdigest()
    synthetic = build_synthetic_report(results, checks, reachability, cases_sha256, arguments.commit, recorded_utc)
    checks["all_required_terminal_cases_exercised"] = synthetic["result"] == "pass"
    pair = {
        "fixture_count": len(results),
        "agreed": [result.case.case_id for result in results if result.pair["agreement"]],
        "disagreed": {result.case.case_id: result.pair for result in results if not result.pair["agreement"]},
    }
    replay_report = build_replay_report(replay, arguments.commit, recorded_utc)
    for name, document, transcript in (("synthetic", synthetic, checks), ("replay", replay_report, replay["checks"])):
        try:
            validate_dry_run_report(document)
            transcript["report_schema_valid"] = True
        except ValidationError as exc:
            print(f"{name} report validation failed: {exc}", file=sys.stderr)
            transcript["report_schema_valid"] = False
            transcript["report_schema_error"] = str(exc)
    write_reports(arguments.output, {
        "exp-0042-replay-report.json": replay_report,
        "exp-0042-replay-transcript.json": replay,
        "a3-synthetic-report.json": synthetic,
        "a3-synthetic-cases.json": cases_document,
        "a3-reachability-transcript.json": reachability,
        "a3-pair-agreement.json": pair,
        "a3-sweep-checks.json": checks,
    })
    failures = [(result.case.case_id, result.failures) for result in results if result.failures]
    print(f"sweep: {len(results)} fixtures, {len(failures)} failing; reachability {reachability['reached_count']}/{reachability['reachable_total']}; pair agreed {len(pair['agreed'])}/{len(results)}", file=sys.stderr)
    for case_id, items in failures:
        print(f"  {case_id}: {items}", file=sys.stderr)
    replay_ok = all(value is True for key, value in replay["checks"].items() if key != "report_schema_error")
    return 0 if synthetic["result"] == "pass" and checks["report_schema_valid"] and replay_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
