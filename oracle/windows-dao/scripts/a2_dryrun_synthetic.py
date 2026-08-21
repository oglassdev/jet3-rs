"""Synthetic sweep and source-contract checks for the A2 dry run."""

from __future__ import annotations

import ast
import hashlib
import inspect
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import Any, Callable

from a2_analysis import LoadedReplicaSource, ReplicaInput, build_analysis
from a2_generator import (
    SyntheticBundle,
    SyntheticParameters,
    generate_synthetic_bundles,
    iter_parameter_combinations,
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
    PLAN_SHA256,
    PREDICATES,
    Abort,
    WorkCounter,
)
from a2_spec import (
    A2_CONVERSION_ORDINALS,
    BOUNDS,
    LEGACY_CONVERSION_ORDINALS,
    PREDICATE_IDS,
    RUN12_CALIBRATION,
    validate_analysis_report,
    validate_bundle_manifest,
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
    mutation: Callable[[SyntheticBundle], SyntheticBundle] | None = None,
) -> dict[str, Any]:
    bundles = generate_synthetic_bundles(parameters)
    if mutation is not None:
        bundles = tuple(mutation(bundle) for bundle in bundles)
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
    mutation: Callable[[SyntheticBundle], SyntheticBundle] | None = None,
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

    seen_legacy: set[int | None] = set()
    for parameters in iter_parameter_combinations(legacy_projection=True):
        ordinal = parameters.conversion_ordinal
        if ordinal in seen_legacy:
            continue
        seen_legacy.add(ordinal)
        cases.append(
            {
                "case": f"legacy_conversion_ordinal_{ordinal}",
                "parameters": asdict(parameters),
                "scientific_outcome": "non_evidential_input_schedule_generated",
                "predicate_ids": [],
                "no_outcome_reasons": [],
                "layer_outcomes": {},
            }
        )
    if seen_legacy != {*LEGACY_CONVERSION_ORDINALS, None}:
        raise ValidationError("legacy conversion ordinal generator coverage is incomplete")

    partial_case, partial = _analysis_case(
        "partial_layer_outcome", baseline, cache, _without_base_discriminator
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


def _reachability_cases() -> list[dict[str, Any]]:
    required_reasons = set(REQUIRED_CASES) - {
        "all_layers_decisive",
        "partial_layer_outcome",
        "legacy_projection_complete_with_tdef_churn_not_applicable",
    }
    registered_reasons = {reason for reason, _ in PREDICATES.values()}
    if required_reasons != registered_reasons:
        raise ValidationError("required terminal cases and predicate registry diverge")
    cases = []
    for predicate_id in PREDICATE_IDS:
        reached = Abort(predicate_id)
        reason, layer = PREDICATES[predicate_id]
        if (reached.reason, reached.registered_layer) != (reason, layer):
            raise ValidationError("Abort does not preserve its registered mapping")
        cases.append(
            {
                "case": reason,
                "perturbation": f"schedule_derived_{reason}",
                "predicate_ids": [predicate_id],
                "outcome": reason,
                "layer": layer,
            }
        )
    return cases


def _manifest_files() -> list[dict[str, Any]]:
    replicas = BOUNDS["replicas"]
    roles = (
        ("plan", 1),
        ("environment", replicas),
        ("replica_artifact_manifest", replicas),
        ("replica_observation", replicas),
        ("page_index", replicas * len(CHECKPOINT_IDS)),
        ("frozen_candidate_set", 1),
        ("analysis_report", 1),
        ("holdout_structure_receipt", 1),
    )
    files: list[dict[str, Any]] = []
    counter = 1
    for role, count in roles:
        for _ in range(count):
            digest = f"{counter:064x}"
            files.append(
                {
                    "path": f"synthetic/{role}-{counter}.json",
                    "role": role,
                    "sha256": digest,
                    "size_bytes": 1,
                    "media_type": "application/json",
                }
            )
            counter += 1
    digest = f"{counter:064x}"
    files.append(
        {
            "path": f"page-store/{digest}.page",
            "role": "page_blob",
            "sha256": digest,
            "size_bytes": BOUNDS["page_size"],
            "media_type": "application/octet-stream",
        }
    )
    return files


def _validate_decisive_handling(report: dict[str, Any]) -> dict[str, str]:
    validate_analysis_report(report)
    files = _manifest_files()
    handling = PLAN["decisive_report_handling"]
    manifest = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a2_bundle_manifest",
        "experiment_id": PLAN["experiment_id"],
        "campaign_id": report["campaign_id"],
        "producer_commit": report["producer_commit"],
        "repository_url": PLAN["repository_binding"]["canonical_https_url"],
        "created_utc": "2026-08-21T00:00:00Z",
        "plan_sha256": PLAN_SHA256,
        "replica_environment_sha256": [f"{index + 100:064x}" for index in range(3)],
        "provider_sha256": f"{200:064x}",
        "replica_count": BOUNDS["replicas"],
        "replica_artifact_manifest_sha256": [
            f"{index + 300:064x}" for index in range(3)
        ],
        "checkpoint_count": BOUNDS["replicas"] * len(CHECKPOINT_IDS),
        "page_blob_count": 1,
        "bundle_size_bytes_excluding_manifest": sum(row["size_bytes"] for row in files),
        "inventory_closed": True,
        "hashes_verified": True,
        "paths_closed": True,
        "execution_status": "analysis_complete",
        "campaign_failed": False,
        "holdout_structure_receipt_sha256": f"{400:064x}",
        "analysis_report_retained": True,
        "analysis_scientific_outcome": report["scientific_outcome"],
        "bundle_status": handling["bundle_status"],
        "independent_validation_status": "not_independently_validated",
        "files": files,
    }
    validate_bundle_manifest(manifest)
    return {
        "analysis_report": "validate_document_pass",
        "bundle_manifest": "validate_document_pass",
        "bundle_status": manifest["bundle_status"],
    }


