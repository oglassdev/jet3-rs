#!/usr/bin/env python3
"""Validate the fixed bootstrap key and LvProp semantics experiment."""

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
ENTRY_AREA_OFFSET = 248
ENTRY_AREA_LENGTH = PAGE_BYTES - ENTRY_AREA_OFFSET
MAX_JSON_BYTES = 4 * 1024 * 1024
CHECKPOINTS = ("empty", "alpha")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_MDB = re.compile(
    r"^bootstrap-composer-semantics-r[1-3]-(empty|alpha)[.]mdb$"
)
SAFE_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")


class AnalysisError(ValueError):
    """The producer result or retained artifact failed an integrity check."""


class DecodeError(ValueError):
    """A bounded checkpoint did not decode under the pinned hypotheses."""


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


def digest(value: Any, location: str) -> str:
    if not isinstance(value, str) or not HEX_64.fullmatch(value):
        raise AnalysisError(f"{location} must be a lowercase SHA-256 digest")
    return value


def exact_object(value: Any, keys: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AnalysisError(f"{location} must contain exactly {sorted(keys)}")
    return value


def bounded_text(value: Any, location: str) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise AnalysisError(f"{location} must be a bounded string")
    return value


def load_document(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AnalysisError("job result must be a regular file")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise AnalysisError("job result exceeds the JSON bound")
    try:
        value = json.loads(raw, object_pairs_hook=object_without_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("job result is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise AnalysisError("job result root must be an object")
    return value


def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def validate_dao(value: Any, location: str, checkpoint: str) -> dict[str, Any]:
    try:
        dao = catalog._dao(value, location)
    except catalog.AnalysisError as error:
        raise AnalysisError(str(error)) from error
    alpha = [entry for entry in dao["tabledefs"] if entry["name"] == "Alpha"]
    expected = 1 if checkpoint == "alpha" else 0
    if len(alpha) != expected:
        raise AnalysisError(
            f"{location}.tabledefs contains {len(alpha)} Alpha entries; expected {expected}"
        )
    return dao


def read_checkpoint(root: Path, value: Any, replica: int, name: str) -> tuple[bytes, bool]:
    item = exact_object(
        value,
        {"name", "database", "size", "sha256", "sha256_after_metadata", "dao"},
        f"replica {replica} checkpoint {name}",
    )
    if item["name"] != name:
        raise AnalysisError(f"replica {replica} checkpoint order is invalid")
    database = item["database"]
    expected_name = f"bootstrap-composer-semantics-r{replica}-{name}.mdb"
    if database != expected_name or not SAFE_MDB.fullmatch(database):
        raise AnalysisError(f"replica {replica} checkpoint filename is invalid")
    size = item["size"]
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size < PAGE_BYTES
        or size > 64 * PAGE_BYTES
        or size % PAGE_BYTES
    ):
        raise AnalysisError(f"replica {replica} checkpoint size is invalid")
    before = digest(item["sha256"], f"replica {replica} checkpoint digest")
    after = digest(
        item["sha256_after_metadata"],
        f"replica {replica} post-metadata digest",
    )
    validate_dao(item["dao"], f"replica {replica} checkpoint {name}.dao", name)
    path = root / database
    if not path.is_file() or path.is_symlink():
        raise AnalysisError(f"replica {replica} checkpoint is missing or not regular")
    raw = path.read_bytes()
    repaired = before != after
    retained_digest = after if repaired else before
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != retained_digest:
        raise AnalysisError(f"replica {replica} checkpoint bytes differ from metadata")
    if name == "empty" and size != 20 * PAGE_BYTES:
        raise AnalysisError(f"replica {replica} empty checkpoint is not 20 pages")
    if name == "alpha" and size != 23 * PAGE_BYTES:
        raise AnalysisError(f"replica {replica} Alpha checkpoint is not 23 pages")
    return raw, repaired


def index_boundaries(page: bytes) -> list[int]:
    boundaries: list[int] = []
    for byte_index, value in enumerate(page[22:ENTRY_AREA_OFFSET]):
        for bit in range(8):
            if value & (1 << bit):
                boundary = byte_index * 8 + bit
                if boundary > ENTRY_AREA_LENGTH:
                    raise DecodeError("page 9 has a boundary outside its entry area")
                boundaries.append(boundary)
    return boundaries


def parent_name_root(definition: dict[str, Any]) -> int:
    matches = [
        entry for entry in definition["logical_indexes"] if entry["name"] == "ParentIdName"
    ]
    if len(matches) != 1:
        raise DecodeError("MSysObjects does not have exactly one ParentIdName index")
    physical_index = matches[0]["physical_index"]
    physical = definition["physical_indexes"]
    if type(physical_index) is not int or not 0 <= physical_index < len(physical):
        raise DecodeError("ParentIdName does not name an in-bounds physical index")
    entry = physical[physical_index]
    columns = definition["columns"]
    try:
        key_names = [columns[key["column"]]["name"] for key in entry["keys"]]
    except (IndexError, KeyError, TypeError) as error:
        raise DecodeError("ParentIdName has malformed key-column linkage") from error
    if key_names != ["ParentId", "Name"]:
        raise DecodeError(f"ParentIdName names unexpected key columns {key_names}")
    if type(entry["root"]) is not int:
        raise DecodeError("ParentIdName root is not an integer")
    return entry["root"]


def parent_name_keys(
    data: bytes, rows: list[dict[str, Any]], root: int = 9
) -> list[dict[str, Any]]:
    if root != 9:
        raise DecodeError(f"ParentIdName root is page {root}, not fixed page 9")
    page = data[root * PAGE_BYTES : (root + 1) * PAGE_BYTES]
    if len(page) != PAGE_BYTES:
        raise DecodeError("page 9 lies outside the database image")
    if page[0] != 4 or page[1] != 1 or int.from_bytes(page[4:8], "little") != 2:
        raise DecodeError("page 9 is not the MSysObjects leaf root")
    if any(page[offset : offset + 4] != b"\0\0\0\0" for offset in (8, 12, 16)):
        raise DecodeError("page 9 has an unexpected sibling or child reference")
    if page[21] != 0:
        raise DecodeError("page 9 is not a leaf")
    area = page[ENTRY_AREA_OFFSET:]
    prefix_length = page[20]
    boundaries = index_boundaries(page)
    prior = prefix_length
    expected_free = ENTRY_AREA_LENGTH - (boundaries[-1] if boundaries else 0)
    if int.from_bytes(page[2:4], "little") != expected_free:
        raise DecodeError("page 9 free space disagrees with its boundary bitmap")
    row_by_locator = {(row["page"], row["row"]): row for row in rows}
    result: list[dict[str, Any]] = []
    for ordinal, boundary in enumerate(boundaries):
        if boundary <= prior:
            raise DecodeError("page 9 has a reversed or repeated boundary")
        suffix = area[prior:boundary]
        if len(suffix) <= 4:
            raise DecodeError(f"page 9 entry {ordinal} is too short")
        trailer = suffix[-4:]
        row_page = int.from_bytes(trailer[:3], "big")
        row_slot = trailer[3]
        row = row_by_locator.get((row_page, row_slot))
        if row is None:
            raise DecodeError(f"page 9 entry {ordinal} has no catalog row")
        values = row["values"]
        if (
            len(values) < 3
            or type(values[0]) is not int
            or type(values[1]) is not int
            or not isinstance(values[2], str)
        ):
            raise DecodeError(f"page 9 entry {ordinal} has malformed catalog identity fields")
        result.append(
            {
                "id": values[0],
                "key_hex": (area[:prefix_length] + suffix[:-4]).hex(),
                "name": values[2],
                "parent_id": values[1],
                "row_page": row_page,
                "row_slot": row_slot,
            }
        )
        prior = boundary
    if len(result) != len(rows):
        raise DecodeError("page 9 does not contain exactly one entry per catalog row")
    if len({(entry["row_page"], entry["row_slot"]) for entry in result}) != len(result):
        raise DecodeError("page 9 repeats a catalog row locator")
    return result


def alpha_lvprop(
    data: bytes, definition: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    name_ordinal = catalog._ordinal(definition, "Name")
    lvprop_ordinal = catalog._ordinal(definition, "LvProp")
    if name_ordinal is None or lvprop_ordinal is None:
        raise DecodeError("catalog lacks a Name or LvProp column")
    required = max(name_ordinal, lvprop_ordinal)
    if any(len(row.get("values", [])) <= required for row in rows):
        raise DecodeError("catalog row is too short for Name and LvProp")
    matches = [row for row in rows if row["values"][name_ordinal] == "Alpha"]
    if len(matches) != 1:
        raise DecodeError(f"catalog contains {len(matches)} Alpha rows")
    value = matches[0]["values"][lvprop_ordinal]
    if not isinstance(value, dict) or set(value) != {
        "inline_length",
        "long_value_header_hex",
    }:
        raise DecodeError("Alpha.LvProp is not one external header")
    try:
        header = bytes.fromhex(value["long_value_header_hex"])
    except (TypeError, ValueError) as error:
        raise DecodeError("Alpha.LvProp header is not hex") from error
    if len(header) != 12 or value["inline_length"] != 12:
        raise DecodeError("Alpha.LvProp header is not 12 bytes")
    if header[8:12] != b"\0\0\0\0":
        raise DecodeError("Alpha.LvProp reserved header bytes are nonzero")
    control = int.from_bytes(header[:4], "little")
    if control & 0xFF000000 != 0x40000000:
        raise DecodeError("Alpha.LvProp is not the observed single-page external form")
    length = control & 0x00FFFFFF
    row_slot = header[4]
    page_number = int.from_bytes(header[5:8], "little")
    if length == 0 or length > PAGE_BYTES or page_number >= len(data) // PAGE_BYTES:
        raise DecodeError("Alpha.LvProp external reference is outside the bound")
    page = data[page_number * PAGE_BYTES : (page_number + 1) * PAGE_BYTES]
    if page[0] != 1 or page[4:8] != b"LVAL":
        raise DecodeError("Alpha.LvProp does not target an LVAL data page")
    directory = catalog._row_directory(page, page_number)
    if row_slot >= len(directory):
        raise DecodeError("Alpha.LvProp row slot is absent")
    row = directory[row_slot]
    if row["hidden"] or row["overflow"]:
        raise DecodeError("Alpha.LvProp targets a flagged row")
    payload = page[row["start"] : row["end"]]
    if len(payload) != length:
        raise DecodeError("Alpha.LvProp payload length disagrees with its header")
    return {
        "header_hex": header.hex(),
        "length": length,
        "page": page_number,
        "payload_hex": payload.hex(),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "row": row_slot,
    }


def analyze_replica(empty: bytes, alpha: bytes) -> dict[str, Any]:
    empty_definition, _, empty_rows = catalog._discover_catalog(empty)
    alpha_definition, _, alpha_rows = catalog._discover_catalog(alpha)
    empty_parent_name_root = parent_name_root(empty_definition)
    alpha_parent_name_root = parent_name_root(alpha_definition)
    differences = [index for index in range(PAGE_BYTES) if empty[index] != alpha[index]]
    return {
        "alpha_keys": parent_name_keys(alpha, alpha_rows, alpha_parent_name_root),
        "empty_keys": parent_name_keys(empty, empty_rows, empty_parent_name_root),
        "lvprop": alpha_lvprop(alpha, alpha_definition, alpha_rows),
        "page0": {
            "alpha": alpha[1538],
            "changed_offsets": differences,
            "empty": empty[1538],
        },
    }


def build_report(document: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    question_names = ("fixed_parent_name_keys", "fixed_alpha_lvprop", "fixed_page0")
    ready = (
        document["status"] == "pass"
        and len(observations) == 3
        and all(
            all(name in observation for name in ("empty_keys", "alpha_keys", "lvprop", "page0"))
            for observation in observations
        )
    )
    if not ready:
        if any(observation.get("metadata_open_repaired") for observation in observations):
            reason = "DAO metadata access changed at least one checkpoint"
        elif any("decode_error" in observation for observation in observations):
            reason = "at least one checkpoint did not decode under the pinned hypotheses"
        else:
            reason = "at least one replica did not complete"
        questions = {
            name: {"reason": reason, "status": "no_outcome"}
            for name in question_names
        }
        status = "no_outcome"
    else:
        keys_equal = all(
            observation["empty_keys"] == observations[0]["empty_keys"]
            and observation["alpha_keys"] == observations[0]["alpha_keys"]
            for observation in observations[1:]
        )
        lvprop_equal = all(
            observation["lvprop"] == observations[0]["lvprop"]
            for observation in observations[1:]
        )
        page0_equal = all(
            observation["page0"] == observations[0]["page0"]
            for observation in observations[1:]
        )
        questions = {
            "fixed_parent_name_keys": (
                {
                    "alpha": observations[0]["alpha_keys"],
                    "empty": observations[0]["empty_keys"],
                    "status": "answered",
                }
                if keys_equal
                else {"reason": "replicas disagree on raw keys", "status": "no_outcome"}
            ),
            "fixed_alpha_lvprop": (
                {**observations[0]["lvprop"], "status": "answered"}
                if lvprop_equal
                else {"reason": "replicas disagree on Alpha.LvProp", "status": "no_outcome"}
            ),
            "fixed_page0": (
                {**observations[0]["page0"], "status": "answered"}
                if page0_equal
                else {"reason": "replicas disagree on page zero", "status": "no_outcome"}
            ),
        }
        status = (
            "accepted"
            if all(question["status"] == "answered" for question in questions.values())
            else "no_outcome"
        )
    return {
        "compatibility_claim": False,
        "development_only": True,
        "document_type": "bootstrap_composer_semantics_report",
        "plan_sha256": document["plan_sha256"],
        "questions": questions,
        "replicas": observations,
        "status": status,
        "support_movement": False,
    }


def evaluate(job_result: Path, expected_plan_sha256: str, output: Path) -> dict[str, Any]:
    expected = digest(expected_plan_sha256, "--expected-plan-sha256")
    document = exact_object(
        load_document(job_result),
        {"document_type", "development_only", "plan_sha256", "run_id", "status", "replicas"},
        "$",
    )
    if document["document_type"] != "dao_bootstrap_composer_semantics_job_result":
        raise AnalysisError("job result document type is invalid")
    if document["development_only"] is not True or document["status"] not in ("pass", "fail"):
        raise AnalysisError("job result status fields are invalid")
    if digest(document["plan_sha256"], "$.plan_sha256") != expected:
        raise AnalysisError("job result plan digest differs from the approved plan")
    if not isinstance(document["run_id"], str) or not SAFE_RUN_ID.fullmatch(document["run_id"]):
        raise AnalysisError("$.run_id is invalid")
    replicas = document["replicas"]
    if not isinstance(replicas, list) or len(replicas) != 3:
        raise AnalysisError("$.replicas must contain exactly three replicas")
    observations: list[dict[str, Any]] = []
    for position, raw in enumerate(replicas):
        item = exact_object(raw, {"replica", "status", "error", "checkpoints"}, f"replicas[{position}]")
        replica = item["replica"]
        if type(replica) is not int or replica != position + 1:
            raise AnalysisError("replicas must be numbered 1 through 3 in order")
        if item["status"] not in ("pass", "fail"):
            raise AnalysisError(f"replica {replica} status is invalid")
        if item["error"] is not None:
            bounded_text(item["error"], f"replica {replica} error")
        checkpoints = item["checkpoints"]
        if not isinstance(checkpoints, list) or len(checkpoints) > len(CHECKPOINTS):
            raise AnalysisError(f"replica {replica} checkpoints are invalid")
        images: dict[str, bytes] = {}
        repaired: list[str] = []
        for index, value in enumerate(checkpoints):
            name = CHECKPOINTS[index]
            image, metadata_open_repaired = read_checkpoint(
                job_result.parent,
                value,
                replica,
                name,
            )
            images[name] = image
            if metadata_open_repaired:
                repaired.append(name)
        observation: dict[str, Any] = {
            "error": item["error"],
            "replica": replica,
            "status": item["status"],
        }
        if item["status"] == "pass":
            if tuple(images) != CHECKPOINTS or item["error"] is not None:
                raise AnalysisError(f"replica {replica} pass inventory is incomplete")
            if repaired:
                observation["metadata_open_repaired"] = repaired
            else:
                try:
                    observation.update(analyze_replica(images["empty"], images["alpha"]))
                except (catalog.DecodeError, DecodeError) as error:
                    observation["decode_error"] = str(error)
        elif item["error"] is None:
            raise AnalysisError(f"replica {replica} failure omits its error")
        observations.append(observation)
    aggregate_status = (
        "pass" if all(observation["status"] == "pass" for observation in observations) else "fail"
    )
    if document["status"] != aggregate_status:
        raise AnalysisError("job result status disagrees with replica statuses")
    report = build_report(document, observations)
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
        print(f"bootstrap composer semantics analysis failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
