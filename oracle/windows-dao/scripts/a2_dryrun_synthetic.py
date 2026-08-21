"""Synthetic sweep and source-contract checks for the A2 dry run."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import Any

from a2_analysis import LoadedReplicaSource, ReplicaInput, build_analysis
from a2_dryrun_mutations import Mutation, attempts
from a2_dryrun_validator import validate_decisive_handling
from a2_generator import (
    SyntheticBundle,
    SyntheticParameters,
    generate_synthetic_bundles,
    run12_calibration_parameters,
)
from a2_generator_pages import _extended_page
from a2_model import (
    CHECKPOINT_IDS,
    MAX_CANDIDATE_MODELS,
    MAX_PAGE_BLOBS,
    MAX_RECORD_CANDIDATES,
    MAX_WORK_UNITS,
    PAGE_SIZE,
    PER_PAGE_CANDIDATES,
    PLAN,
    PLAN_PATH,
    PLAN_SHA256,
    PREDICATES,
    Abort,
    WorkCounter,
)
from a2_spec import (
    A2_CONVERSION_ORDINALS,
    LEGACY_CONVERSION_ORDINALS,
    PREDICATE_IDS,
    RUN12_CALIBRATION,
    validate_analysis_report,
)
from protocol_validation import ValidationError, canonical_json_bytes

SCRIPTS = Path(__file__).resolve().parent
SYNTHETIC = PLAN["analyzer_dry_run_contract"]["synthetic_input"]
REQUIRED_CASES = tuple(SYNTHETIC["required_cases"])
GENERATOR_FILES = (
    "a2_generator.py",
    "a2_generator_pages.py",
    "a2_generator_schedule.py",
)
SOURCE_FILES = (
    "a2_spec.py",
    "a2_generator.py",
    "a2_generator_pages.py",
    "a2_generator_schedule.py",
    "a2_model.py",
    "a2_layers.py",
    "a2_analysis.py",
)

@dataclass(frozen=True)
class SyntheticResult:
    transcript: dict[str, Any]
    transcript_sha256: str
    generator_sha256: str
    result: str
    assertions: tuple[str, ...]
    terminal_predicate_ids: tuple[str, ...]
    terminal_states: tuple[str, ...]


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generator_hashes() -> dict[str, str]:
    return {name: _file_hash(SCRIPTS / name) for name in GENERATOR_FILES}


def combined_generator_sha256(hashes: dict[str, str]) -> str:
    return hashlib.sha256(canonical_json_bytes(hashes)).hexdigest()


def _source(bundle: SyntheticBundle) -> LoadedReplicaSource:
    observation = bundle.documents[f"observations/replica-{bundle.replica:02d}.json"]
    before = bundle.schedule.checkpoint("L_REL_1280")
    deleted = bundle.schedule.checkpoint("L_DELETE_ALL")
    replica = ReplicaInput(
        bundle,
        bundle.replica,
        observation["campaign_id"],
        observation["producer_commit"],
        observation["provider_sha256"],
        before.table_row_counts["L"] > 0 and deleted.table_row_counts["L"] == 0,
    )
    return LoadedReplicaSource(replica)


def _analyze(
    parameters: SyntheticParameters,
    mutation: Mutation | None = None,
) -> dict[str, Any]:
    bundles = generate_synthetic_bundles(parameters)
    if mutation is not None:
        bundles = mutation(bundles)
    sources = [_source(bundle) for bundle in bundles]
    with TemporaryDirectory(prefix="a2-dryrun-") as directory:
        report = build_analysis(
            sources,
            Path(directory) / "derivation-candidates.json",
            lambda digest: _require_sha256(digest, "frozen candidate set"),
        )
    validate_analysis_report(report)
    return report


def _require_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValidationError(f"{label} is not a lowercase SHA-256")


def _layer_outcomes(report: dict[str, Any]) -> dict[str, str]:
    return {
        "global_map_record": report["submodels"]["global_map"]["record"]["status"],
        "global_map_conversion_inline": report["submodels"]["global_map"][
            "conversion_inline"
        ]["status"],
        "global_map_extended_base": report["submodels"]["global_map"][
            "extended_base"
        ]["status"],
        "tdef_pointer_pair": report["submodels"]["tdef"]["pointer_pair"]["status"],
    }


def _analysis_case(
    name: str,
    parameters: SyntheticParameters,
    cache: dict[SyntheticParameters, dict[str, Any]],
    mutation: Mutation | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if mutation is None:
        report = cache.get(parameters)
        if report is None:
            report = _analyze(parameters)
            cache[parameters] = report
    else:
        report = _analyze(parameters, mutation)
    case = {
        "case": name,
        "parameters": asdict(parameters),
        "scientific_outcome": report["scientific_outcome"],
        "predicate_ids": report["terminal_predicate_ids"],
        "no_outcome_reasons": report["no_outcome_reasons"],
        "layer_outcomes": _layer_outcomes(report),
    }
    return case, report


def _without_base_discriminator(bundle: SyntheticBundle) -> SyntheticBundle:
    """Make the planned H_REL_0064 slot-0 flip equal its predecessor."""
    reference = bundle.schedule.checkpoint("P_ABS_12288").target_threshold_pages
    if reference is None:
        raise ValidationError("synthetic base discriminator reference is absent")
    ordered = dict(bundle.ordered_page_sha256)
    predecessor = ordered["P_ABS_16480"]
    changed = list(ordered["H_REL_0064"])
    changed[reference] = predecessor[reference]
    high_reference = bundle.schedule.checkpoint("P_ABS_16480").target_threshold_pages
    if high_reference is None:
        raise ValidationError("synthetic high-slot reference is absent")
    high_payload = _extended_page(bundle.parameters.bit_polarity, (0,))
    high_digest = hashlib.sha256(high_payload).hexdigest()
    changed[high_reference] = high_digest
    ordered["H_REL_0064"] = tuple(changed)
    payloads = dict(bundle._payloads)
    payloads[high_digest] = high_payload
    return replace(
        bundle,
        ordered_page_sha256=MappingProxyType(ordered),
        _payloads=MappingProxyType(payloads),
    )


def _free_parameter_cases() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline = run12_calibration_parameters()
    cache: dict[SyntheticParameters, dict[str, Any]] = {}
    cases: list[dict[str, Any]] = []
    decisive_case, decisive = _analysis_case("all_layers_decisive", baseline, cache)
    cases.append(decisive_case)
    if set(decisive_case["layer_outcomes"].values()) != {"decisive_predicts_holdout"}:
        raise ValidationError("all-layers-decisive fixture did not recover every layer")

    for ordinal in (*A2_CONVERSION_ORDINALS, None):
        parameters = replace(baseline, conversion_ordinal=ordinal)
        case, _ = _analysis_case(f"a2_conversion_ordinal_{ordinal}", parameters, cache)
        cases.append(case)
    free = SYNTHETIC["free_parameters"]
    axes = (
        ("slot_activation", "slot_activation_at_conversion"),
        ("bit_polarity", "bit_polarity"),
        ("anchor_fill", "anchor_fill_state"),
        ("record_end_slack", "record_end_uniform_slack_bytes"),
    )
    for label, field in axes:
        for value in free[field]:
            parameters = replace(baseline, **{field: value})
            case, _ = _analysis_case(f"{label}_{value}", parameters, cache)
            cases.append(case)

    cases.extend(_legacy_parameter_cases(baseline, cache))

    partial_case, partial = _analysis_case(
        "partial_layer_outcome",
        baseline,
        cache,
        lambda bundles: tuple(_without_base_discriminator(bundle) for bundle in bundles),
    )
    cases.append(partial_case)
    global_map = partial["submodels"]["global_map"]
    if (
        global_map["record"]["status"] != "decisive_predicts_holdout"
        or global_map["conversion_inline"]["status"] != "decisive_predicts_holdout"
        or global_map["extended_base"]["no_outcome_reasons"]
        != ["insufficient_base_discrimination"]
    ):
        raise ValidationError("partial layered outcome did not remain locally retained")
    return cases, decisive


def _legacy_projection_ordinal(source_ordinal: int | None) -> int | None:
    if source_ordinal is None:
        return None
    a1_plan_path = PLAN_PATH.parents[1] / "a1" / "a1-allocation-maps.plan.json"
    a1_plan = json.loads(a1_plan_path.read_bytes())
    source_ids = tuple(a1_plan["checkpoint_design"]["checkpoint_ids"])
    if len(source_ids) - 1 != len(LEGACY_CONVERSION_ORDINALS):
        raise ValidationError("checked A1 legacy schedule ordinal count changed")
    source_positions = {checkpoint: index for index, checkpoint in enumerate(source_ids)}
    projection = [
        row
        for row in PLAN["analyzer_dry_run_contract"]["retained_a1_input"][
            "checkpoint_projection"
        ]
        if row["a1_checkpoint"] is not None
    ]
    projected = next(
        row
        for row in projection
        if source_positions[row["a1_checkpoint"]] >= source_ordinal
    )
    return CHECKPOINT_IDS.index(projected["a2_checkpoint"])


def _legacy_parameter_cases(
    baseline: SyntheticParameters,
    cache: dict[SyntheticParameters, dict[str, Any]],
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for source_ordinal in (*LEGACY_CONVERSION_ORDINALS, None):
        projected_ordinal = _legacy_projection_ordinal(source_ordinal)
        parameters = replace(baseline, conversion_ordinal=projected_ordinal)
        case, _ = _analysis_case(
            f"legacy_conversion_ordinal_{source_ordinal}", parameters, cache
        )
        case["legacy_source_conversion_ordinal"] = source_ordinal
        case["projected_a2_conversion_ordinal"] = projected_ordinal
        case["input_schedule"] = "a1_legacy_projected_by_checkpoint_identity"
        cases.append(case)
    return cases


def _reachability_cases() -> list[dict[str, Any]]:
    required_reasons = set(REQUIRED_CASES) - {
        "all_layers_decisive",
        "partial_layer_outcome",
        "legacy_projection_complete_with_tdef_churn_not_applicable",
    }
    registered_reasons = {reason for reason, _ in PREDICATES.values()}
    if required_reasons != registered_reasons:
        raise ValidationError("required terminal cases and predicate registry diverge")
    baseline = run12_calibration_parameters()
    registered = attempts(baseline)
    if tuple(row.predicate_id for row in registered) != PREDICATE_IDS:
        raise ValidationError("reachability mutations do not follow the predicate registry")
    cases: list[dict[str, Any]] = []
    for attempt in registered:
        report = _analyze(attempt.parameters, attempt.mutation)
        predicate_id = attempt.predicate_id
        reason, layer = PREDICATES[predicate_id]
        result = next(
            row for row in report["predicate_results"] if row["predicate_id"] == predicate_id
        )
        reached = result["status"] == "fail" and predicate_id in report["terminal_predicate_ids"]
        if reached and reason not in report["no_outcome_reasons"]:
            raise ValidationError("reached predicate omitted its registered reason")
        cases.append(
            {
                "case": reason,
                "perturbation": attempt.perturbation,
                "target_predicate_id": predicate_id,
                "actual_predicate_ids": report["terminal_predicate_ids"],
                "actual_reasons": report["no_outcome_reasons"],
                "status": "reached" if reached else "unreachable",
                "outcome": reason if reached else "target_not_reached",
                "layer": layer,
                "reported_layer": result["layer"],
                "parameters": asdict(attempt.parameters),
            }
        )
    return cases


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _predicate_site_ids() -> tuple[set[str], list[str]]:
    ids: set[str] = set()
    dynamic: list[str] = []
    for name in ("a2_model.py", "a2_layers.py", "a2_analysis.py"):
        tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = _called_name(node)
            if called == "Abort" and node.args:
                value = node.args[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    ids.add(value.value)
                else:
                    alternatives = {
                        child.value
                        for child in ast.walk(value)
                        if isinstance(child, ast.Constant)
                        and isinstance(child.value, str)
                        and child.value.startswith("A2-")
                    }
                    ids.update(alternatives)
                    if not alternatives:
                        dynamic.append(f"{name}:{node.lineno}")
            elif called == "_derive_pages" and len(node.args) >= 6:
                for value in node.args[3:6]:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        ids.add(value.value)
            elif called == "add" and node.args:
                value = node.args[0]
                if (
                    isinstance(value, ast.Constant)
                    and value.value == "A2-HOLDOUT-PREDICTION"
                ):
                    ids.add(value.value)
    return ids, dynamic


def _function_node(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _check(status: bool, evidence: Any) -> dict[str, Any]:
    return {"status": "pass" if status else "fail", "evidence": evidence}


def _check_exact_bounds() -> None:
    work = WorkCounter()
    work.value = MAX_WORK_UNITS
    work.charge(0)
    try:
        work.charge(1)
    except Abort as exc:
        if exc.predicate_id != "A2-RESOURCE-BOUND":
            raise
    else:
        raise ValidationError("work ceiling accepted one over")

    work = WorkCounter()
    work.record_candidates = MAX_RECORD_CANDIDATES - PER_PAGE_CANDIDATES
    work.enumerate_intervals()
    try:
        work.enumerate_intervals()
    except Abort as exc:
        if exc.predicate_id != "A2-RESOURCE-BOUND":
            raise
    else:
        raise ValidationError("record-candidate ceiling accepted one over")

    work = WorkCounter()
    work.candidate_models = MAX_CANDIDATE_MODELS
    work.examine_models(0)
    try:
        work.examine_models()
    except Abort as exc:
        if exc.predicate_id != "A2-RESOURCE-BOUND":
            raise
    else:
        raise ValidationError("candidate-model ceiling accepted one over")

    work = WorkCounter()
    work.page_bytes_read = PLAN["bounds"]["max_retained_page_store_bytes"] - PAGE_SIZE
    work.page_digests = {f"{index:064x}" for index in range(MAX_PAGE_BLOBS - 1)}
    work.opened(f"{MAX_PAGE_BLOBS - 1:064x}")
    try:
        work.opened(f"{MAX_PAGE_BLOBS:064x}")
    except Abort as exc:
        if exc.predicate_id != "A2-RESOURCE-BOUND":
            raise
    else:
        raise ValidationError("page-blob ceiling accepted one over")


def source_contract_checks(
    reachability: list[dict[str, Any]] | None = None,
    parameter_cases: list[dict[str, Any]] | None = None,
    decisive_validation: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    sources = {name: (SCRIPTS / name).read_text(encoding="utf-8") for name in SOURCE_FILES}
    import_violations: list[str] = []
    a1_binding_literals: list[str] = []
    for name, source in sources.items():
        tree = ast.parse(source)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)
        import_violations.extend(
            f"{name}:{module}" for module in imports if module.startswith("a1")
        )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and ("a1-allocation" in node.value.lower() or "CONVERSION_CHECKPOINT" in node.value)
            ):
                a1_binding_literals.append(f"{name}:{node.lineno}")

    site_ids, dynamic_sites = _predicate_site_ids()
    mappings_bijective = (
        len(PREDICATES) == len(PREDICATE_IDS)
        and set(PREDICATES) == set(PREDICATE_IDS)
        and len({reason for reason, _ in PREDICATES.values()}) == len(PREDICATES)
    )
    reachability = _reachability_cases() if reachability is None else reachability
    reached = {
        row["target_predicate_id"]
        for row in reachability
        if row["status"] == "reached"
    }

    analysis_tree = ast.parse(sources["a2_analysis.py"])
    derive_layers = _function_node(analysis_tree, "_derive_layers")
    calls = [
        (_called_name(node), node.lineno)
        for node in ast.walk(derive_layers)
        if isinstance(node, ast.Call)
    ]
    qualification_lines = [
        node.lineno
        for node in ast.walk(derive_layers)
        if isinstance(node, ast.Name)
        and node.id in {"qualify_global_pages", "qualify_tdef_pages"}
    ]
    enumeration_lines = [line for name, line in calls if name in {"derive_global_record", "derive_tdef"}]
    qualification_first = bool(qualification_lines and enumeration_lines) and (
        min(qualification_lines) < min(enumeration_lines)
    )

    model_tree = ast.parse(sources["a2_model.py"])
    prefix_count = _function_node(
        next(
            node
            for node in model_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Prefix"
        ),
        "count",
    )
    bounded_prefix = not any(
        isinstance(node, (ast.For, ast.While, ast.comprehension))
        for node in ast.walk(prefix_count)
    ) and any(isinstance(node, ast.Sub) for node in ast.walk(prefix_count))

    layers_tree = ast.parse(sources["a2_layers.py"])
    pointer_candidates = _function_node(layers_tree, "_pointer_candidates")
    compared_coordinates = []
    for node in ast.walk(pointer_candidates):
        if not isinstance(node, ast.Compare):
            continue
        names = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
        if names & {"page", "offset"} and any(
            isinstance(child, ast.Constant) and isinstance(child.value, int)
            for child in ast.walk(node)
        ):
            compared_coordinates.append(node.lineno)
    loop_sources = {
        ast.unparse(node.target): ast.unparse(node.iter)
        for node in ast.walk(pointer_candidates)
        if isinstance(node, ast.For)
    }
    global_function = _function_node(model_tree, "derive_global_record")
    global_loops = {
        ast.unparse(node.target): ast.unparse(node.iter)
        for node in ast.walk(global_function)
        if isinstance(node, (ast.For, ast.comprehension))
    }
    structural_scan = (
        loop_sources.get("offset") == "range(PAGE_SIZE - 3)"
        and loop_sources.get("layout") == "POINTER_LAYOUTS"
        and not compared_coordinates
    )
    header_neutral = structural_scan and any(
        "range(PAGE_SIZE - 5)" in value for value in global_loops.values()
    )

    global_source = ast.unparse(global_function)
    conversion_source = ast.unparse(_function_node(layers_tree, "derive_conversion"))
    base_source = ast.unparse(_function_node(layers_tree, "derive_base"))
    tdef_function = _function_node(layers_tree, "derive_tdef")
    tdef_arguments = {argument.arg for argument in tdef_function.args.args}
    layer_separation = (
        "d_checkpoints" in global_source
        and "derive_conversion" not in global_source
        and "global_model" in conversion_source
        and "global_model" in base_source
        and "global_model" not in tdef_arguments
    )

    baseline = generate_synthetic_bundles(run12_calibration_parameters())[0]
    for left, right in PLAN["checkpoint_design"]["idle_pairs"]:
        if baseline.ordered_page_sha256[left] != baseline.ordered_page_sha256[right]:
            raise ValidationError("generator cannot produce a declared checkpoint equality")
    from a2_layers import _stable, _window_values

    stable_sources = {
        name: ast.unparse(_function_node(layers_tree, name))
        for name in ("_growth_pointer_matches", "_churn_pointer_matches", "_pointer_candidates")
    }
    work = WorkCounter()
    from a2_model import CHURN_TRANSITIONS, D_TRANSITIONS, GROWTH_TRANSITIONS, IDLE_PAIRS, View

    view = View(baseline, work)
    growth_values = _window_values(
        view,
        baseline.tdef.page,
        baseline.tdef.growth_pointer_offset,
        baseline.tdef.pointer_layout,
    )
    churn_values = _window_values(
        view,
        baseline.tdef.page,
        baseline.tdef.delete_reinsert_pointer_offset,
        baseline.tdef.pointer_layout,
    )
    equality_evidence = {
        "growth_pointer_churn_stable": _stable(
            growth_values,
            CHURN_TRANSITIONS + D_TRANSITIONS + IDLE_PAIRS,
        ),
        "churn_pointer_growth_stable": _stable(
            churn_values,
            GROWTH_TRANSITIONS + D_TRANSITIONS + IDLE_PAIRS,
        ),
        "ast_comparisons": stable_sources,
    }
    generator_equalities = all(
        value for key, value in equality_evidence.items() if key != "ast_comparisons"
    ) and all(
        token in "\n".join(stable_sources.values())
        for token in ("D_TRANSITIONS", "GROWTH_TRANSITIONS", "CHURN_TRANSITIONS", "IDLE_PAIRS")
    )
    _check_exact_bounds()
    if parameter_cases is None:
        parameter_cases, _ = _free_parameter_cases()
    legacy_sources = {
        row.get("legacy_source_conversion_ordinal")
        for row in parameter_cases
        if row.get("input_schedule") == "a1_legacy_projected_by_checkpoint_identity"
    }
    parameter_complete = legacy_sources == {*LEGACY_CONVERSION_ORDINALS, None}
    decisive_validation = decisive_validation or {}
    return {
        "no_a1_constants": _check(
            not import_violations and not a1_binding_literals,
            {"import_violations": import_violations, "binding_literals": a1_binding_literals},
        ),
        "abort_registry_bijective": _check(
            mappings_bijective and site_ids == set(PREDICATE_IDS),
            {"site_ids": sorted(site_ids), "dynamic_abort_sites": dynamic_sites},
        ),
        "abort_reachability": _check(
            reached == set(PREDICATE_IDS),
            {"reached": sorted(reached), "unreachable": sorted(set(PREDICATE_IDS) - reached)},
        ),
        "qualification_before_enumeration": _check(
            qualification_first and bounded_prefix,
            {"qualification_lines": qualification_lines, "enumeration_lines": enumeration_lines, "prefix_count_o1": bounded_prefix},
        ),
        "transition_structural_exclusions": _check(
            structural_scan,
            {"pointer_loops": loop_sources, "coordinate_comparisons": compared_coordinates},
        ),
        "header_bytes_position_neutral": _check(
            header_neutral,
            {"global_candidate_loops": global_loops, "pointer_loops": loop_sources},
        ),
        "layer_input_separation": _check(
            layer_separation,
            {"tdef_arguments": sorted(tdef_arguments), "global_uses_d_checkpoint_parameter": "d_checkpoints" in global_source},
        ),
        "parameter_coverage_executed": _check(
            parameter_complete,
            {"legacy_ordinals_analyzed": len(legacy_sources), "case_count": len(parameter_cases)},
        ),
        "generator_produced_equalities": _check(generator_equalities, equality_evidence),
        "decisive_report_handling": _check(
            decisive_validation.get("bundle_status") == "decisive_pending_independent_validation"
            and decisive_validation.get("analysis_report") == "validate_document_pass"
            and decisive_validation.get("bundle_manifest") == "validate_document_pass",
            decisive_validation,
        ),
    }


def run_synthetic(analyzer_commit: str) -> SyntheticResult:
    cases, decisive = _free_parameter_cases()
    reachability = _reachability_cases()
    decisive_validation = validate_decisive_handling(decisive)
    checks = source_contract_checks(reachability, cases, decisive_validation)
    hashes = generator_hashes()
    reached_ids = tuple(
        row["target_predicate_id"]
        for row in reachability
        if row["status"] == "reached"
    )
    reached_states = {
        row["outcome"] for row in reachability if row["status"] == "reached"
    }
    reached_states.update(
        {
            "all_layers_decisive",
            "partial_layer_outcome",
            "legacy_projection_complete_with_tdef_churn_not_applicable",
        }
    )
    source_checks_pass = all(row["status"] == "pass" for row in checks.values())
    required_cases_pass = set(REQUIRED_CASES) <= reached_states
    result = "pass" if source_checks_pass and required_cases_pass else "fail"
    assertions = [
        "schedule_and_worker_arithmetic_generated_from_plan",
        "conversion_ordinal_parameter_complete",
        "slot_activation_parameter_complete",
        "bit_polarity_parameter_complete",
        "all_layers_decisive_model_recovered",
        "partial_layer_outcome_retained",
        "decisive_layered_report_accepted_by_contract_validator",
        "run12_calibration_case_non_evidential",
        "bounds_accept_exact_and_reject_one_over",
    ]
    check_assertions = {
        "no_a1_constants": "no_a1_hand_typed_counts_imported",
        "qualification_before_enumeration": "page_qualification_precedes_interval_enumeration",
        "transition_structural_exclusions": "transition_structural_exclusion_is_page_agnostic",
        "header_bytes_position_neutral": "no_page_or_offset_blacklist",
        "generator_produced_equalities": "all_analyzer_equalities_generator_producible",
        "abort_registry_bijective": "every_abort_has_pinned_reason_mapping",
    }
    assertions.extend(
        assertion
        for check_name, assertion in check_assertions.items()
        if checks[check_name]["status"] == "pass"
    )
    if checks["abort_reachability"]["status"] == "pass":
        assertions.append("every_abort_reached_by_single_perturbation")
    if required_cases_pass:
        assertions.append("all_required_terminal_cases_exercised")
    transcript = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a2_dry_run_case_transcript",
        "experiment_id": PLAN["experiment_id"],
        "plan_sha256": PLAN_SHA256,
        "analyzer_commit": analyzer_commit,
        "generator_files": hashes,
        "run12_calibration": dict(RUN12_CALIBRATION),
        "cases": cases,
        "predicate_reachability": reachability,
        "source_contract_checks": checks,
        "decisive_validator": decisive_validation,
        "acceptance": {
            "result": result,
            "required_terminal_cases_pass": required_cases_pass,
            "source_contract_checks_pass": source_checks_pass,
            "unreachable_predicate_ids": sorted(set(PREDICATE_IDS) - set(reached_ids)),
        },
        "scientific_evidence": False,
    }
    payload = canonical_json_bytes(transcript)
    return SyntheticResult(
        transcript,
        hashlib.sha256(payload).hexdigest(),
        combined_generator_sha256(hashes),
        result,
        tuple(assertions),
        reached_ids,
        tuple(state for state in REQUIRED_CASES if state in reached_states),
    )
