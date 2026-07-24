import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_m1_protocol.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("validate_m1_protocol", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)

ROOT = SCRIPT.parents[1]
EXAMPLES = ROOT / "examples"
COMMIT = "1" * 40
RUN_ID = "20260724T120000Z-m1-test"
TIMESTAMP = "2026-07-24T12:00:00+00:00"


def load_example(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def write_json(path, value, canonical=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if canonical:
        content = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    else:
        content = json.dumps(value, indent=2) + "\n"
    path.write_bytes(content.encode("utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def empty_snapshot(scenario_id, database_hash):
    return {
        "protocol_version": "1.1.0",
        "document_type": "canonical_snapshot",
        "scenario_id": scenario_id,
        "producer": {"kind": "dao", "source_revision": COMMIT},
        "database_sha256": database_hash,
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
    candidate = {
        "prog_id": "DAO.DBEngine.36",
        "clsid": "{00000100-0000-0010-8000-00AA006D2EA4}",
        "registry_view": "x86",
        "registration_scope": "machine",
        "registered": True,
        "server_path": "C:\\dao360.dll",
        "server_file_version": "3.60",
        "server_sha256": "3" * 64,
        "activation": "succeeded",
        "provider_version": "3.6",
        "dbversion30_test": {"status": "pass", "detail": "Synthetic test."},
    }
    return {
        "protocol_version": "1.1.0",
        "document_type": "dao_environment",
        "captured_at_utc": TIMESTAMP,
        "status": "ready",
        "status_reason": "Synthetic protocol test only.",
        "host": {
            "is_windows": True,
            "computer_name": "test",
            "os_caption": "test",
            "os_version": "1",
            "os_build": "1",
            "os_architecture": "x86",
            "process_architecture": "x86",
        },
        "runtime": {
            "powershell_edition": "test",
            "powershell_version": "test",
            "dotnet_version": "test",
        },
        "regional": {
            "culture": "en-US",
            "ui_culture": "en-US",
            "ansi_code_page": 1252,
            "oem_code_page": 437,
            "timezone_id": "UTC",
            "utc_offset": "+00:00",
        },
        "provider_candidates": [candidate],
        "accepted_provider": {
            key: candidate[key]
            for key in (
                "prog_id",
                "clsid",
                "registry_view",
                "registration_scope",
                "provider_version",
                "server_path",
                "server_file_version",
                "server_sha256",
            )
        }
        | {"database_version": "dbVersion30"},
    }


def write_pair_bundle(bundle):
    scenarios = [
        load_example("DAO-GEN-EMPTY-REPEAT-A.scenario.json"),
        load_example("DAO-GEN-EMPTY-REPEAT-B.scenario.json"),
    ]
    pair = load_example("DAO-PAIR-EMPTY-REPEAT-001.pair.json")
    environment_path = bundle / "environment.json"
    write_json(environment_path, ready_environment())
    roles = {"environment.json": "environment"}
    results = []
    for index, scenario in enumerate(scenarios):
        scenario_id = scenario["scenario_id"]
        scenario_relative = f"scenarios/{scenario_id}/input.json"
        scenario_path = bundle / scenario_relative
        write_json(scenario_path, scenario)
        roles[scenario_relative] = "scenario_input"

        database_relative = f"databases/{scenario_id}.mdb"
        database_path = bundle / database_relative
        database_path.parent.mkdir(parents=True, exist_ok=True)
        database_path.write_bytes(f"synthetic-{index}".encode())
        database_hash = sha256(database_path)
        roles[database_relative] = "output_database"

        snapshot_relative = f"scenarios/{scenario_id}/dao-snapshot.json"
        snapshot_path = bundle / snapshot_relative
        write_json(snapshot_path, empty_snapshot(scenario_id, database_hash), True)
        roles[snapshot_relative] = "dao_snapshot"

        actions = [
            "activate_provider",
            "create_database",
            "close_database",
            "reopen_database",
            "snapshot",
            "finalize",
        ]
        log = {
            "protocol_version": "1.1.0",
            "document_type": "dao_operation_log",
            "run_id": RUN_ID,
            "scenario_id": scenario_id,
            "git_commit": COMMIT,
            "final_status": "pass",
            "entries": [
                {
                    "sequence": number,
                    "timestamp_utc": TIMESTAMP,
                    "action": action,
                    "status": "pass",
                    "detail": "Synthetic contract test.",
                }
                for number, action in enumerate(actions, 1)
            ],
        }
        log_relative = f"scenarios/{scenario_id}/operation-log.json"
        log_path = bundle / log_relative
        write_json(log_path, log)
        roles[log_relative] = "operation_log"
        results.append(
            {
                "scenario_id": scenario_id,
                "recipe": "repeat_empty",
                "status": "pass",
                "reason": "Synthetic test.",
                "input": {"path": scenario_relative, "sha256": sha256(scenario_path)},
                "output_database": {
                    "path": database_relative,
                    "sha256": database_hash,
                },
                "dao_snapshot": {
                    "path": snapshot_relative,
                    "sha256": sha256(snapshot_path),
                },
                "operation_log": {"path": log_relative, "sha256": sha256(log_path)},
            }
        )
    pair_relative = "pairs/DAO-PAIR-EMPTY-REPEAT-001/input.json"
    pair_path = bundle / pair_relative
    write_json(pair_path, pair)
    roles[pair_relative] = "pair_input"
    pair_result = {
        "pair_id": pair["pair_id"],
        "status": "pass",
        "reason": "Synthetic comparison.",
        "input": {"path": pair_relative, "sha256": sha256(pair_path)},
        "left_scenario_id": pair["left_scenario_id"],
        "right_scenario_id": pair["right_scenario_id"],
        "left_snapshot": results[0]["dao_snapshot"],
        "right_snapshot": results[1]["dao_snapshot"],
        "observed_difference_paths": ["/database_sha256", "/scenario_id"],
    }
    counts = {
        "selected": 2,
        "pass": 2,
        "fail": 0,
        "blocked": 0,
        "error": 0,
        "skipped": 0,
    }
    report = {
        "protocol_version": "1.1.0",
        "document_type": "dao_evidence_report",
        "run_id": RUN_ID,
        "git": {"commit": COMMIT, "dirty": False},
        "oracle_revision": COMMIT,
        "command_line": ["synthetic"],
        "started_at_utc": TIMESTAMP,
        "ended_at_utc": TIMESTAMP,
        "status": "pass",
        "status_reason": "Synthetic validation only.",
        "environment": {"path": "environment.json", "sha256": sha256(environment_path)},
        "scenario_counts": counts,
        "pair_counts": counts | {"selected": 1, "pass": 1},
        "scenarios": results,
        "pairs": [pair_result],
    }
    report_path = bundle / "report.json"
    write_json(report_path, report)
    roles["report.json"] = "report"
    files = []
    for relative, role in roles.items():
        path = bundle / relative
        files.append(
            {
                "path": relative,
                "role": role,
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
                "media_type": "application/vnd.ms-access"
                if path.suffix == ".mdb"
                else "application/json",
            }
        )
    manifest = {
        "protocol_version": "1.1.0",
        "document_type": "dao_bundle_manifest",
        "run_id": RUN_ID,
        "git_commit": COMMIT,
        "dirty": False,
        "created_at_utc": TIMESTAMP,
        "status": "pass",
        "report_path": "report.json",
        "scenario_ids": [scenario["scenario_id"] for scenario in scenarios],
        "pair_ids": [pair["pair_id"]],
        "files": files,
    }
    write_json(bundle / "bundle-manifest.json", manifest)
    return report_path


class M1ProtocolTests(unittest.TestCase):
    def test_schemas_and_checked_example_inventory(self):
        VALIDATOR.validate_schemas()
        self.assertEqual(
            VALIDATOR.validate_document_path(EXAMPLES / "m1-inventory.json"),
            "dao_example_inventory",
        )

    def test_all_inventory_documents_validate(self):
        inventory = load_example("m1-inventory.json")
        for entry in inventory["files"]:
            self.assertEqual(
                VALIDATOR.validate_document_path(EXAMPLES / entry["path"]),
                entry["document_type"],
            )

    def test_unknown_step_action_and_arguments_fail_closed(self):
        scenario = load_example("DAO-GEN-BINARY-MARKER-001.scenario.json")
        scenario["steps"][1]["action"] = "execute_sql"
        with self.assertRaises(VALIDATOR.ValidationError):
            VALIDATOR.validate_document(scenario)
        scenario = load_example("DAO-GEN-BINARY-MARKER-001.scenario.json")
        scenario["steps"][1]["arguments"]["sql"] = "not allowed"
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "unknown key"):
            VALIDATOR.validate_document(scenario)

    def test_insert_values_require_exact_field_order_and_type(self):
        scenario = load_example("DAO-GEN-BINARY-MARKER-001.scenario.json")
        scenario["steps"][2]["arguments"]["values"][0]["dao_type"] = "dbText"
        scenario["steps"][2]["arguments"]["values"][0]["encoding"] = "unicode_string"
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "order/type"):
            VALIDATOR.validate_document(scenario)

    def test_binary_marker_is_exact(self):
        scenario = load_example("DAO-GEN-BINARY-MARKER-001.scenario.json")
        scenario["steps"][2]["arguments"]["values"][0]["value"] = "0011223344556676"
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "controlled recipe"):
            VALIDATOR.validate_document(scenario)

    def test_only_text_recipe_declares_a_field_size(self):
        scenario = load_example("DAO-GEN-BINARY-MARKER-001.scenario.json")
        scenario["steps"][1]["arguments"]["fields"][0]["size"] = 8
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "unknown key"):
            VALIDATOR.validate_document(scenario)
        scenario = load_example("DAO-GEN-TEXT8-BASELINE-001.scenario.json")
        scenario["steps"][2]["arguments"]["values"][0]["value"] = "123456789"
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "exceeds field size"):
            VALIDATOR.validate_document(scenario)

    def test_nonunique_index_flags_are_exact(self):
        scenario = load_example("DAO-GEN-TEXT8-INDEXED-001.scenario.json")
        scenario["steps"][1]["arguments"]["indexes"][0]["unique"] = True
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "controlled recipe"):
            VALIDATOR.validate_document(scenario)

    def test_each_memo_ladder_boundary_is_required_in_order(self):
        scenario = load_example("DAO-GEN-MEMO-LADDER-001.scenario.json")
        for index in range(7):
            mutated = copy.deepcopy(scenario)
            mutated["steps"][2 + index]["arguments"]["values"][0]["length"] += 1
            with self.subTest(index=index), self.assertRaisesRegex(
                VALIDATOR.ValidationError, "ladder lengths|above maximum"
            ):
                VALIDATOR.validate_document(mutated)

    def test_long_binary_ladder_byte_is_exact_and_bounded(self):
        scenario = load_example("DAO-GEN-LONGBINARY-LADDER-001.scenario.json")
        scenario["steps"][2]["arguments"]["values"][0]["byte"] = 164
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "controlled recipe"):
            VALIDATOR.validate_document(scenario)
        scenario = load_example("DAO-GEN-LONGBINARY-LADDER-001.scenario.json")
        scenario["steps"][2]["arguments"]["values"][0]["byte"] = 256
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "above maximum"):
            VALIDATOR.validate_document(scenario)

    def test_pair_allowed_paths_are_exact_ordered_contract(self):
        pair = load_example("DAO-PAIR-TEXT8-INDEX-001.pair.json")
        pair["allowed_difference_paths"].reverse()
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "controlled recipe"):
            VALIDATOR.validate_document(pair)
        pair = load_example("DAO-PAIR-TEXT8-INDEX-001.pair.json")
        pair["allowed_difference_paths"].append("/tables/0/rows")
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "controlled recipe"):
            VALIDATOR.validate_document(pair)

    def test_snapshot_rows_are_bound_to_recipe_type_and_value(self):
        scenario = load_example("DAO-GEN-BINARY-MARKER-001.scenario.json")
        snapshot = empty_snapshot(scenario["scenario_id"], "1" * 64)
        snapshot["tables"] = [
            {
                "name": "BinaryMarker",
                "kind": "user",
                "attributes": 0,
                "columns": [
                    {
                        "name": "marker",
                        "ordinal": 0,
                        "dao_type": "dbBinary",
                        "nullable": False,
                        "required": True,
                        "auto_increment": False,
                        "size": 0,
                        "attributes": 0,
                        "properties": {},
                    }
                ],
                "indexes": [],
                "properties": {},
                "rows": [
                    {
                        "canonical_key": "marker",
                        "values": {
                            "marker": {
                                "kind": "binary",
                                "value": "0011223344556677",
                            }
                        },
                    }
                ],
            }
        ]
        VALIDATOR._validate_snapshot_against_recipe(scenario, snapshot)
        snapshot["tables"][0]["rows"][0]["values"]["marker"]["value"] = (
            "0011223344556676"
        )
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError, "type/value differs"
        ):
            VALIDATOR._validate_snapshot_against_recipe(scenario, snapshot)

    def test_deep_comparator_requires_exact_observed_allowances(self):
        left = empty_snapshot("DAO-GEN-EMPTY-REPEAT-A", "1" * 64)
        right = empty_snapshot("DAO-GEN-EMPTY-REPEAT-B", "2" * 64)
        self.assertEqual(
            VALIDATOR.compare_snapshots(
                left, right, ["/database_sha256", "/scenario_id"]
            ),
            ["/database_sha256", "/scenario_id"],
        )
        right["tables"].append({"unexpected": True})
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError, "unexpected semantic length difference"
        ):
            VALIDATOR.compare_snapshots(
                left, right, ["/database_sha256", "/scenario_id"]
            )
        right["tables"].clear()
        right["database_sha256"] = left["database_sha256"]
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError, "allowed difference was not observed"
        ):
            VALIDATOR.compare_snapshots(
                left, right, ["/database_sha256", "/scenario_id"]
            )

    def test_deep_comparator_accepts_only_the_whole_declared_index_path(self):
        left = empty_snapshot("DAO-GEN-TEXT8-BASELINE-001", "1" * 64)
        left["tables"] = [
            {
                "name": "TextMarker",
                "kind": "user",
                "attributes": 0,
                "columns": [],
                "indexes": [],
                "properties": {},
                "rows": [],
            }
        ]
        right = copy.deepcopy(left)
        right["scenario_id"] = "DAO-GEN-TEXT8-INDEXED-001"
        right["database_sha256"] = "2" * 64
        right["tables"][0]["indexes"] = [
            {
                "name": "ix_marker",
                "primary": False,
                "unique": False,
                "required": False,
                "ignore_nulls": False,
                "fields": [{"name": "marker", "descending": False}],
                "properties": {},
            }
        ]
        paths = ["/database_sha256", "/scenario_id", "/tables/0/indexes"]
        self.assertEqual(VALIDATOR.compare_snapshots(left, right, paths), paths)
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError, "unexpected semantic length difference"
        ):
            VALIDATOR.compare_snapshots(
                left,
                right,
                ["/database_sha256", "/scenario_id", "/tables/0/indexes/0"],
            )

    def test_report_binds_pair_sides_to_selected_scenarios(self):
        counts = {
            "selected": 1,
            "pass": 0,
            "fail": 0,
            "blocked": 1,
            "error": 0,
            "skipped": 0,
        }
        report = {
            "protocol_version": "1.1.0",
            "document_type": "dao_evidence_report",
            "run_id": RUN_ID,
            "git": {"commit": COMMIT, "dirty": False},
            "oracle_revision": COMMIT,
            "command_line": ["test"],
            "started_at_utc": TIMESTAMP,
            "ended_at_utc": TIMESTAMP,
            "status": "blocked",
            "status_reason": "Synthetic.",
            "environment": {"path": "environment.json", "sha256": "2" * 64},
            "scenario_counts": counts,
            "pair_counts": counts,
            "scenarios": [
                {
                    "scenario_id": "DAO-GEN-EMPTY-REPEAT-A",
                    "recipe": "repeat_empty",
                    "status": "blocked",
                    "reason": "Synthetic.",
                    "input": {"path": "a.json", "sha256": "3" * 64},
                    "output_database": None,
                    "dao_snapshot": None,
                    "operation_log": None,
                }
            ],
            "pairs": [
                {
                    "pair_id": "DAO-PAIR-EMPTY-REPEAT-001",
                    "status": "blocked",
                    "reason": "Synthetic.",
                    "input": {"path": "pair.json", "sha256": "4" * 64},
                    "left_scenario_id": "DAO-GEN-EMPTY-REPEAT-A",
                    "right_scenario_id": "DAO-GEN-EMPTY-REPEAT-B",
                    "left_snapshot": None,
                    "right_snapshot": None,
                    "observed_difference_paths": [],
                }
            ],
        }
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "absent"):
            VALIDATOR.validate_document(report)

    def test_synthetic_multi_scenario_pair_bundle_validates(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / COMMIT / RUN_ID
            bundle.mkdir(parents=True)
            write_pair_bundle(bundle)
            VALIDATOR.validate_bundle(bundle)

    def test_bundle_rejects_pair_snapshot_reference_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / COMMIT / RUN_ID
            bundle.mkdir(parents=True)
            report_path = write_pair_bundle(bundle)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["pairs"][0]["left_snapshot"] = report["scenarios"][1]["dao_snapshot"]
            write_json(report_path, report)
            manifest_path = bundle / "bundle-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = next(item for item in manifest["files"] if item["path"] == "report.json")
            entry["sha256"] = sha256(report_path)
            entry["size_bytes"] = report_path.stat().st_size
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(VALIDATOR.ValidationError, "references differ"):
                VALIDATOR.validate_bundle(bundle)

    def test_bundle_rejects_symlink_payload_even_when_bytes_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / COMMIT / RUN_ID
            bundle.mkdir(parents=True)
            write_pair_bundle(bundle)
            payload = (
                bundle
                / "pairs"
                / "DAO-PAIR-EMPTY-REPEAT-001"
                / "input.json"
            )
            retained = Path(temporary) / "retained-pair.json"
            retained.write_bytes(payload.read_bytes())
            payload.unlink()
            try:
                payload.symlink_to(retained)
            except OSError as error:
                if getattr(error, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is unavailable")
                raise
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError, "symlinks are forbidden"
            ):
                VALIDATOR.validate_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
