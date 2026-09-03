#!/usr/bin/env python3
"""Validate the bounded null-LvProp schema experiment for issue #178."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


PAGE_BYTES = 2048
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_PAGES = 80
MAX_TABLES = 16
MAX_FIELDS = 80
MAX_INDEXES = 4
MAX_INDEX_FIELDS = 4
MAX_TEXT = 512
DOCUMENT_TYPE = "dao_lvprop_null_schemas_job_result"
REPORT_TYPE = "lvprop_null_schemas_report"
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
SCHEMA_ORDER = ("alpha", "indexed", "wide")
ROLE_ORDER = tuple(
    [f"candidate_{schema}" for schema in SCHEMA_ORDER]
    + [f"control_{schema}" for schema in SCHEMA_ORDER]
)
SYSTEM_TABLES = ("MSysACEs", "MSysObjects", "MSysQueries", "MSysRelationships")
CANDIDATES = {
    "candidate_alpha": {
        "filename": "lvprop-schemas-alpha.mdb",
        "size": 47104,
        "sha256": "c9d012d6277a0a35ae4248581fc9458d9b270e56277819e84dc7f1f5e8009e21",
    },
    "candidate_indexed": {
        "filename": "lvprop-schemas-indexed.mdb",
        "size": 53248,
        "sha256": "bb7e0d408a5e844dd0fbe6eae008a4ca31bd83f376e611339ad5f8385572835e",
    },
    "candidate_wide": {
        "filename": "lvprop-schemas-wide.mdb",
        "size": 49152,
        "sha256": "81cfd7b86616f9928b71cab4398f26305d5dafdbe4bfa0a514e6f9b4146f1cf6",
    },
}
CONTROL_SIZES = {"alpha": 47104, "indexed": 53248, "wide": 141312}
ENDPOINTS = (
    "open_database",
    "version",
    "tabledefs",
    "direct_lookup",
    "fields",
    "indexes",
    "snapshot",
    "document",
)
INDEXES = (
    {
        "name": "ZPrimary",
        "primary": True,
        "unique": True,
        "required": True,
        "fields": ({"name": "Id", "descending": False},),
    },
    {
        "name": "MUniqueX",
        "primary": False,
        "unique": True,
        "required": False,
        "fields": ({"name": "Code", "descending": True},),
    },
    {
        "name": "ASecondx",
        "primary": False,
        "unique": False,
        "required": False,
        "fields": ({"name": "Sequence", "descending": False},),
    },
)
SCHEMAS = {
    "alpha": {"table": "Alpha", "fields": ("Id",), "indexes": ()},
    "indexed": {
        "table": "IdxTri",
        "fields": ("Id", "Code", "Sequence"),
        "indexes": INDEXES,
    },
    "wide": {
        "table": "ContOneX",
        "fields": tuple(f"F{ordinal:03}AAAAAA" for ordinal in range(70)),
        "indexes": (),
    },
}


class AnalysisError(ValueError):
    """The published result violates the preregistered contract."""


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
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


def bounded_text(value: Any, where: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise AnalysisError(f"{where} must be text of at most {maximum} characters")
    return value


def digest(value: Any, where: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise AnalysisError(f"{where} must be a lowercase SHA-256 digest")
    return value


def integer(value: Any, where: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise AnalysisError(f"{where} must be an integer in [{low}, {high}]")
    return value


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AnalysisError("job result must be a regular non-link file")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise AnalysisError("job result exceeds the JSON bound")
    try:
        value = json.loads(raw, object_pairs_hook=without_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("job result is not valid unique-key UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise AnalysisError("job result root must be an object")
    return value


def normalize_snapshot(value: Any, schema_name: str) -> tuple[dict[str, Any], bool]:
    item = exact(value, {"tabledefs", "fields", "indexes", "table_documents"}, "snapshot")
    schema = SCHEMAS[schema_name]
    expected_tables = sorted((schema["table"], *SYSTEM_TABLES))
    tables_match = item["tabledefs"] == expected_tables and item["table_documents"] == expected_tables

    fields = item["fields"]
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
        raise AnalysisError("snapshot fields exceed the bound")
    normalized_fields = []
    for position, raw in enumerate(fields):
        field = exact(raw, {"name", "type"}, f"field {position}")
        normalized_fields.append(
            {
                "name": bounded_text(field["name"], "field name", 256),
                "type": integer(field["type"], "field type", -(1 << 31), (1 << 31) - 1),
            }
        )

    indexes = item["indexes"]
    if not isinstance(indexes, list) or len(indexes) > MAX_INDEXES:
        raise AnalysisError("snapshot indexes exceed the bound")
    normalized_indexes = []
    for position, raw in enumerate(indexes):
        index = exact(
            raw,
            {"name", "primary", "unique", "required", "fields"},
            f"index {position}",
        )
        if any(type(index[key]) is not bool for key in ("primary", "unique", "required")):
            raise AnalysisError("index flags must be boolean")
        fields_value = index["fields"]
        if not isinstance(fields_value, list) or not 1 <= len(fields_value) <= MAX_INDEX_FIELDS:
            raise AnalysisError("index fields violate the bound")
        normalized_keys = []
        for key_position, raw_key in enumerate(fields_value):
            key = exact(raw_key, {"name", "descending"}, f"index field {key_position}")
            if type(key["descending"]) is not bool:
                raise AnalysisError("index direction must be boolean")
            normalized_keys.append(
                {
                    "name": bounded_text(key["name"], "index field name", 256),
                    "descending": key["descending"],
                }
            )
        normalized_indexes.append(
            {
                "name": bounded_text(index["name"], "index name", 256),
                "primary": index["primary"],
                "unique": index["unique"],
                "required": index["required"],
                "fields": normalized_keys,
            }
        )
    normalized_indexes.sort(key=lambda entry: entry["name"])
    expected_fields = [{"name": name, "type": 4} for name in schema["fields"]]
    expected_indexes = [
        {**{key: value for key, value in entry.items() if key != "fields"}, "fields": list(entry["fields"])}
        for entry in schema["indexes"]
    ]
    expected_indexes.sort(key=lambda entry: entry["name"])
    normalized = {
        "tabledefs": item["tabledefs"],
        "fields": normalized_fields,
        "indexes": normalized_indexes,
        "table_documents": item["table_documents"],
    }
    return normalized, (
        tables_match
        and normalized_fields == expected_fields
        and normalized_indexes == expected_indexes
    )


def read_endpoints(value: Any, schema: str) -> dict[str, Any]:
    item = exact(value, {"status", "completed", "detail", "snapshot"}, "endpoints")
    if item["status"] not in ("pass", "fail"):
        raise AnalysisError("endpoint status is invalid")
    completed = item["completed"]
    if not isinstance(completed, list) or completed != list(ENDPOINTS[: len(completed)]):
        raise AnalysisError("completed endpoints are not an exact prefix")
    if item["status"] == "pass" and completed != list(ENDPOINTS):
        raise AnalysisError("passing endpoints are incomplete")
    detail = bounded_text(item["detail"], "endpoint detail")
    snapshot = item["snapshot"]
    if item["status"] == "pass":
        normalized, schema_matches = normalize_snapshot(snapshot, schema)
    else:
        if not isinstance(snapshot, dict):
            raise AnalysisError("failed endpoint snapshot must be an object")
        normalized, schema_matches = snapshot, False
    return {
        "status": item["status"],
        "completed": completed,
        "detail": detail,
        "snapshot": normalized,
        "schema_matches": schema_matches,
    }


def expected_filename(replica: int, role: str) -> str:
    kind, schema = role.split("_", 1)
    return f"{kind}-r{replica}-{schema}.mdb"


def read_image(root: Path, value: Any, replica: int, expected_role: str) -> dict[str, Any]:
    item = exact(
        value,
        {
            "role",
            "database",
            "size_before",
            "sha256_before",
            "endpoints",
            "size_after",
            "sha256_after",
        },
        f"replica {replica} image",
    )
    if item["role"] != expected_role or item["database"] != expected_filename(replica, expected_role):
        raise AnalysisError("image role or filename is out of order")
    size_before = integer(item["size_before"], "size before", PAGE_BYTES, MAX_PAGES * PAGE_BYTES)
    size_after = integer(item["size_after"], "size after", PAGE_BYTES, MAX_PAGES * PAGE_BYTES)
    if size_before % PAGE_BYTES or size_after % PAGE_BYTES:
        raise AnalysisError("image size is not an exact page sequence")
    before = digest(item["sha256_before"], "digest before")
    after = digest(item["sha256_after"], "digest after")
    if expected_role.startswith("candidate_"):
        pin = CANDIDATES[expected_role]
        if size_before != pin["size"] or before != pin["sha256"]:
            raise AnalysisError("candidate differs from its preregistered identity")
    path = root / item["database"]
    if not path.is_file() or path.is_symlink():
        raise AnalysisError("referenced MDB must be a regular non-link file")
    retained = path.read_bytes()
    if len(retained) != size_after or hashlib.sha256(retained).hexdigest() != after:
        raise AnalysisError("retained MDB differs from its recorded identity")
    schema = expected_role.split("_", 1)[1]
    endpoints = read_endpoints(item["endpoints"], schema)
    if expected_role.startswith("control_") and endpoints["status"] == "pass":
        if size_before != CONTROL_SIZES[schema]:
            raise AnalysisError("passing control has an unexpected size")
    return {
        "role": expected_role,
        "database": item["database"],
        "size_before": size_before,
        "sha256_before": before,
        "size_after": size_after,
        "sha256_after": after,
        "changed": size_before != size_after or before != after,
        "endpoints": endpoints,
    }


def classify(schema: str, replicas: list[dict[str, Any]]) -> dict[str, str]:
    candidate_role = f"candidate_{schema}"
    control_role = f"control_{schema}"
    if any(candidate_role not in replica["by_role"] or control_role not in replica["by_role"] for replica in replicas):
        return {"status": "no_outcome", "reason": "the scientific observation is incomplete"}
    candidates = [replica["by_role"][candidate_role] for replica in replicas]
    controls = [replica["by_role"][control_role] for replica in replicas]
    if any(image["changed"] for image in candidates + controls):
        return {"status": "no_outcome", "reason": "DAO changed at least one image"}
    if any(image["endpoints"]["status"] != "pass" for image in controls):
        return {"status": "no_outcome", "reason": "a fresh same-schema control failed"}
    control_snapshots = [image["endpoints"]["snapshot"] for image in controls]
    if not all(image["endpoints"]["schema_matches"] for image in controls) or len({canonical_bytes(value) for value in control_snapshots}) != 1:
        return {"status": "no_outcome", "reason": "fresh controls disagree or have unexpected semantics"}
    outcomes = [image["endpoints"] for image in candidates]
    statuses = {outcome["status"] for outcome in outcomes}
    if len(statuses) != 1:
        return {"status": "no_outcome", "reason": "candidate replicas disagree"}
    comparable = [
        {key: outcome[key] for key in ("status", "completed", "detail", "snapshot")}
        for outcome in outcomes
    ]
    if len({canonical_bytes(value) for value in comparable}) != 1:
        return {"status": "no_outcome", "reason": "candidate observations disagree"}
    if outcomes[0]["status"] == "fail":
        return {"status": "not_observed_accepted", "reason": "all candidate replicas stopped identically"}
    if all(outcome["schema_matches"] for outcome in outcomes) and outcomes[0]["snapshot"] == control_snapshots[0]:
        return {"status": "observed_accepted", "reason": "all candidate replicas passed unchanged with the control semantics"}
    return {"status": "not_observed_accepted", "reason": "all candidate replicas completed with the same semantic mismatch"}


def evaluate(job_result: Path, expected_plan: str, output: Path) -> dict[str, Any]:
    if not DIGEST.fullmatch(expected_plan):
        raise AnalysisError("expected plan digest is invalid")
    document = exact(
        load(job_result),
        {"document_type", "development_only", "plan_sha256", "run_id", "status", "mutation_started", "replicas"},
        "job result",
    )
    if document["document_type"] != DOCUMENT_TYPE or document["development_only"] is not True:
        raise AnalysisError("job result identity is invalid")
    if document["plan_sha256"] != expected_plan or not RUN_ID.fullmatch(document["run_id"]):
        raise AnalysisError("job result plan or run identity is invalid")
    if type(document["mutation_started"]) is not bool:
        raise AnalysisError("mutation_started must be boolean")
    if document["status"] not in ("pass", "fail"):
        raise AnalysisError("job result status is invalid")
    raw_replicas = document["replicas"]
    if not isinstance(raw_replicas, list) or len(raw_replicas) != 3:
        raise AnalysisError("job result must contain exactly three replicas")
    replicas = []
    for position, raw in enumerate(raw_replicas, 1):
        item = exact(raw, {"replica", "status", "error", "images"}, f"replica {position}")
        if item["replica"] != position or item["status"] not in ("pass", "fail"):
            raise AnalysisError("replica identity or status is invalid")
        if item["status"] == "pass" and item["error"] is not None:
            raise AnalysisError("passing replica has an error")
        if item["status"] == "fail":
            bounded_text(item["error"], "replica error")
        images = item["images"]
        if not isinstance(images, list) or len(images) > len(ROLE_ORDER):
            raise AnalysisError("replica images exceed the bound")
        parsed = [read_image(job_result.parent, value, position, ROLE_ORDER[index]) for index, value in enumerate(images)]
        if item["status"] == "pass" and len(parsed) != len(ROLE_ORDER):
            raise AnalysisError("passing replica is incomplete")
        replicas.append({"replica": position, "status": item["status"], "images": parsed, "by_role": {image["role"]: image for image in parsed}})
    aggregate = "pass" if all(replica["status"] == "pass" for replica in replicas) else "fail"
    if document["status"] != aggregate:
        raise AnalysisError("job status disagrees with replica statuses")
    if not document["mutation_started"]:
        raise AnalysisError("the job failed before its first DAO mutation")

    referenced = {image["database"] for replica in replicas for image in replica["images"]}
    allowed = {expected_filename(replica, role) for replica in range(1, 4) for role in ROLE_ORDER}
    retained_paths = [path for path in job_result.parent.iterdir() if path.suffix.casefold() == ".mdb"]
    retained = set()
    for path in retained_paths:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_PAGES * PAGE_BYTES:
            raise AnalysisError("retained MDB violates the file bound")
        retained.add(path.name)
    if not referenced <= retained or not retained <= allowed or len(retained) != len(retained_paths):
        raise AnalysisError("retained MDB inventory is invalid")

    questions = {schema: classify(schema, replicas) for schema in SCHEMA_ORDER}
    accepted = (
        questions["alpha"]["status"] == "observed_accepted"
        and questions["indexed"]["status"] in ("observed_accepted", "not_observed_accepted")
        and questions["wide"]["status"] in ("observed_accepted", "not_observed_accepted")
    )
    report = {
        "document_type": REPORT_TYPE,
        "plan_sha256": expected_plan,
        "run_id": document["run_id"],
        "status": "accepted" if accepted else "no_outcome",
        "questions": questions,
        "replicas": [
            {
                "replica": replica["replica"],
                "status": replica["status"],
                "files": [
                    {key: image[key] for key in ("role", "database", "size_before", "sha256_before", "size_after", "sha256_after")}
                    for image in replica["images"]
                ],
            }
            for replica in replicas
        ],
        "compatibility_claim": False,
        "support_movement": False,
    }
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
        print(f"null-LvProp schema validation rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
