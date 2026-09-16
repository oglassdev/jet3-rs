#!/usr/bin/env python3
import argparse, copy, hashlib, json, zipfile
from pathlib import Path

import relationship_graph_creation as creation
import relationship_graph_checks as checks
import multiple_relationship_structure as raw
import system_catalog as catalog
import relationship_mutation_structure as structure
import allocation_lifecycle_structure as allocation


def req(value, message):
    if not value:
        raise ValueError(message)


def ident(path):
    data = path.read_bytes()
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def materialize(value):
    if not isinstance(value, dict) or "kind" not in value:
        return copy.deepcopy(value)
    if value["kind"] == "repeat":
        return bytes([value["byte"]]) * value["length"]
    if value["kind"] == "pattern":
        return bytes((i * 37 + value["seed"] * 13 + 11) % 256 for i in range(value["length"]))
    raise ValueError("unknown payload recipe")


def materialize_row(case, table_name, values, current_rows):
    table = next(t for t in case["tables"] if t["name"] == table_name)
    row = {f["name"]: materialize(values.get(f["name"])) for f in table["fields"]}
    auto = next((f["name"] for f in table["fields"] if f.get("attributes") == 16), None)
    if auto and row[auto] is None:
        row[auto] = max((r[auto] for r in current_rows), default=0) + 1
    return row


def apply_operation(model, case, operation):
    rows = model[operation["table"]]
    kind = operation["kind"]
    if kind == "insert":
        rows.append(materialize_row(case, operation["table"], operation["values"], rows))
    elif kind == "delete":
        before = len(rows)
        rows[:] = [r for r in rows if r["Id"] != operation["id"]]
        req(len(rows) + 1 == before, "delete model row")
    elif kind == "replace":
        row = next(r for r in rows if r["Id"] == operation["id"])
        row.clear()
        row.update(materialize_row(case, operation["table"], operation["values"], rows))
    elif kind == "field":
        next(r for r in rows if r["Id"] == operation["id"])[operation["column"]] = materialize(operation["value"])
    else:
        raise ValueError("unknown operation kind")
    rows.sort(key=lambda r: r["Id"])


def normalized_schema(snapshot):
    tables = {}
    for table in creation.flatten(snapshot["user_tables"]):
        fields = []
        for field in creation.flatten(table["fields"]):
            fields.append({k: field[k] for k in ("name", "type", "size", "attributes", "required", "allow_zero_length", "ordinal")})
        indexes = []
        for index in creation.flatten(table["indexes"]):
            indexes.append({k: index[k] for k in ("name", "primary", "unique", "required", "foreign", "ignore_nulls", "fields")})
        tables[table["name"]] = {"attributes": table["attributes"], "fields": fields, "indexes": indexes}
    relations = [{k: r[k] for k in ("name", "table", "foreign_table", "attributes", "fields")} for r in creation.flatten(snapshot["relations"])]
    return {"tables": snapshot["tables"], "queries": snapshot["queries"], "user_tables": tables, "relations": relations}


def property_state(data, case):
    result = {}
    for table in case["tables"]:
        item = creation.find_properties(data, table["name"], catalog, structure)
        req(item is not None, "missing properties " + table["name"])
        payload, descriptor, reached = item
        decoded = creation.decode_properties(payload)
        checks.properties(decoded, table["fields"], table["name"])
        by_field = {block["field"]: block["properties"] for block in decoded["blocks"]}
        for field in table["fields"]:
            if field.get("attributes") == 16:
                continue
            req(by_field[field["name"]]["Required"] is False, "Required property " + table["name"] + "/" + field["name"])
            if field["type"] in (10, 12):
                req(by_field[field["name"]]["AllowZeroLength"] == field.get("allow_zero_length", True), "AllowZeroLength property " + table["name"] + "/" + field["name"])
        result[table["name"]] = {
            "payload_hex": payload.hex(),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "descriptor": descriptor,
            "fragments": [{"page": p, "row": r} for p, r in sorted(reached)],
            "decoded": decoded,
        }
    return result


