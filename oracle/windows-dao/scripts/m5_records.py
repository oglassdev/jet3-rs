#!/usr/bin/env python3
"""Strict document loading and invocation bindings for DAO M5R6."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from m1_bundle_validation import bounded_file_identity
from m5_spec import (
    DATABASE_ROLES,
    EXPERIMENT_ID,
    M4_MANIFEST_SHA256,
    M4_PRODUCER_COMMIT,
    M4_RUN_ID,
    PHASES,
    PLAN_SHA256,
    REMOTE_REF,
    compile_checked_plan,
    require_equal,
)
from protocol_validation import ProtocolSchemaSet, ValidationError

HERE = Path(__file__).resolve().parent
DAO_ROOT = HERE.parent
SCHEMA_DIR = DAO_ROOT / "experiments" / "m5r5"
CHECKED_PLAN = DAO_ROOT / "experiments" / "m5" / "m5-compact-confirm-r6.plan.json"

SCHEMAS = {
    "dao_m5_plan": "plan.schema.json",
    "dao_m5_invocation": "invocation.schema.json",
    "dao_m5_operation_log": "operation-log.schema.json",
    "dao_m5_snapshot": "snapshot.schema.json",
    "dao_m5_worker_result": "worker-result.schema.json",
    "dao_m5_post_worker_quiescence": "post-worker-quiescence.schema.json",
    "dao_m5_clone_log": "clone-log.schema.json",
    "dao_m5_sample_record": "sample-record.schema.json",
    "dao_m5_analysis_report": "analysis-report.schema.json",
    "dao_m5_bundle_manifest": "bundle-manifest.schema.json",
}
SCHEMA_SET = ProtocolSchemaSet(SCHEMA_DIR, SCHEMAS)
MAX_JSON_BYTES = 16 * 1024 * 1024


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def parse_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{label}: UTF-8 byte-order marks are forbidden")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {token}")
            ),
        )
    except ValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValidationError(f"{label}: cannot load strict JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{label}: JSON document must be an object")
    return value


def load_bounded_json(path: Path, maximum_bytes: int = MAX_JSON_BYTES) -> tuple[dict[str, Any], int, str]:
    size, digest, retained = bounded_file_identity(path, maximum_bytes, retain=True)
    assert retained is not None
    return parse_json_bytes(retained, str(path)), size, digest


def load_document(path: Path, maximum_bytes: int, expected_type: str) -> tuple[dict[str, Any], int, str]:
    document, size, digest = load_bounded_json(path, maximum_bytes)
    observed = SCHEMA_SET.validate(document)
    if observed != expected_type:
        raise ValidationError(f"{path}: expected {expected_type!r}, got {observed!r}")
    return document, size, digest


def load_checked_plan(path: Path = CHECKED_PLAN) -> tuple[dict[str, Any], str]:
    checked_size, checked_hash, checked_bytes = bounded_file_identity(CHECKED_PLAN, 1048576, retain=True)
    if checked_hash != PLAN_SHA256:
        raise ValidationError("checked M5R6 plan hash differs from the compiled contract")
    document, size, digest = load_document(path, 1048576, "dao_m5_plan")
    _, _, supplied = bounded_file_identity(path, 1048576, retain=True)
    if size != checked_size or digest != checked_hash or supplied != checked_bytes:
        raise ValidationError(f"{path}: bytes differ from the immutable checked M5R6 plan")
    compile_checked_plan(document)
    return document, digest


def reject_alias_components(path: Path, label: str) -> Path:
    """Return an absolute lexical path after rejecting every existing alias component."""
    from m4r1_snapshot import _is_reparse
    lexical = Path(os.path.abspath(path))
    current = Path(lexical.anchor)
    try:
        for part in lexical.parts[1:]:
            current /= part
            metadata = current.lstat()
            if current.is_symlink() or _is_reparse(metadata):
                raise ValidationError(f"{label}: aliases and reparses are forbidden")
    except FileNotFoundError:
        pass
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"{label}: cannot inspect path components: {exc}") from exc
    return lexical


def resolve_bundle_path(root: Path, locator: str) -> Path:
    relative = PurePosixPath(locator)
    if relative.is_absolute() or not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise ValidationError(f"{locator!r}: unsafe bundle-relative path")
    from m4r1_snapshot import _is_reparse
    lexical_root = reject_alias_components(root, "bundle root")
    try:
        root_metadata = lexical_root.lstat()
    except OSError as exc:
        raise ValidationError(f"{root}: cannot inspect bundle root: {exc}") from exc
    if lexical_root.is_symlink() or _is_reparse(root_metadata):
        raise ValidationError(f"{root}: bundle root aliases and reparses are forbidden")
    root_resolved = lexical_root.resolve(strict=True)
    candidate = root_resolved.joinpath(*relative.parts)
    current = root_resolved
    for part in relative.parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValidationError(f"{locator!r}: cannot inspect path component: {exc}") from exc
        if current.is_symlink() or _is_reparse(metadata):
            raise ValidationError(f"{locator!r}: aliases and reparses are forbidden")
    resolved = candidate.resolve(strict=False)
    if resolved == root_resolved or root_resolved not in resolved.parents:
        raise ValidationError(f"{locator!r}: path escapes the bundle")
    return resolved


def parse_timestamp(value: str, location: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValidationError(f"{location}: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{location}: timestamp must include an offset")
    return parsed.astimezone(dt.timezone.utc)


def _absolute_path(value: str, location: str) -> tuple[str, tuple[str, ...]]:
    if "\x00" in value:
        raise ValidationError(f"{location}: NUL is forbidden")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if windows.is_absolute():
        if value.startswith(("\\\\", "//")) or "/" in value or ":" in value[2:]:
            raise ValidationError(f"{location}: noncanonical Windows path")
        parts = tuple(part.casefold() for part in windows.parts)
        if any(part in ("", ".", "..") for part in windows.parts):
            raise ValidationError(f"{location}: noncanonical Windows component")
        return "windows", parts
    if posix.is_absolute() and str(posix) == value and ".." not in posix.parts:
        return "posix", posix.parts
    raise ValidationError(f"{location}: canonical absolute path required")


def _phase_contract(condition: dict[str, Any], phase: str) -> dict[str, Any]:
    if phase == "source":
        return {
            "kind": "source",
            "method": "DBEngine.CreateDatabase",
            "locale": ";LANGID=0x0409;CP=1252;COUNTRY=0",
            "version_option": condition["source_version_option"],
            "version_api_value": condition["source_version_api_value"],
            "encryption_option": condition["source_encryption_option"],
            "encryption_api_value": condition["source_encryption_api_value"],
            "create_option_value": condition["source_create_option_value"],
            "expected_dao_version": condition["expected_source_dao_version"],
        }
    if phase == "compact":
        return {
            "kind": "compact",
            "method": "DBEngine.CompactDatabase",
            "destination_locale_argument": "omitted",
            "password_argument": "omitted",
            "destination_version_option": condition["destination_version_option"],
            "destination_version_api_value": condition["destination_version_api_value"],
            "encryption_option": condition["compact_encryption_option"],
            "encryption_api_value": condition["compact_encryption_api_value"],
            "compact_option_value": condition["compact_option_value"],
            "expected_dao_version": condition["expected_destination_dao_version"],
        }
    return {
        "kind": "verify",
        "method": "DBEngine.OpenDatabase",
        "mutation_requested": False,
        "expected_dao_version": condition["expected_destination_dao_version"],
    }


def validate_invocation_document(
    invocation: dict[str, Any],
    plan: dict[str, Any],
    plan_sha256: str,
    bundle_root: Path,
    *,
    expected_result_path: str | None = None,
    preflight: bool = False,
) -> dict[str, Any]:
    SCHEMA_SET.validate(invocation)
    checked = compile_checked_plan(plan)
    sample = checked.samples_by_id.get(invocation["sample_id"])
    if sample is None:
        raise ValidationError("$.sample_id: absent from checked plan")
    condition = checked.conditions_by_id[sample["condition_id"]]
    phase = invocation["phase_id"]
    phase_ordinal = PHASES.index(phase) + 1
    worker_ordinal = 3 * sample["launch_ordinal"] - (2 - PHASES.index(phase))
    for actual, expected, location in (
        (invocation["condition_id"], sample["condition_id"], "$.condition_id"),
        (invocation["phase_ordinal"], phase_ordinal, "$.phase_ordinal"),
        (invocation["worker_ordinal"], worker_ordinal, "$.worker_ordinal"),
        (invocation["worker_run_id"], f"{sample['sample_id']}-{phase.upper()}", "$.worker_run_id"),
        (invocation["plan_sha256"], plan_sha256, "$.plan_sha256"),
        (invocation["repository_url"], plan["repository_url"], "$.repository_url"),
        (invocation["remote_ref"], REMOTE_REF, "$.remote_ref"),
        (invocation["phase_contract"], _phase_contract(condition, phase), "$.phase_contract"),
        (invocation["m4_input"], {
            "bundle_manifest_sha256": M4_MANIFEST_SHA256,
            "producer_commit": M4_PRODUCER_COMMIT,
            "campaign_run_id": M4_RUN_ID,
            "validated_before_com": True,
        }, "$.m4_input"),
    ):
        require_equal(actual, expected, location)
    expected_databases = {
        role: sample[f"{role[:-9]}_database_path"] if role != "compacted_database" else sample["compacted_database_path"]
        for role in ({"source": DATABASE_ROLES[:1], "compact": DATABASE_ROLES[1:3], "verify": DATABASE_ROLES[3:]}[phase])
    }
    require_equal(invocation["database_paths"], expected_databases, "$.database_paths")
    if expected_result_path is not None:
        require_equal(invocation["result_path"], expected_result_path, "$.result_path")
    for key in ("plan_path", "environment_path", "result_path"):
        resolve_bundle_path(bundle_root, invocation[key])
    for locator in invocation["database_paths"].values():
        resolve_bundle_path(bundle_root, locator)
    roots = {key: _absolute_path(invocation[key], f"$.{key}") for key in ("repository_root", "stage_root")}
    if roots["repository_root"][0] != roots["stage_root"][0]:
        raise ValidationError("absolute roots use different path flavors")
    repository_parts = roots["repository_root"][1]
    stage_parts = roots["stage_root"][1]
    if repository_parts == stage_parts or repository_parts == stage_parts[: len(repository_parts)] or stage_parts == repository_parts[: len(stage_parts)]:
        raise ValidationError("$.stage_root: must not overlap repository_root")
    retained_plan, retained_hash = load_checked_plan(resolve_bundle_path(bundle_root, invocation["plan_path"]))
    require_equal(retained_plan, plan, "$.plan_path document")
    require_equal(retained_hash, invocation["plan_sha256"], "$.plan_path sha256")
    _, environment_hash, _ = bounded_file_identity(resolve_bundle_path(bundle_root, invocation["environment_path"]), 1048576, retain=False)
    require_equal(environment_hash, invocation["environment_sha256"], "$.environment_path sha256")
    parse_timestamp(invocation["created_at_utc"], "$.created_at_utc")
    if preflight:
        if os.name != "nt":
            raise ValidationError("live invocation validation requires Windows")
        if Path(invocation["stage_root"]).resolve(strict=True) != bundle_root.resolve(strict=True):
            raise ValidationError("$.stage_root: does not match --bundle-root")
        result = resolve_bundle_path(bundle_root, invocation["result_path"])
        if result.exists():
            raise ValidationError("$.result_path: must not preexist")
    return sample
