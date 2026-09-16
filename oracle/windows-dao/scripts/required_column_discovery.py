#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
import allocation_lifecycle_structure as allocation
import column_property_checks as empty_values
import numeric_index_mutation_structure as indexes
import relationship_mutation_structure as raw_rows
import system_catalog as catalog

for module in (catalog, indexes.catalog, allocation.catalog):
    module.MAX_PAGES = 8192
    module.MAX_ROWS_PER_PAGE = 1019
    module.MAX_TABLES = 64
    module.MAX_COLUMNS = 255
    module.MAX_TEXT = 10000


def require(ok, detail):
    if not ok:
        raise ValueError(detail)


def identity(path: Path):
    data = path.read_bytes()
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def normalized_snapshot(snapshot):
    value = copy.deepcopy(snapshot)
    for table in value["tables"]:
        for prop in table["properties"]:
            if prop["name"]["value"] in ("DateCreated", "LastUpdated"):
                prop["value"] = "<timestamp>"
    return value


def dictionary_and_blocks(payload: bytes):
    require(payload[:4] == b"KKD\0" and len(payload) >= 8, "KKD0 property payload")
    dictionary_length = int.from_bytes(payload[4:8], "little")
    dictionary_end = 4 + dictionary_length
    require(8 <= dictionary_end <= len(payload), "bounded property dictionary")
    cursor, dictionary = 8, []
    require(payload[cursor:cursor + 2] == b"\x80\0", "property dictionary marker")
    cursor += 2
    while cursor < dictionary_end:
        require(cursor + 2 <= dictionary_end, "dictionary name length")
        length = int.from_bytes(payload[cursor:cursor + 2], "little")
        cursor += 2
        require(cursor + length <= dictionary_end, "bounded dictionary name")
        dictionary.append(payload[cursor:cursor + length].decode("cp1252"))
        cursor += length
    require(cursor == dictionary_end, "dictionary consumed")
    blocks = []
    cursor = dictionary_end
    while cursor < len(payload):
        require(cursor + 12 <= len(payload), "complete field property block")
        length = int.from_bytes(payload[cursor:cursor + 4], "little")
        end = cursor + length
        nested = int.from_bytes(payload[cursor + 6:cursor + 10], "little")
        name_length = int.from_bytes(payload[cursor + 10:cursor + 12], "little")
        require(payload[cursor + 4:cursor + 6] == b"\x01\0", "field block type")
        require(end <= len(payload) and length in (21 + name_length, 30 + name_length), "field block length")
        require(nested == 6 + name_length, "field nested length")
        name = payload[cursor + 12:cursor + 12 + name_length].decode("cp1252")
        records = payload[cursor + 12 + name_length:end]
        require(len(records) in (9, 18), "one or two property records")
        parsed = []
        for offset in range(0, len(records), 9):
            record = records[offset:offset + 9]
            require(int.from_bytes(record[:2], "little") == 9, "property record length")
            ordinal = record[4]
            require(ordinal < len(dictionary), "property dictionary ordinal")
            parsed.append({"name": dictionary[ordinal], "value": bool(record[8]), "raw_hex": record.hex()})
        blocks.append({"name": name, "length": length, "records": parsed, "raw_hex": payload[cursor:end].hex()})
        cursor = end
    require(cursor == len(payload), "property payload consumed")
    return {"dictionary": dictionary, "dictionary_hex": payload[:dictionary_end].hex(), "blocks": blocks}


def expected_property_model(case):
    dictionary = ["Required"]
    if case["name"] in ("Text", "FixedText", "Memo"):
        dictionary.append("AllowZeroLength")
    blocks = [{"name": "Id", "properties": {"Required": False}}]
    if case["name"] != "AutoIncrement":
        props = {"Required": bool(case["required"])}
        if len(dictionary) == 2:
            props["AllowZeroLength"] = bool(case["allow_zero_length"])
        blocks.append({"name": "Payload", "properties": props})
    return dictionary, blocks


def assert_properties(model, case):
    dictionary, expected = expected_property_model(case)
    require(model["dictionary"] == dictionary, case["id"] + ": exact property dictionary")
    actual = [{"name": b["name"], "properties": {r["name"]: r["value"] for r in b["records"]}} for b in model["blocks"]]
    require(actual == expected, case["id"] + ": exact property blocks")


