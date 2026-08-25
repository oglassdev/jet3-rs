"""Synthetic contracts for A4 hosted fan-in and the holdout boundary."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
TESTS = ROOT / "oracle" / "windows-dao" / "tests"
for location in (SCRIPTS, TESTS):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from a4_bundle import (  # noqa: E402
    CHECKPOINT_COUNT,
    DERIVATION_REPLICA_COUNT,
    MAX_BUNDLE_BYTES,
    MAX_JSON_BYTES,
    MAX_PAGE_BLOBS,
    MAX_PAGE_STORE_BYTES,
    PAGE_SIZE,
    REPLICA_COUNT,
    _validate_target_disclosures,
    analyze_bundle,
    assemble_bundle,
    finalize_bundle,
    validate_bundle,
)
from a4_spec import BOUNDS, EXPERIMENT_ID, PLAN_SHA256  # noqa: E402
from a4_test_bundle import write_replica_trees  # noqa: E402
from protocol_validation import ValidationError  # noqa: E402


class A4BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="a4-hosted-")
        cls.root = Path(cls.temporary.name)
        cls.replica_roots, cls.campaign_id, cls.producer_commit = write_replica_trees(
            cls.root / "source-replicas"
        )
        cls.bundle = cls.root / "bundle"
        assemble_bundle(
            cls.replica_roots[:2], cls.bundle, cls.campaign_id, cls.producer_commit
        )
        cls.holdout_root = cls.root / "downloaded-holdout"
        copy_command = (
            sys.executable,
            "-c",
            "import shutil,sys;shutil.copytree(sys.argv[1],sys.argv[2])",
            str(cls.replica_roots[2]),
            str(cls.holdout_root),
        )
        cls.analysis = analyze_bundle(
            cls.bundle,
            cls.holdout_root,
            cls.campaign_id,
            cls.producer_commit,
            copy_command,
        )
        cls.manifest = finalize_bundle(
            cls.bundle,
            cls.campaign_id,
            cls.producer_commit,
            "2026-08-25T10:00:00Z",
            created_utc="2026-08-25T10:45:00Z",
        )
        cls.validation = validate_bundle(
            cls.bundle, cls.campaign_id, cls.producer_commit
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_fixed_bounds_match_the_checked_plan(self) -> None:
        self.assertEqual(PAGE_SIZE, 2_048)
        self.assertEqual(REPLICA_COUNT, 3)
        self.assertEqual(DERIVATION_REPLICA_COUNT, 2)
        self.assertEqual(CHECKPOINT_COUNT, 25)
        self.assertEqual(MAX_JSON_BYTES, 67_108_864)
        self.assertEqual(MAX_PAGE_BLOBS, 65_536)
        self.assertEqual(MAX_PAGE_STORE_BYTES, 134_217_728)
        self.assertEqual(MAX_BUNDLE_BYTES, 805_306_368)
        self.assertEqual(BOUNDS["fan_in_timeout_seconds"], 900)

    def test_holdout_is_materialized_only_after_frozen_bytes_exist(self) -> None:
        self.assertTrue(self.holdout_root.is_dir())
        candidate = self.bundle / "analysis/derivation-candidates.json"
        receipt = json.loads(
            (self.bundle / "analysis/holdout-structure-receipt.json").read_bytes()
        )
        self.assertEqual(receipt["derivation_candidate_set_sha256"],
                         self.analysis["derivation_candidate_set_sha256"])
        self.assertTrue(receipt["validated_after_candidate_freeze"])
        self.assertFalse(receipt["page_bytes_exposed_to_analyzer"])
        self.assertGreater(candidate.stat().st_size, 0)

    def test_complete_bundle_is_closed_and_bound(self) -> None:
        manifest = self.validation["manifest"]
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(manifest["experiment_id"], EXPERIMENT_ID)
        self.assertEqual(manifest["plan_sha256"], PLAN_SHA256)
        self.assertEqual(manifest["revision_plan_sha256"], PLAN_SHA256)
        self.assertEqual(manifest["checkpoint_count"], 75)
        self.assertEqual(manifest["campaign_elapsed_seconds"], 2_700)
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertIn("plan/a4-row-anchored-maps.plan.json", paths)
        self.assertEqual(
            len([path for path in paths if path.startswith("schema-snapshots/")]),
            75,
        )
        discovered = {
            path.relative_to(self.bundle).as_posix()
            for path in self.bundle.rglob("*") if path.is_file()
        }
        self.assertEqual(discovered, paths | {"bundle-manifest.json"})

    def test_manifest_translates_legacy_outcome_vocabulary_only_at_boundary(self) -> None:
        report = json.loads(
            (self.bundle / "analysis/analysis-report.json").read_bytes()
        )
        expected = (
            "one_or_more_submodels_predict_holdout"
            if report["scientific_outcome"] == "one_or_more_layers_predict_holdout"
            else "no_submodel_predicts_holdout"
        )
        self.assertEqual(self.manifest["analysis_scientific_outcome"], expected)

    def test_target_disclosures_use_t1_t3_t4_plan_baselines(self) -> None:
        observation = self.validation["replicas"][0]
        _validate_target_disclosures(observation)
        changed = copy.deepcopy(observation)
        checkpoint = next(
            row for row in changed["checkpoints"]
            if row["checkpoint_id"] == "T4_REL_0064"
        )
        checkpoint["target_baseline_pages"] += 1
        with self.assertRaisesRegex(ValidationError, "target baseline"):
            _validate_target_disclosures(changed)

    def test_preexisting_holdout_is_rejected_before_analysis(self) -> None:
        root = self.root / "preexisting-case"
        roots, campaign, commit = write_replica_trees(root / "replicas")
        bundle = root / "bundle"
        assemble_bundle(roots[:2], bundle, campaign, commit)
        preexisting = root / "holdout"
        shutil.copytree(roots[2], preexisting)
        with self.assertRaisesRegex(ValidationError, "existed before"):
            analyze_bundle(bundle, preexisting, campaign, commit, (sys.executable, "-V"))

    def test_producer_does_not_import_independent_validator(self) -> None:
        for script in ("a4_bundle.py", "a4_holdout.py"):
            source = (SCRIPTS / script).read_text(encoding="utf-8")
            self.assertNotIn("a4_independent", source)
            self.assertNotIn("os.environ", source)
            self.assertNotIn("os.getenv", source)


if __name__ == "__main__":
    unittest.main()
