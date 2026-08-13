"""Focused immutable-plan, schema, analysis, and bundle tests for M5R7."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from m5_analysis import build_analysis, load_validated_m4, validate_m4_identity
from m5_bundle import validate_bundle
from m5_phase import validate_worker_result
from m5_records import CHECKED_PLAN, SCHEMA_SET, load_checked_plan, resolve_bundle_path
from m5_spec import M4_MANIFEST_SHA256, PLAN_SHA256, compile_checked_plan
from m5_test_bundle import build_bundle, synthetic_m4, unaliased_root, write_json
from protocol_validation import ValidationError


class M5PlanContractTests(unittest.TestCase):
    def test_exact_plan_compiles_complete_factorial_and_schedule(self) -> None:
        plan, digest = load_checked_plan()
        checked = compile_checked_plan(plan)
        self.assertEqual(digest, PLAN_SHA256)
        self.assertEqual(len(checked.conditions), 36)
        self.assertEqual(len(checked.samples), 108)
        self.assertEqual(checked.document["analysis"]["m4_binding"]["bundle_manifest_sha256"], M4_MANIFEST_SHA256)
        self.assertEqual(checked.document["execution_gate"]["status"], "BLOCKED")
        self.assertEqual(
            checked.document["execution_gate"]["blocking_requirements"],
            ["windows_dao_host_bound_to_the_exact_clean_pushed_producer_commit"],
        )
        self.assertEqual(checked.bounds["worker_timeout_seconds"], 120)

    def test_r7_normalized_scientific_design_equals_r6(self) -> None:
        r6_path = CHECKED_PLAN.with_name("m5-compact-confirm-r6.plan.json")
        r6 = json.loads(r6_path.read_text(encoding="utf-8"))
        r7 = json.loads(CHECKED_PLAN.read_text(encoding="utf-8"))
        for key in (
            "provenance_ids",
            "requires_exact_clean_commit",
            "open_provenance_requirements",
            "design",
            "sample_validity_rules",
            "analysis",
            "conditions",
            "samples",
            "resolved_provenance_requirements",
        ):
            self.assertEqual(r7[key], r6[key], key)
        self.assertEqual(r7["bounds"], r6["bounds"])
        self.assertEqual(r7["bounds"]["worker_timeout_seconds"], 120)
        expected_changes = {
            "$.experiment_id", "$.related_experiments", "$.remote_ref",
            "$.preregistration.provenance_entry",
            "$.preregistration.revision_of",
            "$.preregistration.recorded_after_execution_blocker",
            "$.preregistration.revision_scope",
            "$.preregistration.amendment_rule",
            "$.execution_gate.reason",
        }

        def changed_paths(left: object, right: object, path: str = "$") -> set[str]:
            if isinstance(left, dict) and isinstance(right, dict):
                changed: set[str] = set()
                for key in left.keys() | right.keys():
                    if key not in left or key not in right:
                        changed.add(f"{path}.{key}")
                    else:
                        changed.update(changed_paths(left[key], right[key], f"{path}.{key}"))
                return changed
            return set() if left == right else {path}

        self.assertEqual(changed_paths(r6, r7), expected_changes)
        for sample in r7["samples"]:
            self.assertEqual(Path(sample["source_database_path"]).name, "SOURCE.MDB")
            self.assertEqual(Path(sample["compact_input_database_path"]).name, "COMPACT-INPUT.MDB")
            self.assertEqual(Path(sample["compacted_database_path"]).name, "COMPACTED.MDB")
            self.assertEqual(Path(sample["verify_database_path"]).name, "VERIFY.MDB")

    def test_plan_byte_change_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "plan.json"
            changed.write_bytes(CHECKED_PLAN.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValidationError, "bytes differ"):
                load_checked_plan(changed)

    def test_all_m5_schemas_lint(self) -> None:
        SCHEMA_SET.lint()

    def test_r7_schemas_only_change_revision_bindings(self) -> None:
        prior = CHECKED_PLAN.parents[1] / "m5r5"
        revised = CHECKED_PLAN.parents[1] / "m5r6"
        self.assertEqual(
            {path.name for path in revised.glob("*.json")},
            {path.name for path in prior.glob("*.json")},
        )
        for path in revised.glob("*.json"):
            current = path.read_text(encoding="utf-8")
            normalized = (
                current.replace("urn:jet3-rs:dao:m5r7:", "urn:jet3-rs:dao:m5r6:")
                .replace("DAO-M5-COMPACT-CONFIRM-007", "DAO-M5-COMPACT-CONFIRM-006")
                .replace("refs/heads/codex/m5r6-null-prefix-bound", "refs/heads/codex/m5r5-worker-return-bound")
            )
            self.assertEqual(
                json.loads(normalized),
                json.loads((prior / path.name).read_text(encoding="utf-8")),
                path.name,
            )


class M5AnalysisContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = unaliased_root(cls._temporary.name)
        cls.manifest, cls.m4 = build_bundle(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _analysis_inputs(self) -> tuple[dict, list[dict], dict[str, bytes]]:
        plan = json.loads(CHECKED_PLAN.read_text(encoding="utf-8"))
        records = []
        prefixes: dict[str, bytes] = {}
        for sample in plan["samples"]:
            record = json.loads((self.root / sample["record_path"]).read_text(encoding="utf-8"))
            results = {}
            for phase in ("source", "compact", "verify"):
                result_path = self.root / record["phases"][phase]["worker_result"]["path"]
                result = json.loads(result_path.read_text(encoding="utf-8"))
                results[phase] = result
                for observation in result["database_observations"]:
                    if observation["prefix"] is not None:
                        prefixes[observation["prefix"]["path"]] = (self.root / observation["prefix"]["path"]).read_bytes()
            record["_results"] = results
            records.append(record)
        return plan, records, prefixes

    def test_analysis_recomputes_exact_topology_and_outcome(self) -> None:
        plan, records, prefixes = self._analysis_inputs()
        analysis = build_analysis(plan, records, prefixes, self.m4)
        counts: dict[str, int] = {}
        for comparison in analysis["comparisons"]:
            counts[comparison["kind"]] = counts.get(comparison["kind"], 0) + 1
        self.assertEqual(counts, {"paired_phase": 108, "within_condition": 324, "compact_versus_created_matched": 108, "source_versus_compacted_within_sample": 108})
        self.assertEqual(analysis["scientific_outcome"], "compact_matches_created")
        self.assertFalse(analysis["companion_bytes_analyzed"])

    def test_empty_bound_m4_candidate_is_inconclusive(self) -> None:
        plan, records, prefixes = self._analysis_inputs()
        m4 = synthetic_m4()
        m4["analysis"]["candidate_sets"] = []
        analysis = build_analysis(plan, records, prefixes, m4)
        self.assertEqual(analysis["scientific_outcome"], "inconclusive")

    def test_source_replica_instability_is_inconclusive(self) -> None:
        plan, records, prefixes = self._analysis_inputs()
        result = records[0]["_results"]["source"]
        observation = result["database_observations"][0]
        locator = observation["prefix"]["path"]
        changed = bytearray(prefixes[locator])
        changed[10] ^= 0xFF
        prefixes[locator] = bytes(changed)
        observation["prefix"]["sha256"] = hashlib.sha256(changed).hexdigest()
        analysis = build_analysis(plan, records, prefixes, self.m4)
        self.assertEqual(analysis["scientific_outcome"], "inconclusive")

    def test_excluded_m4_commit_region_variation_is_not_analyzed(self) -> None:
        plan, records, prefixes = self._analysis_inputs()
        m4 = synthetic_m4()
        locator = next(iter(m4["prefixes"]))
        changed = bytearray(m4["prefixes"][locator])
        changed[1600] = 0xAA
        m4["prefixes"][locator] = bytes(changed)
        analysis = build_analysis(plan, records, prefixes, m4)
        self.assertEqual(analysis["scientific_outcome"], "compact_matches_created")

    def test_nested_or_wrong_m4_manifest_name_is_not_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = unaliased_root(directory)
            (root / "nested").mkdir()
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            (root / "nested" / "bundle-manifest.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "directly"):
                load_validated_m4(root)

    def test_wrong_m4_manifest_hash_is_rejected_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = unaliased_root(directory)
            (root / "bundle-manifest.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "SHA-256 differs"):
                validate_m4_identity(root)

    def test_companion_size_above_protocol_ceiling_is_rejected(self) -> None:
        path = next(self.root.glob("evidence/quiescence/*/source_database.json"))
        document = json.loads(path.read_text(encoding="utf-8"))
        document["companion"] = {
            "state": "present", "path": document["companion"]["path"],
            "bytes": 65537, "sha256": "0" * 64,
            "file_identity": {"volume_serial_number": "00000001", "file_index": "0000000000000001", "link_count": 1},
            "exclusive_open_verified": True, "checked_after_worker_exit": True,
        }
        with self.assertRaisesRegex(ValidationError, "maximum"):
            SCHEMA_SET.validate(document)

    def test_m4_root_symlink_is_rejected_before_manifest_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = unaliased_root(directory)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            try:
                alias.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")
            with self.assertRaisesRegex(ValidationError, "aliases and reparses"):
                validate_m4_identity(alias)


class M5BundleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._template_temporary = tempfile.TemporaryDirectory()
        cls.template = unaliased_root(cls._template_temporary.name) / "template"
        cls.manifest, cls.m4 = build_bundle(cls.template)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._template_temporary.cleanup()

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = unaliased_root(self._temporary.name) / "bundle"
        shutil.copytree(self.template, self.root)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _validate(self) -> dict:
        with patch("m5_bundle.load_validated_m4", return_value=self.m4):
            return validate_bundle(self.root, self.root)

    def _manifest(self) -> dict:
        return json.loads((self.root / "bundle-manifest.json").read_text(encoding="utf-8"))

    def _rewrite_manifest_entry(self, locator: str) -> None:
        manifest = self._manifest()
        entry = next(row for row in manifest["files"] if row["path"] == locator)
        payload = (self.root / locator).read_bytes()
        entry["size_bytes"] = len(payload)
        entry["sha256"] = hashlib.sha256(payload).hexdigest()
        write_json(self.root / "bundle-manifest.json", manifest)

    def test_complete_bundle_passes_exact_recomputation(self) -> None:
        validated = self._validate()
        self.assertEqual(len(validated["records"]), 108)
        self.assertEqual(len(validated["analysis"]["comparisons"]), 648)

    def test_unexpected_file_breaks_complete_tree_closure(self) -> None:
        (self.root / "unexpected.bin").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValidationError, "bundle tree differs"):
            self._validate()

    def test_supplied_root_symlink_is_rejected_where_supported(self) -> None:
        alias = self.root.parent / "bundle-alias"
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlinks are unavailable")
        with patch("m5_bundle.load_validated_m4", return_value=self.m4):
            with self.assertRaisesRegex(ValidationError, "aliases and reparses"):
                validate_bundle(alias, self.root)

    def test_phase_validator_uses_exact_invocation_not_nested_decoy(self) -> None:
        decoy = self.root / "nested" / "SOURCE-invocation.json"
        decoy.parent.mkdir()
        decoy.write_text("{}", encoding="utf-8")
        result = self.root / "evidence/samples/M5-S20U-D20-OMIT-01/SOURCE-worker-result.json"
        document, _, _ = validate_worker_result(self.root, result)
        self.assertEqual(document["phase_id"], "source")

    def test_reparse_path_component_is_rejected_where_supported(self) -> None:
        outside = self.root.parent / "outside"
        outside.mkdir()
        alias = self.root / "aliased"
        try:
            alias.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlinks are unavailable")
        with self.assertRaisesRegex(ValidationError, "aliases and reparses"):
            resolve_bundle_path(self.root, "aliased/payload.json")

    def test_prefix_corruption_cannot_be_hidden_by_manifest_rehash(self) -> None:
        locator = "evidence/samples/M5-S20U-D20-OMIT-01/SOURCE.prefix.bin"
        payload = bytearray((self.root / locator).read_bytes())
        payload[0] ^= 0xFF
        (self.root / locator).write_bytes(payload)
        self._rewrite_manifest_entry(locator)
        with self.assertRaisesRegex(ValidationError, "retained prefix"):
            self._validate()

    def test_analysis_corruption_cannot_be_hidden_by_manifest_rehash(self) -> None:
        analysis_path = self.root / "analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        analysis["scientific_outcome"] = "inconclusive"
        write_json(analysis_path, analysis)
        self._rewrite_manifest_entry("analysis.json")
        with self.assertRaisesRegex(ValidationError, "retained canonical analysis"):
            self._validate()

    def test_manifest_role_retyping_is_rejected(self) -> None:
        manifest = self._manifest()
        entry = next(row for row in manifest["files"] if row["role"] == "post_worker_quiescence")
        entry["role"] = "companion"
        entry["media_type"] = "application/octet-stream"
        write_json(self.root / "bundle-manifest.json", manifest)
        with self.assertRaisesRegex(ValidationError, "role counts"):
            self._validate()

    def test_provider_clsid_corruption_is_rejected_after_rehash(self) -> None:
        result_locator = "evidence/samples/M5-S20U-D20-OMIT-01/SOURCE-worker-result.json"
        record_locator = "evidence/samples/M5-S20U-D20-OMIT-01/record.json"
        result_path = self.root / result_locator
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["provider"]["clsid"] = "{11111111-1111-1111-1111-111111111111}"
        write_json(result_path, result)
        record_path = self.root / record_locator
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["phases"]["source"]["worker_result"]["sha256"] = hashlib.sha256(result_path.read_bytes()).hexdigest()
        write_json(record_path, record)
        self._rewrite_manifest_entry(result_locator)
        self._rewrite_manifest_entry(record_locator)
        with self.assertRaisesRegex(ValidationError, "provider clsid"):
            self._validate()


if __name__ == "__main__":
    unittest.main()
