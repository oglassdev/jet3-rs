#!/usr/bin/env python3
"""Corruption tests for strict DAO A1 contract and bundle validation."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import a1_bundle as bundle_module  # noqa: E402
from a1_bundle import validate_bundle  # noqa: E402
from a1_contract import (  # noqa: E402
    CHECKED_PLAN,
    SCHEMA_SET,
    ValidationError,
    parse_json_bytes,
    validate_semantics,
)

COMMIT = "1" * 40
PROVIDER = "2" * 64
RUN_ID = "20260819T230000Z-a1-test"
EMPTY_HASH = hashlib.sha256(b"").hexdigest()
PAGE = bytes(2048)
PAGE_HASH = hashlib.sha256(PAGE).hexdigest()


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    )


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            value.update(chunk)
    return value.hexdigest()


def reference(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": digest(path),
        "size_bytes": path.stat().st_size,
    }


def _environment(plan_hash: str) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a1_environment",
        "experiment_id": "DAO-A1-ALLOCATION-MAPS-001",
        "plan_sha256": plan_hash,
        "producer_commit": COMMIT,
        "repository_url": "https://github.com/oglassdev/jet3-rs.git",
        "run_id": RUN_ID,
        "status": "ready",
        "host": {
            "windows_version": "synthetic-test-host",
            "process_architecture": "x86",
            "powershell_version": "5.1.19041.1",
            "python_version": "3.13.15",
        },
        "provider": {
            "prog_id": "DAO.DBEngine.36",
            "clsid": "{00000000-0000-0000-0000-000000000000}",
            "provider_version": "3.6",
            "server_path": "C:\\Program Files (x86)\\Common Files\\dao360.dll",
            "server_file_version": "3.60.0000.0",
            "server_sha256": PROVIDER,
        },
    }


def _page_index(
    plan_hash: str,
    environment_hash: str,
    replica: int,
    checkpoint: str,
    ordinal: int,
    predecessor: str | None,
) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a1_page_index",
        "experiment_id": "DAO-A1-ALLOCATION-MAPS-001",
        "plan_sha256": plan_hash,
        "producer_commit": COMMIT,
        "run_id": RUN_ID,
        "environment_sha256": environment_hash,
        "provider_sha256": PROVIDER,
        "replica": replica,
        "checkpoint_id": checkpoint,
        "ordinal": ordinal,
        "predecessor_checkpoint_id": predecessor,
        "page_count": 1,
        "file_size_bytes": 2048,
        "database_sha256": PAGE_HASH,
        "ordered_page_sha256": [PAGE_HASH],
        "changed_page_indices": [0] if ordinal == 0 else [],
    }


def _checkpoint(checkpoint: str, ordinal: int, page_ref: dict[str, Any]) -> dict[str, Any]:
    counts = {role: 0 for role in ("D", "L", "P", "H")}
    if checkpoint in ("E0", "E0R", "D_DROP"):
        extant_roles: tuple[str, ...] = ()
    elif checkpoint.startswith("D_"):
        extant_roles = ("D",)
    elif checkpoint.startswith("L_"):
        extant_roles = ("D", "L")
    elif checkpoint.startswith("P_"):
        extant_roles = ("D", "L", "P")
    else:
        extant_roles = ("D", "L", "P", "H")
    return {
        "checkpoint_id": checkpoint,
        "ordinal": ordinal,
        "actual_file_pages": 1,
        "actual_size_bytes": 2048,
        "target_baseline_pages": None,
        "target_threshold_pages": None,
        "target_overshoot_pages": None,
        "inserted_rows_total": 0,
        "table_row_counts": counts,
        "dao_reread": [
            {"role": role, "row_count": 0, "rolling_sha256": EMPTY_HASH}
            for role in extant_roles
        ],
        "quiescent": True,
        "post_close_companion": {
            "present_after_close": False,
            "observed_size_bytes": 0,
            "retained_for_physical_analysis": False,
        },
        "page_index": page_ref,
    }


def _analysis(plan: dict[str, Any], plan_hash: str) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a1_analysis_report",
        "experiment_id": plan["experiment_id"],
        "plan_sha256": plan_hash,
        "run_id": RUN_ID,
        "producer_commit": COMMIT,
        "derivation_replicas": plan["replicas"]["derivation"],
        "holdout_replica": plan["replicas"]["holdout"],
        "input_checkpoint_count": 213,
        "candidate_models_examined": 0,
        "derivation_survivor_count": 0,
        "analysis_work_units": 0,
        "holdout_evaluated": False,
        "scientific_outcome": "no_scientific_outcome",
        "no_outcome_reasons": ["incomplete_transition_evidence"],
        "surviving_model": None,
        "claims": plan["claims"],
    }


def refresh_manifest(root: Path, roles: dict[str, str]) -> None:
    files = []
    for locator, role in sorted(roles.items()):
        path = root / locator
        media = {
            "page_blob": "application/octet-stream",
            "acquisition_log": "text/plain",
        }.get(role, "application/json")
        files.append(
            {
                "path": locator,
                "role": role,
                "sha256": digest(path),
                "size_bytes": path.stat().st_size,
                "media_type": media,
            }
        )
    environment_hash = digest(root / "environment/environment.json")
    manifest = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a1_bundle_manifest",
        "experiment_id": "DAO-A1-ALLOCATION-MAPS-001",
        "run_id": RUN_ID,
        "producer_commit": COMMIT,
        "repository_url": "https://github.com/oglassdev/jet3-rs.git",
        "created_utc": "2026-08-19T23:59:00Z",
        "plan_sha256": digest(root / "plan/a1-allocation-maps.plan.json"),
        "environment_sha256": environment_hash,
        "provider_sha256": PROVIDER,
        "replica_count": 3,
        "checkpoint_count": 213,
        "page_blob_count": sum(role == "page_blob" for role in roles.values()),
        "bundle_size_bytes_excluding_manifest": sum(row["size_bytes"] for row in files),
        "inventory_closed": True,
        "hashes_verified": True,
        "paths_closed": True,
        "execution_status": "pass",
        "files": files,
    }
    write_json(root / "bundle-manifest.json", manifest)


def build_bundle(root: Path, checked_plan: Path) -> dict[str, str]:
    plan = json.loads(checked_plan.read_text(encoding="utf-8"))
    plan_path = root / "plan/a1-allocation-maps.plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_bytes(checked_plan.read_bytes())
    plan_hash = digest(plan_path)
    environment_path = root / "environment/environment.json"
    write_json(environment_path, _environment(plan_hash))
    environment_hash = digest(environment_path)
    page_path = root / f"page-store/{PAGE_HASH}.page"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_bytes(PAGE)
    roles = {
        "plan/a1-allocation-maps.plan.json": "plan",
        "environment/environment.json": "environment",
        f"page-store/{PAGE_HASH}.page": "page_blob",
    }
    role_bindings = {row["replica"]: row for row in plan["tables"]["role_bindings"]}
    checkpoint_ids = plan["checkpoint_design"]["checkpoint_ids"]
    for replica in range(1, 4):
        checkpoints = []
        for ordinal, checkpoint_id in enumerate(checkpoint_ids):
            locator = f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint_id}.json"
            index_path = root / locator
            write_json(
                index_path,
                _page_index(
                    plan_hash,
                    environment_hash,
                    replica,
                    checkpoint_id,
                    ordinal,
                    None if ordinal == 0 else checkpoint_ids[ordinal - 1],
                ),
            )
            roles[locator] = "page_index"
            checkpoints.append(_checkpoint(checkpoint_id, ordinal, reference(index_path, root)))
        binding = role_bindings[replica]
        observation = {
            "protocol_version": "1.0.0",
            "document_type": "dao_a1_replica_observation",
            "experiment_id": plan["experiment_id"],
            "plan_sha256": plan_hash,
            "producer_commit": COMMIT,
            "repository_url": plan["repository_binding"]["canonical_https_url"],
            "run_id": RUN_ID,
            "environment_sha256": environment_hash,
            "provider_sha256": PROVIDER,
            "replica": replica,
            "role_binding": {role: binding[role] for role in ("D", "L", "P", "H")},
            "logical_checkpoint_read_bytes": 71 * 2048,
            "inserted_rows_total": 0,
            "changed_hash_entries": 1,
            "checkpoints": checkpoints,
        }
        locator = f"observations/replica-{replica:02d}.json"
        write_json(root / locator, observation)
        roles[locator] = "replica_observation"
    analysis_path = root / "analysis/analysis-report.json"
    write_json(analysis_path, _analysis(plan, plan_hash))
    roles["analysis/analysis-report.json"] = "analysis_report"
    refresh_manifest(root, roles)
    return roles


class A1BundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name).resolve()
        self.root = base / "bundle"
        self.root.mkdir()
        self.checked_plan = base / "checked-plan.json"
        plan = json.loads(CHECKED_PLAN.read_text(encoding="utf-8"))
        plan["checkpoint_design"]["checkpoint_ids"] = [
            "E0",
            "E0R",
            "D_DROP",
            *[f"D_IDLE_{index:03d}" for index in range(68)],
        ]
        write_json(self.checked_plan, plan)
        self.original_checked_plan = bundle_module.CHECKED_PLAN
        self.original_plan_sha256 = bundle_module.PLAN_SHA256
        bundle_module.CHECKED_PLAN = self.checked_plan
        bundle_module.PLAN_SHA256 = digest(self.checked_plan)
        self.roles = build_bundle(self.root, self.checked_plan)

    def tearDown(self) -> None:
        bundle_module.CHECKED_PLAN = self.original_checked_plan
        bundle_module.PLAN_SHA256 = self.original_plan_sha256
        self.temporary.cleanup()

    def test_complete_bundle_passes_without_making_an_outcome_claim(self) -> None:
        result = validate_bundle(self.root)
        self.assertEqual(result["analysis"]["scientific_outcome"], "no_scientific_outcome")

    def test_artifacts_are_read_in_derivation_freeze_holdout_report_order(self) -> None:
        reads: list[str] = []
        original = bundle_module._read_artifact

        def tracked(*args: Any, **kwargs: Any) -> Any:
            reads.append(args[1])
            return original(*args, **kwargs)

        with mock.patch.object(bundle_module, "_read_artifact", side_effect=tracked):
            validate_bundle(self.root)

        positions = {locator: index for index, locator in enumerate(reads)}
        self.assertEqual(reads[0], "bundle-manifest.json")
        self.assertLess(
            positions["plan/a1-allocation-maps.plan.json"],
            positions["environment/environment.json"],
        )
        first_holdout = positions["observations/replica-03.json"]
        derivation_reads = [
            position
            for locator, position in positions.items()
            if locator in ("observations/replica-01.json", "observations/replica-02.json")
            or locator.startswith("page-indexes/replica-01/")
            or locator.startswith("page-indexes/replica-02/")
        ]
        self.assertTrue(derivation_reads)
        self.assertLess(max(derivation_reads), first_holdout)
        holdout_reads = [
            position
            for locator, position in positions.items()
            if locator == "observations/replica-03.json"
            or locator.startswith("page-indexes/replica-03/")
        ]
        self.assertLess(
            max(holdout_reads),
            positions["analysis/analysis-report.json"],
        )

    def test_missing_and_extra_files_are_rejected(self) -> None:
        (self.root / "analysis/analysis-report.json").unlink()
        with self.assertRaisesRegex(ValidationError, "missing"):
            validate_bundle(self.root)
        build_bundle(self.root, self.checked_plan)
        (self.root / "unexpected.bin").write_bytes(b"x")
        with self.assertRaisesRegex(ValidationError, "extra"):
            validate_bundle(self.root)

    def test_hash_and_size_corruption_are_rejected(self) -> None:
        page = self.root / f"page-store/{PAGE_HASH}.page"
        page.write_bytes(b"x" * 2048)
        with self.assertRaisesRegex(ValidationError, "sha256"):
            validate_bundle(self.root)
        build_bundle(self.root, self.checked_plan)
        page.write_bytes(PAGE + b"x")
        with self.assertRaisesRegex(ValidationError, "size|exceeds"):
            validate_bundle(self.root)

    def test_symlink_and_hardlink_are_rejected(self) -> None:
        link = self.root / "alias.json"
        try:
            link.symlink_to(self.root / "analysis/analysis-report.json")
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(ValidationError, "links"):
            validate_bundle(self.root)
        link.unlink()
        os.link(self.root / "analysis/analysis-report.json", link)
        with self.assertRaisesRegex(ValidationError, "hard-linked"):
            validate_bundle(self.root)

    def test_commit_and_checkpoint_identity_corruption_are_rejected(self) -> None:
        path = self.root / "observations/replica-01.json"
        observation = json.loads(path.read_text(encoding="utf-8"))
        observation["producer_commit"] = "3" * 40
        write_json(path, observation)
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "producer commit"):
            validate_bundle(self.root)
        build_bundle(self.root, self.checked_plan)
        observation = json.loads(path.read_text(encoding="utf-8"))
        observation["checkpoints"][1]["checkpoint_id"] = "E0"
        write_json(path, observation)
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "checkpoint order|page_index.path"):
            validate_bundle(self.root)

    def test_reconstruction_and_changed_entry_corruption_are_rejected(self) -> None:
        index_path = self.root / "page-indexes/replica-01/00-E0.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["database_sha256"] = "4" * 64
        write_json(index_path, index)
        observation_path = self.root / "observations/replica-01.json"
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        observation["checkpoints"][0]["page_index"] = reference(index_path, self.root)
        write_json(observation_path, observation)
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "reconstructed database"):
            validate_bundle(self.root)

    def test_unreferenced_page_blob_is_rejected(self) -> None:
        payload = b"y" * 2048
        value = hashlib.sha256(payload).hexdigest()
        path = self.root / f"page-store/{value}.page"
        path.write_bytes(payload)
        self.roles[f"page-store/{value}.page"] = "page_blob"
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "page-store closure"):
            validate_bundle(self.root)

    def test_unproduced_acquisition_log_role_is_rejected(self) -> None:
        path = self.root / "logs/acquisition.txt"
        path.parent.mkdir()
        path.write_text("synthetic log\n", encoding="utf-8")
        self.roles["logs/acquisition.txt"] = "acquisition_log"
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "not produced"):
            validate_bundle(self.root)

    def test_traversal_and_case_collisions_are_rejected(self) -> None:
        manifest_path = self.root / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["path"] = "../escape.json"
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValidationError, "unsafe|traversal|required pattern"):
            validate_bundle(self.root)
        refresh_manifest(self.root, self.roles)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        collision = dict(manifest["files"][0])
        collision["path"] = collision["path"].upper()
        manifest["files"].append(collision)
        manifest["bundle_size_bytes_excluding_manifest"] += collision["size_bytes"]
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValidationError, "case-collid"):
            validate_bundle(self.root)

    def test_environment_provider_binding_is_rejected(self) -> None:
        environment_path = self.root / "environment/environment.json"
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
        environment["provider"]["server_sha256"] = "9" * 64
        write_json(environment_path, environment)
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "provider sha256"):
            validate_bundle(self.root)

    def test_environment_requires_python_3_13(self) -> None:
        environment_path = self.root / "environment/environment.json"
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
        environment["host"]["python_version"] = "3.12.9"
        write_json(environment_path, environment)
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "required pattern"):
            validate_bundle(self.root)

    def test_reread_digest_and_target_semantics_are_rejected(self) -> None:
        observation_path = self.root / "observations/replica-01.json"
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        observation["checkpoints"][3]["dao_reread"][0]["rolling_sha256"] = "8" * 64
        write_json(observation_path, observation)
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "rolling sha256"):
            validate_bundle(self.root)
        build_bundle(self.root, self.checked_plan)
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        observation["checkpoints"][3]["target_threshold_pages"] = 1
        observation["checkpoints"][3]["target_overshoot_pages"] = 0
        write_json(observation_path, observation)
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "non-target checkpoint"):
            validate_bundle(self.root)

    def test_decisive_report_is_rejected_without_independent_recomputation(self) -> None:
        analysis_path = self.root / "analysis/analysis-report.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        analysis.update(
            {
                "candidate_models_examined": 1,
                "derivation_survivor_count": 1,
                "holdout_evaluated": True,
                "scientific_outcome": "one_joint_model_predicts_holdout",
                "no_outcome_reasons": [],
                "surviving_model": {
                    "metadata_page": 1,
                    "record_start": 0,
                    "record_end": 100,
                    "pointer_layout": "u24le_page_then_u8_slot",
                    "used_pointer_offset": 0,
                    "free_pointer_offset": 4,
                    "inline_boundary": 5,
                    "low_type1_slot": 0,
                    "high_type1_slot": 1,
                    "low_reference_page": 2,
                    "high_reference_page": 3,
                    "extended_base_formula": "slot_relative_expected_0_16352",
                },
            }
        )
        write_json(analysis_path, analysis)
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "independent recomputing"):
            validate_bundle(self.root)

    def test_plan_substitution_is_rejected_even_when_rebound(self) -> None:
        plan_path = self.root / "plan/a1-allocation-maps.plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["question"] += " changed"
        write_json(plan_path, plan)
        refresh_manifest(self.root, self.roles)
        with self.assertRaisesRegex(ValidationError, "checked plan"):
            validate_bundle(self.root)


class A1StrictJsonTests(unittest.TestCase):
    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "duplicate"):
            parse_json_bytes(b'{"a":1,"a":2}', "duplicate")
        with self.assertRaisesRegex(ValidationError, "non-finite"):
            parse_json_bytes(b'{"a":NaN}', "nonfinite")

    def test_schema_and_semantic_validation_are_separate(self) -> None:
        plan = json.loads(CHECKED_PLAN.read_text(encoding="utf-8"))
        plan["checkpoint_design"]["count"] = 70
        SCHEMA_SET.validate_schema(plan, "dao_a1_allocation_maps_plan")
        with self.assertRaisesRegex(ValidationError, "checkpoint_design.count"):
            validate_semantics(plan)


if __name__ == "__main__":
    unittest.main()
