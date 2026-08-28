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

    def _validate(self, inventory):
        v1_2.SCHEMA_SET.validate(inventory)
        v1_2.validate_inventory(
            inventory, capability_ids=self.capabilities, branch_ids=self.branches
        )

    def _copy(self):
        return json.loads(json.dumps(self.inventory))

    def test_schemas_lint_and_committed_inventory_is_reproducible_and_valid(self):
        v1_2.validate_schemas()
        committed = build_v1_2_inventory.INVENTORY.read_text(encoding="utf-8")
        self.assertEqual(committed, build_v1_2_inventory.render(self.inventory))
        self.assertEqual(
            v1_2.validate_document_path(build_v1_2_inventory.INVENTORY),
            "dao_scenario_inventory",
        )
        self.assertGreaterEqual(len(self.inventory["scenarios"]), 90)
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
            edited["scenarios"][0]["content_sha256"] = v1_2.scenario_content_sha256(
                edited["scenarios"][0]
            )
            with self.assertRaisesRegex(ValidationError, message):
                self._validate(edited)
        write = self._copy()
        scenario = write["scenarios"][0]
        scenario["id"] = "DAO-WRITE-SMOKE"
        scenario["operation"]["mode"] = "dao_open_rust"
        scenario["content_sha256"] = v1_2.scenario_content_sha256(scenario)
        write["scenarios"].sort(key=lambda entry: entry["id"])
        with self.assertRaisesRegex(ValidationError, "not enabled"):
            self._validate(write)

    def test_recipe_values_must_match_declared_types(self):
        edited = self._copy()
        scenario = next(
            s for s in edited["scenarios"] if s["id"] == "DAO-READ-VALUES-LONG-REP"
        )
        insert = scenario["generator_recipe"]["steps"][2]
        insert["rows"][0][1]["value"] = 2**31
        scenario["content_sha256"] = v1_2.scenario_content_sha256(scenario)
        with self.assertRaisesRegex(ValidationError, "range"):
            self._validate(edited)

    def test_snapshot_rows_are_keyed_by_value_digest_and_duplicate_ordinal(self):
        values = {"Id": {"kind": "long", "value": 1}}
        key = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
        snapshot = {
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
                    "columns": [
                        {
                            "name": "Id",
                            "ordinal": 0,
                            "dao_type": "dbLong",
                            "auto_increment": False,
                            "size": 4,
                            "attributes": 0,
                            "properties": {},
                        }
                    ],
                    "indexes": [],
                    "properties": {},
                    "rows": [
                        {"canonical_key": key, "duplicate_ordinal": 0, "values": values},
                        {"canonical_key": key, "duplicate_ordinal": 1, "values": values},
                    ],
                }
            ],
            "relationships": [],
            "raw_preservation": [],
            "producer_extensions": {},
        }
        self.assertEqual(v1_2.validate_document(snapshot), "canonical_semantic_snapshot")
        broken = json.loads(json.dumps(snapshot))
        broken["tables"][0]["rows"][1]["duplicate_ordinal"] = 2
        with self.assertRaisesRegex(ValidationError, "duplicate_ordinal"):
            v1_2.validate_document(broken)
        broken = json.loads(json.dumps(snapshot))
        broken["tables"][0]["rows"][0]["canonical_key"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "canonical_key"):
            v1_2.validate_document(broken)
        legacy = json.loads(json.dumps(snapshot))
        legacy["tables"][0]["columns"][0]["nullable"] = True
        with self.assertRaises(ValidationError):
            v1_2.validate_document(legacy)


if __name__ == "__main__":
    unittest.main()
