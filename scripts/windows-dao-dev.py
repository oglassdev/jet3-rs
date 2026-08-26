#!/usr/bin/env python3
"""Run bounded, explicitly exploratory DAO jobs in a local Windows VM."""

from __future__ import annotations

import argparse
import base64
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
PROVIDER_PROBE = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "probe-provider.ps1"
)
SAFE_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
SAFE_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SAFE_RUN_ID = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$"
)
ALLOWED_JOBS = ("provider-probe", "create-empty")
MAXIMUM_MANIFEST_BYTES = 1024 * 1024
MAXIMUM_FILE_BYTES = 64 * 1024 * 1024
MAXIMUM_TOTAL_BYTES = 128 * 1024 * 1024
MAXIMUM_COMMAND_OUTPUT = 1024 * 1024


class DevClientError(RuntimeError):
    """A local request or returned development artifact is invalid."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise DevClientError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.decode("utf-8", "strict").strip()


def powershell_encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def canonical_windows_path(value: str, *, label: str) -> str:
    if len(value) > 240 or any(ord(character) < 32 for character in value):
        raise DevClientError(f"{label} is malformed")
    normalized = ntpath.normpath(value)
    drive, tail = ntpath.splitdrive(normalized)
    if not re.fullmatch(r"[A-Za-z]:", drive) or not tail.startswith("\\"):
        raise DevClientError(f"{label} must be an absolute drive path")
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


def request_document(job: str, run_id: str) -> dict[str, object]:
    commit = git(["rev-parse", "HEAD"])
    dirty = bool(git(["status", "--porcelain=v1", "--untracked-files=all"]))
    return {
        "schema_version": 1,
        "document_type": "jet3_windows_dev_request",
        "development_only": True,
        "job": job,
        "run_id": run_id,
        "requested_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "client": {"git_commit": commit, "dirty": dirty},
        "sources": {
            "runner": {
                "path": REMOTE_RUNNER.name,
                "sha256": sha256(REMOTE_RUNNER),
            },
            "provider_probe": {
                "path": PROVIDER_PROBE.name,
                "sha256": sha256(PROVIDER_PROBE),
            },
        },
    }


def stage_request(args: argparse.Namespace) -> Path:
    inbox = args.shared_root / "inbox"
    outbox = args.shared_root / "outbox"
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    final = inbox / args.run_id
    output = outbox / args.run_id
    if final.exists() or output.exists():
        raise DevClientError("run ID already exists in the shared directory")
    staging = inbox / f".{args.run_id}.building.{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        shutil.copyfile(REMOTE_RUNNER, staging / REMOTE_RUNNER.name)
        shutil.copyfile(PROVIDER_PROBE, staging / PROVIDER_PROBE.name)
        document = request_document(args.job, args.run_id)
        encoded = json.dumps(document, indent=2, sort_keys=True) + "\n"
        (staging / "request.json").write_text(encoded, encoding="utf-8")
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def invocation_script(args: argparse.Namespace) -> str:
    remote_input = ntpath.join(args.remote_shared_root, "inbox", args.run_id)
    remote_output = ntpath.join(args.remote_shared_root, "outbox", args.run_id)
    config = {
        "request": ntpath.join(remote_input, "request.json"),
        "runner": ntpath.join(remote_input, REMOTE_RUNNER.name),
        "output": remote_output,
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
        "-File ([string]$c.runner) -RequestPath ([string]$c.request) "
        "-SharedOutputPath ([string]$c.output);exit $LASTEXITCODE"
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
        result = subprocess.run(
            ssh_command(args),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise DevClientError(f"remote job exceeded {args.timeout} seconds") from error
    if len(result.stdout) + len(result.stderr) > MAXIMUM_COMMAND_OUTPUT:
        raise DevClientError("remote command output exceeded 1 MiB")
    if result.stdout:
        sys.stdout.buffer.write(result.stdout)
    if result.stderr:
        sys.stderr.buffer.write(result.stderr)
    return result.returncode


def validated_manifest(args: argparse.Namespace) -> dict[str, object]:
    output = args.shared_root / "outbox" / args.run_id
    manifest_path = output / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise DevClientError("remote job did not publish a regular manifest")
    if manifest_path.stat().st_size > MAXIMUM_MANIFEST_BYTES:
        raise DevClientError("development manifest exceeds 1 MiB")
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DevClientError("development manifest is malformed") from error
    if not isinstance(document, dict):
        raise DevClientError("development manifest must be an object")
    expected = {
        "document_type": "jet3_windows_dev_manifest",
        "development_only": True,
        "job": args.job,
        "run_id": args.run_id,
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise DevClientError(f"development manifest has incorrect {key}")
    if document.get("status") not in ("pass", "fail", "blocked"):
        raise DevClientError("development manifest has invalid status")
    files = document.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 16:
        raise DevClientError("development manifest file inventory is invalid")
    declared: set[str] = set()
    total = 0
    for record in files:
        if not isinstance(record, dict):
            raise DevClientError("development manifest file entry is invalid")
        relative = record.get("path")
        expected_hash = record.get("sha256")
        expected_size = record.get("size")
        if (
            not isinstance(relative, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", relative)
            or relative in declared
            or not isinstance(expected_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or not 0 <= expected_size <= MAXIMUM_FILE_BYTES
        ):
            raise DevClientError("development manifest file entry is invalid")
        path = output / relative
        if not path.is_file() or path.is_symlink():
            raise DevClientError(f"declared output is not a regular file: {relative}")
        actual_size = path.stat().st_size
        total += actual_size
        if total > MAXIMUM_TOTAL_BYTES:
            raise DevClientError("development outputs exceed 128 MiB")
        if actual_size != expected_size or sha256(path) != expected_hash:
            raise DevClientError(f"declared output identity differs: {relative}")
        declared.add(relative)
    actual = {
        path.name
        for path in output.iterdir()
        if path.name != "manifest.json"
    }
    if actual != declared:
        raise DevClientError("development output inventory is not closed")
    return document


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    stage_request(args)
    exit_code = run_remote(args)
    manifest = validated_manifest(args)
    print(
        json.dumps(
            {
                "development_only": True,
                "exit_code": exit_code,
                "job": args.job,
                "output": str(args.shared_root / "outbox" / args.run_id),
                "status": manifest.get("status"),
            },
            sort_keys=True,
        )
    )
    if exit_code not in (0, 1, 3):
        raise DevClientError(f"remote job returned unexpected exit code {exit_code}")
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
    argument_parser.add_argument(
        "--user", default=os.environ.get("JET3_WINDOWS_USER")
    )
    argument_parser.add_argument(
        "--identity", default=os.environ.get("JET3_WINDOWS_IDENTITY")
    )
    argument_parser.add_argument(
        "--shared-root", default=os.environ.get("JET3_WINDOWS_SHARED_ROOT")
    )
    argument_parser.add_argument(
        "--remote-shared-root",
        default=os.environ.get("JET3_WINDOWS_REMOTE_SHARED_ROOT", "Z:\\"),
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
