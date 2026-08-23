#!/usr/bin/env python3
"""A4-H4 (catalog bootstrap) reference evaluation per derivation replica.

A4 rule | implementation
--- | ---
System TDEFs are EMPTY tag-02 pages traversed with the frozen H1/H2/H3 models without refit | :func:`traverse`
Root selection by operation deltas only (AMB-09) | :func:`root_candidates`
SCHEMA-DELTA-OUTSIDE-OWNED before record restriction (AMB-10) | :func:`isolated_delta_outside`
One operation-delta record per instance on admitted tag-01 pages | :func:`delta_records`
Encoding-neutral union name anchor, bounded kind/id/length tuples, kind mapping, lifecycle relation | :func:`structural_candidates`
Observational dedup (AMB-12) and structural length plausibility (AMB-11) | :func:`structural_candidates`
Three encoding/length equivalence classes after one structural model | :func:`fitting_classes`
"""

from __future__ import annotations

from itertools import product
from typing import Any

from a4_dryrun_core import Context
from a4_dryrun_h1 import ReplicaLayer, target_of
from a4_dryrun_h2 import owned_available
from a4_dryrun_h3 import admitted
from a4_pages import TAG_DATA, TAG_TDEF, decode_directory, decode_map_row, row_count, validate_directory
from a4_spec import (
    CHECKPOINTS, ENDIANNESS, FIELD_DELTA_RANGE, ID_WIDTHS, INSTANCE_BY_ID, KIND_WIDTHS, LAYER_PREDICATES, LENGTH_CLASSES,
    LIFECYCLE_RELATIONS, OPERATION_INSTANCES, ORDINAL, ROOT_SIGNATURES, canonical_id, expected_name, name_bytes,
)

H4 = LAYER_PREDICATES["h4_catalog_bootstrap"]
OP_CHECKPOINTS = tuple(cp for cp, _, _ in OPERATION_INSTANCES)
OP_KIND = {cp: kind for cp, _, kind in OPERATION_INSTANCES}
OP_INSTANCE = {cp: inst for cp, inst, _ in OPERATION_INSTANCES}
CLASS_ENCODING = {c["id"]: ("strict_windows_1252" if c["id"].startswith("cp1252") else "utf_8") for c in LENGTH_CLASSES}
Models = dict[str, Any]


def traverse(ctx: Context, replica: int, page: int, models: Models) -> dict[str, set[int]] | str:
    """Admitted owned/in-use page set per checkpoint for a system TDEF under the frozen models."""
    h1, h2, h3 = models["h1"]["model"], models["h2"]["model"], models["h3"]["model"]
    out: dict[str, set[int]] = {}
    for cp in CHECKPOINTS:
        blob = ctx.page(replica, cp, page)
        if blob is None or blob[0] != TAG_TDEF:
            return f"page {page} is not tag 02 at {cp}"
        targets = [target_of(blob, o, h1["layout"]) for o in h1["locator_offsets"]]
        rows = []
        for target_page, row in targets:
            target = ctx.page(replica, cp, target_page)
            if target is None or target[0] != TAG_DATA or row >= row_count(target):
                return f"page {page} locator target ({target_page},{row}) invalid at {cp}"
            slots = decode_directory(target, h2["row_masks"][0])
            if validate_directory(slots):
                return f"page {page} target directory invalid at {cp}"
            slot = slots[row]
            rows.append((slot, target[slot.start: slot.end]))
        decoded = decode_map_row(owned_available(rows, h2["locator_role_assignment"])[0])
        if isinstance(decoded, str):
            return f"page {page}: {decoded} at {cp}"
        out[cp] = admitted(ctx, replica, cp, decoded, h2["polarity"], h3["base_formula"])
    return out


def stream_changes(ctx: Context, replica: int, admitted_by_cp: dict[str, set[int]], cp: str) -> bool:
    previous = ctx.predecessor(cp) or cp
    if admitted_by_cp[previous] != admitted_by_cp[cp]:
        return True
    return bool((admitted_by_cp[cp] | admitted_by_cp[previous]) & ctx.changed_pages(replica, cp))


