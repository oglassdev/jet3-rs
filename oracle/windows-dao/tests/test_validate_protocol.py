import copy
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
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


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


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


def ready_environment():
    environment = blocked_environment()
    environment["status"] = "ready"
    environment["status_reason"] = "Synthetic provider passed protocol checks."
    environment["host"]["is_windows"] = True
    environment["host"]["process_architecture"] = "x86"
    candidate = {
        "prog_id": "DAO.DBEngine.36",
        "clsid": "{00000100-0000-0010-8000-00AA006D2EA4}",
        "registry_view": "x86",
        "registration_scope": "machine",
        "registered": True,
        "server_path": "C:\\Program Files (x86)\\Common Files\\dao360.dll",
        "server_file_version": "3.60.0000.0000",
        "server_sha256": "3" * 64,
        "activation": "succeeded",
        "provider_version": "3.6",
        "dbversion30_test": {
            "status": "pass",
            "detail": "Synthetic protocol test only.",
        },
    }
    environment["provider_candidates"] = [candidate]
    environment["accepted_provider"] = {
        "prog_id": candidate["prog_id"],
        "clsid": candidate["clsid"],
        "registry_view": candidate["registry_view"],
        "registration_scope": candidate["registration_scope"],
        "provider_version": candidate["provider_version"],
        "server_path": candidate["server_path"],
        "server_file_version": candidate["server_file_version"],
        "server_sha256": candidate["server_sha256"],
        "database_version": "dbVersion30",
    }
    return environment


def update_manifest_payloads(root, relative_paths):
    manifest_path = root / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {entry["path"]: entry for entry in manifest["files"]}
    for relative in relative_paths:
        path = root / relative
        entries[relative]["sha256"] = sha256(path)
        entries[relative]["size_bytes"] = path.stat().st_size
    write_json(manifest_path, manifest)


