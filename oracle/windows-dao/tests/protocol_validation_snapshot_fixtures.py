import hashlib
import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from protocol_validation import canonical_json_bytes  # noqa: E402
import build_v1_2_inventory  # noqa: E402
import validate_protocol_v1_2 as v1_2  # noqa: E402


class SnapshotFixtureMixin:
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
            "producer_extensions": {
                "/tables/0/columns/0/required": {
                    "kind": "boolean",
                    "value": True,
                }
            },
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
