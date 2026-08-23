#!/usr/bin/env python3
"""Campaign predicates (orders 1-4) of the A4 plan-driven reference evaluator.

A4 rule | implementation
--- | ---
A4-IDLE-EQUALITY: idle pairs equal in bytes, page-index sequence, snapshot tables | :func:`idle_equality`
A4-SCHEMA-SNAPSHOT: 75 snapshots bound, scheduled schema, constants, row hashes, code page, names, hashes | :func:`schema_snapshot`
A4-SNAPSHOT-RECONSTRUCTION: page count, file size, ordered hashes, database SHA-256 | :func:`snapshot_reconstruction`
A4-RESOURCE-BOUND: independently recomputed named resources <= bounds, equality passes | :func:`resource_bound`
H1 qualified tag-02 pages from schema-lifecycle deltas | :func:`qualified_tag02_pages`
"""

from __future__ import annotations

from a4_dryrun_core import Context, FixtureRejected
from a4_pages import TAG_TDEF
from a4_spec import (
    BOUNDS, CHECKPOINTS, DERIVATION_REPLICAS, EVENT_BY_CHECKPOINT, EXPECTED_SCHEMA, EXPERIMENT_ID, FIELD_DEFS, IDLE_PAIRS,
    INDEX_DEF, MAX_QUALIFIED_PAGES, MAX_ROWS, ORDINAL, PAGE_SIZE, PLAN_SHA256, REVISION_PLAN_SHA256, ROLE_BINDINGS,
    ROW_BATCH, SCHEMA_LIFECYCLE, rolling_sha256, sha256_hex,
)

P_IDLE, P_SNAPSHOT, P_RECON, P_RESOURCE = (
    "A4-IDLE-EQUALITY", "A4-SCHEMA-SNAPSHOT", "A4-SNAPSHOT-RECONSTRUCTION", "A4-RESOURCE-BOUND",
)


def reject_malformed(ctx: Context) -> None:
    """Malformed blobs are rejected before any predicate; they are not a scientific outcome."""
    bad = ctx.campaign.malformed_blobs()
    if bad:
        raise FixtureRejected(f"malformed page blob(s): {bad[:3]}")
    for replica in ctx.replicas():
        data = ctx.campaign.replicas[replica]
        if tuple(data.pages) != CHECKPOINTS:
            raise FixtureRejected(f"replica {replica} does not expose the 25 scheduled checkpoints")


def idle_equality(ctx: Context) -> bool:
    failures = []
    for replica in ctx.replicas():
        data = ctx.campaign.replicas[replica]
        for left, right in IDLE_PAIRS:
            if data.pages[left] != data.pages[right]:
                failures.append(f"replica {replica} {left}/{right} page-index sequence differs")
            elif ctx.campaign.database_sha256(data.pages[left]) != ctx.campaign.database_sha256(data.pages[right]):
                failures.append(f"replica {replica} {left}/{right} reconstructed bytes differ")
            if data.snapshots.get(left, {}).get("tables") != data.snapshots.get(right, {}).get("tables"):
                failures.append(f"replica {replica} {left}/{right} canonical snapshot tables differ")
    return ctx.record(P_IDLE, not failures, 0, "; ".join(failures[:3]))


def _named_ok(entry: dict, name: str) -> bool:
    units = [int.from_bytes(name.encode("utf-16-le")[i: i + 2], "little") for i in range(0, 2 * len(name), 2)]
    return (entry.get("name") == name and entry.get("name_utf16_code_units") == units
            and entry.get("name_windows_1252_hex") == name.encode("cp1252").hex()
            and entry.get("name_utf8_hex") == name.encode("utf-8").hex())


def _field_ok(entry: dict, definition: dict) -> bool:
    return (_named_ok(entry, definition["name"]) and entry.get("type") == definition["dao_type_numeric"]
            and entry.get("size") == definition["size"] and entry.get("attributes") == definition["attributes_numeric"]
            and entry.get("required") == definition["required"] and entry.get("allow_zero_length") == definition["allow_zero_length"])