def write_runner_bundle(root):
    scenario = json.loads(
        (
            SCRIPT.parents[1]
            / "examples"
            / "DAO-GEN-PROBE-001.scenario.json"
        ).read_text(encoding="utf-8")
    )
    scenario_id = scenario["scenario_id"]
    scenario_dir = root / "scenarios" / scenario_id
    input_path = scenario_dir / "input.json"
    environment_path = root / "environment.json"
    write_json(environment_path, ready_environment())
    write_json(input_path, scenario)

    database_bytes = b"synthetic protocol test; not an MDB"
    database_hash = hashlib.sha256(database_bytes).hexdigest()
    database_relative = f"databases/{database_hash}.mdb"
    database_path = root / database_relative
    database_path.parent.mkdir(parents=True)
    database_path.write_bytes(database_bytes)

    snapshot = empty_snapshot()
    snapshot["producer"]["kind"] = "dao"
    snapshot["database_sha256"] = database_hash
    snapshot_relative = f"scenarios/{scenario_id}/dao-snapshot.json"
    snapshot_path = root / snapshot_relative
    snapshot_path.write_bytes(canonical_bytes(snapshot))

    operation_log = {
        "protocol_version": "1.0.0",
        "document_type": "dao_operation_log",
        "run_id": RUN_ID,
        "scenario_id": scenario_id,
        "git_commit": COMMIT,
        "final_status": "pass",
        "entries": [
            {
                "sequence": 1,
                "timestamp_utc": TIMESTAMP,
                "action": "activate_provider",
                "status": "pass",
                "detail": "Synthetic protocol action.",
            },
            {
                "sequence": 2,
                "timestamp_utc": TIMESTAMP,
                "action": "create_database",
                "status": "pass",
                "detail": "Synthetic protocol action.",
            },
            {
                "sequence": 3,
                "timestamp_utc": TIMESTAMP,
                "action": "close_database",
                "status": "pass",
                "detail": "Synthetic protocol action.",
            },
            {
                "sequence": 4,
                "timestamp_utc": TIMESTAMP,
                "action": "reopen_database",
                "status": "pass",
                "detail": "Synthetic protocol action.",
            },
            {
                "sequence": 5,
                "timestamp_utc": TIMESTAMP,
                "action": "snapshot",
                "status": "pass",
                "detail": "Synthetic protocol action.",
            },
            {
                "sequence": 6,
                "timestamp_utc": TIMESTAMP,
                "action": "finalize",
                "status": "pass",
                "detail": "Synthetic protocol bundle.",
            },
        ],
    }
    log_relative = f"scenarios/{scenario_id}/operation-log.json"
    log_path = root / log_relative
    write_json(log_path, operation_log)

    input_relative = f"scenarios/{scenario_id}/input.json"
    environment_hash = sha256(environment_path)
    report = {
        "protocol_version": "1.0.0",
        "document_type": "dao_evidence_report",
        "run_id": RUN_ID,
        "git": {"commit": COMMIT, "dirty": False},
        "oracle_revision": COMMIT,
        "command_line": ["synthetic-runner-test"],
        "started_at_utc": TIMESTAMP,
        "ended_at_utc": TIMESTAMP,
        "status": "pass",
        "status_reason": "Synthetic runner-shaped bundle validated.",
        "environment": {
            "path": "environment.json",
            "sha256": environment_hash,
        },
        "counts": {
            "selected": 1,
            "pass": 1,
            "fail": 0,
            "blocked": 0,
            "error": 0,
            "skipped": 0,
        },
        "scenarios": [
            {
                "scenario_id": scenario_id,
                "mode": scenario["mode"],
                "capabilities": scenario["capabilities"],
                "status": "pass",
                "reason": "Synthetic runner-shaped result.",
                "input": {"path": input_relative, "sha256": sha256(input_path)},
                "source_database": None,
                "output_database": {
                    "path": database_relative,
                    "sha256": database_hash,
                },
                "dao_snapshot": {
                    "path": snapshot_relative,
                    "sha256": sha256(snapshot_path),
                },
                "rust_snapshot": None,
                "operation_log": {
                    "path": log_relative,
                    "sha256": sha256(log_path),
                },
            }
        ],
    }
    report_path = root / "report.json"
    write_json(report_path, report)

    roles = {
        "environment.json": "environment",
        "report.json": "report",
        input_relative: "scenario_input",
        database_relative: "output_database",
        snapshot_relative: "dao_snapshot",
        log_relative: "operation_log",
    }
    files = []
    for relative, role in roles.items():
        path = root / relative
        media_type = (
            "application/vnd.ms-access"
            if path.suffix == ".mdb"
            else "application/json"
        )
        files.append(
            {
                "path": relative,
                "role": role,
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
                "media_type": media_type,
            }
        )
    manifest = {
        "protocol_version": "1.0.0",
        "document_type": "dao_bundle_manifest",
        "run_id": RUN_ID,
        "git_commit": COMMIT,
        "dirty": False,
        "created_at_utc": TIMESTAMP,
        "status": "pass",
        "report_path": "report.json",
        "scenario_ids": [scenario_id],
        "files": files,
    }
    write_json(root / "bundle-manifest.json", manifest)
    return {
        "environment": environment_path,
        "report": report_path,
        "snapshot": snapshot_path,
        "snapshot_relative": snapshot_relative,
        "operation_log": log_path,
        "operation_log_relative": log_relative,
    }


