#!/usr/bin/env python3
"""Freeze one CLI/source state and apply manifest-defined schema edit sequences."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(argv: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"refusing existing output directory: {args.out}")
    args.out.mkdir(parents=True)
    (args.out / "requests").mkdir()
    (args.out / "logs").mkdir()
    (args.out / "source").mkdir()
    frozen_cli = args.out / "jet3-cli"
    shutil.copy2(args.cli, frozen_cli)
    frozen_cli.chmod(frozen_cli.stat().st_mode | 0o111)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    source = args.source_root.resolve()
    head = run(["git", "rev-parse", "HEAD"], cwd=source)
    status = run(["git", "status", "--short"], cwd=source)
    diff = run(["git", "diff", "--binary", "HEAD"], cwd=source)
    untracked = run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=source
    )
    for name, result in (("HEAD.txt", head), ("status.txt", status), ("tracked.diff", diff)):
        (args.out / "source" / name).write_text(result.stdout, encoding="utf-8")
        if result.returncode:
            raise SystemExit(f"git capture failed for {name}: {result.stderr}")
    untracked_names = [x for x in untracked.stdout.split("\0") if x]
    with tarfile.open(args.out / "source" / "untracked.tar", "w") as archive:
        for name in untracked_names:
            path = source / name
            if path.is_file():
                archive.add(path, arcname=name, recursive=False)

    events: list[dict[str, object]] = []
    for case in manifest["cases"]:
        name = case["name"]
        candidate = args.out / f"candidate-{name}.mdb"
        native = args.out / f"native-{name}.mdb"
        source_input = Path(case["input"])
        shutil.copy2(source_input, candidate)
        shutil.copy2(Path(case["native"]), native)
        for number, step in enumerate(case["steps"], 1):
            before_step_sha256 = sha256(candidate)
            request = args.out / "requests" / f"{name}-{number:02d}.json"
            request.write_text(
                json.dumps(step["request"], sort_keys=True) + "\n", encoding="utf-8"
            )
            result = run([str(frozen_cli), step["command"], str(candidate), "--input", str(request)])
            (args.out / "logs" / f"{name}-{number:02d}.stdout").write_text(
                result.stdout, encoding="utf-8"
            )
            (args.out / "logs" / f"{name}-{number:02d}.stderr").write_text(
                result.stderr, encoding="utf-8"
            )
            events.append(
                {
                    "case": name,
                    "step": number,
                    "command": step["command"],
                    "returncode": result.returncode,
                    "request_sha256": sha256(request),
                    "candidate_sha256": sha256(candidate),
                }
            )
            expected = int(step.get("expected_returncode", 0))
            refused_unchanged = None
            if expected != 0:
                refused_unchanged = before_step_sha256 == sha256(candidate)
                events[-1]["before_step_sha256"] = before_step_sha256
                events[-1]["refused_unchanged"] = refused_unchanged
            if result.returncode != expected:
                raise SystemExit(f"{name} step {number}: got {result.returncode}, expected {expected}")
            if expected != 0 and not refused_unchanged:
                raise SystemExit(f"{name} step {number}: refused operation changed candidate bytes")
        validation = run([str(frozen_cli), "validate", str(candidate), "--code-page", str(case.get("code_page", 1252))])
        (args.out / "logs" / f"{name}-validate.stdout").write_text(validation.stdout, encoding="utf-8")
        (args.out / "logs" / f"{name}-validate.stderr").write_text(validation.stderr, encoding="utf-8")
        events.append(
            {
                "case": name,
                "validation_returncode": validation.returncode,
                "input_sha256": sha256(source_input),
                "candidate_sha256": sha256(candidate),
                "native_sha256": sha256(native),
                "refused_case_input_exact": (
                    sha256(source_input) == sha256(candidate)
                    if any(int(step.get("expected_returncode", 0)) != 0 for step in case["steps"])
                    else None
                ),
            }
        )
        if validation.returncode != int(case.get("validation_returncode", 0)):
            raise SystemExit(f"{name}: unexpected validation result {validation.returncode}")

    receipt = {
        "document_type": "jet3_schema_candidate_preparation",
        "cli_sha256": sha256(frozen_cli),
        "manifest_sha256": sha256(args.out / "manifest.json"),
        "source_head": head.stdout.strip(),
        "tracked_diff_sha256": sha256(args.out / "source" / "tracked.diff"),
        "untracked_tar_sha256": sha256(args.out / "source" / "untracked.tar"),
        "events": events,
    }
    (args.out / "PREPARE-RESULT.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