def _table_problem(table: dict, token: str, replica: int, previous_counts: dict[str, int], checkpoint: str) -> str | None:
    parts = token.split(":")
    role, shape = parts[0], parts[-1]
    version = parts[1] if parts[1].startswith("v") else "v1"
    name = ROLE_BINDINGS[replica][role]
    if table.get("logical_role") != role or table.get("lifecycle_instance") != f"{role}-{version}" or not _named_ok(table, name):
        return f"{role} binding or name fields wrong"
    if table.get("attributes") != 0:
        return f"{role} attributes"
    fields = table.get("fields", [])
    expected_fields = [FIELD_DEFS[0]] + ([FIELD_DEFS[1]] if "payload" in shape else [])
    if len(fields) != len(expected_fields) or any(
            f.get("ordinal") != i or not _field_ok(f, d) for i, (f, d) in enumerate(zip(fields, expected_fields))):
        return f"{role} fields differ from construction constants"
    indexes = table.get("indexes", [])
    if "index" in shape:
        if len(indexes) != 1:
            return f"{role} index missing"
        index = indexes[0]
        if not (_named_ok(index, INDEX_DEF["name"]) and index.get("primary") is False and index.get("unique") is False
                and index.get("required") is False and index.get("ignore_nulls") is False and len(index.get("fields", [])) == 1
                and _named_ok(index["fields"][0], INDEX_DEF["fields"][0]) and index["fields"][0].get("descending") is False):
            return f"{role} index constants"
    elif indexes:
        return f"{role} unexpected index"
    count = table.get("row_count")
    if not isinstance(count, int) or count < 0 or count > MAX_ROWS or table.get("rolling_row_sha256") != rolling_sha256(role, count):
        return f"{role} row count/hash"
    event = EVENT_BY_CHECKPOINT[checkpoint]
    before = previous_counts.get(role)
    if event.kind == "grow" and event.role == role:
        if before is None or count < before or (count - before) % ROW_BATCH:
            return f"{role} growth is not whole fixed batches"
    elif event.kind == "delete_all" and event.role == role:
        if count != 0:
            return f"{role} rows remain after delete"
    elif event.kind == "reinsert" and event.role == role:
        if count == 0:
            return f"{role} reinsert produced no rows"
    elif before is not None and count != before:
        return f"{role} row count changed without a listed mutation"
    elif before is None and count != 0:
        return f"{role} created with rows"
    return None


def schema_snapshot(ctx: Context) -> bool:
    problems = []
    for replica in ctx.replicas():
        data = ctx.campaign.replicas[replica]
        previous_counts: dict[str, int] = {}
        for checkpoint in CHECKPOINTS:
            snapshot = data.snapshots.get(checkpoint)
            index = data.page_indexes.get(checkpoint)
            if snapshot is None or index is None:
                problems.append(f"replica {replica} {checkpoint} snapshot missing")
                continue
            binding_ok = (snapshot.get("experiment_id") == EXPERIMENT_ID and snapshot.get("plan_sha256") == PLAN_SHA256
                          and snapshot.get("revision_plan_sha256") == REVISION_PLAN_SHA256 and snapshot.get("replica") == replica
                          and snapshot.get("checkpoint_id") == checkpoint and snapshot.get("ordinal") == ORDINAL[checkpoint])
            if not binding_ok:
                problems.append(f"replica {replica} {checkpoint} wrongly bound")
            if snapshot.get("windows_ansi_code_page") != 1252:
                problems.append(f"replica {replica} {checkpoint} code page")
            if not (snapshot.get("database_sha256_before_read") == snapshot.get("database_sha256_after_read") == index["database_sha256"]):
                problems.append(f"replica {replica} {checkpoint} before/after hash")
            tables = snapshot.get("tables", [])
            expected = EXPECTED_SCHEMA[checkpoint]
            if len(tables) != len(expected):
                problems.append(f"replica {replica} {checkpoint} table count {len(tables)} != {len(expected)}")
                continue
            by_role = {t.get("logical_role"): t for t in tables}
            ordinals = [t.get("ordinal") for t in tables]
            names = [bytes.fromhex(t.get("name_windows_1252_hex", "")) for t in tables]
            if sorted(ordinals) != list(range(len(tables))) or names != sorted(names) or ordinals != sorted(ordinals):
                problems.append(f"replica {replica} {checkpoint} ordinals are not unique and name-sorted")
            for token in expected:
                table = by_role.get(token.split(":")[0])
                problem = None if table is None else _table_problem(table, token, replica, previous_counts, checkpoint)
                if table is None or problem:
                    problems.append(f"replica {replica} {checkpoint} {problem or token + ' missing'}")
            previous_counts = {t.get("logical_role"): t.get("row_count", 0) for t in tables}
            if EVENT_BY_CHECKPOINT[checkpoint].kind == "reinsert":
                role = EVENT_BY_CHECKPOINT[checkpoint].role
                grown = data.snapshots.get("T1_REL_1280", {}).get("tables", [])
                expected_count = next((t.get("row_count") for t in grown if t.get("logical_role") == role), None)
                if by_role.get(role, {}).get("row_count") != expected_count:
                    problems.append(f"replica {replica} {checkpoint} reinsert count differs from T1_REL_1280")
    return ctx.record(P_SNAPSHOT, not problems, 0, "; ".join(problems[:3]))


