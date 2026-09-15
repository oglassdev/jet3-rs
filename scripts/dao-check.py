#!/usr/bin/env python3
"""Repeatable DAO checks on the local Windows VM; retain each run separately."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "oracle/windows-dao/scripts"
sys.path.insert(0, str(ORACLE))
from field_update import canonical, identity

SUITES = {
    "indexed-boundary": ("indexed_boundary", "indexed_boundary_candidate"),
    "indexed-rows": ("indexed_row_candidate", "indexed_row_mutation_candidate"),
    "creation-tables": ("creation_tables", "creation_tables_candidate"),
    "index-trees": ("index_tree_mutation", "index_tree_mutation_candidate"),
    "practical-lifecycle": ("practical_lifecycle", "practical_lifecycle_candidate"),
    "numeric-indexes": ("numeric_index_mutation", "numeric_index_mutation_candidate"),
    "multiple-long-values": ("multiple_long_value_creation", "multiple_long_value_creation_candidate"),
    "long-value-lifecycle": ("long_value_lifecycle", "long_value_lifecycle_candidate"),
}


def write(path, value):
    path.write_text(canonical(value) + "\n")


def command(args, root, name, *, timeout=900):
    result = subprocess.run(
        [str(a) for a in args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
    )
    (root / f"{name}.stdout.log").write_text(result.stdout)
    (root / f"{name}.stderr.log").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"{name} failed ({result.returncode}); see {root / (name + '.stderr.log')}")
    return result.stdout


def source_revision():
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    return revision + ("+dirty" if dirty else "")


def runtime_inputs(module, images, revision, receipts):
    historical = json.loads(module.PLAN.read_text())
    value = {k: historical[k] for k in ("document_type", "arms")}
    names = {name for name in historical["inputs"] if (ROOT / name).is_file()}
    names.update(str(p.relative_to(ROOT)) for p in (ROOT / "crates/jet3/src").rglob("*.rs"))
    value.update(
        source_revision=revision,
        images={p.name: identity(p) for p in sorted(images.glob("*.mdb"))},
        inputs={name: identity(ROOT / name)["sha256"] for name in sorted(names)},
    )
    if module.__name__ == "indexed_boundary":
        # Tree growth has its own lifecycle suite; retain the historical refusal recipe.
        value["arms"] = [arm for arm in value["arms"] if arm["name"] != "split"]
    if receipts:
        value["receipts"] = receipts
    return value


def run_suite(name, root, args, revision):
    root.mkdir()
    module_name, example = SUITES[name]
    module = importlib.import_module(module_name)
    command(["cargo", "build", "--locked", "-p", "jet3", "--example", example], root, "build")
    images = root / "images"
    stdout = command([ROOT / "target/debug/examples" / example, images], root, "generate")
    if name in ("creation-tables", "index-trees", "practical-lifecycle", "numeric-indexes", "multiple-long-values", "long-value-lifecycle"):
        module.prepare(images, revision)
        manifest = {"creation-tables": "creation-tables.json", "index-trees": "index-tree-mutation.json",
                    "practical-lifecycle": "practical-lifecycle.json", "numeric-indexes": "numeric-index-mutation.json",
                    "multiple-long-values": "multiple-long-value-creation.json",
                    "long-value-lifecycle": "long-value-lifecycle.json"}[name]
        input_path = images / manifest
    else:
        receipts = json.loads(stdout) if name == "indexed-rows" else None
        inputs = runtime_inputs(module, images, revision, receipts)
        input_path = root / module.PLAN.name
        write(input_path, inputs)
        for arm in inputs["arms"]:
            before = (images / f"{arm['name']}-original.mdb").read_bytes()
            after = (images / f"{arm['name']}-candidate.mdb").read_bytes()
            if receipts:
                module.patch_check(before, after, arm, receipts[arm["name"]])
            else:
                module.patch_check(before, after, arm)
    report = capture(name, root, args, revision, module, images, input_path)
    if name in ("index-trees", "numeric-indexes", "long-value-lifecycle"):
        continuation_root = root / "continuation"
        continuation_root.mkdir()
        continued = continuation_root / "images"
        try:
            module.prepare_continue(images, Path(report["captures"]), continued,
                                    ROOT / "target/debug/examples" / example, revision)
            continuation = capture(name, continuation_root, args, revision, module, continued,
                                   continued / {"numeric-indexes": "numeric-index-mutation.json",
                                                "index-trees": "index-tree-mutation.json",
                                                "long-value-lifecycle": "long-value-lifecycle.json"}[name])
            report["continuation"] = continuation
        except Exception as error:
            report.update(outcome="failed", error=str(error))
            raise
        finally:
            write(root / "report.json", report)
    return report


def capture(name, root, args, revision, module, images, input_path):
    inputs = json.loads(input_path.read_text())
    module_name = module.__name__
    producer = ORACLE / f"{module_name}.ps1"
    wrapper = root / "run.ps1"
    wrapper.write_text(
        "$ErrorActionPreference='Stop'\n"
        "& (Join-Path $PSScriptRoot 'probe-provider.ps1') -ProtocolVersion 1.2.0 "
        "-OutputPath (Join-Path $env:JET3_OUTBOX 'environment.json')\n"
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        f"& (Join-Path $PSScriptRoot '{producer.name}')\n"
    )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + name[:17] + "-" + uuid.uuid4().hex[:6]
    remote = [sys.executable, ROOT / "scripts/windows-dao-ps.py", wrapper,
              "--run-id", run_id, "--shared-root", args.shared_root, "--timeout", "900"]
    for option in ("host", "port", "user", "identity", "remote_shared_root"):
        remote.extend(["--" + option.replace("_", "-"), getattr(args, option)])
    extras = {p.name: p for p in sorted(images.iterdir()) if p.is_file()}
    extras.update({p.name: p for p in [input_path, producer, ORACLE / "field_update.ps1", ORACLE / "probe-provider.ps1"]})
    for extra in extras.values():
        remote.extend(["--with", extra])
    outbox = args.shared_root / "outbox" / run_id
    failure = None
    try:
        command(remote, root, "windows", timeout=960)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        failure = str(error)
    report = dict(document_type="dao_suite_report", suite=name, source_revision=revision,
                  inputs_sha256=identity(input_path)["sha256"], captures=str(outbox), outcome="failed")
    try:
        environment_path = outbox / "environment.json"
        if not environment_path.exists():
            raise RuntimeError("Missing provider environment")
        environment = json.loads(environment_path.read_text(encoding="utf-8-sig"))
        report["environment"] = environment
        report["environment_identity"] = identity(environment_path)
        from hosted_write_reanalysis import validate_environment
        validate_environment(environment)
        result_path = outbox / "result.json"
        if not result_path.exists():
            raise RuntimeError("Missing DAO result")
        if name in ("creation-tables", "index-trees", "practical-lifecycle", "numeric-indexes", "multiple-long-values", "long-value-lifecycle"):
            comparison = module.evaluate(images, outbox)
            matched = comparison["status"] == "accepted"
        else:
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
            comparison = module.build_report(result, outbox, inputs, plan_path=input_path)
            matched = comparison["outcome"] == "observed_accepted"
        comparison["result_sha256"] = identity(result_path)["sha256"]
        report["comparison"] = comparison
        if not matched:
            failure = failure or str(comparison.get("error") or comparison.get("reasons"))
    except Exception as error:
        failure = failure or str(error)
    report["error"] = failure
    if failure is None:
        report["outcome"] = "matched"
    write(root / "report.json", report)
    if failure:
        raise RuntimeError(failure)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suites", nargs="*", metavar="SUITE", help="Defaults to all suites: " + ", ".join(SUITES))
    parser.add_argument("--out", type=Path, required=True, help="new directory for inputs, logs and reports")
    parser.add_argument("--shared-root", type=Path, default=os.environ.get("JET3_WINDOWS_SHARED_ROOT"))
    for name, default in [("host", "127.0.0.1"), ("port", "2222"), ("user", "jet3runner"),
                          ("identity", str(Path.home() / ".ssh/jet3-dao")),
                          ("remote-shared-root", r"\\host.lan\Data")]:
        parser.add_argument("--" + name, default=os.environ.get("JET3_WINDOWS_" + name.upper().replace("-", "_"), default))
    args = parser.parse_args()
    args.suites = args.suites or list(SUITES)
    unknown = set(args.suites) - SUITES.keys()
    if unknown:
        parser.error("unknown suites: " + ", ".join(sorted(unknown)))
    if args.shared_root is None:
        parser.error("--shared-root or JET3_WINDOWS_SHARED_ROOT is required")
    args.shared_root = args.shared_root.expanduser().resolve()
    args.out = args.out.expanduser().resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    revision = source_revision()
    results = []
    for name in args.suites:
        print(f"{name}: generating and comparing with DAO", flush=True)
        try:
            report = run_suite(name, args.out / name, args, revision)
            results.append(dict(suite=name, outcome=report["outcome"]))
        except Exception as error:
            results.append(dict(suite=name, outcome="failed", error=str(error)))
        print(f"{name}: {results[-1]['outcome']}", flush=True)
    write(args.out / "summary.json", dict(source_revision=revision, suites=results))
    print(f"Reports: {args.out}")
    return int(any(r["outcome"] != "matched" for r in results))


if __name__ == "__main__":
    sys.exit(main())
