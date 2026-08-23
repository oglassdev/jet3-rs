"""Synthetic fan-in contracts for DAO A2 bundle assembly and validation."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts" / "archive"
sys.path.insert(0, str(SCRIPTS))

import a2_bundle  # noqa: E402
from a2_analysis import main as analysis_main  # noqa: E402
from a2_bundle import (  # noqa: E402
    CHECKPOINT_COUNT,
    MAX_BUNDLE_BYTES,
    MAX_JSON_BYTES,
    MAX_PAGE_BLOBS,
    MAX_PAGE_STORE_BYTES,
    PAGE_SIZE,
    REPLICA_COUNT,
    assemble_bundle,
    finalize_bundle,
    validate_bundle,
)
from a2_generator import (  # noqa: E402
    generate_synthetic_bundles,
    write_synthetic_bundle,
)
from a2_holdout import FAN_IN_TIMEOUT_SECONDS, HOLDOUT_TIMEOUT_SECONDS  # noqa: E402
from a2_spec import BOUNDS, PLAN_SHA256  # noqa: E402
from protocol_validation import ValidationError  # noqa: E402


class A2BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.replica_roots = tuple(
            cls.root / f"replica-{replica:02d}"
            for replica in range(1, REPLICA_COUNT + 1)
        )
        cls.synthetic = generate_synthetic_bundles()
        for root, bundle in zip(cls.replica_roots, cls.synthetic, strict=True):
            write_synthetic_bundle(root, bundle)
        observation = cls.synthetic[0].documents["observations/replica-01.json"]
        cls.campaign_id = observation["campaign_id"]
        cls.producer_commit = observation["producer_commit"]
        cls.bundle = cls.root / "complete-bundle"
        cls.assembly = assemble_bundle(
            cls.replica_roots, cls.bundle, cls.campaign_id, cls.producer_commit
        )
        analysis_exit = analysis_main(["--bundle-root", str(cls.bundle)])
        if analysis_exit != 0:
            raise AssertionError("synthetic A2 analysis did not complete")
        cls.manifest = finalize_bundle(
            cls.bundle, cls.campaign_id, cls.producer_commit)
        cls.validation = validate_bundle(
            cls.bundle, cls.campaign_id, cls.producer_commit)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_fixed_bounds_are_not_cli_reparsed_defaults(self) -> None:
        self.assertEqual(PAGE_SIZE, 2_048)
        self.assertEqual(REPLICA_COUNT, 3)
        self.assertEqual(CHECKPOINT_COUNT, 25)
        self.assertEqual(MAX_JSON_BYTES, 67_108_864)
        self.assertEqual(MAX_PAGE_BLOBS, 65_536)
        self.assertEqual(MAX_PAGE_STORE_BYTES, 536_870_912)
        self.assertEqual(MAX_BUNDLE_BYTES, 805_306_368)
        self.assertEqual(FAN_IN_TIMEOUT_SECONDS, 900)
        self.assertEqual(HOLDOUT_TIMEOUT_SECONDS, 300)
        self.assertEqual(
            {
                "page_size": PAGE_SIZE,
                "replicas": REPLICA_COUNT,
                "planned_checkpoints_per_replica": CHECKPOINT_COUNT,
                "max_json_bytes": MAX_JSON_BYTES,
                "max_unique_page_blobs": MAX_PAGE_BLOBS,
                "max_retained_page_store_bytes": MAX_PAGE_STORE_BYTES,
                "max_bundle_bytes": MAX_BUNDLE_BYTES,
            },
            {key: BOUNDS[key] for key in (
                "page_size", "replicas", "planned_checkpoints_per_replica",
                "max_json_bytes", "max_unique_page_blobs",
                "max_retained_page_store_bytes", "max_bundle_bytes",
            )},
        )

    def test_bundle_and_holdout_never_read_ci_bindings_from_environment(self) -> None:
        for script in ("a2_bundle.py", "a2_holdout.py"):
            source = (SCRIPTS / script).read_text(encoding="utf-8")
            self.assertNotIn("os.environ", source)
            self.assertNotIn("os.getenv", source)

    def test_complete_bundle_is_closed_bound_and_decisive(self) -> None:
        manifest = self.validation["manifest"]
        self.assertEqual(self.assembly["campaign_id"], self.campaign_id)
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(manifest["plan_sha256"], PLAN_SHA256)
        self.assertEqual(manifest["replica_count"], REPLICA_COUNT)
        self.assertEqual(manifest["checkpoint_count"], 75)
        self.assertRegex(manifest["created_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(
            manifest["bundle_status"],
            "decisive_pending_independent_validation",
        )
        self.assertEqual(
            manifest["independent_validation_status"],
            "not_independently_validated",
        )
        paths = {entry["path"] for entry in manifest["files"]}
        discovered = {
            path.relative_to(self.bundle).as_posix()
            for path in self.bundle.rglob("*")
            if path.is_file()
        }
        self.assertEqual(discovered, paths | {"bundle-manifest.json"})
        page_entries = [
            entry for entry in manifest["files"] if entry["role"] == "page_blob"
        ]
        self.assertEqual(len(page_entries), manifest["page_blob_count"])
        self.assertLessEqual(len(page_entries), MAX_PAGE_BLOBS)

    def test_holdout_receipt_is_post_freeze_and_manifest_bound(self) -> None:
        candidate = self.bundle / "analysis" / "derivation-candidates.json"
        receipt = json.loads(
            (self.bundle / "analysis" / "holdout-structure-receipt.json").read_bytes()
        )
        replica_manifest = (
            self.bundle / "replica-artifacts" / "replica-03-manifest.json"
        )
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

    def test_assemble_rejects_wrong_campaign_without_publication(self) -> None:
        destination = self.root / "wrong-campaign"
        with self.assertRaisesRegex(ValidationError, "campaign"):
            assemble_bundle(
                self.replica_roots, destination, "a2-wrong-campaign",
                self.producer_commit)
        self.assertFalse(destination.exists())

    def test_assemble_rejects_wrong_explicit_producer_without_publication(self) -> None:
        destination = self.root / "wrong-producer"
        with self.assertRaisesRegex(ValidationError, "expected producer commit"):
            assemble_bundle(
                self.replica_roots, destination, self.campaign_id, "f" * 40)
        self.assertFalse(destination.exists())

    def test_assemble_rejects_extra_replica_inventory(self) -> None:
        replica_copy = self.root / "replica-extra"
        shutil.copytree(self.replica_roots[0], replica_copy)
        (replica_copy / "unexpected.json").write_text("{}\n", encoding="utf-8")
        destination = self.root / "extra-inventory-bundle"
        with self.assertRaisesRegex(ValidationError, "closed inventory"):
            assemble_bundle(
                (replica_copy, *self.replica_roots[1:]),
                destination,
                self.campaign_id,
                self.producer_commit,
            )
        self.assertFalse(destination.exists())

    def test_inventory_uses_descriptor_identity_when_direntry_zeros_it(self) -> None:
        root = self.root / "zeroed-direntry"
        root.mkdir()
        artifact = root / "artifact.json"
        artifact.write_text("{}\n", encoding="utf-8")
        actual = artifact.stat()
        real_scandir = a2_bundle.os.scandir
        real_path_identity = a2_bundle._path_identity

        class ZeroedMetadata:
            def __init__(self, metadata: object) -> None:
                self._metadata = metadata
                self.st_dev = 0
                self.st_ino = 0
                self.st_nlink = 0

            def __getattr__(self, name: str) -> object:
                return getattr(self._metadata, name)

        class ZeroedEntry:
            def __init__(self, entry: object) -> None:
                self._entry = entry
                self.path = entry.path

            def stat(self, *, follow_symlinks: bool = True) -> ZeroedMetadata:
                return ZeroedMetadata(
                    self._entry.stat(follow_symlinks=follow_symlinks)
                )

            def is_symlink(self) -> bool:
                return self._entry.is_symlink()

        class ZeroedScan:
            def __init__(self, path: object) -> None:
                self._scan = real_scandir(path)

            def __enter__(self) -> object:
                return (ZeroedEntry(entry) for entry in self._scan.__enter__())

            def __exit__(self, *arguments: object) -> object:
                return self._scan.__exit__(*arguments)

        def windows_path_identity(path: Path, metadata: object) -> tuple[int, int, int]:
            with mock.patch.object(a2_bundle.os, "name", "nt"):
                return real_path_identity(path, metadata)

        with mock.patch.object(a2_bundle.os, "scandir", ZeroedScan), mock.patch.object(
            a2_bundle, "_path_identity", side_effect=windows_path_identity
        ) as identity:
            tree, directories = a2_bundle._inventory(root)

        self.assertEqual(directories, set())
        self.assertEqual(
            (tree["artifact.json"].device, tree["artifact.json"].inode),
            (actual.st_dev, actual.st_ino),
        )
        self.assertEqual(tree["artifact.json"].links, actual.st_nlink)
        identity.assert_called_once()

    def test_complete_validator_rejects_modified_artifact(self) -> None:
        corrupted = self.root / "corrupted-bundle"
        shutil.copytree(self.bundle, corrupted)
        report = corrupted / "analysis" / "analysis-report.json"
        report.write_bytes(report.read_bytes() + b" ")
        with self.assertRaisesRegex(ValidationError, "size|sha256"):
            validate_bundle(corrupted, self.campaign_id, self.producer_commit)

    def test_complete_validator_rejects_explicit_binding_mismatch(self) -> None:
        with self.assertRaisesRegex(ValidationError, "expected campaign"):
            validate_bundle(
                self.bundle, "a2-wrong-campaign", self.producer_commit)
        with self.assertRaisesRegex(ValidationError, "expected producer commit"):
            validate_bundle(self.bundle, self.campaign_id, "f" * 40)

    def test_finalize_refuses_manifest_replacement(self) -> None:
        with self.assertRaisesRegex(ValidationError, "already exists"):
            finalize_bundle(
                self.bundle, self.campaign_id, self.producer_commit)


if __name__ == "__main__":
    unittest.main()