def snapshot_reconstruction(ctx: Context) -> bool:
    problems = []
    for replica in ctx.replicas():
        data = ctx.campaign.replicas[replica]
        for checkpoint in CHECKPOINTS:
            index = data.page_indexes[checkpoint]
            hashes = index["ordered_page_sha256"]
            if index["page_count"] != len(hashes) or index["file_size_bytes"] != len(hashes) * PAGE_SIZE:
                problems.append(f"replica {replica} {checkpoint} page count/file size")
            blobs = [ctx.campaign.blobs.get(h) for h in hashes]
            if any(b is None for b in blobs):
                problems.append(f"replica {replica} {checkpoint} missing page blob")
                continue
            if any(sha256_hex(b) != h for b, h in zip(blobs, hashes)):
                problems.append(f"replica {replica} {checkpoint} ordered page hash differs from blob")
            if ctx.campaign.database_sha256(list(hashes)) != index["database_sha256"]:
                problems.append(f"replica {replica} {checkpoint} database SHA-256 differs")
            if hashes != data.pages[checkpoint]:
                problems.append(f"replica {replica} {checkpoint} page index differs from observation sequence")
            predecessor = ctx.predecessor(checkpoint)
            previous = data.pages[predecessor] if predecessor else []
            changed = [i for i, h in enumerate(hashes) if i >= len(previous) or previous[i] != h]
            if changed != index["changed_page_indices"]:
                problems.append(f"replica {replica} {checkpoint} changed_page_indices differ")
    return ctx.record(P_RECON, not problems, 0, "; ".join(problems[:3]))


def qualified_tag02_pages(ctx: Context, replica: int) -> set[int]:
    """Pages that change (or appear) across a schema-lifecycle transition and are tag 02 afterwards."""
    out: set[int] = set()
    for before, after in zip(SCHEMA_LIFECYCLE, SCHEMA_LIFECYCLE[1:]):
        for number in ctx.changed_pages(replica, after):
            if ctx.tag(replica, after, number) == TAG_TDEF:
                out.add(number)
    return out


def resource_bound(ctx: Context) -> bool:
    """Static named resources recomputed from the campaign; equality passes, one over fails."""
    measured: dict[str, tuple[int, int]] = {}
    union_qualified: set[int] = set()
    for replica in DERIVATION_REPLICAS:
        if replica in ctx.campaign.replicas:
            union_qualified |= qualified_tag02_pages(ctx, replica)
    measured["qualified_tdef_pages_union"] = (len(union_qualified), MAX_QUALIFIED_PAGES)
    measured["unique_page_blobs"] = (len(ctx.campaign.blobs), int(BOUNDS["max_unique_page_blobs"]))
    for replica in ctx.replicas():
        data = ctx.campaign.replicas[replica]
        measured[f"replica_{replica}_checkpoints"] = (len(data.pages), int(BOUNDS["max_checkpoints_per_replica"]))
        measured[f"replica_{replica}_final_pages"] = (max(len(p) for p in data.pages.values()), int(BOUNDS["max_final_pages_per_replica"]))
        changed = sum(len(i["changed_page_indices"]) for i in data.page_indexes.values())
        measured[f"replica_{replica}_changed_hash_entries"] = (changed, int(BOUNDS["max_changed_hash_entries_per_replica"]))
        inserted, previous = 0, {}
        for checkpoint in CHECKPOINTS:
            counts = {t["logical_role"]: t["row_count"] for t in data.snapshots[checkpoint]["tables"]}
            inserted += sum(max(0, c - previous.get(r, 0)) for r, c in counts.items())
            previous = counts
        measured[f"replica_{replica}_inserted_rows"] = (inserted, MAX_ROWS)
    ctx.charges.add("qualified_tdef_pages", len(union_qualified))
    breaches = [f"{k}={v} > {bound}" for k, (v, bound) in measured.items() if v > bound]
    ctx.models["resources"] = {k: {"measured": v, "bound": b} for k, (v, b) in measured.items()}
    return ctx.record(P_RESOURCE, not breaches, 0, "; ".join(breaches[:3]))


def evaluate_campaign(ctx: Context) -> bool:
    reject_malformed(ctx)
    return idle_equality(ctx) and schema_snapshot(ctx) and snapshot_reconstruction(ctx) and resource_bound(ctx)
