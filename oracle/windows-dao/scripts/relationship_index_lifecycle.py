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

import relationship_index_checks as discovery
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


def tables_with_payload_placement_normalized(raw, allowed):
    """Normalize only selected full-row Memo/OLE descriptor placement."""
    value = tables_without_prefixes(raw)
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


def relation_selector(case, raw):
    relation = case["relations"][0]
    table = raw["tables"][relation["child"]]
    records = [logical_relation(item["raw_hex"])
               for item in table["logical_indexes"]
               if item["class"] == 2 and item["name"] == relation["name"]]
    req(len(records) == 1, "exact foreign logical relation record")
    return relation["child"], records[0]["physical_index"]


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
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    final_verification_path = args.evidence_root / "final-source-verification.json"
    final_verification = json.loads(final_verification_path.read_text())
    lifecycle_checks = [item for item in final_verification["checks"]
                        if item["reference"].startswith("lifecycle-r3/")]
    req(config["final_source_verification"] == ident(final_verification_path)
        and final_verification["source_revision"] == config["final_source_revision"]
        and final_verification["source_archive_sha256"] == config["source_archive_sha256"]
        and final_verification["binary_sha256"] == config["binary_sha256"]
        and final_verification["candidate_binary_sha256"] == config["candidate_binary_sha256"]
        and len(lifecycle_checks) == 88 and all(item["byte_exact"] for item in lifecycle_checks),
        "final source/lifecycle byte equivalence")

    with zipfile.ZipFile(args.bundle) as archive:
        matrix_bytes = archive.read("matrix.json")
        matrix = json.loads(matrix_bytes)
        groups = matrix["groups"]
        req(len(groups) == 8 and sum(len(group["events"]) for group in groups) == 72,
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
            == {"script.ps1", "inputs.zip", "lifecycle-config.json"}, "exact inbox inventory")
        req((inbox / "script.ps1").read_bytes() == (HERE / "relationship_index_lifecycle.ps1").read_bytes()
            and (inbox / "inputs.zip").read_bytes() == args.bundle.read_bytes()
            and (inbox / "lifecycle-config.json").read_bytes() == args.config.read_bytes(),
            "submitted input bytes")
        workers = json.loads((args.outbox / "workers.json").read_text(encoding="utf-8-sig"))
        archive_id = ident(args.bundle)
        config_id = ident(args.config)
        matrix_id = {"size": len(matrix_bytes), "sha256": hashlib.sha256(matrix_bytes).hexdigest()}
        req(workers["archive"] == archive_id and workers["config"] == config_id
            and workers["matrix"] == matrix_id, "master input identities")
        req(len(workers["workers"]) == len(groups), "worker inventory")

        scratch = tempfile.TemporaryDirectory(prefix="jet3-rel-index-life-")
        scratch_root = Path(scratch.name)
        environment = None
        report_groups = []
        successes = refusals = 0
        native_errors = {}
        for position, group in enumerate(groups):
            gid = group["id"]
            receipt_path = args.outbox / f"{gid}-result.json"
            worker = workers["workers"][position]
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

            initial_name = group["initial"]
            source_bytes = archive.read(initial_name)
            source_id = {"size": len(source_bytes), "sha256": hashlib.sha256(source_bytes).hexdigest()}
            req(source_id == group["source_identity"] == receipt["initial"]["identity"],
                f"{gid} initial identity")
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
            events = []

            for spec, event in zip(group["events"], receipt["events"]):
                label = Path(spec["stage"]).stem
                req(event["ordinal"] == spec["ordinal"] and event["label"] == label
                    and event["expected_exit"] == spec["exit"]
                    and event["operation"] == spec["operation"]
                    and event["expected_rows"] == spec["expected_rows"],
                    f"{gid}/{label} request identity")
                rust_path = args.outbox / event["rust_file"]
                native_path = args.outbox / event["native_file"]
                stage_bytes = archive.read(spec["stage"])
                stage_id = {"size": len(stage_bytes), "sha256": hashlib.sha256(stage_bytes).hexdigest()}
                req(stage_id == spec["image"] == event["rust_pin"] == ident(rust_path),
                    f"{gid}/{label} Rust stage pin")
                req(spec["before"] == previous_rust_id and event["before"] == previous_native_id
                    and event["after"] == ident(native_path), f"{gid}/{label} lineage identity chain")
                case = event_case(group, spec)
                operation = spec["operation"]
                if spec["exit"] == 0 and operation["operation"] == "replace":
                    table_spec = next(table for table in case["tables"] if table["name"] == operation["table"])
                    for field in table_spec["fields"]:
                        if field["type"] in (11, 12):
                            payload_placement_exceptions.add((operation["table"], operation["id"], field["name"]))
                if spec["exit"] == 0 and operation["operation"] == "delete":
                    payload_placement_exceptions = {item for item in payload_placement_exceptions
                                                    if item[:2] != (operation["table"], operation["id"])}
                rust = discovery.evaluate_case(
                    case, pseudo(event["rust_capture"], case, group["replica"]), rust_path, include_row_bytes=True)
                native = discovery.evaluate_case(
                    case, pseudo(event["native_capture"], case, group["replica"]), native_path, include_row_bytes=True)

                req(stable_snapshot(rust["snapshot"]) == stable_snapshot(native["snapshot"]),
                    f"{gid}/{label} complete DAO snapshot")
                req(raw_static(rust["raw"]) == raw_static(native["raw"]),
                    f"{gid}/{label} raw schema/alias/system relationship metadata")
                req(physical_keys(rust["raw"]) == physical_keys(native["raw"]),
                    f"{gid}/{label} complete physical keys and locators")
                req(rust["raw"]["maps"] == native["raw"]["maps"],
                    f"{gid}/{label} complete maps")
                req(rust["raw"]["free_pages"] == native["raw"]["free_pages"],
                    f"{gid}/{label} free pages")
                req(tables_with_payload_placement_normalized(rust["raw"], payload_placement_exceptions)
                    == tables_with_payload_placement_normalized(native["raw"], payload_placement_exceptions),
                    f"{gid}/{label} complete raw tables excluding dynamic prefixes/selected payload placement")
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
                else:
                    refusals += 1
                    req(event["native_error"] is not None, f"{gid}/{label} missing native refusal")
                    operation_kind = spec["operation"]["operation"]
                    number = 3201 if operation_kind == "update" else 3200
                    req(number in event["native_error"]["numbers"], f"{gid}/{label} DAO error {number}")
                    native_errors[str(number)] = native_errors.get(str(number), 0) + 1
                    req(stage_id == spec["before"] and rust_path.read_bytes() == previous_rust_bytes,
                        f"{gid}/{label} Rust refusal byte-exact")
                    if number == 3200:
                        req(event["before"] == event["after"]
                            and native_path.read_bytes() == previous_native_bytes,
                            f"{gid}/{label} referenced-parent native exact")
                        req(prefixes(native["raw"]) == prefixes(previous_native["raw"]),
                            f"{gid}/{label} referenced-parent prefixes")
                    else:
                        table_name, ordinal = relation_selector(case, previous_native["raw"])
                        expected_prefixes = copy.deepcopy(prefixes(previous_native["raw"]))
                        first, second = expected_prefixes[table_name][ordinal]
                        if first > 0:
                            expected_prefixes[table_name][ordinal] = [first - 1, min(second, first - 1)]
                        req(prefixes(native["raw"]) == expected_prefixes,
                            f"{gid}/{label} orphan native finite prefix effect")
                        req(prefixes(rust["raw"]) == prefixes(previous_rust["raw"]),
                            f"{gid}/{label} orphan Rust prefix preservation")
                        before = previous_native_bytes
                        after = native_path.read_bytes()
                        selected = previous_native["raw"]["tables"][table_name]["physical_indexes"][ordinal]
                        offset = None
                        # Re-read the definition to get the on-page dynamic counter offset.
                        parsed = catalog.analyze_checkpoint(before)
                        named = {table["name"]: table for table in parsed["tables"].values()}
                        definition_index = named[table_name]["definition"]["physical_indexes"][ordinal]
                        offset = definition_index["entry_count_offset"]
                        allowed = {1538, *range(offset - 4, offset + 4)}
                        req(set(diff_offsets(before, after)) <= allowed,
                            f"{gid}/{label} orphan byte-diff scope")

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
                    "payload_placement_exceptions": sorted(payload_placement_exceptions),
                })
                previous_rust = rust
                previous_native = native
                previous_rust_bytes = rust_path.read_bytes()
                previous_native_bytes = native_path.read_bytes()
                previous_rust_id = ident(rust_path)
                previous_native_id = ident(native_path)

            report_groups.append({
                "id": gid,
                "origin": group["origin"],
                "case": group["case"]["id"],
                "replica": group["replica"],
                "source": source_id,
                "events": events,
            })

    scratch.cleanup()
    req(successes == 56 and refusals == 16, "operation totals")
    report = {
        "document_type": "relationship_index_lifecycle_acceptance_report",
        "outcome": "accepted",
        "source_revision": config["final_source_revision"],
        "source_archive_sha256": config["source_archive_sha256"],
        "binary_sha256": config["binary_sha256"],
        "candidate_binary_sha256": config["candidate_binary_sha256"],
        "final_source_verification": ident(final_verification_path),
        "provider": environment,
        "bundle": ident(args.bundle),
        "config": ident(args.config),
        "producer": ident(HERE / "relationship_index_lifecycle.ps1"),
        "evaluator": ident(Path(__file__)),
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
