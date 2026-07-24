#!/usr/bin/env python3
"""Run all canonical acceptance gates and retain immutable gate evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import acceptance_report

RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _timestamp() -> str:
    rendered = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    return rendered.removesuffix("+00:00") + "Z"


def default_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{timestamp}-{os.getpid()}"


def _blocked_reason(gate: str, stderr: str) -> str:
    markers = [
        line.removeprefix("BLOCKED:").strip()
        for line in stderr.splitlines()
        if line.startswith("BLOCKED:") and line.removeprefix("BLOCKED:").strip()
    ]
    if markers:
        return markers[-1]
    return f"{gate} could not run because required release evidence or tooling is unavailable"


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
    except FileExistsError as error:
        raise acceptance_report.ReportError(
            f"refusing to overwrite immutable acceptance log {path}"
        ) from error


def _status_and_reason(
    gate: str, exit_code: int, stderr: str
) -> tuple[str, str]:
    if exit_code == 0:
        return "PASS", f"{gate} satisfied every checked acceptance requirement"
    if exit_code == 3:
        return "BLOCKED", _blocked_reason(gate, stderr)
    return "FAIL", f"{gate} executable validation failed; inspect the retained logs"


def _execute(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        return subprocess.CompletedProcess(
            command,
            127,
            stdout="",
            stderr=f"ERROR: cannot execute acceptance gate command: {error}\n",
        )
    if result.returncode < 0:
        signal_number = -result.returncode
        return subprocess.CompletedProcess(
            command,
            128 + signal_number,
            stdout=result.stdout,
            stderr=(
                result.stderr
                + f"\nERROR: acceptance gate command terminated by signal "
                f"{signal_number}\n"
            ),
        )
    return result


def run(
    *,
    repo_root: Path,
    run_id: str,
    gate_command: Sequence[str] = ("./scripts/run-acceptance-gate.sh",),
) -> int:
    root = acceptance_report.repository_root(repo_root)
    if not RUN_ID.fullmatch(run_id):
        raise acceptance_report.ReportError("invalid acceptance run ID")
    commit, dirty = acceptance_report.git_state(root)
    run_relative = Path("artifacts", "acceptance", commit, run_id)

    statuses: list[str] = []
    for gate in acceptance_report.GATES:
        command = [*gate_command, gate]
        started_at = _timestamp()
        result = _execute(command, root)
        if gate == "G8" and dirty and result.returncode == 0:
            result = subprocess.CompletedProcess(
                command,
                3,
                stdout=result.stdout,
                stderr=(
                    result.stderr
                    + "BLOCKED: G8 source tree is dirty; full acceptance "
                    "requires evidence from the exact clean commit\n"
                ),
            )
        finished_at = _timestamp()
        stdout_relative = run_relative / "logs" / f"{gate}.stdout.txt"
        stderr_relative = run_relative / "logs" / f"{gate}.stderr.txt"
        _write_new(root / stdout_relative, result.stdout)
        _write_new(root / stderr_relative, result.stderr)
        status, reason = _status_and_reason(gate, result.returncode, result.stderr)
        statuses.append(status)
        acceptance_report.record_gate(
            repo_root=root,
            run_id=run_id,
            gate=gate,
            status=status,
            reason=reason,
            command=command,
            exit_code=result.returncode,
            started_at=started_at,
            finished_at=finished_at,
            stdout_artifact=stdout_relative.as_posix(),
            stderr_artifact=stderr_relative.as_posix(),
            expected_commit=commit,
        )
        print(f"{gate}: {status} - {reason}")

    summary_path = acceptance_report.summarize(
        repo_root=root,
        run_id=run_id,
        expected_commit=commit,
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print(f"acceptance summary: {summary_path}")
    print(
        "acceptance result: "
        f"{summary['status']} "
        f"(PASS={summary['counts']['PASS']}, "
        f"FAIL={summary['counts']['FAIL']}, "
        f"BLOCKED={summary['counts']['BLOCKED']})"
    )
    if "FAIL" in statuses:
        return 1
    if "BLOCKED" in statuses:
        return 3
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--run-id",
        default=os.environ.get("JET3_ACCEPTANCE_RUN_ID") or default_run_id(),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        return run(repo_root=args.repo_root, run_id=args.run_id)
    except acceptance_report.ReportError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
