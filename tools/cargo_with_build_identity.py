#!/usr/bin/env python3
"""Invoke Cargo with a worktree-state trigger verified independently by build.rs."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ATTESTATION_ENV = "JET3_BUILD_ATTESTATION_V1"
MAX_STATUS_BYTES = 8 * 1024


def git_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("GIT_")
    }


def git_output(arguments: list[str], cwd: Path) -> bytes:
    result = subprocess.run(
        ["git", "--no-optional-locks", *arguments],
        cwd=cwd,
        env=git_environment(),
        check=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout


def git_status(cwd: Path) -> bytes:
    process = subprocess.Popen(
        ["git", "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=cwd,
        env=git_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        if process.stdout is None:
            raise RuntimeError("Git status output is unavailable")
        status = process.stdout.read(MAX_STATUS_BYTES + 1)
        if len(status) > MAX_STATUS_BYTES:
            raise RuntimeError("worktree status exceeds build-attestation limit")
        returncode = process.wait()
        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, process.args)
        return status
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()


def attestation(cwd: Path) -> str:
    worktree = Path(
        git_output(["rev-parse", "--show-toplevel"], cwd).decode("utf-8").strip()
    ).resolve(strict=True)
    revision = git_output(["rev-parse", "--verify", "HEAD"], worktree).decode(
        "ascii"
    ).strip()
    status = git_status(worktree)
    return f"v1:{revision}:{status.hex()}"


def main() -> int:
    arguments = sys.argv[1:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if not arguments:
        print("usage: cargo_with_build_identity.py -- <cargo command>", file=sys.stderr)
        return 2
    environment = os.environ.copy()
    try:
        environment[ATTESTATION_ENV] = attestation(Path.cwd())
    except (OSError, RuntimeError, UnicodeError, subprocess.SubprocessError) as error:
        print(f"build identity unavailable: {error}", file=sys.stderr)
        return 1
    return subprocess.run(arguments, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
