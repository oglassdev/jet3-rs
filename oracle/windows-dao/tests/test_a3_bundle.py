"""Synthetic fan-in contracts for DAO A3 bundle assembly, analysis, and validation."""

from __future__ import annotations

import copy
import hashlib
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

from a3_analysis import main as analysis_main  # noqa: E402
from a3_bundle import (  # noqa: E402
    CHECKPOINT_COUNT,
    DERIVATION_REPLICA_COUNT,
    MAX_BUNDLE_BYTES,
    MAX_JSON_BYTES,
    MAX_PAGE_BLOBS,
    MAX_PAGE_STORE_BYTES,
    PAGE_SIZE,
    REPLICA_COUNT,
    assemble_bundle,
    finalize_bundle,
    validate_bundle,
    _validate_target_disclosures,
)
from a3_holdout import (  # noqa: E402
    FAN_IN_TIMEOUT_SECONDS,
    HOLDOUT_TIMEOUT_SECONDS,
    graft_holdout_replica,
    holdout_absent,
    main as holdout_main,
    write_receipt,
)
from a3_spec import (  # noqa: E402
    BOUNDS, EXPERIMENT_ID, PLAN_SHA256, REVISION_CHAIN, REVISION_PLAN_SHA256,
)
from a3_test_bundle import write_replica_trees  # noqa: E402
from protocol_validation import ValidationError, canonical_json_bytes  # noqa: E402


