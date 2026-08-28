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
            "attributes": 1,
            "properties": {},
        }
        flag = dict(column, name="Flag", ordinal=1, dao_type="dbBoolean", size=1)
        return {
            "protocol_version": "1.2.0",
            "document_type": "canonical_semantic_snapshot",
            "scenario_id": "DAO-READ-ROWS-DUPLICATES",
            "producer": {"kind": "rust", "source_revision": "test"},
            "database_sha256": "ab" * 32,
            "outcome": "success",
            "error_class": None,
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

    def _success_receipt(self, snapshot=None):
        snapshot = snapshot or self._snapshot()
        required = self._find(
            self.inventory, snapshot["scenario_id"]
        )["required_branches"]
        return {
            "protocol_version": "1.2.0",
            "document_type": "rust_coverage_receipt",
            "scenario_id": snapshot["scenario_id"],
            "source_revision": snapshot["producer"]["source_revision"],
            "database_sha256": snapshot["database_sha256"],
            "allocated_set_sha256": "cd" * 32,
            "outcome": "success",
            "error_class": None,
            "branches": sorted(required),
        }

    def test_shared_column_normalization_vectors(self):
        fixture = (
            v1_2.SCHEMA_DIR / "fixtures" / "column-normalization-vectors.tsv"
        )
        seen = 0
        for line in fixture.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                continue
            (
                dao_type,
                declared_size,
                _storage,
                auto_increment,
                normalized_size,
                normalized_attributes,
            ) = line.split("\t")
            document = self._snapshot()
            column = document["tables"][0]["columns"][0]
            column.update(
                dao_type=dao_type,
                size=int(normalized_size),
                attributes=int(normalized_attributes),
                auto_increment=auto_increment == "true",
            )
            if dao_type != "dbLong":
                document["tables"][0]["rows"] = []
            v1_2.validate_document(document)
            self.assertEqual(int(declared_size), int(normalized_size))
            self.assertEqual(
                v1_2.normalize_dao_column_attributes(
                    int(normalized_attributes) | 0x4000
                ),
                int(normalized_attributes),
            )
            seen += 1
        self.assertEqual(seen, 15)

    def test_shared_long_value_vectors_are_payload_projected(self):
        fixture = v1_2.SCHEMA_DIR / "fixtures" / "long-value-comparison-vectors.tsv"
        hashes_by_kind = {}
        seen = 0
        for line in fixture.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                continue
            case, kind, storage, semantic, payload, header, expected = line.split("\t")
            value = {"kind": kind, "raw_hex": payload, "value": semantic}
            dao_type = "dbMemo" if kind == "memo" else "dbLongBinary"
            if kind == "memo":
                value["code_page"] = 1252
            values = {"Value": value}
            actual = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
            self.assertEqual(actual, expected, case)
            hashes_by_kind.setdefault((kind, semantic), set()).add(actual)

            snapshot = self._snapshot()
            table = snapshot["tables"][0]
            table["columns"] = [dict(
                table["columns"][0], name="Value", dao_type=dao_type,
                size=0, attributes=2, auto_increment=False,
            )]
            table["indexes"] = []
            table["rows"] = [{
                "canonical_key": actual,
                "duplicate_ordinal": 0,
                "values": values,
            }]
            snapshot["producer_extensions"] = {}
            if storage != "inline":
                path = "/tables/0/rows/0/values/Value/jet_external_long_value_header"
                snapshot["producer_extensions"][path] = {
                    "kind": "binary", "raw_hex": header, "value": header,
                }
            self.assertEqual(
                v1_2.validate_document(snapshot), "canonical_semantic_snapshot", case
            )
            seen += 1
        self.assertEqual(seen, 8)
        self.assertTrue(all(len(hashes) == 1 for hashes in hashes_by_kind.values()))

    def test_shared_text_code_page_vectors_match_full_python_validation(self):
        fixture = v1_2.SCHEMA_DIR / "fixtures" / "text-code-page-vectors.tsv"
        seen = 0
        for line in fixture.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                continue
            case, kind, code_page, raw_hex, value, expected_valid = line.split("\t")
            document = self._snapshot()
            document["database_properties"]["TextVector"] = {
                "kind": kind,
                "code_page": json.loads(code_page),
                "raw_hex": raw_hex,
                "value": value,
            }
            with self.subTest(case=case):
                if expected_valid == "true":
                    self.assertEqual(
                        v1_2.validate_document(document),
                        "canonical_semantic_snapshot",
                    )
                else:
                    with self.assertRaises(ValidationError):
                        v1_2.validate_document(document)
            seen += 1
        self.assertEqual(seen, 8)

    def test_rejected_format_outcomes_follow_shared_normalization_vectors(self):
        fixture = (
            v1_2.SCHEMA_DIR
            / "fixtures"
            / "rejected-format-normalization-vectors.tsv"
        )
        seen = 0
        for line in fixture.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                continue
            case, scenario_id, _variant, error_class = line.split("\t")
            scenario = self._find(self.inventory, scenario_id)
            self.assertEqual(scenario["operation"]["error_class"], error_class, case)
            snapshot, receipt = self._opening_failure(scenario_id, error_class)
            self.assertEqual(
                v1_2.validate_document(snapshot),
                "canonical_semantic_snapshot",
                case,
            )
            self.assertEqual(
                v1_2.validate_document(receipt), "rust_coverage_receipt", case
            )
            seen += 1
        self.assertEqual(seen, 3)

    def test_success_and_opening_failure_artifact_pairs_are_bound(self):
        success_snapshot = self._snapshot()
        success_receipt = self._success_receipt(success_snapshot)
        v1_2.validate_artifact_pair(success_snapshot, success_receipt)

        failure_snapshot, failure_receipt = self._opening_failure(
            "DAO-READ-OPEN-REJECT-JET4", "unsupported_version"
        )
        v1_2.validate_artifact_pair(failure_snapshot, failure_receipt)

    def test_artifact_pair_rejects_documents_mixed_between_valid_bundles(self):
        first_snapshot = self._snapshot()
        first_receipt = self._success_receipt(first_snapshot)
        second_snapshot = json.loads(json.dumps(first_snapshot))
        second_snapshot["producer"]["source_revision"] = "other-revision"
        second_snapshot["database_sha256"] = "ef" * 32
        second_receipt = self._success_receipt(second_snapshot)

        v1_2.validate_artifact_pair(first_snapshot, first_receipt)
        v1_2.validate_artifact_pair(second_snapshot, second_receipt)
        with self.assertRaisesRegex(
            ValidationError, "source_revision, database_sha256"
        ):
            v1_2.validate_artifact_pair(first_snapshot, second_receipt)

    def test_artifact_pair_rejects_each_cross_document_binding_mutation(self):
        snapshot = self._snapshot()
        receipt = self._success_receipt(snapshot)

        wrong_scenario = json.loads(json.dumps(receipt))
        wrong_scenario["scenario_id"] = "DAO-READ-ROWS-SINGLE"
        wrong_scenario["branches"] = sorted(
            self._find(self.inventory, "DAO-READ-ROWS-SINGLE")["required_branches"]
        )
        with self.assertRaisesRegex(ValidationError, "scenario_id"):
            v1_2.validate_artifact_pair(snapshot, wrong_scenario)

        wrong_revision = json.loads(json.dumps(receipt))
        wrong_revision["source_revision"] = "other-revision"
        with self.assertRaisesRegex(ValidationError, "source_revision"):
            v1_2.validate_artifact_pair(snapshot, wrong_revision)

        wrong_database = json.loads(json.dumps(receipt))
        wrong_database["database_sha256"] = "ef" * 32
        with self.assertRaisesRegex(ValidationError, "database_sha256"):
            v1_2.validate_artifact_pair(snapshot, wrong_database)

        dao_snapshot = json.loads(json.dumps(snapshot))
        dao_snapshot["producer"]["kind"] = "dao"
        with self.assertRaisesRegex(ValidationError, "Rust snapshot producer"):
            v1_2.validate_artifact_pair(dao_snapshot, receipt)

    def test_artifact_pair_rejects_constant_outcome_allocation_and_error_mutations(self):
        snapshot = self._snapshot()
        receipt = self._success_receipt(snapshot)

        for field, value in (
            ("protocol_version", "1.1.0"),
            ("document_type", "rust_coverage_receipt"),
        ):
            mutated = json.loads(json.dumps(snapshot))
            mutated[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                v1_2.validate_artifact_pair(mutated, receipt)

        success_without_allocation = json.loads(json.dumps(receipt))
        success_without_allocation["allocated_set_sha256"] = None
        with self.assertRaises(ValidationError):
            v1_2.validate_artifact_pair(snapshot, success_without_allocation)

        success_with_error = json.loads(json.dumps(receipt))
        success_with_error["error_class"] = "unsupported_version"
        with self.assertRaises(ValidationError):
            v1_2.validate_artifact_pair(snapshot, success_with_error)

        failure_snapshot, failure_receipt = self._opening_failure(
            "DAO-READ-OPEN-REJECT-JET4", "unsupported_version"
        )
        failure_with_allocation = json.loads(json.dumps(failure_receipt))
        failure_with_allocation["allocated_set_sha256"] = "cd" * 32
        with self.assertRaises(ValidationError):
            v1_2.validate_artifact_pair(failure_snapshot, failure_with_allocation)

        failure_without_error = json.loads(json.dumps(failure_receipt))
        failure_without_error["error_class"] = None
        with self.assertRaises(ValidationError):
            v1_2.validate_artifact_pair(failure_snapshot, failure_without_error)

        wrong_error = json.loads(json.dumps(failure_receipt))
        wrong_error["error_class"] = "encrypted_database"
        with self.assertRaisesRegex(ValidationError, "does not match"):
            v1_2.validate_artifact_pair(failure_snapshot, wrong_error)

        with self.assertRaises(ValidationError):
            v1_2.validate_artifact_pair(snapshot, failure_receipt)

    def test_artifact_pair_paths_require_canonical_bytes(self):
        snapshot = self._snapshot()
        receipt = self._success_receipt(snapshot)
        with tempfile.TemporaryDirectory() as temporary:
            snapshot_path = Path(temporary) / "snapshot.json"
            receipt_path = Path(temporary) / "coverage-receipt.json"
            snapshot_path.write_bytes(canonical_json_bytes(snapshot))
            receipt_path.write_bytes(canonical_json_bytes(receipt))
            v1_2.validate_artifact_pair_paths(snapshot_path, receipt_path)

            snapshot_path.write_text(
                json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValidationError, "not normalized"):
                v1_2.validate_artifact_pair_paths(snapshot_path, receipt_path)

    def test_rejected_format_outcome_mutations_fail_closed(self):
        snapshot, receipt = self._opening_failure(
            "DAO-READ-OPEN-REJECT-JET4", "unsupported_version"
        )

        wrong_outcome = json.loads(json.dumps(snapshot))
        wrong_outcome["outcome"] = "success"
        with self.assertRaisesRegex(ValidationError, "allowed shape"):
            v1_2.validate_document(wrong_outcome)

        success_scenario = json.loads(json.dumps(snapshot))
        success_scenario["scenario_id"] = "DAO-READ-ROWS-SINGLE"
        with self.assertRaisesRegex(ValidationError, "expected_error scenario"):
            v1_2.validate_document(success_scenario)

        wrong_error = json.loads(json.dumps(snapshot))
        wrong_error["error_class"] = "encrypted_database"
        with self.assertRaisesRegex(ValidationError, "does not match"):
            v1_2.validate_document(wrong_error)

        missing_branch = json.loads(json.dumps(receipt))
        missing_branch["branches"].remove("open.rejected_format")
        with self.assertRaisesRegex(ValidationError, "allowed shape"):
            v1_2.validate_document(missing_branch)

        success_receipt = json.loads(json.dumps(receipt))
        success_receipt["scenario_id"] = "DAO-READ-ROWS-SINGLE"
        with self.assertRaisesRegex(ValidationError, "expected_error scenario"):
            v1_2.validate_document(success_receipt)

    def _opening_failure(self, scenario_id, error_class):
        common = {
            "protocol_version": "1.2.0",
            "scenario_id": scenario_id,
            "database_sha256": "ab" * 32,
            "outcome": "opening_failure",
            "error_class": error_class,
        }
        snapshot = {
            **common,
            "document_type": "canonical_semantic_snapshot",
            "producer": {"kind": "rust", "source_revision": "abc123"},
            "comparison_projection": ["/producer"],
        }
        receipt = {
            **common,
            "document_type": "rust_coverage_receipt",
            "source_revision": "abc123",
            "allocated_set_sha256": None,
            "branches": [
                "open.header_page",
                "open.rejected_format",
                "open.signature_geometry",
            ],
        }
        return snapshot, receipt

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

    def test_snapshot_column_ordinals_are_contiguous_from_zero(self):
        self.assertEqual(
            [column["ordinal"] for column in self._snapshot()["tables"][0]["columns"]],
            [0, 1],
        )

        missing_zero = self._snapshot()
        missing_zero["tables"][0]["columns"][0]["ordinal"] = 1
        with self.assertRaisesRegex(ValidationError, "contiguous from zero"):
            v1_2.validate_document(missing_zero)

        gap = self._snapshot()
        gap["tables"][0]["columns"][1]["ordinal"] = 2
        with self.assertRaisesRegex(ValidationError, "contiguous from zero"):
            v1_2.validate_document(gap)

    def test_snapshot_raw_preservation_paths_are_unique_and_canonical(self):
        valid = self._snapshot()
        valid["raw_preservation"] = [
            {"semantic_path": "/tables/0", "raw_hex": "00", "purpose": "table"},
            {
                "semantic_path": "/tables/0/rows/0",
                "raw_hex": "01",
                "purpose": "row",
            },
        ]
        self.assertEqual(
            v1_2.validate_document(valid), "canonical_semantic_snapshot"
        )

        duplicate = json.loads(json.dumps(valid))
        duplicate["raw_preservation"][1]["semantic_path"] = "/tables/0"
        with self.assertRaisesRegex(ValidationError, "unique and canonically ordered"):
            v1_2.validate_document(duplicate)

        reversed_paths = json.loads(json.dumps(valid))
        reversed_paths["raw_preservation"].reverse()
        with self.assertRaisesRegex(ValidationError, "unique and canonically ordered"):
            v1_2.validate_document(reversed_paths)

    def test_snapshot_comparison_projection_requires_both_exclusions(self):
        for projection in ([], ["/producer"]):
            snapshot = self._snapshot()
            snapshot["comparison_projection"] = projection
            with self.assertRaisesRegex(ValidationError, "too few items"):
                v1_2.validate_document(snapshot)
        self.assertEqual(
            v1_2.validate_document(self._snapshot()),
            "canonical_semantic_snapshot",
        )

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
                "Id": {
                    "kind": "text",
                    "raw_hex": "31",
                    "code_page": 1252,
                    "value": "1",
                },
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
        with self.assertRaisesRegex(ValidationError, "code_page"):
            v1_2.validate_document(text_without_code_page)
        bad_index = self._snapshot()
        bad_index["tables"][0]["indexes"][0]["fields"][0]["name"] = "Missing"
        with self.assertRaisesRegex(ValidationError, "unknown columns"):
            v1_2.validate_document(bad_index)
        bad_primary = self._snapshot()
        bad_primary["tables"][0]["indexes"][0]["unique"] = False
        with self.assertRaisesRegex(ValidationError, "primary indexes must be unique and required"):
            v1_2.validate_document(bad_primary)
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

    def test_coverage_receipt_is_canonical_and_closed_to_registered_branches(self):
        required = self._find(
            self.inventory, "DAO-READ-ROWS-SINGLE"
        )["required_branches"]
        receipt = {
            "protocol_version": "1.2.0",
            "document_type": "rust_coverage_receipt",
            "scenario_id": "DAO-READ-ROWS-SINGLE",
            "source_revision": "abc123",
            "database_sha256": "ab" * 32,
            "allocated_set_sha256": "cd" * 32,
            "outcome": "success",
            "error_class": None,
            "branches": sorted(required),
        }
        self.assertEqual(v1_2.validate_document(receipt), "rust_coverage_receipt")
        unknown = json.loads(json.dumps(receipt))
        unknown["branches"].append("rows.imaginary")
        unknown["branches"].sort()
        with self.assertRaisesRegex(ValidationError, "not in the branch registry"):
            v1_2.validate_document(unknown)
        reordered = json.loads(json.dumps(receipt))
        reordered["branches"].reverse()
        with self.assertRaisesRegex(ValidationError, "unique and sorted"):
            v1_2.validate_document(reordered)

        unknown_scenario = json.loads(json.dumps(receipt))
        unknown_scenario["scenario_id"] = "DAO-READ-NOT-IN-INVENTORY"
        with self.assertRaisesRegex(ValidationError, "unknown scenario"):
            v1_2.validate_document(unknown_scenario)

        missing_required = json.loads(json.dumps(receipt))
        missing_required["branches"].remove(required[0])
        with self.assertRaisesRegex(ValidationError, "missing required scenario branches"):
            v1_2.validate_document(missing_required)

    def test_coverage_receipt_rejects_forbidden_and_overlapping_scenario_branches(self):
        scenario = self._find(
            self.inventory, "DAO-READ-ALLOC-EXTENDED-SLOT-1-BELOW"
        )
        forbidden = scenario["boundary"]["forbidden_branches"][0]
        receipt = {
            "protocol_version": "1.2.0",
            "document_type": "rust_coverage_receipt",
            "scenario_id": scenario["id"],
            "source_revision": "abc123",
            "database_sha256": "ab" * 32,
            "allocated_set_sha256": "cd" * 32,
            "outcome": "success",
            "error_class": None,
            "branches": sorted([*scenario["required_branches"], forbidden]),
        }
        with self.assertRaisesRegex(ValidationError, "forbidden scenario branches"):
            v1_2.validate_document(receipt)

        overlapping_inventory = self._copy()
        overlapping_scenario = self._find(overlapping_inventory, scenario["id"])
        overlapping_scenario["boundary"]["forbidden_branches"] = [
            overlapping_scenario["required_branches"][0]
        ]
        self._rehash(overlapping_scenario)
        with tempfile.TemporaryDirectory() as temporary:
            inventory_path = Path(temporary) / "scenarios.json"
            inventory_path.write_bytes(canonical_json_bytes(overlapping_inventory))
            receipt["branches"] = sorted(scenario["required_branches"])
            with self.assertRaisesRegex(
                ValidationError, "both required and forbidden"
            ):
                v1_2.validate_coverage_receipt(
                    receipt, scenario_inventory_path=inventory_path
                )


if __name__ == "__main__":
    unittest.main()
