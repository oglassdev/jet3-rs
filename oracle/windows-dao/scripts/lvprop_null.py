#!/usr/bin/env python3
"""Validate DAO acceptance of a composed Alpha table with a null LvProp."""

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
MAX_TEXT = 512
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")
SYSTEM_TABLES = ["MSysACEs", "MSysObjects", "MSysQueries", "MSysRelationships"]
ALPHA_TABLES = ["Alpha", *SYSTEM_TABLES]
ALPHA_ENDPOINTS = [
    "open_database",
    "version",
    "tabledefs",
    "direct_lookup",
    "field",
    "properties",
    "snapshot",
    "document",
]
CANDIDATES = {
    "candidate_fixed": {
        "size": 47_104,
        "sha256": "b798de9209637361245703b0132f59c06dd7cb3d051d214415d6ed6a76768df2",
    },
    "candidate_null": {
        "size": 47_104,
        "sha256": "c9d012d6277a0a35ae4248581fc9458d9b270e56277819e84dc7f1f5e8009e21",
    },
}
ROLES = ("candidate_fixed", "candidate_null", "control_alpha")
DATABASE_NAMES = {
    "candidate_fixed": "candidate-r{replica}-fixed.mdb",
    "candidate_null": "candidate-r{replica}-null.mdb",
    "control_alpha": "control-r{replica}-alpha.mdb",
}


class AnalysisError(ValueError):
    """The producer result or retained artifact failed validation."""


