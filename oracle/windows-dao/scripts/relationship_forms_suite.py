#!/usr/bin/env python3
"""EXP-0302 relationship-form suite: shared requests for Rust candidates and DAO.

`plan` writes the DAO job list (native inputs and edit steps) and the edit
manifest template. `manifest` binds the template to the native inputs and
outputs, resolving row locators for mutations from each input image, for
prepare_schema_candidates.py. `split` writes the comparison manifests:
DAO-comparable cases (accepted edits and refusals without native residue) and
refusals whose DAO residue is checked separately by `residue`.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path


def col(name, kind, size=None):
    value = {"name": name, "type": kind}
    if size:
        value["size"] = size
    return value


def primary(field="id"):
    return {"name": "PrimaryKey", "kind": "primary", "fields": [{"column": field}]}


def table(name, columns, indexes=None):
    return {"op": "table", "table": {"name": name, "columns": columns, "indexes": [primary()] if indexes is None else indexes}}


def sql(text):
    return {"op": "sql", "text": text}


def relation(name, parent, child, attributes, pairs):
    return {"op": "relation", "name": name, "parent": parent, "child": child,
            "attributes": attributes, "pairs": [list(p) for p in pairs]}


BASE = [
    table("P", [col("id", "long"), col("x", "long"), col("m", "memo"), col("o", "long_binary"), col("t", "text", 10)]),
    table("C", [col("id", "long"), col("pid", "long"), col("m", "memo"), col("o", "long_binary"),
                col("t", "text", 10), col("qid", "long")]),
    table("Q", [col("id", "long"), col("n", "text", 10)]),
    table("D", [col("id", "long"), col("cid", "long")]),
    table("U", [col("id", "long"), col("note", "memo"), col("bin", "long_binary")]),
    sql("INSERT INTO P (id, x, m, t) VALUES (1, 10, 'pm', 'a')"),
    sql("INSERT INTO P (id, x, t) VALUES (2, 10, 'b')"),
    sql("INSERT INTO P (id, x, t) VALUES (3, 20, 'c')"),
    sql("INSERT INTO Q (id, n) VALUES (1, 'q')"),
    sql("INSERT INTO Q (id, n) VALUES (2, 'r')"),
    sql("INSERT INTO C (id, pid, m, t, qid) VALUES (1, 1, 'cm', 'a', 1)"),
    sql("INSERT INTO C (id, pid, t, qid) VALUES (2, 1, 'b', 2)"),
    sql("INSERT INTO C (id) VALUES (3)"),
    sql("INSERT INTO D (id, cid) VALUES (1, 1)"),
    sql("INSERT INTO U (id) VALUES (1)"),
    {"op": "payload", "table": "U", "key": "id", "id": 1, "column": "note", "length": 6000, "seed": 3},
    {"op": "payload", "table": "U", "key": "id", "id": 1, "column": "bin", "length": 3000, "seed": 5},
    {"op": "payload", "table": "P", "key": "id", "id": 2, "column": "o", "length": 40, "seed": 7},
    {"op": "query", "name": "QC", "sql": "SELECT C.id, P.t FROM C INNER JOIN P ON C.pid = P.id"},
]
LEFT, RIGHT, UNENFORCED = 16777216, 33554432, 2
MIXED = BASE + [
    relation("E", "P", "C", 0, [("id", "pid")]),
    relation("U", "Q", "C", UNENFORCED, [("id", "qid")]),
    relation("L", "P", "Q", UNENFORCED | LEFT, [("x", "id")]),
    relation("J", "C", "D", RIGHT | 4096, [("id", "cid")]),
]
WIDE = [f"k{i}" for i in range(11)]
FORMS = BASE + [
    table("W1", [col(name, "long") for name in WIDE], []),
    table("W2", [col(name, "long") for name in WIDE], []),
    sql("INSERT INTO W1 (k0) VALUES (1)"),
    relation("R11", "W1", "W2", UNENFORCED, [(k, k) for k in WIDE]),
    relation("One", "P", "C", 1, [("id", "qid")]),
    relation("Unknown", "Q", "D", 65536, [("id", "cid")]),
    relation("MemoLoose", "P", "C", UNENFORCED, [("m", "m")]),
    relation("Both", "P", "D", LEFT | RIGHT, [("id", "cid")]),
]
INPUTS = {"base": BASE, "mixed": MIXED, "forms": FORMS}


def rel(name, parent, child, pcols, ccols, **options):
    endpoint = lambda t, c: {"table": t, "columns": c} if len(c) > 1 else {"table": t, "column": c[0]}
    value = {"name": name, "parent": endpoint(parent, pcols), "child": endpoint(child, ccols)}
    value.update(options)
    return value


def schema(request, expect=0, dao=None):
    step = {"command": "schema", "request": request}
    if expect:
        step["expected_returncode"] = expect
    if dao:
        step["dao"] = dao
    return step


def create(relationship, expect=0):
    return schema({"operation": "create_relationship", "relationship": relationship}, expect)


def mutate(request, dao_sql, expect=0, locate=None):
    step = {"command": "mutate", "request": request, "dao": {"sql": dao_sql}}
    if expect:
        step["expected_returncode"] = expect
    if locate:
        step["locate"] = locate
    return step


def case(name, input_name, steps, *, kind="accepted", dao_error=None, note=None):
    value = {"name": name, "input_name": input_name, "steps": steps, "kind": kind}
    if dao_error:
        value["dao_error"] = dao_error
    if note:
        value["note"] = note
    return value


REFUSED = 1
U = {"enforce": False}
AVAILABLE, OWNED = "MSysRelationships/table/available", "MSysRelationships/table/owned"
PLACEMENT = {
    "c-e-left": ["C/index/1/owned", AVAILABLE, OWNED],
    "c-e-right-cascades": ["C/index/1/owned", AVAILABLE, OWNED],
    "c-e-left-delete": ["C/index/1/owned", AVAILABLE, OWNED],
    "c-e-both-update": ["C/index/1/owned", AVAILABLE, OWNED],
    "c-graph": ["C/index/1/owned", "D/index/1/owned", AVAILABLE, OWNED],
    "l-replace-u-enforced": ["C/index/2/owned"],
    "l-replace-j-join": ["D/index/1/owned"],
}


def cases():
    result = [
        case("c-u-unique", "base", [create(rel("R", "P", "C", ["id"], ["pid"], **U))]),
        case("c-u-nonunique", "base", [create(rel("R", "P", "C", ["x"], ["pid"], **U))]),
        case("c-u-memo", "base", [create(rel("R", "P", "C", ["m"], ["m"], **U))]),
        case("c-u-ole", "base", [create(rel("R", "P", "C", ["o"], ["o"], **U))]),
        case("c-u-mismatch", "base", [create(rel("R", "P", "C", ["id"], ["t"], **U))]),
        case("c-u-composite", "base", [create(rel("R", "P", "C", ["id", "x"], ["pid", "qid"], **U))]),
        case("c-u-self", "base", [create(rel("R", "C", "C", ["id"], ["pid"], **U))]),
        case("c-u-left", "base", [create(rel("R", "P", "C", ["id"], ["pid"], join="left", **U))]),
        case("c-u-right", "base", [create(rel("R", "P", "C", ["id"], ["pid"], join="right", **U))]),
        case("c-u-both", "base", [create(rel("R", "P", "C", ["id"], ["pid"], join="left_and_right", **U))]),
        case("c-e-left", "base", [create(rel("R", "P", "C", ["id"], ["pid"], join="left"))]),
        case("c-e-right-cascades", "base", [create(rel("R", "P", "C", ["id"], ["pid"], join="right",
                                                        cascade_updates=True, cascade_deletes=True))]),
        case("c-e-left-delete", "base", [create(rel("R", "P", "C", ["id"], ["pid"], join="left", cascade_deletes=True))]),
        case("c-e-both-update", "base", [create(rel("R", "P", "C", ["id"], ["pid"], join="left_and_right",
                                                     cascade_updates=True))]),
        case("c-graph", "base", [
            create(rel("E", "P", "C", ["id"], ["pid"])),
            create(rel("U", "Q", "C", ["id"], ["qid"], **U)),
            create(rel("L", "P", "Q", ["x"], ["id"], join="left", **U)),
            create(rel("S", "C", "C", ["id"], ["qid"], join="right", **U)),
            create(rel("J", "C", "D", ["id"], ["cid"], join="right", cascade_deletes=True)),
        ]),
        case("r-u-cascade", "base", [create(rel("R", "P", "C", ["id"], ["pid"], cascade_deletes=True, **U), REFUSED)],
             kind="refused", dao_error=3001),
        case("r-e-memo", "base", [create(rel("R", "P", "C", ["m"], ["m"]), REFUSED)], kind="refused", dao_error=3409),
        case("r-e-ole", "base", [create(rel("R", "P", "C", ["o"], ["o"]), REFUSED)], kind="refused", dao_error=3409),
        case("r-e-nonunique", "base", [create(rel("R", "P", "C", ["x"], ["pid"]), REFUSED)], kind="refused", dao_error=3609),
        case("r-e-mismatch", "base", [create(rel("R", "P", "C", ["id"], ["t"]), REFUSED)], kind="refused", dao_error=3368),
        case("r-duplicate-name", "mixed", [create(rel("E", "P", "C", ["t"], ["t"], **U), REFUSED)], kind="refused",
             dao_error=3012),
    ]
    C_COLUMNS = ["id", "pid", "m", "o", "t", "qid"]

    def insert_c(values):
        cells = [None if v is None else {"long": v} for v in values]
        request = {"operation": "insert", "table": "C", "values": cells + [None] * (len(C_COLUMNS) - len(cells))}
        names = ", ".join(C_COLUMNS[:len(values)])
        literal = ", ".join("NULL" if v is None else str(v) for v in values)
        return request, f"INSERT INTO C ({names}) VALUES ({literal})"

    orphan_u = insert_c([4, 2, None, None, None, 99])
    orphan_e = insert_c([5, 9])
    result += [
        case("l-orphan-u-insert", "mixed", [mutate(*orphan_u)]),
        case("l-orphan-u-update", "mixed", [mutate({"operation": "update", "table": "C", "column": 5, "value": {"long": 77}},
                                                    "UPDATE C SET qid = 77 WHERE id = 3", locate={"table": "C", "id": 3})]),
        case("l-orphan-e-insert", "mixed", [mutate(*orphan_e, expect=REFUSED)], kind="refused", dao_error=3201),
        case("l-u-parent-delete", "mixed", [mutate({"operation": "delete", "table": "Q"}, "DELETE FROM Q WHERE id = 1",
                                                    locate={"table": "Q", "id": 1})]),
        case("l-e-parent-delete", "mixed", [mutate({"operation": "delete", "table": "P"}, "DELETE FROM P WHERE id = 1",
                                                    expect=REFUSED, locate={"table": "P", "id": 1})],
             kind="refused", dao_error=3200),
        dict(case("l-cascade-delete", "mixed", [mutate({"operation": "delete", "table": "C"}, "DELETE FROM C WHERE id = 1",
                                                        locate={"table": "C", "id": 1})]), affected_tables=["D"]),
        case("l-drop-u", "mixed", [schema({"operation": "drop_relationship", "name": "U"})]),
        case("l-drop-l", "mixed", [schema({"operation": "drop_relationship", "name": "L"})]),
        case("l-drop-e", "mixed", [schema({"operation": "drop_relationship", "name": "E"})]),
        case("l-replace-u-enforced", "mixed", [schema({"operation": "replace_relationship", "name": "U",
                                                       "relationship": rel("U2", "Q", "C", ["id"], ["qid"])})]),
        case("l-replace-e-unenforced", "mixed", [schema({"operation": "replace_relationship", "name": "E",
                                                         "relationship": rel("E2", "P", "C", ["id"], ["pid"], join="right", **U)})]),
        case("l-replace-j-join", "mixed", [schema({"operation": "replace_relationship", "name": "J",
                                                   "relationship": rel("J", "C", "D", ["id"], ["cid"], join="left",
                                                                       cascade_deletes=True)})]),
        case("l-create-u", "mixed", [create(rel("T", "P", "C", ["t"], ["t"], **U))]),
        case("l-drop-table-q", "mixed", [schema({"operation": "drop_table", "table": "Q"})]),
        case("l-drop-table-d", "mixed", [schema({"operation": "drop_table", "table": "D"})]),
        case("l-drop-table-c", "mixed", [schema({"operation": "drop_table", "table": "C"}, REFUSED)], kind="refused",
             dao_error=3281),
        case("l-drop-table-p", "mixed", [schema({"operation": "drop_table", "table": "P"}, REFUSED)], kind="refused",
             dao_error=3281),
        case("l-drop-col-qid", "mixed", [schema({"operation": "drop_column", "table": "C", "column": "qid"}, REFUSED)],
             kind="residue", dao_error=3303,
             note="DAO reports 3303 but leaves the column dropped (EXP-0301)"),
        case("l-drop-col-x", "mixed", [schema({"operation": "drop_column", "table": "P", "column": "x"}, REFUSED)],
             kind="residue", dao_error=3303),
        case("l-drop-col-t", "mixed", [schema({"operation": "drop_column", "table": "C", "column": "t"})]),
        case("l-rename-q", "mixed", [schema({"operation": "rename_table", "table": "Q", "name": "Q2"})]),
        case("l-rename-qid", "mixed", [schema({"operation": "rename_column", "table": "C", "column": "qid", "name": "qid2"})]),
        case("l-rename-x", "mixed", [schema({"operation": "rename_column", "table": "P", "column": "x", "name": "x2"})]),
        case("l-drop-q-primary", "mixed", [schema({"operation": "drop_index", "table": "Q", "index": "PrimaryKey"})]),
        case("p-insert-unrelated", "forms", [mutate({"operation": "insert", "table": "U", "values": [{"long": 2}, {"memo": "new"}, None]},
                                                     "INSERT INTO U (id, [note]) VALUES (2, 'new')")]),
        case("p-create-column-unrelated", "forms", [schema({"operation": "create_column", "table": "U",
                                                            "column": {"name": "extra", "type": "long"}})]),
        case("p-rename-w2", "forms", [schema({"operation": "rename_table", "table": "W2", "name": "W3"})]),
        case("p-drop-w1", "forms", [schema({"operation": "drop_table", "table": "W1"})]),
        case("p-create-u", "forms", [create(rel("T", "P", "C", ["t"], ["t"], **U))]),
        case("p-drop-memo-loose", "forms", [schema({"operation": "drop_relationship", "name": "MemoLoose"})]),
        case("p-insert-one-child", "forms", [mutate(*insert_c([6, None, None, None, None, 3]), expect=REFUSED)],
             kind="rust-only", note="one-to-one relationships are not interpreted"),
        case("p-insert-unknown-child", "forms", [mutate({"operation": "insert", "table": "D", "values": [{"long": 2}, None]},
                                                         "INSERT INTO D (id) VALUES (2)", expect=REFUSED)], kind="rust-only",
             note="unknown attribute 65536 is not interpreted"),
    ]
    return result


def dated_tables(item):
    """Tables whose DateUpdate DAO refreshes: edited tables and enforced relationship endpoints."""
    relations = [op for op in INPUTS[item["input_name"]] if op["op"] == "relation" and not op["attributes"] & 2]
    names = set()
    for step in item["steps"]:
        request = step["request"]
        if step["command"] != "schema":
            continue
        kind = request["operation"]
        if isinstance(request.get("table"), str):
            names.add(request["table"])
        if kind == "rename_table":
            names.add(request["name"])
        if "relationship" in request and request["relationship"].get("enforce", True):
            names.update((request["relationship"]["parent"]["table"], request["relationship"]["child"]["table"]))
        for op in relations:
            dropped = kind in ("drop_relationship", "replace_relationship") and request["name"] == op["name"]
            if dropped or (kind == "drop_table" and request["table"] in (op["parent"], op["child"])):
                names.update((op["parent"], op["child"]))
    return sorted(names)


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def plan(args) -> None:
    args.out.mkdir(parents=True)
    all_cases = cases()
    jobs = {"inputs": [{"name": name, "ops": ops} for name, ops in INPUTS.items()], "edits": []}
    for item in all_cases:
        steps = [step.get("dao") or {"request": step["request"]} for step in item["steps"]]
        jobs["edits"].append({"name": item["name"], "input": item["input_name"], "steps": steps})
    write(args.out / "jobs.json", jobs)
    for item in all_cases:
        item["normalize_table_dates"] = dated_tables(item)
        # New foreign-index trees are placed independently (as in EXP-0298).
        if item["name"] in PLACEMENT:
            item["placement_roles"] = PLACEMENT[item["name"]]
    write(args.out / "edits-template.json", {"cases": all_cases})


def manifest(args) -> None:
    sys.path.insert(0, str(args.scripts))
    import system_catalog as catalog
    from relationship_mutation_structure import rows as raw_rows

    template = json.loads(args.template.read_text())
    out = {"cases": []}
    for item in template["cases"]:
        source = args.native / f"input-{item['input_name']}.mdb"
        data = source.read_bytes()
        value = copy.deepcopy(item)
        value["input"] = str(source)
        value["native"] = str(args.native / f"native-{item['name']}.mdb")
        for step in value["steps"]:
            locate = step.pop("locate", None)
            step.pop("dao", None)
            if locate:
                tables = catalog.analyze_checkpoint(data)["tables"].values()
                raw, = [t for t in tables if t["name"] == locate["table"]]
                row, = [r for r in raw_rows(data, raw, "id") if r["values"]["id"] == locate["id"]]
                step["request"]["row"] = {"page": row["locator"]["page"], "slot": row["locator"]["row"]}
        out["cases"].append(value)
    write(args.out, out)


def split(args) -> None:
    prepared = json.loads(args.manifest.read_text())
    comparable = [c for c in prepared["cases"] if c["kind"] in ("accepted", "refused")]
    write(args.out / "manifest-compare.json", {"cases": comparable})
    write(args.out / "manifest-structure.json", {"cases": [c for c in comparable if c["kind"] == "accepted"]})
    print(len(comparable), "comparable,", len(prepared["cases"]) - len(comparable), "separate")


def residue(args) -> None:
    """Checks native step outcomes and Rust-only/residue refusals outside the pair comparison."""
    prepared = json.loads(args.manifest.read_text())
    native = json.loads(args.native_result.read_text(encoding="utf-8-sig"))
    outcomes = {e["name"]: e for e in native["edits"]}
    report, ok = [], True
    for item in prepared["cases"]:
        steps = outcomes[item["name"]]["steps"]
        failed = [s for s in steps if not s["ok"]]
        if item["kind"] in ("refused", "residue"):
            passed = len(failed) == 1 and item["dao_error"] in failed[0]["error"]["numbers"]
        else:
            passed = not failed and len(steps) == len(item["steps"])
        entry = {"name": item["name"], "kind": item["kind"], "dao_steps": steps, "dao_expectation_met": passed}
        if item["kind"] in ("residue", "rust-only", "refused"):
            source = Path(item["input"]).read_bytes()
            candidate = (args.prepared / f"candidate-{item['name']}.mdb").read_bytes()
            native_bytes = Path(item["native"]).read_bytes()
            entry["candidate_input_exact"] = candidate == source
            entry["native_changed_bytes"] = sum(a != b for a, b in zip(source, native_bytes)) + abs(len(source) - len(native_bytes))
            passed = passed and entry["candidate_input_exact"]
        ok &= passed
        report.append(entry)
    write(args.out, {"document_type": "jet3_relationship_forms_outcomes", "status": "pass" if ok else "fail", "cases": report})
    print("pass" if ok else "fail", sum(1 for r in report if r["dao_expectation_met"]), "/", len(report))
    if not ok:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=plan)
    p = sub.add_parser("manifest")
    p.add_argument("--scripts", type=Path, required=True)
    p.add_argument("--template", type=Path, required=True)
    p.add_argument("--native", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=manifest)
    p = sub.add_parser("split")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=split)
    p = sub.add_parser("residue")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--native-result", type=Path, required=True)
    p.add_argument("--prepared", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=residue)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
