#!/usr/bin/env python3
"""Validate the bounded table-definition continuation experiment for issue #151."""

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
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_CHECKPOINT_PAGES = 256
MAX_RECOVERY_PAGES = 512
MAX_TABLES = 16
MAX_FIELDS = 140
MAX_TEXT = 512
DOCUMENT_TYPE = "dao_definition_continuation_job_result"
REPORT_TYPE = "definition_continuation_report"
CHECKPOINTS = ("empty", "zero", "one", "two")
SCENARIOS = CHECKPOINTS[1:]
EXPECTED_CONTINUATIONS = {"zero": 0, "one": 1, "two": 2}
EXPECTED_LOGICAL_LENGTHS = {"zero": 2046, "one": 2075, "two": 4105}
FIELD_COUNTS = {"zero": 69, "one": 70, "two": 140}
TABLE_NAMES = {"zero": "ContZero", "one": "ContOneX", "two": "ContTwoX"}
SAFE_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")
SAFE_MDB = re.compile(r"^definition-continuation-r[1-3]-(empty|zero|one|two)[.]mdb$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")

# The shared decoder's default is the earlier experiment's 64-column work bound.
# This process widens only that work bound; the decoded grammar is unchanged.
catalog.MAX_COLUMNS = MAX_FIELDS


class AnalysisError(ValueError):
    """The result or retained inventory violates the preregistered contract."""


class DecodeError(ValueError):
    """A complete observation does not satisfy a recorded grammar or control."""


