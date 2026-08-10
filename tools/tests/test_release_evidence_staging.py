from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from validation import release_evidence as evidence  # noqa: E402
from validation import release_evidence_adapters as adapter_contract  # noqa: E402

PROJECT = TOOLS.parent
CONTRACT_FILES = (
    "docs/validation/schema/evidence-policy.schema.json",
    "docs/validation/schema/release-evidence-overlay.schema.json",
    "docs/validation/evidence-policy.json",
)


def canonical_json(value: object) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def test_adapter(
    item: dict[str, object],
    files: tuple[evidence.ResolvedFile, ...],
    commit: str,
    limits: evidence.Limits,
) -> dict[str, object]:
    del limits
    return {
        "adapter": "structural_manifest_v1",
        "capability_id": item["capability_id"],
        "commit": commit,
        "file_sha256": files[0].sha256,
        "scenario_count": 1,
        "status": "PASS",
    }


class ReleaseEvidenceStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.bundle = self.root / "bundle"
        self.repo.mkdir()
        for relative in CONTRACT_FILES:
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT / relative, destination)
        policy_path = self.repo / evidence.POLICY_PATH
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        next(
            item
            for item in policy["adapters"]
            if item["id"] == "structural_manifest_v1"
        )["status"] = "enabled"
        self._write_json(policy_path, policy)
        original_resolver = adapter_contract.checked_adapter_spec

        def test_resolver(adapter_id: str) -> adapter_contract.AdapterSpec | None:
            if adapter_id == "structural_manifest_v1":
                return adapter_contract.AdapterSpec(
                    id=adapter_id,
                    artifact_kind="test_fixture",
                    exact_verification="internal_only",
                    availability="available",
                    implementation=test_adapter,
                )
            return original_resolver(adapter_id)

        patcher = mock.patch.object(
            adapter_contract, "checked_adapter_spec", side_effect=test_resolver
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self._git("init", "-q")
        self._git("config", "user.email", "tests@example.invalid")
        self._git("config", "user.name", "Release Evidence Tests")
        self._git("add", ".")
        self._git("commit", "-qm", "test contracts")
        self.commit = self._git("rev-parse", "HEAD")
        self._make_bundle()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _hash(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(value), encoding="utf-8", newline="\n")

    def _make_bundle(self) -> None:
        self.bundle.mkdir()
        manifest = {
            "schema_version": 1,
            "commit": self.commit,
            "dirty": False,
            "capability_id": "format.header_and_version",
            "scenario_ids": ["UT-HEADER-READ"],
        }
        self._write_json(self.bundle / "manifest.json", manifest)
        manifest_hash = self._hash(self.bundle / "manifest.json")
        self.overlay = {
            "schema_version": 1,
            "repository": {"commit": self.commit, "dirty": False},
            "contracts": {
                "overlay_schema_path": evidence.OVERLAY_SCHEMA_PATH,
                "overlay_schema_sha256": self._hash(
                    self.repo / evidence.OVERLAY_SCHEMA_PATH
                ),
                "policy_path": evidence.POLICY_PATH,
                "policy_sha256": self._hash(self.repo / evidence.POLICY_PATH),
            },
            "files": [
                {
                    "path": "manifest.json",
                    "sha256": manifest_hash,
                    "size": (self.bundle / "manifest.json").stat().st_size,
                }
            ],
            "evidence": [
                {
                    "id": "format.header.internal",
                    "capability_id": "format.header_and_version",
                    "verification": "internal_only",
                    "adapter": "structural_manifest_v1",
                    "files": ["manifest.json"],
                    "expected_output": {
                        "adapter": "structural_manifest_v1",
                        "capability_id": "format.header_and_version",
                        "commit": self.commit,
                        "file_sha256": manifest_hash,
                        "scenario_count": 1,
                        "status": "PASS",
                    },
                }
            ],
        }
        self._write_json(self.bundle / evidence.OVERLAY_NAME, self.overlay)

    def _refresh_file(self, relative: str) -> None:
        path = self.bundle / relative
        self.overlay["files"][0].update(
            {"path": relative, "sha256": self._hash(path), "size": path.stat().st_size}
        )
        self.overlay["evidence"][0]["files"] = [relative]
        self.overlay["evidence"][0]["expected_output"]["file_sha256"] = self._hash(
            path
        )
        self._write_json(self.bundle / evidence.OVERLAY_NAME, self.overlay)

    def _private_stages(self, destination: Path) -> list[Path]:
        return list(destination.parent.glob(f"{destination.name}-stage-*"))

    def test_stage_overlay_copies_and_revalidates_without_overwrite(self) -> None:
        destination = self.root / "staged"
        staged = evidence.stage_overlay(self.repo, self.bundle, destination)
        self.assertEqual(staged.commit, self.commit)
        self.assertEqual(
            (destination / evidence.OVERLAY_NAME).read_bytes(),
            (self.bundle / evidence.OVERLAY_NAME).read_bytes(),
        )
        with self.assertRaisesRegex(
            evidence.ReleaseEvidenceError, "refusing to overwrite"
        ):
            evidence.stage_overlay(self.repo, self.bundle, destination)

    def test_stage_uses_manifest_last_private_atomic_publication(self) -> None:
        destination = self.root / "ordered-stage"
        copied: list[str] = []
        original_copy = evidence._copy_file_exclusive

        def observe_copy(source: evidence.ResolvedFile, target: Path) -> None:
            copied.append(source.relative_path)
            original_copy(source, target)

        with mock.patch.object(evidence, "_copy_file_exclusive", observe_copy):
            evidence.stage_overlay(self.repo, self.bundle, destination)
        self.assertEqual(copied, ["manifest.json", evidence.OVERLAY_NAME])
        self.assertFalse(
            list(destination.parent.glob(f"{destination.name}-stage-*"))
        )

    def test_stage_can_publish_under_repository_acceptance_artifacts(self) -> None:
        parent = self.repo / "artifacts/acceptance"
        parent.mkdir(parents=True)
        destination = parent / "release-evidence"
        staged = evidence.stage_overlay(self.repo, self.bundle, destination)
        self.assertEqual(staged.root, destination.resolve())
        self.assertTrue((destination / evidence.OVERLAY_NAME).is_file())

    def test_stage_rejects_other_in_repository_destinations(self) -> None:
        destination = self.repo / "artifacts/release-evidence"
        destination.parent.mkdir(parents=True)
        with self.assertRaisesRegex(
            evidence.ReleaseEvidenceError,
            "restricted to artifacts/acceptance",
        ):
            evidence.stage_overlay(self.repo, self.bundle, destination)

    def test_windows_staging_is_explicitly_unavailable_until_handle_safe(self) -> None:
        with mock.patch.object(evidence.os, "name", "nt"):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError,
                "unavailable on Windows",
            ):
                evidence.stage_overlay(
                    self.repo,
                    self.bundle,
                    self.root / "windows-stage",
                )

    def test_public_selector_still_rejects_in_repository_staged_overlay(self) -> None:
        parent = self.repo / "artifacts/acceptance"
        parent.mkdir(parents=True)
        destination = parent / "release-evidence"
        evidence.stage_overlay(self.repo, self.bundle, destination)
        with self.assertRaisesRegex(
            evidence.ReleaseEvidenceError, "must be outside the repository"
        ):
            evidence.validate_overlay(self.repo, destination)

    def test_stage_overlay_preserves_nested_inventory(self) -> None:
        nested = self.bundle / "payload/manifest.json"
        nested.parent.mkdir()
        (self.bundle / "manifest.json").rename(nested)
        self._refresh_file("payload/manifest.json")
        destination = self.root / "nested-stage"
        evidence.stage_overlay(self.repo, self.bundle, destination)
        self.assertEqual(
            nested.read_bytes(),
            (destination / "payload/manifest.json").read_bytes(),
        )

    def test_stage_refuses_symlink_destination(self) -> None:
        destination = self.root / "destination-link"
        destination.symlink_to(self.root / "missing", target_is_directory=True)
        with self.assertRaisesRegex(
            evidence.ReleaseEvidenceError, "refusing to overwrite"
        ):
            evidence.stage_overlay(self.repo, self.bundle, destination)

    def test_stage_failure_retains_private_stage_for_manual_inspection(self) -> None:
        destination = self.root / "failed-stage"
        original_copy = evidence._copy_file_exclusive
        calls = 0

        def fail_manifest(source: evidence.ResolvedFile, target: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise evidence.ReleaseEvidenceError("injected copy failure")
            original_copy(source, target)

        with mock.patch.object(evidence, "_copy_file_exclusive", fail_manifest):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError,
                "private stage retained.*injected copy failure",
            ):
                evidence.stage_overlay(self.repo, self.bundle, destination)
        self.assertFalse(destination.exists())
        self.assertTrue(self.bundle.exists())
        retained = self._private_stages(destination)
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].stat().st_mode & 0o777, 0o700)

    def test_real_atomic_publication_collision_preserves_intruder_and_stage(
        self,
    ) -> None:
        destination = self.root / "publication-race"
        original_publish = evidence._atomic_publish_no_replace

        def collide(staged: Path, final: Path) -> None:
            final.mkdir()
            (final / "intruder").write_text("owned elsewhere", encoding="utf-8")
            original_publish(staged, final)

        with mock.patch.object(evidence, "_atomic_publish_no_replace", collide):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError,
                "private stage retained.*atomic no-replace publication failed",
            ):
                evidence.stage_overlay(self.repo, self.bundle, destination)
        self.assertEqual(
            (destination / "intruder").read_text(encoding="utf-8"),
            "owned elsewhere",
        )
        self.assertEqual(len(self._private_stages(destination)), 1)

    def test_private_stage_replacement_never_deletes_replacement_victim(self) -> None:
        destination = self.root / "replacement-swap"
        victim = self.root / "victim"
        victim.mkdir()
        marker = victim / "must-survive.txt"
        marker.write_text("survive\n", encoding="utf-8")
        original_copy = evidence._copy_file_exclusive
        calls = 0

        def swap_after_payload(source: evidence.ResolvedFile, target: Path) -> None:
            nonlocal calls
            original_copy(source, target)
            calls += 1
            if calls != 1:
                return
            private = target.parent
            displaced = private.with_name(f"{private.name}-displaced")
            private.rename(displaced)
            private.symlink_to(victim, target_is_directory=True)

        with mock.patch.object(evidence, "_copy_file_exclusive", swap_after_payload):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError, "private stage retained"
            ):
                evidence.stage_overlay(self.repo, self.bundle, destination)
        self.assertEqual(marker.read_text(encoding="utf-8"), "survive\n")
        self.assertFalse(destination.exists())

    def test_destination_parent_replacement_fails_closed(self) -> None:
        parent = self.root / "original-parent"
        parent.mkdir()
        destination = parent / "published"
        displaced_parent = self.root / "displaced-parent"
        replacement_parent = self.root / "replacement-parent"
        original_copy = evidence._copy_file_exclusive
        calls = 0

        def replace_parent(source: evidence.ResolvedFile, target: Path) -> None:
            nonlocal calls
            original_copy(source, target)
            calls += 1
            if calls != 1:
                return
            parent.rename(displaced_parent)
            replacement_parent.mkdir()
            replacement_parent.rename(parent)

        with mock.patch.object(evidence, "_copy_file_exclusive", replace_parent):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError,
                "private stage retained.*private staging root: cannot inspect",
            ):
                evidence.stage_overlay(self.repo, self.bundle, destination)
        self.assertFalse(destination.exists())
        self.assertTrue(
            list(displaced_parent.glob(f"{destination.name}-stage-*"))
        )

    def test_stage_rejects_cross_volume_before_copy(self) -> None:
        resolved = evidence.validate_overlay(self.repo, self.bundle)
        destination = self.root / "cross-volume"
        private_metadata = mock.Mock(st_dev=1)
        parent_metadata = mock.Mock(st_dev=2)
        identities = ["parent", "parent", "private"]
        with (
            mock.patch.object(evidence, "validate_overlay", return_value=resolved),
            mock.patch.object(
                evidence,
                "_trusted_staging_parent",
                return_value=parent_metadata,
            ),
            mock.patch.object(
                evidence,
                "_directory_metadata",
                side_effect=[
                    parent_metadata,
                    private_metadata,
                ],
            ),
            mock.patch.object(
                evidence, "_stable_object_identity", side_effect=identities
            ),
        ):
            with self.assertRaisesRegex(evidence.ReleaseEvidenceError, "same volume"):
                evidence.stage_overlay(self.repo, self.bundle, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(len(self._private_stages(destination)), 1)


if __name__ == "__main__":
    unittest.main()
