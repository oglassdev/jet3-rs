import copy
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_m1_protocol.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("validate_m1_protocol", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)
import m1_bundle_validation as BOUNDS

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


def snapshot_for_scenario(scenario, database_hash):
    snapshot = empty_snapshot(scenario["scenario_id"], database_hash)
    if scenario["recipe"] == "repeat_empty":
        return snapshot
    table_step = scenario["steps"][1]["arguments"]
    columns = []
    for ordinal, field in enumerate(table_step["fields"]):
        size = field.get("size")
        if field["dao_type"] in ("dbMemo", "dbLongBinary"):
            size = 0
        columns.append(
            {
                "name": field["name"],
                "ordinal": ordinal,
                "dao_type": field["dao_type"],
                "nullable": not field["required"],
                "required": field["required"],
                "auto_increment": False,
                "size": size,
                "attributes": 0,
                "properties": {},
            }
        )
    indexes = [
        {
            "name": index["name"],
            "primary": index["primary"],
            "unique": index["unique"],
            "required": index["required"],
            "ignore_nulls": index["ignore_nulls"],
            "fields": [
                {"name": field_name, "descending": False}
                for field_name in index["fields"]
            ],
            "properties": {},
        }
        for index in table_step["indexes"]
    ]
    rows = []
    kind_for_type = {
        "dbBinary": "binary",
        "dbText": "text",
        "dbMemo": "memo",
        "dbLongBinary": "ole",
    }
    for step in scenario["steps"]:
        if step["action"] != "insert_row":
            continue
        values = {}
        for declared in step["arguments"]["values"]:
            dao_type = declared["dao_type"]
            if dao_type in ("dbBinary", "dbText"):
                value = declared["value"]
            elif dao_type == "dbMemo":
                value = declared["ascii_character"] * declared["length"]
            else:
                value = f"{declared['byte']:02x}" * declared["length"]
            values[declared["field"]] = {
                "kind": kind_for_type[dao_type],
                "value": value,
            }
        rows.append(
            {
                "canonical_key": ",".join(sorted(values)),
                "values": dict(sorted(values.items())),
            }
        )
    snapshot["tables"] = [
        {
            "name": table_step["name"],
            "kind": "user",
            "attributes": 0,
            "columns": columns,
            "indexes": indexes,
            "properties": {},
            "rows": rows,
        }
    ]
    return snapshot


def operation_log_for_scenario(scenario):
    actions = (
        ["activate_provider"]
        + [step["action"] for step in scenario["steps"]]
        + ["reopen_database", "snapshot", "finalize"]
    )
    entries = []
    row_ordinal = 0
    for sequence, action in enumerate(actions, 1):
        observations = []
        if action == "insert_row":
            step = scenario["steps"][sequence - 2]
            observations = [
                VALIDATOR._expected_value_observation(value, row_ordinal)
                for value in step["arguments"]["values"]
            ]
            row_ordinal += 1
        entries.append(
            {
                "sequence": sequence,
                "timestamp_utc": TIMESTAMP,
                "action": action,
                "status": "pass",
                "detail": "Synthetic contract test.",
                "value_observations": observations,
                "error": None,
            }
        )
    return {
        "protocol_version": "1.1.0",
        "document_type": "dao_operation_log",
        "run_id": RUN_ID,
        "scenario_id": scenario["scenario_id"],
        "git_commit": COMMIT,
        "final_status": "pass",
        "entries": entries,
    }


