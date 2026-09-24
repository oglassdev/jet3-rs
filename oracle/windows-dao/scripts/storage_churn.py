#!/usr/bin/env python3
"""Exercise slot saturation, repeated payload turnover and complete page reuse."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

import storage_preservation as suite
import storage_preservation_plan as base


def tiny_row(ident, flipped=False):
    return [{"byte": ident}] + [{"boolean": bool(ident & (1 << bit)) != flipped} for bit in range(6)]


def churn_case(kind, replica):
    if kind == "tiny":
        columns = [dict(name="Id", type="byte")]
        columns += [dict(name=f"Text{i}", type="boolean") for i in range(1, 7)]
        initial = [tiny_row(i) for i in range(256)]
        stages = [dict(name="holes", operations=[base.operation("delete", i) for i in range(1, 32)]),
                  dict(name="refilled", operations=[base.operation("insert", values=tiny_row(i, True)) for i in range(1, 32)])]
        current = list(range(256))
        reused = [tiny_row(i) for i in current]
        continuation = [base.operation("replace", 0, tiny_row(0, True)),
                        base.operation("delete", 255), base.operation("insert", values=tiny_row(255, True))]
    else:
        columns = base.columns(kind)
        initial = [base.row(kind, i, 1) for i in range(1, 49)]
        current, stages = list(range(2, 49)), []
        for cycle in range(1, 7):
            stages.append(dict(name=f"holes-{cycle}", operations=[base.operation("delete", i) for i in current]))
            current = list(range(cycle * 1000 + 1, cycle * 1000 + 48))
            stages.append(dict(name=f"refilled-{cycle}", operations=[
                base.operation("insert", values=base.row(kind, i, 1)) for i in current]))
        current = [1, *current]
        reused = [base.row(kind, i, 1) for i in range(7001, 7049)]
        continuation = [base.operation("replace", 7001, base.row(kind, 7001, 255, (8000, 6000, 4200, 2100))),
                        base.operation("delete", 7002), base.operation("insert", values=base.row(kind, 9001, 1))]
    stages.extend([
        dict(name="released", operations=[base.operation("delete", i) for i in current]),
        dict(name="reused", operations=[base.operation("insert", values=row) for row in reused]),
    ])
    return dict(name=f"{kind}-churn-r{replica}", kind=kind, columns=columns, initial=initial,
                stages=stages, continuation=continuation)


def plan():
    return dict(cases=[churn_case(kind, replica) for kind in ("wide", "payloads", "tiny") for replica in (1, 2)],
                queries=base.plan()["queries"])


def run(args):
    args.out.mkdir(parents=True)
    plan_path = args.out / "plan.json"
    base.write(plan_path, plan())
    shutil.copy2(__file__, args.out / Path(__file__).name)
    prepared = args.out / "prepared"
    shared = dict(plan=plan_path, prepared=prepared, shared_root=args.shared_root)
    phases = []
    def phase(name, function, **options):
        phases.append(name)
        try:
            function(argparse.Namespace(**options))
        except Exception as error:
            base.write(args.out / "RUN.json", dict(status="fail", phases=phases, error=str(error)))
            raise
    phase("native", suite.remote, **shared, mode="native", out=args.out / "native")
    phase("prepare", suite.prepare, plan=plan_path, native=args.out / "native/captures", cli=args.cli, out=prepared)
    for mode in ("observe", "continue"):
        phase(mode, suite.remote, **shared, mode=mode, out=args.out / mode)
    phase("compare", suite.evaluate, prepared=prepared, readback=args.out / "observe/captures",
          continuation=args.out / "continue/captures", out=args.out / "comparison")
    base.write(args.out / "RUN.json", dict(status="pass", phases=phases))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("plan")
    p.add_argument("--out", type=Path, required=True)
    p = commands.add_parser("run")
    for name in ("cli", "shared-root", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    suite.raw.setup(suite.SCRIPTS)
    if args.command == "plan":
        base.write(args.out, plan())
    else:
        run(args)
