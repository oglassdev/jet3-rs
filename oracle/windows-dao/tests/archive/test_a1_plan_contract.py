"""Focused contracts for the frozen DAO A1 preregistration."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "archive"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a1_spec import (  # noqa: E402
    BASE_FORMULAS,
    CHECKED_PLAN,
    CHECKPOINT_IDS,
    LADDER,
    PLAN_SHA256,
    POINTER_LAYOUTS,
    ROLE_BINDINGS,
    SCHEMAS,
    compile_checked_plan,
    expected_extant_roles,
    expected_reread_sha256,
    load_checked_plan,
    load_bounded_json,
    validate_replica_observation,
)
from protocol_validation import ValidationError, sha256  # noqa: E402

REVISION_PLAN = CHECKED_PLAN.with_name("a1-allocation-maps-r2.plan.json")
REVISION_PLAN_SHA256 = "6967e72c0ea6c6aa68f102d76c48764a6300caebb4b6f7bbb2e0b931822b5b0c"


class A1PlanContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(CHECKED_PLAN.read_text(encoding="utf-8"))

    def test_exact_plan_hash_and_corrected_checkpoint_arithmetic_are_frozen(self) -> None:
        checked = load_checked_plan()
        self.assertEqual(sha256(CHECKED_PLAN), PLAN_SHA256)
        self.assertEqual(len(LADDER), 29)
        self.assertEqual(len(CHECKPOINT_IDS), 71)
        self.assertEqual(len(checked.checkpoint_ids), 71)
        self.assertEqual(checked.document["bounds"]["max_checkpoints_per_replica"], 72)
        self.assertFalse(checked.document["checkpoint_design"]["adaptive_checkpoints_allowed"])

    def test_r2_amendment_hash_is_frozen(self) -> None:
        self.assertEqual(sha256(REVISION_PLAN), REVISION_PLAN_SHA256)

    def test_roles_rotate_and_holdout_cannot_refit(self) -> None:
        checked = load_checked_plan()
        self.assertEqual(len({tuple(binding.values()) for binding in ROLE_BINDINGS}), 3)
        self.assertTrue(all(set(binding.values()) == {"A1TAB_A", "A1TAB_B", "A1TAB_C", "A1TAB_D"} for binding in ROLE_BINDINGS))
        self.assertEqual(checked.document["replicas"]["derivation"], [1, 2])
        self.assertEqual(checked.document["replicas"]["holdout"], 3)
        self.assertIn("without refit", checked.document["replicas"]["holdout_rule"])

    def test_provenance_and_python_host_are_exactly_bound(self) -> None:
        checked = load_checked_plan()
        self.assertEqual(checked.document["preregistration"]["provenance_entry"], "EXP-0037")
        self.assertEqual(checked.document["preregistration"]["recorded_utc_date"], "2026-08-19")
        self.assertEqual(checked.document["environment_binding"]["python_version"], "3.13.x")

        changed = copy.deepcopy(self.document)
        changed["environment_binding"]["python_version"] = "3.12.x"
        with self.assertRaisesRegex(ValidationError, "python_version|environment_binding"):
            compile_checked_plan(changed)

        environment = {
            "protocol_version": "1.0.0",
            "document_type": "dao_a1_environment",
            "experiment_id": "DAO-A1-ALLOCATION-MAPS-001",
            "plan_sha256": PLAN_SHA256,
            "producer_commit": "0" * 40,
            "repository_url": "https://github.com/oglassdev/jet3-rs.git",
            "run_id": "test",
            "status": "ready",
            "host": {
                "windows_version": "test",
                "process_architecture": "x86",
                "powershell_version": "5.1.0",
                "python_version": "3.13.7",
            },
            "provider": {
                "prog_id": "DAO.DBEngine.36",
                "clsid": "{00000100-0000-0010-8000-00AA006D2EA4}",
                "provider_version": "3.6",
                "server_path": "C:\\dao360.dll",
                "server_file_version": "3.60.0",
                "server_sha256": "0" * 64,
            },
        }
        SCHEMAS.validate(environment)
        environment["host"]["python_version"] = "3.12.10"
        with self.assertRaisesRegex(ValidationError, "python_version"):
            SCHEMAS.validate(environment)

    def test_candidate_space_and_forbidden_claims_are_closed(self) -> None:
        checked = load_checked_plan()
        self.assertEqual(checked.document["hypotheses"]["tdef_pointer_layouts"], list(POINTER_LAYOUTS))
        self.assertEqual(checked.document["hypotheses"]["extended_base_candidates"], list(BASE_FORMULAS))
        claims = checked.document["claims"]
        self.assertTrue(claims["descriptive_provider_observation_only"])
        self.assertTrue(all(not value for key, value in claims.items() if key != "descriptive_provider_observation_only"))

    def test_unknown_or_changed_plan_fields_fail_closed(self) -> None:
        changed = copy.deepcopy(self.document)
        changed["tables"]["row_algorithm"]["adaptive_batches"] = True
        with self.assertRaisesRegex(ValidationError, "row_algorithm"):
            compile_checked_plan(changed)
        changed = copy.deepcopy(self.document)
        changed["checkpoint_design"]["checkpoint_ids"].pop()
        with self.assertRaisesRegex(ValidationError, "count|checkpoint_ids"):
            compile_checked_plan(changed)

    def test_plan_bytes_are_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "plan.json"
            changed.write_bytes(CHECKED_PLAN.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValidationError, "bytes differ"):
                load_checked_plan(changed)

    def test_duplicate_json_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"document_type":"x","document_type":"y"}', encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "duplicate object key"):
                load_bounded_json(path)

    def test_schemas_lint_with_only_enforced_keywords(self) -> None:
        SCHEMAS.lint()

    def test_observation_requires_exact_checkpoint_order_and_index_paths(self) -> None:
        digest = "0" * 64
        checkpoints = [
            {
                "checkpoint_id": checkpoint_id,
                "ordinal": ordinal,
                "actual_file_pages": 1,
                "actual_size_bytes": 2048,
                "target_baseline_pages": None,
                "target_threshold_pages": None,
                "target_overshoot_pages": None,
                "inserted_rows_total": 0,
                "table_row_counts": {role: 0 for role in "DLPH"},
                "dao_reread": [
                    {"role": role, "row_count": 0, "rolling_sha256": expected_reread_sha256(role, 0)}
                    for role in expected_extant_roles(checkpoint_id)
                ],
                "quiescent": True,
                "post_close_companion": {"present_after_close": False, "observed_size_bytes": 0, "retained_for_physical_analysis": False},
                "page_index": {"path": f"page-indexes/replica-01/{ordinal:02d}-{checkpoint_id}.json", "sha256": digest, "size_bytes": 1},
            }
            for ordinal, checkpoint_id in enumerate(CHECKPOINT_IDS)
        ]
        observation = {
            "protocol_version": "1.0.0", "document_type": "dao_a1_replica_observation",
            "experiment_id": "DAO-A1-ALLOCATION-MAPS-001", "plan_sha256": PLAN_SHA256,
            "producer_commit": "0" * 40, "repository_url": "https://github.com/oglassdev/jet3-rs.git",
            "run_id": "test", "environment_sha256": digest, "provider_sha256": digest,
            "replica": 1, "role_binding": ROLE_BINDINGS[0],
            "logical_checkpoint_read_bytes": len(CHECKPOINT_IDS) * 2048,
            "inserted_rows_total": 0, "changed_hash_entries": 1,
            "checkpoints": checkpoints,
        }
        for checkpoint in checkpoints:
            checkpoint_id = checkpoint["checkpoint_id"]
            if checkpoint_id.startswith(("D_GROW_", "D_REGROW_", "L_REL_", "H_REL_")):
                target = int(checkpoint_id.rsplit("_", 1)[1])
                checkpoint["target_baseline_pages"] = 1
                checkpoint["target_threshold_pages"] = 1 + target
                checkpoint["actual_file_pages"] = 1 + target
                checkpoint["actual_size_bytes"] = (1 + target) * 2048
                checkpoint["target_overshoot_pages"] = 0
            elif checkpoint_id.startswith("P_ABS_"):
                target = int(checkpoint_id.rsplit("_", 1)[1])
                checkpoint["target_threshold_pages"] = target
                checkpoint["actual_file_pages"] = target
                checkpoint["actual_size_bytes"] = target * 2048
                checkpoint["target_overshoot_pages"] = 0
        observation["logical_checkpoint_read_bytes"] = sum(item["actual_size_bytes"] for item in checkpoints)
        validate_replica_observation(observation, load_checked_plan())
        changed = copy.deepcopy(observation)
        changed["checkpoints"][0], changed["checkpoints"][1] = changed["checkpoints"][1], changed["checkpoints"][0]
        with self.assertRaisesRegex(ValidationError, "checkpoint order|ordinal"):
            validate_replica_observation(changed, load_checked_plan())


if __name__ == "__main__":
    unittest.main()