def page_hash(data, page):
    return hashlib.sha256(catalog._page(data, page, "fingerprint")).hexdigest()


def system_fingerprint(data):
    analysis = catalog.analyze_checkpoint(data)
    named = {t["name"]: t for t in analysis["tables"].values()}
    result = {}
    for name in ("MSysObjects", "MSysACEs", "MSysQueries", "MSysRelationships"):
        table = named[name]
        rows = []
        data_pages = set()
        for row in analysis["system_rows"][name]["rows"]:
            image = catalog._page(data, row["page"], name)
            entry = catalog._row_directory(image, row["page"])[row["row"]]
            rows.append({"page": row["page"], "row": row["row"], "raw_hex": image[entry["start"]:entry["end"]].hex(), "values": row["values"]})
            data_pages.add(row["page"])
        indexes = []
        for index in table["definition"]["physical_indexes"]:
            record, members = allocation.map_record(data, index["map"], name + " index")
            indexes.append({
                "ordinal": index["index"],
                "root": index["root"],
                "flags": index["flags"],
                "prefix_hex": index["prefix_hex"],
                "second_word": index["entry_count"],
                "map_record": record,
                "members": sorted(members),
                "member_hashes": {str(p): page_hash(data, p) for p in sorted(members)},
            })
        result[name] = {
            "definition_pages": table["definition"]["pages"],
            "definition_hashes": {str(p): page_hash(data, p) for p in table["definition"]["pages"]},
            "data_pages": sorted(data_pages),
            "data_hashes": {str(p): page_hash(data, p) for p in sorted(data_pages)},
            "rows": rows,
            "indexes": indexes,
        }
    return result


def observe(path, capture, case, model, label):
    observation = raw.observe(path, capture, case, model, label)
    data = path.read_bytes()
    analysis = catalog.analyze_checkpoint(data)
    named = {t["name"]: t for t in analysis["tables"].values()}
    req(set(named) == set(checks.SYSTEM_INDEXES) | set(model) and len(named) == len(analysis["tables"]), label + " exact raw table inventory")
    checks.schema(capture["snapshot"], case, label)
    checks.index_reads(capture["snapshot"], label)
    observation["storage"] = checks.storage(path, analysis, observation)
    observation["properties"] = property_state(data, case)
    observation["allocation_maps"] = creation.verify_maps(data, named, catalog, allocation, label)
    observation["system_fingerprint"] = system_fingerprint(data)
    observation["schema"] = normalized_schema(capture["snapshot"])
    dao_tables = {t["name"]: t for t in creation.flatten(capture["snapshot"]["user_tables"])}
    complete_reads = {}
    for table in case["tables"]:
        name = table["name"]
        expected = raw.expected_rows(model, name)
        by_id = {row["Id"]: row for row in expected}
        reads = {}
        for index_name, read in dao_tables[name]["index_reads"].items():
            traversal = read["traversal"]
            req(len(traversal) == len(expected), label + " traversal length " + name + "/" + index_name)
            req(all(row == by_id[row["Id"]] for row in traversal), label + " complete traversal rows " + name + "/" + index_name)
            queries = sorted({row[read["field"]] for row in expected if row[read["field"]] is not None}) + [999999]
            req([item["query"] for item in read["seek"]] == queries, label + " Seek inventory " + name + "/" + index_name)
            for item in read["seek"]:
                first = next((row for row in traversal if row[read["field"]] == item["query"]), None)
                req(item["row"] == first, label + " complete Seek result " + name + "/" + index_name + "/" + str(item["query"]))
            reads[index_name] = read
        complete_reads[name] = reads
    observation["complete_index_reads"] = complete_reads
    # structure.rows, invoked by raw.observe, proves every active LVAL slot is reached
    # through the owning column map. Record the complete definition/LVAL inventory too.
    observation["definitions"] = {
        name: {
            "pages": table["definition"]["pages"],
            "columns": table["definition"]["columns"],
            "logical_indexes": table["definition"]["logical_indexes"],
            "long_value_maps": table["definition"]["long_value_maps"],
        }
        for name, table in named.items() if name in {t["name"] for t in case["tables"]}
    }
    if case["name"].startswith("boundary_catalog_continuations"):
        child = next(t["name"] for t in case["tables"] if t["name"].startswith("Boundary_Child"))
        req(len(named[child]["definition"]["pages"]) >= 2, label + " boundary definition continuation")
        req(len(named[child]["definition"]["long_value_maps"]) == 8, label + " boundary LVAL inventory")
        req(len(observation["properties"][child]["fragments"]) >= 2, label + " boundary property chain")
        req(len({row["page"] for row in analysis["system_rows"]["MSysObjects"]["rows"]}) >= 2, label + " boundary multi-page catalog")
    return observation


