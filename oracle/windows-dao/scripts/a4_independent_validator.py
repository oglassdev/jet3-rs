#!/usr/bin/env python3
"""Independently recompute and validate one retained DAO A4 bundle."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from a4_independent_bundle import (
    BundleLoader,
    LoadedBundle,
    ValidationError,
    canonical_document_bytes,
    canonical_json_bytes,
)
from a4_independent_campaign import require_campaign, verify_frozen_transcripts
from a4_independent_contract import (
    CONTRACT,
    EXPECTED_TAMPERS,
    PLAN_SHA256,
    REVISION_PLAN_SHA256,
    ContractError,
    validate_canonical_snapshot,
    validate_snapshot_schedule,
)
from a4_independent_h1 import apply_h1_holdout, recompute_h1
from a4_independent_h2 import predict_h2_holdout, recompute_h2
from a4_independent_h3 import predict_h3_holdout, recompute_h3
from a4_independent_h4 import (
    predict_h4_fields_holdout,
    predict_h4_root_holdout,
    recompute_h4,
)
from a4_independent_projection import (
    compare_frozen_report,
    compare_recomputation,
    failure_document,
    logical_read_projection,
    pair_projection_document,
    recompute_only_document,
    validate_claims,
    verdict,
)


_HOLDOUT_NAMES = {
    "A4-H1-HOLDOUT-PREDICTION": "h1",
    "A4-H2-HOLDOUT-PREDICTION": "h2",
    "A4-H3-HOLDOUT-PREDICTION": "h3",
    "A4-H4-HOLDOUT-ROOT": "h4_root",
    "A4-H4-HOLDOUT-FIELDS": "h4_fields",
}


def _candidate_hash(candidates: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(canonical_json_bytes(list(candidates))).hexdigest()


def _not_applicable() -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    return {
        "status": "not_applicable",
        "predicate_measured_survivor_count": 0,
        "derivation_survivor_count": 0,
        "terminal_predicate_id": None,
        "terminal_payload_kind": None,
        "terminal_candidate_stage": None,
        "candidates": candidates,
        "terminal_evidence": None,
        "canonical_candidates_sha256": _candidate_hash(candidates),
    }


def _h4_not_applicable() -> dict[str, Any]:
    return {
        "root_result": _not_applicable(),
        "structural_result": _not_applicable(),
        "encoding_result": _not_applicable(),
    }


def _is_model(result: Mapping[str, Any]) -> bool:
    return result.get("status") == "model" and len(result.get("candidates", ())) == 1


def _snapshot_row_counts(
    bundle: LoadedBundle, numbers: Sequence[int]
) -> dict[int, dict[str, dict[str, int]]]:
    return {
        number: {
            checkpoint: {
                table["logical_role"]: table["row_count"]
                for table in replica.snapshots[checkpoint]["tables"]
            }
            for checkpoint in CONTRACT.checkpoint_ids
        }
        for number in numbers
        for replica in (bundle.replicas[number],)
    }


def _all_results(layers: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    h4 = layers["h4_catalog_bootstrap"]
    return (
        layers["h1_tdef_to_map_row"],
        layers["h2_row_identity_map_role"],
        layers["h3_indirect_traversal"],
        h4["root_result"],
        h4["structural_result"],
        h4["encoding_result"],
    )


def _charged_candidates(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates = list(result.get("candidates", ()))
    evidence = result.get("terminal_evidence")
    if result.get("terminal_payload_kind") == "replica_pair" and isinstance(
        evidence, Mapping
    ):
        entries = evidence.get("entries")
        if isinstance(entries, list):
            candidates.extend(
                entry["complete_candidate"]
                for entry in entries
                if isinstance(entry, Mapping)
                and isinstance(entry.get("complete_candidate"), Mapping)
            )
    return candidates


def _work_document(
    plan: Mapping[str, Any],
    layers: Mapping[str, Any],
    work_parts: Sequence[Mapping[str, int]],
) -> dict[str, int]:
    model = plan["work_model"]
    limits = {
        **{name: row["units"] for name, row in model["terms"].items()},
        **{
            name: row["units"]
            for name, row in model["terminal_path_maxima"]["alternative_terms"].items()
        },
    }
    work = {name: 0 for name in limits}
    for part in work_parts:
        for name, value in part.items():
            if name not in work or isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError("resource_bound_breach", f"unknown work term {name}")
            work[name] += value
    payloads = {
        canonical_json_bytes(dict(candidate))
        for result in _all_results(layers)
        for candidate in _charged_candidates(result)
    }
    if any(len(payload) > int(plan["bounds"]["max_canonical_candidate_bytes"]) for payload in payloads):
        raise ValidationError("resource_bound_breach", "canonical candidate bytes")
    work["candidate_serializations"] = len(payloads)
    if any(value < 0 or value > limits[name] for name, value in work.items()):
        raise ValidationError("resource_bound_breach", "work term")
    total = sum(work.values())
    if total > int(plan["bounds"]["max_analysis_work_units"]):
        raise ValidationError("resource_bound_breach", "total work")
    return {**work, "total_work_units": total}


def _qualified_pages(
    parts: Sequence[Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    ordinal = {checkpoint: index for index, checkpoint in enumerate(CONTRACT.checkpoint_ids)}
    identities = {
        (row["replica"], row["checkpoint_id"], row["page_number"])
        for part in parts
        for row in part
    }
    return [
        {"replica": replica, "checkpoint_id": checkpoint, "page_number": page}
        for replica, checkpoint, page in sorted(
            identities, key=lambda row: (row[0], ordinal[row[1]], row[2])
        )
    ]


def _module_rows(*groups: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    for group in groups:
        for row in group:
            predicate = row["predicate_id"]
            if predicate in output:
                raise ValidationError("predicate_layer_projection_mismatch")
            output[predicate] = row
    return output


def _predicate_document(
    module_rows: Mapping[str, Mapping[str, Any]],
    holdout: Mapping[str, bool | None],
    campaign_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    contracts = CONTRACT.plan["predicate_registry"]["predicate_contracts"]
    campaign = {row["predicate_id"]: row for row in campaign_rows}
    output: list[dict[str, Any]] = []
    for contract in contracts:
        predicate = contract["predicate_id"]
        if contract["scope"] == "campaign":
            source = campaign.get(predicate)
            status, measured = (
                ("not_applicable", 0)
                if source is None
                else (source["status"], source["predicate_measured_survivor_count"])
            )
        elif predicate in _HOLDOUT_NAMES:
            value = holdout[_HOLDOUT_NAMES[predicate]]
            status = "not_applicable" if value is None else "pass" if value else "fail"
            measured = 0 if value is None else 1
        else:
            source = module_rows.get(predicate)
            if source is None:
                status, measured = "not_applicable", 0
            else:
                status = source["status"]
                measured = source.get(
                    "predicate_measured_survivor_count",
                    source.get("measured_survivor_count"),
                )
                if measured is None:
                    raise ValidationError(
                        "predicate_layer_projection_mismatch",
                        f"missing measurement for {predicate}",
                    )
        retains = (
            contract["scope"] != "campaign"
            and status != "not_applicable"
            and (status == "pass" or predicate in _HOLDOUT_NAMES)
        )
        output.append({
            "predicate_id": predicate,
            "order": contract["order"],
            "scope": contract["scope"],
            "status": status,
            "terminal_predicate_id": predicate if status == "fail" else None,
            "predicate_measured_survivor_count": measured,
            "derivation_survivor_count": 1 if retains else 0,
            "reachability_fixture_id": contract["reachability_fixture_id"],
        })
    return output


def recompute_bundle(
    bundle: LoadedBundle, *, open_holdout: bool = True
) -> dict[str, Any]:
    """Rebuild derivation and holdout projections without analyzer code."""
    plan = bundle.plan
    campaign_rows = (
        require_campaign(bundle).predicate_rows if open_holdout else ()
    )
    contracts = plan["predicate_registry"]["predicate_contracts"]
    rows = _snapshot_row_counts(bundle, (1, 2, 3) if open_holdout else (1, 2))
    h1 = recompute_h1(bundle.replicas, plan=plan, predicate_contracts=contracts)
    h2_result = _not_applicable()
    h3_result = _not_applicable()
    h4_result = _h4_not_applicable()
    h2 = h3 = h4 = None
    row_groups: list[Sequence[Mapping[str, Any]]] = [h1.predicate_results]
    page_groups: list[Sequence[Mapping[str, Any]]] = [h1.qualified_pages]
    work_groups: list[Mapping[str, int]] = [h1.work_charges]
    if _is_model(h1.layer):
        h2 = recompute_h2(
            bundle.replicas,
            h1,
            plan=plan,
            predicate_contracts=contracts,
            snapshot_row_counts=rows,
        )
        h2_result = dict(h2.layer)
        row_groups.append(h2.predicate_results)
        page_groups.append(h2.qualified_pages)
        work_groups.append(h2.work_charges)
    if h2 is not None and _is_model(h2_result):
        h3 = recompute_h3(bundle.replicas, h1, h2, plan)
        h3_result = dict(h3["result"])
        row_groups.append(h3["predicates"])
        page_groups.append(h3["qualified_pages"])
        work_groups.append(h3["work_charges"])
    if h3 is not None and _is_model(h3_result):
        h4 = recompute_h4(
            {
                "protocol_version": bundle.manifest["protocol_version"],
                "plan_sha256": bundle.plan_sha256,
                "revision_plan_sha256": bundle.manifest["revision_plan_sha256"],
                "campaign_id": bundle.manifest["campaign_id"],
            },
            bundle.replicas,
            h1,
            h2,
            h3,
            plan,
        )
        h4_result = dict(h4["result"])
        row_groups.append(h4["predicates"])
        page_groups.append(h4["qualified_pages"])
        work_groups.append(h4["work_charges"])
    layers = {
        "h1_tdef_to_map_row": dict(h1.layer),
        "h2_row_identity_map_role": h2_result,
        "h3_indirect_traversal": h3_result,
        "h4_catalog_bootstrap": h4_result,
    }

    holdout: dict[str, bool | None] = dict.fromkeys(_HOLDOUT_NAMES.values())
    holdout_h1 = None
    if open_holdout and _is_model(h1.layer):
        holdout_h1 = apply_h1_holdout(bundle.replicas[3], h1.layer, plan=plan)
        holdout["h1"] = holdout_h1 is not None
    if holdout_h1 is not None and _is_model(h2_result):
        holdout["h2"] = predict_h2_holdout(
            bundle.replicas[3], holdout_h1, h2_result,
            plan=plan, snapshot_row_counts=rows,
        )
    if holdout_h1 is not None and holdout["h2"] and _is_model(h3_result):
        holdout["h3"] = predict_h3_holdout(
            bundle.replicas[3], holdout_h1, h2_result, h3_result, plan
        )
    if holdout_h1 is not None and holdout["h3"] and _is_model(h4_result["root_result"]):
        args = (bundle.replicas[3], holdout_h1, h2_result, h3_result, h4_result, plan)
        holdout["h4_root"] = predict_h4_root_holdout(*args)
        if holdout["h4_root"] and _is_model(h4_result["encoding_result"]):
            holdout["h4_fields"] = predict_h4_fields_holdout(*args)
    holdout_document = {
        name: {
            "status": "not_applicable" if value is None else "pass" if value else "fail",
            "terminal_predicate_id": (
                next(key for key, item in _HOLDOUT_NAMES.items() if item == name)
                if value is False else None
            ),
        }
        for name, value in holdout.items()
    }
    module_rows = _module_rows(*row_groups)
    return {
        "layers": layers,
        "qualified_pages": _qualified_pages(page_groups),
        "work_charges": _work_document(plan, layers, work_groups),
        "predicate_results": _predicate_document(module_rows, holdout, campaign_rows),
        "holdout_results": holdout_document,
        "scientific_outcome": (
            "one_or_more_layers_predict_holdout"
            if any(value is True for value in holdout.values())
            else "no_layer_predicts_holdout"
        ),
        "h4_occurrence_evidence": None if h4 is None else h4["occurrence_evidence"],
        "h4_occurrence_evidence_bytes": (
            None if h4 is None else h4["occurrence_evidence_bytes"]
        ),
        "transcripts": {
            name: list(rows)
            for name, rows in verify_frozen_transcripts(bundle).items()
        },
    }


def _validate_bindings(bundle: LoadedBundle) -> None:
    values: list[Mapping[str, Any]] = [bundle.manifest, bundle.frozen, bundle.report, bundle.receipt]
    values.extend(replica.environment for replica in bundle.replicas.values())
    values.extend(replica.artifact_manifest for replica in bundle.replicas.values())
    values.extend(replica.observation for replica in bundle.replicas.values())
    values.extend(index for replica in bundle.replicas.values() for index in replica.indexes.values())
    values.extend(snapshot for replica in bundle.replicas.values() for snapshot in replica.snapshots.values())
    if any(
        value.get("plan_sha256") != PLAN_SHA256
        or value.get("revision_plan_sha256") != REVISION_PLAN_SHA256
        for value in values
    ):
        raise ValidationError("plan_binding_mismatch")


def _validate_timing(bundle: LoadedBundle) -> None:
    elapsed = bundle.manifest.get("campaign_elapsed_seconds")
    if isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0:
        raise ValidationError("campaign_timing_mismatch")
    if elapsed > int(CONTRACT.bounds["campaign_timeout_seconds"]):
        raise ValidationError("campaign_timeout_exceeded")


def _validate_snapshots(bundle: LoadedBundle) -> None:
    for number, replica in bundle.replicas.items():
        for checkpoint, snapshot in replica.snapshots.items():
            validate_canonical_snapshot(snapshot, f"replica {number} {checkpoint}")
            validate_snapshot_schedule(snapshot, bundle.plan, number, checkpoint)
            try:
                ordinal = CONTRACT.checkpoint_ids.index(checkpoint)
                path = f"schema-snapshots/replica-{number:02d}/{ordinal:02d}-{checkpoint}.json"
                entry = bundle.entries[path]
            except KeyError as exc:
                raise ValidationError("schema_snapshot_mismatch") from exc
            raw = canonical_document_bytes(snapshot)
            if (
                hashlib.sha256(raw).hexdigest() != entry["sha256"]
                or len(raw) != entry["size_bytes"]
            ):
                raise ValidationError("schema_snapshot_mismatch")


def _candidate_identity(candidate: Mapping[str, Any]) -> tuple[str, str]:
    model_type = candidate.get("model_type")
    model = candidate.get("model")
    bindings = candidate.get("instance_bindings")
    if not isinstance(model_type, str) or not isinstance(model, Mapping):
        raise ValidationError("candidate_canonicalization_mismatch")
    model_id = hashlib.sha256(canonical_json_bytes({
        "model_type": model_type, "model": dict(model),
    })).hexdigest()
    candidate_identity: dict[str, Any] = {"model_type": model_type, "model": dict(model)}
    if bindings is not None:
        if not isinstance(bindings, list):
            raise ValidationError("candidate_canonicalization_mismatch")
        candidate_identity["instance_bindings"] = bindings
    candidate_id = hashlib.sha256(canonical_json_bytes(candidate_identity)).hexdigest()
    return model_id, candidate_id


def _validate_locator_order(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "locator_offsets" and (
                not isinstance(child, list)
                or any(isinstance(item, bool) or not isinstance(item, int) for item in child)
                or child != sorted(child)
                or len(set(child)) != len(child)
            ):
                raise ValidationError("candidate_canonicalization_mismatch")
            _validate_locator_order(child)
    elif isinstance(value, list):
        for child in value:
            _validate_locator_order(child)


def _validate_candidates(layers: Mapping[str, Any]) -> None:
    for result in _all_results(layers):
        candidates = result.get("candidates")
        if not isinstance(candidates, list) or result.get("canonical_candidates_sha256") != _candidate_hash(candidates):
            raise ValidationError("candidate_canonicalization_mismatch")
        for candidate in candidates:
            model_id, candidate_id = _candidate_identity(candidate)
            expected_model_id = model_id if "instance_bindings" in candidate else None
            if candidate.get("canonical_model_id") != expected_model_id or candidate.get(
                "canonical_candidate_id"
            ) != candidate_id:
                raise ValidationError("candidate_canonicalization_mismatch")
            _validate_locator_order(candidate)
        for candidate in _charged_candidates(result)[len(candidates) :]:
            model_id, candidate_id = _candidate_identity(candidate)
            expected_model_id = model_id if "instance_bindings" in candidate else None
            if candidate.get("canonical_model_id") != expected_model_id or candidate.get(
                "canonical_candidate_id"
            ) != candidate_id:
                raise ValidationError("candidate_canonicalization_mismatch")
            _validate_locator_order(candidate)


def _validate_occurrence(bundle: LoadedBundle, recomputed: Mapping[str, Any]) -> None:
    expected = recomputed["h4_occurrence_evidence_bytes"]
    if expected != bundle.occurrence_evidence_raw:
        raise ValidationError("frozen_set_recomputation_mismatch")


def _validate_report_order(report: Mapping[str, Any]) -> None:
    rows = report.get("predicate_results")
    if not isinstance(rows, list) or [row.get("predicate_id") for row in rows] != list(
        CONTRACT.predicate_ids
    ) or [row.get("order") for row in rows] != list(range(1, 41)):
        raise ValidationError("analysis_report_mismatch")


def _validate_predicate_layer_projection(report: Mapping[str, Any]) -> None:
    rows = {row["predicate_id"]: row for row in report["predicate_results"]}
    results = _all_results(report["layers"])
    terminals = [
        result["terminal_predicate_id"]
        for result in results
        if result.get("status") == "no_outcome"
    ]
    for result in results:
        status = result.get("status")
        candidates = result.get("candidates")
        if status == "not_applicable" and candidates:
            raise ValidationError("predicate_layer_projection_mismatch")
        if status == "model" and (
            not isinstance(candidates, list)
            or len(candidates) != 1
            or result.get("terminal_predicate_id") is not None
        ):
            raise ValidationError("predicate_layer_projection_mismatch")
    layer_ids = tuple(
        predicate
        for predicates in CONTRACT.layer_predicates.values()
        for predicate in predicates
    )
    if not terminals:
        if not all(result.get("status") == "model" for result in results) or any(
            rows[predicate]["status"] != "pass" for predicate in layer_ids
        ):
            raise ValidationError("predicate_layer_projection_mismatch")
        return
    unique_terminals = set(terminals)
    if len(unique_terminals) != 1 or next(iter(unique_terminals)) not in layer_ids:
        raise ValidationError("predicate_layer_projection_mismatch")
    terminal_index = layer_ids.index(next(iter(unique_terminals)))
    expected = ["pass"] * terminal_index + ["fail"] + [
        "not_applicable"
    ] * (len(layer_ids) - terminal_index - 1)
    if [rows[predicate]["status"] for predicate in layer_ids] != expected:
        raise ValidationError("predicate_layer_projection_mismatch")


def _validate_holdout_projection(
    report: Mapping[str, Any], recomputed: Mapping[str, Any]
) -> None:
    if report.get("holdout_results") != recomputed.get("holdout_results"):
        raise ValidationError("holdout_projection_mismatch")
    rows = {row["predicate_id"]: row for row in report["predicate_results"]}
    for predicate, name in _HOLDOUT_NAMES.items():
        result = report["holdout_results"][name]
        if (
            rows[predicate]["status"] != result["status"]
            or rows[predicate]["terminal_predicate_id"]
            != result["terminal_predicate_id"]
        ):
            raise ValidationError("holdout_projection_mismatch")
    outcome = (
        "one_or_more_layers_predict_holdout"
        if any(row["status"] == "pass" for row in report["holdout_results"].values())
        else "no_layer_predicts_holdout"
    )
    if report.get("scientific_outcome") != outcome or outcome != recomputed.get(
        "scientific_outcome"
    ):
        raise ValidationError("holdout_projection_mismatch")


def validate_bundle(bundle: LoadedBundle, recomputed: Mapping[str, Any]) -> None:
    _validate_bindings(bundle)
    _validate_timing(bundle)
    _validate_snapshots(bundle)
    _validate_candidates(bundle.frozen["layers"])
    _validate_report_order(bundle.report)
    compare_frozen_report(bundle)
    _validate_predicate_layer_projection(bundle.report)
    _validate_holdout_projection(bundle.report, recomputed)
    compare_recomputation(bundle, recomputed)
    _validate_occurrence(bundle, recomputed)
    validate_claims(bundle)


def _replace_candidate_ids(candidate: dict[str, Any]) -> None:
    model_id, candidate_id = _candidate_identity(candidate)
    if "instance_bindings" in candidate:
        candidate["canonical_model_id"] = model_id
    candidate["canonical_candidate_id"] = candidate_id


def _tamper_h1_candidate(result: dict[str, Any]) -> dict[str, Any]:
    candidates = result["candidates"]
    if candidates:
        candidate = candidates[0]
    else:
        grammar = CONTRACT.plan["candidate_grammars"]["h1"]
        candidate = {
            "model_type": "h1_locator_pair",
            "model": {
                "layout": grammar["locator_layouts"][0],
                "table_signature_id": grammar["table_record_signature"]["signature_id"],
                "locator_offsets": [35, 39],
            },
            "instance_bindings": [{
                "replica": 1,
                "logical_role": "T1",
                "lifecycle_instance": "T1-v1",
                "tdef_page": 1,
                "applicable_checkpoint_range": {
                    "start": "T1_CREATE_ID", "end": "T4_IDLE_R",
                },
                "locator_targets": [
                    {"page": 1, "row": 0}, {"page": 1, "row": 1},
                ],
            }],
        }
        _replace_candidate_ids(candidate)
        candidates.append(candidate)
    return candidate


def _expect_rejection(
    identifier: str,
    expected: str,
    action: Callable[[], None],
) -> dict[str, Any]:
    try:
        action()
    except (ValidationError, ContractError) as exc:
        code = getattr(exc, "code", "malformed_bundle")
        if code == expected:
            return {"id": identifier, "rejected": True, "discrepancy_code": code}
        raise ValidationError("tamper_rejection_mismatch", f"{identifier}: {code}") from exc
    raise ValidationError("tamper_not_rejected", identifier)


def execute_tamper_suite(
    bundle: LoadedBundle, recomputed: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Execute all preregistered mutations against semantic checks."""
    actions: dict[str, Callable[[], None]] = {}

    t1_manifest = copy.deepcopy(bundle.manifest)
    t1_manifest["plan_sha256"] = "0" * 64
    actions["T1"] = lambda: _validate_bindings(replace(bundle, manifest=t1_manifest))

    t2_frozen = copy.deepcopy(bundle.frozen)
    t2_candidates = t2_frozen["layers"]["h1_tdef_to_map_row"]["candidates"]
    t2_candidate = _tamper_h1_candidate(
        t2_frozen["layers"]["h1_tdef_to_map_row"]
    )
    target = t2_candidate["instance_bindings"][0]["locator_targets"][0]
    target["row"] = target["row"] - 1 if target["row"] == 255 else target["row"] + 1
    _replace_candidate_ids(t2_candidate)
    t2_frozen["layers"]["h1_tdef_to_map_row"]["canonical_candidates_sha256"] = _candidate_hash(t2_candidates)
    actions["T2"] = lambda: compare_recomputation(replace(bundle, frozen=t2_frozen), recomputed)

    snapshots = dict(bundle.replicas[1].snapshots)
    checkpoint = CONTRACT.checkpoint_ids[-1]
    snapshots[checkpoint] = copy.deepcopy(snapshots[checkpoint])
    payload = next(
        field
        for table in snapshots[checkpoint]["tables"]
        for field in table["fields"]
        if field["name"] == "Payload"
    )
    payload["attributes"] += 1
    replicas = dict(bundle.replicas)
    replicas[1] = replace(replicas[1], snapshots=snapshots)
    entries = dict(bundle.entries)
    ordinal = CONTRACT.checkpoint_ids.index(checkpoint)
    snapshot_path = f"schema-snapshots/replica-01/{ordinal:02d}-{checkpoint}.json"
    entries[snapshot_path] = dict(entries[snapshot_path])
    snapshot_raw = canonical_document_bytes(snapshots[checkpoint])
    entries[snapshot_path]["sha256"] = hashlib.sha256(snapshot_raw).hexdigest()
    entries[snapshot_path]["size_bytes"] = len(snapshot_raw)
    actions["T3"] = lambda: _validate_snapshots(
        replace(bundle, replicas=replicas, entries=entries)
    )

    t4_report = copy.deepcopy(bundle.report)
    t4_report["predicate_results"] = list(reversed(t4_report["predicate_results"]))
    actions["T4"] = lambda: _validate_report_order(t4_report)

    t5_manifest = copy.deepcopy(bundle.manifest)
    t5_manifest["campaign_elapsed_seconds"] = 2701
    actions["T5"] = lambda: _validate_timing(replace(bundle, manifest=t5_manifest))

    t6_report = copy.deepcopy(bundle.report)
    inapplicable = next(
        (result for result in _all_results(t6_report["layers"])
         if result["status"] == "not_applicable"),
        None,
    )
    if inapplicable is not None:
        inapplicable["candidates"].append({"retained": "tamper"})
    else:
        first = t6_report["layers"]["h1_tdef_to_map_row"]
        first["status"] = "no_outcome"
        first["terminal_predicate_id"] = CONTRACT.layer_predicates[
            "h1_tdef_to_map_row"
        ][0]
    actions["T6"] = lambda: _validate_predicate_layer_projection(t6_report)

    t7_report = copy.deepcopy(bundle.report)
    holdout_name = next(iter(t7_report["holdout_results"]))
    original = t7_report["holdout_results"][holdout_name]["status"]
    t7_report["holdout_results"][holdout_name]["status"] = "fail" if original != "fail" else "pass"
    actions["T7"] = lambda: _validate_holdout_projection(t7_report, recomputed)

    t8_frozen = copy.deepcopy(bundle.frozen)
    t8_candidates = t8_frozen["layers"]["h1_tdef_to_map_row"]["candidates"]
    t8_candidate = _tamper_h1_candidate(
        t8_frozen["layers"]["h1_tdef_to_map_row"]
    )
    t8_frozen["layers"]["h1_tdef_to_map_row"]["canonical_candidates_sha256"] = _candidate_hash(t8_candidates)
    offsets = t8_candidate["model"]["locator_offsets"]
    offsets.reverse()
    actions["T8"] = lambda: _validate_candidates(t8_frozen["layers"])

    t9_report = copy.deepcopy(bundle.report)
    t9_report["derivation_candidate_set_sha256"] = "0" * 64
    actions["T9"] = lambda: compare_frozen_report(replace(bundle, report=t9_report))

    return [
        _expect_rejection(identifier, expected, actions[identifier])
        for identifier, expected in EXPECTED_TAMPERS
    ]