def root_candidates(ctx: Context, replica: int, models: Models) -> tuple[list[tuple[int, dict[str, set[int]]]], list[str]]:
    found, reasons = [], []
    empty_count = ctx.page_count(replica, CHECKPOINTS[0])
    for page in range(empty_count):
        if ctx.tag(replica, CHECKPOINTS[0], page) != TAG_TDEF:
            continue
        ctx.charges.add("catalog_root_signatures", len(OP_CHECKPOINTS))
        traversal = traverse(ctx, replica, page, models)
        if isinstance(traversal, str):
            reasons.append(traversal)
            continue
        if any(stream_changes(ctx, replica, traversal, cp) for cp in OP_CHECKPOINTS):
            found.append((page, traversal))
        else:
            reasons.append(f"page {page}: admitted stream follows no listed operation")
    return found, reasons


def isolated_delta_outside(ctx: Context, replica: int, admitted_by_cp: dict[str, set[int]], h1_bindings: dict[str, Any]) -> str | None:
    for cp in OP_CHECKPOINTS:
        explained: set[int] = set()
        for inst_id, binding in h1_bindings.items():
            inst = INSTANCE_BY_ID[inst_id]
            if cp in inst.checkpoints:
                explained.add(binding["tdef_page"])
                explained.update(p for p, _ in binding["targets"])
        outside = (ctx.changed_pages(replica, cp) - explained) - admitted_by_cp[cp]
        if outside:
            return f"{cp}: schema delta on non-admitted page(s) {sorted(outside)[:4]}"
    return None


def _complete_rows(ctx: Context, replica: int, cp: str, page: int, mask: int) -> dict[int, bytes] | None:
    blob = ctx.page(replica, cp, page)
    if blob is None or blob[0] != TAG_DATA:
        return None
    slots = decode_directory(blob, mask)
    if validate_directory(slots):
        return None
    return {s.ordinal: blob[s.start: s.end] for s in slots if not (s.deleted or s.overflow)}


def delta_records(ctx: Context, replica: int, admitted_by_cp: dict[str, set[int]], mask: int) -> dict[str, list[tuple[int, int, bytes]]]:
    """Per operation: complete rows on admitted tag-01 pages that are new or changed at that transition."""
    out: dict[str, list[tuple[int, int, bytes]]] = {}
    for cp in OP_CHECKPOINTS:
        previous = ctx.predecessor(cp) or cp
        found = []
        for page in sorted(admitted_by_cp[cp]):
            rows = _complete_rows(ctx, replica, cp, page, mask)
            if rows is None:
                continue
            before = _complete_rows(ctx, replica, previous, page, mask) or {}
            for ordinal, row in rows.items():
                ctx.charges.add("catalog_raw_rows")
                if before.get(ordinal) != row:
                    found.append((page, ordinal, row))
        out[cp] = found
    return out


def _decode(row: bytes, start: int, width: int, endianness: str) -> int:
    return int.from_bytes(row[start: start + width], "little" if endianness == "little" else "big")


def _occurrences(ctx: Context, replica: int, cp: str, row: bytes) -> list[tuple[int, str, bytes]]:
    patterns: dict[bytes, str] = {}
    for encoding_id, pattern in name_bytes(expected_name(replica, cp)).items():
        patterns.setdefault(pattern, encoding_id)
    ctx.charges.add("encoding_union_anchor_bytes", len(row) * len(patterns))
    return [(o, encoding_id, pattern) for pattern, encoding_id in patterns.items()
            for o in range(len(row) - len(pattern) + 1) if row[o: o + len(pattern)] == pattern]


def plausible_lengths(name: str) -> set[int]:
    """Counts some registered encoding/length class could report for this name (encoding-neutral union)."""
    return {len(b) for b in name_bytes(name).values()} | {len(name)}


def _row_at(ctx: Context, replica: int, cp: str, page: int, ordinal: int, mask: int) -> bytes | None:
    rows = _complete_rows(ctx, replica, cp, page, mask)
    return None if rows is None else rows.get(ordinal)


