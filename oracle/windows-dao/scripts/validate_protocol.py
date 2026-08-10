#!/usr/bin/env python3
"""Validate DAO protocol documents and immutable evidence bundles.

This intentionally uses only the Python standard library so protocol checking
can run on development hosts that cannot execute the Windows DAO oracle.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from protocol_validation import (
    ProtocolSchemaSet,
    ValidationError,
    canonical_json_bytes,
    load_json,
    sha256,
    validate_environment,
    validate_operation_log,
    validate_snapshot,
)

PROTOCOL_VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCHEMA_DIR = ROOT / "protocol" / "v1"
SCHEMAS = {
    "dao_scenario": "scenario.schema.json",
    "canonical_snapshot": "canonical-snapshot.schema.json",
    "dao_environment": "environment.schema.json",
    "dao_operation_log": "operation-log.schema.json",
    "dao_evidence_report": "evidence-report.schema.json",
    "dao_bundle_manifest": "bundle-manifest.schema.json",
}
REFERENCE_ROLES = {
    "input": "scenario_input",
    "source_database": "source_database",
    "output_database": "output_database",
    "dao_snapshot": "dao_snapshot",
    "rust_snapshot": "rust_snapshot",
    "operation_log": "operation_log",
}
M0_SCENARIO_ID = "DAO-GEN-PROBE-001"
SCHEMA_SET = ProtocolSchemaSet(SCHEMA_DIR, SCHEMAS)


def _validate_scenario(document: dict[str, Any]) -> None:
    family_for_mode = {
        "dao_generate_fixture": "DAO-GEN-",
        "rust_read_dao": "DAO-READ-",
        "dao_open_rust": "DAO-WRITE-",
        "dao_verify_rust_update": "DAO-UPDATE-",
    }
    prefix = family_for_mode[document["mode"]]
    if not document["scenario_id"].startswith(prefix):
        raise ValidationError(
            "$.scenario_id: family does not agree with $.mode"
        )
    database = document["database"]
    if database["input_role"] == "none":
        if "input_path" in database or "input_sha256" in database:
            raise ValidationError(
                "$.database: input path/hash are forbidden when input_role is none"
            )
    elif "input_path" not in database or "input_sha256" not in database:
        raise ValidationError(
            "$.database: non-empty input role requires input_path and input_sha256"
        )
    expected = document["expected"]
    if expected["outcome"] == "expected_error" and "error_class" not in expected:
        raise ValidationError(
            "$.expected: expected_error requires a stable error_class"
        )
    if document["mode"] == "dao_verify_rust_update" and not expected.get(
        "preserve_paths"
    ):
        raise ValidationError(
            "$.expected.preserve_paths: update verification requires preservation paths"
        )
    step_ids = [step["step_id"] for step in document["steps"]]
    if len(step_ids) != len(set(step_ids)):
        raise ValidationError("$.steps: step_id values must be unique")


def _validate_report(document: dict[str, Any]) -> None:
    scenario_ids = [item["scenario_id"] for item in document["scenarios"]]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValidationError("$.scenarios: scenario IDs must be unique")
    observed = Counter(item["status"] for item in document["scenarios"])
    counts = document["counts"]
    if counts["selected"] != len(document["scenarios"]):
        raise ValidationError("$.counts.selected: does not match scenario list")
    for status in ("pass", "fail", "blocked", "error", "skipped"):
        if counts[status] != observed[status]:
            raise ValidationError(f"$.counts.{status}: does not match scenario list")
    if document["status"] == "pass":
        if document["git"]["dirty"]:
            raise ValidationError("$.git.dirty: a passing evidence run must be clean")
        if any(counts[item] for item in ("fail", "blocked", "error", "skipped")):
            raise ValidationError("$.status: pass cannot contain non-passing scenarios")
        if counts["selected"] == 0:
            raise ValidationError("$.status: pass requires at least one scenario")


def validate_document(document: Any) -> str:
    document_type = SCHEMA_SET.validate(document)
    if document.get("protocol_version") != PROTOCOL_VERSION:
        raise ValidationError("$.protocol_version: unsupported protocol version")
    if document_type == "dao_scenario":
        _validate_scenario(document)
    elif document_type == "canonical_snapshot":
        validate_snapshot(document)
    elif document_type == "dao_environment":
        validate_environment(document)
    elif document_type == "dao_operation_log":
        validate_operation_log(document)
    elif document_type == "dao_evidence_report":
        _validate_report(document)
    return document_type


def validate_document_path(path: Path) -> str:
    document = load_json(path)
    document_type = validate_document(document)
    if document_type == "canonical_snapshot":
        canonical = canonical_json_bytes(document)
        try:
            retained = path.read_bytes()
        except OSError as exc:
            raise ValidationError(f"{path}: cannot read snapshot bytes: {exc}") from exc
        if retained != canonical:
            raise ValidationError(
                f"{path}: canonical snapshot bytes are not normalized"
            )
    return document_type


def validate_schemas() -> None:
    SCHEMA_SET.lint()


def _safe_bundle_path(bundle: Path, relative: str) -> Path:
    candidate = bundle / relative
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(bundle.resolve())
    except (OSError, ValueError) as exc:
        raise ValidationError(f"unsafe bundle path {relative!r}") from exc
    return candidate


def _validate_manifest_payloads(
    bundle: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest_path = bundle / "bundle-manifest.json"
    manifest = load_json(manifest_path)
    if validate_document(manifest) != "dao_bundle_manifest":
        raise ValidationError(f"{manifest_path}: wrong document type")

    if bundle.name != manifest["run_id"]:
        raise ValidationError("bundle directory name does not match run_id")
    if bundle.parent.name != manifest["git_commit"]:
        raise ValidationError("bundle parent directory does not match git_commit")

    file_entries = manifest["files"]
    paths = [entry["path"] for entry in file_entries]
    if len(paths) != len(set(paths)):
        raise ValidationError("$.files: paths must be unique")
    entry_by_path = {entry["path"]: entry for entry in file_entries}
    actual_payloads = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual_payloads != set(paths):
        missing = sorted(set(paths) - actual_payloads)
        unlisted = sorted(actual_payloads - set(paths))
        raise ValidationError(
            f"manifest/file mismatch; missing={missing}, unlisted={unlisted}"
        )
    for entry in file_entries:
        path = _safe_bundle_path(bundle, entry["path"])
        if not path.is_file():
            raise ValidationError(f"{entry['path']}: not a regular file")
        if path.stat().st_size != entry["size_bytes"]:
            raise ValidationError(f"{entry['path']}: size does not match manifest")
        if sha256(path) != entry["sha256"]:
            raise ValidationError(f"{entry['path']}: SHA-256 does not match manifest")
    return manifest, entry_by_path


def _load_bound_report(
    bundle: Path,
    manifest: dict[str, Any],
    entry_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    report_entry = entry_by_path.get(manifest["report_path"])
    if report_entry is None or report_entry["role"] != "report":
        raise ValidationError("$.report_path: missing report-role manifest entry")
    report_path = _safe_bundle_path(bundle, manifest["report_path"])
    report = load_json(report_path)
    if validate_document(report) != "dao_evidence_report":
        raise ValidationError(f"{report_path}: wrong document type")
    if report["run_id"] != manifest["run_id"]:
        raise ValidationError("report and manifest run IDs differ")
    if report["git"]["commit"] != manifest["git_commit"]:
        raise ValidationError("report and manifest commits differ")
    if report["git"]["dirty"] != manifest["dirty"]:
        raise ValidationError("report and manifest dirty flags differ")
    if report["oracle_revision"] != manifest["git_commit"]:
        raise ValidationError("oracle revision is not the bundle git commit")
    if report["status"] != manifest["status"]:
        raise ValidationError("report and manifest statuses differ")
    report_ids = [item["scenario_id"] for item in report["scenarios"]]
    if set(report_ids) != set(manifest["scenario_ids"]):
        raise ValidationError("report and manifest scenario IDs differ")
    return report


def _validate_environment_binding(
    bundle: Path,
    report: dict[str, Any],
    entry_by_path: dict[str, dict[str, Any]],
) -> str:
    environment_ref = report["environment"]
    environment_entry = entry_by_path.get(environment_ref["path"])
    if environment_entry is None or environment_entry["role"] != "environment":
        raise ValidationError("report environment lacks environment-role manifest entry")
    if environment_entry["sha256"] != environment_ref["sha256"]:
        raise ValidationError("report and manifest environment hashes differ")
    environment_path = _safe_bundle_path(bundle, environment_ref["path"])
    environment = load_json(environment_path)
    if validate_document(environment) != "dao_environment":
        raise ValidationError(f"{environment_path}: wrong document type")
    if report["status"] == "pass" and environment["status"] != "ready":
        raise ValidationError("passing report requires a ready DAO environment")
    return environment_ref["path"]


def _validate_report_references(
    bundle: Path,
    report: dict[str, Any],
    entry_by_path: dict[str, dict[str, Any]],
) -> set[str]:
    referenced: list[tuple[str, dict[str, str]]] = []
    referenced_paths: set[str] = set()
    for scenario in report["scenarios"]:
        for key in REFERENCE_ROLES:
            reference = scenario[key]
            if reference is not None:
                referenced.append((key, reference))
                referenced_paths.add(reference["path"])
    for key, reference in referenced:
        entry = entry_by_path.get(reference["path"])
        if entry is None:
            raise ValidationError(
                f"{reference['path']}: report reference is absent from manifest"
            )
        if entry["role"] != REFERENCE_ROLES[key]:
            raise ValidationError(
                f"{reference['path']}: manifest role does not match report reference"
            )
        if entry["sha256"] != reference["sha256"]:
            raise ValidationError(
                f"{reference['path']}: report and manifest hashes differ"
            )
        if key in ("input", "dao_snapshot", "rust_snapshot", "operation_log"):
            validate_document_path(_safe_bundle_path(bundle, reference["path"]))
    return referenced_paths


def _validate_operation_binding(
    bundle: Path,
    report: dict[str, Any],
    result: dict[str, Any],
    scenario_input: dict[str, Any],
) -> None:
    reference = result["operation_log"]
    if reference is None:
        if result["status"] == "pass":
            raise ValidationError(
                f"{result['scenario_id']}: passing result lacks operation log"
            )
        return
    operation_log = load_json(_safe_bundle_path(bundle, reference["path"]))
    bindings = (
        ("scenario_id", result["scenario_id"]),
        ("run_id", report["run_id"]),
        ("git_commit", report["git"]["commit"]),
        ("final_status", result["status"]),
    )
    for key, expected in bindings:
        if operation_log[key] != expected:
            raise ValidationError(
                f"{result['scenario_id']}: operation log {key} differs"
            )
    if result["status"] != "pass":
        return
    required_actions = [step["action"] for step in scenario_input["steps"]]
    expected_actions = [
        "activate_provider",
        *required_actions,
        "reopen_database",
        "snapshot",
        "finalize",
    ]
    actions = [entry["action"] for entry in operation_log["entries"]]
    if actions != expected_actions:
        raise ValidationError(
            f"{result['scenario_id']}: operation actions do not match scenario/lifecycle"
        )
    if any(entry["status"] != "pass" for entry in operation_log["entries"]):
        raise ValidationError(
            f"{result['scenario_id']}: passing log contains a failed operation"
        )


def _validate_m0_pass_artifacts(
    bundle: Path,
    report: dict[str, Any],
    result: dict[str, Any],
) -> None:
    expected_presence = {
        "source_database": False,
        "output_database": True,
        "dao_snapshot": True,
        "rust_snapshot": False,
        "operation_log": True,
    }
    for key, required in expected_presence.items():
        if (result[key] is not None) != required:
            raise ValidationError(
                f"{result['scenario_id']}: {key} violates M0 artifact contract"
            )
    database_hash = result["output_database"]["sha256"]
    snapshot = load_json(
        _safe_bundle_path(bundle, result["dao_snapshot"]["path"])
    )
    if snapshot["scenario_id"] != result["scenario_id"]:
        raise ValidationError(f"{result['scenario_id']}: snapshot scenario differs")
    if snapshot["producer"]["kind"] != "dao":
        raise ValidationError(f"{result['scenario_id']}: snapshot producer differs")
    if snapshot["producer"]["source_revision"] != report["git"]["commit"]:
        raise ValidationError(f"{result['scenario_id']}: snapshot revision differs")
    if snapshot["database_sha256"] != database_hash:
        raise ValidationError(
            f"{result['scenario_id']}: snapshot/database hashes differ"
        )


def _validate_scenario_binding(
    bundle: Path,
    report: dict[str, Any],
    result: dict[str, Any],
) -> None:
    input_path = _safe_bundle_path(bundle, result["input"]["path"])
    scenario_input = load_json(input_path)
    if validate_document(scenario_input) != "dao_scenario":
        raise ValidationError(f"{input_path}: wrong document type")
    if scenario_input["scenario_id"] != result["scenario_id"]:
        raise ValidationError(f"{result['scenario_id']}: result/input scenario IDs differ")
    if scenario_input["mode"] != result["mode"]:
        raise ValidationError(f"{result['scenario_id']}: result/input modes differ")
    if scenario_input["capabilities"] != result["capabilities"]:
        raise ValidationError(f"{result['scenario_id']}: result/input capabilities differ")
    if result["mode"] != "dao_generate_fixture":
        raise ValidationError(
            f"{result['scenario_id']}: differential mode is not implemented"
        )
    if result["scenario_id"] != M0_SCENARIO_ID:
        raise ValidationError(
            f"{result['scenario_id']}: only the checked M0 scenario is implemented"
        )
    _validate_operation_binding(bundle, report, result, scenario_input)
    if result["status"] == "pass":
        _validate_m0_pass_artifacts(bundle, report, result)


def _validate_exact_passing_payloads(
    report: dict[str, Any],
    entry_by_path: dict[str, dict[str, Any]],
    referenced_paths: set[str],
) -> None:
    if report["status"] == "pass" and set(entry_by_path) != referenced_paths:
        extras = sorted(set(entry_by_path) - referenced_paths)
        missing = sorted(referenced_paths - set(entry_by_path))
        raise ValidationError(
            f"passing bundle payload contract differs; extras={extras}, missing={missing}"
        )


def validate_bundle(bundle: Path) -> None:
    manifest, entry_by_path = _validate_manifest_payloads(bundle)
    report = _load_bound_report(bundle, manifest, entry_by_path)
    environment_path = _validate_environment_binding(bundle, report, entry_by_path)
    referenced_paths = _validate_report_references(bundle, report, entry_by_path)
    referenced_paths.update((manifest["report_path"], environment_path))
    for result in report["scenarios"]:
        _validate_scenario_binding(bundle, report, result)
    _validate_exact_passing_payloads(report, entry_by_path, referenced_paths)


if __name__ == "__main__":
    from protocol_cli import main
    raise SystemExit(
        main(
            schema_count=len(SCHEMAS),
            validate_schemas=validate_schemas,
            validate_document_path=validate_document_path,
            validate_bundle=validate_bundle,
            validation_error=ValidationError,
        )
    )
