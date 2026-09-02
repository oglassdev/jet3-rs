#!/usr/bin/env python3
"""Validate the bounded CP1252 extended catalog-name experiment for issue #152."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import schema_generalization as schema


DOCUMENT_TYPE = "dao_extended_names_job_result"
REPORT_TYPE = "extended_names_report"
PAGE_BYTES = 2048
UNDEFINED_SLOTS = (0x81, 0x8D, 0x8F, 0x90, 0x9D)
DEFINED_BYTES = tuple(value for value in range(0x80, 0x100) if value not in UNDEFINED_SLOTS)
BATCHES = tuple(tuple(DEFINED_BYTES[offset : offset + 3]) for offset in range(0, len(DEFINED_BYTES), 3))
CHECKPOINT_NAMES = ("empty", *(f"b{index:02d}" for index in range(len(BATCHES))), "reject")
REJECTION_POINTS = (0x7F, *UNDEFINED_SLOTS)
PLAN_DIGEST = re.compile(r"[0-9a-f]{64}")
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}")
MAXIMUM_JSON_BYTES = 8 * 1024 * 1024
MAXIMUM_PAGES = 128
MAXIMUM_TABLES = 32
MAXIMUM_FIELDS = 32
MAXIMUM_INDEXES = 16
MAXIMUM_DETAIL = 512
ASCII_WEIGHTS = {
    **{byte: 0x56 + byte - ord("0") for byte in range(ord("0"), ord("9") + 1)},
    ord("A"): 0x60, ord("B"): 0x61, ord("C"): 0x62,
    ord("D"): 0x64, ord("E"): 0x66, ord("F"): 0x67,
    ord("J"): 0x6B,
    ord("L"): 0x6D, ord("M"): 0x6F, ord("N"): 0x70,
    ord("R"): 0x75, ord("T"): 0x77, ord("V"): 0x7A, ord("Z"): 0x7E,
}


class ValidationError(ValueError):
    pass


def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def digest(value: str, what: str) -> str:
    if not isinstance(value, str) or not PLAN_DIGEST.fullmatch(value):
        raise ValidationError(f"{what} is not a lowercase SHA-256 digest")
    return value


def names_for(point: int) -> list[dict[str, Any]]:
    if point not in DEFINED_BYTES:
        raise ValidationError("extended byte is not defined in CP1252 above 0x7E")
    tag = f"{point:02X}"
    current = bytes([point]).decode("cp1252")
    position = DEFINED_BYTES.index(point)
    neighbor_point = DEFINED_BYTES[(position + 1) % len(DEFINED_BYTES)]
    neighbor = bytes([neighbor_point]).decode("cp1252")
    return [
        {"role": "single_left", "name": f"N{tag}L{current}AZ", "inserted": [point], "prefix": f"N{tag}L", "suffix": "AZ"},
        {"role": "single_middle", "name": f"N{tag}MA{current}Z", "inserted": [point], "prefix": f"N{tag}MA", "suffix": "Z"},
        {"role": "single_right", "name": f"N{tag}RAZ{current}", "inserted": [point], "prefix": f"N{tag}RAZ", "suffix": ""},
        {"role": "repeat", "name": f"N{tag}DA{current}{current}Z", "inserted": [point, point], "prefix": f"N{tag}DA", "suffix": "Z"},
        {"role": "forward", "name": f"N{tag}FA{current}{neighbor}Z", "inserted": [point, neighbor_point], "prefix": f"N{tag}FA", "suffix": "Z"},
        {"role": "reverse", "name": f"N{tag}VA{neighbor}{current}Z", "inserted": [neighbor_point, point], "prefix": f"N{tag}VA", "suffix": "Z"},
    ]


def batch_specs(index: int) -> list[dict[str, Any]]:
    specs = [{"role": "ascii_control", "name": f"C{index:02d}B", "inserted": [], "prefix": f"C{index:02d}B", "suffix": ""}]
    for point in BATCHES[index]:
        specs.extend(names_for(point))
    return specs


def rejection_specs() -> list[dict[str, Any]]:
    specs = [{"role": "ascii_control", "name": "CREJECTB", "inserted": [], "prefix": "CREJECTB", "suffix": ""}]
    for point in REJECTION_POINTS:
        specs.append({
            "role": "boundary_7f" if point == 0x7F else f"undefined_{point:02X}",
            "name": f"R{point:02X}A{chr(point)}Z",
            "inserted": [],
            "prefix": f"R{point:02X}A",
            "suffix": "Z",
        })
    return specs


def expected_database(replica: int, name: str) -> str:
    return f"extended-names-r{replica}-{name}.mdb"


def exact_keys(value: dict[str, Any], expected: set[str], what: str) -> None:
    if set(value) != expected:
        raise ValidationError(f"{what} has an unexpected field inventory")


def load_document(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValidationError("job result must be a regular non-link file")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ValidationError("job result is unreadable") from error
    if len(raw) > MAXIMUM_JSON_BYTES:
        raise ValidationError("job result exceeds the 8-MiB bound")
    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValidationError("job result is malformed") from error
    if not isinstance(value, dict):
        raise ValidationError("job result is not an object")
    exact_keys(value, {"document_type", "development_only", "plan_sha256", "run_id", "status", "replicas"}, "job result")
    if value["document_type"] != DOCUMENT_TYPE or value["development_only"] is not True:
        raise ValidationError("job result identity is invalid")
    digest(value["plan_sha256"], "job plan digest")
    if not isinstance(value["run_id"], str) or not RUN_ID.fullmatch(value["run_id"]):
        raise ValidationError("job run ID is invalid")
    if value["status"] not in ("pass", "fail") or not isinstance(value["replicas"], list):
        raise ValidationError("job status or replicas are invalid")
    return value


def read_image(root: Path, filename: str, size: Any, sha256: Any) -> bytes:
    if not isinstance(filename, str) or not re.fullmatch(r"extended-names-r[1-3]-(?:empty|b(?:0[0-9]|[1-3][0-9]|40)|reject)\.mdb", filename):
        raise ValidationError("checkpoint database name is invalid")
    path = root / filename
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"checkpoint {filename} is not a regular file")
    data = path.read_bytes()
    if type(size) is not int or size != len(data) or size < PAGE_BYTES or size % PAGE_BYTES or size > MAXIMUM_PAGES * PAGE_BYTES:
        raise ValidationError(f"checkpoint {filename} violates its size or page bound")
    if not isinstance(sha256, str) or hashlib.sha256(data).hexdigest() != sha256:
        raise ValidationError(f"checkpoint {filename} differs from its digest")
    return data


def validate_attempts(name: str, attempts: Any) -> list[dict[str, Any]]:
    expected = rejection_specs() if name == "reject" else batch_specs(int(name[1:]))
    if not isinstance(attempts, list) or len(attempts) != len(expected):
        raise ValidationError(f"checkpoint {name} attempt inventory is invalid")
    result = []
    for observed, wanted in zip(attempts, expected, strict=True):
        if not isinstance(observed, dict):
            raise ValidationError("attempt is not an object")
        exact_keys(observed, {"role", "name", "name_utf16le_hex", "inserted_bytes", "created", "failure_operation", "error"}, "attempt")
        if not isinstance(observed["name"], str):
            raise ValidationError("attempt name is not text")
        name_hex = observed["name"].encode("utf-16le").hex()
        if observed["role"] != wanted["role"] or observed["name"] != wanted["name"] or observed["name_utf16le_hex"] != name_hex or observed["inserted_bytes"] != wanted["inserted"]:
            raise ValidationError(f"checkpoint {name} attempt differs from the generated inventory")
        if type(observed["created"]) is not bool or (observed["error"] is not None and not isinstance(observed["error"], str)):
            raise ValidationError("attempt outcome is malformed")
        if observed["error"] is not None and len(observed["error"]) > MAXIMUM_DETAIL:
            raise ValidationError("attempt error exceeds its bound")
        if observed["created"]:
            if observed["error"] is not None or observed["failure_operation"] is not None:
                raise ValidationError("successful attempt carries failure detail")
        elif observed["error"] is None or observed["failure_operation"] not in ("create_tabledef", "tabledefs_append"):
            raise ValidationError("rejected attempt lacks an exact name-bearing DAO operation")
        result.append(observed)
    if not result[0]["created"]:
        raise ValidationError(f"checkpoint {name} ASCII control was rejected")
    return result


def user_tables(dao: Any) -> set[str]:
    if not isinstance(dao, dict) or set(dao) != {"tabledefs"} or not isinstance(dao["tabledefs"], list):
        raise ValidationError("DAO metadata is malformed")
    if len(dao["tabledefs"]) > MAXIMUM_TABLES:
        raise ValidationError("DAO table metadata exceeds its bound")
    names: list[str] = []
    semantic_error = None
    for position, entry in enumerate(dao["tabledefs"]):
        if not isinstance(entry, dict) or set(entry) != {"ordinal", "name", "fields", "indexes"}:
            raise ValidationError("DAO table metadata is malformed")
        if type(entry["ordinal"]) is not int or entry["ordinal"] != position or not isinstance(entry["name"], str) or len(entry["name"]) > 128:
            raise ValidationError("DAO table identity is malformed")
        if not isinstance(entry["fields"], list) or len(entry["fields"]) > MAXIMUM_FIELDS:
            raise ValidationError("DAO field metadata violates its bound")
        if not isinstance(entry["indexes"], list) or len(entry["indexes"]) > MAXIMUM_INDEXES:
            raise ValidationError("DAO index metadata violates its bound")
        for ordinal, field in enumerate(entry["fields"]):
            if not isinstance(field, dict) or set(field) != {"ordinal", "name", "type", "size"}:
                raise ValidationError("DAO field metadata is malformed")
            if type(field["ordinal"]) is not int or field["ordinal"] != ordinal or not isinstance(field["name"], str) or len(field["name"]) > 128 or type(field["type"]) is not int or type(field["size"]) is not int or not 0 <= field["type"] <= 65535 or not 0 <= field["size"] <= 1 << 20:
                raise ValidationError("DAO field metadata value is invalid")
        for ordinal, index in enumerate(entry["indexes"]):
            if not isinstance(index, dict) or set(index) != {"ordinal", "name", "primary", "unique"}:
                raise ValidationError("DAO index metadata is malformed")
            if type(index["ordinal"]) is not int or index["ordinal"] != ordinal or not isinstance(index["name"], str) or len(index["name"]) > 128 or type(index["primary"]) is not bool or type(index["unique"]) is not bool:
                raise ValidationError("DAO index metadata value is invalid")
        if not entry["name"].startswith("MSys"):
            if entry["fields"] != [{"ordinal": 0, "name": "Id", "type": 4, "size": 4}] or entry["indexes"] != []:
                semantic_error = "DAO user-table schema differs from the probe"
            names.append(entry["name"])
    if len(names) != len(set(names)):
        semantic_error = "DAO metadata repeats a user table"
    if semantic_error is not None:
        raise schema.DecodeError(semantic_error)
    return set(names)


def ascii_primary(value: str) -> bytes:
    try:
        return bytes(ASCII_WEIGHTS[byte] for byte in value.encode("cp1252"))
    except KeyError as error:
        raise schema.DecodeError("generated context uses an unmapped EXP-0087 ASCII byte") from error


def isolate_primary(primary: bytes, prefix: str, suffix: str, what: str) -> bytes:
    left = ascii_primary(prefix)
    right = ascii_primary(suffix)
    if not primary.startswith(left) or (right and not primary.endswith(right)):
        raise schema.DecodeError(f"{what} primary section does not retain its ASCII context")
    end = len(primary) - len(right) if right else len(primary)
    if end < len(left):
        raise schema.DecodeError(f"{what} primary context overlaps")
    return primary[len(left):end]


def analyze_arm(data: bytes, name: str, attempts: list[dict[str, Any]], dao: Any) -> dict[str, Any]:
    expected = rejection_specs() if name == "reject" else batch_specs(int(name[1:]))
    wanted_names = {entry["name"] for entry in expected}
    created = {entry["name"] for entry in attempts if entry["created"]}
    if user_tables(dao) != created:
        raise schema.DecodeError(f"checkpoint {name} DAO inventory disagrees with attempts")
    keys = schema.catalog_name_keys(data)
    matching = [entry for entry in keys if entry["name"] in wanted_names]
    by_name = {entry["name"]: entry for entry in matching}
    if len(matching) != len(by_name):
        raise schema.DecodeError(f"checkpoint {name} repeats a catalog key name")
    if set(by_name) != created:
        raise schema.DecodeError(f"checkpoint {name} catalog inventory disagrees with attempts")
    forms = []
    for spec, attempt in zip(expected, attempts, strict=True):
        row: dict[str, Any] = {
            "role": spec["role"], "name": spec["name"],
            "name_utf16le_hex": attempt["name_utf16le_hex"], "inserted_bytes": spec["inserted"],
            "created": attempt["created"], "failure_operation": attempt["failure_operation"],
            "error": attempt["error"],
        }
        if attempt["created"]:
            key = by_name[spec["name"]]
            primary = bytes.fromhex(key["primary_hex"])
            contribution = isolate_primary(primary, spec["prefix"], spec["suffix"], spec["name"])
            row.update({
                "id": key["id"], "key_hex": key["key_hex"], "parent_id": key["parent_id"],
                "primary_hex": key["primary_hex"],
                "secondary_nibbles": key["secondary_nibbles"],
                "row_page": key["row_page"], "row_slot": key["row_slot"],
            })
            if name != "reject" or spec["role"] == "ascii_control":
                row["isolated_primary_hex"] = contribution.hex()
            if spec["role"] == "ascii_control" and (contribution or key["secondary_nibbles"]):
                raise schema.DecodeError(f"checkpoint {name} ASCII control does not match EXP-0087 weights")
        forms.append(row)
    return {"checkpoint": name, "bytes": list(BATCHES[int(name[1:])]) if name != "reject" else [], "forms": forms}


def validate_replica(root: Path, value: Any, expected_replica: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("replica is not an object")
    exact_keys(value, {"replica", "status", "error", "mutation_started", "phase", "checkpoints", "recovery"}, "replica")
    if type(value["replica"]) is not int or value["replica"] != expected_replica or value["status"] not in ("pass", "fail") or type(value["mutation_started"]) is not bool or not isinstance(value["phase"], str):
        raise ValidationError("replica state is malformed")
    if value["error"] is not None and (not isinstance(value["error"], str) or len(value["error"]) > MAXIMUM_DETAIL):
        raise ValidationError("replica error is malformed")
    if not isinstance(value["checkpoints"], list) or not isinstance(value["recovery"], list) or len(value["recovery"]) > 1:
        raise ValidationError("replica artifact inventory is malformed")
    images: dict[str, bytes] = {}
    attempts_by_name: dict[str, list[dict[str, Any]]] = {}
    dao_by_name: dict[str, Any] = {}
    files = []
    metadata_changed = False
    semantic_errors: list[str] = []
    empty_identity: dict[str, Any] | None = None
    for index, checkpoint in enumerate(value["checkpoints"]):
        if index >= len(CHECKPOINT_NAMES) or not isinstance(checkpoint, dict):
            raise ValidationError("replica exceeds the checkpoint bound")
        name = CHECKPOINT_NAMES[index]
        exact_keys(checkpoint, {"name", "database", "size", "sha256", "size_after_metadata", "sha256_after_metadata", "arm_before", "attempts", "dao"}, "checkpoint")
        filename = expected_database(expected_replica, name)
        if checkpoint["name"] != name or checkpoint["database"] != filename:
            raise ValidationError("replica checkpoints are not an ordered prefix")
        original_size = checkpoint["size"]
        if type(original_size) is not int or original_size < PAGE_BYTES or original_size % PAGE_BYTES or original_size > MAXIMUM_PAGES * PAGE_BYTES:
            raise ValidationError("checkpoint pre-metadata size violates its bound")
        original_sha = digest(checkpoint["sha256"], "checkpoint pre-metadata digest")
        data = read_image(root, filename, checkpoint["size_after_metadata"], checkpoint["sha256_after_metadata"])
        metadata_changed |= original_size != len(data) or original_sha != checkpoint["sha256_after_metadata"]
        if name == "empty":
            if checkpoint["arm_before"] is not None or checkpoint["attempts"] != []:
                raise ValidationError("empty checkpoint is not empty")
            empty_identity = {"size": original_size, "sha256": original_sha}
            attempts = []
        else:
            before = checkpoint["arm_before"]
            if not isinstance(before, dict) or set(before) != {"size", "sha256"}:
                raise ValidationError("arm baseline identity is malformed")
            if before != empty_identity:
                raise ValidationError("arm baseline differs from the retained empty image")
            attempts = validate_attempts(name, checkpoint["attempts"])
        try:
            observed_users = user_tables(checkpoint["dao"])
            expected_users = {attempt["name"] for attempt in attempts if attempt["created"]}
            if observed_users != expected_users:
                semantic_errors.append(f"checkpoint {name} DAO inventory disagrees with attempts")
        except schema.DecodeError as error:
            semantic_errors.append(f"checkpoint {name}: {error}")
        images[name] = data
        attempts_by_name[name] = attempts
        dao_by_name[name] = checkpoint["dao"]
        files.append({k: checkpoint[k] for k in ("name", "database", "size", "sha256", "size_after_metadata", "sha256_after_metadata")})
    complete = len(value["checkpoints"]) == len(CHECKPOINT_NAMES)
    if not value["mutation_started"] and (value["checkpoints"] or value["recovery"]):
        raise ValidationError("pre-mutation replica retained an impossible artifact")
    if not value["mutation_started"] and value["phase"] not in ("before_create_database", "create_database"):
        raise ValidationError("pre-mutation replica phase is inconsistent")
    if value["status"] == "pass" and (value["phase"] != "complete" or value["error"] is not None):
        raise ValidationError("passing replica state is inconsistent")
    if value["status"] == "fail" and value["error"] is None:
        raise ValidationError("failed replica omits its bounded error")
    phase_next_name = None
    if value["status"] == "fail":
        batch_phase = re.fullmatch(r"append_b(\d{2})", value["phase"])
        cleanup_batch_phase = re.fullmatch(r"cleanup_b(\d{2})", value["phase"])
        if batch_phase:
            phase_index = int(batch_phase.group(1))
            if phase_index >= len(BATCHES) or len(value["checkpoints"]) != phase_index + 1:
                raise ValidationError("failed phase disagrees with checkpoint prefix")
            phase_next_name = f"b{phase_index:02d}"
        elif cleanup_batch_phase:
            phase_index = int(cleanup_batch_phase.group(1))
            if phase_index >= len(BATCHES) or len(value["checkpoints"]) != phase_index + 2:
                raise ValidationError("failed cleanup phase disagrees with checkpoint prefix")
        elif value["phase"] == "append_reject":
            if len(value["checkpoints"]) != 1 + len(BATCHES):
                raise ValidationError("failed phase disagrees with checkpoint prefix")
            phase_next_name = "reject"
        elif value["phase"] in ("cleanup_reject", "cleanup_complete"):
            if not complete:
                raise ValidationError("failed cleanup phase disagrees with checkpoint prefix")
        elif value["phase"] in ("before_create_database", "create_database", "capture_empty"):
            if value["checkpoints"]:
                raise ValidationError("failed phase disagrees with checkpoint prefix")
            if value["phase"] == "capture_empty":
                phase_next_name = "empty"
        else:
            raise ValidationError("failed phase is outside the bounded state machine")
        if phase_next_name is not None and not value["mutation_started"]:
            raise ValidationError("failed phase and mutation state disagree")
        if value["phase"] == "before_create_database" and value["mutation_started"]:
            raise ValidationError("failed phase and mutation state disagree")
        if value["phase"] == "capture_empty" and not value["mutation_started"]:
            raise ValidationError("failed phase and mutation state disagree")
    if value["status"] == "pass" and (not complete or value["recovery"]):
        raise ValidationError("passing replica has an incomplete inventory")
    if value["status"] == "fail" and complete and value["phase"] not in ("cleanup_reject", "cleanup_complete"):
        raise ValidationError("failed replica has a complete inventory")
    if complete and value["recovery"]:
        raise ValidationError("complete replica cannot retain a recovery artifact")
    if value["recovery"]:
        recovery = value["recovery"][0]
        expected_name = CHECKPOINT_NAMES[len(value["checkpoints"])]
        exact_keys(recovery, {"name", "database", "size", "sha256"}, "recovery")
        if recovery["name"] != expected_name or recovery["database"] != expected_database(expected_replica, expected_name):
            raise ValidationError("recovery is not the next checkpoint")
        if phase_next_name != expected_name:
            raise ValidationError("recovery does not match the failed phase")
        read_image(root, recovery["database"], recovery["size"], recovery["sha256"])
        files.append(recovery)
    result = {k: value[k] for k in ("replica", "status", "error", "mutation_started", "phase")}
    result["files"] = files
    result["metadata_changed"] = metadata_changed
    result["attempts"] = {
        name: attempts_by_name[name] for name in CHECKPOINT_NAMES[: len(value["checkpoints"])]
    }
    observations = []
    for name in CHECKPOINT_NAMES[1 : len(value["checkpoints"])]:
        try:
            observations.append(analyze_arm(images[name], name, attempts_by_name[name], dao_by_name[name]))
        except (ValueError, schema.DecodeError) as error:
            semantic_errors.append(f"checkpoint {name}: {error}")
    if observations and not complete:
        result["partial_observation"] = observations
    if semantic_errors:
        result["decode_error"] = "; ".join(semantic_errors)
    elif complete and not metadata_changed:
        result["observation"] = observations
    return result


def summarize(observation: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    all_forms = [form for arm in observation if arm["checkpoint"] != "reject" for form in arm["forms"]]
    by_byte: dict[str, Any] = {}
    for point in DEFINED_BYTES:
        tag = f"N{point:02X}"
        forms = {entry["role"]: entry for entry in all_forms if entry["name"].startswith(tag)}
        singles = [forms[name] for name in ("single_left", "single_middle", "single_right")]
        created = [name for name, entry in forms.items() if entry["created"]]
        rejected = [name for name, entry in forms.items() if not entry["created"]]
        summary: dict[str, Any] = {"created_forms": created, "rejected_forms": rejected, "forms": list(forms.values())}
        if all(entry["created"] for entry in singles):
            primary = [entry["isolated_primary_hex"] for entry in singles]
            secondary = [entry["secondary_nibbles"] for entry in singles]
            summary["singleton_primary_position_independent"] = len(set(primary)) == 1
            summary["singleton_secondary_position_independent"] = all(value == secondary[0] for value in secondary[1:])
        else:
            summary["singleton_primary_position_independent"] = None
            summary["singleton_secondary_position_independent"] = None
        single = forms["single_middle"]
        repeat = forms["repeat"]
        if single["created"] and repeat["created"]:
            summary["repeat_primary_is_two_singletons"] = repeat["isolated_primary_hex"] == single["isolated_primary_hex"] * 2
            summary["repeat_secondary_is_two_singletons"] = repeat["secondary_nibbles"] == single["secondary_nibbles"] * 2
        else:
            summary["repeat_primary_is_two_singletons"] = None
            summary["repeat_secondary_is_two_singletons"] = None
        neighbor = DEFINED_BYTES[(DEFINED_BYTES.index(point) + 1) % len(DEFINED_BYTES)]
        neighbor_single = next(entry for entry in all_forms if entry["role"] == "single_middle" and entry["inserted_bytes"] == [neighbor])
        for role, order in (("forward", (single, neighbor_single)), ("reverse", (neighbor_single, single))):
            pair = forms[role]
            if pair["created"] and all(entry["created"] for entry in order):
                summary[f"{role}_primary_is_ordered_singletons"] = pair["isolated_primary_hex"] == "".join(entry["isolated_primary_hex"] for entry in order)
                summary[f"{role}_secondary_is_ordered_singletons"] = pair["secondary_nibbles"] == sum((entry["secondary_nibbles"] for entry in order), [])
            else:
                summary[f"{role}_primary_is_ordered_singletons"] = None
                summary[f"{role}_secondary_is_ordered_singletons"] = None
        by_byte[f"{point:02x}"] = summary
    rejection_arm = next(arm for arm in observation if arm["checkpoint"] == "reject")
    controls = {entry["role"]: entry for entry in rejection_arm["forms"]}
    return by_byte, controls


def build_report(document: dict[str, Any], replicas: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [entry for entry in replicas if "observation" in entry]
    question_names = ("coverage", "singleton_positions", "pair_composition", "secondary_order", "replication")
    if document["status"] != "pass" or len(complete) != 3:
        reason = "at least one replica did not yield a complete decoded observation"
        if any(entry.get("metadata_changed") for entry in replicas):
            reason = "DAO metadata access changed at least one checkpoint"
        elif any(entry.get("decode_error") for entry in replicas):
            reason = "at least one retained checkpoint failed a recorded grammar or control"
        questions = {name: {"status": "no_outcome", "reason": reason} for name in question_names}
    elif any(entry["observation"] != complete[0]["observation"] for entry in complete[1:]):
        reason = "replicas disagree on the exact decoded observations"
        questions = {name: {"status": "no_outcome", "reason": reason} for name in question_names}
    else:
        summary, controls = summarize(complete[0]["observation"])
        questions = {
            "coverage": {"status": "answered", "bytes": {key: {k: value[k] for k in ("created_forms", "rejected_forms")} for key, value in summary.items()}, "rejection_controls": controls},
            "singleton_positions": {"status": "answered", "bytes": {key: {k: value[k] for k in ("singleton_primary_position_independent", "singleton_secondary_position_independent")} for key, value in summary.items()}},
            "pair_composition": {"status": "answered", "bytes": {key: {k: value[k] for k in ("repeat_primary_is_two_singletons", "repeat_secondary_is_two_singletons")} for key, value in summary.items()}},
            "secondary_order": {"status": "answered", "bytes": summary},
            "replication": {"status": "answered", "replicas": 3},
        }
    return {
        "compatibility_claim": False, "development_only": True,
        "document_type": REPORT_TYPE, "plan_sha256": document["plan_sha256"],
        "questions": questions, "replicas": [{k: v for k, v in entry.items() if k != "observation"} for entry in replicas],
        "status": "accepted" if all(value["status"] == "answered" for value in questions.values()) else "no_outcome",
        "support_movement": False,
    }


def evaluate(job_result: Path, expected_plan_sha256: str, output: Path) -> dict[str, Any]:
    expected = digest(expected_plan_sha256, "--expected-plan-sha256")
    document = load_document(job_result)
    if document["plan_sha256"] != expected:
        raise ValidationError("job result plan digest differs from the expected plan")
    values = document["replicas"]
    pre_mutation_abort = len(values) == 1 and values[0].get("status") == "fail" and values[0].get("mutation_started") is False
    if len(values) != 3 and not pre_mutation_abort:
        raise ValidationError("job result has an incomplete replica inventory")
    if pre_mutation_abort:
        raise ValidationError("job stopped before the first DAO mutation")
    replicas = [validate_replica(job_result.parent, value, index) for index, value in enumerate(values, 1)]
    if document["status"] == "pass" and any(entry["status"] != "pass" for entry in replicas):
        raise ValidationError("job and replica statuses disagree")
    if document["status"] == "fail" and all(entry["status"] == "pass" for entry in replicas):
        raise ValidationError("failed job has no failed replica")
    referenced = [file["database"] for replica in replicas for file in replica["files"]]
    if len(referenced) != len({name.casefold() for name in referenced}):
        raise ValidationError("retained MDB inventory repeats a database")
    actual = []
    for path in job_result.parent.iterdir():
        if path.name.lower().endswith(".mdb"):
            if path.is_symlink() or not path.is_file():
                raise ValidationError("retained MDB inventory contains a non-regular file")
            actual.append(path.name)
    if len(actual) != len({name.casefold() for name in actual}):
        raise ValidationError("retained MDB inventory repeats a case-folded database")
    if {name.casefold() for name in actual} != {name.casefold() for name in referenced}:
        raise ValidationError("retained MDB inventory differs from the job result")
    report = build_report(document, replicas)
    output.write_bytes(canonical_bytes(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_result", type=Path)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        evaluate(args.job_result, args.expected_plan_sha256, args.output)
    except (OSError, ValidationError, schema.DecodeError) as error:
        print(f"INVALID: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
