#!/usr/bin/env python3
"""Run the allowlisted Windows DAO jobs through OpenSSH."""

from __future__ import annotations

import argparse
import base64
from dataclasses import replace
import hashlib
import json
import ntpath
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
from typing import Sequence
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
REMOTE_SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts" / "remote"
ENTRYPOINT = REMOTE_SCRIPTS / "Invoke-Jet3DaoSshJob.ps1"
PROCESS_MODULE = REMOTE_SCRIPTS / "Remote.Process.ps1"
RESULT_PREFIX = "JET3_REMOTE_RESULT="
SAFE_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
SAFE_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SAFE_RUN_ID = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$"
)

sys.path.insert(0, str(TOOLS))
try:
    from validation.windows_dao_archive import (
        ArchiveLimits,
        ArchiveValidationError,
        validate_archive,
    )
finally:
    del sys.path[0]


class ClientError(RuntimeError):
    pass


def run_bounded(
    command: Sequence[str],
    *,
    timeout: int,
    maximum_output: int,
    watched_file: Path | None = None,
    maximum_file_bytes: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if (watched_file is None) != (maximum_file_bytes is None):
        raise ValueError("watched_file and maximum_file_bytes must be provided together")
    process = subprocess.Popen(
        list(command),
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = [bytearray(), bytearray()]
    lock = threading.Lock()
    exceeded = threading.Event()

    def read_stream(index: int, stream: object) -> None:
        while True:
            chunk = stream.read(8192)  # type: ignore[attr-defined]
            if not chunk:
                return
            with lock:
                used = len(output[0]) + len(output[1])
                remaining = maximum_output - used
                if remaining > 0:
                    output[index].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    exceeded.set()
                    return

    assert process.stdout is not None and process.stderr is not None
    threads = (
        threading.Thread(target=read_stream, args=(0, process.stdout)),
        threading.Thread(target=read_stream, args=(1, process.stderr)),
    )
    for thread in threads:
        thread.daemon = True
        thread.start()
    deadline = time.monotonic() + timeout
    oversized_file = False
    timed_out = False
    while process.poll() is None:
        if exceeded.is_set():
            process.kill()
            break
        if watched_file is not None and maximum_file_bytes is not None:
            try:
                oversized_file = watched_file.stat().st_size > maximum_file_bytes
            except FileNotFoundError:
                oversized_file = False
            if oversized_file:
                process.kill()
                break
        if time.monotonic() >= deadline:
            process.kill()
            timed_out = True
            break
        time.sleep(0.05)
    if process.poll() is None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
    process.stdout.close()
    process.stderr.close()
    if watched_file is not None and maximum_file_bytes is not None:
        try:
            oversized_file = (
                oversized_file or watched_file.stat().st_size > maximum_file_bytes
            )
        except FileNotFoundError:
            pass
    if oversized_file:
        assert watched_file is not None and maximum_file_bytes is not None
        watched_file.unlink(missing_ok=True)
        raise ClientError(
            f"download exceeded its {maximum_file_bytes}-byte file limit"
        )
    if timed_out:
        if watched_file is not None:
            watched_file.unlink(missing_ok=True)
        raise ClientError(f"command exceeded its {timeout}-second timeout")
    if exceeded.is_set():
        if watched_file is not None:
            watched_file.unlink(missing_ok=True)
        raise ClientError(
            f"command exceeded its {maximum_output}-byte output limit"
        )
    return subprocess.CompletedProcess(
        list(command), process.returncode, bytes(output[0]), bytes(output[1])
    )


def git(*arguments: str, timeout: int = 30) -> str:
    result = run_bounded(
        ("git", *arguments), timeout=timeout, maximum_output=2 * 1024 * 1024
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ClientError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.decode("utf-8", "strict").strip()


def validate_repository_url(value: str) -> None:
    if len(value) > 512 or any(character.isspace() for character in value):
        raise ClientError("repository URL is malformed")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.query
        or parsed.fragment
    ):
        raise ClientError("repository URL must be credential-free HTTPS")


def exact_pushed_binding(remote: str, repository_url: str | None) -> tuple[str, str]:
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = [entry for entry in status.split("\0") if entry]
    disallowed = [
        entry
        for entry in entries
        if not (entry.startswith("?? ") and entry[3:].startswith("artifacts/"))
    ]
    if disallowed:
        raise ClientError(
            "the local repository must have no tracked changes or untracked "
            "files outside artifacts/"
        )
    commit = git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ClientError("HEAD is not a full lowercase Git commit")
    url = repository_url or git("remote", "get-url", remote)
    validate_repository_url(url)
    advertised = git(
        "ls-remote", "--heads", "--tags", url, timeout=60
    ).splitlines()
    if not any(line.split(maxsplit=1)[0] == commit for line in advertised):
        raise ClientError("HEAD is not advertised by any ref on the selected remote")
    return commit, url


def committed_file_sha256(commit: str, relative: str) -> str:
    result = run_bounded(
        ("git", "show", f"{commit}:{relative}"),
        timeout=30,
        maximum_output=2 * 1024 * 1024,
    )
    if result.returncode != 0:
        raise ClientError(f"could not read {relative} from the bound commit")
    return hashlib.sha256(result.stdout).hexdigest()


def bound_script_hashes(commit: str) -> tuple[str, str]:
    paths = (
        "oracle/windows-dao/scripts/remote/Invoke-Jet3DaoSshJob.ps1",
        "oracle/windows-dao/scripts/remote/Remote.Process.ps1",
    )
    local_hashes = (sha256(ENTRYPOINT), sha256(PROCESS_MODULE))
    committed_hashes = tuple(committed_file_sha256(commit, path) for path in paths)
    if local_hashes != committed_hashes:
        raise ClientError("local remote-automation files do not match the bound commit")
    return local_hashes


def powershell_encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def ssh_base(args: argparse.Namespace) -> list[str]:
    command = [
        "ssh",
        "-p",
        str(args.port),
        "-o",
        f"ConnectTimeout={args.connect_timeout}",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    if args.identity:
        command.extend(("-o", "IdentitiesOnly=yes", "-i", str(args.identity)))
    command.append(f"{args.user}@{args.host}")
    return command


def scp_base(args: argparse.Namespace) -> list[str]:
    command = [
        "scp",
        "-P",
        str(args.port),
        "-o",
        f"ConnectTimeout={args.connect_timeout}",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    if args.identity:
        command.extend(("-o", "IdentitiesOnly=yes", "-i", str(args.identity)))
    return command


def encoded_ssh_command(args: argparse.Namespace, script: str) -> list[str]:
    return [
        *ssh_base(args),
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        powershell_encoded(script),
    ]


def bootstrap_relative(run_id: str) -> str:
    return f".jet3-rs-bootstrap/{run_id}"


def invocation_script(
    *,
    run_id: str,
    job: str,
    repository_url: str,
    commit: str,
    remote_root: str | None,
    timeout: int,
    maximum_output: int,
    maximum_artifact: int,
    entrypoint_sha256: str,
    process_module_sha256: str,
) -> str:
    config = {
        "commit": commit,
        "entrypoint_sha256": entrypoint_sha256,
        "job": job,
        "maximum_artifact": maximum_artifact,
        "maximum_output": maximum_output,
        "remote_root": remote_root or "",
        "repository_url": repository_url,
        "run_id": run_id,
        "process_module_sha256": process_module_sha256,
        "timeout": timeout,
    }
    encoded = base64.b64encode(
        json.dumps(config, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    relative = bootstrap_relative(run_id).replace("/", "\\")
    return (
        "$ErrorActionPreference='Stop';"
        f"$c=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))"
        "|ConvertFrom-Json;"
        f"$bootstrap=Join-Path $HOME '{relative}';"
        "$entry=Join-Path $bootstrap 'Invoke-Jet3DaoSshJob.ps1';"
        "$module=Join-Path $bootstrap 'Remote.Process.ps1';"
        "$code=4;try{"
        "if((Get-FileHash -LiteralPath $entry -Algorithm SHA256).Hash.ToLowerInvariant() "
        "-cne [string]$c.entrypoint_sha256){throw 'Uploaded entrypoint hash mismatch'};"
        "if((Get-FileHash -LiteralPath $module -Algorithm SHA256).Hash.ToLowerInvariant() "
        "-cne [string]$c.process_module_sha256){throw 'Uploaded process module hash mismatch'};"
        "& $entry -Mode Bootstrap -Job ([string]$c.job) "
        "-RepositoryUrl ([string]$c.repository_url) "
        "-GitCommit ([string]$c.commit) -RunId ([string]$c.run_id) "
        "-RemoteRoot ([string]$c.remote_root) "
        "-TimeoutSeconds ([int]$c.timeout) "
        "-MaximumOutputBytes ([long]$c.maximum_output) "
        "-MaximumArtifactBytes ([long]$c.maximum_artifact);$code=$LASTEXITCODE}"
        "catch{[Console]::Error.WriteLine('ERROR: '+$_.Exception.Message)}"
        "finally{if(Test-Path -LiteralPath $bootstrap){"
        "Remove-Item -LiteralPath $bootstrap -Recurse -Force}};exit $code"
    )


def parse_remote_result(stdout: bytes) -> dict[str, object]:
    lines = stdout.decode("utf-8", "replace").splitlines()
    markers = [line[len(RESULT_PREFIX) :] for line in lines if line.startswith(RESULT_PREFIX)]
    if len(markers) != 1:
        raise ClientError("remote execution did not return exactly one result record")
    try:
        document = json.loads(base64.b64decode(markers[0], validate=True))
    except (ValueError, json.JSONDecodeError) as error:
        raise ClientError("remote result record is malformed") from error
    if not isinstance(document, dict):
        raise ClientError("remote result record is not an object")
    return document


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_windows_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise ClientError(f"{label} is malformed")
    if any(ord(character) < 32 for character in value):
        raise ClientError(f"{label} is malformed")
    parts = [part for part in re.split(r"[\\/]", value) if part]
    if ".." in parts:
        raise ClientError(f"{label} is malformed")
    normalized = ntpath.normpath(value)
    drive, tail = ntpath.splitdrive(normalized)
    if not re.fullmatch(r"[A-Za-z]:", drive) or not tail.startswith("\\"):
        raise ClientError(f"{label} is malformed")
    if tail == "\\":
        raise ClientError(f"{label} cannot be a volume root")
    return normalized


def validated_archive_identity(
    result: dict[str, object],
    *,
    commit: str,
    run_id: str,
    maximum_bytes: int,
    requested_remote_root: str | None,
) -> tuple[str, int, str]:
    archive = result.get("archive_path")
    archive_path = canonical_windows_path(archive, label="remote archive path")
    remote_root = canonical_windows_path(
        result.get("remote_root"), label="resolved remote root"
    )
    if requested_remote_root is not None:
        requested = canonical_windows_path(
            requested_remote_root, label="requested remote root"
        )
        if ntpath.normcase(remote_root) != ntpath.normcase(requested):
            raise ClientError("resolved remote root does not match the request")
    expected_archive = ntpath.join(
        remote_root, "runs", commit, run_id, "artifacts.zip"
    )
    if ntpath.normcase(archive_path) != ntpath.normcase(expected_archive):
        raise ClientError("remote archive path is outside the bound run directory")
    archive_size = result.get("archive_size")
    archive_hash = result.get("archive_sha256")
    if (
        not isinstance(archive_size, int)
        or isinstance(archive_size, bool)
        or not 0 < archive_size <= maximum_bytes
        or not isinstance(archive_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", archive_hash) is None
    ):
        raise ClientError("remote archive identity is malformed")
    return archive_path, archive_size, archive_hash


def validate_remote_result_identity(
    result: dict[str, object],
    *,
    commit: str,
    run_id: str,
    job: str,
    process_exit_code: int,
) -> None:
    if result.get("commit") != commit or result.get("run_id") != run_id:
        raise ClientError("remote result identity does not match the request")
    if result.get("job") != job:
        raise ClientError("remote result job does not match the request")
    if result.get("exit_code") != process_exit_code:
        raise ClientError("remote process and result exit codes disagree")


def validate_downloaded_archive(
    archive: Path,
    *,
    job: str,
    commit: str,
    run_id: str,
    exit_code: int,
    maximum_bytes: int,
) -> None:
    baseline = ArchiveLimits()
    limits = replace(
        baseline,
        maximum_archive_bytes=maximum_bytes,
        maximum_central_directory_bytes=min(
            baseline.maximum_central_directory_bytes, maximum_bytes
        ),
        maximum_entry_uncompressed_bytes=min(
            baseline.maximum_entry_uncompressed_bytes, maximum_bytes
        ),
        maximum_entry_compressed_bytes=min(
            baseline.maximum_entry_compressed_bytes, maximum_bytes
        ),
        maximum_total_uncompressed_bytes=maximum_bytes,
        maximum_total_compressed_bytes=maximum_bytes,
    )
    try:
        validate_archive(
            archive,
            mode=job,
            expected_commit=commit,
            expected_run_id=run_id,
            expected_exit_code=exit_code,
            limits=limits,
        )
    except ArchiveValidationError as error:
        archive.unlink(missing_ok=True)
        raise ClientError(
            f"downloaded artifact is structurally invalid: {error}"
        ) from error


def validate_args(args: argparse.Namespace) -> None:
    if not SAFE_HOST.fullmatch(args.host):
        raise ClientError("SSH host must be a DNS name or IPv4 address")
    if not SAFE_USER.fullmatch(args.user):
        raise ClientError("SSH user is invalid")
    if not SAFE_RUN_ID.fullmatch(args.run_id):
        raise ClientError("run ID is not protocol-valid")
    if args.remote_root is None:
        args.remote_root = rf"C:\Users\{args.user}\AppData\Local\jet3-rs-ssh"
    if (
        len(args.remote_root) > 200
        or any(ord(character) < 32 for character in args.remote_root)
    ):
        raise ClientError("remote root is too long or contains control characters")
    args.remote_root = canonical_windows_path(
        args.remote_root, label="requested remote root"
    )
    if not 1 <= args.port <= 65535:
        raise ClientError("SSH port is invalid")
    if not 10 <= args.process_timeout <= 120:
        raise ClientError("remote process timeout must be between 10 and 120 seconds")
    if not 1 <= args.connect_timeout <= 60:
        raise ClientError("SSH connection timeout must be between 1 and 60 seconds")
    if not 30 <= args.client_timeout <= 900:
        raise ClientError("client timeout must be between 30 and 900 seconds")
    if not 30 <= args.transfer_timeout <= 3600:
        raise ClientError("transfer timeout must be between 30 and 3600 seconds")
    if not 4096 <= args.maximum_remote_output <= 1024 * 1024:
        raise ClientError("remote output limit must be between 4 KiB and 1 MiB")
    if not 4096 <= args.maximum_client_output <= 8 * 1024 * 1024:
        raise ClientError("client output limit must be between 4 KiB and 8 MiB")
    if not 1024 * 1024 <= args.maximum_artifact_bytes <= 1024**3:
        raise ClientError("artifact limit must be between 1 MiB and 1 GiB")
    if args.identity:
        args.identity = args.identity.expanduser().resolve()
        if not args.identity.is_file():
            raise ClientError("SSH identity file does not exist")
    if args.output.exists():
        raise ClientError("output path already exists")


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    commit, repository_url = exact_pushed_binding(
        args.git_remote, args.repository_url
    )
    entrypoint_hash, process_module_hash = bound_script_hashes(commit)
    relative = bootstrap_relative(args.run_id)
    windows_relative = relative.replace("/", "\\")
    create_script = (
        "$ErrorActionPreference='Stop';"
        f"$p=Join-Path $HOME '{windows_relative}';"
        "if(Test-Path -LiteralPath $p){exit 2};"
        "[void](New-Item -ItemType Directory -Path $p -Force:$false)"
    )
    create_command = encoded_ssh_command(args, create_script)
    remote_target = f"{args.user}@{args.host}:{relative}/"
    upload_command = [
        *scp_base(args),
        str(ENTRYPOINT),
        str(PROCESS_MODULE),
        remote_target,
    ]
    execute_command = encoded_ssh_command(
        args,
        invocation_script(
            run_id=args.run_id,
            job=args.job,
            repository_url=repository_url,
            commit=commit,
            remote_root=args.remote_root,
            timeout=args.process_timeout,
            maximum_output=args.maximum_remote_output,
            maximum_artifact=args.maximum_artifact_bytes,
            entrypoint_sha256=entrypoint_hash,
            process_module_sha256=process_module_hash,
        ),
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "commit": commit,
                    "create": shlex.join(create_command),
                    "execute": shlex.join(execute_command),
                    "job": args.job,
                    "repository_url": repository_url,
                    "run_id": args.run_id,
                    "upload": shlex.join(upload_command),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    created = run_bounded(
        create_command,
        timeout=args.client_timeout,
        maximum_output=args.maximum_client_output,
    )
    if created.returncode != 0:
        raise ClientError("could not create the exclusive remote bootstrap directory")
    uploaded = run_bounded(
        upload_command,
        timeout=args.client_timeout,
        maximum_output=args.maximum_client_output,
    )
    if uploaded.returncode != 0:
        detail = uploaded.stderr.decode("utf-8", "replace").strip()
        raise ClientError(f"remote bootstrap upload failed: {detail}")
    executed = run_bounded(
        execute_command,
        timeout=args.client_timeout,
        maximum_output=args.maximum_client_output,
    )
    result = parse_remote_result(executed.stdout)
    validate_remote_result_identity(
        result,
        commit=commit,
        run_id=args.run_id,
        job=args.job,
        process_exit_code=executed.returncode,
    )
    downloadable = result.get("downloadable") is True
    exit_code = result.get("exit_code")
    if executed.returncode not in (0, 1, 3) or not downloadable:
        reason = str(result.get("reason", "remote job was not downloadable"))
        raise ClientError(f"remote job failed without artifacts: {reason}")
    archive, archive_size, archive_hash = validated_archive_identity(
        result,
        commit=commit,
        run_id=args.run_id,
        maximum_bytes=args.maximum_artifact_bytes,
        requested_remote_root=args.remote_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    remote_archive = archive.replace("\\", "/")
    download_command = [
        *scp_base(args),
        f"{args.user}@{args.host}:'{remote_archive}'",
        str(args.output),
    ]
    downloaded = run_bounded(
        download_command,
        timeout=args.transfer_timeout,
        maximum_output=args.maximum_client_output,
        watched_file=args.output,
        maximum_file_bytes=archive_size,
    )
    if downloaded.returncode != 0:
        args.output.unlink(missing_ok=True)
        raise ClientError("artifact download failed")
    if args.output.stat().st_size != archive_size:
        args.output.unlink(missing_ok=True)
        raise ClientError("downloaded artifact size does not match the remote result")
    if args.output.stat().st_size > args.maximum_artifact_bytes:
        args.output.unlink(missing_ok=True)
        raise ClientError("downloaded artifact exceeds its byte ceiling")
    if sha256(args.output) != archive_hash:
        args.output.unlink(missing_ok=True)
        raise ClientError("downloaded artifact hash does not match the remote result")
    validate_downloaded_archive(
        args.output,
        job=args.job,
        commit=commit,
        run_id=args.run_id,
        exit_code=executed.returncode,
        maximum_bytes=args.maximum_artifact_bytes,
    )
    print(json.dumps({**result, "local_archive": str(args.output)}, sort_keys=True))
    return int(exit_code)


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "job", choices=("provider-probe", "m1-controlled")
    )
    argument_parser.add_argument("--host", required=True)
    argument_parser.add_argument("--user", required=True)
    argument_parser.add_argument("--identity", type=Path)
    argument_parser.add_argument("--port", type=int, default=22)
    argument_parser.add_argument("--git-remote", default="origin")
    argument_parser.add_argument("--repository-url")
    default_run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-ssh-dao"
    argument_parser.add_argument("--run-id", default=default_run_id)
    argument_parser.add_argument("--remote-root")
    argument_parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "windows-dao-ssh" / f"{default_run_id}.zip",
    )
    argument_parser.add_argument("--process-timeout", type=int, default=120)
    argument_parser.add_argument("--client-timeout", type=int, default=420)
    argument_parser.add_argument("--transfer-timeout", type=int, default=600)
    argument_parser.add_argument("--connect-timeout", type=int, default=15)
    argument_parser.add_argument(
        "--maximum-remote-output", type=int, default=1024 * 1024
    )
    argument_parser.add_argument(
        "--maximum-client-output", type=int, default=2 * 1024 * 1024
    )
    argument_parser.add_argument(
        "--maximum-artifact-bytes", type=int, default=300 * 1024 * 1024
    )
    argument_parser.add_argument("--dry-run", action="store_true")
    return argument_parser


def main() -> int:
    try:
        return run(parser().parse_args())
    except (ClientError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
