import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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


class ProtocolV12SnapshotTests(unittest.TestCase):
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

    def _apply_semantic_name_mutation(self, snapshot, mutation):
        table = snapshot["tables"][0]

        if mutation == "none":
            return
        if mutation == "table_name":
            table["name"] = ""
            return
        if mutation == "column_name":
            table["columns"][0]["name"] = ""
            table["indexes"][0]["fields"][0]["name"] = ""
            values = dict(table["rows"][0]["values"])
            values[""] = values.pop("Id")
            canonical_key = hashlib.sha256(canonical_json_bytes(values)).hexdigest()
            for row in table["rows"]:
                row["values"] = json.loads(json.dumps(values))
                row["canonical_key"] = canonical_key
            return
        if mutation == "index_name":
            table["indexes"][0]["name"] = ""
            return
        if mutation == "index_field":
            table["indexes"][0]["fields"][0]["name"] = ""
            return
        if mutation == "database_property_name":
            snapshot["database_properties"][""] = {
                "kind": "boolean",
                "value": True,
            }
            return

        relationship = {
            "name": "Self",
            "table": "Items",
            "foreign_table": "Items",
            "attributes": 0,
            "fields": [{"field": "Id", "foreign_field": "Id"}],
            "properties": {},
        }
        if mutation == "relationship_name":
            relationship["name"] = ""
        elif mutation == "relationship_table":
            relationship["table"] = ""
        elif mutation == "relationship_foreign_table":
            relationship["foreign_table"] = ""
        elif mutation == "relationship_field":
            relationship["fields"][0]["field"] = ""
        elif mutation == "relationship_foreign_field":
            relationship["fields"][0]["foreign_field"] = ""
        elif mutation in ("raw_semantic_path", "raw_purpose"):
            raw = {
                "semantic_path": "/tables/0",
                "raw_hex": "00",
                "purpose": "test",
            }
            raw["semantic_path" if mutation == "raw_semantic_path" else "purpose"] = ""
            snapshot["raw_preservation"] = [raw]
            return
        else:
            self.fail(f"unknown semantic-name mutation {mutation!r}")
        snapshot["relationships"] = [relationship]

    def test_shared_semantic_name_vectors_match_schema_and_python_validation(self):
        fixture = v1_2.SCHEMA_DIR / "fixtures" / "semantic-name-vectors.tsv"
        seen = 0
        for line_number, line in enumerate(
            fixture.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.startswith("#"):
                continue
            case, mutation, validation_layers, expected_valid = line.split("\t")
            snapshot = self._snapshot()
            self._apply_semantic_name_mutation(snapshot, mutation)
            expected_valid = expected_valid == "true"
            validators = [v1_2.validate_semantic_snapshot]
            if validation_layers == "schema_and_semantic":
                validators.insert(0, v1_2.SCHEMA_SET.validate)
            elif validation_layers != "semantic_only":
                self.fail(f"unknown validation layers {validation_layers!r}")
            for validator in validators:
                try:
                    validator(snapshot)
                    actual_valid = True
                except ValidationError:
                    actual_valid = False
                self.assertEqual(
                    actual_valid,
                    expected_valid,
                    f"{case} on line {line_number}: {validator.__name__}",
                )
            seen += 1
        self.assertEqual(seen, 13)

    def test_shared_canonical_float_vectors_match_python_validation_and_spelling(self):
        fixture = v1_2.SCHEMA_DIR / "fixtures" / "canonical-float-vectors.tsv"
        seen = 0
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            for line_number, line in enumerate(
                fixture.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if line.startswith("#"):
                    continue
                (
                    case,
                    kind,
                    bits_hex,
                    input_json,
                    canonical_json,
                    expected_valid,
                ) = line.split("\t")
                expected_valid = expected_valid == "true"
                unpack_format = {"single": ">f", "double": ">d"}.get(kind)
                self.assertIsNotNone(unpack_format, f"{case} on line {line_number}")
                bits_value = struct.unpack(
                    unpack_format, bytes.fromhex(bits_hex)
                )[0]
                typed_bits = {"kind": kind, "value": bits_value}
                self.assertEqual(
                    canonical_json_bytes(typed_bits),
                    f'{{"kind":"{kind}","value":{canonical_json}}}\n'.encode(),
                    f"{case} on line {line_number}: fixture bits",
                )

                semantic = json.loads(input_json)
                snapshot = self._snapshot()
                snapshot["database_properties"]["Float"] = {
                    "kind": kind,
                    "raw_hex": bits_hex,
                    "value": semantic,
                }
                path.write_bytes(canonical_json_bytes(snapshot))
                try:
                    document_type = v1_2.validate_document_path(path)
                    actual_valid = document_type == "canonical_semantic_snapshot"
                except ValidationError:
                    actual_valid = False
                self.assertEqual(
                    actual_valid,
                    expected_valid,
                    f"{case} on line {line_number}: validation outcome",
                )
                rendered = canonical_json_bytes({"kind": kind, "value": semantic})
                expected = (
                    f'{{"kind":"{kind}","value":{canonical_json}}}\n'.encode()
                )
                if expected_valid:
                    self.assertEqual(rendered, expected, case)
                    self.assertIs(type(semantic), float, case)
                else:
                    self.assertNotEqual(rendered, expected, case)
                    self.assertIn(type(semantic), (bool, int), case)
                seen += 1
        self.assertEqual(seen, 18)

        for semantic in (float("nan"), float("inf"), float("-inf")):
            snapshot = self._snapshot()
            snapshot["database_properties"]["Float"] = {
                "kind": "double",
                "raw_hex": "0000000000000000",
                "value": semantic,
            }
            with self.assertRaisesRegex(
                ValidationError, "finite JSON floating-point number"
            ):
                v1_2.validate_semantic_snapshot(snapshot)

        snapshot = self._snapshot()
        snapshot["database_properties"]["Float"] = {
            "kind": "single",
            "raw_hex": "7f7fffff",
            "value": 3.5e38,
        }
        with self.assertRaisesRegex(ValidationError, "finite binary32 range"):
            v1_2.validate_semantic_snapshot(snapshot)

    def test_shared_producer_extension_normalization_vector_is_path_aware(self):
        fixture = (
            v1_2.SCHEMA_DIR
            / "fixtures"
            / "producer-extension-normalization-vector.tsv"
        )
        seen = 0
        for line_number, line in enumerate(
            fixture.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.startswith("#"):
                continue
            (
                case,
                semantic_key,
                bits_hex,
                input_json,
                canonical_json,
                opaque_json,
            ) = line.split("\t")
            semantic = {"kind": "single", "value": json.loads(input_json)}
            opaque = json.loads(opaque_json)
            document = {
                "document_type": "canonical_semantic_snapshot",
                "tables": [
                    {
                        "columns": [
                            {
                                "properties": {semantic_key: semantic},
                            }
                        ],
                        "rows": [{"values": {semantic_key: semantic}}],
                    }
                ],
                "producer_extensions": opaque,
            }

            rendered = canonical_json_bytes(document)
            expected_value = struct.unpack(">f", bytes.fromhex(bits_hex))[0]
            self.assertEqual(expected_value, float(canonical_json), case)
            self.assertEqual(
                rendered.count(f'"value":{canonical_json}'.encode()),
                2,
                f"{case} on line {line_number}: semantic paths",
            )
            self.assertIn(
                b'"producer_extensions":' + opaque_json.encode(),
                rendered,
                f"{case} on line {line_number}: opaque extension",
            )
            seen += 1
        self.assertEqual(seen, 1)

    def test_source_revision_length_matches_shared_multibyte_vectors(self):
        fixture = (
            v1_2.SCHEMA_DIR / "fixtures" / "source-revision-length-vectors.tsv"
        ).read_text(encoding="utf-8")
        seen = 0
        for line_number, line in enumerate(fixture.splitlines(), start=1):
            if line.startswith("#"):
                continue
            case, scalar, repetitions, expected_valid = line.split("\t")
            self.assertEqual(len(scalar), 1, case)
            self.assertGreater(len(scalar.encode("utf-8")), 1, case)
            source_revision = scalar * int(repetitions)
            expected_valid = expected_valid == "true"
            snapshot = self._snapshot()
            snapshot["producer"]["source_revision"] = source_revision
            receipt = self._success_receipt(snapshot)
            for document in (snapshot, receipt):
                try:
                    v1_2.SCHEMA_SET.validate(document)
                    actual_valid = True
                except ValidationError:
                    actual_valid = False
                self.assertEqual(
                    actual_valid, expected_valid, f"{case} line {line_number}"
                )
            seen += 1
        self.assertEqual(seen, 2)

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

    def test_shared_relationship_field_uniqueness_vectors_match_schema_and_python_validation(self):
        fixture = (
            v1_2.SCHEMA_DIR
            / "fixtures"
            / "relationship-field-uniqueness-vectors.tsv"
        )
        seen = 0
        for line_number, line in enumerate(
            fixture.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.startswith("#"):
                continue
            case, field, foreign_field, expected_valid = line.split("\t")
            snapshot = self._snapshot()
            snapshot["relationships"] = [
                {
                    "name": "Self",
                    "table": "Items",
                    "foreign_table": "Items",
                    "attributes": 0,
                    "fields": [
                        {"field": "Id", "foreign_field": "Id"},
                        {"field": field, "foreign_field": foreign_field},
                    ],
                    "properties": {},
                }
            ]
            expected_valid = expected_valid == "true"
            for validator in (
                v1_2.SCHEMA_SET.validate,
                v1_2.validate_semantic_snapshot,
            ):
                try:
                    validator(snapshot)
                    actual_valid = True
                except ValidationError:
                    actual_valid = False
                self.assertEqual(
                    actual_valid,
                    expected_valid,
                    f"{case} on line {line_number}: {validator.__name__}",
                )
            seen += 1
        self.assertEqual(seen, 2)

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
            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("canonical validation reread its input"),
            ):
                self.assertEqual(
                    v1_2.validate_document_path(snapshot_path),
                    "canonical_semantic_snapshot",
                )

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
