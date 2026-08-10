from __future__ import annotations

import copy
import hashlib
import json
import os
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


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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


class ReleaseEvidenceTests(unittest.TestCase):
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
        self.original_adapter_resolver = original_resolver
        self._git("init", "-q")
        self._git("config", "user.email", "tests@example.invalid")
        self._git("config", "user.name", "Release Evidence Tests")
        self._git("add", ".")
        self._git("commit", "-qm", "test contracts")
        self.commit = self._git("rev-parse", "HEAD")
        self._make_bundle()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _contract_hash(self, relative: str) -> str:
        return sha256((self.repo / relative).read_bytes())

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
        manifest_content = canonical_json(manifest).encode()
        (self.bundle / "manifest.json").write_bytes(manifest_content)
        manifest_hash = sha256(manifest_content)
        self.overlay = {
            "schema_version": 1,
            "repository": {"commit": self.commit, "dirty": False},
            "contracts": {
                "overlay_schema_path": evidence.OVERLAY_SCHEMA_PATH,
                "overlay_schema_sha256": self._contract_hash(
                    evidence.OVERLAY_SCHEMA_PATH
                ),
                "policy_path": evidence.POLICY_PATH,
                "policy_sha256": self._contract_hash(evidence.POLICY_PATH),
            },
            "files": [
                {
                    "path": "manifest.json",
                    "sha256": manifest_hash,
                    "size": len(manifest_content),
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
        self._flush_overlay()

    def _flush_overlay(self) -> None:
        self._write_json(self.bundle / evidence.OVERLAY_NAME, self.overlay)

    def _refresh_file(self, relative: str) -> None:
        content = (self.bundle / relative).read_bytes()
        for item in self.overlay["files"]:
            if item["path"] == relative:
                item["size"] = len(content)
                item["sha256"] = sha256(content)
        for item in self.overlay["evidence"]:
            if (
                item["adapter"] == "structural_manifest_v1"
                and item["files"] == [relative]
            ):
                item["expected_output"]["file_sha256"] = sha256(content)
        self._flush_overlay()

    def _commit_policy_mutation(self, mutate: object) -> None:
        path = self.repo / evidence.POLICY_PATH
        policy = json.loads(path.read_text(encoding="utf-8"))
        mutate(policy)
        self._write_json(path, policy)
        self._git("add", ".")
        self._git("commit", "-qm", "mutate policy")
        self._rebind_bundle_to_head()

    def _rebind_bundle_to_head(self) -> None:
        self.commit = self._git("rev-parse", "HEAD")
        self.overlay["repository"]["commit"] = self.commit
        self.overlay["contracts"]["policy_sha256"] = self._contract_hash(
            evidence.POLICY_PATH
        )
        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["commit"] = self.commit
        self._write_json(manifest_path, manifest)
        self.overlay["evidence"][0]["expected_output"]["commit"] = self.commit
        self._refresh_file("manifest.json")

    def assert_invalid(self, pattern: str) -> None:
        self._flush_overlay()
        with self.assertRaisesRegex(evidence.ReleaseEvidenceError, pattern):
            evidence.validate_overlay(self.repo, self.bundle)

    def test_valid_overlay_resolves_exact_files_and_outputs(self) -> None:
        resolved = evidence.validate_overlay(self.repo, self.bundle)
        self.assertEqual(resolved.commit, self.commit)
        self.assertEqual(
            [(item.relative_path, item.size) for item in resolved.files],
            [("manifest.json", (self.bundle / "manifest.json").stat().st_size)],
        )
        self.assertEqual(resolved.outputs[0][0], "format.header.internal")

    def test_overlay_must_be_detached_from_repository(self) -> None:
        internal = self.repo / "artifacts/evidence"
        shutil.copytree(self.bundle, internal)
        with self.assertRaisesRegex(
            evidence.ReleaseEvidenceError, "must be outside the repository"
        ):
            evidence.validate_overlay(self.repo, internal)

    def test_overlay_root_symlink_is_rejected(self) -> None:
        linked = self.root / "linked-bundle"
        linked.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaisesRegex(
            evidence.ReleaseEvidenceError, "links and reparse points are forbidden"
        ):
            evidence.validate_overlay(self.repo, linked)

    def test_exact_head_and_clean_worktree_are_required(self) -> None:
        self.overlay["repository"]["commit"] = "a" * 40
        self.assert_invalid("expected current HEAD")
        self.overlay["repository"]["commit"] = self.commit
        (self.repo / "dirty").write_text("dirty", encoding="utf-8")
        self.assert_invalid("requires a clean worktree")

    def test_acceptance_output_siblings_do_not_dirty_exact_head(self) -> None:
        sibling = self.repo / "artifacts/acceptance/earlier-gate/report.json"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("{}\n", encoding="utf-8")
        resolved = evidence.validate_overlay(self.repo, self.bundle)
        self.assertEqual(resolved.commit, self.commit)

    def test_tracked_production_change_after_adapter_fails_final_closure(self) -> None:
        original = evidence._run_evidence_adapters

        def run_then_dirty(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            (self.repo / evidence.POLICY_PATH).write_text("{}\n", encoding="utf-8")
            return result

        with mock.patch.object(evidence, "_run_evidence_adapters", run_then_dirty):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError, "clean index/worktree"
            ):
                evidence.validate_overlay(self.repo, self.bundle)

    def test_head_advance_after_adapter_fails_final_closure(self) -> None:
        original = evidence._run_evidence_adapters

        def run_then_commit(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            path = self.repo / "advance.txt"
            path.write_text("new HEAD\n", encoding="utf-8")
            self._git("add", "advance.txt")
            self._git("commit", "-qm", "advance during validation")
            return result

        with mock.patch.object(evidence, "_run_evidence_adapters", run_then_commit):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError, "expected current HEAD"
            ):
                evidence.validate_overlay(self.repo, self.bundle)

    def test_tracked_acceptance_change_is_not_excluded(self) -> None:
        tracked = self.repo / "artifacts/acceptance/tracked/report.json"
        tracked.parent.mkdir(parents=True)
        tracked.write_text("{}\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "track acceptance output")
        self._rebind_bundle_to_head()
        original = evidence._run_evidence_adapters

        def run_then_dirty(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            tracked.write_text('{"changed":true}\n', encoding="utf-8")
            return result

        with mock.patch.object(evidence, "_run_evidence_adapters", run_then_dirty):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError, "clean index/worktree"
            ):
                evidence.validate_overlay(self.repo, self.bundle)

    def test_head_change_inside_clean_check_is_rejected(self) -> None:
        with (
            mock.patch.object(
                evidence, "git_head", side_effect=[self.commit, "f" * 40]
            ),
            mock.patch.object(evidence, "git_has_gitlinks", return_value=False),
            mock.patch.object(evidence, "git_status_tracked", return_value=b""),
            mock.patch.object(evidence, "git_status_untracked", return_value=b""),
        ):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError, "HEAD changed during"
            ):
                evidence._clean_exact_head(self.repo, self.commit)

    def test_overlay_declared_dirty_is_rejected(self) -> None:
        self.overlay["repository"]["dirty"] = True
        self.assert_invalid("must declare false")

    def test_contract_hashes_and_paths_are_exact(self) -> None:
        mutations = (
            ("overlay_schema_sha256", "0" * 64, "Git blob hash mismatch"),
            ("policy_sha256", "0" * 64, "Git blob hash mismatch"),
            ("overlay_schema_path", "wrong.json", "expected"),
            ("policy_path", "wrong.json", "expected"),
        )
        for field, value, message in mutations:
            with self.subTest(field=field):
                original = self.overlay["contracts"][field]
                self.overlay["contracts"][field] = value
                self.assert_invalid(message)
                self.overlay["contracts"][field] = original

    def test_unknown_disabled_and_forbidden_adapters_fail_closed(self) -> None:
        cases = (
            ("unknown_adapter_v1", "unknown adapter"),
            ("ci_g1_aggregate_v1", "is disabled"),
            ("m4_descriptive_v1", "is forbidden"),
        )
        for adapter, message in cases:
            with self.subTest(adapter=adapter):
                self.overlay["evidence"][0]["adapter"] = adapter
                self.assert_invalid(message)
        self.overlay["evidence"][0]["adapter"] = "structural_manifest_v1"

    def test_production_policy_enables_no_false_evidence_adapter(self) -> None:
        policy = json.loads(
            (PROJECT / evidence.POLICY_PATH).read_text(encoding="utf-8")
        )
        self.assertFalse(
            [item for item in policy["adapters"] if item["status"] == "enabled"]
        )
        structural = self.original_adapter_resolver("structural_manifest_v1")
        self.assertIsNotNone(structural)
        assert structural is not None
        self.assertEqual(structural.availability, "unavailable")
        self.assertIsNone(structural.implementation)

    def test_zero_enabled_adapter_policy_rejects_every_claim(self) -> None:
        def disable_test_adapter(policy: dict[str, object]) -> None:
            next(
                item
                for item in policy["adapters"]
                if item["id"] == "structural_manifest_v1"
            )["status"] = "disabled"

        self._commit_policy_mutation(disable_test_adapter)
        self.assert_invalid("adapter 'structural_manifest_v1' is disabled")

    def test_descriptive_experiment_cannot_be_enabled_by_policy(self) -> None:
        def mutate(policy: dict[str, object]) -> None:
            next(
                item
                for item in policy["adapters"]
                if item["id"] == "m3_descriptive_v1"
            )["status"] = "enabled"

        self._commit_policy_mutation(mutate)
        self.assert_invalid("intrinsically forbidden adapter")

    def test_adapter_verification_and_output_are_exact(self) -> None:
        self.overlay["evidence"][0]["verification"] = "dao_opened"
        self.assert_invalid("intrinsic adapter mismatch")
        self.overlay["evidence"][0]["verification"] = "internal_only"
        self.overlay["evidence"][0]["expected_output"]["scenario_count"] = True
        self.assert_invalid("adapter output mismatch")

    def test_policy_cannot_relabel_dao_differential_adapter(self) -> None:
        spec = self.original_adapter_resolver("dao_differential_v1")
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.artifact_kind, "dao_bundle")
        self.assertEqual(spec.exact_verification, "dao_differential")

        def mutate(policy: dict[str, object]) -> None:
            adapter = next(
                item
                for item in policy["adapters"]
                if item["id"] == "dao_differential_v1"
            )
            adapter["artifact_kind"] = "structural_manifest"
            adapter["permitted_verification"] = "internal_only"

        self._commit_policy_mutation(mutate)
        self.assert_invalid("unknown properties")

    def test_top_level_and_nested_unknown_keys_fail(self) -> None:
        self.overlay["surprise"] = True
        self.assert_invalid("unknown properties")
        del self.overlay["surprise"]
        self.overlay["files"][0]["surprise"] = True
        self.assert_invalid("unknown properties")

    def test_overlay_scalar_and_membership_types_are_strict(self) -> None:
        baseline = copy.deepcopy(self.overlay)
        cases = (
            (
                lambda item: item.__setitem__("schema_version", True),
                "expected integer",
            ),
            (
                lambda item: item["repository"].__setitem__("dirty", 0),
                "must declare false",
            ),
            (
                lambda item: item["files"][0].__setitem__("size", True),
                "expected nonnegative integer",
            ),
            (
                lambda item: item["evidence"][0].__setitem__("verification", 1),
                "invalid verification level",
            ),
            (
                lambda item: item["evidence"][0].__setitem__("files", [1]),
                "expected canonical repository-style relative path",
            ),
        )
        for mutate, message in cases:
            with self.subTest(message=message):
                self.overlay = copy.deepcopy(baseline)
                mutate(self.overlay)
                self.assert_invalid(message)
        self.overlay = baseline

    def test_duplicate_json_properties_and_nonfinite_numbers_fail(self) -> None:
        overlay_path = self.bundle / evidence.OVERLAY_NAME
        overlay_path.write_text(
            '{"schema_version":1,"schema_version":1}\n', encoding="utf-8"
        )
        with self.assertRaisesRegex(
            evidence.ReleaseEvidenceError, "duplicate property"
        ):
            evidence.validate_overlay(self.repo, self.bundle)
        overlay_path.write_text('{"schema_version":NaN}\n', encoding="utf-8")
        with self.assertRaisesRegex(
            evidence.ReleaseEvidenceError, "non-finite JSON number"
        ):
            evidence.validate_overlay(self.repo, self.bundle)
        overlay_path.write_text(
            '{"schema_version":1e999}\n', encoding="utf-8"
        )
        with self.assertRaisesRegex(
            evidence.ReleaseEvidenceError, "non-finite JSON number"
        ):
            evidence.validate_overlay(self.repo, self.bundle)

    def test_excessively_nested_json_is_a_structured_failure(self) -> None:
        (self.bundle / evidence.OVERLAY_NAME).write_text(
            "[" * 2000 + "]" * 2000,
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            evidence.ReleaseEvidenceError, "JSON depth limit exceeded"
        ):
            evidence.validate_overlay(self.repo, self.bundle)

    def test_paths_reject_traversal_absolute_backslash_and_dot_components(self) -> None:
        for path in ("../manifest.json", "/manifest.json", "a\\b.json", "./x.json"):
            with self.subTest(path=path):
                self.overlay["files"][0]["path"] = path
                self.assert_invalid("canonical repository-style relative path")

    def test_paths_reject_windows_reserved_components_on_every_platform(self) -> None:
        for path in ("CON", "aux.json", "safe/LPT9.txt"):
            with self.subTest(path=path):
                self.overlay["files"][0]["path"] = path
                self.assert_invalid("Windows reserved path component")

    def test_file_inventory_requires_sorted_unique_exact_complete_entries(self) -> None:
        extra = self.bundle / "extra.json"
        extra.write_text("{}\n", encoding="utf-8")
        self.assert_invalid("inventory is not exact and complete")
        extra.unlink()
        self.overlay["files"].append(copy.deepcopy(self.overlay["files"][0]))
        self.assert_invalid("unique and sorted")

    def test_file_size_and_hash_tampering_fail(self) -> None:
        self.overlay["files"][0]["size"] += 1
        self.assert_invalid("size mismatch")
        self.overlay["files"][0]["size"] -= 1
        self.overlay["files"][0]["sha256"] = "0" * 64
        self.assert_invalid("SHA-256 mismatch")

    def test_every_file_must_be_referenced(self) -> None:
        extra = self.bundle / "extra.json"
        extra.write_text("{}\n", encoding="utf-8")
        content = extra.read_bytes()
        self.overlay["files"].append(
            {"path": "extra.json", "sha256": sha256(content), "size": len(content)}
        )
        self.overlay["files"].sort(key=lambda item: item["path"])
        self.assert_invalid("every inventoried file must be referenced")

    def test_case_collisions_are_rejected(self) -> None:
        upper = self.bundle / "MANIFEST.json"
        if upper.exists():
            self.skipTest("filesystem does not permit distinct case-colliding names")
        upper.write_text("{}\n", encoding="utf-8")
        self.assert_invalid("case-colliding paths")

    def test_empty_directories_are_rejected(self) -> None:
        (self.bundle / "empty").mkdir()
        self.assert_invalid("empty directories forbidden")

    def test_file_and_directory_symlinks_are_rejected(self) -> None:
        target = self.root / "outside.json"
        target.write_text("{}\n", encoding="utf-8")
        (self.bundle / "linked.json").symlink_to(target)
        self.assert_invalid("links are forbidden")
        (self.bundle / "linked.json").unlink()
        target_dir = self.root / "outside-dir"
        target_dir.mkdir()
        (self.bundle / "linked-dir").symlink_to(target_dir, target_is_directory=True)
        self.assert_invalid("links are forbidden")

    def test_hard_linked_payload_is_rejected(self) -> None:
        target = self.root / "outside-hardlink.json"
        target.write_text("{}\n", encoding="utf-8")
        try:
            os.link(target, self.bundle / "linked.json")
        except OSError as error:
            self.skipTest(f"hard links unavailable: {error}")
        self.assert_invalid("hard-linked files are forbidden")

    @unittest.skipIf(os.name == "nt", "POSIX FIFO corruption test")
    def test_special_files_are_rejected(self) -> None:
        os.mkfifo(self.bundle / "pipe")
        self.assert_invalid("special files forbidden")

    def test_policy_adapter_order_is_checked(self) -> None:
        self._commit_policy_mutation(lambda policy: policy["adapters"].reverse())
        self.assert_invalid("exact sorted intrinsic adapter inventory")

    def test_policy_adapter_ids_are_unique(self) -> None:
        self._commit_policy_mutation(
            lambda policy: policy["adapters"].append(
                copy.deepcopy(policy["adapters"][0])
            )
        )
        self.assert_invalid("exact sorted intrinsic adapter inventory")

    def test_policy_numeric_bounds_are_checked(self) -> None:
        self._commit_policy_mutation(
            lambda policy: policy["limits"].__setitem__("max_file_count", 0)
        )
        self.assert_invalid("outside")

    def test_policy_schema_hash_is_commit_bound(self) -> None:
        self._commit_policy_mutation(
            lambda policy: policy["schema"].__setitem__("sha256", "0" * 64)
        )
        self.assert_invalid("Git blob hash mismatch")

    def test_checked_file_and_evidence_count_bounds_apply(self) -> None:
        self._commit_policy_mutation(
            lambda policy: policy["limits"].__setitem__("max_file_count", 1)
        )
        extra = self.bundle / "extra.json"
        extra.write_text("{}\n", encoding="utf-8")
        self.assert_invalid("file-count limit exceeded")

    def test_checked_evidence_count_bound_applies(self) -> None:
        self._commit_policy_mutation(
            lambda policy: policy["limits"].__setitem__("max_evidence_count", 1)
        )
        duplicate = copy.deepcopy(self.overlay["evidence"][0])
        duplicate["id"] = "writer.header.internal"
        self.overlay["evidence"].append(duplicate)
        self.assert_invalid("invalid evidence count")

    def test_checked_files_per_evidence_bound_applies(self) -> None:
        self._commit_policy_mutation(
            lambda policy: policy["limits"].__setitem__(
                "max_files_per_evidence", 1
            )
        )
        self.overlay["evidence"][0]["files"].append("manifest.json")
        self.assert_invalid("invalid file count")

    def test_checked_adapter_file_visit_bound_applies_across_entries(self) -> None:
        self._commit_policy_mutation(
            lambda policy: policy["limits"].__setitem__(
                "max_adapter_file_visits", 1
            )
        )
        duplicate = copy.deepcopy(self.overlay["evidence"][0])
        duplicate["id"] = "writer.header.internal"
        self.overlay["evidence"].append(duplicate)
        self.assert_invalid("adapter file-visit limit exceeded")

    def test_checked_adapter_input_byte_bound_applies_across_entries(self) -> None:
        manifest_size = (self.bundle / "manifest.json").stat().st_size
        self._commit_policy_mutation(
            lambda policy: policy["limits"].__setitem__(
                "max_adapter_input_bytes", manifest_size
            )
        )
        duplicate = copy.deepcopy(self.overlay["evidence"][0])
        duplicate["id"] = "writer.header.internal"
        self.overlay["evidence"].append(duplicate)
        self.assert_invalid("adapter input-byte limit exceeded")

    def test_checked_per_file_and_total_byte_bounds_apply_before_adapters(self) -> None:
        def mutate(policy: dict[str, object]) -> None:
            policy["limits"]["max_file_bytes"] = 100
            policy["limits"]["max_total_file_bytes"] = 100

        self._commit_policy_mutation(mutate)
        (self.bundle / "manifest.json").write_bytes(b"x" * 101)
        self.assert_invalid("file exceeds 100 byte limit")

    def test_checked_total_byte_bound_applies_across_files(self) -> None:
        def mutate(policy: dict[str, object]) -> None:
            policy["limits"]["max_file_bytes"] = 100
            policy["limits"]["max_total_file_bytes"] = 120

        self._commit_policy_mutation(mutate)
        first = self.bundle / "manifest.json"
        first.write_bytes(b"x" * 70)
        second = self.bundle / "second.json"
        second.write_bytes(b"y" * 70)
        self.assert_invalid("total-byte limit exceeded")

    def test_checked_overlay_size_bound_applies_before_inventory(self) -> None:
        self._commit_policy_mutation(
            lambda policy: policy["limits"].__setitem__("max_overlay_bytes", 1024)
        )
        self.overlay["evidence"][0]["expected_output"]["padding"] = "x" * 200
        self._flush_overlay()
        self.assertGreater(
            (self.bundle / evidence.OVERLAY_NAME).stat().st_size, 1024
        )
        self.assert_invalid("checked size limit exceeded")

    def test_json_depth_and_node_bounds_apply(self) -> None:
        self._commit_policy_mutation(
            lambda policy: policy["limits"].__setitem__("max_json_depth", 3)
        )
        self.assert_invalid("JSON depth limit exceeded")

    def test_json_node_bound_applies(self) -> None:
        self._commit_policy_mutation(
            lambda policy: policy["limits"].__setitem__("max_json_nodes", 1)
        )
        self.assert_invalid("JSON node limit exceeded")

    def test_evidence_ids_and_file_references_are_sorted_unique(self) -> None:
        duplicate = copy.deepcopy(self.overlay["evidence"][0])
        duplicate["id"] = "a.first"
        self.overlay["evidence"].append(duplicate)
        self.assert_invalid("IDs must be unique and sorted")
        self.overlay["evidence"] = [duplicate, copy.deepcopy(duplicate)]
        self.assert_invalid("IDs must be unique and sorted")

    def test_missing_overlay_is_rejected_without_fallback(self) -> None:
        (self.bundle / evidence.OVERLAY_NAME).unlink()
        with self.assertRaisesRegex(evidence.ReleaseEvidenceError, "cannot inspect file"):
            evidence.validate_overlay(self.repo, self.bundle)

    def test_hard_overlay_size_ceiling_applies_before_json_parsing(self) -> None:
        path = self.bundle / evidence.OVERLAY_NAME
        with path.open("wb") as destination:
            destination.truncate(evidence.HARD_MAX_OVERLAY_BYTES + 1)
        with self.assertRaisesRegex(evidence.ReleaseEvidenceError, "file exceeds"):
            evidence.validate_overlay(self.repo, self.bundle)

    def test_file_change_after_inventory_resolution_is_rejected(self) -> None:
        original_scan = evidence._scan_regular_files

        def scan_then_change(
            root: Path, limits: evidence.Limits
        ) -> dict[str, evidence.ResolvedFile]:
            resolved = original_scan(root, limits)
            manifest = json.loads(
                (self.bundle / "manifest.json").read_text(encoding="utf-8")
            )
            manifest["scenario_ids"] = ["UT-HEADER-NEW"]
            self._write_json(self.bundle / "manifest.json", manifest)
            return resolved

        with mock.patch.object(evidence, "_scan_regular_files", scan_then_change):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError, "changed during validation"
            ):
                evidence.validate_overlay(self.repo, self.bundle)

    def test_overlay_change_after_adapters_is_rejected_by_snapshot_closure(
        self,
    ) -> None:
        original = evidence._run_evidence_adapters

        def run_then_change(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            (self.bundle / evidence.OVERLAY_NAME).write_text(
                canonical_json(self.overlay) + " ",
                encoding="utf-8",
            )
            return result

        with mock.patch.object(evidence, "_run_evidence_adapters", run_then_change):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError, "changed during validation"
            ):
                evidence.validate_overlay(self.repo, self.bundle)

    def test_same_byte_overlay_replacement_is_rejected_by_identity(self) -> None:
        original = evidence._run_evidence_adapters

        def run_then_replace(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            overlay = self.bundle / evidence.OVERLAY_NAME
            replacement = self.root / "replacement-overlay.json"
            replacement.write_bytes(overlay.read_bytes())
            os.replace(replacement, overlay)
            return result

        with mock.patch.object(evidence, "_run_evidence_adapters", run_then_replace):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError, "changed during validation"
            ):
                evidence.validate_overlay(self.repo, self.bundle)

    def test_same_byte_payload_replacement_is_rejected_by_identity(self) -> None:
        original = evidence._run_evidence_adapters

        def run_then_replace(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            manifest = self.bundle / "manifest.json"
            replacement = self.root / "replacement-manifest.json"
            replacement.write_bytes(manifest.read_bytes())
            os.replace(replacement, manifest)
            return result

        with mock.patch.object(evidence, "_run_evidence_adapters", run_then_replace):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError, "changed during validation"
            ):
                evidence.validate_overlay(self.repo, self.bundle)

    def test_extra_file_after_adapters_is_rejected_by_snapshot_closure(self) -> None:
        original = evidence._run_evidence_adapters

        def run_then_add(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            (self.bundle / "late.json").write_text("{}\n", encoding="utf-8")
            return result

        with mock.patch.object(evidence, "_run_evidence_adapters", run_then_add):
            with self.assertRaisesRegex(
                evidence.ReleaseEvidenceError, "changed during validation"
            ):
                evidence.validate_overlay(self.repo, self.bundle)

if __name__ == "__main__":
    unittest.main()
