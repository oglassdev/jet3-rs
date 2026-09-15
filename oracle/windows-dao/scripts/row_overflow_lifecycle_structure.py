"""EXP-0262/0263 physical checks for the finite overflow lifecycle."""
import hashlib
from pathlib import Path
from allocation_lifecycle_structure import map_record
from index_tree_mutation import notes_identity, tables
import wide_row_lifecycle_structure as wide

PAGE = 2048
STAGES = (
    "original", "grown", "equal", "keyed", "shrunken", "filled",
    "relocated", "shared", "deleted", "released", "reinserted",
)


def identity(path):
    data = path.read_bytes()
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def locator(entry):
    return (entry["page"], entry["row"])


def page_bytes(data, number):
    result = data[number * PAGE:(number + 1) * PAGE]
    require(len(result) == PAGE, f"page {number} outside image")
    return result


def directory(data, number):
    result = wide.catalog._row_directory(page_bytes(data, number), number)
    for entry in result:
        entry["page"] = number
        entry["flags"] = (0x8000 if entry["hidden"] else 0) | (0x4000 if entry["overflow"] else 0)
    return result


def memberships(data, table):
    _, owned = map_record(data, table["maps"]["owned"], "review-owned")
    _, available = map_record(data, table["maps"]["available"], "review-available")
    _, globally_free = map_record(data, {"page": 1, "row": 0}, "global")
    return set(owned), set(available), set(globally_free)


def lval_identity(data, table):
    pages = set()
    records = {}
    for group in table["long_value_maps"]:
        ordinal = group["column"]
        for role in ("owned", "available"):
            location = group[role]
            record, members = map_record(data, location, f"review-lval-{ordinal}-{role}")
            records[f"{ordinal}-{role}"] = record
            pages.add(location["page"])
            pages.update(p for p in record["references"] if p)
            if role == "owned":
                pages.update(members)
    return {
        "records": records,
        "page_hashes": {
            str(page): hashlib.sha256(page_bytes(data, page)).hexdigest()
            for page in sorted(pages)
        },
    }


def state(directory_path, role, payload, case, stage):
    suffix = f"-{role}" if role else ""
    path = directory_path / f"{case}-{stage}{suffix}.mdb"
    data = path.read_bytes()
    definition = tables(data)["Items"]
    owned_pages, _ = wide.catalog._table_pages(data, definition)
    owned, available, globally_free = memberships(data, definition)
    require(set(owned_pages) == owned, f"{case}/{stage}: owned page enumeration")
    require(available <= owned, f"{case}/{stage}: available subset")

    entries = {}
    live_hidden = set()
    for number in owned_pages:
        for entry in directory(data, number):
            key = locator(entry)
            require(key not in entries, f"{case}/{stage}: duplicate physical locator")
            entries[key] = entry
            if entry["hidden"] and entry["start"] < entry["end"]:
                live_hidden.add(key)

    rows = {}
    reached = set()
    for number in owned_pages:
        for root in directory(data, number):
            if root["hidden"] or root["start"] == root["end"]:
                continue
            logical = locator(root)
            current = root
            current_page = number
            chain = []
            seen = set()
            while True:
                here = (current_page, current["row"])
                require(here not in seen, f"{case}/{stage}: overflow cycle")
                seen.add(here)
                raw = page_bytes(data, current_page)[current["start"]:current["end"]]
                chain.append({"locator": list(here), "flags": current["flags"], "raw": raw.hex()})
                if not current["overflow"]:
                    break
                require(len(raw) == 4, f"{case}/{stage}: pointer width")
                target = (int.from_bytes(raw[1:4], "little"), raw[0])
                target_entries = directory(data, target[0])
                require(target[1] < len(target_entries), f"{case}/{stage}: target slot")
                current = target_entries[target[1]]
                current_page = target[0]
                require(current["hidden"] and current["start"] < current["end"],
                        f"{case}/{stage}: live hidden target")
                require(target not in reached, f"{case}/{stage}: shared hidden target")
                reached.add(target)
            require(len(chain) <= 2, f"{case}/{stage}: multi-hop chain")
            raw_values, shape = wide.layout(raw, definition["columns"])
            row_id = wide.catalog._decode_value(definition["columns"][0], raw_values[0], True)
            require(row_id not in rows, f"{case}/{stage}: duplicate Id")
            headers = {
                str(column["ordinal"]): raw_values[index].hex()
                for index, column in enumerate(definition["columns"])
                if column["type"] in ("Memo", "LongBinary") and raw_values[index] is not None
            }
            rows[row_id] = {
                "logical": list(logical),
                "storage": chain[-1]["locator"],
                "chain": chain,
                "headers": headers,
                "row_length": shape["length"],
            }

    require(reached == live_hidden, f"{case}/{stage}: hidden reachability inventory")
    decoded = wide.table_rows(data, definition, owned_pages)
    require(len(decoded) == len(rows) == definition["row_count"],
            f"{case}/{stage}: complete decoded row inventory")
    decoded_ids = {row["values"][0] for row in decoded}
    require(decoded_ids == set(rows), f"{case}/{stage}: decoded Id inventory")
    if len(definition["columns"]) == 5:
        for decoded_row in decoded:
            row_id = decoded_row["values"][0]
            seed = 31 if row_id == 931 else 9000 if row_id == 9001 else row_id
            require(decoded_row["values"][3] == payload(80, seed, 3),
                    f"{case}/{stage}: complete Memo payload for {row_id}")
            require(decoded_row["values"][4] == payload(80, seed, 4),
                    f"{case}/{stage}: complete OLE payload for {row_id}")
    return {
        "path": path,
        "data": data,
        "table": definition,
        "rows": rows,
        "entries": entries,
        "owned": owned,
        "available": available,
        "global_free": globally_free,
        "notes": notes_identity(data),
        "lval": lval_identity(data, definition),
    }