def _tuples():
    """13,824 relative field-address tuples: one shared endianness for kind, identifier and stored name length (plan P4-B2)."""
    for dk, wk, wid, e, dl, wl in product(FIELD_DELTA_RANGE, KIND_WIDTHS, ID_WIDTHS, ENDIANNESS, FIELD_DELTA_RANGE, KIND_WIDTHS):
        yield {"kind_start_delta": dk, "kind_width": wk, "identifier_width": wid, "endianness": e,
               "name_length_start_delta": dl, "name_length_width": wl}


def _fields(t: dict[str, int | str], o: int, name_len: int, row_len: int) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None:
    kind = (o - int(t["kind_start_delta"]), o - int(t["kind_start_delta"]) + int(t["kind_width"]))
    ident = (kind[0] - int(t["identifier_width"]), kind[0])
    length = (o - int(t["name_length_start_delta"]), o - int(t["name_length_start_delta"]) + int(t["name_length_width"]))
    spans = [kind, ident, length, (o, o + name_len)]
    if any(a < 0 or b > row_len for a, b in spans):
        return None
    ordered = sorted(spans)
    if any(ordered[i][1] > ordered[i + 1][0] for i in range(3)):
        return None
    return kind, ident, length


def structural_candidates(ctx: Context, replica: int, records: dict[str, tuple[int, int, bytes]], mask: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Canonical structural field models after kind-mapping and identifier-lifecycle tests."""
    occurrences = {cp: _occurrences(ctx, replica, cp, records[cp][2]) for cp in OP_CHECKPOINTS}
    if any(not v for v in occurrences.values()):
        return [], [f"{cp}: no registered expected-name occurrence" for cp, v in occurrences.items() if not v]
    later: dict[str, list[str]] = {cp: [c for c in INSTANCE_BY_ID[OP_INSTANCE[cp]].checkpoints if ORDINAL[c] > ORDINAL[cp]] for cp in OP_CHECKPOINTS}
    survivors: dict[str, dict[str, Any]] = {}
    reasons: dict[str, int] = {}

    def note(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    for t in _tuples():
        per_op: dict[str, list[dict[str, Any]]] = {}
        dead = False
        for cp in OP_CHECKPOINTS:
            page, ordinal, row = records[cp]
            compatible = []
            for o, encoding_id, pattern in occurrences[cp]:
                ctx.charges.add("h4_name_length_structural_tuples")
                fields = _fields(t, o, len(pattern), len(row))
                if fields is None:
                    continue
                kind_span, id_span, len_span = fields
                length = _decode(row, len_span[0], len_span[1] - len_span[0], str(t["endianness"]))
                if length not in plausible_lengths(expected_name(replica, cp)):
                    continue  # AMB-11 structural length plausibility
                compatible.append({"name_start": o, "encoding_id": encoding_id, "bytes": pattern, "length": length,
                                   "kind": _decode(row, kind_span[0], kind_span[1] - kind_span[0], str(t["endianness"])),
                                   "identifier": _decode(row, id_span[0], id_span[1] - id_span[0], str(t["endianness"])),
                                   "id_span": id_span})
            if not compatible or len({(c["kind"], c["identifier"]) for c in compatible}) != 1:
                dead = True
                note("no compatible occurrence or disagreeing kind/id within one record")
                break
            per_op[cp] = compatible
        if dead:
            continue
        kinds = {cp: per_op[cp][0]["kind"] for cp in OP_CHECKPOINTS}
        table_values = {kinds[cp] for cp in OP_CHECKPOINTS if OP_KIND[cp] == "table"}
        field_value = next(kinds[cp] for cp in OP_CHECKPOINTS if OP_KIND[cp] == "field")
        index_value = next(kinds[cp] for cp in OP_CHECKPOINTS if OP_KIND[cp] == "index")
        if len(table_values) != 1 or len({next(iter(table_values)), field_value, index_value}) != 3:
            note("kind mapping")
            continue
        mapping = {"table": next(iter(table_values)), "field": field_value, "index": index_value}
        ids = {cp: per_op[cp][0]["identifier"] for cp in OP_CHECKPOINTS}
        stable = True
        for cp in OP_CHECKPOINTS:
            page, ordinal, _ = records[cp]
            span = per_op[cp][0]["id_span"]
            for c in later[cp]:
                row = _row_at(ctx, replica, c, page, ordinal, mask)
                if row is None or len(row) < span[1] or _decode(row, span[0], span[1] - span[0], str(t["endianness"])) != ids[cp]:
                    stable = False
                    break
            if not stable:
                break
        if not stable:
            note("identifier not stable across later checkpoints")
            continue
        distinct = True
        for c in CHECKPOINTS:
            extant = [ids[cp] for cp in OP_CHECKPOINTS if c in INSTANCE_BY_ID[OP_INSTANCE[cp]].checkpoints and ORDINAL[c] >= ORDINAL[cp]]
            if len(extant) != len(set(extant)):
                distinct = False
                break
        if not distinct:
            note("simultaneously extant identifiers are not distinct")
            continue
        relation = LIFECYCLE_RELATIONS[1] if ids["T2_CREATE"] == ids["T2_RECREATE"] else LIFECYCLE_RELATIONS[0]
        vector = canonical_id({cp: [(c["kind"], c["identifier"], c["length"], c["name_start"]) for c in per_op[cp]] for cp in OP_CHECKPOINTS})
        entry = survivors.setdefault(vector, {"members": [], "kind_mapping": mapping, "identifier_lifecycle_relation": relation,
                                              "operation_bindings": {cp: {"page": records[cp][0], "slot": records[cp][1],
                                                                          "compatible_name_occurrences": per_op[cp]} for cp in OP_CHECKPOINTS}})
        entry["members"].append(t)
        ctx.charges.add("candidate_serializations")
    return list(survivors.values()), [f"{k} x{v}" for k, v in reasons.items()]


def fitting_classes(candidate: dict[str, Any], replica: int) -> list[str]:
    fits = []
    for cls in LENGTH_CLASSES:
        encoding = CLASS_ENCODING[cls["id"]]
        ok = True
        for cp in OP_CHECKPOINTS:
            name = expected_name(replica, cp)
            expected = name_bytes(name)[encoding]
            lengths = {len(expected) if m == "encoded_byte_count" else len(name) for m in cls["members"]}
            occurrences = candidate["operation_bindings"][cp]["compatible_name_occurrences"]
            if not any(c["bytes"] == expected and c["length"] in lengths for c in occurrences):
                ok = False
                break
        if ok:
            fits.append(cls["id"])
    return fits


def canonical_field_model(candidate: dict[str, Any], encoding_class: str | None) -> dict[str, Any]:
    model = {"field_tuple_members": sorted(candidate["members"], key=lambda m: tuple(m.values())),
             "kind_mapping": candidate["kind_mapping"], "identifier_lifecycle_relation": candidate["identifier_lifecycle_relation"]}
    if encoding_class is not None:
        model["encoding_length_equivalence_class"] = encoding_class
    return model


def evaluate_replica(ctx: Context, replica: int, models: Models, h1_bindings: dict[str, Any]) -> ReplicaLayer:
    out = ReplicaLayer()
    roots, reasons = root_candidates(ctx, replica, models)
    out.stages["root_candidates"] = [p for p, _ in roots]
    if not roots:
        return out.fail(H4[0], 0, "; ".join(reasons[:2]))
    out.ok(H4[0], len(roots))
    if len(roots) > 1:
        return out.fail(H4[1], len(roots), "multiple system streams follow the operation deltas")
    out.ok(H4[1], 1)
    root_page, admitted_by_cp = roots[0]
    problem = isolated_delta_outside(ctx, replica, admitted_by_cp, h1_bindings)
    if problem:
        return out.fail(H4[2], 1, problem)
    out.ok(H4[2], 1)
    mask = models["h2"]["model"]["row_masks"][0]
    records = delta_records(ctx, replica, admitted_by_cp, mask)
    counts = {cp: len(v) for cp, v in records.items()}
    out.stages["record_counts"] = counts
    empty = [cp for cp, n in counts.items() if n == 0]
    if empty:
        return out.fail(H4[3], 0, f"no operation-delta record for {empty}")
    out.ok(H4[3], 1)
    many = [cp for cp, n in counts.items() if n > 1]
    if many:
        return out.fail(H4[4], max(counts[cp] for cp in many), f"multiple operation-delta records for {many}")
    out.ok(H4[4], 1)
    candidates, reasons = structural_candidates(ctx, replica, {cp: v[0] for cp, v in records.items()}, mask)
    out.stages["structural_field_models"] = [canonical_field_model(c, None) for c in candidates]
    if not candidates:
        return out.fail(H4[5], 0, "; ".join(reasons[:3]))
    out.ok(H4[5], len(candidates))
    if len(candidates) > 1:
        return out.fail(H4[6], len(candidates), "multiple structural field layouts")
    out.ok(H4[6], 1)
    classes = fitting_classes(candidates[0], replica)
    out.stages["fitting_encoding_classes"] = classes
    if len(classes) != 1:
        return out.fail(H4[7], len(classes), f"fitting encoding/length classes: {classes}")
    out.ok(H4[7], 1)
    root_model = {"root_tdef_page": root_page, "catalog_root_selection_signature": ROOT_SIGNATURES[0]}
    field_model = canonical_field_model(candidates[0], classes[0])
    model = {"root": root_model, "fields": field_model}
    out.model = {"model_type": "h4_catalog_model", "model": model, "canonical_model_id": canonical_id({"model_type": "h4_catalog_model", "model": model})}
    out.bindings = {"admitted": {cp: sorted(s) for cp, s in admitted_by_cp.items()}, "records": {cp: list(v[0][:2]) for cp, v in records.items()}}
    return out


def holdout_root(ctx: Context, replica: int, models: Models, h1_bindings: dict[str, Any]) -> tuple[bool, str, dict[str, set[int]] | None]:
    page = models["h4"]["model"]["root"]["root_tdef_page"]
    traversal = traverse(ctx, replica, page, models)
    if isinstance(traversal, str):
        return False, traversal, None
    if not any(stream_changes(ctx, replica, traversal, cp) for cp in OP_CHECKPOINTS):
        return False, "frozen root follows no listed operation on replica 3", traversal
    problem = isolated_delta_outside(ctx, replica, traversal, h1_bindings)
    return problem is None, problem or "", traversal


def holdout_fields(ctx: Context, replica: int, models: Models, admitted_by_cp: dict[str, set[int]]) -> tuple[bool, str]:
    frozen = models["h4"]["model"]["fields"]
    mask = models["h2"]["model"]["row_masks"][0]
    records = delta_records(ctx, replica, admitted_by_cp, mask)
    bad = [cp for cp, v in records.items() if len(v) != 1]
    if bad:
        return False, f"replica 3 record cardinality is not one for {bad}"
    candidates, reasons = structural_candidates(ctx, replica, {cp: v[0] for cp, v in records.items()}, mask)
    member = frozen["field_tuple_members"][0]
    match = [c for c in candidates if member in c["members"]]
    if not match:
        return False, "frozen structural field tuple does not fit replica 3: " + "; ".join(reasons[:2])
    candidate = match[0]
    if candidate["kind_mapping"] != frozen["kind_mapping"]:
        return False, f"kind mapping differs: {candidate['kind_mapping']} != {frozen['kind_mapping']}"
    if candidate["identifier_lifecycle_relation"] != frozen["identifier_lifecycle_relation"]:
        return False, "identifier lifecycle relation differs"
    if frozen["encoding_length_equivalence_class"] not in fitting_classes(candidate, replica):
        return False, "exact name bytes or stored length differ from the frozen equivalence class"
    return True, ""
