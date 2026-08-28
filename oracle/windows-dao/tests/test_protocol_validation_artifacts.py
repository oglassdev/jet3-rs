import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from protocol_validation import ValidationError, canonical_json_bytes  # noqa: E402
from protocol_validation_snapshot_fixtures import SnapshotFixtureMixin  # noqa: E402
import validate_protocol_v1_2 as v1_2  # noqa: E402


class ProtocolV12ArtifactTests(SnapshotFixtureMixin, unittest.TestCase):
    """Canonical artifact publication and cross-document binding rules."""

    def test_rejected_format_outcomes_follow_shared_normalization_vectors(self):
        fixture = (
            v1_2.SCHEMA_DIR
            / "fixtures"
            / "rejected-format-normalization-vectors.tsv"
        )
        seen = 0
        for line in fixture.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                continue
            case, scenario_id, _variant, error_class = line.split("\t")
            scenario = self._find(self.inventory, scenario_id)
            self.assertEqual(scenario["operation"]["error_class"], error_class, case)
            snapshot, receipt = self._opening_failure(scenario_id, error_class)
            self.assertEqual(
                v1_2.validate_document(snapshot),
                "canonical_semantic_snapshot",
                case,
            )
            self.assertEqual(
                v1_2.validate_document(receipt), "rust_coverage_receipt", case
            )
            seen += 1
        self.assertEqual(seen, 3)

    def test_success_and_opening_failure_artifact_pairs_are_bound(self):
        success_snapshot = self._snapshot()
        success_receipt = self._success_receipt(success_snapshot)
        v1_2.validate_artifact_pair(success_snapshot, success_receipt)

        failure_snapshot, failure_receipt = self._opening_failure(
            "DAO-READ-OPEN-REJECT-JET4", "unsupported_version"
        )
        v1_2.validate_artifact_pair(failure_snapshot, failure_receipt)

    def test_artifact_pair_rejects_documents_mixed_between_valid_bundles(self):
        first_snapshot = self._snapshot()
        first_receipt = self._success_receipt(first_snapshot)
        second_snapshot = json.loads(json.dumps(first_snapshot))
        second_snapshot["producer"]["source_revision"] = "other-revision"
        second_snapshot["database_sha256"] = "ef" * 32
        second_receipt = self._success_receipt(second_snapshot)

        v1_2.validate_artifact_pair(first_snapshot, first_receipt)
        v1_2.validate_artifact_pair(second_snapshot, second_receipt)
        with self.assertRaisesRegex(
            ValidationError, "source_revision, database_sha256"
        ):
            v1_2.validate_artifact_pair(first_snapshot, second_receipt)

    def test_artifact_pair_rejects_each_cross_document_binding_mutation(self):
        snapshot = self._snapshot()
        receipt = self._success_receipt(snapshot)

        wrong_scenario = json.loads(json.dumps(receipt))
        wrong_scenario["scenario_id"] = "DAO-READ-ROWS-SINGLE"
        wrong_scenario["branches"] = sorted(
            self._find(self.inventory, "DAO-READ-ROWS-SINGLE")["required_branches"]
        )
        with self.assertRaisesRegex(ValidationError, "scenario_id"):
            v1_2.validate_artifact_pair(snapshot, wrong_scenario)

        wrong_revision = json.loads(json.dumps(receipt))
        wrong_revision["source_revision"] = "other-revision"
        with self.assertRaisesRegex(ValidationError, "source_revision"):
            v1_2.validate_artifact_pair(snapshot, wrong_revision)

        wrong_database = json.loads(json.dumps(receipt))
        wrong_database["database_sha256"] = "ef" * 32
        with self.assertRaisesRegex(ValidationError, "database_sha256"):
            v1_2.validate_artifact_pair(snapshot, wrong_database)

        dao_snapshot = json.loads(json.dumps(snapshot))
        dao_snapshot["producer"]["kind"] = "dao"
        with self.assertRaisesRegex(ValidationError, "Rust snapshot producer"):
            v1_2.validate_artifact_pair(dao_snapshot, receipt)

    def test_artifact_pair_rejects_constant_outcome_allocation_and_error_mutations(self):
        snapshot = self._snapshot()
        receipt = self._success_receipt(snapshot)

        for field, value in (
            ("protocol_version", "1.1.0"),
            ("document_type", "rust_coverage_receipt"),
        ):
            mutated = json.loads(json.dumps(snapshot))
            mutated[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                v1_2.validate_artifact_pair(mutated, receipt)

        success_without_allocation = json.loads(json.dumps(receipt))
        success_without_allocation["allocated_set_sha256"] = None
        with self.assertRaises(ValidationError):
            v1_2.validate_artifact_pair(snapshot, success_without_allocation)

        success_with_error = json.loads(json.dumps(receipt))
        success_with_error["error_class"] = "unsupported_version"
        with self.assertRaises(ValidationError):
            v1_2.validate_artifact_pair(snapshot, success_with_error)

        failure_snapshot, failure_receipt = self._opening_failure(
            "DAO-READ-OPEN-REJECT-JET4", "unsupported_version"
        )
        failure_with_allocation = json.loads(json.dumps(failure_receipt))
        failure_with_allocation["allocated_set_sha256"] = "cd" * 32
        with self.assertRaises(ValidationError):
            v1_2.validate_artifact_pair(failure_snapshot, failure_with_allocation)

        failure_without_error = json.loads(json.dumps(failure_receipt))
        failure_without_error["error_class"] = None
        with self.assertRaises(ValidationError):
            v1_2.validate_artifact_pair(failure_snapshot, failure_without_error)

        wrong_error = json.loads(json.dumps(failure_receipt))
        wrong_error["error_class"] = "encrypted_database"
        with self.assertRaisesRegex(ValidationError, "does not match"):
            v1_2.validate_artifact_pair(failure_snapshot, wrong_error)

        with self.assertRaises(ValidationError):
            v1_2.validate_artifact_pair(snapshot, failure_receipt)

    def test_artifact_pair_paths_require_canonical_bytes(self):
        snapshot = self._snapshot()
        receipt = self._success_receipt(snapshot)
        with tempfile.TemporaryDirectory() as temporary:
            snapshot_path = Path(temporary) / "snapshot.json"
            receipt_path = Path(temporary) / "coverage-receipt.json"
            snapshot_path.write_bytes(canonical_json_bytes(snapshot))
            receipt_path.write_bytes(canonical_json_bytes(receipt))
            v1_2.validate_artifact_pair_paths(snapshot_path, receipt_path)
            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("canonical validation reread its input"),
            ):
                self.assertEqual(
                    v1_2.validate_document_path(snapshot_path),
                    "canonical_semantic_snapshot",
                )

            snapshot_path.write_text(
                json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValidationError, "not normalized"):
                v1_2.validate_artifact_pair_paths(snapshot_path, receipt_path)
            snapshot_path.write_bytes(canonical_json_bytes(snapshot))
            for case, invalid_root in (("scalar", 1), ("array", []), ("null", None)):
                with self.subTest(case=case):
                    invalid_path = Path(temporary) / f"{case}.json"
                    invalid_path.write_text(
                        json.dumps(invalid_root) + "\n", encoding="utf-8"
                    )
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(v1_2.__file__),
                            "pair",
                            str(invalid_path),
                            str(receipt_path),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual((result.returncode, result.stdout), (1, ""))
                    self.assertEqual(
                        result.stderr,
                        "FAIL: $: protocol document must be an object\n",
                    )
                    self.assertNotIn("Traceback", result.stderr)
            pair_result = subprocess.run(
                [
                    sys.executable,
                    str(v1_2.__file__),
                    "pair",
                    str(snapshot_path),
                    str(receipt_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual((pair_result.returncode, pair_result.stderr), (0, ""))
            self.assertTrue(pair_result.stdout.startswith("PASS: "))

    def test_rejected_format_outcome_mutations_fail_closed(self):
        snapshot, receipt = self._opening_failure(
            "DAO-READ-OPEN-REJECT-JET4", "unsupported_version"
        )

        wrong_outcome = json.loads(json.dumps(snapshot))
        wrong_outcome["outcome"] = "success"
        with self.assertRaisesRegex(ValidationError, "allowed shape"):
            v1_2.validate_document(wrong_outcome)

        success_scenario = json.loads(json.dumps(snapshot))
        success_scenario["scenario_id"] = "DAO-READ-ROWS-SINGLE"
        with self.assertRaisesRegex(ValidationError, "expected_error scenario"):
            v1_2.validate_document(success_scenario)

        wrong_error = json.loads(json.dumps(snapshot))
        wrong_error["error_class"] = "encrypted_database"
        with self.assertRaisesRegex(ValidationError, "does not match"):
            v1_2.validate_document(wrong_error)

        missing_branch = json.loads(json.dumps(receipt))
        missing_branch["branches"].remove("open.rejected_format")
        with self.assertRaisesRegex(ValidationError, "allowed shape"):
            v1_2.validate_document(missing_branch)

        success_receipt = json.loads(json.dumps(receipt))
        success_receipt["scenario_id"] = "DAO-READ-ROWS-SINGLE"
        with self.assertRaisesRegex(ValidationError, "expected_error scenario"):
            v1_2.validate_document(success_receipt)

if __name__ == "__main__":
    unittest.main()
