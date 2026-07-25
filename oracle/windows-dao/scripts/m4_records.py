#!/usr/bin/env python3
"""Checked document and relational validation for the bounded DAO M4 campaign."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Protocol

from m1_bundle_validation import bounded_file_identity
from m4_spec import compile_checked_plan
from protocol_validation import ProtocolSchemaSet, ValidationError

HERE = Path(__file__).resolve().parent
DAO_ROOT = HERE.parent
SCHEMA_DIR = DAO_ROOT / "experiments" / "m4"
CHECKED_PLAN = SCHEMA_DIR / "m4-header-discriminator.plan.json"
PROTOCOL_VERSION = "1.0.0"
EXPERIMENT_ID = "DAO-M4-HEADER-DISCRIMINATOR-001"

SCHEMAS = {
    "dao_m4_plan": "plan.schema.json",
    "dao_m4_invocation": "invocation.schema.json",
    "dao_m4_operation_log": "operation-log.schema.json",
    "dao_m4_empty_schema_version_snapshot": "snapshot.schema.json",
    "dao_m4_worker_result": "worker-result.schema.json",
    "dao_m4_clone_log": "clone-log.schema.json",
    "dao_m4_sample_record": "sample-record.schema.json",
    "dao_m4_analysis_report": "analysis-report.schema.json",
    "dao_m4_bundle_manifest": "bundle-manifest.schema.json",
}
SCHEMA_SET = ProtocolSchemaSet(SCHEMA_DIR, SCHEMAS)

MAX_GENERIC_JSON_BYTES = 16 * 1024 * 1024
CREATOR_ACTIONS = [
    "bindings_verified",
    "com_activated",
    "database_created",
    "version_read",
    "empty_schema_read",
    "database_closed",
    "ldb_absence_verified",
    "prefix_observed",
]
REOPEN_ACTIONS = [
    "bindings_verified",
    "clone_verified",
    "com_activated",
    "database_opened",
    "version_read",
    "empty_schema_read",
    "database_closed",
    "ldb_absence_verified",
    "prefix_observed",
]


def require_equal(actual: Any, expected: Any, location: str) -> None:
    """Require an exact controlled projection."""
    if actual != expected:
        raise ValidationError(f"{location}: does not match the checked projection")


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def parse_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    """Parse retained JSON bytes with the protocol's strict settings."""
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{label}: UTF-8 byte-order marks are forbidden")

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    try:
        decoded = payload.decode("utf-8")
        document = json.loads(
            decoded,
            object_pairs_hook=_reject_pairs,
            parse_constant=reject_nonfinite,
        )
    except ValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValidationError(f"{label}: cannot load strict JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"{label}: JSON document must be an object")
    return document


def load_bounded_json(
    path: Path, maximum_bytes: int = MAX_GENERIC_JSON_BYTES
) -> tuple[dict[str, Any], int, str]:
    """Read one stable regular UTF-8 JSON file with strict parser settings."""
    size, digest, retained = bounded_file_identity(path, maximum_bytes, retain=True)
    assert retained is not None
    document = parse_json_bytes(retained, str(path))
    return document, size, digest


def load_document(
    path: Path, maximum_bytes: int, expected_type: str
) -> tuple[dict[str, Any], int, str]:
    document, size, digest = load_bounded_json(path, maximum_bytes)
    observed = SCHEMA_SET.validate(document)
    if observed != expected_type:
        raise ValidationError(
            f"{path}: expected document_type {expected_type!r}, got {observed!r}"
        )
    return document, size, digest


class ArtifactSource(Protocol):
    """Immutable source for already-retained bundle artifacts."""

    root: Path

    def load_document(
        self, locator: str, maximum_bytes: int, expected_type: str
    ) -> tuple[dict[str, Any], int, str]: ...

    def file_identity(self, locator: str) -> tuple[int, str]: ...


def load_artifact_document(
    bundle_root: Path,
    locator: str,
    maximum_bytes: int,
    expected_type: str,
    source: ArtifactSource | None = None,
) -> tuple[dict[str, Any], int, str]:
    if source is not None:
        return source.load_document(locator, maximum_bytes, expected_type)
    return load_document(
        resolve_bundle_path(bundle_root, locator), maximum_bytes, expected_type
    )


def parse_timestamp(value: str, location: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{location}: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{location}: timestamp must include an offset")
    return parsed.astimezone(dt.timezone.utc)


def resolve_bundle_path(root: Path, locator: str) -> Path:
    """Resolve a campaign-relative locator without accepting traversal or links."""
    relative = Path(locator)
    if relative.is_absolute() or not relative.parts:
        raise ValidationError(f"{locator!r}: expected a bundle-relative path")
    if any(part in ("", ".", "..") for part in relative.parts):
        raise ValidationError(f"{locator!r}: unsafe path component")
    root_resolved = root.resolve(strict=True)
    candidate = root_resolved.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise ValidationError(f"{locator!r}: cannot resolve bundle path: {exc}") from exc
    if resolved == root_resolved or root_resolved not in resolved.parents:
        raise ValidationError(f"{locator!r}: path escapes the bundle")
    current = root_resolved
    for part in relative.parts:
        current /= part
        if current.exists() and current.is_symlink():
            raise ValidationError(f"{locator!r}: symlinks are forbidden")
    return resolved


def _validate_lexical_windows_root(
    value: str, location: str
) -> tuple[str, ...]:
    """Validate one local Windows root without normalizing aliases."""
    if "\x00" in value:
        raise ValidationError(f"{location}: NUL is forbidden")
    if (
        not value
        or value.startswith(("\\\\", "//"))
        or "/" in value
        or len(value) < 3
        or value[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        or value[1:3] != ":\\"
    ):
        raise ValidationError(
            f"{location}: noncanonical Windows path; expected a lexical "
            "drive-rooted Windows path, "
            "not UNC or device syntax"
        )
    if ":" in value[2:]:
        raise ValidationError(f"{location}: alternate data streams are forbidden")

    relative = value[3:]
    if not relative:
        return (value[:3].casefold(),)
    components = relative.split("\\")
    reserved = {"con", "prn", "aux", "nul"}
    for component in components:
        stem = component.split(".", 1)[0].casefold()
        if (
            not component
            or component in (".", "..")
            or component.endswith((" ", "."))
            or stem in reserved
            or (
                len(stem) == 4
                and stem[:3] in ("com", "lpt")
                and stem[3] in "123456789"
            )
        ):
            raise ValidationError(
                f"{location}: noncanonical Windows path component"
            )
    return (value[:3].casefold(), *(part.casefold() for part in components))


def _absolute_path_parts(value: str, location: str) -> tuple[str, tuple[str, ...]]:
    if "\x00" in value:
        raise ValidationError(f"{location}: NUL is forbidden")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if windows.is_absolute():
        return "windows", _validate_lexical_windows_root(value, location)
    if posix.is_absolute():
        if any(part in ("", ".", "..") for part in value.split("/")[1:]):
            raise ValidationError(f"{location}: noncanonical path component")
        if str(posix) != value:
            raise ValidationError(f"{location}: POSIX path is not canonical")
        return "posix", posix.parts
    raise ValidationError(f"{location}: expected a canonical absolute path")


def _is_within(parts: tuple[str, ...], parent: tuple[str, ...]) -> bool:
    return len(parts) >= len(parent) and parts[: len(parent)] == parent


def _validate_live_windows_roots(
    invocation: dict[str, Any], bundle_root: Path
) -> None:
    if os.name != "nt":
        for key in ("repository_root", "stage_root"):
            _validate_lexical_windows_root(invocation[key], f"$.{key}")
        raise ValidationError("live invocation validation requires Windows")

    def checked_live_root(path: Path, location: str) -> Path:
        _validate_lexical_windows_root(str(path), location)
        lexical = Path(os.path.abspath(path))
        current = Path(lexical.anchor)
        try:
            for part in lexical.parts[1:]:
                current /= part
                metadata = current.lstat()
                if current.is_symlink() or (
                    getattr(metadata, "st_file_attributes", 0) & 0x400
                ):
                    raise ValidationError(
                        f"{location}: live root contains a reparse point"
                    )
            resolved = lexical.resolve(strict=True)
        except ValidationError:
            raise
        except OSError as exc:
            raise ValidationError(
                f"{location}: cannot resolve live root: {exc}"
            ) from exc
        if not resolved.is_dir():
            raise ValidationError(f"{location}: live root is not a directory")
        return resolved

    bundle_resolved = checked_live_root(bundle_root, "--bundle-root")
    resolved_roots: dict[str, Path] = {}
    for key in ("repository_root", "stage_root"):
        path = Path(invocation[key])
        resolved_roots[key] = checked_live_root(path, f"$.{key}")
    if resolved_roots["stage_root"] != bundle_resolved:
        raise ValidationError("$.stage_root: does not match --bundle-root")
    repository = resolved_roots["repository_root"]
    stage = resolved_roots["stage_root"]
    if (
        stage == repository
        or repository in stage.parents
        or stage in repository.parents
    ):
        raise ValidationError("$.stage_root: must not overlap repository_root")


def _creator_contract(condition: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "creator",
        "method": plan["design"]["creation_method"],
        "locale": plan["design"]["locale"],
        "version_option": condition["version_option"],
        "version_api_value": condition["version_api_value"],
        "encryption_option": condition["encryption_option"],
        "encryption_api_value": condition["encryption_api_value"],
        "create_option_value": condition["create_option_value"],
        "compact_database_used": False,
        "expected_dao_version": condition["expected_dao_version"],
    }


def _creation_projection(condition: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    contract = _creator_contract(condition, plan)
    contract.pop("kind")
    contract.pop("locale")
    contract.pop("expected_dao_version")
    return contract


def validate_plan_document(plan: dict[str, Any]) -> None:
    """Validate and compile the exact relational M4 plan contract."""
    SCHEMA_SET.validate(plan)
    compile_checked_plan(plan)


def load_checked_plan(path: Path = CHECKED_PLAN) -> tuple[dict[str, Any], str]:
    """Load the exact checked plan bytes and validate its relational content."""
    checked_size, checked_hash, checked_bytes = bounded_file_identity(
        CHECKED_PLAN, 1048576, retain=True
    )
    plan, size, digest = load_document(path, 1048576, "dao_m4_plan")
    _, _, supplied_bytes = bounded_file_identity(path, 1048576, retain=True)
    if size != checked_size or digest != checked_hash or supplied_bytes != checked_bytes:
        raise ValidationError(f"{path}: bytes differ from the checked M4 plan")
    validate_plan_document(plan)
    return plan, digest


def plan_indexes(
    plan: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    return (
        {row["sample_id"]: row for row in plan["samples"]},
        {row["condition_id"]: row for row in plan["conditions"]},
    )


def validate_invocation_document(
    invocation: dict[str, Any],
    plan: dict[str, Any],
    plan_sha256: str,
    bundle_root: Path,
    *,
    expected_path: str | None = None,
    preflight: bool = False,
    source: ArtifactSource | None = None,
) -> dict[str, Any]:
    """Bind an invocation to the exact plan sample and safe bundle locators."""
    SCHEMA_SET.validate(invocation)
    if preflight and os.name == "nt":
        _validate_lexical_windows_root(str(bundle_root), "--bundle-root")
    samples, conditions = plan_indexes(plan)
    sample = samples.get(invocation["sample_id"])
    if sample is None:
        raise ValidationError("$.sample_id: not present in the checked plan")
    condition = conditions[sample["condition_id"]]
    phase = invocation["phase_id"]
    phase_ordinal = 1 if phase == "creator" else 2
    worker_ordinal = 2 * sample["launch_ordinal"] - (1 if phase == "creator" else 0)
    expected_worker = f"{sample['sample_id']}-{phase.upper()}"
    require_equal(invocation["condition_id"], sample["condition_id"], "$.condition_id")
    require_equal(invocation["phase_ordinal"], phase_ordinal, "$.phase_ordinal")
    require_equal(invocation["worker_ordinal"], worker_ordinal, "$.worker_ordinal")
    require_equal(invocation["worker_run_id"], expected_worker, "$.worker_run_id")
    require_equal(invocation["plan_sha256"], plan_sha256, "$.plan_sha256")
    require_equal(invocation["repository_url"], plan["repository_url"], "$.repository_url")
    require_equal(invocation["remote_ref"], plan["remote_ref"], "$.remote_ref")
    expected_db = sample[f"{phase}_database_path"]
    require_equal(invocation["database_path"], expected_db, "$.database_path")
    if expected_path is not None:
        require_equal(invocation["result_path"], expected_path, "$.result_path")
    for key in (
        "plan_path",
        "environment_path",
        "database_path",
        "result_path",
    ):
        if source is None:
            resolve_bundle_path(bundle_root, invocation[key])
        else:
            source.file_identity(invocation[key])
    root_paths = {
        key: _absolute_path_parts(invocation[key], f"$.{key}")
        for key in ("repository_root", "stage_root")
    }
    flavors = {value[0] for value in root_paths.values()}
    if len(flavors) != 1:
        raise ValidationError("absolute roots use different path flavors")
    repository_parts = root_paths["repository_root"][1]
    stage_parts = root_paths["stage_root"][1]
    if _is_within(stage_parts, repository_parts) or _is_within(repository_parts, stage_parts):
        raise ValidationError("$.stage_root: must not overlap repository_root")
    required = ("plan_path", "environment_path")
    for key in required:
        if source is None and not resolve_bundle_path(
            bundle_root, invocation[key]
        ).is_file():
            raise ValidationError(f"$.{key}: required binding file does not exist")
    if source is None:
        retained_plan, retained_plan_hash = load_checked_plan(
            resolve_bundle_path(bundle_root, invocation["plan_path"])
        )
    else:
        retained_plan, _, retained_plan_hash = source.load_document(
            invocation["plan_path"],
            plan["bounds"]["max_plan_bytes"],
            "dao_m4_plan",
        )
        validate_plan_document(retained_plan)
    require_equal(retained_plan, plan, "$.plan_path document")
    require_equal(retained_plan_hash, invocation["plan_sha256"], "$.plan_path sha256")
    if source is None:
        _, retained_environment_hash, _ = bounded_file_identity(
            resolve_bundle_path(bundle_root, invocation["environment_path"]),
            1024 * 1024,
            retain=False,
        )
    else:
        _, retained_environment_hash = source.file_identity(
            invocation["environment_path"]
        )
    require_equal(
        retained_environment_hash,
        invocation["environment_sha256"],
        "$.environment_path sha256",
    )
    if preflight:
        _validate_live_windows_roots(invocation, bundle_root)
        database = resolve_bundle_path(bundle_root, invocation["database_path"])
        result = resolve_bundle_path(bundle_root, invocation["result_path"])
        if result.exists():
            raise ValidationError("$.result_path: worker result must not preexist")
        if phase == "creator" and database.exists():
            raise ValidationError("$.database_path: creator database must not preexist")
        if phase == "reopen" and not database.is_file():
            raise ValidationError("$.database_path: reopen clone must already exist")
    if phase == "creator":
        require_equal(
            invocation["phase_contract"], _creator_contract(condition, plan),
            "$.phase_contract",
        )
    else:
        contract = invocation["phase_contract"]
        require_equal(contract["kind"], "reopen", "$.phase_contract.kind")
        require_equal(
            contract["expected_dao_version"],
            condition["expected_dao_version"],
            "$.phase_contract.expected_dao_version",
        )
        clone, _, clone_hash = load_artifact_document(
            bundle_root,
            contract["clone_log"]["path"],
            65536,
            "dao_m4_clone_log",
            source,
        )
        require_equal(clone_hash, contract["clone_log"]["sha256"], "$.phase_contract.clone_log.sha256")
        require_equal(clone["sample_id"], sample["sample_id"], "$.phase_contract.clone_log.sample_id")
        require_equal(clone["destination_path"], invocation["database_path"], "$.phase_contract.clone_log.destination_path")
        require_equal(clone["destination_bytes"], contract["pre_com_database_bytes"], "$.phase_contract.pre_com_database_bytes")
        require_equal(clone["destination_sha256"], contract["pre_com_database_sha256"], "$.phase_contract.pre_com_database_sha256")
        if parse_timestamp(clone["completed_at_utc"], "$.clone_log.completed_at_utc") > parse_timestamp(
            invocation["created_at_utc"], "$.created_at_utc"
        ):
            raise ValidationError("$.phase_contract.clone_log: clone completed after invocation creation")
        if preflight:
            database_size, database_hash, _ = bounded_file_identity(
                resolve_bundle_path(bundle_root, invocation["database_path"]),
                plan["bounds"]["max_database_bytes"],
                retain=False,
            )
            require_equal(database_size, contract["pre_com_database_bytes"], "$.phase_contract.pre_com_database_bytes")
            require_equal(database_hash, contract["pre_com_database_sha256"], "$.phase_contract.pre_com_database_sha256")
    return sample