def tombstone(data, where, label):
    entry = directory(data, where[0])[where[1]]
    require(entry["hidden"] and entry["overflow"] and entry["start"] == entry["end"], label)
    return {"locator": list(where), "word_flags": entry["flags"], "start": entry["start"]}


def released_page(data, page, root, label):
    image = page_bytes(data, page)
    entries = directory(data, page)
    require(image[0] == 9 and image[1] == 1, f"{label}: released tag")
    require(int.from_bytes(image[4:8], "little") == root, f"{label}: retained owner")
    require(all(e["hidden"] and e["overflow"] and e["start"] == e["end"] == PAGE for e in entries),
            f"{label}: released tombstones")
    free = int.from_bytes(image[2:4], "little")
    require(free == 2038 - 2 * len(entries), f"{label}: released free count")
    return {"page": page, "slots": len(entries), "free": free}


def review_case(directory_path, role, case, pin, payload):
    states = {stage: state(directory_path, role, payload, case, stage) for stage in STAGES}
    selected = [0, 27, 55, 71] if case == "ordinary" else [0, 15, 31, 71]
    old_key = selected[2]
    new_key = old_key + 900
    checks = []
    witnesses = {}

    source = directory_path / f"{case}-source.mdb"
    require(identity(source) == pin, f"{case}: source pin")
    require(states["original"]["data"] == source.read_bytes(), f"{case}: original is source")

    baseline_notes = states["original"]["notes"]
    require(all(item["notes"] == baseline_notes for item in states.values()),
            f"{case}: Notes bytes changed")
    checks.append("exact Notes definition/map/data/LVAL page hashes across all checkpoints")

    stable = 0
    for before_name, after_name in zip(STAGES, STAGES[1:]):
        before = states[before_name]["rows"]
        after = states[after_name]["rows"]
        for row_id in set(before) & set(after):
            require(before[row_id]["logical"] == after[row_id]["logical"],
                    f"{case}/{after_name}: logical locator changed for {row_id}")
            stable += 1
        if after_name == "keyed":
            require(before[old_key]["logical"] == after[new_key]["logical"],
                    f"{case}/keyed: renamed logical locator")
            stable += 1
    checks.append(f"{stable} adjacent surviving-row logical locator comparisons")

    overflow_counts = {
        stage: sum(len(row["chain"]) == 2 for row in item["rows"].values())
        for stage, item in states.items()
    }
    witnesses["overflow_counts"] = overflow_counts
    checks.append("all live hidden slots reached exactly once; every chain has at most one link")

    original = states["original"]["rows"]
    grown = states["grown"]["rows"]
    if case != "payloads-native":
        for row_id in selected[:3]:
            require(len(original[row_id]["chain"]) == 1 and len(grown[row_id]["chain"]) == 2,
                    f"{case}/grown: missing growth overflow for {row_id}")
            require(original[row_id]["logical"] == grown[row_id]["logical"],
                    f"{case}/grown: growth logical locator")
        require(len(grown[selected[3]]["chain"]) == 1,
                f"{case}/grown: tail control overflowed")
        witnesses["growth"] = {
            str(row_id): {"logical": grown[row_id]["logical"], "storage": grown[row_id]["storage"]}
            for row_id in selected[:3]
        }
        witnesses["growth_tail_control"] = {
            "id": selected[3], "locator": grown[selected[3]]["logical"]
        }
    else:
        require(all(len(original[row_id]["chain"]) == 2 for row_id in selected[:3]),
                f"{case}/original: retained native overflow inventory")
        require(all(original[row_id]["storage"] == grown[row_id]["storage"]
                    for row_id in selected[:3]),
                f"{case}/grown: Rust edit moved retained native overflow target")
        witnesses["native_overflow_source"] = {
            str(row_id): {"logical": original[row_id]["logical"],
                          "storage": original[row_id]["storage"],
                          "rust_equal_width_storage": grown[row_id]["storage"]}
            for row_id in selected[:3]
        }

    equal = states["equal"]["rows"]
    for row_id in selected[:2]:
        require(grown[row_id]["storage"] == equal[row_id]["storage"] and
                len(equal[row_id]["chain"]) == 2,
                f"{case}/equal: target changed for {row_id}")
    witnesses["equal_target_retention"] = {
        str(row_id): equal[row_id]["storage"] for row_id in selected[:2]
    }

    keyed_before = states["equal"]
    keyed = states["keyed"]
    require(old_key not in keyed["rows"] and new_key in keyed["rows"], f"{case}/keyed: key inventory")
    require(keyed_before["rows"][old_key]["logical"] == keyed["rows"][new_key]["logical"] and
            keyed_before["rows"][old_key]["storage"] == keyed["rows"][new_key]["storage"],
            f"{case}/keyed: storage changed")
    require(keyed_before["rows"][old_key]["headers"] == keyed["rows"][new_key]["headers"],
            f"{case}/keyed: selected payload header changed")
    if case != "ordinary":
        require(keyed_before["lval"] == keyed["lval"], f"{case}/keyed: LVAL bytes changed")
    witnesses["fixed_key"] = {
        "from": old_key, "to": new_key,
        "logical": keyed["rows"][new_key]["logical"],
        "storage": keyed["rows"][new_key]["storage"],
        "payload_headers": keyed["rows"][new_key]["headers"],
    }

    shrunken = states["shrunken"]
    collapsed = {}
    for row_id in selected[:2]:
        old_target = tuple(keyed["rows"][row_id]["storage"])
        require(len(shrunken["rows"][row_id]["chain"]) == 1 and
                shrunken["rows"][row_id]["storage"] == shrunken["rows"][row_id]["logical"],
                f"{case}/shrunken: row {row_id} did not collapse")
        collapsed[str(row_id)] = tombstone(shrunken["data"], old_target,
                                            f"{case}/shrunken: old target {row_id}")
    witnesses["collapse"] = collapsed

    filled = states["filled"]
    inserted_ids = set(range(1000, 1013)) & set(filled["rows"])
    link_pages = {
        row["logical"][0] for row in filled["rows"].values() if len(row["chain"]) == 2
    }
    link_page_inserts = {
        row_id: filled["rows"][row_id]["logical"]
        for row_id in inserted_ids
        if filled["rows"][row_id]["logical"][0] in link_pages
    }
    witnesses["inserts_on_link_bearing_pages"] = {
        "witnessed": bool(link_page_inserts),
        "rows": {str(row_id): where for row_id, where in sorted(link_page_inserts.items())},
    }
    relocated = states["relocated"]
    old_storage = tuple(filled["rows"][new_key]["storage"])
    new_storage = tuple(relocated["rows"][new_key]["storage"])
    require(len(filled["rows"][new_key]["chain"]) == 2 and
            len(relocated["rows"][new_key]["chain"]) == 2,
            f"{case}/relocated: fixed-key row is not a one-link overflow")
    if old_storage != new_storage:
        relocation_tomb = tombstone(relocated["data"], old_storage,
                                    f"{case}/relocated: old target")
        witnesses["direct_relocation"] = {
            "witnessed": True,
            "id": new_key,
            "logical": relocated["rows"][new_key]["logical"],
            "old_storage": list(old_storage),
            "new_storage": list(new_storage),
            "old_target": relocation_tomb,
        }
    else:
        witnesses["direct_relocation"] = {
            "witnessed": False,
            "id": new_key,
            "logical": relocated["rows"][new_key]["logical"],
            "retained_storage": list(old_storage),
        }
    tail = relocated["rows"][selected[3]]
    witnesses["tail_row_after_relocation_stage"] = {
        "id": selected[3],
        "logical": tail["logical"],
        "storage": tail["storage"],
        "in_place": len(tail["chain"]) == 1 and tail["logical"] == tail["storage"],
    }

    shared = states["shared"]
    hidden_by_page = {}
    ordinary_by_page = {}
    for row_id, row in shared["rows"].items():
        if len(row["chain"]) == 2:
            hidden_by_page.setdefault(row["storage"][0], []).append(row_id)
        elif row["logical"] == row["storage"]:
            ordinary_by_page.setdefault(row["storage"][0], []).append(row_id)
    shared_pages = sorted(set(hidden_by_page) & set(ordinary_by_page))
    require(shared_pages, f"{case}/shared: no hidden/ordinary shared page")
    witnesses["shared_pages"] = [
        {"page": page, "hidden_ids": sorted(hidden_by_page[page]),
         "ordinary_ids": sorted(ordinary_by_page[page])}
        for page in shared_pages
    ]

    deleted = states["deleted"]
    deletion = {}
    for row_id in (selected[0], selected[1], new_key):
        before = shared["rows"][row_id]
        require(len(before["chain"]) == 2 and row_id not in deleted["rows"],
                f"{case}/deleted: overflow row {row_id} inventory")
        deletion[str(row_id)] = {
            "logical": tombstone(deleted["data"], tuple(before["logical"]),
                                 f"{case}/deleted: logical tombstone {row_id}"),
            "storage": tombstone(deleted["data"], tuple(before["storage"]),
                                 f"{case}/deleted: storage tombstone {row_id}"),
        }
    witnesses["overflow_deletion"] = deletion

    released = states["released"]
    released_pages = sorted(deleted["owned"] - released["owned"])
    require(released_pages, f"{case}/released: no data page released")
    release_records = []
    for page in released_pages:
        require(page in released["global_free"], f"{case}/released: page {page} not global-free")
        release_records.append(released_page(released["data"], page,
                                             released["table"]["root"], f"{case}/released/{page}"))
    reinserted = states["reinserted"]
    reused_pages = sorted(set(released_pages) & reinserted["owned"])
    require(reused_pages, f"{case}/reinserted: no released page reused")
    require(len(released["data"]) == len(reinserted["data"]), f"{case}/reinserted: EOF grew")
    reuse_records = []
    for page in reused_pages:
        require(page not in reinserted["global_free"], f"{case}/reinserted: reused page remains free")
        image = page_bytes(reinserted["data"], page)
        entries = directory(reinserted["data"], page)
        require(image[0] == 1 and entries and entries[0]["row"] == 0 and
                not entries[0]["hidden"] and not entries[0]["overflow"] and
                entries[0]["start"] < entries[0]["end"],
                f"{case}/reinserted: page {page} not reset to fresh ordinary slots")
        reuse_records.append({"page": page, "slots": len(entries), "first_slot": entries[0]["row"]})
    witnesses["release_reuse"] = {
        "released": release_records,
        "reused": reuse_records,
        "file_pages": len(released["data"]) // PAGE,
    }

    if case != "ordinary":
        full_replacements = {
            "grown": set(selected),
            "equal": set(selected[:2]),
            "shrunken": set(selected[:2]),
            "relocated": {selected[0], selected[1], new_key, selected[3]},
        }
        header_checks = 0
        selected_header_changes = {}
        for before_name, after_name in zip(STAGES, STAGES[1:]):
            before = states[before_name]["rows"]
            after = states[after_name]["rows"]
            touched = full_replacements.get(after_name, set())
            for row_id in set(before) & set(after) - touched:
                require(before[row_id]["headers"] == after[row_id]["headers"],
                        f"{case}/{after_name}: unrelated payload header changed for {row_id}")
                header_checks += 1
            if after_name in full_replacements:
                selected_header_changes[after_name] = {
                    str(row_id): before[row_id]["headers"] != after[row_id]["headers"]
                    for row_id in full_replacements[after_name]
                    if row_id in before and row_id in after
                }
        checks.append(f"{header_checks} unrelated surviving-row Memo/OLE header comparisons")
        checks.append("complete 80-byte Memo/OLE values and column-owned reachability for every row")
        witnesses["selected_full_row_header_changes"] = selected_header_changes

    native_path = directory_path / f"{case}-native{('-' + role) if role else ''}.mdb"
    if native_path.exists():
        native = state(directory_path, role, payload, case, "native")
        require(native["notes"] == baseline_notes, f"{case}/native: Notes bytes changed")
        require(9000 not in native["rows"] and 9001 in native["rows"] and 3000 not in native["rows"],
                f"{case}/native: insert/key/delete inventory")
        native_stable = 0
        native_headers = 0
        for row_id in set(reinserted["rows"]) & set(native["rows"]):
            require(reinserted["rows"][row_id]["logical"] == native["rows"][row_id]["logical"],
                    f"{case}/native: logical locator changed for {row_id}")
            native_stable += 1
            if case != "ordinary":
                require(reinserted["rows"][row_id]["headers"] == native["rows"][row_id]["headers"],
                        f"{case}/native: unrelated payload header changed for {row_id}")
                native_headers += 1
        witnesses["native_successor"] = {
            "logical_locator_checks": native_stable,
            "unrelated_payload_header_checks": native_headers,
            "inserted_then_renamed": {
                "id": 9001,
                "logical": native["rows"][9001]["logical"],
                "storage": native["rows"][9001]["storage"],
                "chain_length": len(native["rows"][9001]["chain"]),
            },
            "deleted_id": 3000,
            "image": identity(native_path),
        }

    return {
        "source": identity(source),
        "checkpoint_images": {stage: identity(states[stage]["path"]) for stage in STAGES},
        "checks": checks,
        "witnesses": witnesses,
    }


def review(directory_path, role, revision, pins, payload):
    report = dict(document_type="jet3_overflow_physical_review", status="failed",
                  source_revision=revision, verifier=identity(Path(__file__)),
                  input_directory=str(directory_path), role=role or "rust-checkpoint",
                  cases={}, error=None)
    try:
        require(set(pins) == {"ordinary", "payloads", "payloads-native"}, "source pin inventory")
        require(role in ("", "candidate", "control"), "known capture role")
        for case, pin in pins.items():
            if role:
                require((directory_path / f"{case}-native-{role}.mdb").is_file(),
                        f"{case}/{role}: native successor absent")
            report["cases"][case] = review_case(directory_path, role, case, pin, payload)
        require(any(case["witnesses"]["direct_relocation"]["witnessed"]
                    for case in report["cases"].values()),
                "suite has no direct relocation witness")
        report["status"] = "accepted"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    return report
