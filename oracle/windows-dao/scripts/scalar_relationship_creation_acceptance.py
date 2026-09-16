#!/usr/bin/env python3
"""Compare Rust-created scalar relationships with independent DAO creation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scalar_relationship_creation as native


PROVIDER = {
    "ansi": 1252,
    "bits": 32,
    "culture": "en-US",
    "dll_sha256": "4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac",
    "dll_version": "03.60.9765.0",
    "os": "Microsoft Windows NT 10.0.20348.0",
    "provider": "DAO.DBEngine.36",
    "version": "3.6",
}


def read(path):
    return json.loads(path.read_text())


def ident(path):
    return native.identity(path)


def req(ok, message):
    native.req(ok, message)


def table_by_root(raw):
    return {table["root"]: name for name, table in raw["tables"].items()}


def logical_semantics(raw, table_name):
    table = raw["tables"][table_name]
    roots = table_by_root(raw)
    result = []
    for item in table["logical_indexes"]:
        if item["class"] == 2:
            record = native.logical_relation(item["raw_hex"])
            physical = table["physical_indexes"][record["physical_index"]]
            result.append({
                "name": item["name"],
                "class": 2,
                "selector": record["selector"],
                "side": record["side"],
                "relation_ordinal": record["relation_ordinal"],
                "context": record["context_hex"],
                "related_table": roots[record["related_root"]],
                "selected_flags": physical["flags"],
                "selected_keys": physical["keys"],
            })
        else:
            encoded = bytes.fromhex(item["raw_hex"])
            physical = table["physical_indexes"][int.from_bytes(encoded[4:8], "little")]
            result.append({
                "name": item["name"],
                "class": item["class"],
                "selected_flags": physical["flags"],
                "selected_keys": physical["keys"],
            })
    return result


def key_bytes(index):
    return [entry[:-8] for entry in index["entries_hex"]]


def system_semantics(raw):
    result = {}
    for name, table in raw["system_indexes"].items():
        result[name] = {
            "schema": table["schema"],
            "definition_row_count": table["definition_row_count"],
            "live_row_count": table["live_row_count"],
            "indexes": [{
                "ordinal": index["ordinal"],
                "flags": index["flags"],
                "second_word": index["second_word"],
                "expected_second_word": index["expected_second_word"],
                "prefix_matches_live_keys": index["prefix_matches_live_keys"],
                "keys": [entry[:-8] for entry in index["entries"]],
            } for index in table["indexes"]],
        }
    return json.loads(json.dumps(result, sort_keys=True))


def check_counters(raw, role, label):
    for table_name in ("Parent", "Child"):
        for index in raw["tables"][table_name]["physical_indexes"]:
            entries = [bytes.fromhex(entry) for entry in index["entries_hex"]]
            req(index["second_word"] == len({entry[:-4] for entry in entries}),
                f"{label} {role} distinct counter")
            if role == "candidate":
                req(index["first_word"] == 0, f"{label} candidate creation prefix")


def capture_inventory(run, producer, inputs_zip, expected_ids):
    inbox, outbox = run / "inbox", run / "outbox"
    req(ident(inbox / "script.ps1") == ident(producer), "submitted creation-readback producer")
    req(ident(inbox / inputs_zip.name) == ident(inputs_zip), "submitted creation inputs")
    req((outbox / "exit.txt").read_text().strip() == "0", "creation readback exit")
    workers = read(outbox / "workers.json")
    req(all(worker["exit_code"] == 0 for worker in workers["workers"]), "creation worker exits")
    items = {}
    environments = []
    for path in sorted(outbox.glob("worker-*-result.json")):
        worker = read(path)
        req(worker["status"] == "pass" and worker["error"] is None, f"{path.name} status")
        environments.append(worker["environment"])
        for item in worker["items"]:
            req(item["producer_error"] is None, f"{item['id']} readback")
            artifact = outbox / f"{item['id']}.mdb"
            req(item["artifact"] == item["submitted"] == item["capture"]["before"]
                == item["capture"]["after"] == ident(artifact), f"{item['id']} read-only artifact")
            req(item["id"] not in items, "unique creation capture")
            items[item["id"]] = (item, artifact)
    req(len(environments) == 8 and all(environment == PROVIDER for environment in environments),
        "creation provider environment")
    req(set(items) == expected_ids, "complete creation capture inventory")
    expected_files = {f"{item}.mdb" for item in expected_ids}
    expected_files |= {f"worker-{worker}-{suffix}.json" for worker in range(1, 9) for suffix in ("progress", "result")}
    expected_files |= {"workers.json", "exit.txt", "log.txt"}
    req({path.name for path in outbox.iterdir()} == expected_files, "exact creation outbox inventory")
    return items, {"run_id": run.name, "producer": ident(producer), "inputs_zip": ident(inputs_zip),
                   "workers": ident(outbox / "workers.json")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--native-report", type=Path, required=True)
    parser.add_argument("--capture-run", type=Path, required=True)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--inputs-zip", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-refusals", type=int, default=14)
    args = parser.parse_args()
    preparation = read(args.prepared_root / "preparation.json")
    matrix = read(args.prepared_root / "matrix.json")
    native_report = read(args.native_report)
    req(preparation["status"] == "prepared_not_dao_verified" and len(preparation["refusals"]) == args.expected_refusals,
        "creation preparation and expected incompatibility refusals")
    req(native_report["status"] == "accepted" and native_report["provider_environment"] == PROVIDER,
        "accepted native creation report")
    prepared_files = {item["name"]: {"size": item["size"], "sha256": item["sha256"]}
                      for item in preparation["files"]}
    expected_ids = {name[:-4] for name in prepared_files}
    captured, capture_run = capture_inventory(
        args.capture_run, args.producer, args.inputs_zip, expected_ids)
    native_results = {item["id"]: item for item in native_report["results"] if item["accepted"]}
    req(set(native_results) == expected_ids, "native/candidate accepted inventory")
    results = []
    for label in sorted(expected_ids):
        candidate_file = args.prepared_root / "candidates" / f"{label}.mdb"
        capture_item, captured_file = captured[label]
        req(ident(candidate_file) == prepared_files[f"{label}.mdb"] == capture_item["artifact"],
            f"{label} prepared/captured identity")
        candidate = native.raw_observation(captured_file, True)
        control = native_results[label]["raw"]
        req(candidate["identity"] == capture_item["artifact"], f"{label} candidate raw identity")
        req(native.normalized_snapshot(capture_item["capture"]["snapshot"])
            == native.normalized_snapshot(native_results[label]["snapshot"]),
            f"{label} complete DAO properties/rows/traversal/Seek")
        for table_name in ("Parent", "Child"):
            left, right = candidate["tables"][table_name], control["tables"][table_name]
            req(left["columns"] == right["columns"], f"{label} {table_name} raw columns")
            req([row["values"] for row in left["rows"]] == [row["values"] for row in right["rows"]],
                f"{label} {table_name} complete raw values")
            req(logical_semantics(candidate, table_name) == logical_semantics(control, table_name),
                f"{label} {table_name} logical index semantics")
            req(len(left["physical_indexes"]) == len(right["physical_indexes"]),
                f"{label} {table_name} physical index count")
            for cindex, nindex in zip(left["physical_indexes"], right["physical_indexes"], strict=True):
                req(cindex["flags"] == nindex["flags"] and cindex["keys"] == nindex["keys"],
                    f"{label} {table_name} physical schema")
                req(key_bytes(cindex) == key_bytes(nindex), f"{label} {table_name} complete key bytes")
                req(cindex["second_word"] == nindex["second_word"], f"{label} {table_name} distinct counter")
        for key in ("relationship_rows", "relationship_objects", "relationship_aces"):
            req(candidate[key] == control[key], f"{label} {key}")
        req(system_semantics(candidate) == system_semantics(control), f"{label} complete system-index semantics")
        check_counters(candidate, "candidate", label)
        check_counters(control, "native", label)
        results.append({
            "id": label,
            "candidate": candidate["identity"],
            "native": control["identity"],
            "candidate_prefixes": {
                table: [[index["first_word"], index["second_word"]]
                        for index in candidate["tables"][table]["physical_indexes"]]
                for table in ("Parent", "Child")
            },
            "native_prefixes": {
                table: [[index["first_word"], index["second_word"]]
                        for index in control["tables"][table]["physical_indexes"]]
                for table in ("Parent", "Child")
            },
        })
    report = {
        "document_type": "scalar_relationship_creation_acceptance",
        "status": "pass",
        "source_revision": preparation["source_revision"],
        "provider": PROVIDER,
        "preparation": ident(args.prepared_root / "preparation.json"),
        "native_report": ident(args.native_report),
        "capture_run": capture_run,
        "evaluator": ident(Path(__file__)),
        "pairs": len(results),
        "results": results,
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))


if __name__ == "__main__":
    main()
