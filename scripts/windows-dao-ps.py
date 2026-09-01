#!/usr/bin/env python3
"""Run one local PowerShell script under x86 Windows PowerShell on the DAO VM.

Discovery aid only: stages the script (plus optional extra files) in the shared
inbox, runs it on the guest's local disk, and prints the captured log inline.
Inside the script, `$env:JET3_WORK` is the guest-local working directory and
`$env:JET3_OUTBOX` is the shared outbox directory for files to bring back.
Nothing this produces is evidence.
"""

from __future__ import annotations

import argparse
import base64
import json
import ntpath
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def guest_script(remote_shared_root: str, run_id: str, script_name: str) -> str:
    config = {
        "inbox": ntpath.join(remote_shared_root, "inbox", run_id),
        "outbox": ntpath.join(remote_shared_root, "outbox", run_id),
        "script": script_name,
        "run_id": run_id,
    }
    blob = base64.b64encode(json.dumps(config).encode("utf-8")).decode("ascii")
    return (
        "$ErrorActionPreference='Stop';"
        f"$c=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{blob}'))"
        "|ConvertFrom-Json;"
        "$work=Join-Path $env:LOCALAPPDATA ('jet3-rs-dev\\ps\\'+$c.run_id);"
        "New-Item -ItemType Directory -Force -Path $work | Out-Null;"
        "New-Item -ItemType Directory -Force -Path $c.outbox | Out-Null;"
        "Copy-Item -Path (Join-Path $c.inbox '*') -Destination $work -Recurse -Force;"
        "$env:JET3_WORK=$work;$env:JET3_OUTBOX=$c.outbox;"
        "$winps=Join-Path $env:WINDIR 'SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "$log=Join-Path $work 'log.txt';"
        "$ErrorActionPreference='Continue';"
        "& $winps -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        "-File (Join-Path $work $c.script) *> $log;"
        "$code=$LASTEXITCODE;$ErrorActionPreference='Stop';"
        "Copy-Item $log (Join-Path $c.outbox 'log.txt') -Force;"
        "Set-Content -Path (Join-Path $c.outbox 'exit.txt') -Value $code;"
        "exit $code"
    )


def read_log(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    parser.add_argument(
        "--with", dest="extra", action="append", default=[], type=Path,
        help="extra local file staged next to the script (repeatable)",
    )
    parser.add_argument("--host", default=os.environ.get("JET3_WINDOWS_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=os.environ.get("JET3_WINDOWS_PORT", "2222"))
    parser.add_argument("--user", default=os.environ.get("JET3_WINDOWS_USER", "jet3runner"))
    parser.add_argument(
        "--identity",
        default=os.environ.get("JET3_WINDOWS_IDENTITY", str(Path.home() / ".ssh/jet3-dao")),
    )
    parser.add_argument("--shared-root", default=os.environ.get("JET3_WINDOWS_SHARED_ROOT"))
    parser.add_argument(
        "--remote-shared-root",
        default=os.environ.get("JET3_WINDOWS_REMOTE_SHARED_ROOT", r"\\host.lan\Data"),
    )
    parser.add_argument("--run-id", default=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-ps")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    if not args.shared_root:
        parser.error("--shared-root or JET3_WINDOWS_SHARED_ROOT is required")
    shared = Path(args.shared_root).expanduser().resolve()
    if not args.script.is_file():
        parser.error(f"script not found: {args.script}")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}", args.run_id):
        parser.error("--run-id must match <UTC timestamp>-<lowercase-slug>")

    inbox = shared / "inbox" / args.run_id
    outbox = shared / "outbox" / args.run_id
    if inbox.exists() or outbox.exists():
        parser.error(f"run id already used: {args.run_id}")
    inbox.mkdir(parents=True)
    shutil.copyfile(args.script, inbox / "script.ps1")
    for extra in args.extra:
        shutil.copyfile(extra, inbox / extra.name)

    command = [
        "ssh", "-p", str(args.port),
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
        "-o", "IdentitiesOnly=yes", "-i", args.identity,
        f"{args.user}@{args.host}",
        "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
        encoded(guest_script(args.remote_shared_root, args.run_id, "script.ps1")),
    ]
    print(f"run id: {args.run_id}", file=sys.stderr)
    try:
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, check=False,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"remote script exceeded {args.timeout} seconds", file=sys.stderr)
        return 124

    log = outbox / "log.txt"
    if log.is_file():
        sys.stdout.write(read_log(log))
    else:
        sys.stdout.write(completed.stdout.decode("utf-8", errors="replace"))
        sys.stderr.write(completed.stderr.decode("utf-8", errors="replace"))
    if outbox.is_dir():
        names = sorted(p.name for p in outbox.iterdir() if p.name not in ("log.txt", "exit.txt"))
        if names:
            print(f"outbox {outbox}: {', '.join(names)}", file=sys.stderr)
    print(f"exit code: {completed.returncode}", file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