def stable_metadata(observation):
    return {
        "properties": observation["properties"],
        "system_fingerprint": observation["system_fingerprint"],
        "schema": observation["schema"],
        "definitions": observation["definitions"],
        "logical_relationships": observation["logical_relationships"],
        "relationship_rows": observation["relationship_rows"],
        "relationship_objects": observation["relationship_objects"],
        "relationship_aces": observation["relationship_aces"],
        "system_relationship_indexes": observation["system_relationship_indexes"],
    }


def table_physical(observation, table):
    return {
        "rows": observation["rows"][table],
        "indexes": observation["physical_indexes"][table],
        "maps": {k: v for k, v in observation["maps"].items() if k.startswith(table + "/")},
    }


def prefix_snapshot(observation):
    return {
        table: [
            {
                "name": item["name"],
                "column": item["column"],
                "flags": item["flags"],
                "first_word": item["first_word"],
                "second_word": item["second_word"],
                "entries_hex": item["entries_hex"],
            }
            for item in indexes
        ]
        for table, indexes in observation["physical_indexes"].items()
    }


def assert_prefix_transition(before, after, operations, before_model, case, label):
    left, right = prefix_snapshot(before), prefix_snapshot(after)
    req(set(left) == set(right), label + " prefix table inventory")
    expected = copy.deepcopy(left)
    model = copy.deepcopy(before_model)
    for operation in operations:
        table = operation["table"]
        rows = model[table]
        old_row = None if operation["kind"] == "insert" else next(row for row in rows if row["Id"] == operation["id"])
        new_row = None
        if operation["kind"] == "insert":
            new_row = materialize_row(case, table, operation["values"], rows)
        elif operation["kind"] == "replace":
            new_row = materialize_row(case, table, operation["values"], rows)
        elif operation["kind"] == "field":
            new_row = copy.deepcopy(old_row)
            new_row[operation["column"]] = materialize(operation["value"])
        for index in expected[table]:
            column = index["column"]
            foreign = index["flags"] == 0
            if operation["kind"] == "insert":
                if new_row[column] not in {row[column] for row in rows}:
                    index["second_word"] += 1
            elif operation["kind"] == "delete":
                if foreign and index["first_word"] > 0:
                    index["first_word"] -= 1
                    index["second_word"] = min(index["second_word"], index["first_word"])
            elif foreign and (operation["kind"] == "replace" or operation.get("column") == column):
                if index["first_word"] > 0:
                    index["first_word"] -= 1
                    index["second_word"] = min(index["second_word"], index["first_word"])
        apply_operation(model, case, operation)
    for table in left:
        req([(x["name"], x["column"], x["flags"]) for x in left[table]] == [(x["name"], x["column"], x["flags"]) for x in right[table]], label + " prefix index inventory " + table)
        for predicted, actual in zip(expected[table], right[table]):
            req((actual["first_word"], actual["second_word"]) == (predicted["first_word"], predicted["second_word"]), label + " derived prefix words " + table + "/" + actual["name"])
            if not any(op["table"] == table and (op["kind"] in ("insert", "delete", "replace") or op.get("column") == actual["column"]) for op in operations):
                req(actual == predicted, label + " unaffected prefix/key inventory " + table + "/" + actual["name"])
    return right


