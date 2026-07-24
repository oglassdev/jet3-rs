import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "validate_protocol.py"
)
SPEC = importlib.util.spec_from_file_location("validate_protocol", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)

COMMIT = "1" * 40
RUN_ID = "20260723T120000Z-protocol-test"
TIMESTAMP = "2026-07-23T12:00:00+00:00"


def canonical_bytes(value):
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blocked_environment():
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_environment",
        "captured_at_utc": TIMESTAMP,
        "status": "blocked",
        "status_reason": "Synthetic protocol test has no provider.",
        "host": {
            "is_windows": False,
            "computer_name": "protocol-test",
            "os_caption": "test",
            "os_version": "1",
            "os_build": "1",
            "os_architecture": "test",
            "process_architecture": "unknown",
        },
        "runtime": {
            "powershell_edition": "not-run",
            "powershell_version": "not-run",
            "dotnet_version": "not-run",
        },
        "regional": {
            "culture": "en-US",
            "ui_culture": "en-US",
            "ansi_code_page": 1252,
            "oem_code_page": 437,
            "timezone_id": "Etc/UTC",
            "utc_offset": "+00:00",
        },
        "provider_candidates": [],
        "accepted_provider": None,
    }


def empty_snapshot():
    return {
        "protocol_version": "1.0.0",
        "document_type": "canonical_snapshot",
        "scenario_id": "DAO-GEN-PROBE-001",
        "producer": {"kind": "rust", "source_revision": COMMIT},
        "database_sha256": "2" * 64,
        "ordering": {
            "objects": "name_codepoint_ascending",
            "columns": "ordinal_ascending",
            "indexes": "name_codepoint_ascending",
            "relationships": "name_codepoint_ascending",
            "rows": "declared_key_then_canonical_value",
            "object_keys": "unicode_codepoint_ascending",
        },
        "database_properties": {},
        "tables": [],
        "relationships": [],
        "raw_preservation": [],
    }


class ProtocolValidationTests(unittest.TestCase):
    def test_all_schemas_are_well_formed_and_refs_resolve(self):
        VALIDATOR.validate_schemas()

    def test_checked_example_scenario_is_valid(self):
        example = (
            SCRIPT.parents[1]
            / "examples"
            / "DAO-GEN-PROBE-001.scenario.json"
        )
        self.assertEqual(
            VALIDATOR.validate_document_path(example),
            "dao_scenario",
        )

    def test_scenario_family_must_match_mode(self):
        example = json.loads(
            (
                SCRIPT.parents[1]
                / "examples"
                / "DAO-GEN-PROBE-001.scenario.json"
            ).read_text(encoding="utf-8")
        )
        example["mode"] = "dao_open_rust"
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError, "family does not agree"
        ):
            VALIDATOR.validate_document(example)

    def test_ready_environment_requires_passing_provider(self):
        environment = blocked_environment()
        environment["status"] = "ready"
        environment["status_reason"] = "Incorrect test record."
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError, "requires an accepted provider"
        ):
            VALIDATOR.validate_document(environment)

    def test_probe_candidate_registration_scope_is_validated(self):
        environment = blocked_environment()
        environment["status_reason"] = "Candidate is present but wrong-bitness."
        environment["provider_candidates"] = [
            {
                "prog_id": "DAO.DBEngine.36",
                "clsid": "{00000100-0000-0010-8000-00AA006D2EA4}",
                "registry_view": "x86",
                "registration_scope": "machine",
                "registered": True,
                "server_path": "C:\\Program Files (x86)\\Common Files\\dao.dll",
                "server_file_version": "3.60.0000.0000",
                "server_sha256": "3" * 64,
                "activation": "not_tested",
                "provider_version": None,
                "dbversion30_test": {
                    "status": "not_run",
                    "detail": "Candidate bitness differs from this process.",
                },
            }
        ]
        self.assertEqual(
            VALIDATOR.validate_document(environment),
            "dao_environment",
        )
        del environment["provider_candidates"][0]["registration_scope"]
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError,
            "registration_scope",
        ):
            VALIDATOR.validate_document(environment)

    def test_snapshot_must_use_canonical_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(
                json.dumps(empty_snapshot(), indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError, "not normalized"
            ):
                VALIDATOR.validate_document_path(path)
            path.write_bytes(canonical_bytes(empty_snapshot()))
            self.assertEqual(
                VALIDATOR.validate_document_path(path),
                "canonical_snapshot",
            )

    def test_bundle_hashes_and_bindings_are_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / COMMIT / RUN_ID
            root.mkdir(parents=True)
            environment_path = root / "environment.json"
            environment_path.write_text(
                json.dumps(blocked_environment(), indent=2) + "\n",
                encoding="utf-8",
            )
            environment_hash = sha256(environment_path)
            report = {
                "protocol_version": "1.0.0",
                "document_type": "dao_evidence_report",
                "run_id": RUN_ID,
                "git": {"commit": COMMIT, "dirty": False},
                "oracle_revision": COMMIT,
                "command_line": ["protocol-test"],
                "started_at_utc": TIMESTAMP,
                "ended_at_utc": TIMESTAMP,
                "status": "blocked",
                "status_reason": "Synthetic bundle exercises validation only.",
                "environment": {
                    "path": "environment.json",
                    "sha256": environment_hash,
                },
                "counts": {
                    "selected": 0,
                    "pass": 0,
                    "fail": 0,
                    "blocked": 0,
                    "error": 0,
                    "skipped": 0,
                },
                "scenarios": [],
            }
            report_path = root / "report.json"
            report_path.write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            manifest = {
                "protocol_version": "1.0.0",
                "document_type": "dao_bundle_manifest",
                "run_id": RUN_ID,
                "git_commit": COMMIT,
                "dirty": False,
                "created_at_utc": TIMESTAMP,
                "status": "blocked",
                "report_path": "report.json",
                "scenario_ids": [],
                "files": [
                    {
                        "path": "environment.json",
                        "role": "environment",
                        "sha256": environment_hash,
                        "size_bytes": environment_path.stat().st_size,
                        "media_type": "application/json",
                    },
                    {
                        "path": "report.json",
                        "role": "report",
                        "sha256": sha256(report_path),
                        "size_bytes": report_path.stat().st_size,
                        "media_type": "application/json",
                    },
                ],
            }
            manifest_path = root / "bundle-manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )

            VALIDATOR.validate_bundle(root)
            environment_path.write_text(
                json.dumps(blocked_environment()) + " ",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError, "size does not match manifest"
            ):
                VALIDATOR.validate_bundle(root)

    def test_unknown_fields_are_rejected(self):
        environment = copy.deepcopy(blocked_environment())
        environment["compatibility_claim"] = True
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "unknown key"):
            VALIDATOR.validate_document(environment)

    def test_json_byte_order_mark_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "environment.json"
            path.write_bytes(
                b"\xef\xbb\xbf"
                + json.dumps(blocked_environment()).encode("utf-8")
            )
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError,
                "byte-order marks are forbidden",
            ):
                VALIDATOR.validate_document_path(path)


if __name__ == "__main__":
    unittest.main()
