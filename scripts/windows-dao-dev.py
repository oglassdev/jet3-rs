#!/usr/bin/env python3
"""Run explicitly exploratory DAO jobs in a local Windows VM."""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import NamedTuple
import uuid


ROOT = Path(__file__).resolve().parents[1]
REMOTE_RUNNER = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "scripts"
    / "dev"
    / "Invoke-Jet3DaoDevJob.ps1"
)
PROVIDER_PROBE = ROOT / "oracle" / "windows-dao" / "scripts" / "probe-provider.ps1"
CATALOG_JOB = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "dev" / "Catalog.DevJob.ps1"
)
TABLE_DEFINITION_JOB = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "dev" / "TableDefinition.DevJob.ps1"
)
TABLE_DEFINITION_TYPES = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "dev" / "TableDefinition.TypeInputs.json"
)
STAGED_DISPATCH = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "dev" / "Dispatch.DevJob.ps1"
)
STAGED_PUBLICATION = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "dev" / "Publish.DevJob.ps1"
)
ROW_JOB = ROOT / "oracle" / "windows-dao" / "scripts" / "dev" / "Row.DevJob.ps1"
VALUE_JOB = ROOT / "oracle" / "windows-dao" / "scripts" / "dev" / "Value.DevJob.ps1"
INDEX_JOB = ROOT / "oracle" / "windows-dao" / "scripts" / "dev" / "Index.DevJob.ps1"
BOOTSTRAP_LAYOUT_JOB = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "scripts"
    / "dev"
    / "BootstrapLayout.DevJob.ps1"
)
BOOTSTRAP_LAYOUT_PLAN = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "acquisition"
    / "bootstrap-layout-sufficiency.plan.json"
)
BOOTSTRAP_LAYOUT_ANALYZER = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "scripts"
    / "bootstrap_layout.py"
)
SYSTEM_CATALOG_JOB = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "dev" / "SystemCatalog.DevJob.ps1"
)
BOOTSTRAP_COMPOSER_VALIDATION_JOB = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "scripts"
    / "dev"
    / "BootstrapComposerValidation.DevJob.ps1"
)
SYSTEM_CATALOG_PLAN = (
    ROOT / "oracle" / "windows-dao" / "acquisition" / "system-catalog.plan.json"
)
SYSTEM_CATALOG_ANALYZER = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "system_catalog.py"
)
LONG_VALUE_MAPS_PLAN = (
    ROOT / "oracle" / "windows-dao" / "acquisition" / "long-value-maps.plan.json"
)
LONG_VALUE_MAPS_FOLLOWUP_PLAN = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "acquisition"
    / "long-value-maps-followup.plan.json"
)
BOOTSTRAP_COMPOSER_SEMANTICS_PLAN = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "acquisition"
    / "bootstrap-composer-semantics.plan.json"
)
BOOTSTRAP_COMPOSER_SEMANTICS_ANALYZER = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "scripts"
    / "bootstrap_composer_semantics.py"
)
BOOTSTRAP_COMPOSER_VALIDATION_PLAN = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "acquisition"
    / "bootstrap-composer-validation.plan.json"
)
BOOTSTRAP_COMPOSER_VALIDATION_ANALYZER = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "scripts"
    / "bootstrap_composer_validation.py"
)
SCHEMA_GENERALIZATION_JOB = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "scripts"
    / "dev"
    / "SchemaGeneralization.DevJob.ps1"
)
SCHEMA_GENERALIZATION_PLAN = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "acquisition"
    / "schema-generalization.plan.json"
)
SCHEMA_GENERALIZATION_ANALYZER = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "schema_generalization.py"
)
SAFE_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
SAFE_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SAFE_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")
SAFE_WINDOWS_COMMAND_PATH = re.compile(
    r"^(?:[A-Za-z]:\\(?:[A-Za-z0-9._-]+(?:\\[A-Za-z0-9._-]+)*)?"
    r"|\\\\[A-Za-z0-9._-]+\\[A-Za-z0-9._-]+(?:\\[A-Za-z0-9._-]+)*)$"
)
ALLOWED_JOBS = (
    "provider-probe",
    "create-empty",
    "opening-matrix",
    "allocation-map",
    "catalog",
    "table-definition",
    "row",
    "value",
    "index",
    "bootstrap-layout",
    "system-catalog",
    "long-value-maps",
    "long-value-maps-followup",
    "bootstrap-composer-semantics",
    "bootstrap-composer-validation",
    "schema-generalization",
)