def _validator_commit() -> str:
    root = Path(__file__).resolve().parents[3]
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit("cannot resolve validator commit") from exc


def _write_output(value: Mapping[str, Any], output: Path | None, root: Path) -> None:
    raw = canonical_document_bytes(value)
    if output is None:
        sys.stdout.buffer.write(raw)
        return
    destination = output.resolve()
    bundle_root = root.resolve()
    if destination == bundle_root or bundle_root in destination.parents:
        raise ValidationError("output_inside_bundle")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validator-commit")
    parser.add_argument("--recompute-only", action="store_true")
    parser.add_argument("--pair-projection", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.recompute_only and args.pair_projection:
        raise SystemExit("--recompute-only and --pair-projection are mutually exclusive")
    validator_commit = args.validator_commit or _validator_commit()
    if len(validator_commit) != 40 or any(character not in "0123456789abcdef" for character in validator_commit):
        raise SystemExit("--validator-commit must be 40 lowercase hexadecimal characters")
    bundle: LoadedBundle | None = None
    tamper_results: list[dict[str, Any]] = []
    try:
        bundle = BundleLoader(args.bundle_root).load(
            open_holdout=not args.recompute_only
        )
        recomputed = recompute_bundle(bundle, open_holdout=not args.recompute_only)
        if args.recompute_only:
            _write_output(recompute_only_document(bundle, recomputed), args.output, args.bundle_root)
            return 0
        if args.pair_projection:
            _write_output(pair_projection_document(bundle, recomputed), args.output, args.bundle_root)
            return 0
        validate_bundle(bundle, recomputed)
        tamper_results = execute_tamper_suite(bundle, recomputed)
        reads = logical_read_projection(bundle)
        result = verdict(
            bundle, validator_commit, accepted=True, discrepancy_codes=[],
            tamper_results=tamper_results, logical_reads=reads,
        )
        CONTRACT.validate_document(result, "dao_a4_independent_validation_report")
        _write_output(result, args.output, args.bundle_root)
        return 0
    except (ValidationError, ContractError) as exc:
        code = getattr(exc, "code", "malformed_bundle")
    except (KeyError, TypeError, ValueError, IndexError, OSError, OverflowError, json.JSONDecodeError):
        code = "malformed_bundle"
    result = failure_document(
        bundle, validator_commit, code, tamper_results,
    )
    _write_output(result, args.output, args.bundle_root)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