class ProtocolValidationTests(unittest.TestCase):
    def test_powershell_sources_parse_or_have_balanced_delimiters(self):
        scripts = sorted((SCRIPT.parent).glob("*.ps1"))
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is not None:
            for script in scripts:
                escaped = str(script).replace("'", "''")
                command = (
                    "$tokens=$null;$errors=$null;"
                    "[Management.Automation.Language.Parser]::ParseFile("
                    f"'{escaped}',[ref]$tokens,[ref]$errors)|Out-Null;"
                    "if($errors.Count){$errors|ForEach-Object{"
                    "[Console]::Error.WriteLine($_.Message)};exit 1}"
                )
                result = subprocess.run(
                    [shell, "-NoProfile", "-Command", command],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"{script}: {result.stderr}{result.stdout}",
                )
            return

        def delimiters(source):
            stack = []
            pairs = {")": "(", "]": "[", "}": "{"}
            quote = None
            escaped = False
            comment = False
            for character in source:
                if comment:
                    if character == "\n":
                        comment = False
                    continue
                if quote is not None:
                    if escaped:
                        escaped = False
                    elif character == "`":
                        escaped = True
                    elif character == quote:
                        quote = None
                    continue
                if character == "#":
                    comment = True
                elif character in ("'", '"'):
                    quote = character
                elif character in "([{":
                    stack.append(character)
                elif character in ")]}":
                    if not stack or stack.pop() != pairs[character]:
                        return False
            return not stack and quote is None

        duplicate_call = re.compile(
            r"\[Console\]::Error\.WriteLine\(\s*"
            r"\[Console\]::Error\.WriteLine\(",
            re.MULTILINE,
        )
        for script in scripts:
            source = script.read_text(encoding="utf-8")
            self.assertTrue(delimiters(source), msg=f"{script}: unbalanced")
            self.assertIsNone(
                duplicate_call.search(source),
                msg=f"{script}: nested duplicate WriteLine call",
            )

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

    def test_unsupported_scenario_action_fails_closed(self):
        example = json.loads(
            (
                SCRIPT.parents[1]
                / "examples"
                / "DAO-GEN-PROBE-001.scenario.json"
            ).read_text(encoding="utf-8")
        )
        example["steps"][0] = {
            "step_id": "unsupported",
            "action": "create_table",
            "arguments": {},
        }
        with self.assertRaises(VALIDATOR.ValidationError):
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

    def test_snapshot_rejects_noncanonical_typed_value(self):
        snapshot = empty_snapshot()
        snapshot["database_properties"]["currency"] = {
            "kind": "currency",
            "value": 12.5,
        }
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError,
            "invariant decimal string",
        ):
            VALIDATOR.validate_document(snapshot)

    def test_snapshot_rejects_unsorted_table_names(self):
        snapshot = empty_snapshot()
        table = {
            "name": "zeta",
            "kind": "user",
            "attributes": 0,
            "columns": [],
            "indexes": [],
            "properties": {},
            "rows": [],
        }
        other = copy.deepcopy(table)
        other["name"] = "alpha"
        snapshot["tables"] = [table, other]
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError,
            "names must be unique and sorted",
        ):
            VALIDATOR.validate_document(snapshot)

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

    def test_synthetic_runner_shaped_bundle_is_valid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / COMMIT / RUN_ID
            root.mkdir(parents=True)
            write_runner_bundle(root)
            VALIDATOR.validate_bundle(root)

    def test_passing_bundle_rejects_blocked_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / COMMIT / RUN_ID
            root.mkdir(parents=True)
            paths = write_runner_bundle(root)
            environment = blocked_environment()
            write_json(paths["environment"], environment)
            report = json.loads(paths["report"].read_text(encoding="utf-8"))
            report["environment"]["sha256"] = sha256(paths["environment"])
            write_json(paths["report"], report)
            update_manifest_payloads(
                root, ["environment.json", "report.json"]
            )
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError,
                "requires a ready DAO environment",
            ):
                VALIDATOR.validate_bundle(root)

    def test_passing_bundle_binds_snapshot_to_database_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / COMMIT / RUN_ID
            root.mkdir(parents=True)
            paths = write_runner_bundle(root)
            snapshot = json.loads(paths["snapshot"].read_text(encoding="utf-8"))
            snapshot["database_sha256"] = "4" * 64
            paths["snapshot"].write_bytes(canonical_bytes(snapshot))
            report = json.loads(paths["report"].read_text(encoding="utf-8"))
            report["scenarios"][0]["dao_snapshot"]["sha256"] = sha256(
                paths["snapshot"]
            )
            write_json(paths["report"], report)
            update_manifest_payloads(
                root, [paths["snapshot_relative"], "report.json"]
            )
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError,
                "snapshot/database hashes differ",
            ):
                VALIDATOR.validate_bundle(root)

    def test_passing_bundle_binds_scenario_capabilities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / COMMIT / RUN_ID
            root.mkdir(parents=True)
            paths = write_runner_bundle(root)
            report = json.loads(paths["report"].read_text(encoding="utf-8"))
            report["scenarios"][0]["capabilities"].pop()
            write_json(paths["report"], report)
            update_manifest_payloads(root, ["report.json"])
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError,
                "result/input capabilities differ",
            ):
                VALIDATOR.validate_bundle(root)

    def test_passing_bundle_rejects_unreferenced_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / COMMIT / RUN_ID
            root.mkdir(parents=True)
            write_runner_bundle(root)
            extra_path = root / "unexpected.json"
            write_json(extra_path, {"diagnostic": True})
            manifest_path = root / "bundle-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"].append(
                {
                    "path": "unexpected.json",
                    "role": "other",
                    "sha256": sha256(extra_path),
                    "size_bytes": extra_path.stat().st_size,
                    "media_type": "application/json",
                }
            )
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError,
                "payload contract differs",
            ):
                VALIDATOR.validate_bundle(root)

    def test_passing_bundle_rejects_forged_earlier_log_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / COMMIT / RUN_ID
            root.mkdir(parents=True)
            paths = write_runner_bundle(root)
            operation_log = json.loads(
                paths["operation_log"].read_text(encoding="utf-8")
            )
            operation_log["entries"][1]["status"] = "fail"
            operation_log["entries"][1]["detail"] = "Forged earlier failure."
            write_json(paths["operation_log"], operation_log)
            report = json.loads(paths["report"].read_text(encoding="utf-8"))
            report["scenarios"][0]["operation_log"]["sha256"] = sha256(
                paths["operation_log"]
            )
            write_json(paths["report"], report)
            update_manifest_payloads(
                root, [paths["operation_log_relative"], "report.json"]
            )
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError,
                "cannot contain an earlier failure",
            ):
                VALIDATOR.validate_bundle(root)

    def test_contradictory_differential_snapshots_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / COMMIT / RUN_ID
            root.mkdir(parents=True)
            paths = write_runner_bundle(root)
            report = json.loads(paths["report"].read_text(encoding="utf-8"))
            result = report["scenarios"][0]
            database_reference = result["output_database"]
            new_id = "DAO-READ-PROBE-001"

            input_path = root / result["input"]["path"]
            scenario = json.loads(input_path.read_text(encoding="utf-8"))
            scenario["scenario_id"] = new_id
            scenario["mode"] = "rust_read_dao"
            scenario["database"]["input_role"] = "dao_created"
            scenario["database"]["input_path"] = database_reference["path"]
            scenario["database"]["input_sha256"] = database_reference["sha256"]
            write_json(input_path, scenario)

            dao_snapshot = json.loads(
                paths["snapshot"].read_text(encoding="utf-8")
            )
            dao_snapshot["scenario_id"] = new_id
            paths["snapshot"].write_bytes(canonical_bytes(dao_snapshot))
            rust_snapshot = copy.deepcopy(dao_snapshot)
            rust_snapshot["producer"]["kind"] = "rust"
            rust_snapshot["tables"] = [
                {
                    "name": "contradiction",
                    "kind": "user",
                    "attributes": 0,
                    "columns": [],
                    "indexes": [],
                    "properties": {},
                    "rows": [],
                }
            ]
            rust_relative = (
                "scenarios/DAO-GEN-PROBE-001/rust-snapshot.json"
            )
            rust_path = root / rust_relative
            rust_path.write_bytes(canonical_bytes(rust_snapshot))

            operation_log = json.loads(
                paths["operation_log"].read_text(encoding="utf-8")
            )
            operation_log["scenario_id"] = new_id
            write_json(paths["operation_log"], operation_log)

            result["scenario_id"] = new_id
            result["mode"] = "rust_read_dao"
            result["input"]["sha256"] = sha256(input_path)
            result["source_database"] = database_reference
            result["output_database"] = None
            result["dao_snapshot"]["sha256"] = sha256(paths["snapshot"])
            result["rust_snapshot"] = {
                "path": rust_relative,
                "sha256": sha256(rust_path),
            }
            result["operation_log"]["sha256"] = sha256(paths["operation_log"])
            write_json(paths["report"], report)

            manifest_path = root / "bundle-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["scenario_ids"] = [new_id]
            entry_by_path = {
                entry["path"]: entry for entry in manifest["files"]
            }
            entry_by_path[database_reference["path"]]["role"] = "source_database"
            entry_by_path[result["input"]["path"]]["sha256"] = sha256(input_path)
            entry_by_path[result["input"]["path"]][
                "size_bytes"
            ] = input_path.stat().st_size
            entry_by_path[result["dao_snapshot"]["path"]]["sha256"] = sha256(
                paths["snapshot"]
            )
            entry_by_path[result["dao_snapshot"]["path"]][
                "size_bytes"
            ] = paths["snapshot"].stat().st_size
            entry_by_path[result["operation_log"]["path"]]["sha256"] = sha256(
                paths["operation_log"]
            )
            entry_by_path[result["operation_log"]["path"]][
                "size_bytes"
            ] = paths["operation_log"].stat().st_size
            manifest["files"].append(
                {
                    "path": rust_relative,
                    "role": "rust_snapshot",
                    "sha256": sha256(rust_path),
                    "size_bytes": rust_path.stat().st_size,
                    "media_type": "application/json",
                }
            )
            entry_by_path["report.json"]["sha256"] = sha256(paths["report"])
            entry_by_path["report.json"][
                "size_bytes"
            ] = paths["report"].stat().st_size
            write_json(manifest_path, manifest)

            with self.assertRaisesRegex(
                VALIDATOR.ValidationError,
                "differential mode is not implemented",
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
