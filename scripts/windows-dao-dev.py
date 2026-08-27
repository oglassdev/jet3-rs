#!/usr/bin/env python3
"""Run explicitly exploratory DAO jobs in a local Windows VM."""

from __future__ import annotations

import argparse
import base64
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
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
SAFE_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
SAFE_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SAFE_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")
ALLOWED_JOBS = (
    "provider-probe",
    "create-empty",
    "opening-matrix",
    "allocation-map",
    "catalog",
    "table-definition",
)


class DevClientError(RuntimeError):
    """A local request or returned development result is invalid."""


def powershell_encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


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
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def invocation_script(args: argparse.Namespace) -> str:
    remote_input = ntpath.join(args.remote_shared_root, "inbox", args.run_id)
    config = {
        "job": args.job,
        "run_id": args.run_id,
        "runner": ntpath.join(remote_input, REMOTE_RUNNER.name),
        "probe": ntpath.join(remote_input, PROVIDER_PROBE.name),
        "catalog_job": ntpath.join(remote_input, CATALOG_JOB.name),
        "table_definition_job": ntpath.join(remote_input, TABLE_DEFINITION_JOB.name),
        "table_definition_types": ntpath.join(remote_input, TABLE_DEFINITION_TYPES.name),
        "output": ntpath.join(args.remote_shared_root, "outbox", args.run_id),
    }
    encoded = base64.b64encode(
        json.dumps(config, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return (
        "$ErrorActionPreference='Stop';"
        f"$c=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))"
        "|ConvertFrom-Json;"
        "$winps=Join-Path $env:WINDIR "
        "'SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "& $winps -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        "-File ([string]$c.runner) -Job ([string]$c.job) "
        "-RunId ([string]$c.run_id) -ProviderProbePath ([string]$c.probe) "
        "-SharedOutputPath ([string]$c.output) "
        "-CatalogJobPath ([string]$c.catalog_job) "
        "-TableDefinitionJobPath ([string]$c.table_definition_job) "
        "-TableDefinitionTypeInputPath ([string]$c.table_definition_types);"
        "exit $LASTEXITCODE"
    )


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
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        powershell_encoded(invocation_script(args)),
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
    print(
        json.dumps(
            {
                "development_only": True,
                "exit_code": exit_code,
                "job": args.job,
                "output": str(args.shared_root / "outbox" / args.run_id),
                "status": result["status"],
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
