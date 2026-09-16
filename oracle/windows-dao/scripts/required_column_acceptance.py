#!/usr/bin/env python3
"""Replay Required-column creation and independent mutation comparisons."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import struct
import tempfile
import uuid
import zipfile
from pathlib import Path

import column_property_checks as properties
import required_column_discovery as discovery
import relationship_system_indexes as system_indexes

catalog = discovery.catalog
require = discovery.require
identity = discovery.identity


def byte_identity(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def request_value(value, case):
    if value is None:
        return False if case["name"] == "Boolean" else None
    if value == "auto_increment":
        return 1
    require(isinstance(value, dict) and len(value) == 1, "one typed request value")
    kind, value = next(iter(value.items()))
    if kind in ("boolean", "byte", "integer", "long"):
        return value
    if kind in ("currency", "single", "double", "date_time"):
        return struct.pack({"currency": "<q", "single": "<f", "double": "<d", "date_time": "<d"}[kind], value).hex()
    if kind in ("text", "memo"):
        return value.encode("cp1252").hex()
    if kind == "guid":
        return uuid.UUID(bytes=bytes(value)).bytes_le.hex()
    require(kind in ("binary", "long_binary"), "known request kind")
    return bytes(value).hex() if value else None


def expected_outcome(case, operation):
    if operation["state"] == "null":
        return (False, 3314) if case["required"] and case["name"] != "Boolean" else (True, None)
    if operation["state"] == "special":
        if case["name"] in ("Text", "Memo") and not case["allow_zero_length"]:
            return False, 3315
        if case["name"] in ("Binary", "LongBinary") and case["required"]:
            return False, 3314
    return True, None


def property_observation(data, case):
    definition, pages, _ = catalog._discover_catalog(data)
    name, ordinal = (catalog._ordinal(definition, n) for n in ("Name", "LvProp"))
    for page in pages:
        image = catalog._page(data, page, "catalog properties")
        for entry in catalog._row_directory(image, page):
            if entry["hidden"]:
                continue
            fields = properties.row_layout(image[entry["start"]:entry["end"]], definition["columns"])
            if fields[name][1] != b"Rows":
                continue
            required = case["required"] and case["name"] != "AutoIncrement"
            if not fields[ordinal][0]:
                require(not required and case["name"] not in ("Text", "FixedText", "Memo"), "text policies and nondefault Required must be explicit")
                return {"payload_hex": None, "descriptor": None, "model": None}
            payload, descriptor = properties.property_payload(data, "Rows")
            model = discovery.dictionary_and_blocks(payload)
            observed = {}
            for block in model["blocks"]:
                require(block["name"] not in observed, "distinct field property blocks")
                records = {}
                for record in block["records"]:
                    raw = bytes.fromhex(record["raw_hex"])
                    require(raw[2:4] == b"\x01\x01" and raw[5:8] == b"\0\x01\0" and raw[8] in (0, 255), "exact Boolean property framing")
                    require(record["name"] not in records, "distinct field properties")
                    records[record["name"]] = record["value"]
                observed[block["name"]] = records
            _, expected = discovery.expected_property_model(case)
            allowed = {block["name"]: block["properties"] for block in expected}
            require(set(observed) <= set(allowed), "known property owners")
            require(set(model["dictionary"]) <= {"Required", "AllowZeroLength"}
                    and len(set(model["dictionary"])) == len(model["dictionary"]), "known distinct property dictionary")
            for field, wanted in allowed.items():
                actual = observed.get(field, {})
                require(set(actual) <= set(wanted), "known field properties")
                require(all(actual.get(key, False) == value for key, value in wanted.items()), "exact effective field properties")
            require(observed == allowed, "complete explicit named properties")
            return {"payload_hex": payload.hex(), "descriptor": descriptor, "model": model}
    raise ValueError("Rows catalog entry missing")


def allocation_observation(data, analysis, user_nodes, systems):
    records, claimed, metadata, system_pages = {}, {}, {0, 1}, set()
    locators, references = [], []
    free = set(analysis["free_pages"])
    for table in analysis["tables"].values():
        name, definition = table["name"], table["definition"]
        metadata.update(definition["pages"])
        local = set(definition["pages"])
        groups = [("table", definition["maps"]["owned"], definition["maps"]["available"])]
        groups.extend((f"index/{idx['index']}", idx["map"], None) for idx in definition["physical_indexes"])
        groups.extend((f"payload/{group['column']}", group["owned"], group["available"]) for group in definition["long_value_maps"])
        for role, owned, available in groups:
            record, members = properties.map_record(data, owned, f"{name}/{role}")
            records[f"{name}/{role}/owned"] = {"locator": owned, **record}
            local.update(members)
            for page in members:
                require(page not in claimed, "distinct page ownership")
                claimed[page] = f"{name}/{role}"
            if role.startswith("index/"):
                index = int(role.split("/")[1])
                nodes = user_nodes if name == "Rows" else systems[name]["indexes"][index]["nodes"]
                require(set(nodes) <= members, "all index nodes owned")
            for label, locator in (("owned", owned), ("available", available)):
                if locator is None:
                    continue
                current, selected = properties.map_record(data, locator, f"{name}/{role}/{label}")
                if label == "available":
                    require(selected <= members, "available pages are owned")
                    records[f"{name}/{role}/available"] = {"locator": locator, **current}
                locators.append((locator["page"], locator["row"]))
                refs = [page for page in current["references"] if page]
                references.extend(refs)
                metadata.add(locator["page"])
                metadata.update(refs)
                local.add(locator["page"])
                local.update(refs)
        if name.startswith("MSys"):
            system_pages.update(local)
    global_record, global_free = properties.map_record(data, {"page": 1, "row": 0}, "global")
    require(global_free == free, "global free inventory")
    metadata.update(page for page in global_record["references"] if page)
    require(len(locators) == len(set(locators)) and len(references) == len(set(references)), "distinct map rows and bitmap pages")
    require(not set(claimed) & metadata and not free & (set(claimed) | metadata), "disjoint allocated roles and free pages")
    require(free | set(claimed) | metadata == set(range(len(data) // 2048)), "every physical page classified")
    return {"maps": records, "free_pages": sorted(free), "global": global_record,
            "system_page_hashes": {str(page): hashlib.sha256(catalog._page(data, page, "system preservation")).hexdigest() for page in sorted(system_pages)}}


def observe(path, capture, case, expected_rows):
    data = path.read_bytes()
    require(identity(path) == capture["before"] == capture["after"], path.name + ": closed read-only capture")
    snapshot = capture["snapshot"]
    require(snapshot["version"] == "3.0" and snapshot["relations"] == [] and len(snapshot["tables"]) == 1, "complete DAO database inventory")
    table = snapshot["tables"][0]
    require(table["name"]["value"] == "Rows" and table["attributes"] == 0 and table["rows"] == expected_rows, path.name + ": complete DAO rows")
    fields = table["fields"]
    expected_fields = [("Id", 4, 4, 1, False, False), ("Payload", case["type"], case["size"], case["attributes"], case["required"] and case["name"] != "AutoIncrement", case["allow_zero_length"])]
    require([(f["name"]["value"], f["type"], f["size"], f["attributes"], f["required"], f["allow_zero_length"]) for f in fields] == expected_fields, "exact requested DAO schema")
    require(len(table["indexes"]) == 1, "one DAO primary index")
    index = table["indexes"][0]
    require(index["name"]["value"] == "PrimaryKey" and index["primary"] and index["unique"] and index["required"] and not index["foreign"] and not index["ignore_nulls"], "DAO primary flags")
    require([(field["name"]["value"], field["attributes"]) for field in index["fields"]] == [("Id", 0)], "DAO primary fields")
    require(set(table["index_reads"]) == {"PrimaryKey"}, "complete index read inventory")
    read = table["index_reads"]["PrimaryKey"]
    require(read["field"] == "Id" and read["error"] is None and read["traversal"] == expected_rows, "complete primary traversal")
    by_id = {row["Id"]: row for row in expected_rows}
    require([seek["query"] for seek in read["seek"]] == list(by_id) + [2147483000], "complete Seek inventory")
    require(all(seek["row"] == by_id.get(seek["query"]) and seek["no_match"] == (seek["query"] not in by_id) for seek in read["seek"]), "complete Seek values")
    analysis = catalog.analyze_checkpoint(data)
    named = {t["name"]: t for t in analysis["tables"].values()}
    require(set(named) == {"Rows", *system_indexes.SYSTEM_INDEXES}, "complete raw table inventory")
    raw_table = named["Rows"]
    definition = raw_table["definition"]
    physical_type = {"FixedText": "Text", "AutoIncrement": "Long", "DateTime": "Date", "Guid": "GUID"}.get(case["name"], case["name"])
    require([(c["name"], c["ordinal"], c["ordinal_repeat"], c["constant"], c["type"], c["size"], c["class"]) for c in definition["columns"]]
            == [("Id", 0, 0, 1, "Long", 4, 3), ("Payload", 1, 1, 1, physical_type, case["size"], 7 if case["name"] == "AutoIncrement" else 3 if case["attributes"] == 1 else 2)], "requested raw column schema")
    rows = discovery.raw_rows.rows(data, raw_table)
    require([{"Id": row["values"]["Id"], "Payload": discovery.api_from_raw(row["values"]["Payload"], case)} for row in rows] == expected_rows, "complete raw row values")
    physical = definition["physical_indexes"]
    require(len(physical) == 1 and physical[0]["keys"] == [{"column": 0, "direction": 1}] and physical[0]["flags"] == 9, "exact raw primary schema")
    idx = physical[0]
    nodes, entries = discovery.indexes.tree(data, idx["root"], definition["root"], [(4, False)])
    require(entries == sorted(discovery.long_key(row["values"]["Id"], row["locator"]) for row in rows), "complete physical keys and locators")
    require(idx["prefix_hex"] == "00000000" and idx["entry_count"] == len(rows), "exact primary prefixes")
    require([prop["value"] for prop in index["properties"] if prop["name"]["value"] == "DistinctCount"] == [str(len(rows))], "DAO distinct count agrees with raw prefix")
    systems = system_indexes.inventory(path)
    allocation = allocation_observation(data, analysis, [node["page"] for node in nodes], systems)
    stable_definition = copy.deepcopy(definition)
    stable_definition.pop("row_count")
    for physical_index in stable_definition["physical_indexes"]:
        physical_index.pop("entry_count")
        physical_index.pop("prefix_hex")
    masked = bytearray(data)
    offset = definition["row_count_offset"]
    masked[offset:offset + 4] = bytes(4)
    for physical_index in physical:
        offset = physical_index["entry_count_offset"]
        masked[offset - 4:offset + 4] = bytes(8)
    return {"identity": identity(path), "rows": rows, "schema": stable_definition,
            "definition_hashes": {str(page): hashlib.sha256(masked[page * 2048:(page + 1) * 2048]).hexdigest() for page in definition["pages"]},
            "index": {"root": idx["root"], "nodes": nodes, "entries_hex": [entry.hex() for entry in entries], "prefix_hex": idx["prefix_hex"], "count": idx["entry_count"]},
            "properties": property_observation(data, case), "system_indexes": systems, **allocation}


def compact_json(value):
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    raise TypeError(type(value).__name__)


def comparable_row(row, column, selected):
    result = copy.deepcopy(row)
    raw = bytearray.fromhex(row["raw_hex"])
    fields, _ = discovery.raw_rows.layout(bytes(raw), [
        {"ordinal": 0, "name": "Id", "type": "Long", "storage": "fixed", "fixed_offset": 0, "size": 4}, column])
    if selected and column["type"] != "Boolean" and fields[1] is None and column["storage"] == "fixed":
        # EXP-0283: bytes behind a clear fixed-field presence bit are unspecified.
        start = 1 + column["fixed_offset"]
        raw[start:start + column["size"]] = bytes(column["size"])
    if selected and row["descriptors"]:
        require(set(row["descriptors"]) == {"Payload"}, "only assigned long-value descriptor")
        descriptor = row["descriptors"]["Payload"]
        bounds = row["shape"]["boundaries"]
        start, end = bounds[column["variable_index"]:column["variable_index"] + 2]
        require(raw[start:end].hex() == descriptor["raw_hex"], "exact selected descriptor bytes")
        # Complete payload bytes and reachability are checked by raw_rows.rows.
        raw[start:end] = bytes(end - start)
        result["descriptors"] = {"Payload": {"length": descriptor["length"]}}
    result["raw_hex"] = raw.hex()
    return result


def evaluate_case(case, result, matrix, inputs, outbox):
    label = case["id"]
    require(result["document_type"] == "required_column_acceptance" and result["case"] == label and result["status"] == "pass" and result["error"] is None, label + ": successful receipt")
    environment = result["environment"]
    require(environment["provider"] == "DAO.DBEngine.36" and environment["version"] == "3.6" and environment["bits"] == 32 and environment["culture"] == "en-US" and environment["ansi"] == 1252, "exact provider environment")
    require(environment["dll_sha256"] == "4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac", "frozen DAO provider")
    creations = [item for item in matrix["creations"] if item["case"] == case]
    mutations = [item for item in matrix["mutations"] if item["case"] == case]
    require([item["id"] for item in result["creations"]] == [item["id"] for item in creations] and [item["id"] for item in result["mutations"]] == [item["id"] for item in mutations], "complete ordered receipt inventory")
    observations, sources = [], {}
    for prepared, receipt in zip(creations, result["creations"]):
        expected = [{"Id": row[0]["long"], "Payload": request_value(row[1], case)} for row in prepared["request"]["tables"][0]["rows"]]
        pair = {}
        for role, suffix in (("candidate", "rust"), ("control", "native")):
            saved = receipt[role]
            require(saved["file"] == prepared["id"] + f"-{suffix}.mdb", "creation filename")
            path = outbox / saved["file"]
            require(identity(path) == prepared[role + "_identity"] == identity(inputs / prepared[role]), "exact submitted creation image")
            pair[role] = observe(path, saved["capture"], case, expected)
            if prepared["populated"]:
                sources["candidate" if role == "candidate" else "native"] = (saved["capture"], pair[role])
        require(discovery.normalized_snapshot(receipt["candidate"]["capture"]["snapshot"]) == discovery.normalized_snapshot(receipt["control"]["capture"]["snapshot"]), prepared["id"] + ": complete creation DAO comparison")
        candidate_model = pair["candidate"]["properties"]["model"]
        native_model = pair["control"]["properties"]["model"]
        if candidate_model is not None:
            def named_properties(model):
                return {block["name"]: {record["name"]: record["value"] for record in block["records"]} for block in model["blocks"]}
            require(candidate_model["dictionary"] == native_model["dictionary"] and named_properties(candidate_model) == named_properties(native_model), "creation named property models match")
        observations.append({"id": prepared["id"], "kind": "creation", **pair})
    for prepared, receipt in zip(mutations, result["mutations"]):
        accepted, error = expected_outcome(case, prepared["operation"])
        require(prepared["expected_accepted"] == accepted and prepared["expected_native_error"] == error and (prepared["exit"] == 0) == accepted, "independent expected mutation outcome")
        operation = receipt["operation"]
        require(operation["operation"] == prepared["operation"] and operation["accepted"] == accepted, "native operation outcome")
        require(([] if operation["error"] is None else operation["error"]["numbers"]) == ([] if error is None else [error]), "exact native error numbers")
        initial_capture, initial = sources[prepared["origin"]]
        require(receipt["before"] == prepared["source_identity"] == initial["identity"] == identity(inputs / prepared["source"]), "shared one-row mutation source")
        expected = copy.deepcopy(initial_capture["snapshot"]["tables"][0]["rows"])
        if accepted:
            new_row = {"Id": prepared["request"]["values"][0]["long"], "Payload": request_value(prepared["request"]["values"][1], case)}
            expected = sorted(expected + [new_row], key=lambda row: row["Id"]) if prepared["operation"]["kind"] == "insert" else [new_row]
        pair = {}
        for role, suffix in (("candidate", "rust"), ("control", "native")):
            saved = receipt[role]
            require(saved["file"] == prepared["id"] + f"-{suffix}.mdb", "mutation filename")
            path = outbox / saved["file"]
            if role == "candidate":
                require(identity(path) == prepared["stage_identity"] == identity(inputs / prepared["stage"]), "exact submitted Rust mutation")
            if not accepted:
                require(identity(path) == initial["identity"], "rejected write preserves whole file")
            observation = observe(path, saved["capture"], case, expected)
            for key in ("schema", "definition_hashes", "properties", "system_indexes", "system_page_hashes"):
                require(observation[key] == initial[key], prepared["id"] + ": preserved " + key)
            if prepared["operation"]["kind"] == "insert":
                require(observation["rows"][0] == initial["rows"][0], "unselected row preserved exactly")
            pair[role] = observation
        require(discovery.normalized_snapshot(receipt["candidate"]["capture"]["snapshot"]) == discovery.normalized_snapshot(receipt["control"]["capture"]["snapshot"]), prepared["id"] + ": complete mutation DAO comparison")
        require(pair["candidate"]["index"]["entries_hex"] == pair["control"]["index"]["entries_hex"], "same-source complete physical keys and locators")
        selected_id = prepared["operation"]["id"]
        column = initial["schema"]["columns"][1]
        require([comparable_row(row, column, row["values"]["Id"] == selected_id) for row in pair["candidate"]["rows"]]
                == [comparable_row(row, column, row["values"]["Id"] == selected_id) for row in pair["control"]["rows"]], prepared["id"] + ": exact row structure and bytes outside assigned null padding/payload placement")
        observations.append({"id": prepared["id"], "kind": "mutation", "accepted": accepted, "native_error": error, **pair})
    return {"case": case, "environment": environment, "observations": observations}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence_root.resolve()
    run = root / "runs" / args.run_id
    inbox, outbox = run / "inbox", run / "outbox"
    pins = json.loads((root / "source-pins.json").read_text())
    archive = inbox / "required-inputs.zip"
    require(identity(archive) == pins["bundle"] and identity(inbox / "script.ps1") == pins["producer"], "frozen submitted inputs")
    require({p.name for p in inbox.iterdir()} == {"required-inputs.zip", "script.ps1"}, "exact inbox inventory")
    with tempfile.TemporaryDirectory(prefix="jet3-required-replay-") as temporary:
        inputs = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            require(len(bundle.namelist()) == len(set(bundle.namelist())) and all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in bundle.namelist()), "unique relative input paths")
            matrix = json.loads(bundle.read("matrix.json"))
            expected_inputs = {"matrix.json"} | {item[role] for item in matrix["creations"] for role in ("candidate", "control")} | {item[role] for item in matrix["mutations"] for role in ("source", "stage")}
            require(set(bundle.namelist()) == expected_inputs, "complete ZIP inventory")
            bundle.extractall(inputs)
        require(identity(inputs / "matrix.json") == pins["matrix"] and matrix["source_revision"] == pins["source_revision"] and matrix["binary"] == pins["binary"] and matrix["native_manifest"] == pins["native_discovery_manifest"], "source and matrix linkage")
        cases = json.loads((Path(__file__).parent / "required_column_matrix.json").read_text())["cases"]
        require(len(matrix["creations"]) == 72 and len(matrix["mutations"]) == 204 and len(matrix["initial_null_checks"]) == 36, "complete acceptance scope")
        require([item["case"] for item in matrix["creations"]] == [case for case in cases for _ in range(2)], "all 36 requested schemas")
        all_ids = [item["id"] for item in matrix["creations"] + matrix["mutations"]]
        require(len(all_ids) == len(set(all_ids)), "unique pair identities")
        expected_files = {"workers.json", "exit.txt", "log.txt"} | {case["id"] + "-result.json" for case in cases} | {name + f"-{origin}.mdb" for name in all_ids for origin in ("rust", "native")}
        require({path.name for path in outbox.iterdir()} == expected_files, "complete outbox inventory")
        require((outbox / "exit.txt").read_text() == "0\n" and (outbox / "log.txt").read_bytes() == b"", "successful guest exit and empty log")
        workers = json.loads((outbox / "workers.json").read_text())
        require(workers["document_type"] == "required_column_acceptance_workers" and len(workers["workers"]) == 36 and {w["case"] for w in workers["workers"]} == {case["id"] for case in cases}, "complete worker inventory")
        for key in ("archive", "matrix", "script"):
            require(workers[key] == pins[{"archive": "bundle", "script": "producer"}.get(key, key)], "worker input identity")
        results, environment = [], None
        for case in cases:
            path = outbox / (case["id"] + "-result.json")
            worker = next(w for w in workers["workers"] if w["case"] == case["id"])
            require(worker["exit_code"] == 0 and worker["result"] == identity(path), "successful linked worker")
            receipt = json.loads(path.read_text())
            require(all(receipt[key] == workers[key] for key in ("archive", "matrix", "script")) and receipt["source_revision"] == pins["source_revision"], "case input linkage")
            result = evaluate_case(case, receipt, matrix, inputs, outbox)
            environment = result["environment"] if environment is None else environment
            require(result["environment"] == environment, "consistent provider environment")
            results.append(result)
        checks = matrix["initial_null_checks"]
        require([check["case"] for check in checks] == [case["id"] for case in cases], "local initial-null check inventory")
        for case, check in zip(cases, checks):
            expected = case["name"] == "Boolean" or (not case["required"] and case["name"] != "AutoIncrement")
            require(check["expected_accepted"] == check["published"] == (check["exit"] == 0) == expected, "local initial-null publication outcome")
        accepted = sum(item["accepted"] for result in results for item in result["observations"] if item["kind"] == "mutation")
        require(accepted == 134, "exact 134 successes and 70 refusals")
        report = {"document_type": "required_column_acceptance_evaluation", "status": "pass", "run_id": args.run_id,
                  "source_pins": pins, "evaluator": identity(Path(__file__)), "provider": environment,
                  "creation_pairs": 72, "mutation_pairs": 204, "accepted_mutations": accepted, "refused_mutations": 204 - accepted,
                  "closed_captures": 552, "initial_null_checks": checks, "cases": results}
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True, default=compact_json) + "\n")
        print(json.dumps({key: value for key, value in report.items() if key not in ("cases", "initial_null_checks", "source_pins")}))


if __name__ == "__main__":
    main()
