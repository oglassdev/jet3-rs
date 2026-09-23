#!/usr/bin/env python3
"""EXP-0299/0300 text-property suite: shared requests for Rust candidates and the DAO producer.

`plan` writes CLI creation requests, the DAO job list and the edit manifest template.
`manifest` binds the template to retained inputs and native outputs for
prepare_schema_candidates.py. `compare-creation` compares DAO readbacks and raw
LvProp payloads of Rust creations with both native replicas.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

KINDS = [
    ("boolean", {}, "Yes", "Is Not Null"),
    ("byte", {}, "1", ">=0"),
    ("integer", {}, "2", ">=0"),
    ("long", {}, "3", ">=0"),
    ("auto_increment", {}, None, None),
    ("currency", {}, "4.5", ">=0"),
    ("single", {}, "1.5", ">=0"),
    ("double", {}, "2.5", ">=0"),
    ("date_time", {}, "#1/2/2000#", "Is Not Null"),
    ("guid", {}, "1", None),
    ("text", {"size": 20}, '"abc"', '<>"x"'),
    ("fixed_text", {"size": 10}, '"xy"', '<>"x"'),
    ("binary", {"size": 20}, "1", None),
    ("memo", {}, '"memo"', "Is Not Null"),
    ("long_binary", {}, "1", None),
]


def column(name, kind, **extra):
    value = {"name": name, "type": kind}
    value.update(extra)
    return value


def primary(field="Id"):
    return {"name": "PrimaryKey", "kind": "primary", "fields": [{"column": field}]}


def kinds_table(name, *, default=False, rule=False, description=False):
    columns = [column("Id", "long")]
    for kind, extra, dv, vr in KINDS:
        spec = column("F_" + kind, kind, **extra)
        if default and dv is not None:
            spec["default_value"] = dv
        if rule and vr is not None:
            spec["validation_rule"] = vr
            spec["validation_text"] = "bad " + kind
        if description:
            spec["description"] = "About " + kind
        if len(spec) > 2 + len(extra) or not (default or rule):
            columns.append(spec)
    return {"name": name, "columns": columns, "indexes": [primary()]}


def index_matrix(required, allow):
    variants = [
        ("UniqueInclude", "unique", "include"),
        ("UniqueIgnore", "unique", "ignore_all_null"),
        ("UniqueRequired", "unique", "required"),
        ("OrdinaryInclude", "ordinary", "include"),
        ("OrdinaryIgnore", "ordinary", "ignore_all_null"),
        ("OrdinaryRequired", "ordinary", "required"),
        ("PrimaryKey", "primary", "required"),
    ]
    tables = []
    for name, kind, policy in variants:
        key = column("K", "text", size=20, required=required, allow_zero_length=allow)
        index = {"name": "By" + name, "kind": kind, "null_policy": policy, "fields": [{"column": "K"}]}
        indexes = [index] if kind == "primary" else [primary(), index]
        values = ["b", "a"]
        if allow:
            values.append("")
        if kind == "ordinary":
            values.append("a")
        if not required and policy != "required":
            values += [None, None] if kind == "unique" else [None]
        rows = [[{"long": n + 1}, None if v is None else {"text": v}] for n, v in enumerate(values)]
        tables.append({"name": "K" + name, "columns": [column("Id", "long"), key], "indexes": indexes, "rows": rows})
    return {"tables": tables}


def long_text(prefix, length):
    return (prefix * length)[:length]


def creations():
    specs = {
        "c01-kinds-defaults": {"tables": [kinds_table("Kinds", default=True)]},
        "c02-kinds-rules": {"tables": [kinds_table("Kinds", rule=True)]},
        "c03-kinds-descriptions": {"tables": [kinds_table("Kinds", description=True)]},
        "c04-table-rule": {"tables": [{
            "name": "Ruled",
            "columns": [column("Id", "long"), column("A", "long", required=True, default_value="1"),
                        column("B", "text", size=20, validation_rule='<>"x"')],
            "indexes": [primary()],
            "validation_rule": "[A]>0 Or [B] Is Null", "validation_text": "table says no"}]},
        "c05-table-text-only": {"tables": [{
            "name": "Messages", "columns": [column("Id", "long"), column("Name", "text", size=30)],
            "indexes": [primary()], "validation_text": "only a message"}]},
        "c06-required-azl-mix": {"tables": [{
            "name": "Mixed",
            "columns": [column("Id", "long"),
                        column("T1", "text", size=20, required=True, allow_zero_length=True, default_value='""'),
                        column("T2", "text", size=20, required=True, validation_rule='<>"no"'),
                        column("M1", "memo", allow_zero_length=True, validation_text="memo message"),
                        column("M2", "memo", required=True, default_value='"m"', description="memo two"),
                        column("F1", "fixed_text", size=4, required=True, default_value='"abcd"')],
            "indexes": [primary()]}]},
        "c07-accents": {"tables": [{
            "name": "Accents",
            "columns": [column("Id", "long"),
                        column("Name", "text", size=20, default_value='"café"', validation_rule='<>"é"',
                               validation_text="Valeur €", description="Clé ÿ")],
            "indexes": [primary()], "validation_text": "Tableau €"}]},
        "c08-chained": {"tables": [{
            "name": "Big",
            "columns": [column("Id", "long"),
                        column("Body", "memo", default_value='"' + long_text("x", 1000) + '"',
                               validation_text=long_text("v", 1500), description=long_text("d", 2000))],
            "indexes": [primary()],
            "rows": [[{"long": 1}, {"memo": "first"}], [{"long": 2}, None]]}]},
        "c09-two-tables": {"tables": [
            {"name": "Plain",
             "columns": [column("Id", "long"), column("Amount", "long", default_value="5"),
                         column("Tag", "guid"), column("Label", "text", size=20, description="label")],
             "indexes": [primary()],
             "rows": [[{"long": 1}, {"long": 10}, None, {"text": "one"}], [{"long": 2}, None, None, None]]},
            {"name": "Checked",
             "columns": [column("Id", "long"), column("Qty", "long", validation_rule=">=0", validation_text="no negatives")],
             "indexes": [primary()], "validation_rule": "[Qty]<1000", "validation_text": "too many"}]},
        "c10-index-plain": index_matrix(False, False),
        "c11-index-azl": index_matrix(False, True),
        "c12-index-required": index_matrix(True, False),
        "c13-index-required-azl": index_matrix(True, True),
        "c14-composite-index": {"tables": [{
            "name": "Pairs",
            "columns": [column("Id", "long"), column("A", "text", size=10, required=True, validation_text="need A"),
                        column("B", "long", default_value="0")],
            "indexes": [primary(), {"name": "ByPair", "kind": "unique", "null_policy": "ignore_all_null",
                                    "fields": [{"column": "A"}, {"column": "B", "direction": "descending"}]}],
            "rows": [[{"long": 1}, {"text": "a"}, {"long": 2}], [{"long": 2}, {"text": "a"}, None],
                     [{"long": 3}, {"text": "b"}, {"long": 1}]]}]},
        "c15-fixed-text": {"tables": [{
            "name": "Fixed",
            "columns": [column("Id", "long"), column("F", "fixed_text", size=4, required=True, allow_zero_length=True,
                                                     default_value='"abcd"', validation_rule='Like "????"', validation_text="four")],
            "indexes": [primary()]}]},
        "c16-kinds-everything": {"tables": [kinds_table("Kinds", default=True, rule=True, description=True)]},
        "c17-later-table": {"tables": [
            {"name": "First", "columns": [column("Id", "long"), column("Name", "text", size=10)], "indexes": [primary()]},
            {"name": "Second", "columns": [column("Id", "long"), column("Note", "memo", description=long_text("n", 1800))],
             "indexes": [primary()]},
            {"name": "Third", "columns": [column("Id", "long"), column("Score", "double", validation_rule="Between 0 And 1")],
             "indexes": [primary()], "validation_rule": "[Score]<>0.5", "validation_text": long_text("t", 1200)}]},
        "c18-memo-rules": {"tables": [{
            "name": "Notes",
            "columns": [column("Id", "long"), column("Body", "memo", required=True, validation_rule="Is Not Null",
                                                     validation_text="body needed", default_value='"-"')],
            "indexes": [primary()]}]},
        "c19-relationship": {"tables": [
            {"name": "Parent", "columns": [column("Id", "long"), column("Name", "text", size=20, default_value='"p"')],
             "indexes": [primary()]},
            {"name": "Child", "columns": [column("Id", "long"), column("ParentId", "long", validation_rule=">0", description="parent")],
             "indexes": [primary()]}],
            "relationships": [{"name": "ParentChild", "parent": {"table": "Parent", "column": "Id"},
                               "child": {"table": "Child", "column": "ParentId"}}]},
        "c20-descending-primary": {"tables": [{
            "name": "Codes",
            "columns": [column("Id", "long", description="key"), column("Code", "text", size=8, required=True,
                                                                          validation_text="code message")],
            "indexes": [{"name": "PrimaryKey", "kind": "primary", "fields": [{"column": "Id", "direction": "descending"}]},
                        {"name": "ByCode", "kind": "unique", "null_policy": "required", "fields": [{"column": "Code"}]}],
            "rows": [[{"long": 1}, {"text": "x"}], [{"long": 2}, {"text": "y"}]]}]},
    }
    return specs


def schema(request):
    return {"command": "schema", "request": request}


def props(table, name, **changes):
    return schema({"operation": "set_column_properties", "table": table, "column": name, **changes})


def table_props(table, **changes):
    return schema({"operation": "set_table_properties", "table": table, **changes})


ALL_LABEL = {"validation_rule": "Is Not Null", "validation_text": "label needed", "default_value": '"lbl"',
             "description": "The label"}


def edits():
    """Paired edits: DAO applies the same requests to the same retained inputs."""
    cases = [
        ("e01-field-all", "baseline", [props("Target", "Label", **ALL_LABEL)], ["Target"]),
        ("e02-field-change-clear", "baseline", [
            props("Target", "Label", **ALL_LABEL),
            props("Target", "Label", validation_rule='Like "L*"', validation_text=None, description=None)], ["Target"]),
        ("e03-table-rule", "baseline", [table_props("Target", validation_rule="[Id]>0", validation_text="positive id")], ["Target"]),
        ("e04-table-rule-clear-one", "baseline", [
            table_props("Target", validation_rule="[Id]>0", validation_text="positive id"),
            table_props("Target", validation_rule=None)], ["Target"]),
        ("e05-column-after-table-block", "baseline", [
            table_props("Target", validation_rule="[Id]>0", validation_text="positive id"),
            schema({"operation": "create_column", "table": "Target", "column": column(
                "Extra", "text", size=30, required=True, validation_rule='<>"x"', validation_text="no x",
                default_value='"e"', description="extra")})], ["Target"]),
        ("e06-autoincrement-properties", "baseline", [
            schema({"operation": "create_column", "table": "Target", "column": column("Serial", "auto_increment")}),
            props("Target", "Serial", validation_rule=">0", default_value="1", description="serial")], ["Target"]),
        ("e07-rename-referenced", "baseline", [
            table_props("Target", validation_rule="[Label] Is Not Null"),
            schema({"operation": "rename_column", "table": "Target", "column": "Label", "name": "Caption"})], ["Target"]),
        ("e08-drop-referenced", "baseline", [
            table_props("Target", validation_rule="[Label] Is Not Null"),
            schema({"operation": "drop_column", "table": "Target", "column": "Label"})], ["Target"]),
        ("e09-create-table", "baseline", [schema({"operation": "create_table", "table": {
            "name": "Props", "columns": [column("Id", "long"), column("Name", "text", size=20, validation_rule='<>"x"',
                                                                     default_value='"n"', description="name"),
                                         column("Qty", "long", default_value="0")],
            "indexes": [primary()], "validation_rule": "[Qty]>=0", "validation_text": "qty"}})], ["Props"]),
        ("e10-options-after-text", "baseline", [
            props("Target", "Label", default_value='"d"'),
            schema({"operation": "set_column_options", "table": "Target", "column": "Label", "required": True,
                    "allow_zero_length": False})], ["Target"]),
        ("e11-refuse-ole-rule", "baseline", [
            dict(props("Sentinel", "Blob", validation_rule="Is Not Null"), expected_returncode=1)], []),
        ("e12-refuse-guid-rule", "n-c09", [
            dict(props("Plain", "Tag", validation_text="guid"), expected_returncode=1)], []),
        ("e13-explicit-null-default", "n-c09", [
            {"command": "mutate", "request": {"operation": "insert", "table": "Plain",
                                               "values": [{"long": 3}, None, None, None]}}], ["Plain"]),
        ("e14-native-rule-edits", "n-c09", [
            table_props("Checked", validation_rule=None, validation_text="changed"),
            props("Checked", "Qty", validation_rule="Between 0 And 9", description="quantity")], ["Checked"]),
        ("e15-native-chained", "n-c08", [
            props("Big", "Body", validation_text="short", description=long_text("e", 2040))], ["Big"]),
        ("e16-rust-rule-edits", "r-c09", [
            table_props("Checked", validation_rule=None, validation_text="changed"),
            props("Checked", "Qty", validation_rule="Between 0 And 9", description="quantity")], ["Checked"]),
        ("e17-rust-chained", "r-c08", [
            props("Big", "Body", validation_text="short", description=long_text("e", 2040))], ["Big"]),
        ("e18-replace-ordinary-unique-required", "n-c13", [schema({
            "operation": "replace_index", "table": "KUniqueInclude", "index": "ByUniqueInclude",
            "replacement": {"name": "ByKey", "kind": "unique", "null_policy": "required", "fields": [{"column": "K"}]}})],
            ["KUniqueInclude"]),
        ("e19-replace-unique-ordinary-ignore", "n-c11", [schema({
            "operation": "replace_index", "table": "KUniqueInclude", "index": "ByUniqueInclude",
            "replacement": {"name": "ByKey", "kind": "ordinary", "null_policy": "ignore_all_null",
                            "fields": [{"column": "K", "direction": "descending"}]}})], ["KUniqueInclude"]),
        ("e20-replace-required-unique-include", "n-c12", [schema({
            "operation": "replace_index", "table": "KUniqueRequired", "index": "ByUniqueRequired",
            "replacement": {"name": "ByKey", "kind": "unique", "fields": [{"column": "K"}]}})], ["KUniqueRequired"]),
        ("e21-replace-ignore-primary", "n-c10", [schema({
            "operation": "replace_index", "table": "KUniqueIgnore", "index": "PrimaryKey",
            "replacement": {"name": "PrimaryKey", "kind": "primary", "fields": [{"column": "Id", "direction": "descending"}]}})],
            ["KUniqueIgnore"]),
        ("e22-refuse-unique-duplicates", "n-c11", [dict(schema({
            "operation": "create_index", "table": "KOrdinaryInclude",
            "index": {"name": "Unique", "kind": "unique", "fields": [{"column": "K"}]}}), expected_returncode=1)], []),
        ("e23-refuse-required-null", "n-c10", [dict(schema({
            "operation": "create_index", "table": "KOrdinaryInclude",
            "index": {"name": "Needed", "kind": "ordinary", "null_policy": "required", "fields": [{"column": "K"}]}}),
            expected_returncode=1)], []),
    ]
    # EXP-0297: a newly Required column retains old nulls, which validation reports.
    invalid = {"e05-column-after-table-block"}
    return [{"name": name, "input": source, "steps": steps, "normalize_table_dates": dates,
             "validation_returncode": int(name in invalid)}
            for name, source, steps, dates in cases]


def rust_only():
    """Rust refusals where DAO would evaluate or silently drop the request; inputs must stay exact."""
    return [
        ("x01-insert-ruled-table", "n-c09", {"command": "mutate", "request": {
            "operation": "insert", "table": "Checked", "values": [{"long": 1}, {"long": 5}]}}),
        ("x02-insert-field-rule", "r-c09", {"command": "mutate", "request": {
            "operation": "insert", "table": "Checked", "values": [{"long": 1}, {"long": 5}]}}),
        ("x03-nordic-edit", "nordic", props("T", "Name", description="no")),
        ("x04-nordic-insert", "nordic", {"command": "mutate", "request": {
            "operation": "insert", "table": "T", "values": [{"text": "z"}]}}),
        ("x05-autoincrement-default", "baseline", schema({"operation": "create_column", "table": "Target",
                                                           "column": column("Serial", "auto_increment", default_value="1")})),
        ("x06-empty-set", "baseline", props("Target", "Label", validation_text="")),
    ]


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def plan(args) -> None:
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    (out / "creation").mkdir()
    specs = creations()
    for name, request in specs.items():
        write(out / "creation" / f"{name}.json", request)
    write(out / "jobs.json", {
        "creations": [{"name": name, "replicas": [1, 2], "request": request} for name, request in specs.items()],
        "edits": edits(),
    })
    write(out / "edits-template.json", {"cases": edits(), "rust_only": [
        {"name": name, "input": source, "steps": [dict(step, expected_returncode=1)]}
        for name, source, step in rust_only()]})


def manifest(args) -> None:
    template = json.loads(args.template.read_text(encoding="utf-8"))
    inputs = json.loads(args.inputs.read_text(encoding="utf-8"))
    cases = []
    for case in template["cases"]:
        bound = copy.deepcopy(case)
        bound["input"] = inputs[case["input"]]
        bound["native"] = str(args.native / f"native-{case['name']}.mdb")
        cases.append(bound)
    for case in template["rust_only"]:
        bound = copy.deepcopy(case)
        bound["input"] = inputs[case["input"]]
        bound["native"] = bound["input"]
        bound["rust_only_refusal"] = True
        cases.append(bound)
    write(args.out, {"cases": cases})


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare_creation(args) -> None:
    sys.path.insert(0, str(args.scripts))
    import column_property_checks as cpc
    from compare_schema_candidates import mask_dates

    readback = json.loads(args.readback.read_text(encoding="utf-8"))
    observed = {item["file"]: item for item in readback["files"]}
    report = {"document_type": "jet3_text_property_creation_comparison", "cases": []}
    passed = True
    for name, request in creations().items():
        tables = {table["name"] for table in request["tables"]}
        candidate = observed[f"candidate-{name}.mdb"]
        candidate_bytes = (args.dir / f"candidate-{name}.mdb").read_bytes()
        for replica in (1, 2):
            native_name = f"native-{name}-r{replica}.mdb"
            native = observed[native_name]
            native_bytes = (args.dir / native_name).read_bytes()
            left = mask_dates({k: v for k, v in candidate.items() if k not in {"file", "identity"}}, tables)
            right = mask_dates({k: v for k, v in native.items() if k not in {"file", "identity"}}, tables)
            semantic = left == right
            payloads = []
            for table in sorted(tables):
                ours, _ = cpc.property_payload(candidate_bytes, table)
                theirs, _ = cpc.property_payload(native_bytes, table)
                payloads.append({"table": table, "equal": ours == theirs, "candidate_sha256": sha256(ours),
                                 "native_sha256": sha256(theirs), "length": len(ours)})
            ok = semantic and all(item["equal"] for item in payloads)
            passed &= ok
            report["cases"].append({"name": name, "replica": replica, "passed": ok, "semantic_equal": semantic,
                                    "lvprop": payloads})
    report["status"] = "pass" if passed else "fail"
    write(args.out, report)
    print(json.dumps({"status": report["status"], "pairs": len(report["cases"]),
                      "failed": [(c["name"], c["replica"]) for c in report["cases"] if not c["passed"]]}))
    if not passed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("plan")
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=plan)
    p = commands.add_parser("manifest")
    p.add_argument("--template", type=Path, required=True)
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--native", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=manifest)
    p = commands.add_parser("compare-creation")
    p.add_argument("--scripts", type=Path, required=True)
    p.add_argument("--readback", type=Path, required=True)
    p.add_argument("--dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=compare_creation)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
