#!/usr/bin/env python3
"""Validate and recompute the bounded M3 replicated DAO experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from m1_bundle_validation import bounded_file_identity
from m3_analysis import PAGE_SIZE, build_physical_analysis
from m3_experiment import (
    M3,
    validate_invocation as validate_checked_invocation,
    validate_plan as validate_checked_plan,
    worker_run_id,
)
from protocol_validation import (
    ValidationError,
    canonical_json_bytes,
)
from validate_m1_protocol import compare_snapshots, validate_document

HERE = Path(__file__).resolve().parent
ORACLE = HERE.parent
REPOSITORY = ORACLE.parent.parent
M3 = ORACLE / "experiments" / "m3"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_DATABASE_BYTES = 1024 * 1024
MAX_TOTAL_DATABASE_BYTES = 9 * 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 160
MAX_DEPTH = 5
MAX_ANALYSIS_WORKING_BYTES = 16 * 1024 * 1024
REPARSE = 0x400


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> Any:
    """Load bounded strict UTF-8 JSON with duplicate/non-finite rejection."""
    _, _, retained = bounded_file_identity(path, MAX_JSON_BYTES, retain=True)
    assert retained is not None
    if retained.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{path}: UTF-8 byte-order marks are forbidden")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"{path}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def nonfinite(value: str) -> None:
        raise ValidationError(f"{path}: non-finite JSON number {value}")

    try:
        return json.loads(
            retained.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=nonfinite,
        )
    except ValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path}: invalid JSON: {exc}") from exc


def canonical(document: dict[str, Any]) -> bytes:
    return canonical_json_bytes(document)


def _timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ValidationError(f"{label}: timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label}: invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"{label}: timestamp lacks an offset")


def _sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValidationError(f"{label}: invalid SHA-256")


def _safe_relative(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 240
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValidationError(f"{label}: unsafe relative path")
    return value


def _resolved_under(bundle: Path, relative: Any, label: str) -> Path:
    safe = _safe_relative(relative, label)
    candidate = (bundle / safe).resolve()
    try:
        candidate.relative_to(bundle.resolve())
    except ValueError as exc:
        raise ValidationError(f"{label}: path escapes bundle") from exc
    return candidate


def validate_invocation(
    document: Any,
    invocation_path: Path,
    retained_environment_path: Path | None = None,
) -> None:
    validate_checked_invocation(
        document, invocation_path, load_json, retained_environment_path
    )


def validate_plan(document: dict[str, Any]) -> None:
    validate_checked_plan(document, load_json)


def page_hashes(value: bytes) -> list[str]:
    if not value or len(value) % PAGE_SIZE:
        raise ValidationError("M3 database is empty or not 2-KiB aligned")
    return [
        sha256_bytes(value[offset : offset + PAGE_SIZE])
        for offset in range(0, len(value), PAGE_SIZE)
    ]


def _normalized_snapshot(document: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(document))
    normalized["database_sha256"] = "0" * 64
    return normalized


def _load_samples(bundle: Path, plan: dict[str, Any]) -> tuple[dict[str, bytes], dict[str, Any]]:
    values: dict[str, bytes] = {}
    records: dict[str, Any] = {}
    total = 0
    process_keys: set[tuple[int, str]] = set()
    nonces: set[str] = set()
    environment = load_json(bundle / "environment.json")
    if (
        validate_document(environment) != "dao_environment"
        or environment["status"] != "ready"
        or environment["host"]["process_architecture"] != "x86"
        or environment["accepted_provider"] is None
        or environment["accepted_provider"]["registry_view"] != "x86"
    ):
        raise ValidationError("M3 requires its exact ready x86 environment")
    accepted = environment["accepted_provider"]
    _, environment_hash, _ = bounded_file_identity(
        bundle / "environment.json", MAX_JSON_BYTES
    )
    for sample in plan["samples"]:
        sample_id = sample["sample_id"]
        record = load_json(bundle / "samples" / sample_id / "record.json")
        required = {
            "block",
            "condition_id",
            "database",
            "document_type",
            "git_commit",
            "invocation",
            "launch_nonce",
            "launch_ordinal",
            "operation_log",
            "process",
            "protocol_version",
            "replica",
            "run_id",
            "sample_id",
            "scenario_id",
            "scenario_sha256",
            "snapshot",
            "status",
            "worker_run_id",
        }
        if not isinstance(record, dict) or set(record) != required:
            raise ValidationError(f"{sample_id}: sample record keys differ")
        for key in ("block", "condition_id", "launch_ordinal", "replica", "sample_id"):
            if record[key] != sample[key]:
                raise ValidationError(f"{sample_id}: plan binding differs for {key}")
        if (
            record["document_type"] != "dao_m3_sample_record"
            or record["protocol_version"] != "1.0.0"
            or record["status"] != "pass"
        ):
            raise ValidationError(f"{sample_id}: required worker did not pass")
        condition = next(
            item for item in plan["conditions"] if item["condition_id"] == sample["condition_id"]
        )
        if (
            record["scenario_id"] != condition["scenario_id"]
            or record["scenario_sha256"] != condition["scenario_sha256"]
        ):
            raise ValidationError(f"{sample_id}: scenario binding differs")
        process = record["process"]
        if set(process) != {
            "architecture",
            "id",
            "powershell_version",
            "provider_clsid",
            "provider_prog_id",
            "provider_server_path",
            "provider_server_sha256",
            "started_at_utc",
        } or process["architecture"] != "x86":
            raise ValidationError(f"{sample_id}: process identity differs")
        if (
            not isinstance(process["id"], int)
            or isinstance(process["id"], bool)
            or process["id"] <= 0
            or any(
                not isinstance(process[key], str) or not process[key]
                for key in (
                    "powershell_version",
                    "provider_clsid",
                    "provider_prog_id",
                    "provider_server_path",
                    "provider_server_sha256",
                    "started_at_utc",
                )
            )
            or not isinstance(record["launch_nonce"], str)
        ):
            raise ValidationError(f"{sample_id}: malformed process/nonce identity")
        try:
            uuid.UUID(record["launch_nonce"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError(f"{sample_id}: malformed launch nonce") from exc
        if (
            process["architecture"] != environment["host"]["process_architecture"]
            or process["powershell_version"]
            != environment["runtime"]["powershell_version"]
            or process["provider_prog_id"] != accepted["prog_id"]
            or process["provider_clsid"].lower() != accepted["clsid"].lower()
            or process["provider_server_path"].lower()
            != accepted["server_path"].lower()
            or process["provider_server_sha256"] != accepted["server_sha256"]
        ):
            raise ValidationError(f"{sample_id}: provider/environment binding differs")
        _sha256(process["provider_server_sha256"], f"{sample_id}.process.provider")
        _timestamp(process["started_at_utc"], f"{sample_id}.process.started_at_utc")
        process_key = (process["id"], process["started_at_utc"])
        if process_key in process_keys or record["launch_nonce"] in nonces:
            raise ValidationError(f"{sample_id}: worker process or nonce was reused")
        process_keys.add(process_key)
        nonces.add(record["launch_nonce"])
        if (
            record["git_commit"] != bundle.parent.name
            or record["run_id"] != bundle.name
            or record["worker_run_id"]
            != worker_run_id(sample["launch_ordinal"], bundle.name)
        ):
            raise ValidationError(f"{sample_id}: campaign commit/run binding differs")
        expected_invocation = f"samples/{sample_id}/invocation.json"
        invocation_ref = record["invocation"]
        if (
            not isinstance(invocation_ref, dict)
            or set(invocation_ref) != {"path", "sha256"}
            or invocation_ref["path"] != expected_invocation
        ):
            raise ValidationError(f"{sample_id}: invocation reference differs")
        invocation_path = _resolved_under(
            bundle, expected_invocation, f"{sample_id}.invocation"
        )
        invocation = load_json(invocation_path)
        validate_invocation(
            invocation, invocation_path, bundle / "environment.json"
        )
        _, invocation_hash, _ = bounded_file_identity(
            invocation_path, MAX_JSON_BYTES
        )
        if (
            invocation_hash != invocation_ref["sha256"]
            or invocation["campaign_run_id"] != record["run_id"]
            or invocation["run_id"] != record["worker_run_id"]
            or invocation["git_commit"] != record["git_commit"]
            or invocation["launch_nonce"] != record["launch_nonce"]
            or invocation["environment_sha256"] != environment_hash
        ):
            raise ValidationError(f"{sample_id}: invocation cross-binding differs")
        database_ref = record["database"]
        if (
            not isinstance(database_ref, dict)
            or set(database_ref) != {"path", "sha256", "size_bytes"}
        ):
            raise ValidationError(f"{sample_id}: database reference keys differ")
        expected_database_path = f"databases/{database_ref['sha256']}.mdb"
        if database_ref.get("path") != expected_database_path:
            raise ValidationError(f"{sample_id}: database path is not content addressed")
        database_path = _resolved_under(
            bundle, database_ref["path"], f"{sample_id}.database"
        )
        size, digest, retained = bounded_file_identity(
            database_path, MAX_DATABASE_BYTES, retain=True
        )
        assert retained is not None
        if (
            size != database_ref["size_bytes"]
            or digest != database_ref["sha256"]
            or digest != Path(database_ref["path"]).stem
        ):
            raise ValidationError(f"{sample_id}: database identity differs")
        total += size
        if total > MAX_TOTAL_DATABASE_BYTES:
            raise ValidationError("M3 database aggregate exceeds its ceiling")
        expected_snapshot = f"samples/{sample_id}/dao-snapshot.json"
        if (
            not isinstance(record["snapshot"], dict)
            or set(record["snapshot"]) != {"path", "sha256"}
            or record["snapshot"].get("path") != expected_snapshot
        ):
            raise ValidationError(f"{sample_id}: snapshot path differs")
        snapshot_path = _resolved_under(
            bundle, expected_snapshot, f"{sample_id}.snapshot"
        )
        snapshot = load_json(snapshot_path)
        if validate_document(snapshot) != "canonical_snapshot":
            raise ValidationError(f"{sample_id}: snapshot document type differs")
        if (
            snapshot["scenario_id"] != record["scenario_id"]
            or snapshot["database_sha256"] != digest
            or snapshot["producer"]["kind"] != "dao"
            or snapshot["producer"]["source_revision"] != record["git_commit"]
        ):
            raise ValidationError(f"{sample_id}: snapshot/database binding differs")
        _, snapshot_hash, _ = bounded_file_identity(snapshot_path, MAX_JSON_BYTES)
        if snapshot_hash != record["snapshot"]["sha256"]:
            raise ValidationError(f"{sample_id}: snapshot hash differs")
        expected_log = f"samples/{sample_id}/operation-log.json"
        if (
            not isinstance(record["operation_log"], dict)
            or set(record["operation_log"]) != {"path", "sha256"}
            or record["operation_log"].get("path") != expected_log
        ):
            raise ValidationError(f"{sample_id}: operation-log path differs")
        log_path = _resolved_under(bundle, expected_log, f"{sample_id}.operation_log")
        log = load_json(log_path)
        if validate_document(log) != "dao_operation_log":
            raise ValidationError(f"{sample_id}: operation-log document type differs")
        _, log_hash, _ = bounded_file_identity(log_path, MAX_JSON_BYTES)
        if (
            log_hash != record["operation_log"]["sha256"]
            or log["final_status"] != "pass"
            or log["scenario_id"] != record["scenario_id"]
            or log["git_commit"] != record["git_commit"]
            or log["run_id"] != record["worker_run_id"]
        ):
            raise ValidationError(f"{sample_id}: operation log differs")
        record["_snapshot_document"] = snapshot
        values[sample_id] = retained
        records[sample_id] = record
    for condition_id in ("B", "E", "I"):
        cohort = [
            records[item["sample_id"]]["_snapshot_document"]
            for item in plan["samples"]
            if item["condition_id"] == condition_id
        ]
        first = _normalized_snapshot(cohort[0])
        if any(_normalized_snapshot(item) != first for item in cohort[1:]):
            raise ValidationError(f"{condition_id}: replica semantic snapshots differ")
    baseline = records["M3-SAMPLE-B-01"]["_snapshot_document"]
    indexed = records["M3-SAMPLE-I-01"]["_snapshot_document"]
    compare_snapshots(
        baseline,
        indexed,
        ["/database_sha256", "/scenario_id", "/tables/0/indexes"],
    )
    return values, records


def build_analysis(bundle: Path, plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bytes]]:
    values, _ = _load_samples(bundle, plan)
    return build_physical_analysis(values, plan, MAX_ANALYSIS_WORKING_BYTES)


def discover_files(root: Path) -> set[str]:
    try:
        current = root
        ancestors = []
        while True:
            ancestors.append(current)
            if current.parent == current:
                break
            current = current.parent
        for ancestor in reversed(ancestors):
            if not ancestor.exists():
                continue
            metadata = ancestor.lstat()
            if ancestor.is_symlink() or getattr(
                metadata, "st_file_attributes", 0
            ) & REPARSE:
                raise ValidationError("M3 bundle path contains a reparse point")
        root_stat = root.lstat()
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or root.is_symlink()
            or getattr(root_stat, "st_file_attributes", 0) & REPARSE
        ):
            raise ValidationError("M3 bundle root must be a non-reparse directory")
        found: set[str] = set()
        pending = [(root, 0)]
        entries = 0
        while pending:
            directory, depth = pending.pop()
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    entries += 1
                    if entries > MAX_ENTRIES:
                        raise ValidationError("M3 bundle exceeds entry ceiling")
                    path = Path(entry.path)
                    metadata = path.lstat()
                    if entry.is_symlink() or getattr(
                        metadata, "st_file_attributes", 0
                    ) & REPARSE:
                        raise ValidationError("M3 bundle contains a reparse point")
                    if stat.S_ISDIR(metadata.st_mode):
                        if depth >= MAX_DEPTH:
                            raise ValidationError("M3 bundle exceeds depth ceiling")
                        pending.append((path, depth + 1))
                    elif stat.S_ISREG(metadata.st_mode):
                        if metadata.st_nlink > 1:
                            raise ValidationError("M3 bundle hard links are forbidden")
                        found.add(path.relative_to(root).as_posix())
                    else:
                        raise ValidationError("M3 bundle contains a non-regular entry")
        return found
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"cannot enumerate M3 bundle: {exc}") from exc


def _expected_role(path: str) -> tuple[str, str] | None:
    fixed = {
        "analysis/summary.json": ("analysis_summary", "application/json"),
        "environment.json": ("environment", "application/json"),
        "plan.json": ("plan", "application/json"),
        "report.json": ("report", "application/json"),
    }
    if path in fixed:
        return fixed[path]
    if path.startswith("analysis/masks/") and path.endswith(".bin"):
        return ("analysis_mask", "application/octet-stream")
    if path.startswith("databases/") and path.endswith(".mdb"):
        return ("output_database", "application/vnd.ms-access")
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "samples":
        roles = {
            "dao-snapshot.json": "dao_snapshot",
            "invocation.json": "worker_invocation",
            "operation-log.json": "operation_log",
            "record.json": "sample_record",
        }
        if parts[2] in roles:
            return (roles[parts[2]], "application/json")
    return None


def _require_entry(
    entries: dict[str, dict[str, Any]],
    path: str,
    role: str,
    media_type: str,
) -> None:
    entry = entries.get(path)
    if (
        entry is None
        or entry["role"] != role
        or entry["media_type"] != media_type
    ):
        raise ValidationError(f"{path}: manifest role/media binding differs")


def validate_bundle(bundle: Path) -> None:
    manifest_path = bundle / "bundle-manifest.json"
    manifest = load_json(manifest_path)
    required = {
        "created_at_utc",
        "dirty",
        "document_type",
        "files",
        "git_commit",
        "plan",
        "protocol_version",
        "report_path",
        "run_id",
        "status",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValidationError("M3 manifest keys differ")
    if (
        manifest["document_type"] != "dao_m3_campaign_manifest"
        or manifest["protocol_version"] != "1.0.0"
        or manifest["dirty"]
        or manifest["status"] != "pass"
        or bundle.name != manifest["run_id"]
        or bundle.parent.name != manifest["git_commit"]
    ):
        raise ValidationError("M3 manifest identity/status differs")
    _timestamp(manifest["created_at_utc"], "$.created_at_utc")
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        raise ValidationError("$.files: nonempty array required")
    files = manifest["files"]
    paths = [
        _safe_relative(item.get("path") if isinstance(item, dict) else None, "$.files.path")
        for item in files
    ]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValidationError("M3 manifest paths must be sorted and unique")
    actual = discover_files(bundle)
    actual.discard("bundle-manifest.json")
    if actual != set(paths):
        raise ValidationError("M3 manifest/file set differs")
    total = 0
    entries: dict[str, dict[str, Any]] = {}
    for item in files:
        if set(item) != {"media_type", "path", "role", "sha256", "size_bytes"}:
            raise ValidationError("M3 manifest file entry keys differ")
        expected = _expected_role(item["path"])
        if expected is None or (item["role"], item["media_type"]) != expected:
            raise ValidationError(f"{item['path']}: unexpected manifest role/path")
        maximum = (
            MAX_DATABASE_BYTES
            if item["role"] == "output_database"
            else MAX_JSON_BYTES
        )
        size, digest, _ = bounded_file_identity(
            _resolved_under(bundle, item["path"], "$.files.path"), maximum
        )
        if (
            not isinstance(item["size_bytes"], int)
            or isinstance(item["size_bytes"], bool)
            or item["size_bytes"] < 0
            or size != item["size_bytes"]
            or digest != item["sha256"]
        ):
            raise ValidationError(f"{item['path']}: manifest identity differs")
        _sha256(item["sha256"], f"{item['path']}.sha256")
        total += size
        entries[item["path"]] = item
        suffix = Path(item["path"]).suffix
        if item["role"] == "output_database":
            if (
                suffix != ".mdb"
                or item["media_type"] != "application/vnd.ms-access"
            ):
                raise ValidationError(f"{item['path']}: database role/media differs")
        elif suffix == ".bin":
            if (
                item["role"] != "analysis_mask"
                or item["media_type"] != "application/octet-stream"
            ):
                raise ValidationError(f"{item['path']}: mask role/media differs")
        elif item["media_type"] != "application/json":
            raise ValidationError(f"{item['path']}: JSON media type differs")
    if total > MAX_BUNDLE_BYTES:
        raise ValidationError("M3 bundle exceeds aggregate ceiling")
    if (
        not isinstance(manifest["plan"], dict)
        or set(manifest["plan"]) != {"path", "sha256"}
        or manifest["plan"]["path"] != "plan.json"
        or manifest["report_path"] != "report.json"
    ):
        raise ValidationError("M3 manifest fixed references differ")
    _require_entry(entries, "plan.json", "plan", "application/json")
    _require_entry(entries, "report.json", "report", "application/json")
    _require_entry(entries, "environment.json", "environment", "application/json")
    plan_path = _resolved_under(bundle, manifest["plan"]["path"], "$.plan.path")
    plan = load_json(plan_path)
    validate_plan(plan)
    _, plan_hash, _ = bounded_file_identity(plan_path, MAX_JSON_BYTES)
    if plan_hash != manifest["plan"]["sha256"]:
        raise ValidationError("M3 plan reference differs")
    _sha256(manifest["plan"]["sha256"], "$.plan.sha256")
    report = load_json(
        _resolved_under(bundle, manifest["report_path"], "$.report_path")
    )
    environment_path = _resolved_under(
        bundle, "environment.json", "$.environment"
    )
    environment = load_json(environment_path)
    if validate_document(environment) != "dao_environment":
        raise ValidationError("M3 environment document type differs")
    if environment.get("status") != "ready":
        raise ValidationError("M3 requires a ready protocol-1.1 environment")
    _, environment_hash, _ = bounded_file_identity(
        environment_path, MAX_JSON_BYTES
    )
    report_keys = {
        "comparison_count",
        "document_type",
        "environment_sha256",
        "git_commit",
        "plan_sha256",
        "remote_ref",
        "repository_url",
        "run_id",
        "sample_count",
        "samples",
        "status",
    }
    if not isinstance(report, dict) or set(report) != report_keys:
        raise ValidationError("M3 report keys differ")
    if (
        report["document_type"] != "dao_m3_report"
        or report.get("status") != "pass"
        or report.get("git_commit") != manifest["git_commit"]
        or report.get("run_id") != manifest["run_id"]
        or report.get("sample_count") != 9
        or report.get("comparison_count") != 18
        or report.get("environment_sha256") != environment_hash
        or report.get("plan_sha256") != plan_hash
        or report.get("remote_ref") != plan["remote_ref"]
        or report.get("repository_url") != plan["repository_url"]
    ):
        raise ValidationError("M3 report binding/status differs")
    expected_report_samples = []
    for sample in plan["samples"]:
        record = load_json(
            bundle / "samples" / sample["sample_id"] / "record.json"
        )
        expected_report_samples.append(
            {
                "database_sha256": record["database"]["sha256"],
                "sample_id": sample["sample_id"],
                "status": "pass",
            }
        )
    if report.get("samples") != expected_report_samples:
        raise ValidationError("M3 report sample results differ")
    summary, masks = build_analysis(bundle, plan)
    expected_summary = canonical(summary)
    summary_path = bundle / "analysis" / "summary.json"
    _require_entry(
        entries, "analysis/summary.json", "analysis_summary", "application/json"
    )
    _, _, retained = bounded_file_identity(summary_path, MAX_JSON_BYTES, retain=True)
    if retained != expected_summary:
        raise ValidationError("M3 analysis summary does not recompute")
    for relative, expected in masks.items():
        _require_entry(
            entries, relative, "analysis_mask", "application/octet-stream"
        )
        _, _, retained = bounded_file_identity(
            bundle / relative, MAX_JSON_BYTES, retain=True
        )
        if retained != expected:
            raise ValidationError(f"{relative}: M3 analysis mask does not recompute")
    expected_paths = {
        "analysis/summary.json",
        "environment.json",
        "plan.json",
        "report.json",
        *masks.keys(),
    }
    for sample in plan["samples"]:
        root = f"samples/{sample['sample_id']}"
        expected_paths.update(
            {
                f"{root}/dao-snapshot.json",
                f"{root}/invocation.json",
                f"{root}/operation-log.json",
                f"{root}/record.json",
            }
        )
    expected_paths.update(
        f"databases/{sample['database_sha256']}.mdb"
        for sample in expected_report_samples
    )
    if set(entries) != expected_paths:
        raise ValidationError("M3 manifest contains missing or unreferenced payloads")


def write_analysis(bundle: Path, output: Path) -> None:
    plan = load_json(bundle / "plan.json")
    validate_plan(plan)
    summary, masks = build_analysis(bundle, plan)
    output.mkdir(parents=True, exist_ok=False)
    (output / "masks").mkdir()
    (output / "summary.json").write_bytes(canonical(summary))
    for relative, value in masks.items():
        destination = output / Path(relative).relative_to("analysis")
        destination.write_bytes(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("path", type=Path)
    analysis = subparsers.add_parser("analyze")
    analysis.add_argument("bundle", type=Path)
    analysis.add_argument("output", type=Path)
    bundle = subparsers.add_parser("bundle")
    bundle.add_argument("path", type=Path)
    invocation = subparsers.add_parser("invocation")
    invocation.add_argument("path", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "plan":
            document = load_json(args.path)
            validate_plan(document)
            print(f"PASS: {args.path} (checked M3 plan)")
        elif args.command == "analyze":
            write_analysis(args.bundle.resolve(), args.output.resolve())
            print(f"PASS: wrote recomputable M3 analysis to {args.output}")
        elif args.command == "bundle":
            validate_bundle(args.path.resolve())
            print(f"PASS: {args.path} (immutable M3 campaign)")
        else:
            validate_invocation(load_json(args.path.resolve()), args.path.resolve())
            print(f"PASS: {args.path} (checked M3 worker invocation)")
        return 0
    except (OSError, ValidationError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
