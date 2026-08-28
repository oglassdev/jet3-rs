import hashlib
import json
import sys
import tempfile
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

    def test_row_keys_match_shared_canonical_vectors(self):
        fixture = (
            v1_2.SCHEMA_DIR / "fixtures" / "row-key-vectors.tsv"
        ).read_text(encoding="utf-8")
        seen = 0
        for line in fixture.splitlines():
            if line.startswith("#"):
                continue
            case, input_json, canonical_json, expected = line.split("\t")
            values = json.loads(input_json)
            canonical = canonical_json_bytes(values)
            self.assertNotEqual(input_json, canonical_json, case)
            self.assertEqual(canonical, (canonical_json + "\n").encode("utf-8"), case)
            self.assertEqual(hashlib.sha256(canonical).hexdigest(), expected, case)
            seen += 1
        self.assertEqual(seen, 3)

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

    def test_continuation_scenario_matches_recorded_boundary_recipe(self):
        scenario = self._find(self.inventory, "DAO-READ-SCHEMA-WIDE-TABLE")
        steps = scenario["generator_recipe"]["steps"]
        self.assertEqual(
            [step["action"] for step in steps],
            ["create_database", "create_table", "close_database"],
        )
        table = steps[1]
        self.assertEqual(table["name"], "BoundaryProbe")
        self.assertEqual(table["indexes"], [])
        self.assertEqual(
            {len(field["name"].encode("ascii")) for field in table["fields"]},
            {48},
        )
        self.assertEqual(
            table["fields"],
            [
                {
                    "name": f"Boundary_{index:02d}_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                    "dao_type": "dbText" if index % 2 else "dbLong",
                    "size": 31 if index % 2 else None,
                    "required": False,
                }
                for index in range(64)
            ],
        )
        self.assertEqual(
            scenario["capability_ids"], ["schema.catalog_and_table_definitions"]
        )
        self.assertIn("tdef.continuation_chain", scenario["required_branches"])

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

    def test_table_branch_requirements_match_recipe_shape(self):
        allocation_forms = {"allocation.inline_map", "allocation.indirect_map"}
        definition_forms = {"tdef.single_page", "tdef.continuation_chain"}
        scenarios_with_active_rows = 0
        scenarios_without_active_rows = 0

        for scenario in self.inventory["scenarios"]:
            if scenario["operation"]["expected_outcome"] != "success":
                continue
            steps = scenario["generator_recipe"]["steps"]
            tables = {}
            for step in steps:
                action = step["action"]
                if action == "create_table":
                    tables[step["name"]] = 0
                elif action == "drop_table":
                    tables.pop(step["name"], None)
                elif action == "insert_rows":
                    tables[step["table"]] += len(step["rows"]) * step["repeat"]
                elif action == "insert_until_page_count":
                    tables[step["table"]] += 1
                elif action == "delete_rows":
                    if step["count"] == "all":
                        tables[step["table"]] = 0
                    else:
                        tables[step["table"]] = max(
                            0, tables[step["table"]] - step["count"]
                        )

            required = set(scenario["required_branches"])
            has_tables = bool(tables)
            has_active_rows = any(count > 0 for count in tables.values())
            with self.subTest(scenario=scenario["id"]):
                if has_tables:
                    self.assertEqual(len(required & allocation_forms), 1)
                    self.assertEqual(len(required & definition_forms), 1)
                self.assertEqual("rows.direct" in required, has_active_rows)
            scenarios_with_active_rows += has_active_rows
            scenarios_without_active_rows += not has_active_rows

        self.assertGreater(scenarios_with_active_rows, 0)
        self.assertGreater(scenarios_without_active_rows, 0)

    def test_extended_and_wide_scenarios_require_only_their_actual_forms(self):
        indirect = self._find(
            self.inventory, "DAO-READ-ALLOC-EXTENDED-SLOT-1-ABOVE"
        )["required_branches"]
        wide = self._find(
            self.inventory, "DAO-READ-SCHEMA-WIDE-TABLE"
        )["required_branches"]
        empty = self._find(
            self.inventory, "DAO-READ-ROWS-EMPTY-TABLE"
        )["required_branches"]

        self.assertIn("allocation.indirect_map", indirect)
        self.assertNotIn("allocation.inline_map", indirect)
        self.assertIn("tdef.continuation_chain", wide)
        self.assertNotIn("tdef.single_page", wide)
        self.assertNotIn("rows.direct", empty)

    def test_small_index_property_recipes_do_not_claim_branched_controls(self):
        index_scenarios = [
            scenario
            for scenario in self.inventory["scenarios"]
            if scenario["id"].startswith("DAO-READ-SCHEMA-INDEX-")
        ]
        self.assertEqual(len(index_scenarios), 6)
        for scenario in index_scenarios:
            steps = scenario["generator_recipe"]["steps"]
            inserts = [step for step in steps if step["action"] == "insert_rows"]
            with self.subTest(scenario=scenario["id"]):
                self.assertEqual(
                    sum(
                        len(insert["rows"]) * insert["repeat"] for insert in inserts
                    ),
                    3,
                )
                self.assertNotIn(
                    "index.branch_leaf_traversal", scenario["required_branches"]
                )

    def test_page_span_recipe_matches_the_recorded_one_variable_wide_layout(self):
        scenario = self._find(self.inventory, "DAO-READ-ROWS-PAGE-SPAN")
        steps = scenario["generator_recipe"]["steps"]
        table = next(step for step in steps if step["action"] == "create_table")
        insert = next(step for step in steps if step["action"] == "insert_rows")
        fields = {field["name"]: field for field in table["fields"]}
        values = {value["field"]: value for value in insert["rows"][0]}
        variable_fields = [
            field
            for field in table["fields"]
            if field["dao_type"] in ("dbText", "dbBinary")
        ]

        self.assertEqual([field["name"] for field in variable_fields], ["Payload"])
        self.assertEqual(fields["Payload"]["size"], 255)
        self.assertEqual(values["Payload"]["encoding"], "repeat_ascii")
        self.assertEqual(values["Payload"]["value"], {"unit": "O", "length": 255})
        self.assertEqual(insert["repeat"], 16)

        fixed_boundary = 1 + 4  # column count plus the recorded dbLong width
        variable_end = fixed_boundary + values["Payload"]["value"]["length"]
        variable_count = len(variable_fields)
        boundary_bytes = variable_count + 1
        jump_bytes = 1
        variable_count_bytes = 1
        presence_bytes = (len(fields) + 7) // 8
        row_length = (
            variable_end
            + boundary_bytes
            + jump_bytes
            + variable_count_bytes
            + presence_bytes
        )
        self.assertEqual(
            (fixed_boundary, variable_end, variable_count, row_length),
            (5, 260, 1, 265),
        )
        self.assertGreater(row_length, 255)
        self.assertIn("rows.wide_variable_layout", scenario["required_branches"])
        self.assertNotIn("rows.overflow_pointer", scenario["required_branches"])

    def test_insert_only_recipes_do_not_claim_growth_only_overflow_pointers(self):
        insert_only_actions = {
            "create_database",
            "create_table",
            "insert_rows",
            "insert_until_page_count",
            "reopen",
            "close_database",
        }
        offenders = []
        for scenario in self.inventory["scenarios"]:
            actions = {
                step["action"] for step in scenario["generator_recipe"]["steps"]
            }
            if (
                actions <= insert_only_actions
                and "rows.overflow_pointer" in scenario["required_branches"]
            ):
                offenders.append(scenario["id"])
        self.assertEqual(offenders, [])

    def test_row_branch_registry_tracks_decomposed_module_owners(self):
        registry = json.loads(v1_2.BRANCH_REGISTRY.read_text(encoding="utf-8"))
        owners = {
            branch["id"]: branch["module"]
            for branch in registry["branches"]
            if branch["id"] in {
                "rows.direct",
                "rows.overflow_pointer",
                "rows.wide_variable_layout",
            }
        }
        self.assertEqual(
            owners,
            {
                "rows.direct": "crates/jet3/src/row_cursor.rs",
                "rows.overflow_pointer": "crates/jet3/src/row_cursor.rs",
                "rows.wide_variable_layout": "crates/jet3/src/row_layout.rs",
            },
        )



if __name__ == "__main__":
    unittest.main()