def byte_diff(before, after):
    left, right = before.read_bytes(), after.read_bytes()
    limit = min(len(left), len(right))
    offsets = [i for i in range(limit) if left[i] != right[i]]
    return {"before": ident(before), "after": ident(after), "changed_bytes": len(offsets) + abs(len(left) - len(right)), "changed_pages": sorted({i // 2048 for i in offsets}), "size_delta": len(right) - len(left)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--outbox", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.matrix.read_text())
    req(not args.report.exists(), "report already exists")
    matrix_identity = ident(args.matrix)
    expected_stems = {f"{case['name']}-r{replica}" for case in plan["graphs"] for replica in range(1, plan["replicas"] + 1)}
    req(len(expected_stems) == len(plan["graphs"]) * plan["replicas"], "unique case inventory")
    with zipfile.ZipFile(args.input) as archive:
        req(archive.testzip() is None, "input ZIP CRC")
        expected_entries = {"matrix.json"} | {"candidates/" + stem + ".mdb" for stem in expected_stems}
        req(set(archive.namelist()) == expected_entries and len(archive.namelist()) == len(expected_entries), "input ZIP inventory")
        req(archive.read("matrix.json") == args.matrix.read_bytes(), "input ZIP matrix bytes")
        submitted_candidates = {stem: archive.read("candidates/" + stem + ".mdb") for stem in expected_stems}
    req({p.name for p in args.inbox.iterdir()} == {"script.ps1", "inputs.zip"}, "inbox inventory")
    req((args.inbox / "script.ps1").read_bytes() == args.producer.read_bytes(), "submitted producer bytes")
    req((args.inbox / "inputs.zip").read_bytes() == args.input.read_bytes(), "submitted input bytes")
    req((args.outbox / "exit.txt").read_text().strip() == "0", "process exit receipt")
    req((args.outbox / "log.txt").read_bytes() == b"", "process log")
    expected_files = {"exit.txt", "log.txt"}
    for case in plan["graphs"]:
        for replica in range(1, plan["replicas"] + 1):
            stem = f"{case['name']}-r{replica}"
            expected_files.add(stem + ".json")
            for role in ("candidate", "control"):
                for stage in case["lifecycle"]["stages"]:
                    expected_files.add(f"{stem}-{role}-{stage['name']}.mdb")
                for refusal in case["lifecycle"]["refusals"]:
                    expected_files.add(f"{stem}-{role}-refusal-{refusal['name']}.mdb")
    observations, refusals, errors = [], [], []
    for case0 in plan["graphs"]:
        case = creation.resolve_auto(case0)
        for replica in range(1, plan["replicas"] + 1):
            stem = f"{case['name']}-r{replica}"
            expected_files.add(stem + ".json")
            try:
                receipt = json.loads((args.outbox / (stem + ".json")).read_text())
                req(receipt["status"] == "pass" and receipt["error"] is None, stem + " worker")
                req(receipt["source_revision"] == plan["source_revision"], stem + " source revision")
                req(receipt["matrix_sha256"] == matrix_identity["sha256"], stem + " matrix hash")
                req(receipt["environment"] == checks.PROVIDER, stem + " provider")
                req(receipt["case"] == case["name"] and receipt["replica"] == replica, stem + " receipt identity")
                specs = case["lifecycle"]
                req(len(receipt["roles"]) == 2 and [r["role"] for r in receipt["roles"]] == ["candidate", "control"], stem + " role inventory")
                role_reports = {}
                for role_record in receipt["roles"]:
                    role = role_record["role"]
                    req(len(role_record["stages"]) == len(specs["stages"]), stem + "/" + role + " stage length")
                    req(len(role_record["refusals"]) == len(specs["refusals"]), stem + "/" + role + " refusal length")
                    req([x["name"] for x in role_record["stages"]] == [x["name"] for x in specs["stages"]], stem + "/" + role + " stage names")
                    req([x["name"] for x in role_record["refusals"]] == [x["name"] for x in specs["refusals"]], stem + "/" + role + " refusal names")
                    model = {t["name"]: copy.deepcopy(t["rows"]) for t in case["tables"]}
                    stage_models, stage_observations = {}, []
                    prefix_history = []
                    original_metadata = None
                    previous = None
                    previous_identity = None
                    for stage_spec, stage_record in zip(specs["stages"], role_record["stages"]):
                        model_before_stage = copy.deepcopy(model)
                        req(len(stage_record["operations"]) == len(stage_spec["operations"]), stem + "/" + role + "/" + stage_spec["name"] + " operation length")
                        req([x["request"] for x in stage_record["operations"]] == stage_spec["operations"], stem + "/" + role + "/" + stage_spec["name"] + " requests")
                        req(all(x["status"] == "success" and x["error"] is None for x in stage_record["operations"]), stem + "/" + role + "/" + stage_spec["name"] + " success")
                        for operation in stage_spec["operations"]:
                            apply_operation(model, case, operation)
                        stage_models[stage_spec["name"]] = copy.deepcopy(model)
                        filename = f"{stem}-{role}-{stage_spec['name']}.mdb"
                        expected_files.add(filename)
                        path = args.outbox / filename
                        current = observe(path, stage_record["capture"], case, model, stem + "/" + role + "/" + stage_spec["name"])
                        if previous_identity is None:
                            req(not stage_record["operations"], stem + "/" + role + " original operation inventory")
                            checks.prefixes(current, model, role, stem)
                            if role == "candidate":
                                req(path.read_bytes() == submitted_candidates[stem], stem + " submitted candidate lineage")
                        elif stage_record["operations"]:
                            req(stage_record["operations"][0]["before"] == previous_identity, stem + "/" + role + "/" + stage_spec["name"] + " operation source")
                            for left, right in zip(stage_record["operations"], stage_record["operations"][1:]):
                                req(left["after"] == right["before"], stem + "/" + role + "/" + stage_spec["name"] + " operation chain")
                            req(stage_record["operations"][-1]["after"] == current["identity"], stem + "/" + role + "/" + stage_spec["name"] + " operation result")
                        else:
                            req(current["identity"] == previous_identity, stem + "/" + role + "/" + stage_spec["name"] + " no-op lineage")
                        if original_metadata is None:
                            original_metadata = stable_metadata(current)
                        else:
                            req(stable_metadata(current) == original_metadata, stem + "/" + role + "/" + stage_spec["name"] + " metadata preservation")
                        if previous is not None:
                            assert_prefix_transition(previous, current, stage_spec["operations"], model_before_stage, case, stem + "/" + role + "/" + stage_spec["name"])
                            touched = {op["table"] for op in stage_spec["operations"]}
                            for table in {t["name"] for t in case["tables"]} - touched:
                                req(table_physical(current, table) == table_physical(previous, table), stem + "/" + role + "/" + stage_spec["name"] + " untouched table " + table)
                        stage_observations.append({"name": stage_spec["name"], "observation": current})
                        prefix_history.append({"name": stage_spec["name"], "indexes": prefix_snapshot(current)})
                        previous = current
                        previous_identity = current["identity"]
                    refusal_observations = []
                    for refusal_spec, refusal_record in zip(specs["refusals"], role_record["refusals"]):
                        req(refusal_record["source_stage"] == refusal_spec["source_stage"], stem + "/" + role + "/" + refusal_spec["name"] + " source")
                        req(refusal_record["expected"] == refusal_spec["number"], stem + "/" + role + "/" + refusal_spec["name"] + " expected number")
                        operation = refusal_record["operation"]
                        req(operation["request"] == refusal_spec["operation"] and operation["status"] == "rejected", stem + "/" + role + "/" + refusal_spec["name"] + " rejected request")
                        req(operation["error"]["numbers"] == [refusal_spec["number"]], stem + "/" + role + "/" + refusal_spec["name"] + " DAO number")
                        model_at_source = stage_models[refusal_spec["source_stage"]]
                        filename = f"{stem}-{role}-refusal-{refusal_spec['name']}.mdb"
                        expected_files.add(filename)
                        path = args.outbox / filename
                        source_path = args.outbox / f"{stem}-{role}-{refusal_spec['source_stage']}.mdb"
                        current = observe(path, refusal_record["capture"], case, model_at_source, stem + "/" + role + "/refusal/" + refusal_spec["name"])
                        req(operation["before"] == ident(source_path) and operation["after"] == current["identity"], stem + "/" + role + "/refusal/" + refusal_spec["name"] + " lineage")
                        req(stable_metadata(current) == original_metadata, stem + "/" + role + "/refusal/" + refusal_spec["name"] + " metadata preservation")
                        refusal_observations.append({"name": refusal_spec["name"], "source_stage": refusal_spec["source_stage"], "operation": operation, "observation": current, "prefixes": prefix_snapshot(current), "source_diff": byte_diff(source_path, path)})
                        refusals.append({"case": case["name"], "replica": replica, "role": role, "name": refusal_spec["name"], "number": refusal_spec["number"]})
                    req(len(prefix_history) == len(specs["stages"]), stem + "/" + role + " prefix history length")
                    role_reports[role] = {"stages": stage_observations, "prefix_history": prefix_history, "refusals": refusal_observations}
                # Every checkpoint has identical complete semantics across construction lineages.
                for index, stage in enumerate(specs["stages"]):
                    left = role_reports["candidate"]["stages"][index]["observation"]
                    right = role_reports["control"]["stages"][index]["observation"]
                    req(left["schema"] == right["schema"], stem + "/" + stage["name"] + " cross-lineage schema")
                    req({t: [r["values"] for r in left["rows"][t]] for t in left["rows"]} == {t: [r["values"] for r in right["rows"][t]] for t in right["rows"]}, stem + "/" + stage["name"] + " cross-lineage rows")
                observations.append({"case": case["name"], "replica": replica, "environment": receipt["environment"], "roles": role_reports})
            except Exception as error:
                errors.append({"case": case["name"], "replica": replica, "error": f"{type(error).__name__}: {error}"})
    actual_files = {p.name for p in args.outbox.iterdir() if p.is_file()}
    if actual_files != expected_files:
        errors.append({"inventory": "outbox", "missing": sorted(expected_files - actual_files), "extra": sorted(actual_files - expected_files)})
    expected_pairs = len(expected_stems)
    expected_refusals = sum(2 * len(c["lifecycle"]["refusals"]) for c in plan["graphs"]) * plan["replicas"]
    report = {
        "document_type": "relationship_graph_creation_lifecycle_acceptance",
        "source_revision": plan["source_revision"],
        "matrix": matrix_identity,
        "input": ident(args.input),
        "producer": ident(args.producer),
        "inbox": str(args.inbox),
        "outbox": str(args.outbox),
        "status": "accepted" if not errors and len(observations) == expected_pairs and len(refusals) == expected_refusals else "rejected",
        "expected_pairs": expected_pairs,
        "observed_pairs": len(observations),
        "expected_refusals": expected_refusals,
        "observed_refusals": len(refusals),
        "errors": errors,
        "observations": observations,
        "refusal_inventory": refusals,
    }
    args.report.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps({k: report[k] for k in ("status", "expected_pairs", "observed_pairs", "expected_refusals", "observed_refusals", "errors")}, indent=2))
    return 0 if report["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