def write_pair_bundle(bundle):
    inventory = load_example("m1-inventory.json")
    scenarios = [
        load_example(entry["path"])
        for entry in inventory["files"]
        if entry["document_type"] == "dao_scenario"
    ]
    pairs = [
        load_example(entry["path"])
        for entry in inventory["files"]
        if entry["document_type"] == "dao_pair"
    ]
    environment_path = bundle / "environment.json"
    write_json(environment_path, ready_environment())
    roles = {"environment.json": "environment"}
    inventory_path = bundle / "inventory.json"
    inventory_path.write_bytes((EXAMPLES / "m1-inventory.json").read_bytes())
    roles["inventory.json"] = "inventory"
    results = []
    result_by_id = {}
    snapshot_by_id = {}
    for scenario in scenarios:
        scenario_id = scenario["scenario_id"]
        scenario_relative = f"scenarios/{scenario_id}/input.json"
        scenario_path = bundle / scenario_relative
        scenario_path.parent.mkdir(parents=True, exist_ok=True)
        source_name = next(
            entry["path"]
            for entry in inventory["files"]
            if entry["document_type"] == "dao_scenario"
            and load_example(entry["path"])["scenario_id"] == scenario_id
        )
        scenario_path.write_bytes((EXAMPLES / source_name).read_bytes())
        roles[scenario_relative] = "scenario_input"

        database_bytes = f"synthetic-{scenario_id}".encode()
        database_hash = hashlib.sha256(database_bytes).hexdigest()
        database_relative = f"databases/{database_hash}.mdb"
        database_path = bundle / database_relative
        database_path.parent.mkdir(parents=True, exist_ok=True)
        database_path.write_bytes(database_bytes)
        roles[database_relative] = "output_database"

        snapshot_relative = f"scenarios/{scenario_id}/dao-snapshot.json"
        snapshot_path = bundle / snapshot_relative
        snapshot = snapshot_for_scenario(scenario, database_hash)
        write_json(snapshot_path, snapshot, True)
        snapshot_by_id[scenario_id] = snapshot
        roles[snapshot_relative] = "dao_snapshot"

        log = operation_log_for_scenario(scenario)
        log_relative = f"scenarios/{scenario_id}/operation-log.json"
        log_path = bundle / log_relative
        write_json(log_path, log)
        roles[log_relative] = "operation_log"
        result = {
            "scenario_id": scenario_id,
            "recipe": scenario["recipe"],
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
        results.append(result)
        result_by_id[scenario_id] = result

    pair_results = []
    for pair in pairs:
        pair_relative = f"pairs/{pair['pair_id']}/input.json"
        pair_path = bundle / pair_relative
        pair_path.parent.mkdir(parents=True, exist_ok=True)
        source_name = next(
            entry["path"]
            for entry in inventory["files"]
            if entry["document_type"] == "dao_pair"
            and load_example(entry["path"])["pair_id"] == pair["pair_id"]
        )
        pair_path.write_bytes((EXAMPLES / source_name).read_bytes())
        roles[pair_relative] = "pair_input"
        left_id = pair["left_scenario_id"]
        right_id = pair["right_scenario_id"]
        pair_results.append(
            {
                "pair_id": pair["pair_id"],
                "status": "pass",
                "reason": "Synthetic comparison.",
                "input": {"path": pair_relative, "sha256": sha256(pair_path)},
                "left_scenario_id": left_id,
                "right_scenario_id": right_id,
                "left_snapshot": result_by_id[left_id]["dao_snapshot"],
                "right_snapshot": result_by_id[right_id]["dao_snapshot"],
                "observed_difference_paths": VALIDATOR.compare_snapshots(
                    snapshot_by_id[left_id],
                    snapshot_by_id[right_id],
                    pair["allowed_difference_paths"],
                ),
            }
        )
    scenario_counts = {
        "selected": len(results),
        "pass": len(results),
        "fail": 0,
        "blocked": 0,
        "error": 0,
        "skipped": 0,
    }
    pair_counts = scenario_counts | {
        "selected": len(pair_results),
        "pass": len(pair_results),
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
        "inventory": {"path": "inventory.json", "sha256": sha256(inventory_path)},
        "scenario_counts": scenario_counts,
        "pair_counts": pair_counts,
        "scenarios": results,
        "pairs": pair_results,
    }
    report_path = bundle / "report.json"
    write_json(report_path, report)
    roles["report.json"] = "report"
    files = []
    for relative, role in sorted(roles.items()):
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
        "pair_ids": [pair["pair_id"] for pair in pairs],
        "files": files,
    }
    write_json(bundle / "bundle-manifest.json", manifest)
    return report_path


def refresh_manifest_entry(bundle, relative):
    manifest_path = bundle / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item["path"] == relative)
    payload = bundle / relative
    entry["sha256"] = sha256(payload)
    entry["size_bytes"] = payload.stat().st_size
    write_json(manifest_path, manifest)


def refresh_report(bundle, report):
    report_path = bundle / "report.json"
    write_json(report_path, report)
    refresh_manifest_entry(bundle, "report.json")