class A3BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.replica_roots, cls.campaign_id, cls.producer_commit = write_replica_trees(
            cls.root / "replicas"
        )
        cls.derivation_roots = cls.replica_roots[:DERIVATION_REPLICA_COUNT]
        cls.holdout_root = cls.replica_roots[DERIVATION_REPLICA_COUNT]
        cls.bundle = cls.root / "complete-bundle"
        cls.assembly = assemble_bundle(
            cls.derivation_roots, cls.bundle, cls.campaign_id, cls.producer_commit
        )
        cls.holdout_absent_before_analysis = holdout_absent(cls.bundle)
        cls.freeze_state = cls.root / "freeze-phase.json"
        if analysis_main([
            "--freeze-only", "--bundle-root", str(cls.bundle),
            "--freeze-state", str(cls.freeze_state),
            "--holdout-artifact-path", str(cls.root / "not-downloaded-holdout"),
        ]) != 0:
            raise AssertionError("synthetic A3 freeze did not complete")
        freeze = json.loads(cls.freeze_state.read_bytes())
        if holdout_main([
            "--bundle-root", str(cls.bundle),
            "--holdout-replica-root", str(cls.holdout_root),
            "--candidate-set", str(cls.bundle / "analysis" / "derivation-candidates.json"),
            "--candidate-sha256", freeze["derivation_candidate_set_sha256"],
            "--campaign-id", cls.campaign_id,
            "--producer-commit", cls.producer_commit,
            "--output", str(cls.bundle / "analysis" / "holdout-structure-receipt.json"),
            "--freeze-state", str(cls.freeze_state),
        ]) != 0:
            raise AssertionError("synthetic A3 holdout validation did not complete")
        if analysis_main([
            "--resume", "--bundle-root", str(cls.bundle),
            "--freeze-state", str(cls.freeze_state),
        ]) != 0:
            raise AssertionError("synthetic A3 analysis did not resume")
        cls.pre_finalization = cls.root / "pre-finalization-bundle"
        shutil.copytree(cls.bundle, cls.pre_finalization)
        cls.campaign_started_utc = "2026-08-22T11:15:00Z"
        cls.created_utc = "2026-08-22T12:00:00Z"
        cls.manifest = finalize_bundle(
            cls.bundle, cls.campaign_id, cls.producer_commit,
            cls.campaign_started_utc, created_utc=cls.created_utc,
        )
        cls.validation = validate_bundle(cls.bundle, cls.campaign_id, cls.producer_commit)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_fixed_bounds_match_the_checked_a3_plan(self) -> None:
        self.assertEqual(PAGE_SIZE, 2_048)
        self.assertEqual(REPLICA_COUNT, 3)
        self.assertEqual(DERIVATION_REPLICA_COUNT, 2)
        self.assertEqual(CHECKPOINT_COUNT, 25)
        self.assertEqual(MAX_JSON_BYTES, 67_108_864)
        self.assertEqual(MAX_PAGE_BLOBS, 65_536)
        self.assertEqual(MAX_PAGE_STORE_BYTES, 536_870_912)
        self.assertEqual(MAX_BUNDLE_BYTES, 805_306_368)
        self.assertEqual(FAN_IN_TIMEOUT_SECONDS, 900)
        self.assertEqual(HOLDOUT_TIMEOUT_SECONDS, 300)
        self.assertEqual(BOUNDS["fan_in_timeout_seconds"], FAN_IN_TIMEOUT_SECONDS)
        self.assertEqual(BOUNDS["max_bundle_bytes"], MAX_BUNDLE_BYTES)

    def test_bundle_and_holdout_never_read_ci_bindings_from_environment(self) -> None:
        # EXP-0043 lesson: fan-in tools take every binding as an explicit argument.
        for script in ("a3_bundle.py", "a3_holdout.py"):
            source = (SCRIPTS / script).read_text(encoding="utf-8")
            self.assertNotIn("os.environ", source)
            self.assertNotIn("os.getenv", source)
            self.assertNotIn("a2_", source)

    def test_complete_bundle_is_closed_bound_and_decisive(self) -> None:
        manifest = self.validation["manifest"]
        self.assertEqual(self.assembly["campaign_id"], self.campaign_id)
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(manifest["document_type"], "dao_a3_bundle_manifest")
        self.assertEqual(manifest["experiment_id"], EXPERIMENT_ID)
        self.assertEqual(manifest["plan_sha256"], PLAN_SHA256)
        self.assertEqual(manifest["revision_plan_sha256"], REVISION_PLAN_SHA256)
        self.assertEqual(manifest["campaign_started_utc"], self.campaign_started_utc)
        self.assertEqual(manifest["created_utc"], self.created_utc)
        self.assertEqual(manifest["campaign_elapsed_seconds"], 2_700)
        self.assertEqual(manifest["replica_count"], REPLICA_COUNT)
        self.assertEqual(manifest["checkpoint_count"], 75)
        self.assertRegex(manifest["created_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(manifest["bundle_status"], "decisive_pending_independent_validation")
        self.assertEqual(manifest["independent_validation_status"], "not_independently_validated")
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertIn("plan/a3-allocation-maps.plan.json", paths)
        entries = {entry["path"]: entry for entry in manifest["files"]}
        for locator, digest in REVISION_CHAIN.items():
            self.assertEqual(entries[locator]["role"], "revision_plan")
            self.assertEqual(entries[locator]["media_type"], "application/json")
            self.assertEqual(entries[locator]["sha256"], digest)
        for entry in manifest["files"]:
            if entry["media_type"] == "application/json" and entry["role"] not in {
                "plan", "revision_plan",
            }:
                document = json.loads((self.bundle / entry["path"]).read_bytes())
                self.assertEqual(document["revision_plan_sha256"], REVISION_PLAN_SHA256)
        discovered = {
            path.relative_to(self.bundle).as_posix()
            for path in self.bundle.rglob("*")
            if path.is_file()
        }
        self.assertEqual(discovered, paths | {"bundle-manifest.json"})
        page_entries = [entry for entry in manifest["files"] if entry["role"] == "page_blob"]
        self.assertEqual(len(page_entries), manifest["page_blob_count"])
        self.assertLessEqual(len(page_entries), MAX_PAGE_BLOBS)

    def test_analyzer_spawned_holdout_receipt_is_post_freeze_and_manifest_bound(self) -> None:
        candidate = self.bundle / "analysis" / "derivation-candidates.json"
        receipt = json.loads(
            (self.bundle / "analysis" / "holdout-structure-receipt.json").read_bytes()
        )
        replica_manifest = self.bundle / "replica-artifacts" / "replica-03-manifest.json"
        self.assertEqual(receipt["document_type"], "dao_a3_holdout_structure_receipt")
        self.assertEqual(receipt["experiment_id"], EXPERIMENT_ID)
        self.assertEqual(receipt["plan_sha256"], PLAN_SHA256)
        self.assertEqual(receipt["replica"], 3)
        self.assertEqual(
            receipt["derivation_candidate_set_sha256"],
            hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            receipt["replica_artifact_manifest_sha256"],
            hashlib.sha256(replica_manifest.read_bytes()).hexdigest(),
        )
        self.assertTrue(receipt["validated_after_candidate_freeze"])
        self.assertFalse(receipt["page_bytes_exposed_to_analyzer"])

    def test_producer_enforces_r5_target_baselines_and_arithmetic(self) -> None:
        observation = self.validation["replicas"][0]
        _validate_target_disclosures(observation)
        for checkpoint_id, field in (
            ("L_REL_0064", "target_baseline_pages"),
            ("H_REL_0064", "target_threshold_pages"),
            ("P_ABS_04096", "target_baseline_pages"),
        ):
            with self.subTest(checkpoint=checkpoint_id, field=field):
                changed = copy.deepcopy(observation)
                checkpoint = next(
                    row for row in changed["checkpoints"]
                    if row["checkpoint_id"] == checkpoint_id
                )
                checkpoint[field] = 1 if checkpoint[field] is None else checkpoint[field] + 1
                with self.assertRaisesRegex(ValidationError, "target"):
                    _validate_target_disclosures(changed)

    def test_holdout_is_grafted_only_after_the_candidate_freeze(self) -> None:
        self.assertTrue(self.holdout_absent_before_analysis)
        self.assertFalse(holdout_absent(self.bundle))
        report = json.loads((self.bundle / "analysis" / "analysis-report.json").read_bytes())
        self.assertTrue(report["holdout_structurally_validated_after_freeze"])
        with self.assertRaisesRegex(ValidationError, "exactly the two derivation"):
            assemble_bundle(self.replica_roots, self.root / "three-root-bundle",
                            self.campaign_id, self.producer_commit)
        self.assertFalse((self.root / "three-root-bundle").exists())
        candidate = self.bundle / "analysis" / "derivation-candidates.json"
        with self.assertRaisesRegex(ValidationError, "already present before graft"):
            graft_holdout_replica(
                self.holdout_root, self.bundle, candidate,
                hashlib.sha256(candidate.read_bytes()).hexdigest(),
                self.campaign_id, self.producer_commit,
            )
        unfrozen = self.root / "unfrozen-bundle"
        assemble_bundle(self.derivation_roots, unfrozen, self.campaign_id, self.producer_commit)
        unfrozen_candidate = unfrozen / "analysis" / "derivation-candidates.json"
        with self.assertRaisesRegex(ValidationError, "more than the plan"):
            graft_holdout_replica(
                self.holdout_root, unfrozen, unfrozen_candidate,
                "0" * 64, self.campaign_id, self.producer_commit,
            )
        unfrozen_candidate.parent.mkdir()
        unfrozen_candidate.write_bytes(candidate.read_bytes())
        with self.assertRaisesRegex(ValidationError, "frozen candidate hash"):
            graft_holdout_replica(
                self.holdout_root, unfrozen, unfrozen_candidate,
                "0" * 64, self.campaign_id, self.producer_commit,
            )
        self.assertTrue(holdout_absent(unfrozen))

    def test_freeze_marker_observables_each_fail_before_holdout_graft(self) -> None:
        bundle = self.root / "marker-observable-bundle"
        assemble_bundle(
            self.derivation_roots, bundle, self.campaign_id, self.producer_commit
        )
        candidate = bundle / "analysis" / "derivation-candidates.json"
        candidate.parent.mkdir()
        candidate.write_bytes(
            (self.bundle / "analysis" / "derivation-candidates.json").read_bytes()
        )
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        base_state = json.loads(self.freeze_state.read_bytes())
        cases = (
            ("freeze_phase_completed", False, "candidate freeze"),
            (
                "replica_3_artifact_existed_before_freeze_phase_completed",
                True,
                "candidate freeze",
            ),
            ("derivation_candidate_set_sha256", "f" * 64, "candidate freeze"),
            (
                "analyzer_replica_3_opens_before_receipt",
                1,
                "analyzer opened replica 3",
            ),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                state = dict(base_state)
                state[field] = value
                marker = self.root / f"marker-{field}.json"
                marker.write_bytes(canonical_json_bytes(state))
                with self.assertRaisesRegex(ValidationError, message):
                    write_receipt(
                        bundle, self.holdout_root, candidate, digest,
                        self.campaign_id, self.producer_commit,
                        self.root / f"receipt-{field}.json", marker,
                    )
                self.assertTrue(holdout_absent(bundle))

    def test_assemble_rejects_wrong_bindings_without_publication(self) -> None:
        destination = self.root / "wrong-campaign"
        with self.assertRaisesRegex(ValidationError, "campaign"):
            assemble_bundle(self.derivation_roots, destination, "a3-wrong-campaign", self.producer_commit)
        self.assertFalse(destination.exists())
        destination = self.root / "wrong-producer"
        with self.assertRaisesRegex(ValidationError, "expected producer commit"):
            assemble_bundle(self.derivation_roots, destination, self.campaign_id, "f" * 40)
        self.assertFalse(destination.exists())

    def test_assemble_rejects_extra_replica_inventory(self) -> None:
        replica_copy = self.root / "replica-extra"
        shutil.copytree(self.replica_roots[0], replica_copy)
        (replica_copy / "unexpected.json").write_text("{}\n", encoding="utf-8")
        destination = self.root / "extra-inventory-bundle"
        with self.assertRaisesRegex(ValidationError, "closed inventory"):
            assemble_bundle(
                (replica_copy, *self.derivation_roots[1:]),
                destination,
                self.campaign_id,
                self.producer_commit,
            )
        self.assertFalse(destination.exists())

    def test_complete_validator_rejects_modified_artifact(self) -> None:
        corrupted = self.root / "corrupted-bundle"
        shutil.copytree(self.bundle, corrupted)
        report = corrupted / "analysis" / "analysis-report.json"
        report.write_bytes(report.read_bytes() + b" ")
        with self.assertRaisesRegex(ValidationError, "size|sha256"):
            validate_bundle(corrupted, self.campaign_id, self.producer_commit)

    def test_revision_inventory_and_document_binding_tampering_are_rejected(self) -> None:
        relabeled = self.root / "relabeled-revision"
        shutil.copytree(self.bundle, relabeled)
        manifest_path = relabeled / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        revision = next(
            entry for entry in manifest["files"]
            if entry["path"] == "plan/a3-allocation-maps-r5.plan.json"
        )
        revision["role"] = "plan"
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(ValidationError, "role"):
            validate_bundle(relabeled, self.campaign_id, self.producer_commit)

        rebound = self.root / "rebound-receipt"
        shutil.copytree(self.bundle, rebound)
        manifest_path = rebound / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        receipt_path = rebound / "analysis/holdout-structure-receipt.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["revision_plan_sha256"] = "f" * 64
        receipt_bytes = canonical_json_bytes(receipt)
        receipt_path.write_bytes(receipt_bytes)
        entry = next(
            row for row in manifest["files"]
            if row["path"] == "analysis/holdout-structure-receipt.json"
        )
        entry["sha256"] = hashlib.sha256(receipt_bytes).hexdigest()
        entry["size_bytes"] = len(receipt_bytes)
        manifest["holdout_structure_receipt_sha256"] = entry["sha256"]
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(ValidationError, "revision_plan_sha256"):
            validate_bundle(rebound, self.campaign_id, self.producer_commit)

    def test_overtime_run_cannot_finalize_a_successful_bundle(self) -> None:
        overtime = self.root / "overtime-bundle"
        shutil.copytree(self.pre_finalization, overtime)
        with self.assertRaisesRegex(ValidationError, "retained-evidence bound"):
            finalize_bundle(
                overtime, self.campaign_id, self.producer_commit,
                self.campaign_started_utc, created_utc="2026-08-22T12:00:01Z",
            )
        self.assertFalse((overtime / "bundle-manifest.json").exists())

    def test_complete_validator_rejects_non_a3_manifest_identity(self) -> None:
        relabeled = self.root / "relabeled-bundle"
        shutil.copytree(self.bundle, relabeled)
        manifest_path = relabeled / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["document_type"] = "dao_a2_bundle_manifest"
        manifest["experiment_id"] = "DAO-A2-ALLOCATION-MAPS-001"
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(ValidationError, "document type"):
            validate_bundle(relabeled, self.campaign_id, self.producer_commit)

    def test_complete_validator_rejects_explicit_binding_mismatch(self) -> None:
        with self.assertRaisesRegex(ValidationError, "expected campaign"):
            validate_bundle(self.bundle, "a3-wrong-campaign", self.producer_commit)
        with self.assertRaisesRegex(ValidationError, "expected producer commit"):
            validate_bundle(self.bundle, self.campaign_id, "f" * 40)

    def test_finalize_refuses_manifest_replacement(self) -> None:
        with self.assertRaisesRegex(ValidationError, "already exists"):
            finalize_bundle(
                self.bundle, self.campaign_id, self.producer_commit,
                self.campaign_started_utc,
            )


if __name__ == "__main__":
    unittest.main()
