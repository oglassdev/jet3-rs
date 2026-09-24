#!/usr/bin/env python3
"""Prepare and replay the #369 storage/preservation DAO differential suite.

Native acquisition and readback use storage_preservation.ps1; all MDBs and
logs belong in a new output directory outside the repository. The evaluator
checks every image independently, then compares complete DAO snapshots.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import schema_edit_structure as raw
from compare_schema_structure import differences, semantics
from storage_preservation_plan import plan as make_plan, refusals, write

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[2]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def identity(path):
    data = path.read_bytes()
    return dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def invoke(cli, command, path, request, log):
    write(log.with_suffix(".request.json"), request)
    result = subprocess.run([str(cli), command, str(path), "--input", "-"],
                            input=json.dumps(request), text=True, capture_output=True)
    write(log.with_suffix(".result.json"), dict(returncode=result.returncode,
                                               stdout=result.stdout, stderr=result.stderr))
    require(result.returncode == 0, f"{log.name}: {result.stderr}")


def request_for(path, op):
    request = copy.deepcopy(op)
    ident = request.pop("id", None)
    if ident is not None:
        observed = raw.observe(path)
        row, = [r for r in observed["tables"][op["table"]]["rows"] if r["values"]["Id"] == ident]
        request["row"] = dict(page=row["locator"]["page"], slot=row["locator"]["row"])
    return request


def prepare(args):
    args.out.mkdir(parents=True)
    (args.out / "logs").mkdir()
    shutil.copy2(args.cli, args.out / "jet3-cli")
    shutil.copy2(args.plan, args.out / "plan.json")
    shutil.copytree(args.native, args.out / "native")
    cli = args.out.resolve() / "jet3-cli"
    source = {name: subprocess.check_output(command, cwd=ROOT, text=True) for name, command in (
        ("revision", ["git", "rev-parse", "HEAD"]), ("diff", ["git", "diff", "HEAD"]),
        ("status", ["git", "status", "--short"]))}
    write(args.out / "source.json", source | {"cli": identity(cli)})
    for case in read(args.plan)["cases"]:
        name = case["name"]
        source = args.native / f"native-{name}-original.mdb"
        path = args.out / f"candidate-{name}-original.mdb"
        shutil.copy2(source, path)
        for stage in case["stages"]:
            target = args.out / f"candidate-{name}-{stage['name']}.mdb"
            shutil.copy2(path, target)
            for index, op in enumerate(stage["operations"]):
                request = request_for(target, op)
                invoke(cli, "mutate", target, request, args.out / "logs" / f"{name}-{stage['name']}-{index:03d}")
            result = subprocess.run([str(cli), "validate", str(target)], text=True, capture_output=True)
            write(args.out / "logs" / f"{name}-{stage['name']}-validation.json", vars_result(result))
            require(result.returncode == 0, result.stderr)
            path = target
        print(name, "prepared", flush=True)


def vars_result(result):
    return dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def remote(args):
    """Stage a frozen worker and retain the outbox, including failed attempts."""
    args.out.mkdir(parents=True)
    plan = read(args.plan)
    shutil.copy2(args.plan, args.out / "plan.json")
    wrapper = args.out / "run.ps1"
    wrapper.write_text("$ErrorActionPreference='Stop'\n& (Join-Path $PSScriptRoot 'storage_preservation.ps1') -Mode " + args.mode + "\n")
    extras = [args.out / "plan.json"]
    for name in ("storage_preservation.ps1", "schema_candidate_observer.ps1",
                 "query_preservation_seed.ps1", "relationship_forms_suite.ps1"):
        shutil.copy2(SCRIPTS / name, args.out / name)
        extras.append(args.out / name)
    if args.mode == "observe":
        extras += [args.prepared / f"candidate-{case['name']}-{stage}.mdb"
                   for case in plan["cases"] for stage in ["original", *(s["name"] for s in case["stages"])]]
    elif args.mode == "continue":
        extras += [directory / f"{role}-{case['name']}-reused.mdb" for case in plan["cases"]
                   for role,directory in (("candidate",args.prepared),("native",args.prepared / "native"))]
    elif args.mode == "failures":
        jobs = []
        for case in plan["cases"]:
            for refusal in refusals(case):
                jobs.append(refusal | dict(case=case["name"], transaction=False, edit_first=False))
                jobs.append(refusal | dict(case=case["name"], name=refusal["name"]+"-rollback",
                                           transaction=True, edit_first=refusal["name"] != "relationship"))
        write(args.out / "failures.json", jobs)
        extras += [args.out / "failures.json"]
        extras += [args.prepared / "native" / f"native-{case['name']}-original.mdb" for case in plan["cases"]]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-storage-" + args.mode + "-" + uuid.uuid4().hex[:6]
    command = [sys.executable, str(ROOT / "scripts/windows-dao-ps.py"), str(wrapper),
               "--run-id", run_id, "--shared-root", str(args.shared_root), "--timeout", "3600"]
    for path in extras:
        command += ["--with", str(path)]
    write(args.out / "dispatch.json", dict(run_id=run_id, command=command, inputs={str(p):identity(p) for p in extras}))
    result = subprocess.run(command, capture_output=True, text=True)
    write(args.out / "worker.json", vars_result(result))
    outbox = args.shared_root / "outbox" / run_id
    if outbox.is_dir():
        shutil.copytree(outbox, args.out / "captures")
    require(result.returncode == 0, result.stderr + result.stdout)
    print(args.out / "captures")


def normalized(snapshot):
    result = copy.deepcopy(snapshot)
    result.pop("file"); result.pop("identity")
    # DAO reports the opened copy's absolute path in the Database.Name getter.
    for prop in result["database_properties"]:
        if prop["name"] == "Name":
            prop["result"]["value"] = "<opened path>"
    result["tables"] = unbox(result["tables"])
    result["relations"] = unbox(result["relations"])
    for table in result["tables"]:
        table["rows"] = sorted(unbox(table["rows"]), key=lambda r: r["Id"])
    return result


def unbox(value):
    return value[0] if len(value) == 1 and isinstance(value[0], list) else value


def model_row(columns, cells, *, dao=False):
    result = {}
    for column, cell in zip(columns, cells, strict=True):
        value = None if cell is None else next(iter(cell.values()))
        typ = column["type"]
        if value is not None and typ in ("memo", "long_binary"):
            data = value.encode("cp1252") if typ == "memo" else bytes(value)
            if dao:
                if typ == "long_binary" or len(value) > 1024:
                    value = dict(kind="bytes" if typ == "long_binary" else "text", length=len(value),
                                 sha256=hashlib.sha256(data).hexdigest())
            else:
                value = data.hex()
        result[column["name"]] = value
    return result


def apply_model(rows, operations):
    for op in operations:
        if op["operation"] == "delete":
            del rows[op["id"]]
        else:
            ident = op["values"][0]["long"]
            if op["operation"] == "replace":
                del rows[op["id"]]
            rows[ident] = op["values"]


def preserve(before, after, before_bytes, after_bytes):
    pages = set()
    for name, old in before["tables"].items():
        if name == "Items":
            continue
        require(old == after["tables"][name], "unrelated complete table: " + name)
        pages.update(old["definition"]["pages"])
        for role, record in before["maps"].items():
            if role.startswith(name + "/"):
                require(record == after["maps"][role], "unrelated map: " + role)
                pages.update(record["members"])
                pages.update(p for p in record["record"]["references"] if p)
    for page in pages:
        require(before_bytes[page*2048:(page+1)*2048] == after_bytes[page*2048:(page+1)*2048],
                "unrelated page " + str(page))
    return sorted(pages)


def storage(observed):
    table = observed["tables"]["Items"]
    return dict(pages=observed["maps"]["Items/table/owned"]["members"],
                free_pages=observed["free_pages"], size=observed["identity"]["size"],
                rows=[dict(id=r["values"]["Id"], locator=r["locator"], storage=r["storage"])
                      for r in table["rows"]],
                overflow_rows=sum(r["locator"] != r["storage"] for r in table["rows"]),
                maps={k: v for k,v in observed["maps"].items() if k.startswith("Items/")})


def surviving_rows(before, after, operations):
    changed = {op["id"] for op in operations if "id" in op}
    indexed = {r["values"]["Id"]: r for r in after["tables"]["Items"]["rows"]}
    preserved = []
    for row in before["tables"]["Items"]["rows"]:
        ident = row["values"]["Id"]
        if ident in changed:
            continue
        require(ident in indexed and row == indexed[ident], "complete unassigned row/descriptor: " + str(ident))
        preserved.append(ident)
    return preserved


def compare_snapshot(snapshot, baseline, expected, image):
    require(snapshot["identity"] == identity(image), "snapshot identity: " + image.name)
    require(snapshot["queries"] == baseline["queries"], "complete QueryDefs: " + image.name)
    observed, = [t for t in unbox(snapshot["tables"]) if t["name"] == "Items"]
    require(sorted(unbox(observed["rows"]), key=lambda r: r["Id"]) == expected,
            "complete expected DAO rows: " + image.name)


def evaluate(args):
    plan = read(args.prepared / "plan.json")
    args.out.mkdir()
    results, failures = [], []
    for case in plan["cases"]:
        name = case["name"]
        baseline_path = args.prepared / "native" / f"native-{name}-original.mdb"
        baseline = raw.observe(baseline_path)
        baseline_snapshot = read(baseline_path.with_suffix(".json"))
        require({q["name"]: q["type"] for q in baseline_snapshot["queries"]} ==
                {q["name"]: q["type"] for q in plan["queries"]}, "complete query form inventory")
        model = {r[0]["long"]: r for r in case["initial"]}
        previous = {role: baseline for role in ("candidate", "native")}
        stages = [dict(name="original", operations=[]), *case["stages"]]
        if args.continuation:
            stages += [dict(name="continued", operations=case["continuation"])]
        for stage in stages:
            apply_model(model, stage["operations"])
            expected_raw = [model_row(case["columns"], row) for _,row in sorted(model.items())]
            expected_dao = [model_row(case["columns"], row, dao=True) for _,row in sorted(model.items())]
            label = f"{name}-{stage['name']}"
            try:
                observations, snapshots, preserved, unassigned, raw_semantics = {}, {}, {}, {}, {}
                for role in ("candidate", "native"):
                    stem = f"{role}-{label}"
                    directory = args.prepared if role == "candidate" else args.prepared / "native"
                    snapshot_dir = args.readback if role == "candidate" else args.prepared / "native"
                    if stage["name"] == "continued":
                        directory = snapshot_dir = args.continuation
                    path = directory / (stem + ".mdb")
                    observed = raw.observe(path)
                    write(args.out / (stem + ".raw.json"), observed)
                    values = sorted((r["values"] for r in observed["tables"]["Items"]["rows"]), key=lambda r:r["Id"])
                    require(values == expected_raw, "complete raw values: " + stem)
                    preserved[role] = preserve(baseline, observed, baseline_path.read_bytes(), path.read_bytes())
                    unassigned[role] = surviving_rows(previous[role], observed, stage["operations"])
                    previous[role] = observed
                    snapshot = read(snapshot_dir / (stem + ".json"))
                    compare_snapshot(snapshot, baseline_snapshot, expected_dao, path)
                    observations[role] = storage(observed)
                    raw_semantics[role] = semantics(observed, dict(name=label, steps=[]))
                    snapshots[role] = normalized(snapshot)
                diff = differences(raw_semantics["candidate"], raw_semantics["native"])
                require(not diff, "raw semantic mismatch: " + str(diff[:3]))
                diff = differences(snapshots["candidate"], snapshots["native"])
                require(not diff, "DAO snapshot mismatch: " + str(diff[:3]))
                results.append(dict(name=label, preserved_pages=preserved, unassigned_rows=unassigned, storage=observations))
            except Exception as error:
                failures.append(dict(name=label, error=str(error)))
    report = dict(status="fail" if failures else "pass", pairs=len(results), results=results, failures=failures)
    write(args.out / "REPORT.json", report)
    print(json.dumps(dict(status=report["status"], pairs=report["pairs"], failures=failures), indent=2))
    require(not failures, "storage/preservation comparison failed")


def run(args):
    """One complete acquisition; a retry must use a fresh output directory."""
    from storage_preservation_failures import evaluate as evaluate_failures
    args.out.mkdir(parents=True)
    plan = args.out / "plan.json"
    write(plan, make_plan())
    prepared = args.out / "prepared"
    for name in ("storage_preservation.py", "storage_preservation_plan.py", "storage_preservation_failures.py"):
        shutil.copy2(SCRIPTS / name, args.out / name)
    phases = []
    def phase(name, function, **options):
        phases.append(name)
        try:
            function(argparse.Namespace(**options))
        except Exception as error:
            write(args.out / "RUN.json", dict(status="failed", phases=phases, error=str(error)))
            raise
    shared = dict(plan=plan, prepared=prepared, shared_root=args.shared_root)
    phase("native", remote, **shared, mode="native", out=args.out / "native")
    phase("prepare", prepare, plan=plan, native=args.out / "native/captures", cli=args.cli, out=prepared)
    for mode in ("observe", "continue", "failures"):
        phase(mode, remote, **shared, mode=mode, out=args.out / mode)
    phase("compare", evaluate, prepared=prepared, readback=args.out / "observe/captures",
          continuation=args.out / "continue/captures", out=args.out / "comparison")
    phase("compare-failures", evaluate_failures, prepared=prepared, native=args.out / "failures/captures",
          out=args.out / "failure-comparison")
    write(args.out / "RUN.json", dict(status="pass", phases=phases))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser("prepare")
    for name in ("cli", "plan", "native", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    p = subs.add_parser("evaluate")
    for name in ("prepared", "readback", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--continuation", type=Path)
    p = subs.add_parser("remote")
    p.add_argument("--mode", choices=("native", "observe", "continue", "failures"), required=True)
    for name in ("plan", "shared-root", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--prepared", type=Path)
    p = subs.add_parser("run")
    for name in ("cli", "shared-root", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    raw.setup(SCRIPTS)
    {"prepare": prepare, "evaluate": evaluate, "remote": remote, "run": run}[args.command](args)


if __name__ == "__main__":
    main()
