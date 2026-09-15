#!/usr/bin/env python3
"""Reproduce the EXP-0273 multiple-relationship candidate preparation.

This script is intentionally independent of the repository.  It consumes the
retained native discovery artifact and a prebuilt
relationship_mutation_candidate binary, writes every Rust mutation lineage,
and optionally compares all reproducible artifacts with a retained reference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any


MAIN_RUN = "20260915T221615Z-multi-rel-discovery-r2"
SHARED_RUN = "20260915T222201Z-duplicate-fk-rel-r1"
SELF_RUN = "20260915T221928Z-self-rel-atomic-r1"
MAIN_GRAPHS = (
    "parent_two_children",
    "two_fk_same_parent",
    "two_fk_distinct_parents",
    "three_table_chain",
    "self_reference",
)
EXPECTED_SOURCE_REVISION = "bd5a1b16b15d34c5cc4c5cb6017dcdaabcad0807"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_pretty(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_compact(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")


def retained_file(discovery: Path, run_id: str, name: str) -> Path:
    preferred = discovery / "runs" / run_id / "outbox-copy" / name
    if preferred.is_file():
        return preferred
    matches = sorted((discovery / "runs" / run_id).rglob(name))
    if not matches:
        raise FileNotFoundError(f"missing retained input {name!r} below run {run_id}")
    hashes = {sha256(path) for path in matches}
    if len(hashes) != 1:
        rendered = ", ".join(str(path) for path in matches)
        raise RuntimeError(f"ambiguous nonidentical retained inputs for {name}: {rendered}")
    return matches[0]


def convert_value(value: Any, field: dict[str, Any]) -> Any:
    if value is not None and field["type"] in (10, 12):
        return value.encode("cp1252").hex()
    return value


def convert_operation(operation: dict[str, Any], tables: dict[str, Any]) -> dict[str, Any]:
    result = dict(operation)
    fields = tables[operation["table"]]["fields"]
    if operation["kind"] == "field":
        column = next(i for i, field in enumerate(fields) if field["name"] == operation["column"])
        result["column"] = column
        result["value"] = convert_value(operation["value"], fields[column])
    elif operation["kind"] in ("insert", "replace"):
        values = operation["values"]
        result["row"] = [convert_value(values.get(field["name"]), field) for field in fields]
        result.pop("values")
    return result


def main_recipe(graph: dict[str, Any]) -> dict[str, Any]:
    tables = {table["name"]: table for table in graph["tables"]}
    return {
        "stages": [{"name": "original", "operations": []}]
        + [
            {"name": f"success-{index}", "operations": [convert_operation(operation, tables)]}
            for index, operation in enumerate(graph["success"], 1)
        ],
        "refusals": [
            {"name": refusal["name"], "operation": convert_operation(refusal["operation"], tables)}
            for refusal in graph["refusals"]
        ],
    }


def shared_recipe(graph: dict[str, Any]) -> dict[str, Any]:
    recipe = main_recipe(graph)
    recipe["refusals"].append(
        {
            "name": "key-only-parent-a",
            "operation": {
                "kind": "field",
                "table": "Child",
                "id": 100,
                "column": 1,
                "value": 9,
            },
        }
    )
    return recipe


def self_atomic_recipe() -> dict[str, Any]:
    return {
        "stages": [
            {"name": "original", "operations": []},
            {
                "name": "inserted-self",
                "operations": [
                    {"kind": "insert", "table": "Node", "row": [30, 30, "73656c663330"]}
                ],
            },
            {
                "name": "replaced-self",
                "operations": [
                    {
                        "kind": "replace",
                        "table": "Node",
                        "id": 30,
                        "row": [31, 31, "73656c663331"],
                    }
                ],
            },
            {
                "name": "deleted-self",
                "operations": [{"kind": "delete", "table": "Node", "id": 31}],
            },
        ],
        "refusals": [
            {
                "name": "primary-only",
                "operation": {
                    "kind": "field",
                    "table": "Node",
                    "id": 30,
                    "column": 0,
                    "value": 31,
                },
            }
        ],
    }


def self_primary_recipe() -> dict[str, Any]:
    return {
        "stages": [{"name": "original", "operations": []}],
        "refusals": self_atomic_recipe()["refusals"],
    }


def payload_recipe() -> dict[str, Any]:
    return {
        "stages": [
            {"name": "original", "operations": []},
            {
                "name": "grown",
                "operations": [
                    {
                        "kind": "replace",
                        "table": "Child",
                        "id": 100,
                        "row": [100, 10, "41" * 4096],
                    }
                ],
            },
            {
                "name": "shrunk",
                "operations": [
                    {"kind": "replace", "table": "Child", "id": 100, "row": [100, 10, "78"]}
                ],
            },
            {
                "name": "nulled",
                "operations": [
                    {"kind": "replace", "table": "Child", "id": 100, "row": [100, None, None]}
                ],
            },
        ],
        "refusals": [],
    }


def extras_plan(
    shared: dict[str, Any], self_graph: dict[str, Any], payload_graph: dict[str, Any]
) -> dict[str, Any]:
    return {
        "plans": [
            {
                "name": "shared",
                "case": shared,
                "source_prefix": "shared",
                "stages": [{"name": "original", "ops": []}]
                + [
                    {"name": f"success-{index}", "ops": [operation]}
                    for index, operation in enumerate(shared["success"], 1)
                ],
                "refusals": shared["refusals"]
                + [
                    {
                        "name": "key-only-parent-a",
                        "number": 3201,
                        "operation": {
                            "kind": "field",
                            "table": "Child",
                            "id": 100,
                            "column": "ParentId",
                            "value": 9,
                        },
                    }
                ],
                "successor": {
                    "kind": "field",
                    "table": "Child",
                    "id": 100,
                    "column": "ParentId",
                    "value": 1,
                },
            },
            {
                "name": "self-atomic",
                "case": self_graph,
                "source_prefix": "self-atomic",
                "stages": [
                    {"name": "original", "ops": []},
                    {
                        "name": "inserted-self",
                        "ops": [
                            {
                                "kind": "insert",
                                "table": "Node",
                                "values": {"Id": 30, "ParentId": 30, "Body": "self30"},
                            }
                        ],
                    },
                    {
                        "name": "replaced-self",
                        "ops": [
                            {
                                "kind": "replace",
                                "table": "Node",
                                "id": 30,
                                "values": {"Id": 31, "ParentId": 31, "Body": "self31"},
                            }
                        ],
                    },
                    {
                        "name": "deleted-self",
                        "ops": [{"kind": "delete", "table": "Node", "id": 31}],
                    },
                ],
                "refusals": [
                    {
                        "name": "primary-only",
                        "number": 3200,
                        "operation": {
                            "kind": "field",
                            "table": "Node",
                            "id": 30,
                            "column": "Id",
                            "value": 31,
                        },
                        "source_stage": "inserted-self",
                    }
                ],
                "successor": {
                    "kind": "insert",
                    "table": "Node",
                    "values": {"Id": 40, "ParentId": 2, "Body": "native"},
                },
            },
            {
                "name": "payload",
                "case": payload_graph,
                "source_prefix": "payload",
                "stages": [
                    {"name": "original", "ops": []},
                    {
                        "name": "grown",
                        "ops": [
                            {
                                "kind": "replace",
                                "table": "Child",
                                "id": 100,
                                "values": {"Id": 100, "MiddleId": 10, "Body": "A" * 4096},
                            }
                        ],
                    },
                    {
                        "name": "shrunk",
                        "ops": [
                            {
                                "kind": "replace",
                                "table": "Child",
                                "id": 100,
                                "values": {"Id": 100, "MiddleId": 10, "Body": "x"},
                            }
                        ],
                    },
                    {
                        "name": "nulled",
                        "ops": [
                            {
                                "kind": "replace",
                                "table": "Child",
                                "id": 100,
                                "values": {"Id": 100, "MiddleId": None, "Body": None},
                            }
                        ],
                    },
                ],
                "refusals": [],
                "successor": {
                    "kind": "replace",
                    "table": "Child",
                    "id": 100,
                    "values": {"Id": 100, "MiddleId": 11, "Body": "native"},
                },
            },
        ]
    }


def run_candidate(
    binary: Path,
    source: Path,
    recipe: Path,
    destination: Path,
    log: Path,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [str(binary), str(source), str(recipe), str(destination)]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"candidate failed ({completed.returncode}); see {log}")
    return {
        "command": command,
        "exit_code": completed.returncode,
        "log": str(log),
        "source_sha256": sha256(source),
        "recipe_sha256": sha256(recipe),
    }


def reproducible_paths(output: Path) -> list[Path]:
    paths: list[Path] = []
    for path in output.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(output)
        if relative.parts[0] not in ("candidate", "extras", "extras-staging"):
            if relative.name not in {
                "matrix.json",
                "matrix-duplicate-staged.json",
                "extras-plan.json",
                "shared-r1-recipe.json",
                "shared-r2-recipe.json",
                "self-atomic-r1-recipe.json",
                "self-atomic-r2-recipe.json",
                "self-primary-recipe.json",
                "payload-r1-recipe.json",
                "payload-r2-recipe.json",
            }:
                continue
        if path.suffix == ".log":
            continue
        paths.append(relative)
    return sorted(paths)


def compare_reference(output: Path, reference: Path) -> dict[str, Any]:
    generated = reproducible_paths(output)
    expected_paths = set(reproducible_paths(reference))
    records = []
    exact = 0
    generated_only = 0
    mismatch = 0
    for relative in generated:
        actual = output / relative
        expected = reference / relative
        record: dict[str, Any] = {
            "path": str(relative),
            "actual_sha256": sha256(actual),
            "actual_size": actual.stat().st_size,
        }
        if not expected.is_file():
            record["status"] = "generated-supporting-input"
            generated_only += 1
        else:
            record["expected_sha256"] = sha256(expected)
            record["expected_size"] = expected.stat().st_size
            if record["actual_sha256"] == record["expected_sha256"]:
                record["status"] = "exact"
                exact += 1
            else:
                record["status"] = "mismatch"
                mismatch += 1
        records.append(record)
    generated_paths = set(generated)
    reference_only = sorted(expected_paths - generated_paths)
    return {
        "generated_count": len(generated),
        "exact_count": exact,
        "mismatch_count": mismatch,
        "generated_supporting_input_count": generated_only,
        "reference_only_count": len(reference_only),
        "reference_only_paths": [str(path) for path in reference_only],
        "records": records,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reference", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    discovery = args.discovery.resolve()
    binary = args.binary.resolve()
    output = args.output.resolve()
    reference = args.reference.resolve() if args.reference else None

    if args.source_revision != EXPECTED_SOURCE_REVISION:
        raise ValueError(
            f"source revision must be the accepted {EXPECTED_SOURCE_REVISION}, got {args.source_revision}"
        )
    if not discovery.is_dir():
        raise FileNotFoundError(discovery)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise FileNotFoundError(f"candidate binary is missing or not executable: {binary}")
    if output.exists():
        raise FileExistsError(f"output must be fresh: {output}")
    if reference is not None and not reference.is_dir():
        raise FileNotFoundError(reference)
    output.mkdir(parents=True)

    matrix_source = discovery / "matrix.json"
    duplicate_source = discovery / "matrix-duplicate-staged.json"
    matrix = json.loads(matrix_source.read_text(encoding="utf-8"))
    duplicate = json.loads(duplicate_source.read_text(encoding="utf-8"))
    graphs = {graph["name"]: graph for graph in matrix["graphs"]}
    if tuple(graphs) != MAIN_GRAPHS:
        raise ValueError(f"unexpected main graph inventory: {tuple(graphs)}")
    shared_graph = duplicate["graphs"][0]
    if shared_graph["name"] != "duplicate_fk_distinct_targets":
        raise ValueError("unexpected shared-column graph")

    shutil.copyfile(matrix_source, output / "matrix.json")
    (output / "input-staging").mkdir(parents=True)
    shutil.copyfile(matrix_source, output / "input-staging" / "matrix.json")
    shutil.copyfile(duplicate_source, output / "matrix-duplicate-staged.json")
    plan = extras_plan(shared_graph, graphs["self_reference"], graphs["three_table_chain"])
    write_compact(output / "extras-plan.json", plan)
    (output / "extras-staging").mkdir(parents=True)
    shutil.copyfile(output / "extras-plan.json", output / "extras-staging" / "extras-plan.json")

    commands: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []

    for graph_name in MAIN_GRAPHS:
        recipe_path = output / "candidate" / f"{graph_name}-recipe.json"
        write_pretty(recipe_path, main_recipe(graphs[graph_name]))
        for replica in (1, 2):
            source = retained_file(discovery, MAIN_RUN, f"{graph_name}-r{replica}-original.mdb")
            source_records.append(
                {"role": f"main/{graph_name}-r{replica}", "path": str(source), "sha256": sha256(source)}
            )
            commands.append(
                run_candidate(
                    binary,
                    source,
                    recipe_path,
                    output / "candidate" / f"{graph_name}-r{replica}",
                    output / "candidate" / f"{graph_name}-r{replica}.log",
                )
            )

    shared = shared_recipe(shared_graph)
    atomic = self_atomic_recipe()
    primary = self_primary_recipe()
    payload = payload_recipe()
    write_pretty(output / "self-primary-recipe.json", primary)
    for replica in (1, 2):
        shared_recipe_path = output / f"shared-r{replica}-recipe.json"
        atomic_recipe_path = output / f"self-atomic-r{replica}-recipe.json"
        payload_recipe_path = output / f"payload-r{replica}-recipe.json"
        write_pretty(shared_recipe_path, shared)
        write_pretty(atomic_recipe_path, atomic)
        write_pretty(payload_recipe_path, payload)

        shared_source = retained_file(
            discovery, SHARED_RUN, f"duplicate_fk_distinct_targets-r{replica}-original.mdb"
        )
        self_source = retained_file(discovery, MAIN_RUN, f"self_reference-r{replica}-original.mdb")
        payload_source = retained_file(discovery, MAIN_RUN, f"three_table_chain-r{replica}-original.mdb")
        native_inserted = retained_file(discovery, SELF_RUN, f"self-r{replica}-inserted.mdb")

        source_copies = {
            output / "extras-staging" / "sources" / f"shared-r{replica}.mdb": shared_source,
            output / "extras-staging" / "sources" / f"self-atomic-r{replica}.mdb": self_source,
            output / "extras-staging" / "sources" / f"payload-r{replica}.mdb": payload_source,
            output
            / "extras-staging"
            / "control-pre"
            / f"self-atomic-r{replica}-inserted-self.mdb": native_inserted,
        }
        for destination, source in source_copies.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            source_records.append(
                {"role": str(destination.relative_to(output)), "path": str(source), "sha256": sha256(source)}
            )

        commands.append(
            run_candidate(
                binary,
                shared_source,
                shared_recipe_path,
                output / "extras" / f"shared-r{replica}",
                output / "extras" / f"shared-r{replica}.log",
            )
        )
        atomic_output = output / "extras" / f"self-atomic-r{replica}"
        commands.append(
            run_candidate(
                binary,
                self_source,
                atomic_recipe_path,
                atomic_output,
                output / "extras" / f"self-atomic-r{replica}.log",
            )
        )
        commands.append(
            run_candidate(
                binary,
                atomic_output / "inserted-self.mdb",
                output / "self-primary-recipe.json",
                output / "extras" / f"self-primary-r{replica}",
                output / "extras" / f"self-primary-r{replica}.log",
            )
        )
        commands.append(
            run_candidate(
                binary,
                payload_source,
                payload_recipe_path,
                output / "extras" / f"payload-r{replica}",
                output / "extras" / f"payload-r{replica}.log",
            )
        )
        for name in ("shared", "self-atomic", "payload"):
            shutil.copytree(
                output / "extras" / f"{name}-r{replica}",
                output / "extras-staging" / "candidate" / f"{name}-r{replica}",
            )
    # Retain complete native controls for evaluation; submit only the final
    # native images needed by the main successor producer.
    control = output / "control"
    control.mkdir()
    for graph_name in MAIN_GRAPHS:
        for replica in (1, 2):
            stem = f"{graph_name}-r{replica}"
            stages = ["original"] + [f"success-{n}" for n in range(1, 5)]
            stages += ["refusal-" + r["name"] for r in graphs[graph_name]["refusals"]]
            for name in [stem + ".json"] + [f"{stem}-{stage}.mdb" for stage in stages]:
                shutil.copyfile(retained_file(discovery, MAIN_RUN, name), control / name)
            shutil.copytree(output / "candidate" / stem, output / "input-staging/candidate" / stem)
            staged_control = output / "input-staging/control"
            staged_control.mkdir(exist_ok=True)
            shutil.copyfile(control / f"{stem}-success-4.mdb", staged_control / f"{stem}-success-4.mdb")
    for directory, name in (("input-staging", "inputs.zip"), ("extras-staging", "extras-input.zip")):
        with zipfile.ZipFile(output / name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((output / directory).rglob("*")):
                if path.is_file():
                    info = zipfile.ZipInfo(path.relative_to(output / directory).as_posix())
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes())

    comparison = compare_reference(output, reference) if reference else None
    if comparison is not None:
        write_pretty(output / "comparison.json", comparison)

    report = {
        "source_revision": args.source_revision,
        "binary": {"path": str(binary), "sha256": sha256(binary)},
        "discovery": str(discovery),
        "discovery_inputs": {
            "matrix_sha256": sha256(matrix_source),
            "duplicate_matrix_sha256": sha256(duplicate_source),
            "sources": source_records,
        },
        "commands": commands,
        "reproducible_artifact_count": len(reproducible_paths(output)),
        "comparison": None
        if comparison is None
        else {key: value for key, value in comparison.items() if key != "records"},
    }
    write_pretty(output / "preparation-report.json", report)

    sums = []
    for relative in sorted(path.relative_to(output) for path in output.rglob("*") if path.is_file()):
        if relative.name == "SHA256SUMS":
            continue
        sums.append(f"{sha256(output / relative)}  {relative}")
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")

    if comparison is not None and (
        comparison["mismatch_count"] or comparison["reference_only_count"]
    ):
        print(json.dumps(report["comparison"], indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report["comparison"] or {"generated": report["reproducible_artifact_count"]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"prepare.py: {error}", file=sys.stderr)
        raise SystemExit(2)