class DevClientError(RuntimeError):
    """A local request or returned development result is invalid."""


def reject_duplicate_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DevClientError(f"JSON object contains duplicate field {key!r}")
        result[key] = value
    return result


def load_unique_json(raw: bytes, what: str) -> dict[str, object]:
    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicate_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DevClientError(f"{what} is unreadable") from error
    if not isinstance(value, dict):
        raise DevClientError(f"{what} is malformed")
    return value


class PlanBinding(NamedTuple):
    """A preregistered job: its plan, analyzer, and result file names."""

    job: str
    plan: Path
    analyzer: Path
    document_type: str

    @property
    def analyzer_relative(self) -> str:
        return self.analyzer.relative_to(ROOT).as_posix()

    @property
    def job_result_name(self) -> str:
        return f"{self.job}-job-result.json"

    @property
    def report_name(self) -> str:
        return f"{self.job}-report.json"


def plan_binding(job: str) -> PlanBinding | None:
    if job == "bootstrap-layout":
        return PlanBinding(
            job,
            BOOTSTRAP_LAYOUT_PLAN,
            BOOTSTRAP_LAYOUT_ANALYZER,
            "dao_bootstrap_layout_sufficiency_plan",
        )
    if job == "system-catalog":
        return PlanBinding(
            job, SYSTEM_CATALOG_PLAN, SYSTEM_CATALOG_ANALYZER, "dao_system_catalog_plan"
        )
    if job == "long-value-maps":
        return PlanBinding(
            job, LONG_VALUE_MAPS_PLAN, SYSTEM_CATALOG_ANALYZER, "dao_long_value_maps_plan"
        )
    if job == "long-value-maps-followup":
        return PlanBinding(
            job,
            LONG_VALUE_MAPS_FOLLOWUP_PLAN,
            SYSTEM_CATALOG_ANALYZER,
            "dao_long_value_maps_followup_plan",
        )
    if job == "bootstrap-composer-semantics":
        return PlanBinding(
            job,
            BOOTSTRAP_COMPOSER_SEMANTICS_PLAN,
            BOOTSTRAP_COMPOSER_SEMANTICS_ANALYZER,
            "dao_bootstrap_composer_semantics_plan",
        )
    if job == "bootstrap-composer-validation":
        return PlanBinding(
            job,
            BOOTSTRAP_COMPOSER_VALIDATION_PLAN,
            BOOTSTRAP_COMPOSER_VALIDATION_ANALYZER,
            "dao_bootstrap_composer_validation_plan",
        )
    if job == "schema-generalization":
        return PlanBinding(
            job,
            SCHEMA_GENERALIZATION_PLAN,
            SCHEMA_GENERALIZATION_ANALYZER,
            "dao_schema_generalization_plan",
        )
    return None