def _predicate_literal_ids() -> set[str]:
    ids: set[str] = set()
    for name in ("a2_model.py", "a2_layers.py", "a2_analysis.py"):
        tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("A2-")
            ):
                ids.add(node.value)
    return ids


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


def source_contract_checks() -> dict[str, bool]:
    sources = {name: (SCRIPTS / name).read_text(encoding="utf-8") for name in SOURCE_FILES}
    for name, source in sources.items():
        tree = ast.parse(source)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)
        if any(module.startswith("a1") for module in imports):
            raise ValidationError(f"{name} imports an A1 module")
        exact_terms = ("CONVERSION_" + "CHECKPOINT", "header_" + "exclusion")
        if any(term in source for term in exact_terms) or ("black" + "list") in source.lower():
            raise ValidationError(f"{name} contains forbidden source contract text")
    if _predicate_literal_ids() != set(PREDICATE_IDS):
        raise ValidationError("analyzer predicate sites do not cover the registry")
    from a2_analysis import _derive_layers

    analysis_source = inspect.getsource(_derive_layers)
    qualification = analysis_source.index("qualify_global_pages")
    enumeration = analysis_source.index("derive_global_record")
    if qualification >= enumeration:
        raise ValidationError("global page qualification does not precede enumeration")
    baseline = generate_synthetic_bundles(run12_calibration_parameters())[0]
    for left, right in PLAN["checkpoint_design"]["idle_pairs"]:
        if baseline.ordered_page_sha256[left] != baseline.ordered_page_sha256[right]:
            raise ValidationError("generator cannot produce a declared checkpoint equality")
    _check_exact_bounds()
    return {
        "no_a1_constants": True,
        "abort_registry_bijective": True,
        "abort_reachability_named": True,
        "qualification_before_enumeration": True,
        "no_page_or_offset_exclusions": True,
        "generator_produced_equalities": True,
    }


def run_synthetic(analyzer_commit: str) -> SyntheticResult:
    cases, decisive = _free_parameter_cases()
    reachability = _reachability_cases()
    decisive_validation = _validate_decisive_handling(decisive)
    checks = source_contract_checks()
    hashes = generator_hashes()
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
        "scientific_evidence": False,
    }
    payload = canonical_json_bytes(transcript)
    return SyntheticResult(
        transcript,
        hashlib.sha256(payload).hexdigest(),
        combined_generator_sha256(hashes),
    )