def canonical_bytes(document: Any) -> bytes:
    return (
        json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
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


def integer(value: Any, where: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise AnalysisError(f"{where} must be an integer in [{low},{high}]")
    return value


def text(value: Any, where: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise AnalysisError(f"{where} must be a string of at most {maximum} characters")
    return value


def digest(value: Any, where: str) -> str:
    if not isinstance(value, str) or not HEX_64.fullmatch(value):
        raise AnalysisError(f"{where} must be a lowercase SHA-256 digest")
    return value


def validate_measurement(value: Any, where: str) -> dict[str, Any]:
    item = exact(
        value,
        {
            "raw_byte_length",
            "divisible_by_page_size",
            "page_count",
            "failed_predicate",
        },
        where,
    )
    raw_length = integer(
        item["raw_byte_length"], f"{where}.raw_byte_length", 0, (1 << 63) - 1
    )
    divisible = item["divisible_by_page_size"]
    if type(divisible) is not bool or divisible != (raw_length % PAGE_BYTES == 0):
        raise AnalysisError(f"{where}.divisible_by_page_size is inconsistent")
    expected_pages = raw_length // PAGE_BYTES if divisible else None
    if item["page_count"] != expected_pages or (
        item["page_count"] is not None and type(item["page_count"]) is not int
    ):
        raise AnalysisError(f"{where}.page_count is inconsistent")
    expected_predicate = None
    if raw_length < PAGE_BYTES:
        expected_predicate = "minimum_page_length"
    elif not divisible:
        expected_predicate = "page_alignment"
    elif expected_pages > MAX_CHECKPOINT_PAGES:
        expected_predicate = "checkpoint_bound_exceeded"
    if item["failed_predicate"] != expected_predicate:
        raise AnalysisError(f"{where}.failed_predicate is inconsistent")
    return item


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AnalysisError("job result must be a regular non-link file")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise AnalysisError("job result exceeds the JSON bound")
    try:
        value = json.loads(raw, object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("job result is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise AnalysisError("job result root must be an object")
    return value


def field_name(ordinal: int) -> str:
    return f"F{ordinal:03d}AAAAAA"


def validate_dao(value: Any, where: str) -> dict[str, Any]:
    item = exact(value, {"tabledefs"}, where)
    tables = item["tabledefs"]
    if not isinstance(tables, list) or len(tables) > MAX_TABLES:
        raise AnalysisError(f"{where}.tabledefs violates the bound")
    for table_position, raw_table in enumerate(tables):
        table = exact(raw_table, {"ordinal", "name", "fields"}, "DAO table")
        if integer(table["ordinal"], "DAO table ordinal", 0, MAX_TABLES - 1) != table_position:
            raise AnalysisError("DAO table ordinals must be sequential")
        text(table["name"], "DAO table name", 256)
        fields = table["fields"]
        if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
            raise AnalysisError("DAO fields violate the bound")
        for field_position, raw_field in enumerate(fields):
            field = exact(raw_field, {"ordinal", "name", "type", "size"}, "DAO field")
            if integer(field["ordinal"], "DAO field ordinal", 0, MAX_FIELDS - 1) != field_position:
                raise AnalysisError("DAO field ordinals must be sequential")
            text(field["name"], "DAO field name", 64)
            integer(field["type"], "DAO field type", 0, 65535)
            integer(field["size"], "DAO field size", 0, 1 << 20)
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
            "measurement",
            "measurement_after_metadata",
            "arm_before",
            "dao",
        },
        f"replica {replica} checkpoint {name}",
    )
    if item["name"] != name:
        raise AnalysisError(f"replica {replica} checkpoint order is invalid")
    filename = item["database"]
    expected = f"definition-continuation-r{replica}-{name}.mdb"
    if filename != expected or not SAFE_MDB.fullmatch(filename):
        raise AnalysisError(f"replica {replica} checkpoint filename is invalid")
    measurement = validate_measurement(item["measurement"], "checkpoint measurement")
    measurement_after = validate_measurement(
        item["measurement_after_metadata"], "post-metadata checkpoint measurement"
    )
    if (
        measurement["failed_predicate"] is not None
        or measurement_after["failed_predicate"] is not None
    ):
        raise AnalysisError("completed checkpoint has a failed measurement predicate")
    size = integer(
        item["size"], "checkpoint size", PAGE_BYTES, MAX_CHECKPOINT_PAGES * PAGE_BYTES
    )
    size_after = integer(
        item["size_after_metadata"],
        "post-metadata checkpoint size",
        PAGE_BYTES,
        MAX_CHECKPOINT_PAGES * PAGE_BYTES,
    )
    if (
        size != measurement["raw_byte_length"]
        or size_after != measurement_after["raw_byte_length"]
    ):
        raise AnalysisError("checkpoint size differs from its raw measurement")
    if size % PAGE_BYTES or size_after % PAGE_BYTES:
        raise AnalysisError("checkpoint is not an exact sequence of pages")
    before = digest(item["sha256"], "checkpoint digest")
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
    return [entry for entry in dao["tabledefs"] if not entry["name"].startswith("MSys")]


def expected_fields(scenario: str) -> list[dict[str, Any]]:
    return [
        {"ordinal": ordinal, "name": field_name(ordinal), "type": 4, "size": 4}
        for ordinal in range(FIELD_COUNTS[scenario])
    ]


def validate_schema(scenario: str, dao: dict[str, Any]) -> None:
    users = user_tables(dao)
    if len(users) != 1 or users[0]["name"] != TABLE_NAMES[scenario]:
        raise DecodeError(f"{scenario} DAO metadata lacks the exact user table")
    if users[0]["fields"] != expected_fields(scenario):
        raise DecodeError(f"{scenario} DAO fields differ from the preregistered schema")


def decoded_user_table(analysis: dict[str, Any], scenario: str) -> dict[str, Any]:
    users = [
        entry
        for entry in analysis["tables"].values()
        if not entry["flags"] & catalog.SYSTEM_FLAG
    ]
    if len(users) != 1 or users[0]["name"] != TABLE_NAMES[scenario]:
        raise DecodeError(f"{scenario} decoded tables lack the exact user table")
    return users[0]


def created_lvprop(
    data: bytes, analysis: dict[str, Any], table_name: str, before_pages: int
) -> dict[str, Any]:
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
    roles = {entry["page"]: entry for entry in analysis["pages"]}
    appended_lval = {
        page
        for page, role in roles.items()
        if page >= before_pages and role["role"] == "long_value"
    }
    lval_inventory = {
        "appended_lval_pages": sorted(appended_lval),
        "referenced_appended_lval_pages": [],
        "unreferenced_appended_lval_pages": sorted(appended_lval),
    }
    if value is None:
        return {"storage": "null", **lval_inventory}
    if not isinstance(value, dict) or set(value) != {
        "inline_length",
        "long_value_header_hex",
    }:
        raise DecodeError(f"{table_name}.LvProp does not expose a bounded raw header")
    try:
        header = bytes.fromhex(value["long_value_header_hex"])
    except (TypeError, ValueError) as error:
        raise DecodeError(f"{table_name}.LvProp header is not hex") from error
    if len(header) != 12 or type(value["inline_length"]) is not int:
        raise DecodeError(f"{table_name}.LvProp does not carry a 12-byte header")
    control = int.from_bytes(header[:4], "little")
    flag = control & 0xFF000000
    storage = {0: "chained", 0x40000000: "single_page", 0x80000000: "inline"}.get(flag)
    if storage is None:
        raise DecodeError(f"{table_name}.LvProp storage flag is outside EXP-0061")
    observation: dict[str, Any] = {
        "declared_length": control & 0x00FFFFFF,
        "header_hex": header.hex(),
        "inline_length": value["inline_length"],
        "storage": storage,
    }
    if observation["declared_length"] == 0:
        raise DecodeError(f"{table_name}.LvProp has an empty non-null payload")
    if storage == "inline":
        if header[4:12] != bytes(8):
            raise DecodeError(f"{table_name}.LvProp inline header has nonzero reserved bytes")
        if value["inline_length"] != 12 + observation["declared_length"]:
            raise DecodeError(f"{table_name}.LvProp inline framing is inconsistent")
        observation["first_locator"] = None
        observation.update(lval_inventory)
        return observation
    if value["inline_length"] != 12:
        raise DecodeError(f"{table_name}.LvProp external framing is not 12 bytes")
    if header[8:12] != bytes(4):
        raise DecodeError(f"{table_name}.LvProp external header has nonzero reserved bytes")
    locator = {"row": header[4], "page": int.from_bytes(header[5:8], "little")}
    seen: set[tuple[int, int]] = set()
    locators = []
    payload = bytearray()
    while True:
        key = (locator["page"], locator["row"])
        if key in seen or len(seen) >= MAX_CHECKPOINT_PAGES:
            raise DecodeError(f"{table_name}.LvProp chain repeats or exceeds the bound")
        seen.add(key)
        if locator["page"] >= len(data) // PAGE_BYTES:
            raise DecodeError(f"{table_name}.LvProp locator is outside the image")
        page = catalog._page(data, locator["page"], f"{table_name}.LvProp")
        if page[0] != 1 or page[4:8] != b"LVAL":
            raise DecodeError(f"{table_name}.LvProp locator does not target an LVAL page")
        rows_on_page = catalog._row_directory(page, locator["page"])
        if locator["row"] >= len(rows_on_page):
            raise DecodeError(f"{table_name}.LvProp row is absent")
        row = rows_on_page[locator["row"]]
        if row["hidden"] or row["overflow"]:
            raise DecodeError(f"{table_name}.LvProp row is flagged")
        role = roles.get(locator["page"])
        if role is None or role["role"] != "long_value":
            raise DecodeError(f"{table_name}.LvProp lacks an attributed LVAL role")
        raw_row = page[row["start"] : row["end"]]
        locators.append(
            {
                **locator,
                "appended": locator["page"] >= before_pages,
                "row_length": len(raw_row),
            }
        )
        if storage == "single_page":
            payload.extend(raw_row)
            break
        if len(raw_row) <= 4:
            raise DecodeError(f"{table_name}.LvProp chained row has no payload fragment")
        following = raw_row[:4]
        payload.extend(raw_row[4:])
        if following == bytes(4):
            break
        locator = {"row": following[0], "page": int.from_bytes(following[1:4], "little")}
    if len(payload) != observation["declared_length"]:
        raise DecodeError(f"{table_name}.LvProp chain length differs from its header")
    referenced_appended = {
        entry["page"] for entry in locators if entry["appended"]
    }
    observation["first_locator"] = locators[0]
    observation["locators"] = locators
    observation["payload_sha256"] = hashlib.sha256(payload).hexdigest()
    observation.update(
        {
            "appended_lval_pages": sorted(appended_lval),
            "referenced_appended_lval_pages": sorted(referenced_appended),
            "unreferenced_appended_lval_pages": sorted(appended_lval - referenced_appended),
        }
    )
    return observation


def definition_chunks(data: bytes, pages: list[int], logical_length: int) -> list[dict[str, int]]:
    chunks = []
    consumed = 0
    for position, page in enumerate(pages):
        capacity = PAGE_BYTES if position == 0 else PAGE_BYTES - 8
        used = min(capacity, logical_length - consumed)
        if used <= 0:
            raise DecodeError("definition chain contains an unnecessary continuation page")
        image = catalog._page(data, page, "definition chain")
        following = int.from_bytes(image[4:8], "little")
        expected_following = pages[position + 1] if position + 1 < len(pages) else 0
        if following != expected_following:
            raise DecodeError("definition continuation pointer differs from decoded chain order")
        chunks.append(
            {
                "capacity": capacity,
                "logical_end": consumed + used,
                "logical_start": consumed,
                "page": page,
                "used": used,
            }
        )
        consumed += used
    if consumed != logical_length:
        raise DecodeError("definition chunks do not cover the logical length exactly")
    return chunks


def analyze_scenario(before: bytes, after: bytes, scenario: str) -> dict[str, Any]:
    before_analysis = catalog.analyze_checkpoint(before)
    after_analysis = catalog.analyze_checkpoint(after)
    if any(
        not entry["flags"] & catalog.SYSTEM_FLAG
        for entry in before_analysis["tables"].values()
    ):
        raise DecodeError(f"{scenario} empty baseline decodes a user table")
    table = decoded_user_table(after_analysis, scenario)
    definition = table["definition"]
    if definition["logical_length"] != EXPECTED_LOGICAL_LENGTHS[scenario]:
        raise DecodeError(
            f"{scenario} logical definition length differs from the boundary control"
        )
    expected_names = [field_name(value) for value in range(FIELD_COUNTS[scenario])]
    columns = definition["columns"]
    if (
        [entry["name"] for entry in columns] != expected_names
        or any(entry["type"] != "Long" or entry["size"] != 4 for entry in columns)
        or definition["physical_indexes"]
        or definition["logical_indexes"]
        or definition["long_value_maps"]
        or definition["row_count"] != 0
    ):
        raise DecodeError(f"{scenario} decoded definition differs from the empty fixed schema")
    pages = definition["pages"]
    expected_continuations = EXPECTED_CONTINUATIONS[scenario]
    if len(pages) != expected_continuations + 1:
        raise DecodeError(
            f"{scenario} has {len(pages) - 1} continuation pages; "
            f"the control requires {expected_continuations}"
        )
    if pages[0] != definition["root"] or len(set(pages)) != len(pages):
        raise DecodeError(f"{scenario} definition chain is malformed")
    chunks = definition_chunks(after, pages, definition["logical_length"])
    roles = {entry["page"]: entry for entry in after_analysis["pages"]}
    owner = f"table {definition['root']} {TABLE_NAMES[scenario]}"
    for position, page in enumerate(pages):
        expected_role = "definition_root" if position == 0 else "definition_continuation"
        role = roles.get(page)
        if role is None or role["role"] != expected_role or owner not in role["owners"]:
            raise DecodeError(f"{scenario} definition page {page} has the wrong role or owner")
    before_pages = len(before) // PAGE_BYTES
    after_pages = len(after) // PAGE_BYTES
    for chunk in chunks:
        chunk["delta_from_definition"] = chunk["page"] - definition["root"]
        chunk["delta_from_empty"] = chunk["page"] - before_pages
    if after_pages < before_pages:
        raise DecodeError(f"{scenario} create shrank the image")
    appended = []
    for page in range(before_pages, after_pages):
        role = roles.get(page)
        if role is None or role["role"] == "unassigned":
            raise DecodeError(f"{scenario} appended page {page} is unattributed")
        appended.append(
            {
                "delta_from_definition": page - definition["root"],
                "delta_from_empty": page - before_pages,
                "owners": role["owners"],
                "page": page,
                "role": role["role"],
            }
        )
    table_maps = {}
    for kind in ("owned", "available"):
        locator = definition["maps"][kind]
        table_maps[kind] = {
            "locator": locator,
            "mapped_pages": sorted(
                catalog._locator_pages(after, locator, f"{scenario} {kind} map")
            ),
        }
    lvprop = created_lvprop(after, after_analysis, TABLE_NAMES[scenario], before_pages)
    return {
        "appended_pages": appended,
        "catalog_object_id": definition["root"],
        "continuation_count": len(pages) - 1,
        "definition_chunks": chunks,
        "definition_pages": pages,
        "definition_root": definition["root"],
        "logical_length": definition["logical_length"],
        "lvprop": lvprop,
        "page_zero": {
            "after": after[catalog.PAGE0_COUNTER],
            "before": before[catalog.PAGE0_COUNTER],
            "changed_offsets": [
                offset
                for offset in range(PAGE_BYTES)
                if before[offset] != after[offset]
            ],
            "delta": after[catalog.PAGE0_COUNTER] - before[catalog.PAGE0_COUNTER],
        },
        "page_count": {"after": after_pages, "before": before_pages},
        "table_maps": table_maps,
    }


def build_report(document: dict[str, Any], replicas: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [entry for entry in replicas if "observations" in entry]
    question_names = (
        "continuation_counts",
        "placement",
        "counters",
        "producer_outcome",
        "replication",
    )
    if document["status"] != "pass" or len(complete) != 3:
        reason = "at least one replica did not complete"
        if any(entry.get("metadata_changed") for entry in replicas):
            reason = "DAO metadata access changed at least one checkpoint"
        elif any(entry.get("decode_error") for entry in replicas):
            reason = "at least one complete checkpoint failed a recorded grammar or control"
        questions = {name: {"reason": reason, "status": "no_outcome"} for name in question_names}
    else:
        observations = [entry["observations"] for entry in complete]
        if any(value != observations[0] for value in observations[1:]):
            questions = {
                name: {
                    "reason": "replicas disagree on the complete decoded observation",
                    "status": "no_outcome",
                }
                for name in question_names
            }
        else:
            value = observations[0]
            questions = {
                "continuation_counts": {
                    "scenarios": {
                        name: {
                            "continuation_count": value[name]["continuation_count"],
                            "definition_pages": value[name]["definition_pages"],
                            "logical_length": value[name]["logical_length"],
                            "lvprop": value[name]["lvprop"],
                        }
                        for name in SCENARIOS
                    },
                    "status": "answered",
                },
                "placement": {
                    "scenarios": {
                        name: {
                            "appended_pages": value[name]["appended_pages"],
                            "catalog_object_id": value[name]["catalog_object_id"],
                            "definition_chunks": value[name]["definition_chunks"],
                            "definition_root": value[name]["definition_root"],
                            "page_count": value[name]["page_count"],
                            "table_maps": value[name]["table_maps"],
                        }
                        for name in SCENARIOS
                    },
                    "status": "answered",
                },
                "counters": {
                    "scenarios": {
                        name: value[name]["page_zero"] for name in SCENARIOS
                    },
                    "status": "answered",
                },
                "producer_outcome": {"kind": "completed", "status": "answered"},
                "replication": {"replicas": 3, "status": "answered"},
            }
    return {
        "compatibility_claim": False,
        "development_only": True,
        "document_type": REPORT_TYPE,
        "plan_sha256": document["plan_sha256"],
        "questions": questions,
        "replicas": [
            {
                key: value
                for key, value in entry.items()
                if key != "observations"
            }
            for entry in replicas
        ],
        "status": (
            "accepted"
            if all(value["status"] == "answered" for value in questions.values())
            else "no_outcome"
        ),
        "support_movement": False,
    }


def evaluate(job_result: Path, expected_plan_sha256: str, output: Path) -> dict[str, Any]:
    expected_plan = digest(expected_plan_sha256, "--expected-plan-sha256")
    document = exact(
        load(job_result),
        {"document_type", "development_only", "plan_sha256", "run_id", "status", "replicas"},
        "$",
    )
    if document["document_type"] != DOCUMENT_TYPE or document["development_only"] is not True:
        raise AnalysisError("job result identity is invalid")
    if digest(document["plan_sha256"], "$.plan_sha256") != expected_plan:
        raise AnalysisError("job result plan digest differs from the approved plan")
    if not isinstance(document["run_id"], str) or not SAFE_RUN_ID.fullmatch(document["run_id"]):
        raise AnalysisError("$.run_id is invalid")
    if document["status"] not in ("pass", "fail"):
        raise AnalysisError("$.status is invalid")
    raw_replicas = document["replicas"]
    if not isinstance(raw_replicas, list) or not 1 <= len(raw_replicas) <= 3:
        raise AnalysisError("$.replicas must contain one through three replicas")
    replicas = []
    referenced = []
    for position, raw_replica in enumerate(raw_replicas):
        item = exact(
            raw_replica,
            {
                "replica",
                "status",
                "error",
                "mutation_started",
                "phase",
                "checkpoints",
                "arm_baselines",
                "failure_measurement",
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
            *(f"append_{name}" for name in SCENARIOS),
            *(f"capture_{name}" for name in SCENARIOS),
            "complete",
        }:
            raise AnalysisError("replica phase is invalid")
        if item["error"] is not None:
            text(item["error"], "replica error")
        failure_measurement = item["failure_measurement"]
        if failure_measurement is not None:
            failure_measurement = validate_measurement(
                failure_measurement, "replica failure_measurement"
            )
            if failure_measurement["failed_predicate"] is None:
                raise AnalysisError("failure_measurement must identify a failed predicate")
        checkpoints = item["checkpoints"]
        if not isinstance(checkpoints, list) or len(checkpoints) > len(CHECKPOINTS):
            raise AnalysisError("replica checkpoints violate the bound")
        images: dict[str, bytes] = {}
        daos: dict[str, dict[str, Any]] = {}
        identities: dict[str, dict[str, Any]] = {}
        arm_befores: dict[str, dict[str, Any]] = {}
        changed = []
        for checkpoint_position, raw_checkpoint in enumerate(checkpoints):
            name = CHECKPOINTS[checkpoint_position]
            image, repaired, dao, arm_before, identity = read_checkpoint(
                job_result.parent, raw_checkpoint, replica, name
            )
            images[name] = image
            daos[name] = dao
            identities[name] = identity
            referenced.append(f"definition-continuation-r{replica}-{name}.mdb")
            if repaired:
                changed.append(name)
            if name == "empty":
                if arm_before is not None:
                    raise AnalysisError("empty checkpoint must not have an arm identity")
            else:
                before = exact(
                    arm_before, {"size", "sha256", "measurement"}, f"{name}.arm_before"
                )
                before_size = integer(
                    before["size"],
                    "arm size",
                    PAGE_BYTES,
                    MAX_CHECKPOINT_PAGES * PAGE_BYTES,
                )
                if before_size % PAGE_BYTES:
                    raise AnalysisError("arm size is not an exact sequence of pages")
                before_measurement = validate_measurement(
                    before["measurement"], f"{name}.arm_before.measurement"
                )
                if (
                    before_measurement["failed_predicate"] is not None
                    or before_measurement["raw_byte_length"] != before_size
                ):
                    raise AnalysisError("arm identity has an invalid raw measurement")
                arm_befores[name] = before
                if before_size != identities["empty"]["size"] or digest(
                    before["sha256"], "arm digest"
                ) != identities["empty"]["sha256"]:
                    raise AnalysisError(
                        f"{name} arm identity differs from the retained empty image"
                    )
        raw_baselines = item["arm_baselines"]
        if not isinstance(raw_baselines, list) or len(raw_baselines) > len(SCENARIOS):
            raise AnalysisError("replica arm_baselines violate the bound")
        baselines: dict[str, dict[str, Any]] = {}
        for baseline_position, raw_baseline in enumerate(raw_baselines):
            scenario = SCENARIOS[baseline_position]
            baseline = exact(
                raw_baseline,
                {"name", "size", "sha256", "measurement"},
                f"replica {replica} arm_baselines[{baseline_position}]",
            )
            if baseline["name"] != scenario:
                raise AnalysisError("arm_baselines are not an ordered scenario prefix")
            baseline_size = integer(
                baseline["size"],
                "arm baseline size",
                PAGE_BYTES,
                MAX_CHECKPOINT_PAGES * PAGE_BYTES,
            )
            baseline_measurement = validate_measurement(
                baseline["measurement"], "arm baseline measurement"
            )
            if (
                baseline_measurement["failed_predicate"] is not None
                or baseline_measurement["raw_byte_length"] != baseline_size
            ):
                raise AnalysisError("arm baseline has an invalid raw measurement")
            baseline_identity = {
                "measurement": baseline_measurement,
                "sha256": digest(baseline["sha256"], "arm baseline digest"),
                "size": baseline_size,
            }
            if "empty" not in identities or {
                "sha256": baseline_identity["sha256"],
                "size": baseline_identity["size"],
            } != identities["empty"]:
                raise AnalysisError("arm baseline differs from the retained empty image")
            baselines[scenario] = baseline_identity
        for scenario, before in arm_befores.items():
            if baselines.get(scenario) != before:
                raise AnalysisError("checkpoint arm identity differs from its recorded baseline")
        recovery = item["recovery"]
        if not isinstance(recovery, list) or len(recovery) > 1:
            raise AnalysisError("replica recovery inventory violates the bound")
        checkpoint_names = set(images)
        for raw_recovery in recovery:
            value = exact(
                raw_recovery,
                {
                    "name",
                    "database",
                    "size",
                    "sha256",
                    "measurement",
                    "reason",
                    "interpreted",
                },
                "recovery artifact",
            )
            name = value["name"]
            expected_recovery = (
                CHECKPOINTS[len(images)] if len(images) < len(CHECKPOINTS) else None
            )
            if name != expected_recovery or name in checkpoint_names:
                raise AnalysisError("recovery artifact name is invalid or duplicated")
            filename = value["database"]
            if (
                filename != f"definition-continuation-r{replica}-{name}.mdb"
                or not SAFE_MDB.fullmatch(filename)
            ):
                raise AnalysisError("recovery artifact filename is invalid")
            size = integer(
                value["size"],
                "recovery size",
                PAGE_BYTES,
                MAX_RECOVERY_PAGES * PAGE_BYTES,
            )
            if size % PAGE_BYTES:
                raise AnalysisError("recovery artifact is not an exact sequence of pages")
            measurement = validate_measurement(value["measurement"], "recovery measurement")
            if size != measurement["raw_byte_length"]:
                raise AnalysisError("recovery size differs from its raw measurement")
            if value["reason"] not in ("checkpoint_bound_exceeded", "post_mutation_failure"):
                raise AnalysisError("recovery reason is invalid")
            if value["interpreted"] is not False:
                raise AnalysisError("recovery artifact must be explicitly uninterpreted")
            if measurement["failed_predicate"] == "checkpoint_bound_exceeded":
                if value["reason"] != "checkpoint_bound_exceeded":
                    raise AnalysisError("over-bound recovery omits its checkpoint failure reason")
            elif measurement["failed_predicate"] is not None:
                raise AnalysisError("unaligned or undersized recovery cannot be retained")
            elif value["reason"] != "post_mutation_failure":
                raise AnalysisError("in-bound recovery has an invalid reason")
            path = job_result.parent / filename
            if not path.is_file() or path.is_symlink():
                raise AnalysisError("recovery artifact must be a regular non-link file")
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest(
                value["sha256"], "recovery digest"
            ):
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
                "measurement": raw_checkpoint["measurement"],
                "measurement_after_metadata": raw_checkpoint["measurement_after_metadata"],
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
                "measurement": artifact["measurement"],
                "reason": artifact["reason"],
                "interpreted": artifact["interpreted"],
            }
            for artifact in recovery
        )
        entry: dict[str, Any] = {
            "arm_baselines": raw_baselines,
            "error": item["error"],
            "files": files,
            "failure_measurement": failure_measurement,
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
                or tuple(baselines) != SCENARIOS
                or failure_measurement is not None
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
                    entry["observations"] = {
                        name: analyze_scenario(images["empty"], images[name], name)
                        for name in SCENARIOS
                    }
                except (catalog.DecodeError, DecodeError) as error:
                    entry["decode_error"] = str(error)
        elif item["error"] is None:
            raise AnalysisError("failed replica omits its error")
        else:
            progress = {
                "before_create_database": (0, None, False),
                "create_database": (0, "empty", False),
                "capture_empty": (0, "empty", True),
                "copy_arms": (1, None, True),
                "append_zero": (1, "zero", True),
                "capture_zero": (1, "zero", True),
                "append_one": (2, "one", True),
                "capture_one": (2, "one", True),
                "append_two": (3, "two", True),
                "capture_two": (3, "two", True),
                "complete": (4, None, True),
            }
            expected = progress.get(phase)
            if expected is None:
                raise AnalysisError("failed replica phase is inconsistent with producer progress")
            checkpoint_count, recovery_name, mutation_required = expected
            if len(images) != checkpoint_count:
                raise AnalysisError(
                    "failed replica checkpoint prefix is inconsistent with its phase"
                )
            if recovery and recovery[0]["name"] != recovery_name:
                raise AnalysisError("failed replica recovery is inconsistent with its phase")
            if recovery_name is None and recovery:
                raise AnalysisError("failed replica phase cannot retain a recovery artifact")
            if phase in ("before_create_database", "create_database", "capture_empty") and baselines:
                raise AnalysisError("failure before arm copying retained an arm baseline")
            if phase == "copy_arms" and tuple(baselines) != SCENARIOS[: len(baselines)]:
                raise AnalysisError("copy_arms failure has an invalid baseline prefix")
            if phase not in (
                "before_create_database",
                "create_database",
                "capture_empty",
                "copy_arms",
            ) and tuple(baselines) != SCENARIOS:
                raise AnalysisError("table-append phase lacks all arm baselines")
            if mutation_required and not item["mutation_started"]:
                raise AnalysisError("failed replica phase requires a started DAO mutation")
            if phase == "before_create_database" and item["mutation_started"]:
                raise AnalysisError(
                    "before_create_database phase cannot have a started DAO mutation"
                )
            measurement_phases = {
                "create_database",
                "capture_empty",
                "copy_arms",
                *(f"append_{name}" for name in SCENARIOS),
                *(f"capture_{name}" for name in SCENARIOS),
            }
            if failure_measurement is not None and (
                phase not in measurement_phases
                or (phase == "create_database" and not item["mutation_started"])
            ):
                raise AnalysisError(
                    "failure_measurement is inconsistent with producer phase"
                )
            if not item["mutation_started"] and recovery:
                raise AnalysisError("pre-mutation failure cannot retain a recovery artifact")
            if recovery and recovery[0]["reason"] == "checkpoint_bound_exceeded":
                if failure_measurement != recovery[0]["measurement"]:
                    raise AnalysisError(
                        "bound-exceeded recovery lacks its exact failed checkpoint measurement"
                    )
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
        print(f"definition-continuation analysis failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
