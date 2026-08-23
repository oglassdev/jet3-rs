from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
PLAN_PATH = ROOT / "oracle" / "windows-dao" / "experiments" / "a3" / "a3-allocation-maps.plan.json"
R4_PATH = ROOT / "oracle" / "windows-dao" / "experiments" / "a3" / "a3-allocation-maps-r4.plan.json"
sys.path.insert(0, str(SCRIPTS))

from a3_independent_bundle import BundleLoader, ValidationError, canonical_json_bytes  # noqa: E402
from a3_independent_core import recompute_derivation  # noqa: E402
from a3_independent_validator import (  # noqa: E402
    R4_SHA256,
    verify_report_bounds,
    _expected_predicate_statuses,
    _load_predicate_sequences,
    _t5_target,
    _tamper_t5,
    _validate_terminal_predicate_ids,
    main,
)


CHECKPOINTS = [
    "E0", "E0R", "D_GROW_0128", "D_DROP", "D_RECREATE_EMPTY", "D_REGROW_0128",
    "L_REL_0064", "L_REL_0512", "L_REL_0768", "L_REL_0896", "L_REL_0904",
    "L_REL_1024", "L_REL_1088", "L_REL_1280", "L_DELETE_ALL", "L_REINSERT_SAME",
    "L_IDLE_REOPEN", "P_ABS_04096", "P_ABS_08192", "P_ABS_12288", "P_ABS_16480",
    "H_REL_0064", "H_REL_0896", "H_REL_0904", "H_IDLE_REOPEN",
]
COUNTS = [
    129, 129, 257, 257, 258, 386, 450, 898, 1154, 1282, 1290, 1410, 1474,
    1666, 1666, 1666, 1666, 4096, 8192, 12288, 16480, 16544, 17376, 17384, 17384,
]
PRODUCER = "a" * 40
PROVIDER = "b" * 64
CAMPAIGN = "a3-synthetic-tamper-suite"
GLOBAL_START = 1700


