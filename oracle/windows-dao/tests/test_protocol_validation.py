import hashlib
import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from protocol_validation import (  # noqa: E402
    ValidationError,
    canonical_json_bytes,
    lint_schema,
    validate_schema_value,
)
import build_v1_2_inventory  # noqa: E402
import validate_protocol_v1_2 as v1_2  # noqa: E402


class SharedProtocolValidationTests(unittest.TestCase):
    def test_schema_lint_rejects_an_unimplemented_keyword(self):
        with self.assertRaisesRegex(ValidationError, "unsupported schema keywords"):
            lint_schema({"type": "integer", "multipleOf": 2})

    def test_maximum_and_max_items_are_enforced_in_the_main_walk(self):
        schema = {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "maxItems": 1,
                    "items": {"type": "integer", "maximum": 255},
                }
            },
            "required": ["values"],
            "additionalProperties": False,
        }
        lint_schema(schema)
        validate_schema_value({"values": [255]}, schema, schema, "$")
        with self.assertRaisesRegex(ValidationError, "above maximum"):
            validate_schema_value({"values": [256]}, schema, schema, "$")
        with self.assertRaisesRegex(ValidationError, "too many items"):
            validate_schema_value({"values": [1, 2]}, schema, schema, "$")

    def test_prefix_items_are_positional_and_items_false_closes_the_array(self):
        schema = {
            "type": "array",
            "prefixItems": [{"const": "a"}, {"type": "integer"}],
            "items": False,
        }
        lint_schema(schema)
        validate_schema_value(["a", 1], schema, schema, "$")
        validate_schema_value(["a"], schema, schema, "$")
        with self.assertRaisesRegex(ValidationError, "const"):
            validate_schema_value([1, "a"], schema, schema, "$")
        with self.assertRaisesRegex(ValidationError, "beyond prefixItems are forbidden"):
            validate_schema_value(["a", 1, 2], schema, schema, "$")
        with self.assertRaisesRegex(ValidationError, "prefixItems"):
            lint_schema({"type": "array", "items": False})
        with self.assertRaisesRegex(ValidationError, "prefixItems"):
            lint_schema({"type": "array", "prefixItems": []})

    def test_m1_uses_the_shared_public_boundary(self):
        source = (SCRIPTS / "validate_m1_protocol.py").read_text(encoding="utf-8")
        self.assertNotIn("importlib", source)
        self.assertNotIn("_V1", source)
        self.assertNotIn("_walk_schema_constraints", source)
        self.assertIn("SCHEMA_SET.validate(document)", source)


