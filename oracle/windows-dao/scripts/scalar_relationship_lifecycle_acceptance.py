#!/usr/bin/env python3
"""Compare prepared Rust scalar-relationship mutations with DAO controls."""
from __future__ import annotations

import argparse
import copy
import hashlib
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


def read(path: Path):
    return json.loads(path.read_text())


def ident(path: Path):
    return native.identity(path)


def req(ok, message):
    native.req(ok, message)


def prefixes(raw):
    return {
        f"{table}/{index['index']}": [index["first_word"], index["second_word"]]
        for table, record in raw["tables"].items()
        for index in record["physical_indexes"]
    }


def tables_without_prefixes(raw):
    value = copy.deepcopy(raw["tables"])
    for table in value.values():
        for index in table["physical_indexes"]:
            index.pop("first_word", None)
            index.pop("second_word", None)
    return value


def requested_exceptions(before, item):
    if not item["accepted"]:
        return set(), set()
    request = item["request"]
    kind = request["operation"]
    if kind == "delete":
        return set(), set()
    table_name = request["table"]
    table = before["tables"][table_name]
    columns = table["columns"]
    operation = item["operation"]
    row_id = operation["row"][0] if operation["kind"] == "insert" else operation["id"]
    assigned = []
    if kind in ("insert", "replace"):
        req(len(request["values"]) == len(columns), f"{item['id']} complete request")
        assigned = list(zip(columns, request["values"], strict=True))
    elif kind == "update":
        column = columns[request["column"]]
        assigned = [(column, request["value"])]
    else:
        raise AssertionError(f"{item['id']} request kind {kind}")
    nulls = {
        (table_name, row_id, column["name"])
        for column, value in assigned
        if value is None and column["type"] != "Boolean" and column["storage"] == "fixed" and column["size"]
    }
    payloads = set()
    if kind == "replace":
        payloads = {
            (table_name, row_id, column["name"])
            for column in columns
            if column["type"] in ("Memo", "OLE")
        }
    return nulls, payloads