class M1ProtocolTests(unittest.TestCase):
    def test_schemas_and_checked_example_inventory(self):
        VALIDATOR.validate_schemas()
        self.assertEqual(
            VALIDATOR.validate_document_path(EXAMPLES / "m1-inventory.json"),
            "dao_example_inventory",
        )

    def test_duplicate_json_keys_and_oversized_documents_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            duplicate = Path(temporary) / "duplicate.json"
            duplicate.write_text('{"document_type":"a","document_type":"b"}', encoding="utf-8")
            with self.assertRaisesRegex(VALIDATOR.ValidationError, "duplicate JSON"):
                VALIDATOR.validate_document_path(duplicate)
            oversized = Path(temporary) / "oversized.json"
            oversized.write_bytes(b" " * (VALIDATOR.MAX_JSON_BYTES + 1))
            with self.assertRaisesRegex(VALIDATOR.ValidationError, "exceeds"):
                VALIDATOR.validate_document_path(oversized)

    def test_bounded_json_reader_rejects_identity_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "drifting.json"
            path.write_bytes(b"{}\n")
            real_fstat = os.fstat
            calls = 0

            def drifting_fstat(fd):
                nonlocal calls
                calls += 1
                observed = real_fstat(fd)
                if calls != 2:
                    return observed
                fields = list(observed)
                fields[6] += 1
                return os.stat_result(fields)

            with mock.patch.object(
                BOUNDS.os, "fstat", side_effect=drifting_fstat
            ):
                with self.assertRaisesRegex(
                    VALIDATOR.ValidationError, "changed while being read"
                ):
                    BOUNDS.load_json(path)

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
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError, "controlled recipe|too many items"
        ):
            VALIDATOR.validate_document(pair)
        pair = load_example("DAO-PAIR-TEXT8-INDEX-001.pair.json")
        pair["allowed_difference_paths"].append("/tables/0/rows")
        with self.assertRaisesRegex(
            VALIDATOR.ValidationError, "controlled recipe|too many items"
        ):
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

    def test_snapshot_requires_controlled_empty_metadata_and_field_semantics(self):
        scenario = load_example("DAO-GEN-BINARY-MARKER-001.scenario.json")
        snapshot = snapshot_for_scenario(scenario, "1" * 64)
        snapshot["relationships"].append(
            {
                "name": "unexpected",
                "table": "BinaryMarker",
                "foreign_table": "BinaryMarker",
                "attributes": 0,
                "fields": [{"field": "marker", "foreign_field": "marker"}],
                "properties": {},
            }
        )
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "relationships"):
            VALIDATOR._validate_snapshot_against_recipe(scenario, snapshot)
        snapshot = snapshot_for_scenario(scenario, "1" * 64)
        snapshot["tables"][0]["columns"][0]["required"] = False
        with self.assertRaisesRegex(VALIDATOR.ValidationError, "column semantics"):
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
            "inventory": {"path": "inventory.json", "sha256": "5" * 64},
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

    def test_bundle_tree_walk_is_entry_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / COMMIT / RUN_ID
            bundle.mkdir(parents=True)
            write_pair_bundle(bundle)
            for index in range(BOUNDS.MAX_BUNDLE_ENTRIES + 1):
                (bundle / f"extra-{index:03d}").mkdir()
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError, "directory-entry limit"
            ):
                VALIDATOR.validate_bundle(bundle)

    def test_bundle_tree_walk_rejects_directory_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / COMMIT / RUN_ID
            bundle.mkdir(parents=True)
            write_pair_bundle(bundle)
            target = Path(temporary) / "linked-target"
            target.mkdir()
            link = bundle / "linked-directory"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory-link privilege is unavailable: {exc}")
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError, "symlinks and junctions"
            ):
                VALIDATOR.validate_bundle(bundle)

    def test_bundle_requires_complete_checked_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / COMMIT / RUN_ID
            bundle.mkdir(parents=True)
            report_path = write_pair_bundle(bundle)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            removed = report["scenarios"].pop(0)
            report["scenario_counts"]["selected"] -= 1
            report["scenario_counts"]["pass"] -= 1
            manifest_path = bundle / "bundle-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["scenario_ids"].remove(removed["scenario_id"])
            write_json(manifest_path, manifest)
            refresh_report(bundle, report)
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError, "every inventoried scenario"
            ):
                VALIDATOR.validate_bundle(bundle)

    def test_bundle_rejects_observation_and_media_type_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / COMMIT / RUN_ID
            bundle.mkdir(parents=True)
            report_path = write_pair_bundle(bundle)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            binary = report["scenarios"][0]
            log_path = bundle / binary["operation_log"]["path"]
            log = json.loads(log_path.read_text(encoding="utf-8"))
            insert = next(entry for entry in log["entries"] if entry["action"] == "insert_row")
            insert["value_observations"][0]["readback_length"] = 7
            write_json(log_path, log)
            binary["operation_log"]["sha256"] = sha256(log_path)
            refresh_manifest_entry(bundle, binary["operation_log"]["path"])
            refresh_report(bundle, report)
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError, "value observation differs"
            ):
                VALIDATOR.validate_bundle(bundle)

        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / COMMIT / RUN_ID
            bundle.mkdir(parents=True)
            write_pair_bundle(bundle)
            manifest_path = bundle / "bundle-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][0]["media_type"] = "application/octet-stream"
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(VALIDATOR.ValidationError, "media type"):
                VALIDATOR.validate_bundle(bundle)

    def test_complete_nonpassing_bundle_binds_structured_error_and_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / COMMIT / RUN_ID
            bundle.mkdir(parents=True)
            report_path = write_pair_bundle(bundle)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            failed = report["scenarios"][0]
            failed["status"] = "fail"
            failed["reason"] = "Synthetic DAO failure."
            report["scenario_counts"]["pass"] -= 1
            report["scenario_counts"]["fail"] += 1
            report["status"] = "fail"
            report["status_reason"] = "Synthetic DAO failure."
            log_path = bundle / failed["operation_log"]["path"]
            log = json.loads(log_path.read_text(encoding="utf-8"))
            log["final_status"] = "fail"
            log["entries"][-1]["status"] = "fail"
            log["entries"][-1]["error"] = {
                "exception_type": "System.Runtime.InteropServices.COMException",
                "hresult": "0x800A0CBB",
                "message": "Synthetic normalized COM failure.",
                "cleanup_errors": [],
            }
            write_json(log_path, log)
            failed["operation_log"]["sha256"] = sha256(log_path)
            refresh_manifest_entry(bundle, failed["operation_log"]["path"])
            refresh_report(bundle, report)
            manifest_path = bundle / "bundle-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["status"] = "fail"
            write_json(manifest_path, manifest)
            VALIDATOR.validate_bundle(bundle)

    def test_real_runner_shaped_nonpassing_logs_are_protocol_valid(self):
        scenario = load_example("DAO-GEN-BINARY-MARKER-001.scenario.json")
        normalized = {
            "exception_type": "System.Runtime.InteropServices.COMException",
            "hresult": "0x800A0CBB",
            "message": "Synthetic normalized COM failure.",
            "cleanup_errors": [],
        }
        passing = operation_log_for_scenario(scenario)
        cases = (
            ("blocked", 1, False),
            ("fail", 2, False),
            ("error", 2, False),
            ("error", len(passing["entries"]), True),
        )
        for status, attempted_count, cleanup_failure in cases:
            with self.subTest(status=status, cleanup=cleanup_failure):
                log = copy.deepcopy(passing)
                if cleanup_failure:
                    failed_entries = log["entries"]
                    failed_entries[-2]["status"] = "error"
                    failed_entries[-2]["error"] = copy.deepcopy(normalized)
                    failed_entries[-2]["error"]["cleanup_errors"] = [
                        "database.Close: synthetic cleanup failure"
                    ]
                    failed_entries[-1]["status"] = "error"
                    failed_entries[-1]["error"] = copy.deepcopy(
                        failed_entries[-2]["error"]
                    )
                else:
                    failed_entries = log["entries"][:attempted_count]
                    failed_entries[-1]["status"] = status
                    failed_entries[-1]["error"] = copy.deepcopy(normalized)
                    finalize = copy.deepcopy(log["entries"][-1])
                    finalize["sequence"] = attempted_count + 1
                    finalize["status"] = status
                    finalize["error"] = copy.deepcopy(normalized)
                    failed_entries.append(finalize)
                    log["entries"] = failed_entries
                log["final_status"] = status
                VALIDATOR.validate_document(log)
                VALIDATOR._validate_log_details(scenario, log, status)

    def test_bundle_rejects_pair_snapshot_reference_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / COMMIT / RUN_ID
            bundle.mkdir(parents=True)
            report_path = write_pair_bundle(bundle)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["pairs"][0]["left_snapshot"] = report["scenarios"][2]["dao_snapshot"]
            write_json(report_path, report)
            manifest_path = bundle / "bundle-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = next(item for item in manifest["files"] if item["path"] == "report.json")
            entry["sha256"] = sha256(report_path)
            entry["size_bytes"] = report_path.stat().st_size
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(VALIDATOR.ValidationError, "reference differs"):
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
                VALIDATOR.ValidationError, r"symlinks.*forbidden"
            ):
                VALIDATOR.validate_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