def expected_outcome(case, ordinal):
    name, required, azl = case["name"], bool(case["required"]), bool(case["allow_zero_length"])
    if name == "AutoIncrement":
        accepted = [True, False, True, True, False, False][ordinal - 1]
        code = {2: 3162, 5: 3164, 6: 3164}.get(ordinal)
        return accepted, code
    if name == "Boolean":
        return True, None
    if ordinal in (2, 3, 5) and required:
        return False, 3314
    if ordinal == 4:
        if name in ("Text", "Memo") and not azl:
            return False, 3315
        if name in ("Binary", "LongBinary") and required:
            return False, 3314
    return True, None


def value_for(case, state):
    name = case["name"]
    if state == "null" or state == "omit":
        return False if name == "Boolean" else None
    if state == "empty":
        if name == "FixedText":
            return "20202020"
        if name in ("Binary", "LongBinary"):
            return None
        return ""
    zero = state == "zero"
    values = {
        "Boolean": not zero,
        "Byte": 0 if zero else 23,
        "Integer": 0 if zero else -123,
        "Long": 0 if zero else 123,
        "AutoIncrement": 0 if zero else 123,
        "Currency": "0000000000000000" if zero else "40e2010000000000",
        "Single": "00000000" if zero else "0000a03f",
        "Double": "0000000000000000" if zero else "000000000000f43f",
        "DateTime": "0000000000000000" if zero else "0000000008f9e540",
        "Guid": "0" * 32 if zero else "78563412341278569abcdef012345678",
        "Text": "74657874",
        "FixedText": "41424344",
        "Binary": "0041ff",
        "Memo": "6d656d6f",
        "LongBinary": "0041ff",
    }
    return values[name]


def expected_api_rows(case, stages):
    rows = {}
    generated = 0
    results = []
    for ordinal, stage in enumerate(stages, 1):
        op = stage["operation"]
        if case["name"] == "AutoIncrement" and op["kind"] == "insert":
            generated += 1
        if stage["accepted"]:
            if op["kind"] == "insert":
                value = generated if case["name"] == "AutoIncrement" and op["state"] == "omit" else value_for(case, op["state"])
                rows[op["id"]] = value
            else:
                rows[op["id"]] = value_for(case, op["state"])
        results.append([{"Id": key, "Payload": rows[key]} for key in sorted(rows)])
    return results


def api_from_raw(value, case):
    name = case["name"]
    if name == "Boolean":
        return bool(value)
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict) and set(value) == {"raw_hex"}:
        return value["raw_hex"]
    if name == "DateTime":
        return struct.pack("<d", value).hex()
    if name in ("Text", "FixedText"):
        return value.encode("cp1252").hex()
    return value


def stable_raw_projection(observation):
    rows = []
    for row in observation["rows"]:
        row = copy.deepcopy(row)
        row.pop("raw_hex")
        rows.append(row)
    return {
        "rows": rows,
        "index_entries": observation["index"]["entries_hex"],
        "property_payload": observation["property_payload"]["payload_hex"],
    }


def long_key(value, locator):
    suffix = locator["page"].to_bytes(3, "big") + bytes([locator["row"]])
    return b"\x7f" + ((value & 0xffffffff) ^ 0x80000000).to_bytes(4, "big") + suffix


