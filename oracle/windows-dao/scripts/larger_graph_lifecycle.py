#!/usr/bin/env python3
"""Evaluate relationship-index mutation lifecycles from identical sources."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


import larger_graph_checks as discovery
import larger_graph_allocation as allocation
from larger_graph_property_checks import GetterSweep
import system_catalog as catalog
import relationship_system_indexes as system_index_inventory
from relationship_mutation_structure import logical_relation, map_info

PROVIDER = {"ansi": 1252, "bits": 32, "culture": "en-US",
            "dll_sha256": "4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac",
            "dll_version": "03.60.9765.0", "os": "Microsoft Windows NT 10.0.20348.0",
            "provider": "DAO.DBEngine.36", "version": "3.6"}


def req(ok, message):
    if not ok:
        raise AssertionError(message)


def ident(path):
    data = path.read_bytes()
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def event_case(group, event):
    case = copy.deepcopy(group["case"])
    for table in case["tables"]:
        table["rows"] = event["expected_rows"][table["name"]]
    return case


def pseudo(capture, case, replica):
    return {
        "status": "pass",
        "error": None,
        "table_error": None,
        "capture": capture,
        "replica": replica,
        "environment": {},
        "relations": [{"name": relation["name"], "error": None} for relation in case["relations"]],
    }


def stable_snapshot(snapshot):
    return discovery.normalized_snapshot(snapshot)


def raw_static(raw):
    """Schema, aliases, roots and map locators, excluding row/index state."""
    tables = {}
    for name, table in raw["tables"].items():
        tables[name] = {
            "root": table["root"],
            "columns": table["columns"],
            "logical_indexes": table["logical_indexes"],
            "maps": table["maps"],
            "long_value_maps": table["long_value_maps"],
            "physical_indexes": [
                {key: value for key, value in index.items()
                 if key not in ("first_word", "second_word", "mapped_pages", "nodes", "entries_hex")}
                for index in table["physical_indexes"]
            ],
        }
    return {
        "tables": tables,
        "relationship_rows": raw["relationship_rows"],
        "relationship_objects": raw["relationship_objects"],
        "relationship_aces": raw["relationship_aces"],
        "relationship_system_indexes": raw["relationship_system_indexes"],
    }


def prefixes(raw):
    return {
        name: [[index["first_word"], index["second_word"]]
               for index in table["physical_indexes"]]
        for name, table in raw["tables"].items()
    }


def physical_keys(raw):
    return {
        name: [
            {
                "index": index["index"],
                "flags": index["flags"],
                "keys": index["keys"],
                "entries_hex": index.get("entries_hex", []),
            }
            for index in table["physical_indexes"]
        ]
        for name, table in raw["tables"].items()
    }


def tables_without_prefixes(raw):
    value = copy.deepcopy(raw["tables"])
    for table in value.values():
        for index in table["physical_indexes"]:
            index.pop("first_word", None)
            index.pop("second_word", None)
    return value


def tables_with_payload_placement_normalized(raw, allowed, null_allowed):
    """Normalize requested null padding (EXP-0264) and selected payload locations."""
    value = tables_without_prefixes(raw)
    for table_name, row_id, column_name in null_allowed:
        table = value[table_name]
        rows = [row for row in table["rows"] if row["values"].get("Id") == row_id]
        if not rows:
            continue
        req(len(rows) == 1, "unique requested null row")
        row = rows[0]
        column = next(column for column in table["columns"] if column["name"] == column_name)
        req(column["type"] == "Long" and column["storage"] == "fixed" and column["size"] == 4,
            "requested null Long storage")
        raw_bytes = bytearray.fromhex(row["raw_hex"])
        presence_length = (len(table["columns"]) + 7) // 8
        ordinal = column["ordinal"]
        presence = raw_bytes[-presence_length:]
        req(row["values"][column_name] is None and not presence[ordinal // 8] & (1 << (ordinal % 8)),
            "requested null value and clear presence bit")
        start = 1 + column["fixed_offset"]
        req(start + 4 <= len(raw_bytes) - presence_length, "bounded null fixed span")
        raw_bytes[start:start + 4] = bytes(4)
        row["raw_hex"] = raw_bytes.hex()
        row["raw_sha256"] = "<NULL_PADDING>"
    for table_name, row_id, column_name in allowed:
        rows = [row for row in value[table_name]["rows"] if row["values"].get("Id") == row_id]
        if not rows:
            continue
        req(len(rows) == 1 and column_name in rows[0]["descriptors"], "payload placement exception target")
        descriptor = rows[0]["descriptors"][column_name]
        raw_descriptor = descriptor["raw_hex"]
        req(rows[0]["raw_hex"].count(raw_descriptor) == 1, "unique selected payload descriptor bytes")
        rows[0]["raw_hex"] = rows[0]["raw_hex"].replace(raw_descriptor, "<PLACEMENT>", 1)
        descriptor["raw_hex"] = "<PLACEMENT>"
        descriptor["locator"] = "<PLACEMENT>"
        rows[0]["raw_sha256"] = "<PLACEMENT>"
    return value


def inbox_for(outbox):
    retained = outbox.parent / "inbox"
    if retained.is_dir():
        return retained
    return outbox.parent.parent / "inbox" / outbox.name


def native_assignment(case, spec):
    request = spec["request"]
    table = next(table for table in case["tables"] if table["name"] == request["table"])
    kind = request["operation"]
    req(kind in ("insert", "replace", "delete"), "native assignment operation")
    fields = []
    if kind != "delete":
        req(len(request["values"]) == len(table["fields"]), "complete native assignment row")
        for field, cell in zip(table["fields"], request["values"]):
            value = None
            if cell is not None:
                key = {4: "long", 10: "text", 12: "memo"}[field["type"]]
                req(isinstance(cell, dict) and set(cell) == {key}, "typed native assignment")
                value = cell[key] if field["type"] == 4 else bytes(cell[key]).decode("cp1252")
            if field["name"] == "Id":
                req(value == spec["operation"]["id"], "unchanged logical Id")
                if kind == "replace":
                    continue
            fields.append({"name": field["name"], "value": value})
    return {"operation": kind, "table": request["table"], "id": spec["operation"]["id"], "fields": fields}


def foreign_selectors(case, raw, assignment):
    table_name = assignment["table"]
    assigned = {field["name"] for field in assignment["fields"]}
    relations = [relation for relation in case["relations"]
                 if relation["child"] == table_name and
                 (assignment["operation"] == "delete" or relation["child_field"] in assigned)]
    selected = set()
    for relation in relations:
        records = [logical_relation(item["raw_hex"])
                   for item in raw["tables"][table_name]["logical_indexes"]
                   if item["class"] == 2 and item["name"] == relation["name"]]
        req(len(records) == 1, "exact assigned foreign logical relation record")
        selected.add(records[0]["physical_index"])
    return sorted(selected)


def snapshot_with_expected_counters(observation, expected):
    """Validate every exposed count, then project explicitly expected prefix effects."""
    snapshot = stable_snapshot(observation["snapshot"])
    for table in snapshot["tables"]:
        name = table["name"]["value"]
        raw = observation["raw"]["tables"][name]
        logical = {index["name"]: index for index in raw["logical_indexes"]}
        for index in table["indexes"]:
            record = logical[index["name"]["value"]]
            physical = (logical_relation(record["raw_hex"])["physical_index"] if record["class"] == 2
                        else int.from_bytes(bytes.fromhex(record["raw_hex"])[4:8], "little"))
            properties = [prop for prop in index["properties"] if prop["name"]["value"] == "DistinctCount"]
            req(len(properties) == 1, "one exposed DistinctCount property")
            prop = properties[0]
            req(prop["type"] == 4 and prop["is_null"] is False
                and prop["value"] == str(raw["physical_indexes"][physical]["second_word"]),
                "DAO DistinctCount equals the complete raw physical prefix")
            prop["value"] = str(expected[name][physical][1])
    return snapshot


def system_page_hashes(path):
    data = path.read_bytes()
    analysis = catalog.analyze_checkpoint(data)
    named = {table["name"]: table for table in analysis["tables"].values()}
    pages = set()
    for name, table in named.items():
        if not name.startswith("MSys"):
            continue
        definition = table["definition"]
        pages.update(definition["pages"])
        locators = list(definition["maps"].values())
        locators.extend(index["map"] for index in definition["physical_indexes"])
        locators.extend(group[role] for group in definition["long_value_maps"] for role in ("owned", "available"))
        for locator in locators:
            record, members = map_info(data, locator, f"{name} system map")
            pages.add(locator["page"])
            pages.update(page for page in record["references"] if page)
            pages.update(members)
    return {str(page): hashlib.sha256(data[page * 2048:(page + 1) * 2048]).hexdigest()
            for page in sorted(pages)}


def diff_offsets(before, after):
    if len(before) != len(after):
        return [-1]
    return [position for position, pair in enumerate(zip(before, after)) if pair[0] != pair[1]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("outbox", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--producer", type=Path, default=HERE / "larger_graph_lifecycle.ps1")
    parser.add_argument("--getter-run", type=Path, required=True)
    parser.add_argument("--getter-producer", type=Path, default=HERE / "larger_graph_property_getters.ps1")
    args = parser.parse_args()
    getters = GetterSweep(args.getter_run, args.getter_producer, args.outbox.parent.name, PROVIDER)

    config = json.loads(args.config.read_text())
    pins_path = args.evidence_root / "source-pins.json"
    pins = json.loads(pins_path.read_text())
    req(config["source_pins"] == ident(pins_path)
        and config["source_revision"] == pins["source_revision"]
        and config["bundle"] == ident(args.bundle),
        "source and lifecycle input pins")

    with zipfile.ZipFile(args.bundle) as archive:
        matrix_bytes = archive.read("matrix.json")
        matrix = json.loads(matrix_bytes)
        groups = matrix["groups"]
        req(len(groups) == 10 and sum(len(group["events"]) for group in groups) == 80,
            "matrix group/event inventory")
        names = [name for name in archive.namelist() if not name.endswith("/")]
        expected_zip = {"matrix.json"}
        expected_zip.update(group["initial"] for group in groups)
        expected_zip.update(event["stage"] for group in groups for event in group["events"])
        req(len(names) == len(set(names)) and set(names) == expected_zip,
            "exact unique lifecycle ZIP inventory")
        expected = {"workers.json", "exit.txt", "log.txt"}
        for group in groups:
            expected.add(f"{group['id']}-result.json")
            for event in group["events"]:
                label = Path(event["stage"]).stem
                expected.add(f"{group['id']}-{label}-native.mdb")
                expected.add(f"{group['id']}-{label}-rust.mdb")
        actual = {path.name for path in args.outbox.iterdir() if path.is_file()}
        req(actual == expected,
            f"exact outbox inventory missing={sorted(expected-actual)} extra={sorted(actual-expected)}")
        req((args.outbox / "exit.txt").read_text().strip() == "0", "wrapper exit")
        req(not (args.outbox / "log.txt").read_text(encoding="utf-8-sig").strip(), "wrapper log")
        inbox = inbox_for(args.outbox)
        req({path.name for path in inbox.iterdir() if path.is_file()}
            == {"script.ps1", "lifecycle-inputs.zip", "lifecycle-config.json"}, "exact inbox inventory")
        req((inbox / "script.ps1").read_bytes() == args.producer.read_bytes()
            and (inbox / "lifecycle-inputs.zip").read_bytes() == args.bundle.read_bytes()
            and (inbox / "lifecycle-config.json").read_bytes() == args.config.read_bytes(),
            "submitted input bytes")
        workers = json.loads((args.outbox / "workers.json").read_text(encoding="utf-8-sig"))
        archive_id = ident(args.bundle)
        config_id = ident(args.config)
        matrix_id = {"size": len(matrix_bytes), "sha256": hashlib.sha256(matrix_bytes).hexdigest()}
        req(config["matrix"] == matrix_id, "configured lifecycle matrix")
        req(workers["archive"] == archive_id and workers["config"] == config_id
            and workers["matrix"] == matrix_id, "master input identities")
        req(len(workers["workers"]) == len(groups), "worker inventory")
        workers_by_group = {worker["group"]: worker for worker in workers["workers"]}
        req(len(workers_by_group) == len(groups)
            and set(workers_by_group) == {group["id"] for group in groups},
            "exact unique worker groups")

        scratch = tempfile.TemporaryDirectory(prefix="jet3-rel-index-life-")
        scratch_root = Path(scratch.name)
        environment = None
        report_groups = []
        successes = refusals = 0
        native_errors = {}
        for group in groups:
            gid = group["id"]
            receipt_path = args.outbox / f"{gid}-result.json"
            worker = workers_by_group[gid]
            req(worker["group"] == gid and worker["exit_code"] == 0
                and worker["result"] == ident(receipt_path), f"{gid} worker linkage")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
            req(receipt["status"] == "pass" and receipt["error"] is None,
                f"{gid} worker status")
            req(receipt["archive"] == archive_id and receipt["config"] == config_id
                and receipt["matrix"] == matrix_id, f"{gid} input linkage")
            req(receipt["group"] == gid and receipt["origin"] == group["origin"]
                and receipt["case"] == group["case"]["id"]
                and receipt["replica"] == group["replica"], f"{gid} identity")
            req(len(receipt["events"]) == len(group["events"]), f"{gid} event count")
            if environment is None:
                environment = receipt["environment"]
            req(receipt["environment"] == environment == PROVIDER, "provider replication/identity")

            getters.check(gid, 0, "initial", receipt["initial"]["capture"])
            initial_name = group["initial"]
            source_bytes = archive.read(initial_name)
            source_id = {"size": len(source_bytes), "sha256": hashlib.sha256(source_bytes).hexdigest()}
            req(source_id == group["source_identity"] == receipt["initial"]["identity"],
                f"{gid} initial identity")
            req(isinstance(receipt["initial"].get("capture_elapsed_ms"), int)
                and receipt["initial"]["capture_elapsed_ms"] >= 0,
                f"{gid} initial capture timing")
            extracted = scratch_root / initial_name
            extracted.parent.mkdir(parents=True, exist_ok=True)
            extracted.write_bytes(source_bytes)
            initial = discovery.evaluate_case(
                group["case"], pseudo(receipt["initial"]["capture"], group["case"], group["replica"]), extracted, include_row_bytes=True)
            initial_system = system_page_hashes(extracted)
            initial_system_indexes = system_index_inventory.inventory(extracted)
            previous_native = initial
            previous_rust = initial
            previous_native_bytes = source_bytes
            previous_rust_bytes = source_bytes
            previous_native_id = source_id
            previous_rust_id = source_id
            payload_placement_exceptions = set()
            selected_payload_columns = set()
            selected_page_history = set()
            null_padding_exceptions = set()
            events = []

            for spec, event in zip(group["events"], receipt["events"]):
                label = Path(spec["stage"]).stem
                req(event["ordinal"] == spec["ordinal"] and event["label"] == label
                    and event["expected_exit"] == spec["exit"]
                    and event["operation"] == spec["operation"]
                    and event["expected_rows"] == spec["expected_rows"],
                    f"{gid}/{label} request identity")
                req(set(event.get("timing", {})) == {"operation_ms", "native_capture_ms", "rust_capture_ms"}
                    and all(isinstance(value, int) and value >= 0 for value in event["timing"].values()),
                    f"{gid}/{label} complete timing")
                rust_path = args.outbox / event["rust_file"]
                native_path = args.outbox / event["native_file"]
                stage_bytes = archive.read(spec["stage"])
                stage_id = {"size": len(stage_bytes), "sha256": hashlib.sha256(stage_bytes).hexdigest()}
                req(stage_id == spec["image"] == event["rust_pin"] == ident(rust_path),
                    f"{gid}/{label} Rust stage pin")
                req(spec["before"] == previous_rust_id and event["before"] == previous_native_id
                    and event["after"] == ident(native_path), f"{gid}/{label} lineage identity chain")
                getters.check(gid, spec["ordinal"], "rust", event["rust_capture"])
                getters.check(gid, spec["ordinal"], "native", event["native_capture"])
                case = event_case(group, spec)
                operation = spec["operation"]
                assignment = native_assignment(case, spec)
                req(event["native_assignment"] == assignment
                    and event["isolated_refusal"] is (spec["exit"] != 0),
                    f"{gid}/{label} complete native assignment and refusal isolation")
                if spec["exit"] == 0:
                    request = spec["request"]
                    table_name = request["table"]
                    row_id = operation["id"]
                    if request["operation"] == "delete":
                        null_padding_exceptions = {item for item in null_padding_exceptions if item[:2] != (table_name, row_id)}
                    else:
                        fields = next(table["fields"] for table in case["tables"] if table["name"] == table_name)
                        req(request["operation"] in ("insert", "replace") and len(request["values"]) == len(fields),
                            "complete requested row values")
                        for field, requested_value in zip(fields, request["values"]):
                            item = (table_name, row_id, field["name"])
                            if field["type"] == 4 and requested_value is None:
                                null_padding_exceptions.add(item)
                            else:
                                null_padding_exceptions.discard(item)
                if spec["exit"] == 0 and operation["operation"] in ("replace", "update"):
                    table_spec = next(table for table in case["tables"] if table["name"] == operation["table"])
                    fields = table_spec["fields"]
                    for field in fields:
                        if field["type"] in (11, 12):
                            payload_placement_exceptions.add((operation["table"], operation["id"], field["name"]))
                            selected_payload_columns.add((operation["table"], field["name"]))
                if spec["exit"] == 0 and operation["operation"] == "delete":
                    payload_placement_exceptions = {item for item in payload_placement_exceptions
                                                    if item[:2] != (operation["table"], operation["id"])}
                rust = discovery.evaluate_case(
                    case, pseudo(event["rust_capture"], case, group["replica"]), rust_path, include_row_bytes=True)
                native = discovery.evaluate_case(
                    case, pseudo(event["native_capture"], case, group["replica"]), native_path, include_row_bytes=True)

                req(raw_static(rust["raw"]) == raw_static(native["raw"]),
                    f"{gid}/{label} raw schema/alias/system relationship metadata")
                req(physical_keys(rust["raw"]) == physical_keys(native["raw"]),
                    f"{gid}/{label} complete physical keys and locators")
                allocation_comparison = allocation.compare(
                    rust_path, native_path, rust["raw"], native["raw"],
                    selected_payload_columns, selected_page_history)
                req(tables_with_payload_placement_normalized(rust["raw"], payload_placement_exceptions, null_padding_exceptions)
                    == tables_with_payload_placement_normalized(native["raw"], payload_placement_exceptions, null_padding_exceptions),
                    f"{gid}/{label} complete raw tables excluding verified prefixes/requested null padding/selected payload placement")
                req(system_page_hashes(rust_path) == initial_system
                    and system_page_hashes(native_path) == initial_system,
                    f"{gid}/{label} system pages preserved")
                req(system_index_inventory.inventory(rust_path) == initial_system_indexes
                    and system_index_inventory.inventory(native_path) == initial_system_indexes,
                    f"{gid}/{label} all system index keys/locators preserved")
                if "Notes" in initial["raw"]["tables"]:
                    req(rust["raw"]["tables"]["Notes"] == initial["raw"]["tables"]["Notes"]
                        and native["raw"]["tables"]["Notes"] == initial["raw"]["tables"]["Notes"],
                        f"{gid}/{label} unrelated Notes exact")

                if spec["exit"] == 0:
                    successes += 1
                    req(event["native_error"] is None, f"{gid}/{label} unexpected native error")
                    req(prefixes(rust["raw"]) == prefixes(native["raw"]),
                        f"{gid}/{label} successful prefix history")
                    expected_snapshot = snapshot_with_expected_counters(rust, prefixes(rust["raw"]))
                    req(expected_snapshot == snapshot_with_expected_counters(native, prefixes(native["raw"])),
                        f"{gid}/{label} complete successful DAO snapshot")
                else:
                    refusals += 1
                    req(event["native_error"] is not None, f"{gid}/{label} missing native refusal")
                    operation_kind = spec["operation"]["operation"]
                    number = 3201 if operation_kind == "update" else 3200
                    req(event["native_error"]["numbers"] == [number], f"{gid}/{label} exact DAO error {number}")
                    native_errors[str(number)] = native_errors.get(str(number), 0) + 1
                    req(stage_id == spec["before"] and rust_path.read_bytes() == previous_rust_bytes,
                        f"{gid}/{label} Rust refusal byte-exact")
                    expected_prefixes = copy.deepcopy(prefixes(previous_native["raw"]))
                    table_name = assignment["table"]
                    affected = foreign_selectors(case, previous_native["raw"], assignment)
                    for ordinal in affected:
                        first, second = expected_prefixes[table_name][ordinal]
                        if first > 0:
                            expected_prefixes[table_name][ordinal] = [first - 1, min(second, first - 1)]
                    req(prefixes(native["raw"]) == expected_prefixes,
                        f"{gid}/{label} native finite refusal prefix effects")
                    req(prefixes(rust["raw"]) == prefixes(previous_rust["raw"]),
                        f"{gid}/{label} Rust refusal prefix preservation")
                    expected_snapshot = snapshot_with_expected_counters(rust, expected_prefixes)
                    req(expected_snapshot == snapshot_with_expected_counters(native, expected_prefixes),
                        f"{gid}/{label} complete refused DAO snapshot with exact native counter effects")
                    before = previous_native_bytes
                    after = native_path.read_bytes()
                    allowed = {1538}
                    parsed = catalog.analyze_checkpoint(before)
                    named = {table["name"]: table for table in parsed["tables"].values()}
                    for ordinal in affected:
                        definition_index = named[table_name]["definition"]["physical_indexes"][ordinal]
                        offset = definition_index["entry_count_offset"]
                        allowed.update(range(offset - 4, offset + 4))
                    req(len(before) == len(after) and set(diff_offsets(before, after)) <= allowed,
                        f"{gid}/{label} native refusal byte-diff scope")


                continuation = ident(native_path) if spec["exit"] == 0 else previous_native_id
                req(event["continuation_identity"] == continuation,
                    f"{gid}/{label} native success lineage excludes refused attempts")

                events.append({
                    "ordinal": spec["ordinal"],
                    "label": label,
                    "exit": spec["exit"],
                    "native_error": event["native_error"],
                    "rust_identity": ident(rust_path),
                    "native_identity": ident(native_path),
                    "rust_prefixes": prefixes(rust["raw"]),
                    "native_prefixes": prefixes(native["raw"]),
                    "raw_tables_equal": rust["raw"]["tables"] == native["raw"]["tables"],
                    "maps_equal": rust["raw"]["maps"] == native["raw"]["maps"],
                    "allocation_comparison": allocation_comparison,
                    "payload_placement_exceptions": sorted(payload_placement_exceptions),
                    "null_padding_exceptions": sorted(null_padding_exceptions),
                    "native_assignment": assignment,
                    "isolated_refusal": event["isolated_refusal"],
                    "continuation_identity": continuation,
                    "timing": event["timing"],
                })
                previous_rust = rust
                previous_rust_bytes = rust_path.read_bytes()
                previous_rust_id = ident(rust_path)
                if spec["exit"] == 0:
                    previous_native = native
                    previous_native_bytes = native_path.read_bytes()
                    previous_native_id = ident(native_path)

            report_groups.append({
                "id": gid,
                "origin": group["origin"],
                "case": group["case"]["id"],
                "replica": group["replica"],
                "source": source_id,
                "initial_capture_ms": receipt["initial"]["capture_elapsed_ms"],
                "events": events,
            })

    scratch.cleanup()
    req(successes == 60 and refusals == 20, "operation totals")
    report = {
        "document_type": "larger_relationship_graph_lifecycle_acceptance_report",
        "outcome": "accepted",
        "source_revision": pins["source_revision"],
        "source_archive": pins["source_archive"],
        "binary": pins["binary"],
        "cli_binary": pins["cli_binary"],
        "source_pins": ident(pins_path),
        "provider": environment,
        "bundle": ident(args.bundle),
        "config": ident(args.config),
        "producer": ident(args.producer),
        "evaluator": ident(Path(__file__)),
        "actual_property_getters": getters.finish(),
        "coverage": {
            "groups": len(report_groups),
            "stages": successes + refusals,
            "successful_operations": successes,
            "refusals": refusals,
            "captures": 2 * (successes + refusals),
            "native_errors": native_errors,
        },
        "groups": report_groups,
    }
    args.report.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    print(args.report)
    print(f"accepted {successes} successful and {refusals} refused lifecycle pairs")


if __name__ == "__main__":
    main()
