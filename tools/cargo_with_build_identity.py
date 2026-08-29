#!/usr/bin/env python3
"""Invoke Cargo with a worktree-state trigger verified independently by build.rs."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ATTESTATION_ENV = "JET3_BUILD_ATTESTATION_V1"
MAX_STATUS_BYTES = 8 * 1024


def git_output(arguments: list[str], cwd: Path) -> bytes:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("GIT_")
    }
    result = subprocess.run(
        ["git", "--no-optional-locks", *arguments],
        cwd=cwd,
        env=environment,
        check=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout


def attestation(cwd: Path) -> str:
    worktree = Path(
        git_output(["rev-parse", "--show-toplevel"], cwd).decode("utf-8").strip()
    ).resolve(strict=True)
    revision = git_output(["rev-parse", "--verify", "HEAD"], worktree).decode(
        "ascii"
    ).strip()
    status = git_output(
        ["status", "--porcelain=v1", "--untracked-files=all"], worktree
    )
    if len(status) > MAX_STATUS_BYTES:
        raise RuntimeError("worktree status exceeds build-attestation limit")
    return f"v1:{revision}:{status.hex()}"


def main() -> int:
    arguments = sys.argv[1:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if not arguments:
        print("usage: cargo_with_build_identity.py -- <cargo command>", file=sys.stderr)
        return 2
    environment = os.environ.copy()
    environment[ATTESTATION_ENV] = attestation(Path.cwd())
    return subprocess.run(arguments, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
