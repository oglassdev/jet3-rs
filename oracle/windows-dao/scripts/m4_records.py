#!/usr/bin/env python3
"""Checked document and relational validation for the bounded DAO M4 campaign."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from m1_bundle_validation import bounded_file_identity
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


def load_bounded_json(
    path: Path, maximum_bytes: int = MAX_GENERIC_JSON_BYTES
) -> tuple[dict[str, Any], int, str]:
    """Read one stable regular UTF-8 JSON file with strict parser settings."""
    size, digest, retained = bounded_file_identity(path, maximum_bytes, retain=True)
    assert retained is not None
    if retained.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{path}: UTF-8 byte-order marks are forbidden")

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    try:
        decoded = retained.decode("utf-8")
        document = json.loads(
            decoded,
            object_pairs_hook=_reject_pairs,
            parse_constant=reject_nonfinite,
        )
    except ValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValidationError(f"{path}: cannot load strict JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"{path}: JSON document must be an object")
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


def _absolute_path_parts(value: str, location: str) -> tuple[str, tuple[str, ...]]:
    if "\x00" in value:
        raise ValidationError(f"{location}: NUL is forbidden")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if windows.is_absolute():
        raw_parts = value.replace("/", "\\").split("\\")
        if any(part in ("", ".", "..") for part in raw_parts[1:]):
            raise ValidationError(f"{location}: noncanonical path component")
        if str(windows) != value:
            raise ValidationError(f"{location}: Windows path is not canonical")
        return "windows", tuple(part.casefold() for part in windows.parts)
    if posix.is_absolute():
        if any(part in ("", ".", "..") for part in value.split("/")[1:]):
            raise ValidationError(f"{location}: noncanonical path component")
        if str(posix) != value:
            raise ValidationError(f"{location}: POSIX path is not canonical")
        return "posix", posix.parts
    raise ValidationError(f"{location}: expected a canonical absolute path")


def _is_within(parts: tuple[str, ...], parent: tuple[str, ...]) -> bool:
    return len(parts) >= len(parent) and parts[: len(parent)] == parent


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


def _checked_condition_rows() -> list[dict[str, Any]]:
    rows = []
    for version, api, label in (("20", 16, "2.0"), ("30", 32, "3.0"), ("40", 64, "4.0")):
        for encrypted, suffix, enc_name, enc_value in (
            (False, "U", "omitted", 0),
            (True, "E", "dbEncrypt", 2),
        ):
            rows.append(
                {
                    "condition_id": f"V{version}-{suffix}",
                    "version_option": f"dbVersion{version}",
                    "version_api_value": api,
                    "encryption_option": enc_name,
                    "encryption_api_value": enc_value,
                    "create_option_value": api + (2 if encrypted else 0),
                    "expected_dao_version": label,
                }
            )
    return rows


def validate_plan_document(plan: dict[str, Any]) -> None:
    """Validate relational schedule, path, and arithmetic contracts."""
    SCHEMA_SET.validate(plan)
    require_equal(plan["conditions"], _checked_condition_rows(), "$.conditions")
    bounds = plan["bounds"]
    require_equal(
        bounds,
        {
            "max_database_bytes": 1048576,
            "max_database_artifacts": 72,
            "max_total_database_bytes": 75497472,
            "max_acquisition_database_reads": 216,
            "max_acquisition_database_read_bytes": 226492416,
            "max_validator_database_reads_per_run": 72,
            "max_validator_database_read_bytes_per_run": 75497472,
            "max_plan_bytes": 1048576,
            "max_sample_record_bytes": 65536,
            "max_analysis_report_bytes": 16777216,
            "prefix_bytes_per_phase": 2048,
            "max_prefix_artifacts": 72,
            "max_total_prefix_bytes": 147456,
            "max_analyzed_offsets": 1536,
            "max_comparisons": 324,
            "max_comparison_byte_visits": 995328,
            "max_candidate_sets": 24,
            "max_worker_processes": 72,
            "worker_timeout_seconds": 120,
        },
        "$.bounds",
    )
    require_equal(
        plan["analysis"]["analyzed_ranges"], [{"start": 0, "end": 1536}],
        "$.analysis.analyzed_ranges",
    )
    require_equal(
        plan["analysis"]["excluded_ranges"],
        [{"start": 1536, "end": 2048, "provenance_id": "SRC-0013"}],
        "$.analysis.excluded_ranges",
    )
    require_equal(
        plan["analysis"]["retained_prefix_range"], {"start": 0, "end": 2048},
        "$.analysis.retained_prefix_range",
    )
    conditions = [row["condition_id"] for row in plan["conditions"]]
    samples = plan["samples"]
    if len(samples) != 36:
        raise ValidationError("$.samples: expected exactly 36 samples")
    ids: set[str] = set()
    paths: set[str] = set()
    ordinals: set[int] = set()
    by_block: dict[int, list[dict[str, Any]]] = {}
    for row in samples:
        condition = row["condition_id"]
        expected_id = f"M4-{condition}-{row['replica']:02d}"
        require_equal(row["sample_id"], expected_id, "$.samples[].sample_id")
        require_equal(row["replica"], row["block"], "$.samples[].replica")
        require_equal(
            row["launch_ordinal"],
            (row["block"] - 1) * 6 + row["position_in_block"],
            "$.samples[].launch_ordinal",
        )
        base = f"evidence/samples/{expected_id}"
        require_equal(row["creator_database_path"], f"{base}/creator.mdb", f"{expected_id}.creator_database_path")
        require_equal(row["reopen_database_path"], f"{base}/reopen.mdb", f"{expected_id}.reopen_database_path")
        require_equal(row["record_path"], f"{base}/record.json", f"{expected_id}.record_path")
        ids.add(expected_id)
        ordinals.add(row["launch_ordinal"])
        for key in ("creator_database_path", "reopen_database_path", "record_path"):
            if row[key] in paths:
                raise ValidationError(f"$.samples: duplicate declared path {row[key]!r}")
            paths.add(row[key])
        by_block.setdefault(row["block"], []).append(row)
    if len(ids) != 36 or ordinals != set(range(1, 37)):
        raise ValidationError("$.samples: IDs or launch ordinals are not complete")
    for block in range(1, 7):
        rows = sorted(by_block.get(block, []), key=lambda item: item["position_in_block"])
        observed = [item["condition_id"] for item in rows]
        expected = conditions[block - 1 :] + conditions[: block - 1]
        require_equal(observed, expected, f"$.samples block {block} cyclic schedule")
    within = 6 * 2 * (6 * 5 // 2)
    version = (3 * 2 // 2) * 2 * 6 * 2
    encryption = 3 * 6 * 2
    comparisons = 36 + within + version + encryption
    require_equal(comparisons, bounds["max_comparisons"], "$.bounds.max_comparisons")
    require_equal(
        comparisons * bounds["max_analyzed_offsets"] * 2,
        bounds["max_comparison_byte_visits"],
        "$.bounds.max_comparison_byte_visits",
    )


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
) -> dict[str, Any]:
    """Bind an invocation to the exact plan sample and safe bundle locators."""
    SCHEMA_SET.validate(invocation)
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
        resolve_bundle_path(bundle_root, invocation[key])
    root_paths = {
        key: _absolute_path_parts(invocation[key], f"$.{key}")
        for key in ("repository_root", "stage_root", "output_root")
    }
    flavors = {value[0] for value in root_paths.values()}
    if len(flavors) != 1:
        raise ValidationError("absolute roots use different path flavors")
    repository_parts = root_paths["repository_root"][1]
    stage_parts = root_paths["stage_root"][1]
    output_parts = root_paths["output_root"][1]
    if _is_within(stage_parts, repository_parts) or _is_within(repository_parts, stage_parts):
        raise ValidationError("$.stage_root: must not overlap repository_root")
    if any(
        (
            _is_within(output_parts, repository_parts),
            _is_within(repository_parts, output_parts),
            _is_within(output_parts, stage_parts),
            _is_within(stage_parts, output_parts),
        )
    ):
        raise ValidationError("$.output_root: must not overlap repository_root or stage_root")
    required = ("plan_path", "environment_path")
    for key in required:
        if not resolve_bundle_path(bundle_root, invocation[key]).is_file():
            raise ValidationError(f"$.{key}: required binding file does not exist")
    retained_plan, retained_plan_hash = load_checked_plan(
        resolve_bundle_path(bundle_root, invocation["plan_path"])
    )
    require_equal(retained_plan, plan, "$.plan_path document")
    require_equal(retained_plan_hash, invocation["plan_sha256"], "$.plan_path sha256")
    _, retained_environment_hash, _ = bounded_file_identity(
        resolve_bundle_path(bundle_root, invocation["environment_path"]),
        1024 * 1024,
        retain=False,
    )
    require_equal(
        retained_environment_hash,
        invocation["environment_sha256"],
        "$.environment_path sha256",
    )
    if preflight:
        flavor = next(iter(flavors))
        if flavor == "posix":
            require_equal(
                Path(invocation["stage_root"]).resolve(strict=True),
                bundle_root.resolve(strict=True),
                "$.stage_root",
            )
            if not Path(invocation["repository_root"]).is_dir():
                raise ValidationError("$.repository_root: source repository does not exist")
            if not Path(invocation["output_root"]).is_dir():
                raise ValidationError("$.output_root: publication root does not exist")
        elif os.name == "nt":
            require_equal(
                Path(invocation["stage_root"]).resolve(strict=True),
                bundle_root.resolve(strict=True),
                "$.stage_root",
            )
            if not Path(invocation["repository_root"]).is_dir():
                raise ValidationError("$.repository_root: source repository does not exist")
            if not Path(invocation["output_root"]).is_dir():
                raise ValidationError("$.output_root: publication root does not exist")
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
        clone_path = resolve_bundle_path(bundle_root, contract["clone_log"]["path"])
        clone, _, clone_hash = load_document(
            clone_path, 65536, "dao_m4_clone_log"
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


def _validate_common_projection(
    document: dict[str, Any],
    sample: dict[str, Any],
    phase: str,
    worker_id: str,
) -> None:
    require_equal(document["sample_id"], sample["sample_id"], "$.sample_id")
    require_equal(document["phase_id"], phase, "$.phase_id")
    require_equal(document["phase_ordinal"], 1 if phase == "creator" else 2, "$.phase_ordinal")
    require_equal(document.get("worker_run_id", worker_id), worker_id, "$.worker_run_id")


def validate_phase_documents(
    bundle_root: Path,
    record: dict[str, Any],
    sample: dict[str, Any],
    condition: dict[str, Any],
    phase: str,
    plan: dict[str, Any],
    plan_sha256: str,
) -> dict[str, Any]:
    """Validate all immutable phase documents and their exact projections."""
    phase_row = record["phases"][phase]
    artifacts = phase_row["artifacts"]
    loaded: dict[str, Any] = {}
    types = {
        "invocation": "dao_m4_invocation",
        "operation_log": "dao_m4_operation_log",
        "snapshot": "dao_m4_empty_schema_version_snapshot",
        "worker_result": "dao_m4_worker_result",
    }
    for name, expected_type in types.items():
        ref = artifacts[name]
        path = resolve_bundle_path(bundle_root, ref["path"])
        document, _, digest = load_document(path, 65536, expected_type)
        require_equal(digest, ref["sha256"], f"$.phases.{phase}.artifacts.{name}.sha256")
        loaded[name] = document
    invocation = loaded["invocation"]
    result = loaded["worker_result"]
    log = loaded["operation_log"]
    snapshot = loaded["snapshot"]
    worker = phase_row["worker"]
    worker_id = f"{sample['sample_id']}-{phase.upper()}"
    validate_invocation_document(
        invocation,
        plan,
        plan_sha256,
        bundle_root,
        expected_path=artifacts["worker_result"]["path"],
    )
    require_equal(invocation["producer_commit"], record["producer_commit"], "$.invocation.producer_commit")
    require_equal(invocation["environment_sha256"], record["environment_sha256"], "$.invocation.environment_sha256")
    require_equal(invocation["provider_sha256"], record["provider_sha256"], "$.invocation.provider_sha256")
    for document in (result, log, snapshot):
        _validate_common_projection(document, sample, phase, worker_id)
    require_equal(invocation["nonce"], worker["nonce"], "$.invocation.nonce")
    require_equal(invocation["worker_ordinal"], worker["worker_ordinal"], "$.invocation.worker_ordinal")
    require_equal(result["nonce"], worker["nonce"], "$.worker_result.nonce")
    require_equal(result["worker_ordinal"], worker["worker_ordinal"], "$.worker_result.worker_ordinal")
    require_equal(result["process_id"], worker["process_id"], "$.worker_result.process_id")
    require_equal(result["architecture"], worker["architecture"], "$.worker_result.architecture")
    require_equal(result["provider"], worker["provider"], "$.worker_result.provider")
    require_equal(worker["provider"]["server_sha256"], record["provider_sha256"], "$.worker.provider.server_sha256")
    require_equal(result["started_at_utc"], worker["started_at_utc"], "$.worker_result.started_at_utc")
    require_equal(result["invocation_sha256"], artifacts["invocation"]["sha256"], "$.worker_result.invocation_sha256")
    require_equal(result["operation_log"], artifacts["operation_log"], "$.worker_result.operation_log")
    require_equal(result["snapshot"], artifacts["snapshot"], "$.worker_result.snapshot")
    actions = [entry["action"] for entry in log["entries"]]
    require_equal(actions, CREATOR_ACTIONS if phase == "creator" else REOPEN_ACTIONS, "$.operation_log.entries actions")
    require_equal([entry["sequence"] for entry in log["entries"]], list(range(1, len(actions) + 1)), "$.operation_log.entries sequence")
    times = [parse_timestamp(entry["timestamp_utc"], "$.operation_log.entries[].timestamp_utc") for entry in log["entries"]]
    if times != sorted(times):
        raise ValidationError("$.operation_log.entries: timestamps are out of order")
    started = parse_timestamp(result["started_at_utc"], "$.worker_result.started_at_utc")
    finished = parse_timestamp(result["finished_at_utc"], "$.worker_result.finished_at_utc")
    captured = parse_timestamp(snapshot["captured_at_utc"], "$.snapshot.captured_at_utc")
    created = parse_timestamp(invocation["created_at_utc"], "$.invocation.created_at_utc")
    if not (created <= started <= times[0] <= captured <= times[-1] <= finished):
        raise ValidationError(f"{sample['sample_id']} {phase}: invalid phase timestamp ordering")
    observations = phase_row["dao_observations_while_open"]
    require_equal(snapshot["captured_while_database_open"], observations["captured_while_database_open"], "$.snapshot.captured_while_database_open")
    require_equal(snapshot["dao_version"], observations["dao_version"], "$.snapshot.dao_version")
    require_equal(snapshot["empty_user_schema"], observations["empty_user_schema"], "$.snapshot.empty_user_schema")
    require_equal(snapshot["user_table_count"], observations["user_table_count"], "$.snapshot.user_table_count")
    require_equal(observations["dao_version"], condition["expected_dao_version"], f"$.phases.{phase}.dao_version")
    post = phase_row["post_close_file_observations"]
    result_post = result["post_close_file_observations"]
    require_equal(result_post["database_path"], post["database_path"], "$.worker_result.post_close.database_path")
    require_equal(result_post["database_bytes"], post["database_bytes"], "$.worker_result.post_close.database_bytes")
    require_equal(result_post["database_sha256"], post["database_sha256"], "$.worker_result.post_close.database_sha256")
    require_equal(result_post["prefix"], {"path": post["prefix_path"], "sha256": post["prefix_sha256"]}, "$.worker_result.post_close.prefix")
    require_equal(result_post["prefix_bytes"], post["prefix_bytes"], "$.worker_result.post_close.prefix_bytes")
    require_equal(post["database_path"], sample[f"{phase}_database_path"], f"$.phases.{phase}.database_path")
    if phase == "creator":
        require_equal(result["pre_com_file_binding"], None, "$.worker_result.pre_com_file_binding")
    else:
        require_equal(
            result["pre_com_file_binding"],
            {
                key: phase_row["pre_com_file_binding"][key]
                for key in ("database_path", "database_bytes", "database_sha256")
            },
            "$.worker_result.pre_com_file_binding",
        )
        contract = invocation["phase_contract"]
        pre = phase_row["pre_com_file_binding"]
        require_equal(contract["pre_com_database_bytes"], pre["database_bytes"], "$.phase_contract.pre_com_database_bytes")
        require_equal(contract["pre_com_database_sha256"], pre["database_sha256"], "$.phase_contract.pre_com_database_sha256")
        require_equal(contract["clone_log"], record["controller_clone"]["clone_log"], "$.phase_contract.clone_log")
    return {
        "invocation": invocation,
        "result": result,
        "log": log,
        "snapshot": snapshot,
        "started": started,
        "finished": finished,
    }


def validate_sample_record(
    bundle_root: Path,
    record: dict[str, Any],
    sample: dict[str, Any],
    condition: dict[str, Any],
    plan: dict[str, Any],
    plan_sha256: str,
) -> dict[str, dict[str, Any]]:
    """Validate a record, both phases, and the controller clone handoff."""
    SCHEMA_SET.validate(record)
    for key in ("sample_id", "condition_id", "replica", "block", "position_in_block", "launch_ordinal"):
        require_equal(record[key], sample[key], f"$.{key}")
    require_equal(record["plan_sha256"], plan_sha256, "$.plan_sha256")
    require_equal(record["creation"], _creation_projection(condition, plan), "$.creation")
    phases = {
        phase: validate_phase_documents(
            bundle_root, record, sample, condition, phase, plan, plan_sha256
        )
        for phase in ("creator", "reopen")
    }
    clone_ref = record["controller_clone"]["clone_log"]
    clone_path = resolve_bundle_path(bundle_root, clone_ref["path"])
    clone, _, clone_hash = load_document(clone_path, 65536, "dao_m4_clone_log")
    require_equal(clone_hash, clone_ref["sha256"], "$.controller_clone.clone_log.sha256")
    controller = record["controller_clone"]
    common = (
        "started_at_utc",
        "completed_at_utc",
        "source_path",
        "destination_path",
        "source_bytes",
        "destination_bytes",
        "source_sha256_before_clone",
        "source_sha256_after_clone",
        "destination_sha256",
        "source_file_identity",
        "destination_file_identity",
        "all_hashes_equal",
        "no_hardlink",
        "same_volume",
        "distinct_file_identity",
        "completed_before_reopen_com",
        "status",
    )
    for key in common:
        require_equal(clone[key], controller[key], f"$.controller_clone.{key}")
    require_equal(clone["sample_id"], sample["sample_id"], "$.clone_log.sample_id")
    require_equal(clone["reparse_free"], controller["source_reparse_free"] and controller["destination_reparse_free"], "$.clone_log.reparse_free")
    hashes = {
        controller["source_sha256_before_clone"],
        controller["source_sha256_after_clone"],
        controller["destination_sha256"],
    }
    if len(hashes) != 1:
        raise ValidationError("$.controller_clone: three clone hashes differ")
    require_equal(controller["source_bytes"], controller["destination_bytes"], "$.controller_clone.destination_bytes")
    require_equal(controller["source_path"], sample["creator_database_path"], "$.controller_clone.source_path")
    require_equal(controller["destination_path"], sample["reopen_database_path"], "$.controller_clone.destination_path")
    source_identity = controller["source_file_identity"]
    destination_identity = controller["destination_file_identity"]
    require_equal(source_identity["volume_serial_number"], destination_identity["volume_serial_number"], "$.controller_clone.same_volume")
    if source_identity["file_index"] == destination_identity["file_index"]:
        raise ValidationError("$.controller_clone: source and destination file identities are equal")
    creator_post = record["phases"]["creator"]["post_close_file_observations"]
    reopen_pre = record["phases"]["reopen"]["pre_com_file_binding"]
    require_equal((creator_post["database_bytes"], creator_post["database_sha256"]), (controller["source_bytes"], controller["source_sha256_before_clone"]), "$.controller_clone source binding")
    require_equal((reopen_pre["database_path"], reopen_pre["database_bytes"], reopen_pre["database_sha256"]), (controller["destination_path"], controller["destination_bytes"], controller["destination_sha256"]), "$.controller_clone destination binding")
    clone_started = parse_timestamp(controller["started_at_utc"], "$.controller_clone.started_at_utc")
    clone_finished = parse_timestamp(controller["completed_at_utc"], "$.controller_clone.completed_at_utc")
    if not (phases["creator"]["finished"] <= clone_started <= clone_finished <= phases["reopen"]["started"]):
        raise ValidationError("$.controller_clone: clone and phase timestamps are out of order")
    creator_worker = record["phases"]["creator"]["worker"]
    reopen_worker = record["phases"]["reopen"]["worker"]
    if creator_worker["worker_run_id"] == reopen_worker["worker_run_id"]:
        raise ValidationError("$.phases: paired worker run IDs must differ")
    if creator_worker["nonce"] == reopen_worker["nonce"]:
        raise ValidationError("$.phases: paired worker nonces must differ")
    return phases
