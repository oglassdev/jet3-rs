#!/usr/bin/env python3
"""Record immutable acceptance gate results and build deterministic summaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

GATES = tuple(f"G{number}" for number in range(9))
STATUSES = ("PASS", "FAIL", "BLOCKED")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_PATH = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)


class ReportError(ValueError):
    """An acceptance report is unsafe, stale, inconsistent, or incomplete."""


def _git(repo_root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )


def repository_root(path: Path) -> Path:
    candidate = path.resolve()
    result = _git(candidate, ["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise ReportError(f"not a git repository: {candidate}")
    root = Path(result.stdout.strip()).resolve()
    if candidate != root:
        raise ReportError(f"repository root must be {root}, got {candidate}")
    return root


def git_state(repo_root: Path) -> tuple[str, bool]:
    commit_result = _git(repo_root, ["rev-parse", "HEAD"])
    if commit_result.returncode != 0:
        raise ReportError("cannot determine git commit")
    commit = commit_result.stdout.strip()
    if not GIT_COMMIT.fullmatch(commit):
        raise ReportError(f"invalid git commit returned by git: {commit!r}")

    # Acceptance output is generated after the source-tree cleanliness check and
    # must not make later reports dirty merely by existing.
    status_result = _git(
        repo_root,
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".",
            ":(exclude)artifacts/acceptance/**",
        ],
    )
    if status_result.returncode != 0:
        raise ReportError("cannot determine git worktree state")
    return commit, bool(status_result.stdout)


def _safe_relative_path(raw_path: str, field: str) -> PurePosixPath:
    if not isinstance(raw_path, str) or not REPOSITORY_PATH.fullmatch(raw_path):
        raise ReportError(f"{field} must be a safe repository-relative path")
    if "\\" in raw_path:
        raise ReportError(f"{field} must use forward slashes")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReportError(f"{field} is unsafe: {raw_path!r}")
    return path


def _run_relative_root(commit: str, run_id: str) -> PurePosixPath:
    return PurePosixPath("artifacts", "acceptance", commit, run_id)


def _resolve_artifact(
    repo_root: Path,
    raw_path: str,
    run_relative_root: PurePosixPath,
    field: str,
) -> tuple[Path, str]:
    relative = _safe_relative_path(raw_path, field)
    try:
        relative.relative_to(run_relative_root)
    except ValueError as error:
        raise ReportError(
            f"{field} must be beneath {run_relative_root.as_posix()}"
        ) from error

    candidate = repo_root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise ReportError(f"{field} does not exist: {raw_path!r}") from error
    try:
        resolved.relative_to(repo_root)
    except ValueError as error:
        raise ReportError(f"{field} escapes the repository") from error
    run_root = repo_root.joinpath(*run_relative_root.parts).resolve()
    try:
        resolved.relative_to(run_root)
    except ValueError as error:
        raise ReportError(f"{field} escapes the acceptance run directory") from error
    if not resolved.is_file():
        raise ReportError(f"{field} must be a regular file")
    return resolved, relative.as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path, relative_path: str) -> dict[str, Any]:
    return {
        "path": relative_path,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _canonical_json(document: Any) -> bytes:
    return (
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        # Windows and some filesystems do not permit opening a directory.
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_immutable_json(path: Path, document: Any) -> None:
    encoded = _canonical_json(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != encoded:
            raise ReportError(f"refusing to overwrite immutable report {path}")
        return

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != encoded:
                raise ReportError(f"concurrent conflicting report at {path}") from None
        _fsync_directory(path.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _load_json(path: Path, description: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError as error:
        raise ReportError(f"missing {description}: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ReportError(f"invalid {description} {path}: {error}") from error


def _parse_timestamp(raw: str, field: str) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise ReportError(f"{field} must be an RFC 3339 timestamp")
    value = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ReportError(f"{field} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReportError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    rendered = value.isoformat(timespec="microseconds")
    return rendered.removesuffix("+00:00") + "Z"


def _duration_ms(started: datetime, finished: datetime) -> int:
    delta = finished - started
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ReportError(
            "run ID must start with an ASCII alphanumeric and contain only "
            "ASCII alphanumerics, '.', '_', or '-'"
        )


def _validate_command(command: Iterable[str]) -> list[str]:
    argv = list(command)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise ReportError("command argv must not be empty")
    if any(not isinstance(argument, str) or "\x00" in argument for argument in argv):
        raise ReportError("command argv must contain NUL-free strings")
    return argv


def _validate_outcome(status: str, reason: str, exit_code: int) -> None:
    if status not in STATUSES:
        raise ReportError(f"unknown gate status {status!r}")
    if not isinstance(reason, str) or not reason.strip():
        raise ReportError("every gate result requires a non-empty reason")
    if type(exit_code) is not int or exit_code < 0:
        raise ReportError("exit code must be a non-negative integer")
    if status == "PASS" and exit_code != 0:
        raise ReportError("PASS requires exit code 0")
    if status in {"FAIL", "BLOCKED"} and exit_code == 0:
        raise ReportError(f"{status} requires a nonzero exit code")


def _paths(
    repo_root: Path, commit: str, run_id: str
) -> tuple[PurePosixPath, Path]:
    relative = _run_relative_root(commit, run_id)
    return relative, repo_root.joinpath(*relative.parts)


def record_gate(
    *,
    repo_root: Path,
    run_id: str,
    gate: str,
    status: str,
    reason: str,
    command: Iterable[str],
    exit_code: int,
    started_at: str,
    finished_at: str,
    stdout_artifact: str,
    stderr_artifact: str,
    expected_commit: str | None = None,
    require_clean: bool = False,
) -> Path:
    """Record one immutable, commit-bound acceptance gate result."""
    root = repository_root(repo_root)
    _validate_run_id(run_id)
    if gate not in GATES:
        raise ReportError(f"unknown acceptance gate {gate!r}")
    _validate_outcome(status, reason, exit_code)
    argv = _validate_command(command)

    commit, dirty = git_state(root)
    if expected_commit is not None and expected_commit != commit:
        raise ReportError(
            f"stale commit: expected {expected_commit}, repository is {commit}"
        )
    if require_clean and dirty:
        raise ReportError("clean acceptance evidence cannot be recorded from a dirty tree")

    started = _parse_timestamp(started_at, "started_at")
    finished = _parse_timestamp(finished_at, "finished_at")
    if finished < started:
        raise ReportError("finished_at precedes started_at")
    duration_ms = _duration_ms(started, finished)

    run_relative, run_root = _paths(root, commit, run_id)
    stdout_path, stdout_relative = _resolve_artifact(
        root, stdout_artifact, run_relative, "stdout artifact"
    )
    stderr_path, stderr_relative = _resolve_artifact(
        root, stderr_artifact, run_relative, "stderr artifact"
    )
    if stdout_relative == stderr_relative:
        raise ReportError("stdout and stderr artifacts must be distinct files")

    metadata = {
        "schema_version": 1,
        "git_commit": commit,
        "dirty": dirty,
        "run_id": run_id,
    }
    metadata_path = run_root / "run-metadata.json"
    if metadata_path.exists():
        retained_metadata = _load_json(metadata_path, "run metadata")
        if retained_metadata != metadata:
            raise ReportError("run metadata does not match current commit/dirty state")
    else:
        _write_immutable_json(metadata_path, metadata)

    report = {
        "schema_version": 1,
        "gate": gate,
        "status": status,
        "reason": reason.strip(),
        "git_commit": commit,
        "dirty": dirty,
        "run_id": run_id,
        "started_at": _format_timestamp(started),
        "finished_at": _format_timestamp(finished),
        "duration_ms": duration_ms,
        "command": {
            "argv": argv,
            "exit_code": exit_code,
        },
        "stdout": _artifact(stdout_path, stdout_relative),
        "stderr": _artifact(stderr_path, stderr_relative),
    }
    report_path = run_root / "gates" / f"{gate}.json"
    _write_immutable_json(report_path, report)
    return report_path


def _expect_exact_keys(
    document: dict[str, Any], keys: set[str], description: str
) -> None:
    missing = keys - document.keys()
    unknown = document.keys() - keys
    if missing or unknown:
        raise ReportError(
            f"{description} has invalid keys; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )


def _validate_artifact_record(
    record: Any,
    *,
    repo_root: Path,
    run_relative: PurePosixPath,
    description: str,
) -> str:
    if not isinstance(record, dict):
        raise ReportError(f"{description} must be an object")
    _expect_exact_keys(record, {"path", "sha256", "size_bytes"}, description)
    path, relative = _resolve_artifact(
        repo_root, record.get("path"), run_relative, f"{description} path"
    )
    expected_hash = record.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_hash
    ):
        raise ReportError(f"{description} has invalid SHA-256")
    if sha256_file(path) != expected_hash:
        raise ReportError(f"{description} SHA-256 no longer matches")
    size = record.get("size_bytes")
    if type(size) is not int or size < 0 or path.stat().st_size != size:
        raise ReportError(f"{description} size no longer matches")
    return relative


def _validate_gate_report(
    report: Any,
    *,
    gate: str,
    commit: str,
    dirty: bool,
    run_id: str,
    repo_root: Path,
    run_relative: PurePosixPath,
) -> tuple[str, list[tuple[str, str]]]:
    description = f"{gate} report"
    if not isinstance(report, dict):
        raise ReportError(f"{description} must be an object")
    keys = {
        "schema_version",
        "gate",
        "status",
        "reason",
        "git_commit",
        "dirty",
        "run_id",
        "started_at",
        "finished_at",
        "duration_ms",
        "command",
        "stdout",
        "stderr",
    }
    _expect_exact_keys(report, keys, description)
    if report["schema_version"] != 1:
        raise ReportError(f"{description} has unsupported schema version")
    if report["gate"] != gate:
        raise ReportError(f"{description} gate mismatch")
    if report["git_commit"] != commit:
        raise ReportError(f"{description} is stale or commit-mismatched")
    if report["dirty"] is not dirty:
        raise ReportError(f"{description} dirty state mismatches run metadata")
    if report["run_id"] != run_id:
        raise ReportError(f"{description} run ID mismatch")

    command = report["command"]
    if not isinstance(command, dict):
        raise ReportError(f"{description} command must be an object")
    _expect_exact_keys(command, {"argv", "exit_code"}, f"{description} command")
    argv = _validate_command(command["argv"])
    if argv != command["argv"]:
        raise ReportError(f"{description} command contains an invalid separator")
    _validate_outcome(report["status"], report["reason"], command["exit_code"])

    started = _parse_timestamp(report["started_at"], f"{description} started_at")
    finished = _parse_timestamp(report["finished_at"], f"{description} finished_at")
    if finished < started:
        raise ReportError(f"{description} has reversed timestamps")
    expected_duration = _duration_ms(started, finished)
    if report["duration_ms"] != expected_duration:
        raise ReportError(f"{description} duration does not match timestamps")

    artifacts = []
    for stream in ("stdout", "stderr"):
        relative = _validate_artifact_record(
            report[stream],
            repo_root=repo_root,
            run_relative=run_relative,
            description=f"{description} {stream}",
        )
        artifacts.append((stream, relative))
    if artifacts[0][1] == artifacts[1][1]:
        raise ReportError(f"{description} stdout and stderr paths are identical")
    return report["status"], artifacts


def summarize(
    *,
    repo_root: Path,
    run_id: str,
    expected_commit: str | None = None,
    require_clean: bool = False,
    required_gates: Iterable[str] = GATES,
) -> Path:
    """Validate all required reports and write a deterministic run summary."""
    root = repository_root(repo_root)
    _validate_run_id(run_id)
    commit, current_dirty = git_state(root)
    if expected_commit is not None and expected_commit != commit:
        raise ReportError(
            f"stale commit: expected {expected_commit}, repository is {commit}"
        )
    if require_clean and current_dirty:
        raise ReportError("clean acceptance summary cannot use a dirty tree")

    gates = list(required_gates)
    if tuple(gates) != GATES:
        raise ReportError(
            "release summary requires exactly G0 through G8 in canonical order; "
            "gate subsets are diagnostic-only and cannot be published"
        )

    run_relative, run_root = _paths(root, commit, run_id)
    metadata_path = run_root / "run-metadata.json"
    if not metadata_path.exists():
        stale_candidates = sorted(
            root.glob(
                f"artifacts/acceptance/*/{run_id}/run-metadata.json"
            )
        )
        if stale_candidates:
            stale_commits = sorted(
                {
                    candidate.parents[1].name
                    for candidate in stale_candidates
                }
            )
            raise ReportError(
                f"stale run {run_id!r}: retained for commit(s) "
                f"{', '.join(stale_commits)}, current commit is {commit}"
            )
    metadata = _load_json(metadata_path, "run metadata")
    if not isinstance(metadata, dict):
        raise ReportError("run metadata must be an object")
    _expect_exact_keys(
        metadata,
        {"schema_version", "git_commit", "dirty", "run_id"},
        "run metadata",
    )
    expected_metadata = {
        "schema_version": 1,
        "git_commit": commit,
        "dirty": current_dirty,
        "run_id": run_id,
    }
    if metadata != expected_metadata:
        raise ReportError("run metadata is stale or mismatches current repository state")
    if require_clean and metadata["dirty"]:
        raise ReportError("dirty reports are ineligible for clean acceptance")

    counts = {status: 0 for status in STATUSES}
    gate_summaries = []
    manifest_entries: dict[str, dict[str, Any]] = {}

    metadata_relative = (run_relative / "run-metadata.json").as_posix()
    manifest_entries[metadata_relative] = {
        "kind": "run_metadata",
        "path": metadata_relative,
        "sha256": sha256_file(metadata_path),
    }

    for gate in gates:
        report_path = run_root / "gates" / f"{gate}.json"
        report = _load_json(report_path, f"{gate} report")
        status, artifacts = _validate_gate_report(
            report,
            gate=gate,
            commit=commit,
            dirty=metadata["dirty"],
            run_id=run_id,
            repo_root=root,
            run_relative=run_relative,
        )
        counts[status] += 1
        report_relative = (run_relative / "gates" / f"{gate}.json").as_posix()
        report_hash = sha256_file(report_path)
        manifest_entries[report_relative] = {
            "kind": "gate_result",
            "gate": gate,
            "path": report_relative,
            "sha256": report_hash,
        }
        gate_summaries.append(
            {
                "gate": gate,
                "status": status,
                "report_path": report_relative,
                "report_sha256": report_hash,
            }
        )
        for stream, artifact_relative in artifacts:
            if artifact_relative in manifest_entries:
                raise ReportError(
                    f"artifact path reused across reports: {artifact_relative}"
                )
            artifact_path = root.joinpath(*PurePosixPath(artifact_relative).parts)
            manifest_entries[artifact_relative] = {
                "kind": stream,
                "gate": gate,
                "path": artifact_relative,
                "sha256": sha256_file(artifact_path),
            }

    manifest = {
        "schema_version": 1,
        "git_commit": commit,
        "dirty": metadata["dirty"],
        "run_id": run_id,
        "files": [
            manifest_entries[path] for path in sorted(manifest_entries)
        ],
    }
    manifest_path = run_root / "manifest.json"
    _write_immutable_json(manifest_path, manifest)
    manifest_relative = (run_relative / "manifest.json").as_posix()
    manifest_hash = sha256_file(manifest_path)

    overall_status = (
        "FAIL"
        if counts["FAIL"]
        else "BLOCKED"
        if counts["BLOCKED"]
        else "PASS"
    )
    summary = {
        "schema_version": 1,
        "git_commit": commit,
        "dirty": metadata["dirty"],
        "run_id": run_id,
        "release_eligible": True,
        "required_gates": list(GATES),
        "status": overall_status,
        "counts": counts,
        "gates": gate_summaries,
        "manifest_path": manifest_relative,
        "manifest_sha256": manifest_hash,
    }
    summary_path = run_root / "summary.json"
    _write_immutable_json(summary_path, summary)
    return summary_path


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-commit")
    parser.add_argument("--require-clean", action="store_true")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    record = subparsers.add_parser("record", help="record one gate result")
    _add_common_arguments(record)
    record.add_argument("--gate", choices=GATES, required=True)
    record.add_argument("--status", choices=STATUSES, required=True)
    record.add_argument("--reason", required=True)
    record.add_argument("--exit-code", type=int, required=True)
    record.add_argument("--started-at", required=True)
    record.add_argument("--finished-at", required=True)
    record.add_argument("--stdout-artifact", required=True)
    record.add_argument("--stderr-artifact", required=True)
    record.add_argument("command", nargs=argparse.REMAINDER)

    summary = subparsers.add_parser("summarize", help="build a run summary")
    _add_common_arguments(summary)
    summary.add_argument(
        "--required-gate",
        action="append",
        choices=GATES,
        dest="required_gates",
        help="required gate (repeatable; defaults to all G0..G8)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.action == "record":
            path = record_gate(
                repo_root=args.repo_root,
                run_id=args.run_id,
                gate=args.gate,
                status=args.status,
                reason=args.reason,
                command=args.command,
                exit_code=args.exit_code,
                started_at=args.started_at,
                finished_at=args.finished_at,
                stdout_artifact=args.stdout_artifact,
                stderr_artifact=args.stderr_artifact,
                expected_commit=args.expected_commit,
                require_clean=args.require_clean,
            )
        else:
            path = summarize(
                repo_root=args.repo_root,
                run_id=args.run_id,
                expected_commit=args.expected_commit,
                require_clean=args.require_clean,
                required_gates=args.required_gates or GATES,
            )
    except ReportError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
