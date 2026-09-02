#!/usr/bin/env python3
"""Validate the bounded multiple-index first-create experiment for issue #150."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import system_catalog as catalog


PAGE_BYTES = 2048
INDEX_ENTRY_AREA_OFFSET = 248
INDEX_ENTRY_AREA_LENGTH = PAGE_BYTES - INDEX_ENTRY_AREA_OFFSET
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_PAGES = 64
MAX_TABLES = 16
MAX_FIELDS = 8
MAX_INDEXES = 4
MAX_INDEX_FIELDS = 4
MAX_TEXT = 512
DOCUMENT_TYPE = "dao_multiple_indexes_job_result"
REPORT_TYPE = "multiple_indexes_report"
CHECKPOINTS = ("empty", "one", "two", "three", "composite")
SCENARIOS = CHECKPOINTS[1:]
SAFE_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_MDB = re.compile(r"^multiple-indexes-r[1-3]-(empty|one|two|three|composite)[.]mdb$")
UNASSIGNED = "unassigned"


FIELDS = [
    {"name": "Id", "ordinal": 0, "size": 4, "type": 4},
    {"name": "Code", "ordinal": 1, "size": 4, "type": 4},
    {"name": "Sequence", "ordinal": 2, "size": 4, "type": 4},
]


def index(
    ordinal: int,
    name: str,
    fields: list[tuple[str, bool]],
    *,
    primary: bool = False,
    unique: bool = False,
    required: bool = False,
) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "name": name,
        "primary": primary,
        "unique": unique,
        "required": required,
        "fields": [
            {"ordinal": position, "name": field, "descending": descending}
            for position, (field, descending) in enumerate(fields)
        ],
    }


PRIMARY = index(0, "ZPrimary", [("Id", False)], primary=True, unique=True, required=True)
EXPECTED = {
    "one": {"table": "IdxOne", "indexes": [PRIMARY]},
    "two": {
        "table": "IdxTwo",
        "indexes": [PRIMARY, index(1, "ASecondx", [("Code", False)])],
    },
    "three": {
        "table": "IdxTri",
        "indexes": [
            PRIMARY,
            index(1, "MUniqueX", [("Code", True)], unique=True),
            index(2, "ASecondx", [("Sequence", False)]),
        ],
    },
    "composite": {
        "table": "IdxMix",
        "indexes": [
            index(0, "ZComposi", [("Code", True), ("Sequence", False)], unique=True),
            index(1, "ASecondx", [("Id", True)]),
        ],
    },
}


class AnalysisError(ValueError):
    """The result or retained inventory violates the preregistered contract."""


class DecodeError(ValueError):
    """A complete scientific observation does not satisfy a pinned grammar."""


def canonical_bytes(document: Any) -> bytes:
    return (
        json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        + b"\n"
    )


def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def exact(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AnalysisError(f"{where} must contain exactly {sorted(keys)}")
    return value


def digest(value: Any, where: str) -> str:
    if not isinstance(value, str) or not HEX_64.fullmatch(value):
        raise AnalysisError(f"{where} must be a lowercase SHA-256 digest")
    return value


def integer(value: Any, where: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise AnalysisError(f"{where} must be an integer in [{low},{high}]")
    return value


def text(value: Any, where: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise AnalysisError(f"{where} must be a string of at most {maximum} characters")
    return value


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AnalysisError("job result must be a regular non-link file")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise AnalysisError("job result exceeds the JSON bound")
    try:
        result = json.loads(raw, object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("job result is not valid UTF-8 JSON") from error
    if not isinstance(result, dict):
        raise AnalysisError("job result root must be an object")
    return result


def validate_dao(value: Any, where: str) -> dict[str, Any]:
    item = exact(value, {"tabledefs"}, where)
    tables = item["tabledefs"]
    if not isinstance(tables, list) or len(tables) > MAX_TABLES:
        raise AnalysisError(f"{where}.tabledefs exceeds the bound")
    for table_position, raw_table in enumerate(tables):
        table = exact(raw_table, {"ordinal", "name", "fields", "indexes"}, f"{where}.tabledefs[{table_position}]")
        if integer(table["ordinal"], "table ordinal", 0, MAX_TABLES - 1) != table_position:
            raise AnalysisError("DAO table ordinals must be sequential")
        text(table["name"], "table name", 256)
        fields = table["fields"]
        indexes = table["indexes"]
        if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
            raise AnalysisError("DAO fields exceed the bound")
        if not isinstance(indexes, list) or len(indexes) > MAX_INDEXES:
            raise AnalysisError("DAO indexes exceed the bound")
        for position, raw_field in enumerate(fields):
            field = exact(raw_field, {"ordinal", "name", "type", "size"}, "DAO field")
            if integer(field["ordinal"], "field ordinal", 0, MAX_FIELDS - 1) != position:
                raise AnalysisError("DAO field ordinals must be sequential")
            text(field["name"], "field name", 256)
            integer(field["type"], "field type", 0, 65535)
            integer(field["size"], "field size", 0, 1 << 20)
        for position, raw_index in enumerate(indexes):
            entry = exact(
                raw_index,
                {"ordinal", "name", "primary", "unique", "required", "fields"},
                "DAO index",
            )
            if integer(entry["ordinal"], "index ordinal", 0, MAX_INDEXES - 1) != position:
                raise AnalysisError("DAO index ordinals must be sequential")
            text(entry["name"], "index name", 256)
            if any(type(entry[key]) is not bool for key in ("primary", "unique", "required")):
                raise AnalysisError("DAO index flags must be boolean")
            keys = entry["fields"]
            if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_INDEX_FIELDS:
                raise AnalysisError("DAO index fields violate the bound")
            for key_position, raw_key in enumerate(keys):
                key = exact(raw_key, {"ordinal", "name", "descending"}, "DAO index field")
                if integer(key["ordinal"], "index field ordinal", 0, MAX_INDEX_FIELDS - 1) != key_position:
                    raise AnalysisError("DAO index field ordinals must be sequential")
                text(key["name"], "index field name", 256)
                if type(key["descending"]) is not bool:
                    raise AnalysisError("DAO descending flag must be boolean")
    return item


def read_checkpoint(
    root: Path, value: Any, replica: int, name: str
) -> tuple[bytes, bool, dict[str, Any], Any, dict[str, Any]]:
    item = exact(
        value,
        {
            "name",
            "database",
            "size",
            "size_after_metadata",
            "sha256",
            "sha256_after_metadata",
            "arm_before",
            "dao",
        },
        f"replica {replica} checkpoint {name}",
    )
    if item["name"] != name:
        raise AnalysisError(f"replica {replica} checkpoint order is invalid")
    filename = item["database"]
    expected = f"multiple-indexes-r{replica}-{name}.mdb"
    if filename != expected or not SAFE_MDB.fullmatch(filename):
        raise AnalysisError(f"replica {replica} checkpoint filename is invalid")
    size = integer(item["size"], "checkpoint size", PAGE_BYTES, MAX_PAGES * PAGE_BYTES)
    if size % PAGE_BYTES:
        raise AnalysisError("checkpoint is not an exact sequence of pages")
    before = digest(item["sha256"], "checkpoint digest")
    size_after = integer(
        item["size_after_metadata"],
        "post-metadata checkpoint size",
        PAGE_BYTES,
        MAX_PAGES * PAGE_BYTES,
    )
    if size_after % PAGE_BYTES:
        raise AnalysisError("post-metadata checkpoint is not an exact sequence of pages")
    after = digest(item["sha256_after_metadata"], "post-metadata digest")
    path = root / filename
    if not path.is_file() or path.is_symlink():
        raise AnalysisError("checkpoint must be a regular non-link file")
    raw = path.read_bytes()
    if len(raw) != size_after or hashlib.sha256(raw).hexdigest() != after:
        raise AnalysisError("checkpoint bytes differ from the recorded identity")
    return (
        raw,
        size != size_after or before != after,
        validate_dao(item["dao"], f"checkpoint {name}.dao"),
        item["arm_before"],
        {"sha256": before, "size": size},
    )


def user_tables(dao: dict[str, Any]) -> list[dict[str, Any]]:
    return [table for table in dao["tabledefs"] if not table["name"].startswith("MSys")]


def validate_schema(scenario: str, dao: dict[str, Any]) -> None:
    users = user_tables(dao)
    expected = EXPECTED[scenario]
    if len(users) != 1 or users[0]["name"] != expected["table"]:
        raise DecodeError(f"{scenario} DAO metadata does not contain exactly {expected['table']}")
    table = users[0]
    if table["fields"] != FIELDS:
        raise DecodeError(f"{scenario} DAO fields differ from the preregistered schema")
    if len(table["indexes"]) != len(expected["indexes"]):
        raise DecodeError(f"{scenario} DAO index count differs from the preregistered schema")
    observed = {entry["name"]: {key: value for key, value in entry.items() if key != "ordinal"} for entry in table["indexes"]}
    if len(observed) != len(table["indexes"]):
        raise DecodeError(f"{scenario} DAO metadata repeats an index name")
    wanted = {entry["name"]: {key: value for key, value in entry.items() if key != "ordinal"} for entry in expected["indexes"]}
    if observed != wanted:
        raise DecodeError(f"{scenario} DAO indexes differ from the preregistered schema")


def decoded_user_table(analysis: dict[str, Any], scenario: str) -> dict[str, Any]:
    expected_name = EXPECTED[scenario]["table"]
    users = [entry for entry in analysis["tables"].values() if not entry["flags"] & catalog.SYSTEM_FLAG]
    if len(users) != 1 or users[0]["name"] != expected_name:
        raise DecodeError(f"{scenario} decoded tables do not contain exactly {expected_name}")
    return users[0]


def created_lvprop(data: bytes, table_name: str) -> dict[str, Any]:
    definition, _, rows = catalog._discover_catalog(data)
    name_ordinal = catalog._ordinal(definition, "Name")
    lvprop_ordinal = catalog._ordinal(definition, "LvProp")
    if name_ordinal is None or lvprop_ordinal is None:
        raise DecodeError("catalog lacks a Name or LvProp column")
    required = max(name_ordinal, lvprop_ordinal)
    matches = [
        row
        for row in rows
        if len(row.get("values", [])) > required
        and row["values"][name_ordinal] == table_name
    ]
    if len(matches) != 1:
        raise DecodeError(f"catalog contains {len(matches)} rows for {table_name}")
    value = matches[0]["values"][lvprop_ordinal]
    if not isinstance(value, dict) or set(value) != {
        "inline_length",
        "long_value_header_hex",
    }:
        raise DecodeError(f"{table_name}.LvProp is not one external header")
    try:
        header = bytes.fromhex(value["long_value_header_hex"])
    except (TypeError, ValueError) as error:
        raise DecodeError(f"{table_name}.LvProp header is not hex") from error
    if len(header) != 12 or value["inline_length"] != 12:
        raise DecodeError(f"{table_name}.LvProp header is not 12 bytes")
    if header[8:12] != bytes(4):
        raise DecodeError(f"{table_name}.LvProp reserved bytes are nonzero")
    control = int.from_bytes(header[:4], "little")
    if control & 0xFF000000 != 0x40000000:
        raise DecodeError(f"{table_name}.LvProp is not single-page external")
    length = control & 0x00FFFFFF
    row_number = header[4]
    page_number = int.from_bytes(header[5:8], "little")
    if length == 0 or length > PAGE_BYTES or page_number >= len(data) // PAGE_BYTES:
        raise DecodeError(f"{table_name}.LvProp reference is outside the image")
    page = catalog._page(data, page_number, f"{table_name}.LvProp")
    if page[0] != 1 or page[4:8] != b"LVAL":
        raise DecodeError(f"{table_name}.LvProp does not target an LVAL page")
    directory = catalog._row_directory(page, page_number)
    if row_number >= len(directory):
        raise DecodeError(f"{table_name}.LvProp row is absent")
    row = directory[row_number]
    if row["hidden"] or row["overflow"]:
        raise DecodeError(f"{table_name}.LvProp targets a flagged row")
    payload = page[row["start"] : row["end"]]
    if len(payload) != length:
        raise DecodeError(f"{table_name}.LvProp payload length disagrees with its header")
    return {
        "header_hex": header.hex(),
        "length": length,
        "page": page_number,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "row": row_number,
    }


def validate_empty_leaf(data: bytes, root: int, owner: int, what: str) -> None:
    page = catalog._page(data, root, what)
    if page[0] != 4 or page[1] != 1 or int.from_bytes(page[4:8], "little") != owner:
        raise DecodeError(f"{what} is not a leaf root owned by definition {owner}")
    if any(page[offset : offset + 4] != bytes(4) for offset in (8, 12, 16)):
        raise DecodeError(f"{what} has a sibling or child reference")
    if page[20] != 0 or page[21] != 0 or any(page[22:INDEX_ENTRY_AREA_OFFSET]):
        raise DecodeError(f"{what} has a prefix, branch marker, or entry boundary")
    if int.from_bytes(page[2:4], "little") != INDEX_ENTRY_AREA_LENGTH:
        raise DecodeError(f"{what} free space does not describe an empty leaf")


def index_layout(
    data: bytes, page_tags: dict[int, int], table: dict[str, Any], scenario: str
) -> dict[str, Any]:
    definition = table["definition"]
    columns = definition["columns"]
    if [entry["name"] for entry in columns] != [entry["name"] for entry in FIELDS] or any(
        entry["type"] != "Long" for entry in columns
    ):
        raise DecodeError(f"{scenario} decoded columns differ from the DAO schema")
    physical = definition["physical_indexes"]
    logical = definition["logical_indexes"]
    expected_indexes = EXPECTED[scenario]["indexes"]
    if len(physical) != len(expected_indexes) or len(logical) != len(expected_indexes):
        raise DecodeError(f"{scenario} decoded index counts differ from the schema")
    if definition["row_count"] != 0:
        raise DecodeError(f"{scenario} decoded table is not empty")
    if definition["pages"] != [definition["root"]]:
        raise DecodeError(f"{scenario} definition requires a continuation page")
    by_name = {entry["name"]: entry for entry in expected_indexes}
    if len({entry["name"] for entry in logical}) != len(logical) or set(entry["name"] for entry in logical) != set(by_name):
        raise DecodeError(f"{scenario} logical index names differ from the schema")
    named_physical: dict[int, str] = {}
    logical_rows = []
    for logical_ordinal, entry in enumerate(logical):
        physical_ordinal = entry["physical_index"]
        if type(physical_ordinal) is not int or not 0 <= physical_ordinal < len(physical):
            raise DecodeError(f"{scenario} logical index names an invalid physical ordinal")
        if physical_ordinal in named_physical:
            raise DecodeError(f"{scenario} logical indexes alias one physical index")
        expected_class = 1 if by_name[entry["name"]]["primary"] else 0
        if entry["class"] != expected_class:
            raise DecodeError(f"{scenario} logical class for {entry['name']} differs from DAO metadata")
        named_physical[physical_ordinal] = entry["name"]
        logical_rows.append(
            {"class": entry["class"], "logical_ordinal": logical_ordinal, "name": entry["name"], "physical_ordinal": physical_ordinal}
        )
    physical_rows = []
    for ordinal, entry in enumerate(physical):
        name = named_physical.get(ordinal)
        if name is None:
            raise DecodeError(f"{scenario} physical index {ordinal} has no logical name")
        keys = []
        for key in entry["keys"]:
            column = key["column"]
            if not 0 <= column < len(columns) or key["direction"] not in (0, 1):
                raise DecodeError(f"{scenario} physical index {ordinal} has an invalid key")
            keys.append({"column": column, "name": columns[column]["name"], "direction": key["direction"]})
        expected_keys = [
            {"column": next(i for i, field in enumerate(FIELDS) if field["name"] == key["name"]), "name": key["name"], "direction": 0 if key["descending"] else 1}
            for key in by_name[name]["fields"]
        ]
        if keys != expected_keys:
            raise DecodeError(f"{scenario} physical keys for {name} differ from DAO metadata")
        expected_flags = (1 if by_name[name]["unique"] else 0) | (8 if by_name[name]["required"] else 0)
        if entry["flags"] != expected_flags:
            raise DecodeError(f"{scenario} physical flags for {name} differ from DAO metadata")
        if entry["entry_count"] != 0:
            raise DecodeError(f"{scenario} physical index {ordinal} is not empty")
        root = entry["root"]
        if page_tags.get(root) != 4:
            raise DecodeError(f"{scenario} physical index {ordinal} root is not a leaf page")
        validate_empty_leaf(
            data, root, definition["root"], f"{scenario} physical index {ordinal}"
        )
        mapped_pages = sorted(
            catalog._locator_pages(data, entry["map"], f"{scenario} index {ordinal} map")
        )
        physical_rows.append(
            {
                "entry_count": entry["entry_count"],
                "flags": entry["flags"],
                "keys": keys,
                "map": entry["map"],
                "mapped_pages": mapped_pages,
                "name": name,
                "physical_ordinal": ordinal,
                "root": root,
                "root_delta_from_definition": root - definition["root"],
            }
        )
    table_maps = {
        kind: {
            "locator": definition["maps"][kind],
            "mapped_pages": sorted(
                catalog._locator_pages(
                    data, definition["maps"][kind], f"{scenario} table {kind} map"
                )
            ),
        }
        for kind in ("owned", "available")
    }
    return {
        "definition_root": definition["root"],
        "definition_pages": definition["pages"],
        "logical_definition_order": logical_rows,
        "logical_name_sorted_order": sorted(entry["name"] for entry in logical_rows),
        "physical_ordinal_order": [entry["name"] for entry in physical_rows],
        "physical_indexes": physical_rows,
        "table_maps": table_maps,
    }


def analyze_scenario(before: bytes, after: bytes, scenario: str) -> dict[str, Any]:
    before_analysis = catalog.analyze_checkpoint(before)
    after_analysis = catalog.analyze_checkpoint(after)
    if any(
        not entry["flags"] & catalog.SYSTEM_FLAG
        for entry in before_analysis["tables"].values()
    ):
        raise DecodeError(f"{scenario} empty baseline decodes a user table")
    table = decoded_user_table(after_analysis, scenario)
    before_pages = len(before) // PAGE_BYTES
    after_pages = len(after) // PAGE_BYTES
    if after_pages < before_pages:
        raise DecodeError(f"{scenario} create shrank the image")
    roles = {entry["page"]: entry for entry in after_analysis["pages"]}
    appended = []
    for page in range(before_pages, after_pages):
        role = roles.get(page)
        if role is None or role["role"] == UNASSIGNED:
            raise DecodeError(f"{scenario} appended page {page} is unattributed")
        appended.append(
            {
                "delta_from_definition": page - table["definition"]["root"],
                "delta_from_empty": page - before_pages,
                "owners": role["owners"],
                "page": page,
                "role": role["role"],
            }
        )
    layout = index_layout(
        after, {page: entry["tag"] for page, entry in roles.items()}, table, scenario
    )
    lvprop = created_lvprop(after, EXPECTED[scenario]["table"])
    if not any(
        entry["page"] == lvprop["page"] and entry["role"] == "long_value"
        for entry in appended
    ):
        raise DecodeError(f"{scenario} LvProp page is not an appended long-value page")
    map_pages = sorted(
        {
            layout["table_maps"][kind]["locator"]["page"]
            for kind in ("owned", "available")
        }
        | {entry["map"]["page"] for entry in layout["physical_indexes"]}
    )
    return {
        "appended_pages": appended,
        "index_layout": layout,
        "lvprop": lvprop,
        "map_pages": map_pages,
        "page_count": {"before": before_pages, "after": after_pages},
    }


def build_report(document: dict[str, Any], replicas: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [entry for entry in replicas if "observations" in entry]
    questions: dict[str, Any]
    if document["status"] != "pass" or len(complete) != 3:
        reason = "at least one replica did not complete"
        if any(entry.get("metadata_changed") for entry in replicas):
            reason = "DAO metadata access changed at least one checkpoint"
        elif any(entry.get("decode_error") for entry in replicas):
            reason = "at least one complete checkpoint did not decode under the pinned grammars"
        questions = {name: {"reason": reason, "status": "no_outcome"} for name in ("page_assignment", "index_layout", "map_placement", "replication")}
    else:
        observations = [entry["observations"] for entry in complete]
        if any(value != observations[0] for value in observations[1:]):
            questions = {name: {"reason": "replicas disagree on the complete decoded observation", "status": "no_outcome"} for name in ("page_assignment", "index_layout", "map_placement", "replication")}
        else:
            value = observations[0]
            questions = {
                "page_assignment": {
                    "scenarios": {
                        name: {
                            "appended_pages": value[name]["appended_pages"],
                            "lvprop": value[name]["lvprop"],
                            "page_count": value[name]["page_count"],
                        }
                        for name in SCENARIOS
                    },
                    "status": "answered",
                },
                "index_layout": {"scenarios": {name: value[name]["index_layout"] for name in SCENARIOS}, "status": "answered"},
                "map_placement": {"scenarios": {name: value[name]["map_pages"] for name in SCENARIOS}, "status": "answered"},
                "replication": {"replicas": 3, "status": "answered"},
            }
    return {
        "compatibility_claim": False,
        "development_only": True,
        "document_type": REPORT_TYPE,
        "plan_sha256": document["plan_sha256"],
        "questions": questions,
        "replicas": [{key: value for key, value in entry.items() if key != "observations"} for entry in replicas],
        "status": "accepted" if all(value["status"] == "answered" for value in questions.values()) else "no_outcome",
        "support_movement": False,
    }


def evaluate(job_result: Path, expected_plan_sha256: str, output: Path) -> dict[str, Any]:
    expected_plan = digest(expected_plan_sha256, "--expected-plan-sha256")
    document = exact(load(job_result), {"document_type", "development_only", "plan_sha256", "run_id", "status", "replicas"}, "$")
    if document["document_type"] != DOCUMENT_TYPE or document["development_only"] is not True:
        raise AnalysisError("job result identity is invalid")
    if digest(document["plan_sha256"], "$.plan_sha256") != expected_plan:
        raise AnalysisError("job result plan digest differs from the approved plan")
    if not isinstance(document["run_id"], str) or not SAFE_RUN_ID.fullmatch(document["run_id"]):
        raise AnalysisError("$.run_id is invalid")
    if document["status"] not in ("pass", "fail"):
        raise AnalysisError("$.status is invalid")
    if not isinstance(document["replicas"], list) or not 1 <= len(document["replicas"]) <= 3:
        raise AnalysisError("$.replicas must contain one through three replicas")
    replicas = []
    referenced = []
    for position, raw_replica in enumerate(document["replicas"]):
        item = exact(
            raw_replica,
            {
                "replica",
                "status",
                "error",
                "mutation_started",
                "phase",
                "checkpoints",
                "recovery",
            },
            f"replicas[{position}]",
        )
        replica = integer(item["replica"], "replica number", 1, 3)
        if replica != position + 1:
            raise AnalysisError("replicas must be numbered 1 through 3 in order")
        if item["status"] not in ("pass", "fail"):
            raise AnalysisError("replica status is invalid")
        if type(item["mutation_started"]) is not bool:
            raise AnalysisError("replica mutation_started is invalid")
        phase = text(item["phase"], "replica phase", 32)
        if phase not in {
            "before_create_database",
            "create_database",
            "capture_empty",
            "copy_arms",
            *SCENARIOS,
            "complete",
        }:
            raise AnalysisError("replica phase is invalid")
        if item["error"] is not None:
            text(item["error"], "replica error")
        checkpoints = item["checkpoints"]
        if not isinstance(checkpoints, list) or len(checkpoints) > len(CHECKPOINTS):
            raise AnalysisError("replica checkpoints violate the bound")
        images: dict[str, bytes] = {}
        daos: dict[str, dict[str, Any]] = {}
        identities: dict[str, dict[str, Any]] = {}
        changed = []
        for checkpoint_position, raw_checkpoint in enumerate(checkpoints):
            name = CHECKPOINTS[checkpoint_position]
            image, repaired, dao, arm_before, identity = read_checkpoint(
                job_result.parent, raw_checkpoint, replica, name
            )
            images[name] = image
            daos[name] = dao
            identities[name] = identity
            referenced.append(f"multiple-indexes-r{replica}-{name}.mdb")
            if repaired:
                changed.append(name)
            if name == "empty":
                if arm_before is not None:
                    raise AnalysisError("empty checkpoint must not have an arm identity")
            else:
                before = exact(arm_before, {"size", "sha256"}, f"{name}.arm_before")
                before_size = integer(
                    before["size"], "arm size", PAGE_BYTES, MAX_PAGES * PAGE_BYTES
                )
                if before_size % PAGE_BYTES:
                    raise AnalysisError("arm size is not an exact sequence of pages")
                if before_size != identities["empty"]["size"] or digest(
                    before["sha256"], "arm digest"
                ) != identities["empty"]["sha256"]:
                    raise AnalysisError(f"{name} arm identity differs from the retained empty image")
        recovery = item["recovery"]
        if not isinstance(recovery, list) or len(recovery) > 1:
            raise AnalysisError("replica recovery inventory violates the bound")
        checkpoint_names = set(images)
        for raw_recovery in recovery:
            value = exact(raw_recovery, {"name", "database", "size", "sha256"}, "recovery artifact")
            name = value["name"]
            expected_recovery = CHECKPOINTS[len(images)] if len(images) < len(CHECKPOINTS) else None
            if name != expected_recovery or name not in CHECKPOINTS or name in checkpoint_names:
                raise AnalysisError("recovery artifact name is invalid or duplicated")
            filename = value["database"]
            if filename != f"multiple-indexes-r{replica}-{name}.mdb" or not SAFE_MDB.fullmatch(filename):
                raise AnalysisError("recovery artifact filename is invalid")
            size = integer(value["size"], "recovery size", PAGE_BYTES, MAX_PAGES * PAGE_BYTES)
            if size % PAGE_BYTES:
                raise AnalysisError("recovery artifact is not an exact sequence of pages")
            path = job_result.parent / filename
            if not path.is_file() or path.is_symlink():
                raise AnalysisError("recovery artifact must be a regular non-link file")
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest(value["sha256"], "recovery digest"):
                raise AnalysisError("recovery artifact bytes differ from metadata")
            referenced.append(filename)
        files = [
            {
                "arm_before": raw_checkpoint["arm_before"],
                "database": raw_checkpoint["database"],
                "name": raw_checkpoint["name"],
                "sha256": raw_checkpoint["sha256"],
                "sha256_after_metadata": raw_checkpoint["sha256_after_metadata"],
                "size": raw_checkpoint["size"],
                "size_after_metadata": raw_checkpoint["size_after_metadata"],
            }
            for raw_checkpoint in checkpoints
        ]
        files.extend(
            {
                "database": artifact["database"],
                "name": artifact["name"],
                "recovery": True,
                "sha256": artifact["sha256"],
                "size": artifact["size"],
            }
            for artifact in recovery
        )
        entry: dict[str, Any] = {
            "error": item["error"],
            "files": files,
            "mutation_started": item["mutation_started"],
            "phase": phase,
            "replica": replica,
            "status": item["status"],
        }
        if item["status"] == "pass":
            if (
                tuple(images) != CHECKPOINTS
                or item["error"] is not None
                or recovery
                or not item["mutation_started"]
                or phase != "complete"
            ):
                raise AnalysisError("passing replica inventory is incomplete")
            if changed:
                entry["metadata_changed"] = changed
            else:
                try:
                    if user_tables(daos["empty"]):
                        raise DecodeError("empty checkpoint contains a DAO user table")
                    for name in SCENARIOS:
                        validate_schema(name, daos[name])
                    entry["observations"] = {name: analyze_scenario(images["empty"], images[name], name) for name in SCENARIOS}
                except (catalog.DecodeError, DecodeError) as error:
                    entry["decode_error"] = str(error)
        elif item["error"] is None:
            raise AnalysisError("failed replica omits its error")
        else:
            progress = {
                "create_database": (0, "empty", False),
                "capture_empty": (0, "empty", True),
                "copy_arms": (1, None, True),
                "one": (1, "one", True),
                "two": (2, "two", True),
                "three": (3, "three", True),
                "composite": (4, "composite", True),
                "complete": (5, None, True),
            }
            expected = progress.get(phase)
            if expected is None:
                raise AnalysisError("failed replica phase is inconsistent with producer progress")
            checkpoint_count, recovery_name, mutation_required = expected
            if len(images) != checkpoint_count:
                raise AnalysisError("failed replica checkpoint prefix is inconsistent with its phase")
            if recovery and recovery[0]["name"] != recovery_name:
                raise AnalysisError("failed replica recovery is inconsistent with its phase")
            if recovery_name is None and recovery:
                raise AnalysisError("failed replica phase cannot retain a recovery artifact")
            if mutation_required and not item["mutation_started"]:
                raise AnalysisError("failed replica phase requires a started DAO mutation")
            if not item["mutation_started"] and recovery:
                raise AnalysisError("pre-mutation failure cannot retain a recovery artifact")
        replicas.append(entry)
    if not any(entry["mutation_started"] for entry in replicas):
        raise AnalysisError("acquisition did not reach the first DAO mutation")
    if len(replicas) != 3:
        raise AnalysisError("post-mutation result must contain exactly three replicas")
    aggregate = "pass" if all(entry["status"] == "pass" for entry in replicas) else "fail"
    if document["status"] != aggregate:
        raise AnalysisError("job status disagrees with replica statuses")
    retained = sorted(
        path.name
        for path in job_result.parent.iterdir()
        if path.suffix.casefold() == ".mdb"
    )
    if retained != sorted(referenced):
        raise AnalysisError("retained MDB inventory differs from the job result")
    report = build_report(document, replicas)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_bytes(report))
    return report


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_result", type=Path)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(arguments)
    try:
        evaluate(args.job_result, args.expected_plan_sha256, args.output)
    except (AnalysisError, OSError) as error:
        print(f"multiple-index analysis failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
