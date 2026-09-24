#!/usr/bin/env python3
"""Reproducible native lifecycles for storage reuse and opaque-object preservation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


QUERIES = [
    ("Q Select", "SELECT Id, Text1 FROM Items ORDER BY Id;", 0),
    ("Q Parameter", "PARAMETERS [pId] Long, [pText] Text(255); SELECT Id FROM Items WHERE Id=[pId] OR Text1=[pText];", 0),
    ("Q Aggregate", "SELECT Text1, Count(*) AS N FROM Items GROUP BY Text1;", 0),
    ("Q Join", "SELECT Items.Id, Watch.Text1 FROM Items LEFT JOIN Watch ON Items.Id=Watch.Id;", 0),
    ("Q Union", "SELECT Id FROM Items UNION SELECT Id FROM Watch;", 128),
    ("Q Crosstab", "TRANSFORM Count(Id) AS N SELECT Text1 FROM Items GROUP BY Text1 PIVOT (Id Mod 3);", 16),
    ("Q Update", "UPDATE Items SET Text1='never executed' WHERE Id=-1;", 48),
    ("Q Append", "INSERT INTO Watch (Id, Text1) SELECT Id, Text1 FROM Items WHERE Id=-1;", 64),
    ("Q Delete", "DELETE FROM Items WHERE Id=-1;", 32),
    ("Q MakeTable", "SELECT Id, Text1 INTO NeverExecuted FROM Items;", 80),
    ("Q DDL", "CREATE TABLE NeverExecutedDDL (Id LONG);", 96),
]


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def columns(kind):
    result = [dict(name="Id", type="auto_increment" if kind == "sparse" else "long"),
              dict(name="Text1", type="text", size=255, required=True)]
    result += [dict(name=f"Text{i}", type="text", size=255) for i in range(2, 7)]
    if kind != "wide":
        result += [dict(name=name, type=typ) for name, typ in
                   (("Memo1", "memo"), ("Blob1", "long_binary"), ("Memo2", "memo"), ("Blob2", "long_binary"))]
    return result


def row(kind, ident, width=20, lengths=(40, 80, 160, 2100)):
    result = [{"long": ident}]
    result += [{"text": (f"{ident:04d}-{i}-" + chr(65 + i) * width)[:width]} for i in range(1, 7)]
    if kind != "wide":
        for i, (typ, length) in enumerate(zip(("memo", "long_binary", "memo", "long_binary"), lengths)):
            value = None if length is None else (
                {typ: "".join(chr(65 + (j * 7 + ident + i) % 26) for j in range(length))}
                if typ == "memo" else {typ: [(j * 13 + ident + i) % 256 for j in range(length)]})
            result.append(value)
    return result


def operation(kind, ident=None, values=None):
    value = {"operation": kind, "table": "Items"}
    if ident is not None:
        value["id"] = ident
    if values is not None:
        value["values"] = values
    return value


def stages(kind):
    deleted = list(range(3, 49, 3))
    return [
        {"name": "holes", "operations": [operation("delete", i) for i in deleted]},
        {"name": "refilled", "operations": [operation("insert", values=row(kind, 100+i, 12)) for i in deleted]},
        {"name": "grown", "operations": [operation("replace", i, row(kind, i, 230, (2100, 4200, 6000, 8000))) for i in (1, 16, 31, 46)]},
        {"name": "regrown", "operations": [operation("replace", i, row(kind, i, 255, (8000, 6000, 4200, 2100))) for i in (1, 16, 31, 46)]},
        {"name": "shrunken", "operations": [operation("replace", i, row(kind, i, 2, (None, 12, 40, None))) for i in (1, 16, 31, 46)]},
        {"name": "released", "operations": [operation("delete", i) for i in [*(i for i in range(1,49) if i not in deleted), *(100+i for i in deleted)]]},
        {"name": "reused", "operations": [operation("insert", values=row(kind, 200+i, 24, (80, 160, 2100, 40))) for i in range(1, 49)]},
    ]


def plan():
    cases = []
    for kind in ("wide", "payloads", "sparse"):
        for replica in (1, 2):
            cases.append(dict(name=f"{kind}-r{replica}", kind=kind, columns=columns(kind),
                              initial=[row(kind, i) for i in range(1, 49)], stages=stages(kind),
                              continuation=[operation("replace", 201, row(kind, 201, 200, (4200, 40, None, 6000))),
                                            operation("delete", 202), operation("insert", values=row(kind, 901))]))
    return dict(cases=cases, queries=[dict(name=n, sql=s, type=t) for n,s,t in QUERIES])


def refusals(case):
    required = row(case["kind"], 1)
    required[1] = None
    return [
        dict(name="duplicate", command="mutate", error=3022,
             request=operation("insert", values=case["initial"][0])),
        dict(name="required", command="mutate", error=3314,
             request=operation("replace", 1, required)),
        dict(name="relationship", command="schema", error=3201,
             request=dict(operation="create_relationship", relationship=dict(name="Rejected",
                          parent=dict(table="Watch", column="Id"), child=dict(table="Items", column="Id")))),
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    write(args.out / "plan.json", plan())