def analyze_mdb(path: Path, capture, case, expected_rows, expected_property_hex):
    data = path.read_bytes()
    require(identity(path) == capture["after"] == capture["before"], path.name + ": exact capture identity")
    analysis = catalog.analyze_checkpoint(data)
    tables = [table for table in analysis["tables"].values() if table["name"] == "Rows"]
    require(len(tables) == 1, path.name + ": one Rows table")
    table = tables[0]
    rows = raw_rows.rows(data, table)
    actual_api = [{"Id": row["values"]["Id"], "Payload": api_from_raw(row["values"]["Payload"], case)} for row in rows]
    require(actual_api == expected_rows, path.name + ": complete raw values")
    physical = table["definition"]["physical_indexes"]
    require(len(physical) == 1 and physical[0]["keys"] == [{"column": 0, "direction": 1}], path.name + ": exact primary physical index")
    idx = physical[0]
    nodes, entries = indexes.tree(data, idx["root"], table["definition"]["root"], [(4, False)])
    expected_entries = sorted(long_key(row["values"]["Id"], row["locator"]) for row in rows)
    require(entries == expected_entries, path.name + ": complete physical primary keys")
    require(idx["prefix_hex"] == "00000000" and idx["entry_count"] == len(rows), path.name + ": exact primary counters")
    payload, descriptor = empty_values.property_payload(data, "Rows")
    model = dictionary_and_blocks(payload)
    assert_properties(model, case)
    if expected_property_hex is not None:
        require(payload.hex() == expected_property_hex, path.name + ": property payload stable")
    return {
        "identity": identity(path),
        "rows": rows,
        "index": {"root": idx["root"], "first_word": 0, "counter": idx["entry_count"], "nodes": nodes, "entries_hex": [x.hex() for x in entries]},
        "property_payload": {"payload_hex": payload.hex(), "descriptor": descriptor, "model": model},
        "free_pages": analysis["free_pages"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--run-id", default="20260916T050547Z-required-columns-r1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence_root.resolve()
    run = root / "runs" / args.run_id
    inbox, outbox = run / "inbox", run / "outbox"
    matrix = json.loads((root / "matrix.json").read_text())
    matrix_id, script_id = identity(root / "matrix.json"), identity(root / "required_columns.ps1")
    require(matrix["document_type"] == "required_column_native_discovery" and matrix["replicas"] == 2 and len(matrix["cases"]) == 36, "exact matrix inventory")
    expected_files = {"workers.json", "exit.txt", "log.txt"}
    for replica in (1, 2):
        for case in matrix["cases"]:
            stem = f"r{replica}-{case['id']}"
            expected_files.add(stem + "-result.json")
            expected_files.add(stem + "-initial.mdb")
            expected_files.update(stem + f"-{ordinal:02}.mdb" for ordinal in range(1, 7))
    require({p.name for p in outbox.iterdir()} == expected_files, "exact outbox inventory")
    require((outbox / "exit.txt").read_text() == "0\n" and (outbox / "log.txt").read_bytes() == b"", "exact guest exit and log")
    require({p.name for p in inbox.iterdir()} == {"matrix.json", "script.ps1"}, "exact inbox inventory")
    require(identity(inbox / "matrix.json") == matrix_id and identity(inbox / "script.ps1") == script_id, "submitted input linkage")
    workers = json.loads((outbox / "workers.json").read_text())
    require(workers["document_type"] == "required_column_workers" and workers["matrix"] == matrix_id, "worker header")
    expected_workers = {(r, c["id"]) for c in matrix["cases"] for r in (1, 2)}
    require(len(workers["workers"]) == 72 and {(w["replica"], w["case"]) for w in workers["workers"]} == expected_workers and all(w["exit_code"] == 0 for w in workers["workers"]), "exact successful worker inventory")
    observations, normalized = [], {}
    expected_environment = None
    for case in matrix["cases"]:
        case_observations = []
        for replica in (1, 2):
            stem = f"r{replica}-{case['id']}"
            result_path = outbox / (stem + "-result.json")
            result = json.loads(result_path.read_text())
            require(result["document_type"] == "required_column_native_discovery" and result["case"] == case and result["replica"] == replica, stem + ": receipt identity")
            require(result["matrix"] == matrix_id and result["script"] == script_id and result["status"] == "pass" and result["schema_error"] is None and result["error"] is None, stem + ": complete successful receipt")
            environment = result["environment"]
            require(environment["provider"] == "DAO.DBEngine.36" and environment["version"] == "3.6" and environment["bits"] == 32 and environment["culture"] == "en-US" and environment["ansi"] == 1252, stem + ": provider environment")
            if expected_environment is None:
                expected_environment = environment
            require(environment == expected_environment, stem + ": identical provider identity")
            require(len(result["stages"]) == 6, stem + ": six stages")
            expected_rows = expected_api_rows(case, result["stages"])
            stage_records = []
            previous_identity = result["initial"]["capture"]["after"]
            initial_path = outbox / (stem + "-initial.mdb")
            require(result["initial"]["file"] == initial_path.name and identity(initial_path) == result["initial"]["capture"]["after"], stem + ": initial file linkage")
            property_payload, _ = empty_values.property_payload(initial_path.read_bytes(), "Rows")
            initial_raw = analyze_mdb(initial_path, result["initial"]["capture"], case, [], property_payload.hex())
            snapshots = [normalized_snapshot(result["initial"]["capture"]["snapshot"])]
            raw_stages = [initial_raw]
            for ordinal, stage in enumerate(result["stages"], 1):
                accepted, error_code = expected_outcome(case, ordinal)
                numbers = [] if stage["error"] is None else stage["error"]["numbers"]
                require(stage["accepted"] == accepted and numbers == ([] if error_code is None else [error_code]), stem + f": stage {ordinal} exact outcome")
                expected_operation = [
                    {"kind": "insert", "id": 1, "state": "omit" if case["name"] == "AutoIncrement" else "value"},
                    {"kind": "insert", "id": 2, "state": "null"},
                    {"kind": "insert", "id": 3, "state": "omit"},
                    {"kind": "insert", "id": 4, "state": "empty" if case["type"] in (9, 10, 11, 12) else "zero"},
                    {"kind": "update", "id": 1, "state": "null"},
                    {"kind": "update", "id": 1, "state": "value"},
                ][ordinal - 1]
                require(stage["operation"] == expected_operation, stem + f": stage {ordinal} operation")
                stage_path = outbox / stage["stage"]["file"]
                require(stage_path.name == stem + f"-{ordinal:02}.mdb", stem + f": stage {ordinal} filename")
                changed = stage["stage"]["capture"]["after"] != previous_identity
                if not accepted and not (case["name"] == "AutoIncrement" and ordinal == 2):
                    require(not changed, stem + f": stage {ordinal} rejected write exact preservation")
                if case["name"] == "AutoIncrement" and ordinal == 2:
                    require(changed, stem + ": failed null Auto assignment consumes counter")
                raw = analyze_mdb(stage_path, stage["stage"]["capture"], case, expected_rows[ordinal - 1], property_payload.hex())
                table = stage["stage"]["capture"]["snapshot"]["tables"]
                require(len(table) == 1 and table[0]["name"]["value"] == "Rows" and table[0]["rows"] == expected_rows[ordinal - 1], stem + f": stage {ordinal} complete DAO rows")
                require(stage["stage"]["capture"]["snapshot"]["relations"] == [], stem + f": stage {ordinal} no relationships")
                index_reads = table[0]["index_reads"]
                require(set(index_reads) == {"PrimaryKey"}, stem + f": stage {ordinal} exact index inventory")
                read = index_reads["PrimaryKey"]
                require(read["error"] is None and read["traversal"] == expected_rows[ordinal - 1], stem + f": stage {ordinal} complete traversal")
                seeks = read["seek"]
                expected_queries = [row["Id"] for row in expected_rows[ordinal - 1]] + [2147483000]
                require([seek["query"] for seek in seeks] == expected_queries, stem + f": stage {ordinal} exact Seek inventory")
                expected_by_id = {row["Id"]: row for row in expected_rows[ordinal - 1]}
                for seek in seeks:
                    expected = expected_by_id.get(seek["query"])
                    require(seek["row"] == expected and seek["no_match"] == (expected is None), stem + f": stage {ordinal} complete Seek result")
                snapshots.append(normalized_snapshot(stage["stage"]["capture"]["snapshot"]))
                raw_stages.append(raw)
                stage_records.append({"ordinal": ordinal, "accepted": accepted, "error_numbers": numbers, "changed": changed, "file": stage_path.name, "identity": identity(stage_path), "rows": expected_rows[ordinal - 1], "raw": raw})
                previous_identity = stage["stage"]["capture"]["after"]
            normalized[replica] = snapshots
            case_observations.append({"replica": replica, "result": identity(result_path), "initial": {"file": initial_path.name, "raw": initial_raw}, "stages": stage_records})
        require(normalized[1] == normalized[2], case["id"] + ": replicas exact after timestamp normalization")
        for ordinal in range(7):
            a = case_observations[0]["initial"]["raw"] if ordinal == 0 else case_observations[0]["stages"][ordinal - 1]["raw"]
            b = case_observations[1]["initial"]["raw"] if ordinal == 0 else case_observations[1]["stages"][ordinal - 1]["raw"]
            require(stable_raw_projection(a) == stable_raw_projection(b), case["id"] + f": replica raw semantics stage {ordinal}")
        observations.append({"case": case, "replicas": case_observations})
    report = {
        "document_type": "required_column_native_discovery_evaluation",
        "status": "pass",
        "run_id": args.run_id,
        "provider": expected_environment,
        "matrix": matrix_id,
        "producer": script_id,
        "workers": identity(outbox / "workers.json"),
        "counts": {"cases": 36, "replicas": 2, "receipts": 72, "checkpoints": 504, "operations": 432},
        "observations": observations,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, default=lambda value: value.hex() if isinstance(value, bytes) else value) + "\n")
    print(json.dumps({"status": "pass", "output": str(args.output), "identity": identity(args.output), "counts": report["counts"]}, indent=2))


if __name__ == "__main__":
    main()