def normalized_tables(raw, nulls, payloads):
    value = tables_without_prefixes(raw)
    for table_name, row_id, column_name in nulls:
        table = value[table_name]
        rows = [row for row in table["rows"] if row["values"].get("Id") == row_id]
        if not rows:
            continue
        req(len(rows) == 1, "unique requested-null row")
        row = rows[0]
        column = next(column for column in table["columns"] if column["name"] == column_name)
        raw_bytes = bytearray.fromhex(row["raw_hex"])
        presence_length = (len(table["columns"]) + 7) // 8
        presence = raw_bytes[-presence_length:]
        ordinal = column["ordinal"]
        req(row["values"][column_name] is None, "requested-null decoded value")
        req(not presence[ordinal // 8] & (1 << (ordinal % 8)), "requested-null presence bit")
        start = 1 + column["fixed_offset"]
        req(start + column["size"] <= len(raw_bytes) - presence_length, "requested-null fixed span")
        raw_bytes[start:start + column["size"]] = bytes(column["size"])
        row["raw_hex"] = raw_bytes.hex()
    for table_name, row_id, column_name in payloads:
        rows = [row for row in value[table_name]["rows"] if row["values"].get("Id") == row_id]
        if not rows:
            continue
        req(len(rows) == 1 and column_name in rows[0]["descriptors"], "selected payload descriptor")
        row = rows[0]
        descriptor = row["descriptors"][column_name]
        raw_descriptor = descriptor["raw_hex"]
        req(row["raw_hex"].count(raw_descriptor) == 1, "unique selected payload descriptor")
        row["raw_hex"] = row["raw_hex"].replace(raw_descriptor, "<PLACEMENT>", 1)
        descriptor["raw_hex"] = "<PLACEMENT>"
        descriptor["locator"] = "<PLACEMENT>"
    return value


def raw_top(raw):
    value = copy.deepcopy(raw)
    for key in ("identity", "page0_relationship_byte", "tables"):
        value.pop(key)
    return value


def system_page_hashes(path: Path):
    data = path.read_bytes()
    analysis = native.catalog.analyze_checkpoint(data)
    pages = set()
    for table in analysis["tables"].values():
        if not table["name"].startswith("MSys"):
            continue
        definition = table["definition"]
        pages.update(definition["pages"])
        locators = list(definition["maps"].values())
        locators.extend(index["map"] for index in definition["physical_indexes"])
        locators.extend(group[role] for group in definition["long_value_maps"] for role in ("owned", "available"))
        for locator in locators:
            record, members = native.map_info(data, locator, f"{table['name']} system map")
            pages.add(locator["page"])
            pages.update(page for page in record["references"] if page)
            pages.update(members)
    return {
        str(page): hashlib.sha256(data[page * 2048:(page + 1) * 2048]).hexdigest()
        for page in sorted(pages)
    }


def expected_refusal_prefixes(before, item):
    expected = copy.deepcopy(prefixes(before))
    operation = item["operation"]
    table_name = operation["table"]
    if operation["kind"] == "insert":
        req(table_name == "Child", f"{item['id']} refused insert role")
        expected["Child/0"][1] += 1
        return expected
    assigned = set()
    if operation["kind"] == "field":
        assigned.add(operation["column"])
    elif operation["kind"] == "replace":
        assigned.update(column["name"] for column in before["tables"][table_name]["columns"])
    elif operation["kind"] != "delete":
        raise AssertionError(f"{item['id']} refusal kind")
    if table_name == "Child" and "Key" in assigned:
        ordinal = before["reciprocal"]["child"]["physical_index"]
        first, second = expected[f"Child/{ordinal}"]
        if first:
            expected[f"Child/{ordinal}"] = [first - 1, min(second, first - 1)]
    return expected


def check_refusal_bytes(source: Path, post: Path, before, expected):
    old, new = source.read_bytes(), post.read_bytes()
    req(len(old) == len(new), "refusal length")
    allowed = {1538}
    analysis = native.catalog.analyze_checkpoint(old)
    by_name = {table["name"]: table for table in analysis["tables"].values()}
    prior = prefixes(before)
    for table_name, table in by_name.items():
        for index in table["definition"]["physical_indexes"]:
            key = f"{table_name}/{index['index']}"
            if key in expected and prior[key] != expected[key]:
                offset = index["entry_count_offset"]
                allowed.update(range(offset - 4, offset + 4))
    changed = {offset for offset, (a, b) in enumerate(zip(old, new)) if a != b}
    req(changed <= allowed, "refusal changes only exact counter/header spans")


def projected_snapshot(snapshot, raw, expected):
    result = copy.deepcopy(snapshot)
    for table in result["tables"]:
        table_name = native.pname(table)
        definitions = {item["name"]: item for item in raw["tables"][table_name]["logical_indexes"]}
        for index in table["indexes"]:
            name = native.pname(index)
            definition = definitions[name]
            if definition["class"] == 2:
                ordinal = native.logical_relation(definition["raw_hex"])["physical_index"]
            else:
                ordinal = int.from_bytes(bytes.fromhex(definition["raw_hex"])[4:8], "little")
            prop, = [prop for prop in index["properties"] if native.pname(prop) == "DistinctCount"]
            actual = raw["tables"][table_name]["physical_indexes"][ordinal]["second_word"]
            req(prop["type"] == 4 and not prop["is_null"] and prop["value"] == str(actual),
                "DistinctCount getter/raw agreement")
            prop["value"] = str(expected[f"{table_name}/{ordinal}"][1])
    return result


def capture_inventory(run: Path, producer: Path, inputs_zip: Path, matrix: dict):
    inbox, outbox = run / "inbox", run / "outbox"
    req(ident(inbox / "script.ps1") == ident(producer), "submitted readback producer")
    req(ident(inbox / inputs_zip.name) == ident(inputs_zip), "submitted prepared ZIP")
    req((outbox / "exit.txt").read_text().strip() == "0", "readback wrapper exit")
    workers = read(outbox / "workers.json")
    req(all(worker["exit_code"] == 0 for worker in workers["workers"]), "readback worker exits")
    items = {}
    environments = []
    for path in sorted(outbox.glob("worker-*-result.json")):
        worker = read(path)
        req(worker["status"] == "pass" and worker["error"] is None, f"{path.name} status")
        environments.append(worker["environment"])
        for item in worker["items"]:
            req(item["producer_error"] is None, f"{item['id']} readback")
            artifact = outbox / item["file"] if "file" in item else outbox / f"{item['id']}.mdb"
            req(item["artifact"] == item["capture"]["before"] == item["capture"]["after"] == ident(artifact),
                f"{item['id']} read-only artifact")
            req(item["id"] not in items, "unique captured item")
            items[item["id"]] = (item, artifact)
    req(len(environments) == 8 and all(env == PROVIDER for env in environments), "exact provider environment")
    req(set(items) == {item["id"] for item in matrix["items"]}, "complete captured item inventory")
    expected = {item["file"] for item in matrix["items"]}
    expected |= {f"worker-{worker}-{suffix}.json" for worker in range(1, 9) for suffix in ("progress", "result")}
    expected |= {"workers.json", "exit.txt", "log.txt"}
    req({path.name for path in outbox.iterdir()} == expected, "exact readback outbox inventory")
    return items, {"run_id": run.name, "producer": ident(producer), "inputs_zip": ident(inputs_zip),
                   "workers": ident(outbox / "workers.json")}


def native_events(report):
    events = {}
    for lineage in report["results"]:
        for event in lineage["events"]:
            key = f"{lineage['id']}--{event['name']}"
            req(key not in events, "unique native event")
            events[key] = event
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, action="append", required=True)
    parser.add_argument("--native-report", type=Path, action="append", required=True)
    parser.add_argument("--capture-run", type=Path, action="append", required=True)
    parser.add_argument("--producer", type=Path, action="append", required=True)
    parser.add_argument("--capture-input", type=Path, action="append", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    count = len(args.prepared_root)
    req(count == len(args.native_report) == len(args.capture_run) == len(args.producer) == len(args.capture_input), "parallel input lists")
    results = []
    capture_runs = []
    seen = set()
    source_revision = None
    for prepared, report_path, run, producer, capture_input in zip(
            args.prepared_root, args.native_report, args.capture_run, args.producer, args.capture_input, strict=True):
        matrix = read(prepared / "matrix.json")
        req(matrix["status"] == "prepared_not_dao_verified" and not matrix["failures"], "successful preparation")
        if source_revision is None:
            source_revision = matrix["source_revision"]
        req(matrix["source_revision"] == source_revision, "one production source")
        native_report = read(report_path)
        req(native_report["status"] == "accepted" and native_report["provider_environment"] == PROVIDER,
            "accepted native report/provider")
        req(matrix["native_run"] == native_report["run_id"], "native run linkage")
        events = native_events(native_report)
        req(ident(capture_input) == ident(prepared / "inputs.zip"), "prepared/capture ZIP equivalence")
        captured, capture_receipt = capture_inventory(run, producer, capture_input, matrix)
        capture_runs.append(capture_receipt)
        native_outbox = args.native_root / "runs" / matrix["native_run"] / "outbox"
        for item in matrix["items"]:
            label = item["id"]
            req(label not in seen and label in events, f"{label} unique native linkage")
            seen.add(label)
            event = events[label]
            capture_item, captured_path = captured[label]
            rust_path = prepared / item["file"]
            source_path = native_outbox / item["source_file"]
            native_path = native_outbox / item["native_file"]
            req(ident(rust_path) == item["identity"] == capture_item["submitted"] == capture_item["artifact"],
                f"{label} Rust source/capture identity")
            req(ident(source_path) == item["source"] and ident(native_path) == item["native"],
                f"{label} native artifact linkage")
            req(event["artifact"] == item["native"] and event["operation"] == item["operation"],
                f"{label} native receipt linkage")
            req((event["error"] is None) == item["accepted"], f"{label} outcome linkage")
            before = native.raw_observation(source_path, True)
            rust = native.raw_observation(captured_path, True)
            dao = native.raw_observation(native_path, True)
            req(before["identity"] == item["source"] and rust["identity"] == item["identity"]
                and dao["identity"] == item["native"], f"{label} raw identities")
            req(json.loads(json.dumps(dao, sort_keys=True)) == event["raw"], f"{label} retained native raw observation")
            req(system_page_hashes(source_path) == system_page_hashes(rust_path) == system_page_hashes(native_path),
                f"{label} all system pages preserved")
            nulls, payloads = requested_exceptions(before, item)
            if item["accepted"]:
                expected = prefixes(dao)
                req(prefixes(rust) == expected, f"{label} exact successful counters")
            else:
                req(rust_path.read_bytes() == source_path.read_bytes(), f"{label} Rust refusal whole-file exact")
                expected = expected_refusal_prefixes(before, item)
                req(prefixes(dao) == expected, f"{label} exact native refusal counters")
                req(raw_top(before) == raw_top(dao), f"{label} native refusal non-table state")
                req(tables_without_prefixes(before) == tables_without_prefixes(dao),
                    f"{label} native refusal complete table state")
                check_refusal_bytes(source_path, native_path, before, expected)
            req(raw_top(rust) == raw_top(dao), f"{label} complete maps/system/catalog state")
            req(normalized_tables(rust, nulls, payloads) == normalized_tables(dao, nulls, payloads),
                f"{label} complete raw table state")
            rust_snapshot = capture_item["capture"]["snapshot"]
            dao_snapshot = event["snapshot"]
            req(projected_snapshot(rust_snapshot, rust, expected)
                == projected_snapshot(dao_snapshot, dao, expected),
                f"{label} complete DAO properties/rows/traversal/Seek")
            results.append({
                "id": label,
                "accepted": item["accepted"],
                "native_error": item["native_error"],
                "source": item["source"],
                "rust": item["identity"],
                "native": item["native"],
                "rust_prefixes": prefixes(rust),
                "native_prefixes": prefixes(dao),
                "null_padding_fields": sorted(nulls),
                "payload_placement_fields": sorted(payloads),
            })
    report = {
        "document_type": "scalar_relationship_lifecycle_acceptance",
        "status": "pass",
        "source_revision": source_revision,
        "provider": PROVIDER,
        "evaluator": ident(Path(__file__)),
        "capture_runs": capture_runs,
        "pairs": len(results),
        "successful": sum(item["accepted"] for item in results),
        "refused": sum(not item["accepted"] for item in results),
        "results": results,
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in ("results", "capture_runs")}, indent=2))


if __name__ == "__main__":
    main()