class ProtocolV12Tests(unittest.TestCase):
    """Cross-field rules of the 1.2 differential read contract."""

    @classmethod
    def setUpClass(cls):
        cls.capabilities = v1_2.load_capability_ids()
        cls.branches = v1_2.load_branch_ids()
        cls.inventory = build_v1_2_inventory.build_inventory()

    def _validate(self, inventory, complete=False):
        v1_2.SCHEMA_SET.validate(inventory)
        return v1_2.validate_inventory(
            inventory,
            capability_ids=self.capabilities,
            branch_ids=self.branches,
            complete=complete,
        )

    def _copy(self):
        return json.loads(json.dumps(self.inventory))

    def _rehash(self, scenario):
        scenario["content_sha256"] = v1_2.scenario_content_sha256(scenario)

    def _find(self, inventory, scenario_id):
        return next(s for s in inventory["scenarios"] if s["id"] == scenario_id)

    def test_schemas_lint_and_committed_inventory_is_reproducible_and_valid(self):
        v1_2.validate_schemas()
        committed = build_v1_2_inventory.INVENTORY.read_text(encoding="utf-8")
        self.assertEqual(committed, build_v1_2_inventory.render(self.inventory))
        self.assertEqual(
            v1_2.validate_document_path(build_v1_2_inventory.INVENTORY),
            "dao_scenario_inventory",
        )
        self.assertTrue(
            all(s["expected_snapshot_sha256"] is None for s in self.inventory["scenarios"])
        )

    def test_content_hash_covers_semantic_fields_but_not_serialization(self):
        inventory = self._copy()
        self._validate(inventory)
        edited = self._copy()
        edited["scenarios"][0]["required_branches"] = []
        with self.assertRaisesRegex(ValidationError, "content_sha256"):
            self._validate(edited)
        hash_only = self._copy()
        hash_only["scenarios"][0]["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "content_sha256"):
            self._validate(hash_only)
        scenario = inventory["scenarios"][0]
        reordered = json.loads(
            json.dumps({key: scenario[key] for key in reversed(list(scenario))}, indent=4)
        )
        self.assertEqual(
            v1_2.scenario_content_sha256(reordered), scenario["content_sha256"]
        )
        self.assertEqual(
            v1_2.scenario_content_sha256(reordered),
            hashlib.sha256(
                canonical_json_bytes(
                    {k: v for k, v in scenario.items() if k != "content_sha256"}
                )
            ).hexdigest(),
        )

    def test_registry_matrix_and_mode_gating_fail_closed(self):
        for field, value, message in (
            ("capability_ids", ["not.a_capability"], "support matrix"),
            ("required_branches", ["values.unknown"], "branch registry"),
            ("preserve_paths", ["/tables"], "preserve nothing"),
        ):
            edited = self._copy()
            edited["scenarios"][0][field] = value
            self._rehash(edited["scenarios"][0])
            with self.assertRaisesRegex(ValidationError, message):
                self._validate(edited)
        write = self._copy()
        scenario = write["scenarios"][0]
        scenario["id"] = "DAO-WRITE-SMOKE"
        scenario["operation"]["mode"] = "dao_open_rust"
        self._rehash(scenario)
        write["scenarios"].sort(key=lambda entry: entry["id"])
        with self.assertRaisesRegex(ValidationError, "not enabled"):
            self._validate(write)

    def test_recipe_values_must_match_declared_types(self):
        edited = self._copy()
        scenario = self._find(edited, "DAO-READ-VALUES-LONG-REP")
        scenario["generator_recipe"]["steps"][2]["rows"][0][1]["value"] = 2**31
        self._rehash(scenario)
        with self.assertRaisesRegex(ValidationError, "range"):
            self._validate(edited)

    def test_plan_minimum_set_is_present_or_explicitly_deferred(self):
        deferred = self._validate(self._copy())
        self.assertEqual(
            deferred,
            [
                "allocation.further_extended_slots",
                "allocation.inline_capacity_boundary",
                "open.largest_supported_size",
                "values.code_page_cp1251",
            ],
        )
        with self.assertRaisesRegex(ValidationError, "incomplete"):
            self._validate(self._copy(), complete=True)
        silent = self._copy()
        silent["scenarios"] = [
            s for s in silent["scenarios"] if s["id"] != "DAO-READ-SCHEMA-TYPE-GUID"
        ]
        with self.assertRaisesRegex(ValidationError, "schema.every_type.*not deferred"):
            self._validate(silent)
        stale = self._copy()
        stale["deferred_requirements"].append(
            {
                "requirement": "allocation.small_inline",
                "reason": "x",
                "provenance_needed": "y",
            }
        )
        stale["deferred_requirements"].sort(key=lambda entry: entry["requirement"])
        with self.assertRaisesRegex(ValidationError, "deferred although"):
            self._validate(stale)

        semantic_drift = self._copy()
        guid = self._find(semantic_drift, "DAO-READ-SCHEMA-TYPE-GUID")
        guid["generator_recipe"]["steps"][1]["fields"][1]["dao_type"] = "dbLong"
        self._rehash(guid)
        with self.assertRaisesRegex(ValidationError, "differs from its generated contract"):
            self._validate(semantic_drift)

    def test_branch_requirements_follow_recorded_storage_forms(self):
        fixed = self._find(self.inventory, "DAO-READ-VALUES-LONG-REP")
        text = self._find(self.inventory, "DAO-READ-VALUES-TEXT-REP")
        memo_2048 = self._find(self.inventory, "DAO-READ-VALUES-MEMO-CHAINED-2048")
        memo_max = self._find(self.inventory, "DAO-READ-VALUES-MEMO-MAX-32769")
        self.assertIn("values.fixed_scalar", fixed["required_branches"])
        self.assertNotIn("values.fixed_scalar", text["required_branches"])
        self.assertIn("values.variable_short", text["required_branches"])
        self.assertIn("long_value.chained", memo_2048["required_branches"])
        self.assertFalse(
            [b for b in memo_max["required_branches"] if b.startswith("long_value.")]
        )
        self.assertNotIn("values.fixed_scalar", memo_2048["required_branches"])
        all_types = "values.all_dao_jet3_table_types"
        self.assertIn(all_types, fixed["capability_ids"])
        self.assertIn(
            all_types, self._find(self.inventory, "DAO-READ-SCHEMA-TYPE-GUID")["capability_ids"]
        )
        for scenario in self.inventory["scenarios"]:
            if scenario["boundary"] is not None:
                boundary = scenario["boundary"]
                self.assertEqual(boundary["dimension"], "extended_slot_0_page_capacity")
                insert = next(
                    step
                    for step in scenario["generator_recipe"]["steps"]
                    if step["action"] == "insert_until_page_count"
                )
                self.assertTrue(insert["require_exact_page_count"])
                if boundary["position"] in ("below", "at"):
                    self.assertIn(
                        "allocation.extended_slot", boundary["forbidden_branches"]
                    )
                else:
                    self.assertIn(
                        "allocation.extended_slot", scenario["required_branches"]
                    )

    def _snapshot(self):
        values = {
            "Id": {"kind": "long", "raw_hex": "01000000", "value": 1},
            "Flag": {"kind": "boolean", "value": True},
        }
        key = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
        column = {
            "name": "Id",
            "ordinal": 0,
            "dao_type": "dbLong",
            "auto_increment": False,
            "size": 4,
            "attributes": 0,
            "properties": {},
        }
        flag = dict(column, name="Flag", ordinal=1, dao_type="dbBoolean", size=1)
        return {
            "protocol_version": "1.2.0",
            "document_type": "canonical_semantic_snapshot",
            "scenario_id": "DAO-READ-ROWS-DUPLICATES",
            "producer": {"kind": "rust", "source_revision": "test"},
            "database_sha256": "ab" * 32,
            "ordering": {
                "objects": "name_codepoint_ascending",
                "columns": "ordinal_ascending",
                "indexes": "name_codepoint_ascending",
                "relationships": "name_codepoint_ascending",
                "rows": "values_sha256_then_duplicate_ordinal",
                "object_keys": "unicode_codepoint_ascending",
            },
            "comparison_projection": ["/producer", "/producer_extensions"],
            "database_properties": {},
            "tables": [
                {
                    "name": "Items",
                    "kind": "user",
                    "attributes": 0,
                    "columns": [column, flag],
                    "indexes": [
                        {
                            "name": "PK",
                            "primary": True,
                            "unique": True,
                            "required": True,
                            "fields": [{"name": "Id", "descending": False}],
                            "properties": {},
                        }
                    ],
                    "properties": {},
                    "rows": [
                        {"canonical_key": key, "duplicate_ordinal": 0, "values": values},
                        {"canonical_key": key, "duplicate_ordinal": 1, "values": values},
                    ],
                }
            ],
            "relationships": [],
            "raw_preservation": [],
            "producer_extensions": {"/tables/0/columns/0/required": {"kind": "boolean", "value": True}},
        }

    def test_snapshot_rows_are_keyed_by_value_digest_and_duplicate_ordinal(self):
        snapshot = self._snapshot()
        self.assertEqual(v1_2.validate_document(snapshot), "canonical_semantic_snapshot")
        broken = self._snapshot()
        broken["tables"][0]["rows"][1]["duplicate_ordinal"] = 2
        with self.assertRaisesRegex(ValidationError, "duplicate_ordinal"):
            v1_2.validate_document(broken)
        broken = self._snapshot()
        broken["tables"][0]["rows"][0]["canonical_key"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "canonical_key"):
            v1_2.validate_document(broken)
        legacy = self._snapshot()
        legacy["tables"][0]["columns"][0]["nullable"] = True
        with self.assertRaises(ValidationError):
            v1_2.validate_document(legacy)

    def test_snapshot_model_integrity_and_lossless_raw_are_enforced(self):
        def rows_with(values):
            key = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
            return [{"canonical_key": key, "duplicate_ordinal": 0, "values": values}]

        missing_column = self._snapshot()
        missing_column["tables"][0]["rows"] = rows_with(
            {"Id": {"kind": "long", "raw_hex": "01000000", "value": 1}}
        )
        with self.assertRaisesRegex(ValidationError, "declared column names"):
            v1_2.validate_document(missing_column)
        wrong_kind = self._snapshot()
        wrong_kind["tables"][0]["rows"] = rows_with(
            {
                "Id": {"kind": "text", "raw_hex": "31", "value": "1"},
                "Flag": {"kind": "boolean", "value": True},
            }
        )
        with self.assertRaisesRegex(ValidationError, "not admitted for dbLong"):
            v1_2.validate_document(wrong_kind)
        no_raw = self._snapshot()
        no_raw["tables"][0]["rows"] = rows_with(
            {"Id": {"kind": "long", "value": 1}, "Flag": {"kind": "boolean", "value": True}}
        )
        with self.assertRaisesRegex(ValidationError, "raw_hex"):
            v1_2.validate_document(no_raw)
        property_no_raw = self._snapshot()
        property_no_raw["database_properties"]["Version"] = {
            "kind": "long",
            "value": 1,
        }
        with self.assertRaisesRegex(ValidationError, "database_properties.*raw_hex"):
            v1_2.validate_document(property_no_raw)
        wrong_width = self._snapshot()
        wrong_width["tables"][0]["rows"] = rows_with(
            {
                "Id": {"kind": "long", "raw_hex": "00", "value": 0},
                "Flag": {"kind": "boolean", "value": True},
            }
        )
        with self.assertRaisesRegex(ValidationError, "expected 4 bytes for dbLong"):
            v1_2.validate_document(wrong_width)
        text_without_code_page = self._snapshot()
        text_without_code_page["tables"][0]["columns"][0]["dao_type"] = "dbText"
        text_without_code_page["tables"][0]["columns"][0]["size"] = 32
        text_without_code_page["tables"][0]["rows"] = rows_with(
            {
                "Id": {"kind": "text", "raw_hex": "41", "value": "A"},
                "Flag": {"kind": "boolean", "value": True},
            }
        )
        with self.assertRaisesRegex(ValidationError, "identify their code_page"):
            v1_2.validate_document(text_without_code_page)
        bad_index = self._snapshot()
        bad_index["tables"][0]["indexes"][0]["fields"][0]["name"] = "Missing"
        with self.assertRaisesRegex(ValidationError, "unknown columns"):
            v1_2.validate_document(bad_index)
        bad_relationship = self._snapshot()
        bad_relationship["relationships"] = [
            {
                "name": "R",
                "table": "Items",
                "foreign_table": "Nowhere",
                "attributes": 0,
                "fields": [{"field": "Id", "foreign_field": "Id"}],
                "properties": {},
            }
        ]
        with self.assertRaisesRegex(ValidationError, "unknown table"):
            v1_2.validate_document(bad_relationship)


if __name__ == "__main__":
    unittest.main()
