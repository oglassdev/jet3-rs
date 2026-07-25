#!/usr/bin/env python3
"""Focused corruption and projection tests for the checked M4 validator."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from m4_records import (  # noqa: E402
    CHECKED_PLAN,
    ValidationError,
    _validate_lexical_windows_root,
    load_bounded_json,
    load_checked_plan,
    resolve_bundle_path,
    validate_invocation_document,
    validate_plan_document,
)
from m4_bundle import (  # noqa: E402
    _validate_databases_and_prefixes,
    discover_bundle,
)
from m4_snapshot import (  # noqa: E402
    BundleSnapshot,
    FileStamp,
    TreeEntry,
    _path_identity,
    _read_captured,
)
from m4_contract import _write_exclusive  # noqa: E402


def windows_short_path(path: Path) -> Path | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    query = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    query.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    query.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    characters = query(str(path), buffer, len(buffer))
    if characters == 0 or characters >= len(buffer):
        return None
    return Path(buffer.value)


class StrictJsonTests(unittest.TestCase):
    def _load(self, payload: bytes) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.json"
            path.write_bytes(payload)
            load_bounded_json(path, 1024)

    def test_duplicate_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "duplicate JSON object key"):
            self._load(b'{"same":1,"same":2}')

    def test_bom_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "byte-order marks"):
            self._load(b"\xef\xbb\xbf{}")

    def test_nonfinite_numbers_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "non-finite"):
            self._load(b'{"number":NaN}')

    def test_windows_root_lexical_validation_preserves_only_safe_aliases(
        self,
    ) -> None:
        self.assertEqual(
            _validate_lexical_windows_root(
                r"C:\Users\RUNNER~1\repository", "$.root"
            ),
            ("c:\\", "users", "runner~1", "repository"),
        )
        invalid = (
            "",
            r"C:\repo\.\child",
            r"C:\repo\..\child",
            "C:\\repo\\\\child",
            "C:\\repo.\\child",
            "C:\\repo \\child",
            r"C:\repo:stream",
            r"\\server\share\repo",
            r"\\?\C:\repo",
            r"\\.\C:\repo",
            "C:/repo",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    _validate_lexical_windows_root(value, "$.root")


class SnapshotIdentityTests(unittest.TestCase):
    def test_snapshot_uses_handle_identity_not_unreliable_stat_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "payload.bin"
            payload = b"snapshot payload"
            path.write_bytes(payload)
            metadata = path.lstat()
            unreliable = mock.Mock(
                st_mode=metadata.st_mode,
                st_size=metadata.st_size,
                st_mtime_ns=metadata.st_mtime_ns,
                st_ctime_ns=metadata.st_ctime_ns,
                st_file_attributes=getattr(metadata, "st_file_attributes", 0),
                st_dev=metadata.st_dev + 1,
                st_ino=metadata.st_ino + 1,
                st_nlink=metadata.st_nlink + 1,
            )
            expected = TreeEntry(
                "file",
                FileStamp.from_stat(unreliable),
                _path_identity(path, metadata),
            )

            captured = _read_captured(
                root,
                path.name,
                expected,
                len(payload),
                role="prefix",
            )

            self.assertEqual(captured.payload, payload)
            self.assertEqual(captured.size, len(payload))


class AtomicAnalysisWriteTests(unittest.TestCase):
    def test_complete_bytes_are_published_without_a_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "analysis.json"
            _write_exclusive(output, b'{"complete":true}\n')
            self.assertEqual(output.read_bytes(), b'{"complete":true}\n')
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_preexisting_output_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "analysis.json"
            output.write_bytes(b"original")
            with self.assertRaisesRegex(ValidationError, "refusing to replace"):
                _write_exclusive(output, b"replacement")
            self.assertEqual(output.read_bytes(), b"original")

    def test_failed_flush_leaves_no_partial_output_or_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "analysis.json"
            with mock.patch(
                "m4_contract.os.fsync",
                side_effect=OSError("injected flush failure"),
            ):
                with self.assertRaisesRegex(ValidationError, "cannot create"):
                    _write_exclusive(output, b"partial")
            self.assertFalse(output.exists())
            self.assertEqual(list(root.iterdir()), [])


class CheckedPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan, cls.digest = load_checked_plan()

    def test_checked_plan_passes(self) -> None:
        validate_plan_document(copy.deepcopy(self.plan))

    def test_partial_cyclic_schedule_is_rejected(self) -> None:
        altered = copy.deepcopy(self.plan)
        altered["samples"][6]["condition_id"] = "V20-U"
        with self.assertRaises(ValidationError):
            validate_plan_document(altered)

    def test_duplicate_plan_path_is_rejected(self) -> None:
        altered = copy.deepcopy(self.plan)
        altered["samples"][1]["record_path"] = altered["samples"][0]["record_path"]
        with self.assertRaises(ValidationError):
            validate_plan_document(altered)

    def test_permuted_launch_schedule_is_rejected(self) -> None:
        altered = copy.deepcopy(self.plan)
        altered["samples"][0], altered["samples"][1] = (
            altered["samples"][1],
            altered["samples"][0],
        )
        with self.assertRaisesRegex(ValidationError, "launch order"):
            validate_plan_document(altered)

    def test_byte_identity_rejects_schema_valid_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(json.dumps(self.plan), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "bytes differ"):
                load_checked_plan(path)


class InvocationProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.bundle = base / "stage"
        self.repository = base / "repository"
        for path in (self.bundle, self.repository):
            path.mkdir()
        (self.bundle / "plan").mkdir()
        (self.bundle / "bindings").mkdir()
        (self.bundle / "evidence" / "samples" / "M4-V20-U-01").mkdir(
            parents=True
        )
        self.plan_path = self.bundle / "plan" / "checked-plan.json"
        self.plan_path.write_bytes(CHECKED_PLAN.read_bytes())
        self.environment_path = self.bundle / "bindings" / "environment.json"
        self.environment_path.write_bytes(b'{"binding":"test"}\n')
        self.plan, self.plan_hash = load_checked_plan()
        env_hash = hashlib.sha256(self.environment_path.read_bytes()).hexdigest()
        condition = self.plan["conditions"][0]
        self.invocation = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_invocation",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "sample_id": "M4-V20-U-01",
            "condition_id": "V20-U",
            "phase_id": "creator",
            "phase_ordinal": 1,
            "worker_run_id": "M4-V20-U-01-CREATOR",
            "worker_ordinal": 1,
            "nonce": "0123456789abcdef0123456789abcdef",
            "campaign_run_id": "20260725T120000Z-m4-test",
            "producer_commit": "0" * 40,
            "repository_url": self.plan["repository_url"],
            "remote_ref": self.plan["remote_ref"],
            "repository_root": str(self.repository.resolve()),
            "plan_path": "plan/checked-plan.json",
            "plan_sha256": self.plan_hash,
            "environment_path": "bindings/environment.json",
            "environment_sha256": env_hash,
            "provider_sha256": "1" * 64,
            "stage_root": str(self.bundle.resolve()),
            "database_path": "evidence/samples/M4-V20-U-01/creator.mdb",
            "result_path": "evidence/samples/M4-V20-U-01/creator-result.json",
            "phase_contract": {
                "kind": "creator",
                "method": self.plan["design"]["creation_method"],
                "locale": self.plan["design"]["locale"],
                "version_option": condition["version_option"],
                "version_api_value": condition["version_api_value"],
                "encryption_option": condition["encryption_option"],
                "encryption_api_value": condition["encryption_api_value"],
                "create_option_value": condition["create_option_value"],
                "compact_database_used": False,
                "expected_dao_version": condition["expected_dao_version"],
            },
            "created_at_utc": "2026-07-25T12:00:00-04:00",
            "bindings_verified_before_com": True,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _reopen_invocation(
        self, *, destination_sha: str | None = None, completed: str = "2026-07-25T15:59:00Z"
    ) -> dict[str, object]:
        payload = b"R" * 2048
        database = (
            self.bundle / "evidence" / "samples" / "M4-V20-U-01" / "reopen.mdb"
        )
        database.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        clone = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_clone_log",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "sample_id": "M4-V20-U-01",
            "started_at_utc": "2026-07-25T15:58:00Z",
            "completed_at_utc": completed,
            "source_path": "evidence/samples/M4-V20-U-01/creator.mdb",
            "destination_path": "evidence/samples/M4-V20-U-01/reopen.mdb",
            "source_bytes": 2048,
            "destination_bytes": 2048,
            "source_sha256_before_clone": digest,
            "source_sha256_after_clone": digest,
            "destination_sha256": destination_sha or digest,
            "source_file_identity": {
                "volume_serial_number": "12345678",
                "file_index": "0000000000000001",
                "link_count": 1,
            },
            "destination_file_identity": {
                "volume_serial_number": "12345678",
                "file_index": "0000000000000002",
                "link_count": 1,
            },
            "all_hashes_equal": True,
            "same_volume": True,
            "distinct_file_identity": True,
            "no_hardlink": True,
            "reparse_free": True,
            "completed_before_reopen_com": True,
            "status": "pass",
        }
        clone_path = (
            self.bundle / "evidence" / "samples" / "M4-V20-U-01" / "clone.json"
        )
        clone_path.write_text(json.dumps(clone), encoding="utf-8")
        clone_hash = hashlib.sha256(clone_path.read_bytes()).hexdigest()
        invocation = copy.deepcopy(self.invocation)
        invocation.update(
            {
                "phase_id": "reopen",
                "phase_ordinal": 2,
                "worker_run_id": "M4-V20-U-01-REOPEN",
                "worker_ordinal": 2,
                "nonce": "fedcba9876543210fedcba9876543210",
                "database_path": "evidence/samples/M4-V20-U-01/reopen.mdb",
                "result_path": "evidence/samples/M4-V20-U-01/reopen-result.json",
                "phase_contract": {
                    "kind": "reopen",
                    "expected_dao_version": "2.0",
                    "pre_com_database_bytes": 2048,
                    "pre_com_database_sha256": digest,
                    "clone_log": {
                        "path": "evidence/samples/M4-V20-U-01/clone.json",
                        "sha256": clone_hash,
                    },
                },
                "created_at_utc": "2026-07-25T16:00:00Z",
            }
        )
        return invocation

    def test_valid_creator_retained_projection(self) -> None:
        validate_invocation_document(
            self.invocation,
            self.plan,
            self.plan_hash,
            self.bundle,
            preflight=False,
        )

    def test_valid_creator_live_preflight_on_windows(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows live-root check")
        validate_invocation_document(
            self.invocation,
            self.plan,
            self.plan_hash,
            self.bundle,
            preflight=True,
        )

    def test_live_preflight_accepts_platform_short_ancestor(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows live-root check")
        long_parent = self.bundle.parent.resolve()
        short_parent = windows_short_path(long_parent)
        if short_parent is None or os.path.normcase(
            str(short_parent)
        ) == os.path.normcase(str(long_parent)):
            self.skipTest("8.3 ancestor aliases are unavailable")
        aliased_bundle = short_parent / self.bundle.name
        self.invocation["stage_root"] = str(aliased_bundle)
        self.invocation["repository_root"] = str(
            short_parent / self.repository.name
        )
        validate_invocation_document(
            self.invocation,
            self.plan,
            self.plan_hash,
            aliased_bundle,
            preflight=True,
        )

    def test_worker_ordinal_projection_is_enforced(self) -> None:
        self.invocation["worker_ordinal"] = 2
        with self.assertRaisesRegex(ValidationError, "worker_ordinal"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
            preflight=False,
        )

    def test_reopen_clone_destination_binding_is_enforced(self) -> None:
        invocation = self._reopen_invocation(destination_sha="f" * 64)
        with self.assertRaisesRegex(ValidationError, "pre_com_database_sha256"):
            validate_invocation_document(
                invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=False,
            )

    def test_reopen_clone_must_precede_invocation_creation(self) -> None:
        invocation = self._reopen_invocation(completed="2026-07-25T16:01:00Z")
        with self.assertRaisesRegex(ValidationError, "completed after"):
            validate_invocation_document(
                invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=False,
            )

    def test_creator_refuses_preexisting_database(self) -> None:
        if os.name != "nt":
            self.skipTest("live preflight requires Windows")
        database = resolve_bundle_path(
            self.bundle, self.invocation["database_path"]
        )
        database.write_bytes(b"x")
        with self.assertRaisesRegex(ValidationError, "must not preexist"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_posix_roots_are_rejected_for_live_preflight(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX live-root rejection is non-Windows coverage")
        with self.assertRaisesRegex(ValidationError, "drive-rooted Windows"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_live_preflight_rejects_symlinked_stage_root(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows live-root check")
        link = self.bundle.parent / "stage-link"
        try:
            link.symlink_to(self.bundle, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege unavailable")
            raise
        self.invocation["stage_root"] = str(link)
        with self.assertRaisesRegex(ValidationError, "canonical|reparse"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_live_preflight_rejects_alternate_data_stream_root(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows live-root check")
        self.invocation["repository_root"] += ":stream"
        with self.assertRaisesRegex(ValidationError, "alternate data streams"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_live_preflight_rejects_trailing_dot_and_space_aliases(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows live-root check")
        original = self.invocation["repository_root"]
        for suffix in (".", " "):
            with self.subTest(suffix=suffix):
                self.invocation["repository_root"] = original + suffix
                with self.assertRaisesRegex(ValidationError, "noncanonical"):
                    validate_invocation_document(
                        self.invocation,
                        self.plan,
                        self.plan_hash,
                        self.bundle,
                        preflight=True,
                    )
        self.invocation["repository_root"] = original

    def test_live_preflight_rejects_noncanonical_bundle_root(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows live-root check")
        noncanonical = self.bundle / ".." / self.bundle.name
        with self.assertRaisesRegex(ValidationError, "noncanonical"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                noncanonical,
                preflight=True,
            )

    def test_bundle_locator_cannot_escape(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unsafe path"):
            resolve_bundle_path(self.bundle, "evidence/../outside.json")

    def test_noncanonical_absolute_path_is_rejected(self) -> None:
        self.invocation["repository_root"] = (
            f"{self.repository}/../{self.repository.name}"
        )
        with self.assertRaisesRegex(ValidationError, "noncanonical"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_stage_cannot_overlap_repository(self) -> None:
        self.invocation["repository_root"] = str(self.bundle.parent.resolve())
        with self.assertRaisesRegex(ValidationError, "overlap"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )


class BundleTopologyTests(unittest.TestCase):
    def test_snapshot_hard_link_identity_is_handle_derived_on_windows(self) -> None:
        source = (SCRIPT_DIR / "m4_snapshot.py").read_text(encoding="utf-8")
        self.assertIn("GetFileInformationByHandle", source)
        self.assertIn("identity.links != 1", source)
        self.assertNotIn("metadata.st_nlink != 1", source)

    def test_snapshot_rejects_symlinked_bundle_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            real = parent / "real"
            alias = parent / "alias"
            real.mkdir()
            try:
                alias.symlink_to(real, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks unavailable")
            with self.assertRaisesRegex(ValidationError, "aliases|reparses"):
                BundleSnapshot.capture(alias)

    def test_hard_link_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.json"
            second = root / "second.json"
            first.write_bytes(b"{}\n")
            try:
                second.hardlink_to(first)
            except OSError:
                self.skipTest("hard links unavailable")
            with self.assertRaisesRegex(ValidationError, "hard links"):
                discover_bundle(root)

    def test_each_reopen_uses_its_own_clone_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = b"D" * 2048
            digest = hashlib.sha256(payload).hexdigest()
            prefix_digest = digest
            records = []
            index = {}
            for record_number in (1, 2):
                phases = {}
                for phase in ("creator", "reopen"):
                    database_path = f"db/r{record_number}-{phase}.mdb"
                    prefix_path = f"prefix/r{record_number}-{phase}.bin"
                    database = root / database_path
                    prefix = root / prefix_path
                    database.parent.mkdir(exist_ok=True)
                    prefix.parent.mkdir(exist_ok=True)
                    database.write_bytes(payload)
                    prefix.write_bytes(payload)
                    post = {
                        "database_path": database_path,
                        "database_bytes": 2048,
                        "database_sha256": digest,
                        "prefix_path": prefix_path,
                        "prefix_sha256": prefix_digest,
                    }
                    phase_row = {
                        "phase_id": phase,
                        "post_close_file_observations": post,
                    }
                    if phase == "reopen":
                        phase_row["pre_com_file_binding"] = {
                            "database_path": database_path,
                            "database_bytes": 2048,
                            "database_sha256": "a" * 64,
                        }
                    phases[phase] = phase_row
                    index[database_path] = {
                        "role": "database",
                        "size_bytes": 2048,
                        "sha256": digest,
                    }
                    index[prefix_path] = {"role": "prefix"}
                records.append(
                    {
                        "phases": phases,
                        "controller_clone": {
                            "destination_path": phases["reopen"][
                                "post_close_file_observations"
                            ]["database_path"],
                            "destination_bytes": 2048,
                            "destination_sha256": (
                                "b" * 64 if record_number == 1 else "a" * 64
                            ),
                        },
                    }
                )
            plan = {
                "bounds": {
                    "max_database_artifacts": 4,
                    "max_database_bytes": 2048,
                    "max_validator_database_reads_per_run": 4,
                    "max_validator_database_read_bytes_per_run": 8192,
                    "max_prefix_artifacts": 4,
                    "max_total_prefix_bytes": 8192,
                }
            }
            with self.assertRaisesRegex(ValidationError, "pre-COM clone hash"):
                _validate_databases_and_prefixes(root, plan, index, records)


if __name__ == "__main__":
    unittest.main()