def canonical_bytes(document: Any) -> bytes:
    return (
        json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def load_document(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AnalysisError("job result must be a regular file")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise AnalysisError("job result exceeds the four-MiB bound")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise AnalysisError("job result exceeds the four-MiB bound")
    try:
        value = json.loads(raw, object_pairs_hook=object_without_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("job result is not valid unique-key UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise AnalysisError("job result root must be an object")
    return value


def exact_object(value: Any, keys: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AnalysisError(f"{location} must contain exactly {sorted(keys)}")
    return value


def digest(value: Any, location: str) -> str:
    if not isinstance(value, str) or not HEX_64.fullmatch(value):
        raise AnalysisError(f"{location} must be a lowercase SHA-256 digest")
    return value


def bounded_text(value: Any, location: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise AnalysisError(f"{location} must be a string of at most {maximum} characters")
    return value


def exact_names(value: Any, expected: list[str], location: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or len(item) > 256 for item in value)
        or value != sorted(expected)
    ):
        raise AnalysisError(f"{location} differs from the exact expected names")
    return value


def property_shape(value: Any, location: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 64:
        raise AnalysisError(f"{location} must contain at most 64 properties")
    names: set[str] = set()
    for index, raw in enumerate(value):
        item = exact_object(raw, {"name", "type"}, f"{location}[{index}]")
        name = bounded_text(item["name"], f"{location}[{index}].name", 256)
        if not name or name in names:
            raise AnalysisError(f"{location} property names must be nonempty and unique")
        if type(item["type"]) is not int or not -(1 << 31) <= item["type"] < (1 << 31):
            raise AnalysisError(f"{location}[{index}].type is invalid")
        names.add(name)
    return value


def validate_snapshot(value: Any, completed: list[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnalysisError(f"{location} must be an object")
    allowed = {
        "tabledefs",
        "table_documents",
        "field",
        "table_properties",
        "field_properties",
        "field_required",
    }
    if not set(value) <= allowed:
        raise AnalysisError(f"{location} has unexpected fields")
    if "tabledefs" in value:
        exact_names(value["tabledefs"], ALPHA_TABLES, f"{location}.tabledefs")
    elif "tabledefs" in completed:
        raise AnalysisError(f"{location}.tabledefs is missing")
    if "table_documents" in value:
        exact_names(
            value["table_documents"],
            ALPHA_TABLES,
            f"{location}.table_documents",
        )
    elif ("documents" in completed) or ("document" in completed):
        raise AnalysisError(f"{location}.table_documents is missing")
    if "field" in value:
        field = exact_object(value["field"], {"name", "type"}, f"{location}.field")
        if field != {"name": "Id", "type": 4}:
            raise AnalysisError(f"{location}.field is not Id Long")
    elif "field" in completed:
        raise AnalysisError(f"{location}.field is missing")
    if "table_properties" in value:
        property_shape(value["table_properties"], f"{location}.table_properties")
    if "field_properties" in value:
        fields = property_shape(value["field_properties"], f"{location}.field_properties")
        if "Required" not in [item["name"] for item in fields]:
            raise AnalysisError(f"{location}.field_properties omits Required")
    if "field_required" in value and type(value["field_required"]) is not bool:
        raise AnalysisError(f"{location}.field_required must be boolean")
    if "properties" in completed:
        if not {"table_properties", "field_properties", "field_required"} <= set(value):
            raise AnalysisError(f"{location} omits completed property observations")
        if value["field_required"] is not False:
            raise AnalysisError(f"{location}.field_required must be false")
    return value


def validate_endpoints(value: Any, role: str, location: str) -> dict[str, Any]:
    item = exact_object(value, {"status", "completed", "detail", "snapshot"}, location)
    expected = ALPHA_ENDPOINTS
    completed = item["completed"]
    if (
        not isinstance(completed, list)
        or any(not isinstance(entry, str) for entry in completed)
        or completed != expected[: len(completed)]
    ):
        raise AnalysisError(f"{location}.completed is not an ordered endpoint prefix")
    if item["status"] not in ("pass", "fail"):
        raise AnalysisError(f"{location}.status is invalid")
    if (item["status"] == "pass") != (completed == expected):
        raise AnalysisError(f"{location}.status disagrees with its endpoint frontier")
    bounded_text(item["detail"], f"{location}.detail")
    validate_snapshot(item["snapshot"], completed, f"{location}.snapshot")
    return item


def read_image(
    root: Path,
    value: Any,
    replica: int,
    position: int,
    replica_status: str,
) -> dict[str, Any]:
    location = f"replicas[{replica - 1}].images[{position}]"
    item = exact_object(
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
        location,
    )
    role = item["role"]
    if role != ROLES[position]:
        raise AnalysisError(f"{location}.role is out of order")
    expected_name = DATABASE_NAMES[role].format(replica=replica)
    if item["database"] != expected_name:
        raise AnalysisError(f"{location}.database is invalid")
    for key in ("size_before", "size_after"):
        size = item[key]
        if type(size) is not int or not 0 <= size <= 64 * PAGE_BYTES:
            raise AnalysisError(f"{location}.{key} is outside the byte bound")
    before = digest(item["sha256_before"], f"{location}.sha256_before")
    after = digest(item["sha256_after"], f"{location}.sha256_after")
    endpoints = validate_endpoints(item["endpoints"], role, f"{location}.endpoints")
    if role in CANDIDATES:
        expected = CANDIDATES[role]
        if item["size_before"] != expected["size"] or before != expected["sha256"]:
            raise AnalysisError(f"{location} differs from the preregistered candidate")
    elif replica_status == "pass" and item["size_before"] != 47_104:
        raise AnalysisError(f"{location} control is not exactly 23 pages")
    path = root / expected_name
    if not path.is_file() or path.is_symlink():
        raise AnalysisError(f"{location}.database is not an adjacent regular file")
    retained_size = path.stat().st_size
    if retained_size > 64 * PAGE_BYTES:
        raise AnalysisError(f"{location}.database exceeds the byte bound")
    hasher = hashlib.sha256()
    observed_size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            observed_size += len(chunk)
            if observed_size > 64 * PAGE_BYTES:
                raise AnalysisError(f"{location}.database grew beyond the byte bound")
            hasher.update(chunk)
    if observed_size != retained_size:
        raise AnalysisError(f"{location}.database changed while it was hashed")
    if observed_size != item["size_after"] or hasher.hexdigest() != after:
        raise AnalysisError(f"{location}.database differs from its retained identity")
    return {
        "database": expected_name,
        "endpoints": endpoints,
        "metadata_repaired": item["size_before"] != item["size_after"] or before != after,
        "role": role,
        "sha256_after": after,
        "sha256_before": before,
        "size_after": item["size_after"],
        "size_before": item["size_before"],
    }


def images_for_role(
    replicas: list[dict[str, Any]], role: str
) -> list[dict[str, Any]] | None:
    selected: list[dict[str, Any]] = []
    for replica in replicas:
        matching = [image for image in replica["images"] if image["role"] == role]
        if replica["status"] != "pass" or len(matching) != 1:
            return None
        selected.append(matching[0])
    return selected


def endpoint_projection(image: dict[str, Any]) -> dict[str, Any]:
    return {
        "completed": image["endpoints"]["completed"],
        "detail": image["endpoints"]["detail"],
        "snapshot": image["endpoints"]["snapshot"],
        "status": image["endpoints"]["status"],
    }


def fresh_control_snapshot(
    document: dict[str, Any], replicas: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None]:
    selected = images_for_role(replicas, "control_alpha")
    if document["status"] != "pass" or selected is None:
        return None, "at least one fresh DAO control did not complete"
    if any(image["metadata_repaired"] for image in selected):
        return None, "DAO changed at least one fresh control"
    projections = [endpoint_projection(image) for image in selected]
    if any(projection["status"] != "pass" for projection in projections):
        return None, "at least one fresh DAO control failed"
    if any(projection != projections[0] for projection in projections[1:]):
        return None, "fresh DAO control observations disagree"
    return projections[0]["snapshot"], None


def question_for_candidate(
    replicas: list[dict[str, Any]], role: str, control_snapshot: dict[str, Any]
) -> dict[str, Any]:
    selected = images_for_role(replicas, role)
    if selected is None:
        return {"reason": "at least one replica did not complete", "status": "no_outcome"}
    if any(image["metadata_repaired"] for image in selected):
        return {"reason": "DAO changed at least one candidate", "status": "no_outcome"}
    projections = [endpoint_projection(image) for image in selected]
    if any(projection != projections[0] for projection in projections[1:]):
        return {"reason": "candidate endpoint observations disagree", "status": "no_outcome"}
    projection = projections[0]
    if projection["status"] == "fail":
        return {**projection, "status": "not_observed_accepted"}
    if projection["snapshot"] != control_snapshot:
        return {
            **projection,
            "reason": "candidate semantic snapshot differs from the fresh DAO control",
            "status": "not_observed_accepted",
        }
    return {**projection, "status": "observed_accepted"}


def build_report(document: dict[str, Any], replicas: list[dict[str, Any]]) -> dict[str, Any]:
    control_snapshot, control_error = fresh_control_snapshot(document, replicas)
    if control_snapshot is None:
        questions = {
            name: {"reason": control_error, "status": "no_outcome"}
            for name in ("fixed_candidate", "null_candidate")
        }
    else:
        fixed = question_for_candidate(replicas, "candidate_fixed", control_snapshot)
        if fixed["status"] == "observed_accepted":
            null = question_for_candidate(replicas, "candidate_null", control_snapshot)
        else:
            fixed = {
                **fixed,
                "reason": "the fixed-LvProp positive control was not observed accepted",
                "status": "no_outcome",
            }
            null = {
                "reason": "the fixed-LvProp positive control was not observed accepted",
                "status": "no_outcome",
            }
        questions = {
            "fixed_candidate": fixed,
            "null_candidate": null,
        }
    answered = (
        questions["fixed_candidate"]["status"] == "observed_accepted"
        and questions["null_candidate"]["status"]
        in ("observed_accepted", "not_observed_accepted")
    )
    summaries = [
        {
            "error": replica["error"],
            "images": [
                {
                    "completed": image["endpoints"]["completed"],
                    "database": image["database"],
                    "endpoint_status": image["endpoints"]["status"],
                    "metadata_repaired": image["metadata_repaired"],
                    "role": image["role"],
                    "sha256_after": image["sha256_after"],
                    "sha256_before": image["sha256_before"],
                }
                for image in replica["images"]
            ],
            "replica": replica["replica"],
            "status": replica["status"],
        }
        for replica in replicas
    ]
    return {
        "compatibility_claim": False,
        "development_only": True,
        "document_type": "lvprop_null_report",
        "plan_sha256": document["plan_sha256"],
        "questions": questions,
        "replicas": summaries,
        "status": "accepted" if answered else "no_outcome",
        "support_movement": False,
    }


def evaluate(job_result: Path, expected_plan_sha256: str, output: Path) -> dict[str, Any]:
    expected = digest(expected_plan_sha256, "--expected-plan-sha256")
    document = exact_object(
        load_document(job_result),
        {"document_type", "development_only", "plan_sha256", "run_id", "status", "replicas"},
        "$",
    )
    if document["document_type"] != "dao_lvprop_null_job_result":
        raise AnalysisError("job result document type is invalid")
    if document["development_only"] is not True or document["status"] not in ("pass", "fail"):
        raise AnalysisError("job result status fields are invalid")
    if digest(document["plan_sha256"], "$.plan_sha256") != expected:
        raise AnalysisError("job result plan digest differs from the approved plan")
    if not isinstance(document["run_id"], str) or not SAFE_RUN_ID.fullmatch(document["run_id"]):
        raise AnalysisError("$.run_id is invalid")
    raw_replicas = document["replicas"]
    if not isinstance(raw_replicas, list) or len(raw_replicas) != 3:
        raise AnalysisError("$.replicas must contain exactly three replicas")
    replicas: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_replicas):
        location = f"replicas[{position}]"
        item = exact_object(raw, {"replica", "status", "error", "images"}, location)
        replica = item["replica"]
        if type(replica) is not int or replica != position + 1:
            raise AnalysisError("replicas must be numbered 1 through 3 in order")
        if item["status"] not in ("pass", "fail"):
            raise AnalysisError(f"{location}.status is invalid")
        if item["error"] is not None:
            bounded_text(item["error"], f"{location}.error")
        images = item["images"]
        if not isinstance(images, list) or len(images) > len(ROLES):
            raise AnalysisError(f"{location}.images exceeds the bound")
        if item["status"] == "pass" and (item["error"] is not None or len(images) != len(ROLES)):
            raise AnalysisError(f"{location} passed without every image")
        if item["status"] == "fail" and item["error"] is None:
            raise AnalysisError(f"{location} failure omits its error")
        replicas.append(
            {
                "error": item["error"],
                "images": [
                    read_image(
                        job_result.parent,
                        image,
                        replica,
                        index,
                        item["status"],
                    )
                    for index, image in enumerate(images)
                ],
                "replica": replica,
                "status": item["status"],
            }
        )
    aggregate = "pass" if all(replica["status"] == "pass" for replica in replicas) else "fail"
    if document["status"] != aggregate:
        raise AnalysisError("job result status disagrees with replica statuses")
    referenced = sorted(
        image["database"] for replica in replicas for image in replica["images"]
    )
    retained_paths = sorted(
        (
            path
            for path in job_result.parent.iterdir()
            if path.suffix.casefold() == ".mdb"
        ),
        key=lambda path: path.name,
    )
    for path in retained_paths:
        if not path.is_file() or path.is_symlink():
            raise AnalysisError("retained MDB is not a regular file")
        if path.stat().st_size > 64 * PAGE_BYTES:
            raise AnalysisError("retained MDB exceeds the byte bound")
    retained = [path.name for path in retained_paths]
    allowed = {
        DATABASE_NAMES[role].format(replica=replica)
        for replica in range(1, 4)
        for role in ROLES
    }
    failed_replica_names = {
        DATABASE_NAMES[role].format(replica=replica["replica"])
        for replica in replicas
        if replica["status"] == "fail"
        for role in ROLES
    }
    referenced_set = set(referenced)
    retained_set = set(retained)
    if (
        len(referenced_set) != len(referenced)
        or len(retained_set) != len(retained)
        or not referenced_set <= retained_set
        or not retained_set <= allowed
        or not retained_set - referenced_set <= failed_replica_names
    ):
        raise AnalysisError("retained MDB inventory differs from the job result")
    report = build_report(document, replicas)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_bytes(report))
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("job_result", type=Path)
    result.add_argument("--expected-plan-sha256", required=True)
    result.add_argument("--output", required=True, type=Path)
    return result


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    try:
        evaluate(args.job_result, args.expected_plan_sha256, args.output)
    except (AnalysisError, OSError) as error:
        print(f"LvProp null validation rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