def verified_plan_sha256(binding: PlanBinding) -> str:
    job = binding.job
    try:
        plan_bytes = binding.plan.read_bytes()
        plan = load_unique_json(plan_bytes, f"{job} plan")
    except OSError as error:
        raise DevClientError(f"{job} plan is unreadable") from error
    if (
        not isinstance(plan, dict)
        or plan.get("document_type") != binding.document_type
        or plan.get("issue") != 100
        or plan.get("development_only") is not True
    ):
        raise DevClientError(f"{job} plan is malformed")
    inputs = plan.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise DevClientError(f"{job} plan has no pinned inputs")
    for relative, expected in inputs.items():
        if (
            not isinstance(relative, str)
            or not isinstance(expected, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
        ):
            raise DevClientError(f"{job} input pin is malformed")
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise DevClientError(f"{job} input is missing or outside the repository")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise DevClientError(f"{job} input differs from its plan: {relative}")
    manifest_pin = plan.get("candidate_source_manifest")
    if manifest_pin is None:
        if job == "bootstrap-composer-validation":
            raise DevClientError(f"{job} candidate source manifest is missing")
        return hashlib.sha256(plan_bytes).hexdigest()
    if not isinstance(manifest_pin, dict) or set(manifest_pin) != {"path", "sha256"}:
        raise DevClientError(f"{job} candidate source manifest pin is malformed")
    manifest_relative = manifest_pin["path"]
    manifest_digest = manifest_pin["sha256"]
    if (
        not isinstance(manifest_relative, str)
        or not isinstance(manifest_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", manifest_digest)
    ):
        raise DevClientError(f"{job} candidate source manifest pin is malformed")
    manifest_path = (ROOT / manifest_relative).resolve()
    if not manifest_path.is_relative_to(ROOT) or not manifest_path.is_file():
        raise DevClientError(f"{job} candidate source manifest is missing")
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_digest:
        raise DevClientError(f"{job} candidate source manifest differs from its plan")
    manifest = load_unique_json(manifest_bytes, f"{job} candidate source manifest")
    if set(manifest) != {"document_type", "files"} or manifest.get("document_type") != "bootstrap_composer_candidate_sources":
        raise DevClientError(f"{job} candidate source manifest is malformed")
    candidate_sources = manifest.get("files")
    required = {
        "Cargo.lock",
        "Cargo.toml",
        "crates/jet3/Cargo.toml",
        "rust-toolchain.toml",
        *(path.relative_to(ROOT).as_posix() for path in (ROOT / "crates/jet3/src").glob("*.rs")),
    }
    if not isinstance(candidate_sources, dict) or set(candidate_sources) != required:
        raise DevClientError(f"{job} candidate source inventory is incomplete")
    for relative, expected in candidate_sources.items():
        if (
            not isinstance(relative, str)
            or not isinstance(expected, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
        ):
            raise DevClientError(f"{job} candidate source pin is malformed")
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise DevClientError(f"{job} candidate source is missing")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise DevClientError(
                f"{job} candidate source differs from its plan: {relative}"
            )
    return hashlib.sha256(plan_bytes).hexdigest()


def generate_bootstrap_candidates(staging: Path, plan: dict[str, object]) -> None:
    candidate_root = staging / ".bootstrap-candidates"
    candidate_root.mkdir()
    environment = os.environ.copy()
    environment["JET3_BOOTSTRAP_CANDIDATE_DIR"] = str(candidate_root)
    completed = subprocess.run(
        [
            "cargo",
            "test",
            "-p",
            "jet3",
            "--lib",
            "bootstrap_composer::tests::export_dao_validation_candidates",
            "--",
            "--ignored",
            "--exact",
        ],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise DevClientError("bootstrap candidate exporter failed")
    candidates = plan.get("candidates")
    if not isinstance(candidates, dict) or set(candidates) != {"empty", "alpha"}:
        raise DevClientError("bootstrap candidate pins are malformed")
    names: set[str] = set()
    for role, raw in candidates.items():
        if not isinstance(raw, dict) or set(raw) != {"filename", "size", "sha256"}:
            raise DevClientError(f"bootstrap {role} candidate pin is malformed")
        filename = raw["filename"]
        size = raw["size"]
        expected = raw["sha256"]
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not isinstance(expected, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
        ):
            raise DevClientError(f"bootstrap {role} candidate pin is malformed")
        names.add(filename)
        source = candidate_root / filename
        if (
            not source.is_file()
            or source.is_symlink()
            or source.stat().st_size != size
            or hashlib.sha256(source.read_bytes()).hexdigest() != expected
        ):
            raise DevClientError(
                f"generated bootstrap {role} candidate differs from its plan"
            )
        source.rename(staging / filename)
    if any(candidate_root.iterdir()) or names != {
        "bootstrap-composer-empty.mdb",
        "bootstrap-composer-alpha.mdb",
    }:
        raise DevClientError("bootstrap candidate inventory is invalid")
    candidate_root.rmdir()


def canonical_windows_path(value: str, *, label: str) -> str:
    if len(value) > 240 or any(ord(character) < 32 for character in value):
        raise DevClientError(f"{label} is malformed")
    normalized = ntpath.normpath(value)
    drive, tail = ntpath.splitdrive(normalized)
    drive_absolute = bool(re.fullmatch(r"[A-Za-z]:", drive)) and tail.startswith("\\")
    unc_parts = drive[2:].split("\\") if drive.startswith("\\\\") else []
    unc_absolute = (
        len(unc_parts) == 2
        and all(unc_parts)
        and not drive.startswith(("\\\\?\\", "\\\\.\\"))
        and (not tail or tail.startswith("\\"))
    )
    if not drive_absolute and not unc_absolute:
        raise DevClientError(f"{label} must be an absolute drive or UNC path")
    if ".." in [part for part in re.split(r"[\\/]", value) if part]:
        raise DevClientError(f"{label} cannot contain parent traversal")
    if not SAFE_WINDOWS_COMMAND_PATH.fullmatch(normalized):
        raise DevClientError(f"{label} contains a remote-shell-unsafe character")
    return normalized


def validate_args(args: argparse.Namespace) -> None:
    if not SAFE_HOST.fullmatch(args.host):
        raise DevClientError("SSH host must be a DNS name or IPv4 address")
    if not SAFE_USER.fullmatch(args.user or ""):
        raise DevClientError("SSH user is required and malformed")
    if not SAFE_RUN_ID.fullmatch(args.run_id):
        raise DevClientError("run ID is malformed")
    if not 1 <= args.port <= 65535:
        raise DevClientError("SSH port is invalid")
    if not 10 <= args.timeout <= 900:
        raise DevClientError("timeout must be between 10 and 900 seconds")
    args.identity = Path(args.identity).expanduser().resolve() if args.identity else None
    if args.identity is None or not args.identity.is_file():
        raise DevClientError("an existing SSH identity file is required")
    if not args.shared_root:
        raise DevClientError("the host shared root is required")
    args.shared_root = Path(args.shared_root).expanduser().resolve()
    if not args.shared_root.is_dir():
        raise DevClientError("the host shared root must already exist")
    args.remote_shared_root = canonical_windows_path(
        args.remote_shared_root, label="remote shared root"
    )
    binding = plan_binding(args.job)
    args.plan_sha256 = verified_plan_sha256(binding) if binding is not None else ""


def stage_job(args: argparse.Namespace) -> Path:
    inbox = args.shared_root / "inbox"
    output = args.shared_root / "outbox" / args.run_id
    inbox.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    final = inbox / args.run_id
    if final.exists() or output.exists():
        raise DevClientError("run ID already exists in the shared directory")
    staging = inbox / f".{args.run_id}.building.{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        shutil.copyfile(REMOTE_RUNNER, staging / REMOTE_RUNNER.name)
        shutil.copyfile(PROVIDER_PROBE, staging / PROVIDER_PROBE.name)
        shutil.copyfile(CATALOG_JOB, staging / CATALOG_JOB.name)
        shutil.copyfile(TABLE_DEFINITION_JOB, staging / TABLE_DEFINITION_JOB.name)
        shutil.copyfile(TABLE_DEFINITION_TYPES, staging / TABLE_DEFINITION_TYPES.name)
        shutil.copyfile(STAGED_DISPATCH, staging / STAGED_DISPATCH.name)
        shutil.copyfile(STAGED_PUBLICATION, staging / STAGED_PUBLICATION.name)
        shutil.copyfile(ROW_JOB, staging / ROW_JOB.name)
        shutil.copyfile(VALUE_JOB, staging / VALUE_JOB.name)
        shutil.copyfile(INDEX_JOB, staging / INDEX_JOB.name)
        shutil.copyfile(BOOTSTRAP_LAYOUT_JOB, staging / BOOTSTRAP_LAYOUT_JOB.name)
        shutil.copyfile(SYSTEM_CATALOG_JOB, staging / SYSTEM_CATALOG_JOB.name)
        shutil.copyfile(
            BOOTSTRAP_COMPOSER_VALIDATION_JOB,
            staging / BOOTSTRAP_COMPOSER_VALIDATION_JOB.name,
        )
        shutil.copyfile(SCHEMA_GENERALIZATION_JOB, staging / SCHEMA_GENERALIZATION_JOB.name)
        binding = plan_binding(args.job)
        if binding is not None:
            shutil.copyfile(binding.analyzer, staging / binding.analyzer.name)
            shutil.copyfile(binding.plan, staging / binding.plan.name)
            if verified_plan_sha256(binding) != args.plan_sha256:
                raise DevClientError(f"{args.job} plan changed during staging")
            plan = json.loads(binding.plan.read_bytes())
            for relative, expected in plan["inputs"].items():
                staged_input = staging / Path(relative).name
                source_input = ROOT / relative
                if not staged_input.is_file():
                    shutil.copyfile(source_input, staged_input)
                if hashlib.sha256(staged_input.read_bytes()).hexdigest() != expected:
                    raise DevClientError(
                        f"staged {args.job} input differs from its plan: {relative}"
                    )
            if args.job == "bootstrap-composer-validation":
                generate_bootstrap_candidates(staging, plan)
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def remote_job_command(args: argparse.Namespace) -> list[str]:
    remote_input = ntpath.join(args.remote_shared_root, "inbox", args.run_id)
    binding = plan_binding(args.job)
    command = [
        r"%WINDIR%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        ntpath.join(remote_input, REMOTE_RUNNER.name),
        "-Job",
        args.job,
        "-RunId",
        args.run_id,
        "-ProviderProbePath",
        ntpath.join(remote_input, PROVIDER_PROBE.name),
        "-SharedOutputPath",
        ntpath.join(args.remote_shared_root, "outbox", args.run_id),
        "-CatalogJobPath",
        ntpath.join(remote_input, CATALOG_JOB.name),
        "-TableDefinitionJobPath",
        ntpath.join(remote_input, TABLE_DEFINITION_JOB.name),
        "-TableDefinitionTypeInputPath",
        ntpath.join(remote_input, TABLE_DEFINITION_TYPES.name),
        "-DispatchPath",
        ntpath.join(remote_input, STAGED_DISPATCH.name),
        "-PublicationPath",
        ntpath.join(remote_input, STAGED_PUBLICATION.name),
        "-RowJobPath",
        ntpath.join(remote_input, ROW_JOB.name),
        "-ValueJobPath",
        ntpath.join(remote_input, VALUE_JOB.name),
        "-IndexJobPath",
        ntpath.join(remote_input, INDEX_JOB.name),
        "-BootstrapLayoutJobPath",
        ntpath.join(remote_input, BOOTSTRAP_LAYOUT_JOB.name),
        "-SystemCatalogJobPath",
        ntpath.join(remote_input, SYSTEM_CATALOG_JOB.name),
        "-BootstrapComposerValidationJobPath",
        ntpath.join(remote_input, BOOTSTRAP_COMPOSER_VALIDATION_JOB.name),
        "-BootstrapComposerEmptyPath",
        ntpath.join(remote_input, "bootstrap-composer-empty.mdb"),
        "-BootstrapComposerAlphaPath",
        ntpath.join(remote_input, "bootstrap-composer-alpha.mdb"),
        "-SchemaGeneralizationJobPath",
        ntpath.join(remote_input, SCHEMA_GENERALIZATION_JOB.name),
    ]
    if binding is not None:
        command.extend(
            [
                "-PlanSha256",
                args.plan_sha256,
                "-PlanPath",
                ntpath.join(remote_input, binding.plan.name),
            ]
        )
    serialized_units = len(" ".join(command).encode("utf-16-le")) // 2
    if serialized_units > 8000:
        raise DevClientError("remote Windows command exceeds the 8,000-unit bound")
    return command


def ssh_command(args: argparse.Namespace) -> list[str]:
    return [
        "ssh",
        "-p",
        str(args.port),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        str(args.identity),
        f"{args.user}@{args.host}",
        *remote_job_command(args),
    ]


def run_remote(args: argparse.Namespace) -> int:
    try:
        return subprocess.run(
            ssh_command(args),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=args.timeout,
        ).returncode
    except subprocess.TimeoutExpired as error:
        raise DevClientError(f"remote job exceeded {args.timeout} seconds") from error


def analyze_plan_bound_output(args: argparse.Namespace, binding: PlanBinding) -> Path:
    job = binding.job
    if verified_plan_sha256(binding) != args.plan_sha256:
        raise DevClientError(f"{job} plan or input changed during acquisition")
    staged_root = args.shared_root / "inbox" / args.run_id
    staged_analyzer = staged_root / binding.analyzer.name
    plan = json.loads(binding.plan.read_bytes())
    for relative, expected in plan["inputs"].items():
        staged_input = staged_root / Path(relative).name
        if (
            not staged_input.is_file()
            or staged_input.is_symlink()
            or hashlib.sha256(staged_input.read_bytes()).hexdigest() != expected
        ):
            raise DevClientError(
                f"staged {job} input differs before analysis: {relative}"
            )
    if job == "bootstrap-composer-validation":
        for raw in plan["candidates"].values():
            staged_candidate = staged_root / raw["filename"]
            if (
                not staged_candidate.is_file()
                or staged_candidate.is_symlink()
                or staged_candidate.stat().st_size != raw["size"]
                or hashlib.sha256(staged_candidate.read_bytes()).hexdigest()
                != raw["sha256"]
            ):
                raise DevClientError(
                    "staged bootstrap candidate differs before analysis"
                )
    output = args.shared_root / "outbox" / args.run_id
    job_result = output / binding.job_result_name
    report = output / binding.report_name
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(staged_analyzer),
            "--expected-plan-sha256",
            args.plan_sha256,
            "--output",
            str(report),
            str(job_result),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0 or not report.is_file():
        raise DevClientError(f"{job} analyzer rejected the published result")
    return report


def validated_result(args: argparse.Namespace, exit_code: int) -> dict[str, object]:
    result_path = args.shared_root / "outbox" / args.run_id / "result.json"
    if not result_path.is_file() or result_path.is_symlink():
        raise DevClientError("remote job did not publish a regular result")
    try:
        document = json.loads(result_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DevClientError("development result is malformed") from error
    expected = {
        "development_only": True,
        "job": args.job,
        "run_id": args.run_id,
        "status": {0: "pass", 1: "fail", 3: "blocked"}.get(exit_code),
    }
    if plan_binding(args.job) is not None:
        expected["plan_sha256"] = args.plan_sha256
    if not isinstance(document, dict) or any(
        document.get(key) != value for key, value in expected.items()
    ):
        raise DevClientError("development result does not match the requested job")
    return document


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    stage_job(args)
    exit_code = run_remote(args)
    if exit_code not in (0, 1, 3):
        raise DevClientError(f"remote job returned unexpected exit code {exit_code}")
    result = validated_result(args, exit_code)
    report = None
    binding = plan_binding(args.job)
    if binding is not None and exit_code in (0, 1):
        report = analyze_plan_bound_output(args, binding)
    print(
        json.dumps(
            {
                "development_only": True,
                "exit_code": exit_code,
                "job": args.job,
                "output": str(args.shared_root / "outbox" / args.run_id),
                "status": result["status"],
                "report": str(report) if report is not None else None,
            },
            sort_keys=True,
        )
    )
    return exit_code


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("job", choices=ALLOWED_JOBS)
    argument_parser.add_argument(
        "--host", default=os.environ.get("JET3_WINDOWS_HOST", "127.0.0.1")
    )
    argument_parser.add_argument(
        "--port", type=int, default=os.environ.get("JET3_WINDOWS_PORT", "2222")
    )
    argument_parser.add_argument("--user", default=os.environ.get("JET3_WINDOWS_USER"))
    argument_parser.add_argument(
        "--identity", default=os.environ.get("JET3_WINDOWS_IDENTITY")
    )
    argument_parser.add_argument(
        "--shared-root", default=os.environ.get("JET3_WINDOWS_SHARED_ROOT")
    )
    argument_parser.add_argument(
        "--remote-shared-root",
        default=os.environ.get("JET3_WINDOWS_REMOTE_SHARED_ROOT", r"\\host.lan\Data"),
    )
    default_run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-dev-dao"
    argument_parser.add_argument("--run-id", default=default_run_id)
    argument_parser.add_argument("--timeout", type=int, default=180)
    return argument_parser


def main() -> int:
    try:
        return run(parser().parse_args())
    except (DevClientError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
