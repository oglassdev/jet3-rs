#!/usr/bin/env python3
"""End-to-end synthetic coverage for every checked M4 validator layer."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

from m4_bundle import (  # noqa: E402
    build_analysis_from_stage,
    validate_bundle,
    validate_worker_result,
)
import m4_bundle as m4_bundle_module  # noqa: E402
import m4_snapshot as m4_snapshot_module  # noqa: E402
from m4_analysis import canonical_analysis_bytes  # noqa: E402
from m4_campaign import validate_campaign_bindings_and_chronology  # noqa: E402
from m4_phase import (  # noqa: E402
    validate_phase_documents,
    validate_sample_record,
)
from m4_records import (  # noqa: E402
    ValidationError,
    load_checked_plan,
)
from m4_test_bundle import build_bundle, digest, write_json  # noqa: E402


class M4BundleIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.bundle = Path(self.temporary.name) / "bundle"
        self.bundle.mkdir()
        self.plan, self.records = build_bundle(self.bundle)
        self.plan, self.plan_hash = load_checked_plan(
            self.bundle / "plan/checked-plan.json"
        )
        self.samples = {row["sample_id"]: row for row in self.plan["samples"]}
        self.conditions = {
            row["condition_id"]: row for row in self.plan["conditions"]
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_507_file_bundle_reaches_every_validator_layer(self) -> None:
        record = self.records[0]
        sample = self.samples[record["sample_id"]]
        condition = self.conditions[record["condition_id"]]
        validate_phase_documents(
            self.bundle,
            record,
            sample,
            condition,
            "creator",
            self.plan,
            self.plan_hash,
        )
        validate_sample_record(
            self.bundle,
            record,
            sample,
            condition,
            self.plan,
            self.plan_hash,
        )
        result_path = self.bundle / record["phases"]["creator"]["artifacts"][
            "worker_result"
        ]["path"]
        validate_worker_result(self.bundle, result_path)
        recomputed = build_analysis_from_stage(self.bundle)
        self.assertEqual(recomputed["execution_status"], "pass")
        validated = validate_bundle(self.bundle)
        self.assertEqual(len(validated["records"]), 36)
        self.assertEqual(len(validated["prefixes"]), 72)

    def test_snapshot_after_close_and_worker_timeout_are_rejected(self) -> None:
        record = copy.deepcopy(self.records[0])
        sample = self.samples[record["sample_id"]]
        condition = self.conditions[record["condition_id"]]
        snapshot_ref = record["phases"]["creator"]["artifacts"]["snapshot"]
        snapshot_path = self.bundle / snapshot_ref["path"]
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot["captured_at_utc"] = "2026-07-25T00:01:09Z"
        write_json(snapshot_path, snapshot)
        snapshot_ref["sha256"] = digest(snapshot_path)
        result_ref = record["phases"]["creator"]["artifacts"]["worker_result"]
        result_path = self.bundle / result_ref["path"]
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["snapshot"]["sha256"] = snapshot_ref["sha256"]
        write_json(result_path, result)
        result_ref["sha256"] = digest(result_path)
        with self.assertRaisesRegex(ValidationError, "while-open observation"):
            validate_phase_documents(
                self.bundle,
                record,
                sample,
                condition,
                "creator",
                self.plan,
                self.plan_hash,
            )
        snapshot["captured_at_utc"] = "2026-07-25T00:01:07Z"
        write_json(snapshot_path, snapshot)
        snapshot_ref["sha256"] = digest(snapshot_path)
        result["snapshot"]["sha256"] = snapshot_ref["sha256"]
        write_json(result_path, result)
        result_ref["sha256"] = digest(result_path)
        with self.assertRaisesRegex(ValidationError, "while-open observation"):
            validate_phase_documents(
                self.bundle,
                record,
                sample,
                condition,
                "creator",
                self.plan,
                self.plan_hash,
            )
        snapshot["captured_at_utc"] = "2026-07-25T00:01:06Z"
        write_json(snapshot_path, snapshot)
        snapshot_ref["sha256"] = digest(snapshot_path)
        result["snapshot"]["sha256"] = snapshot_ref["sha256"]
        result["finished_at_utc"] = "2026-07-25T00:04:00Z"
        write_json(result_path, result)
        result_ref["sha256"] = digest(result_path)
        with self.assertRaisesRegex(ValidationError, "elapsed time"):
            validate_phase_documents(
                self.bundle,
                record,
                sample,
                condition,
                "creator",
                self.plan,
                self.plan_hash,
            )

    def test_campaign_root_and_launch_chronology_drift_are_rejected(self) -> None:
        record = self.records[1]
        invocation_ref = record["phases"]["creator"]["artifacts"]["invocation"]
        invocation_path = self.bundle / invocation_ref["path"]
        invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
        original = copy.deepcopy(invocation)
        invocation["repository_root"] = "/synthetic/other-repository"
        write_json(invocation_path, invocation)
        with self.assertRaisesRegex(ValidationError, "runtime-root binding"):
            validate_campaign_bindings_and_chronology(
                self.bundle, self.plan, self.records
            )
        write_json(invocation_path, original)
        original["created_at_utc"] = "2026-07-25T00:00:01Z"
        write_json(invocation_path, original)
        with self.assertRaisesRegex(ValidationError, "previous launch ordinal"):
            validate_campaign_bindings_and_chronology(
                self.bundle, self.plan, self.records
            )

    def test_role_ceiling_and_noncanonical_analysis_bytes_are_rejected(self) -> None:
        manifest_path = self.bundle / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        prefix_entry = next(
            entry for entry in manifest["files"] if entry["role"] == "prefix"
        )
        prefix_entry["size_bytes"] = 2049
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValidationError, "byte ceiling"):
            validate_bundle(self.bundle)
        manifest = json.loads(
            (self.bundle / "bundle-manifest.json").read_text(encoding="utf-8")
        )
        prefix_entry = next(
            entry for entry in manifest["files"] if entry["role"] == "prefix"
        )
        prefix_entry["size_bytes"] = 2048
        analysis_entry = next(
            entry for entry in manifest["files"] if entry["role"] == "analysis_report"
        )
        analysis_path = self.bundle / analysis_entry["path"]
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        analysis_path.write_text(json.dumps(analysis, indent=2) + "\n", encoding="utf-8")
        analysis_entry["size_bytes"] = analysis_path.stat().st_size
        analysis_entry["sha256"] = digest(analysis_path)
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValidationError, "canonical retained bytes"):
            validate_bundle(self.bundle)

    def test_canonical_but_semantically_wrong_analysis_is_rejected(self) -> None:
        manifest_path = self.bundle / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        analysis_entry = next(
            entry
            for entry in manifest["files"]
            if entry["role"] == "analysis_report"
        )
        analysis_path = self.bundle / analysis_entry["path"]
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        analysis["comparisons"][0]["differing_offsets"] = [0]
        analysis_path.write_bytes(canonical_analysis_bytes(analysis))
        analysis_entry["size_bytes"] = analysis_path.stat().st_size
        analysis_entry["sha256"] = digest(analysis_path)
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValidationError, "canonical retained bytes"):
            validate_bundle(self.bundle)

    def test_snapshot_recheck_rejects_mixed_epoch_mutation(self) -> None:
        target = self.bundle / self.records[0]["phases"]["creator"][
            "artifacts"
        ]["invocation"]["path"]
        original = m4_bundle_module.build_full_analysis

        def mutate_after_snapshot(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            target.write_bytes(target.read_bytes() + b" ")
            return result

        with mock.patch.object(
            m4_bundle_module,
            "build_full_analysis",
            side_effect=mutate_after_snapshot,
        ):
            with self.assertRaisesRegex(
                ValidationError, "tree or file identities changed"
            ) as raised:
                validate_bundle(self.bundle)
        self.assertIn(
            target.relative_to(self.bundle).as_posix(),
            str(raised.exception),
        )
        self.assertIn("metadata changed", str(raised.exception))

    def test_complete_validation_reads_every_payload_exactly_once(self) -> None:
        captured: list[tuple[str, str]] = []
        original = m4_snapshot_module._read_captured

        def observe_read(*args: object, **kwargs: object) -> object:
            locator = str(args[1])
            role = str(kwargs["role"])
            captured.append((locator, role))
            return original(*args, **kwargs)

        with mock.patch.object(
            m4_snapshot_module, "_read_captured", side_effect=observe_read
        ), mock.patch.object(
            m4_snapshot_module.BundleSnapshot,
            "recheck",
            autospec=True,
        ) as recheck:
            # Wrapping all 508 captures perturbs synthetic-fixture metadata
            # timing on Windows. This test owns read cardinality; neighboring
            # tests execute both successful and rejecting final rechecks.
            validate_bundle(self.bundle)
        recheck.assert_called_once()
        self.assertEqual(len(captured), 508)
        self.assertEqual(len({locator for locator, _ in captured}), 508)
        self.assertEqual(
            sum(role == "database" for _, role in captured),
            72,
        )


class InventoryDifferenceDiagnosticTests(unittest.TestCase):
    @staticmethod
    def _entry(size: int) -> object:
        stamp = m4_snapshot_module.FileStamp(
            mode=0o100644,
            size=size,
            modified_ns=1,
            attributes=0,
            changed_ns=None,
            device=None,
            inode=None,
            links=None,
        )
        return m4_snapshot_module.TreeEntry(
            kind="file", stamp=stamp, identity=None
        )

    def test_control_characters_in_locator_are_escaped(self) -> None:
        locator = "dir/\nname\x1b[31m\ttrick"
        difference = m4_snapshot_module._first_inventory_difference(
            {locator: self._entry(1)}, {}
        )
        self.assertNotIn("\n", difference)
        self.assertNotIn("\x1b", difference)
        self.assertNotIn("\t", difference)
        self.assertIn("\\n", difference)
        self.assertIn("\\x1b", difference)
        self.assertIn("\\t", difference)
        self.assertIn("entry removed", difference)

    def test_locator_bound_keeps_exact_limit_and_truncates_one_above(
        self,
    ) -> None:
        limit = m4_snapshot_module._DIAGNOSTIC_LOCATOR_CHARACTERS
        exact = "a" * limit
        difference = m4_snapshot_module._first_inventory_difference(
            {}, {exact: self._entry(1)}
        )
        self.assertIn(exact, difference)
        self.assertNotIn("...", difference)
        over = "a" * (limit + 1)
        difference = m4_snapshot_module._first_inventory_difference(
            {}, {over: self._entry(1)}
        )
        self.assertIn("a" * (limit - 3) + "...", difference)
        self.assertNotIn(over, difference)


if __name__ == "__main__":
    unittest.main()