def _write(path: Path, value: Any, canonical: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    raw = canonical_json_bytes(value) if canonical else (json.dumps(value, indent=2) + "\n").encode()
    path.write_bytes(raw)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(root: Path, relative: str, role: str, media_type: str = "application/json") -> dict[str, Any]:
    path = root / relative
    return {
        "path": relative,
        "role": role,
        "sha256": _digest(path),
        "size_bytes": path.stat().st_size,
        "media_type": media_type,
    }


def _bitmap_page(in_use_count: int, violation: int | None = None) -> bytes:
    page = bytearray([0xFF] * 2048)
    page[GLOBAL_START] = 0
    page[GLOBAL_START + 1 : GLOBAL_START + 5] = (0).to_bytes(4, "little")
    for physical in range(in_use_count):
        page[GLOBAL_START + 5 + physical // 8] &= ~(1 << (physical % 8))
    if violation is not None:
        page[GLOBAL_START + 5 + violation // 8] |= 1 << (violation % 8)
    return bytes(page)


def _pointer_bytes(reference: int, slot: int = 1) -> bytes:
    return reference.to_bytes(3, "little") + bytes([slot])


def _tdef_page(checkpoint: str) -> bytes:
    page = bytearray(2048)
    growth_reference = 65546 if CHECKPOINTS.index(checkpoint) <= CHECKPOINTS.index("L_REL_0064") else 10
    churn_reference = 65556 if checkpoint == "L_DELETE_ALL" else 20
    page[2040:2044] = bytes([1]) + growth_reference.to_bytes(3, "little")
    page[2044:2048] = bytes([1]) + churn_reference.to_bytes(3, "little")
    return bytes(page)


def _global_page(checkpoint: str) -> bytes:
    if checkpoint in {"E0", "E0R", "D_DROP", "D_RECREATE_EMPTY"}:
        return _bitmap_page(129)
    if checkpoint == "D_GROW_0128":
        return _bitmap_page(257)
    if checkpoint == "D_REGROW_0128":
        return _bitmap_page(386)
    if checkpoint == "L_REL_0064":
        return _bitmap_page(450)
    if checkpoint == "L_REL_0512":
        return _bitmap_page(898)
    if CHECKPOINTS.index(checkpoint) < CHECKPOINTS.index("P_ABS_04096"):
        return _bitmap_page(COUNTS[CHECKPOINTS.index(checkpoint)])
    page = bytearray([0xFF] * 2048)
    page[GLOBAL_START] = 1
    page[GLOBAL_START + 1 : GLOBAL_START + 5] = (10).to_bytes(4, "little")
    page[GLOBAL_START + 5 : GLOBAL_START + 9] = (16362).to_bytes(4, "little")
    page[GLOBAL_START + 9 :] = bytes(2048 - GLOBAL_START - 9)
    return bytes(page)


def _extended_page(slot: int, checkpoint: str) -> bytes:
    page = bytearray([0xFF] * 2048)
    page[:4] = bytes([0x05, 0x00, 0x00, 0x00])
    self_bit = 10
    page[4 + self_bit // 8] &= ~(1 << (self_bit % 8))
    if slot == 0 and CHECKPOINTS.index(checkpoint) >= CHECKPOINTS.index("H_REL_0064"):
        page[4 + 100 // 8] &= ~(1 << (100 % 8))
    return bytes(page)


def _changed_discriminator_page(checkpoint: str) -> bytes:
    page = bytearray([0x05]) + bytearray(2047)
    if CHECKPOINTS.index(checkpoint) >= CHECKPOINTS.index("H_REL_0064"):
        page[1] = 1
    return bytes(page)


def _page_digest(root: Path, raw: bytes, known: dict[str, bytes]) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    if digest not in known:
        known[digest] = raw
        path = root / "page-store" / f"{digest}.page"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return digest


def _environment(replica: int, plan_sha: str) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0", "document_type": "dao_a3_environment",
        "experiment_id": "DAO-A3-ALLOCATION-MAPS-001", "plan_sha256": plan_sha,
        "producer_commit": PRODUCER, "repository_url": "https://github.com/oglassdev/jet3-rs.git",
        "campaign_id": CAMPAIGN, "replica": replica, "matrix_job_id": f"synthetic-{replica}",
        "status": "ready",
        "host": {"windows_version": "synthetic", "process_architecture": "x86", "powershell_version": "5.1", "python_version": "3.13.7", "runner_image": "synthetic"},
        "provider": {"prog_id": "DAO.DBEngine.36", "clsid": "{00000000-0000-0000-0000-000000000000}", "provider_version": "synthetic", "server_path": "C:/dao360.dll", "server_file_version": "synthetic", "server_sha256": PROVIDER},
    }


def _reread(role: str, rows: int) -> dict[str, Any]:
    return {"role": role, "row_count": rows, "rolling_sha256": "0" * 64}


def _checkpoint_observation(checkpoint: str, ordinal: int, count: int, index_entry: dict[str, Any]) -> dict[str, Any]:
    if checkpoint == "L_DELETE_ALL":
        l_rows = 0
    elif ordinal >= 6:
        l_rows = 1024
    else:
        l_rows = 0
    row_counts = {"D": 2048 if ordinal >= 2 and checkpoint not in {"D_DROP", "D_RECREATE_EMPTY"} else 0, "L": l_rows, "P": 0, "H": 0}
    return {
        "checkpoint_id": checkpoint, "ordinal": ordinal,
        "actual_file_pages": count, "actual_size_bytes": count * 2048,
        "target_baseline_pages": None, "target_threshold_pages": None, "target_overshoot_pages": None,
        "inserted_rows_total": 10000, "table_row_counts": row_counts,
        "dao_reread": [_reread(role, rows) for role, rows in row_counts.items()],
        "quiescent": True,
        "post_close_companion": {"present_after_close": False, "observed_size_bytes": 0, "retained_for_physical_analysis": False},
        "page_index": {"path": index_entry["path"], "sha256": index_entry["sha256"], "size_bytes": index_entry["size_bytes"]},
    }


def _frozen(plan_sha: str, slack: int) -> dict[str, Any]:
    cross = {
        "evaluated_legs": [
            {"left_checkpoint_id": "D_REGROW_0128", "right_checkpoint_id": "L_REL_0064"},
            {"left_checkpoint_id": "L_REL_0064", "right_checkpoint_id": "L_REL_0512"},
            {"left_checkpoint_id": "L_REL_0512", "right_checkpoint_id": "L_REL_0768"},
            {"left_checkpoint_id": "L_REL_0768", "right_checkpoint_id": "L_REL_0896"},
            {"left_checkpoint_id": "L_REL_0896", "right_checkpoint_id": "L_REL_0904"},
            {"left_checkpoint_id": "L_REL_0904", "right_checkpoint_id": "L_REL_1024"},
            {"left_checkpoint_id": "L_REL_1024", "right_checkpoint_id": "L_REL_1088"},
            {"left_checkpoint_id": "L_REL_1088", "right_checkpoint_id": "L_REL_1280"},
        ],
        "representation_change_stop": {"left_checkpoint_id": "L_IDLE_REOPEN", "right_checkpoint_id": "P_ABS_04096"},
        "first_violating_leg": None,
        "first_violating_page": None,
    }
    return {
        "protocol_version": "1.0.0", "document_type": "dao_a3_frozen_derivation_candidates",
        "experiment_id": "DAO-A3-ALLOCATION-MAPS-001", "plan_sha256": plan_sha,
        "campaign_id": CAMPAIGN, "derivation_replicas": [1, 2],
        "qualified_pages": {"global_map": [1], "tdef": [2]}, "polarity_cross_check": cross,
        "layers": {
            "global_map_record": {"applicable": True, "derivation_survivor_count": 1, "model": {"record": {"page": 1, "start": GLOBAL_START, "end": 2048}, "bit_polarity": "set_means_not_in_use", "zero_suffix_slack_bytes": slack}, "no_outcome_reason": None, "terminal_predicate_id": None},
            "global_map_conversion_inline": {"applicable": True, "derivation_survivor_count": 1, "model": {"conversion_checkpoint_id": "P_ABS_04096", "conversion_ordinal": 17, "indirect_tag": 1, "active_slot_count_at_conversion": 2, "active_slot_count_at_h_rel_0904": 2, "inline_boundary": 1914, "slot_reference_pages": [10, 16362]}, "no_outcome_reason": None, "terminal_predicate_id": None},
            "global_map_extended_base": {"applicable": True, "derivation_survivor_count": 1, "model": {"extended_base_formula": "slot_relative_expected_0_16352"}, "no_outcome_reason": None, "terminal_predicate_id": None},
            "tdef_pointer_pair": {"applicable": True, "derivation_survivor_count": 1, "model": {"record": {"page": 2, "start": 2039, "end": 2048}, "pointer_layout": "u8_slot_then_u24le_page", "growth_pointer_offset": 2040, "delete_reinsert_pointer_offset": 2044}, "no_outcome_reason": None, "terminal_predicate_id": None},
        },
    }


def _report(plan: dict[str, Any], plan_sha: str, frozen: dict[str, Any], frozen_sha: str) -> dict[str, Any]:
    terminal: set[str] = set()
    mapping = {item["predicate_id"]: item["layer"] for item in plan["predicate_registry"]["mappings"]}
    predicates = [
        {"predicate_id": predicate, "status": "pass", "layer": mapping[predicate]}
        for predicate in plan["predicate_registry"]["ids"]
    ]
    layers = frozen["layers"]
    def report_layer(name: str, status: str, evaluated: bool) -> dict[str, Any]:
        layer = layers[name]
        return {"status": status, "derivation_survivor_count": layer["derivation_survivor_count"], "holdout_evaluated": evaluated, "no_outcome_reasons": [] if layer["no_outcome_reason"] is None else [layer["no_outcome_reason"]], "terminal_predicate_id": layer["terminal_predicate_id"], "model": layer["model"]}
    return {
        "protocol_version": "1.0.0", "document_type": "dao_a3_analysis_report",
        "experiment_id": "DAO-A3-ALLOCATION-MAPS-001", "plan_sha256": plan_sha,
        "campaign_id": CAMPAIGN, "producer_commit": PRODUCER, "derivation_replicas": [1, 2],
        "holdout_replica": 3, "input_checkpoint_count": 75,
        "qualified_page_counts": {"global_map": 1, "tdef": 1}, "qualified_pages": frozen["qualified_pages"],
        "record_candidates_examined": 2 * 2_098_176, "candidate_models_examined": 10,
        "derivation_survivor_counts": {name: layer["derivation_survivor_count"] for name, layer in layers.items()},
        "derivation_candidate_set_sha256": frozen_sha, "polarity_cross_check": frozen["polarity_cross_check"],
        "analysis_work_units": 10_000_000, "holdout_structurally_validated_after_freeze": True,
        "holdout_opened_after_freeze": True, "holdout_evaluated": True,
        "predicate_results": predicates, "terminal_predicate_ids": [predicate for predicate in plan["predicate_registry"]["ids"] if predicate in terminal],
        "scientific_outcome": "one_or_more_submodels_predict_holdout",
        "no_outcome_reasons": [],
        "submodels": {"global_map": {"record": report_layer("global_map_record", "decisive_predicts_holdout", True), "conversion_inline": report_layer("global_map_conversion_inline", "decisive_predicts_holdout", True), "extended_base": report_layer("global_map_extended_base", "decisive_predicts_holdout", True)}, "tdef": {"pointer_pair": report_layer("tdef_pointer_pair", "decisive_predicts_holdout", True)}},
        "claims": {"descriptive_provider_observation_only": True, "general_tdef_catalog_row_index_or_lval_layout": False, "unobserved_slot_or_base_behavior": False, "compaction_encryption_or_version_behavior": False, "rust_correctness": False, "dao_compatibility_or_support": False},
    }


def build_bundle(root: Path) -> None:
    plan = json.loads(PLAN_PATH.read_text())
    plan_raw = PLAN_PATH.read_bytes()
    plan_sha = hashlib.sha256(plan_raw).hexdigest()
    (root / "plan").mkdir(parents=True)
    (root / "plan" / "a3-allocation-maps.plan.json").write_bytes(plan_raw)
    known_pages: dict[str, bytes] = {}
    generic = _page_digest(root, bytes([0x05]) + bytes(2047), known_pages)
    page_zero = _page_digest(root, bytes(2048), known_pages)
    artifact_entries: dict[int, list[dict[str, Any]]] = {}
    environments: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    replica_manifests: list[dict[str, Any]] = []
    for replica in (1, 2, 3):
        env_relative = f"environment/replica-{replica:02d}.json"
        _write(root / env_relative, _environment(replica, plan_sha))
        environments.append(_entry(root, env_relative, "environment"))
        previous: list[str] = []
        index_entries: list[dict[str, Any]] = []
        checkpoint_rows: list[dict[str, Any]] = []
        logical = changed_total = 0
        for ordinal, (checkpoint, count) in enumerate(zip(CHECKPOINTS, COUNTS)):
            hashes = [generic] * count
            hashes[0] = page_zero
            hashes[1] = _page_digest(root, _global_page(checkpoint), known_pages)
            hashes[2] = _page_digest(root, _tdef_page(checkpoint), known_pages)
            if count > 100:
                hashes[100] = _page_digest(root, _changed_discriminator_page(checkpoint), known_pages)
            if CHECKPOINTS.index(checkpoint) >= CHECKPOINTS.index("P_ABS_16480"):
                hashes[10] = _page_digest(root, _extended_page(0, checkpoint), known_pages)
                hashes[16362] = _page_digest(root, _extended_page(1, checkpoint), known_pages)
            changed = [page for page in range(max(len(previous), len(hashes))) if (previous[page] if page < len(previous) else None) != (hashes[page] if page < len(hashes) else None)]
            database = hashlib.sha256()
            for digest in hashes:
                database.update(known_pages[digest])
            index = {
                "protocol_version": "1.0.0", "document_type": "dao_a3_page_index", "experiment_id": "DAO-A3-ALLOCATION-MAPS-001",
                "plan_sha256": plan_sha, "producer_commit": PRODUCER, "campaign_id": CAMPAIGN,
                "environment_sha256": environments[-1]["sha256"], "provider_sha256": PROVIDER, "replica": replica,
                "checkpoint_id": checkpoint, "ordinal": ordinal, "predecessor_checkpoint_id": None if ordinal == 0 else CHECKPOINTS[ordinal - 1],
                "page_count": count, "file_size_bytes": count * 2048, "database_sha256": database.hexdigest(),
                "ordered_page_sha256": hashes, "changed_page_indices": changed,
            }
            relative = f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"
            _write(root / relative, index)
            entry = _entry(root, relative, "page_index")
            index_entries.append(entry)
            checkpoint_rows.append(_checkpoint_observation(checkpoint, ordinal, count, entry))
            logical += count * 2048
            changed_total += len(changed)
            previous = hashes
        bindings = plan["tables"]["role_bindings"][replica - 1]
        observation = {
            "protocol_version": "1.0.0", "document_type": "dao_a3_replica_observation", "experiment_id": "DAO-A3-ALLOCATION-MAPS-001",
            "plan_sha256": plan_sha, "producer_commit": PRODUCER, "repository_url": "https://github.com/oglassdev/jet3-rs.git", "campaign_id": CAMPAIGN,
            "matrix_job": {"job_id": f"synthetic-{replica}", "replica_only": True, "shared_mutable_state": False},
            "environment_sha256": environments[-1]["sha256"], "provider_sha256": PROVIDER, "replica": replica,
            "role_binding": {role: bindings[role] for role in ("D", "L", "P", "H")},
            "d_growth_observation": {"first_baseline_pages": 129, "first_target_pages": 257, "first_achieved_pages": 257, "first_rows": 2048, "regrowth_baseline_pages": 258, "regrowth_target_pages": 386, "regrowth_achieved_pages": 386, "regrowth_rows": 2048},
            "logical_checkpoint_read_bytes": logical, "inserted_rows_total": 10000, "changed_hash_entries": changed_total, "checkpoints": checkpoint_rows,
        }
        observation_relative = f"observations/replica-{replica:02d}.json"
        _write(root / observation_relative, observation)
        observation_entry = _entry(root, observation_relative, "replica_observation")
        observations.append(observation_entry)
        artifact_entries[replica] = [environments[-1], observation_entry, *index_entries]
    page_entries = [_entry(root, f"page-store/{digest}.page", "page_blob", "application/octet-stream") for digest in sorted(known_pages)]
    for replica in (1, 2, 3):
        files = artifact_entries[replica] + page_entries
        replica_manifest = {
            "protocol_version": "1.0.0", "document_type": "dao_a3_replica_artifact_manifest", "experiment_id": "DAO-A3-ALLOCATION-MAPS-001",
            "plan_sha256": plan_sha, "producer_commit": PRODUCER, "campaign_id": CAMPAIGN, "matrix_job_id": f"synthetic-{replica}", "replica": replica,
            "environment_sha256": environments[replica - 1]["sha256"], "provider_sha256": PROVIDER, "checkpoint_count": 25,
            "inventory_closed": True, "hashes_verified": True, "paths_closed": True, "files": files,
        }
        relative = f"replica-artifacts/replica-{replica:02d}-manifest.json"
        _write(root / relative, replica_manifest)
        replica_manifests.append(_entry(root, relative, "replica_artifact_manifest"))
    frozen = _frozen(plan_sha, 294)
    _write(root / "analysis/derivation-candidates.json", frozen, canonical=True)
    frozen_sha = _digest(root / "analysis/derivation-candidates.json")
    report = _report(plan, plan_sha, frozen, frozen_sha)
    _write(root / "analysis/analysis-report.json", report)
    receipt = {
        "protocol_version": "1.0.0", "document_type": "dao_a3_holdout_structure_receipt", "experiment_id": "DAO-A3-ALLOCATION-MAPS-001",
        "plan_sha256": plan_sha, "producer_commit": PRODUCER, "campaign_id": CAMPAIGN, "derivation_candidate_set_sha256": frozen_sha,
        "replica": 3, "replica_artifact_manifest_sha256": replica_manifests[2]["sha256"], "validated_after_candidate_freeze": True,
        "page_bytes_exposed_to_analyzer": False, "result": "pass",
    }
    _write(root / "analysis/holdout-structure-receipt.json", receipt)
    fixed_entries = [
        _entry(root, "plan/a3-allocation-maps.plan.json", "plan"), *environments, *replica_manifests, *observations,
        *[item for replica in artifact_entries.values() for item in replica if item["role"] == "page_index"],
        *page_entries, _entry(root, "analysis/derivation-candidates.json", "frozen_candidate_set"),
        _entry(root, "analysis/analysis-report.json", "analysis_report"),
        _entry(root, "analysis/holdout-structure-receipt.json", "holdout_structure_receipt"),
    ]
    unique_entries = {item["path"]: item for item in fixed_entries}
    files = [unique_entries[path] for path in sorted(unique_entries)]
    manifest = {
        "protocol_version": "1.0.0", "document_type": "dao_a3_bundle_manifest", "experiment_id": "DAO-A3-ALLOCATION-MAPS-001",
        "campaign_id": CAMPAIGN, "producer_commit": PRODUCER, "repository_url": "https://github.com/oglassdev/jet3-rs.git", "created_utc": "2026-08-22T12:00:00Z",
        "plan_sha256": plan_sha, "replica_environment_sha256": [item["sha256"] for item in environments], "provider_sha256": PROVIDER,
        "replica_count": 3, "replica_artifact_manifest_sha256": [item["sha256"] for item in replica_manifests], "checkpoint_count": 75,
        "page_blob_count": len(page_entries), "bundle_size_bytes_excluding_manifest": sum(item["size_bytes"] for item in files),
        "inventory_closed": True, "hashes_verified": True, "paths_closed": True, "execution_status": "analysis_complete", "campaign_failed": False,
        "holdout_structure_receipt_sha256": _digest(root / "analysis/holdout-structure-receipt.json"), "analysis_report_retained": True,
        "analysis_scientific_outcome": "one_or_more_submodels_predict_holdout", "bundle_status": "decisive_pending_independent_validation",
        "independent_validation_status": "not_independently_validated", "files": files,
    }
    _write(root / "bundle-manifest.json", manifest)


def relink(root: Path) -> None:
    frozen_path = root / "analysis/derivation-candidates.json"
    report_path = root / "analysis/analysis-report.json"
    receipt_path = root / "analysis/holdout-structure-receipt.json"
    frozen = json.loads(frozen_path.read_text())
    _write(frozen_path, frozen, canonical=True)
    frozen_sha = _digest(frozen_path)
    report = json.loads(report_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    report["derivation_candidate_set_sha256"] = frozen_sha
    receipt["derivation_candidate_set_sha256"] = frozen_sha
    _write(report_path, report)
    _write(receipt_path, receipt)
    manifest_path = root / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    roles = {item["path"]: (item["role"], item["media_type"]) for item in manifest["files"]}
    manifest["files"] = [
        _entry(root, path, roles[path][0], roles[path][1]) for path in sorted(roles)
    ]
    manifest["bundle_size_bytes_excluding_manifest"] = sum(item["size_bytes"] for item in manifest["files"])
    manifest["holdout_structure_receipt_sha256"] = _digest(receipt_path)
    _write(manifest_path, manifest)


def _set_layer_terminal(report: dict[str, Any], old: str, new: str) -> None:
    if old in report["terminal_predicate_ids"]:
        report["terminal_predicate_ids"] = [new if value == old else value for value in report["terminal_predicate_ids"]]
    else:
        report["terminal_predicate_ids"].append(new)
    for result in report["predicate_results"]:
        if result["predicate_id"] == old:
            result["status"] = "not_applicable"
        elif result["predicate_id"] == new:
            result["status"] = "fail"


def tamper_t1(root: Path) -> None:
    frozen = json.loads((root / "analysis/derivation-candidates.json").read_text())
    report = json.loads((root / "analysis/analysis-report.json").read_text())
    frozen["layers"]["global_map_record"]["model"]["bit_polarity"] = "set_means_in_use"
    report["submodels"]["global_map"]["record"]["model"]["bit_polarity"] = "set_means_in_use"
    _write(root / "analysis/derivation-candidates.json", frozen, canonical=True)
    _write(root / "analysis/analysis-report.json", report)
    relink(root)


def tamper_t2(root: Path) -> None:
    frozen = json.loads((root / "analysis/derivation-candidates.json").read_text())
    report = json.loads((root / "analysis/analysis-report.json").read_text())
    layer = frozen["layers"]["global_map_conversion_inline"]
    layer.update({"derivation_survivor_count": 0, "model": None, "no_outcome_reason": "missing_inline_to_indirect_conversion", "terminal_predicate_id": "A3-CONVERSION-NONE"})
    report_layer = report["submodels"]["global_map"]["conversion_inline"]
    report_layer.update({"status": "no_outcome", "derivation_survivor_count": 0, "holdout_evaluated": False, "no_outcome_reasons": ["missing_inline_to_indirect_conversion"], "terminal_predicate_id": "A3-CONVERSION-NONE", "model": None})
    report["derivation_survivor_counts"]["global_map_conversion_inline"] = 0
    report["no_outcome_reasons"] = ["missing_inline_to_indirect_conversion"]
    _set_layer_terminal(report, "A3-POLARITY-CROSSCHECK", "A3-CONVERSION-NONE")
    _write(root / "analysis/derivation-candidates.json", frozen, canonical=True)
    _write(root / "analysis/analysis-report.json", report)
    relink(root)


def tamper_t3(root: Path) -> None:
    frozen = json.loads((root / "analysis/derivation-candidates.json").read_text())
    frozen["qualified_pages"]["global_map"] = [5]
    _write(root / "analysis/derivation-candidates.json", frozen, canonical=True)
    relink(root)


def tamper_t4(root: Path) -> None:
    frozen = json.loads((root / "analysis/derivation-candidates.json").read_text())
    report = json.loads((root / "analysis/analysis-report.json").read_text())
    layer = frozen["layers"]["tdef_pointer_pair"]
    layer.update({"derivation_survivor_count": 0, "model": None, "no_outcome_reason": "no_growth_only_pointer_candidate", "terminal_predicate_id": "A3-GROWTH-POINTER-NONE"})
    report_layer = report["submodels"]["tdef"]["pointer_pair"]
    report_layer.update({"status": "no_outcome", "derivation_survivor_count": 0, "holdout_evaluated": False, "no_outcome_reasons": ["no_growth_only_pointer_candidate"], "terminal_predicate_id": "A3-GROWTH-POINTER-NONE", "model": None})
    report["derivation_survivor_counts"]["tdef_pointer_pair"] = 0
    report["no_outcome_reasons"] = ["no_growth_only_pointer_candidate"]
    _set_layer_terminal(report, "A3-TDEF-RECORD-NONE", "A3-GROWTH-POINTER-NONE")
    _write(root / "analysis/derivation-candidates.json", frozen, canonical=True)
    _write(root / "analysis/analysis-report.json", report)
    relink(root)


def tamper_t5(root: Path) -> None:
    report = json.loads((root / "analysis/analysis-report.json").read_text())
    additions = {"A3-IDLE-EQUALITY", "A3-D-SET-RELATION", "A3-HOLDOUT-PREDICTION"}
    terminals = set(report["terminal_predicate_ids"]) | additions
    registry = json.loads(PLAN_PATH.read_text())["predicate_registry"]["ids"]
    report["terminal_predicate_ids"] = [value for value in registry if value in terminals]
    for result in report["predicate_results"]:
        if result["predicate_id"] in additions:
            result["status"] = "fail"
    _write(root / "analysis/analysis-report.json", report)
    relink(root)


def _run(root: Path, output: Path) -> tuple[int, dict[str, Any]]:
    code = main(["--bundle-root", str(root), "--plan", str(PLAN_PATH), "--validator-commit", "c" * 40, "--output", str(output)])
    return code, json.loads(output.read_text())


class IndependentValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="a3-independent-")
        cls.synthetic_bundle = Path(cls.temporary.name) / "bundle"
        build_bundle(cls.synthetic_bundle)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_accepts_untampered_synthetic_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a3-result-") as directory:
            code, result = _run(self.synthetic_bundle, Path(directory) / "accepted.json")
        self.assertEqual(code, 0, result)
        self.assertIs(result["accepted"], True)
        self.assertEqual(result["discrepancy_codes"], [])
        self.assertEqual([item["id"] for item in result["tamper_results"]], ["T1", "T2", "T3", "T4", "T5"])
        self.assertTrue(all(item["rejected"] for item in result["tamper_results"]))

    def test_accepts_terminal_predicate_ids_outside_registry_order(self) -> None:
        _validate_terminal_predicate_ids(
            ["A3-TDEF-RECORD-NONE", "A3-POLARITY-CROSSCHECK"],
            {"A3-POLARITY-CROSSCHECK", "A3-TDEF-RECORD-NONE"},
        )

    def test_binds_published_r4_by_hash(self) -> None:
        plan_raw = PLAN_PATH.read_bytes()
        plan_sha256 = hashlib.sha256(plan_raw).hexdigest()
        plan = json.loads(plan_raw)
        campaign, sequences = _load_predicate_sequences(
            R4_PATH,
            plan_sha256,
            plan["predicate_registry"]["ids"],
        )
        self.assertEqual(hashlib.sha256(R4_PATH.read_bytes()).hexdigest(), R4_SHA256)
        self.assertEqual(campaign, ["A3-IDLE-EQUALITY", "A3-SNAPSHOT-RECONSTRUCTION", "A3-RESOURCE-BOUND"])
        self.assertEqual(
            set(sequences),
            {
                "global_map_record",
                "global_map_conversion_inline",
                "global_map_extended_base",
                "tdef_pointer_pair",
            },
        )
        with tempfile.TemporaryDirectory(prefix="a3-r2-") as directory:
            mutated = Path(directory) / R4_PATH.name
            mutated.write_bytes(R4_PATH.read_bytes() + b" ")
            with self.assertRaisesRegex(ValidationError, "predicate_revision_hash_mismatch"):
                _load_predicate_sequences(
                    mutated,
                    plan_sha256,
                    plan["predicate_registry"]["ids"],
                )

    def test_r4_c01_counts_each_union_page_once_and_enforces_record_bound(self) -> None:
        bundle = BundleLoader(self.synthetic_bundle, PLAN_PATH).load()
        derivation = recompute_derivation(bundle)
        per_replica = derivation["per_replica_qualified_pages"]
        self.assertEqual(per_replica[1], per_replica[2])
        self.assertEqual(derivation["record_candidate_enumerations"], 2)
        per_page = bundle.plan["bounds"]["max_record_candidates_per_page"]
        verify_report_bounds(bundle, derivation)
        bundle.report["record_candidates_examined"] = 4 * per_page
        with self.assertRaisesRegex(ValidationError, "record_candidate_count_mismatch"):
            verify_report_bounds(bundle, derivation)
        bundle.report["record_candidates_examined"] = 2 * per_page
        bundle.report["analysis_work_units"] = 8 * 2 * per_page + 16 * 2049 * 2 + 1
        with self.assertRaisesRegex(ValidationError, "analysis_work_bound"):
            verify_report_bounds(bundle, derivation)
        ceiling = bundle.plan["bounds"]["max_record_candidates"]
        self.assertEqual(ceiling, 32 * per_page)
        derivation["record_candidate_enumerations"] = 32
        bundle.report["record_candidates_examined"] = ceiling
        bundle.report["analysis_work_units"] = 8 * ceiling + 16 * 2049 * 32
        verify_report_bounds(bundle, derivation)
        derivation["record_candidate_enumerations"] = 33
        with self.assertRaisesRegex(ValidationError, "record_candidate_bound"):
            verify_report_bounds(bundle, derivation)

    def test_r4_s01_multiple_terminal_carries_multiplicity(self) -> None:
        bundle = BundleLoader(self.synthetic_bundle, PLAN_PATH).load()
        # Qualify a second global page whose record is a byte-identical copy of page 1 at every
        # checkpoint, so two starts survive on two pages and stage 7 fires with two survivors.
        for replica in (1, 2):
            for checkpoint_id in CHECKPOINTS:
                index = bundle.replicas[replica].indexes[checkpoint_id]["ordered_page_sha256"]
                index[3] = index[1]
        derivation = recompute_derivation(bundle)
        layer = derivation["layers"]["global_map_record"]
        self.assertEqual(layer["terminal_predicate_id"], "A3-GLOBAL-PAGE-MULTIPLE")
        self.assertEqual(layer["derivation_survivor_count"], 2)
        self.assertEqual(derivation["qualified_pages"]["global_map"], [1, 3])

    def test_t5_targets_a_nonterminal_predicate(self) -> None:
        bundle = BundleLoader(self.synthetic_bundle, PLAN_PATH).load()
        self.assertEqual(_t5_target(bundle.report), "A3-HOLDOUT-PREDICTION")
        variant, target = _tamper_t5(bundle)
        self.assertEqual(target, "A3-HOLDOUT-PREDICTION")
        self.assertNotEqual(variant.report, bundle.report)
        # When every layer fails holdout, A3-HOLDOUT-PREDICTION is the legitimate terminal and
        # T5 must flip some other nonterminal predicate instead of producing a no-op variant.
        failed = copy.deepcopy(bundle.report)
        for item in failed["predicate_results"]:
            if item["predicate_id"] == "A3-HOLDOUT-PREDICTION":
                item["status"] = "fail"
        failed["terminal_predicate_ids"].append("A3-HOLDOUT-PREDICTION")
        target = _t5_target(failed)
        self.assertNotEqual(target, "A3-HOLDOUT-PREDICTION")
        self.assertEqual(next(i["status"] for i in failed["predicate_results"] if i["predicate_id"] == target), "pass")
        variant, chosen = _tamper_t5(replace(bundle, report=failed))
        self.assertEqual(chosen, target)
        self.assertNotEqual(variant.report, failed)
        self.assertIn(target, variant.report["terminal_predicate_ids"])
        all_fail = copy.deepcopy(bundle.report)
        for item in all_fail["predicate_results"]:
            item["status"] = "fail"
        with self.assertRaisesRegex(ValidationError, "tamper_suite_not_executable"):
            _t5_target(all_fail)

    def test_idle_campaign_terminal_skips_all_layers(self) -> None:
        bundle = BundleLoader(self.synthetic_bundle, PLAN_PATH).load()
        idle_index = bundle.replicas[1].indexes["E0R"]["ordered_page_sha256"]
        idle_index[0] = idle_index[1]
        derivation = recompute_derivation(bundle)
        campaign, sequences = _load_predicate_sequences(
            R4_PATH,
            hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest(),
            bundle.plan["predicate_registry"]["ids"],
        )
        statuses = _expected_predicate_statuses(
            bundle,
            derivation,
            False,
            {"A3-IDLE-EQUALITY"},
            campaign,
            sequences,
        )
        self.assertEqual(derivation["campaign_terminal_predicate_id"], "A3-IDLE-EQUALITY")
        self.assertTrue(all(not layer["applicable"] for layer in derivation["layers"].values()))
        self.assertEqual(statuses["A3-IDLE-EQUALITY"], "fail")
        self.assertEqual(statuses["A3-SNAPSHOT-RECONSTRUCTION"], "not_applicable")
        self.assertEqual(statuses["A3-GLOBAL-PAGE-NONE"], "not_applicable")

    def test_rejects_relinked_tamper_cases(self) -> None:
        cases: list[tuple[str, Callable[[Path], None], str]] = [
            ("T1", tamper_t1, "global_record_model_mismatch"),
            ("T2", tamper_t2, "conversion_outcome_mismatch"),
            ("T3", tamper_t3, "frozen_set_recomputation_mismatch"),
            ("T4", tamper_t4, "tdef_outcome_mismatch"),
            ("T5", tamper_t5, "predicate_reporting_mismatch"),
        ]
        for name, tamper, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix=f"a3-{name.lower()}-") as directory:
                temporary = Path(directory)
                mutated = temporary / "bundle"
                shutil.copytree(self.synthetic_bundle, mutated, copy_function=os.link)
                tamper(mutated)
                code, result = _run(mutated, temporary / "result.json")
                self.assertNotEqual(code, 0)
                self.assertIs(result["accepted"], False)
                self.assertEqual(result["discrepancy_codes"], [expected])
                self.assertTrue(all(not item["rejected"] for item in result["tamper_results"]))


if __name__ == "__main__":
    unittest.main()
