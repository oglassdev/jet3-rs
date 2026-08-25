#!/usr/bin/env python3
"""Small bounded combinatorics helpers for the A4 H4 field layer."""

from __future__ import annotations

import itertools
from typing import Any, Mapping, Sequence

from a4_spec import CHECKPOINT_ORDINALS, LIFECYCLE_RANGES


_OPERATION_LIFECYCLE = {
    "T1_CREATE_ID": "T1-v1",
    "T1_ADD_TEXT": "T1-v1",
    "T1_ADD_INDEX": "T1-v1",
    "T2_CREATE": "T2-v1",
    "T2_RECREATE": "T2-v2",
    "T3_CREATE": "T3-v1",
    "T4_CREATE": "T4-v1",
}


def _simultaneously_extant(left: str, right: str) -> bool:
    left_range = LIFECYCLE_RANGES[_OPERATION_LIFECYCLE[left]]
    right_range = LIFECYCLE_RANGES[_OPERATION_LIFECYCLE[right]]
    left_start = CHECKPOINT_ORDINALS[left]
    right_start = CHECKPOINT_ORDINALS[right]
    return max(left_start, right_start) <= min(
        left_range.last_ordinal, right_range.last_ordinal
    )


def encoded_patterns(name: str) -> tuple[tuple[bytes, tuple[str, ...]], ...]:
    """Return byte-unique CP1252/UTF-8 patterns without preference."""
    by_bytes: dict[bytes, list[str]] = {}
    for encoding_id, encoding in (
        ("strict_windows_1252", "cp1252"),
        ("utf_8", "utf-8"),
    ):
        by_bytes.setdefault(name.encode(encoding, errors="strict"), []).append(encoding_id)
    return tuple(
        (payload, tuple(encodings))
        for payload, encodings in sorted(by_bytes.items(), key=lambda item: item[0])
    )


def expected_operation_name(
    replica: int,
    operation_id: str,
    role_bindings: Mapping[int, Mapping[str, str]],
    table_roles: Mapping[str, str],
) -> str:
    if operation_id == "T1_ADD_TEXT":
        return "Payload"
    if operation_id == "T1_ADD_INDEX":
        return "A4IX_ID"
    return str(role_bindings[replica][table_roles[operation_id]])


def ranges_are_disjoint(ranges: Sequence[tuple[int, int]]) -> bool:
    return all(
        max(left[0], right[0]) >= min(left[1], right[1])
        for left, right in itertools.combinations(ranges, 2)
    )


def kind_mappings(values: frozenset[int]) -> tuple[dict[str, int], ...]:
    """Enumerate the six explicit mappings only for exactly three raw kinds."""
    if len(values) != 3:
        return ()
    return tuple(
        {"table": table, "field": field, "index": index}
        for table, field, index in itertools.permutations(sorted(values))
    )


def encoding_class_matches(
    class_id: str, name: str, payload: bytes, stored_length: int
) -> bool:
    """Match one registered encoding/length equivalence class exactly."""
    cp = name.encode("cp1252", errors="strict")
    utf8 = name.encode("utf-8", errors="strict")
    if class_id == "cp1252_single_byte_per_scalar":
        return payload == cp and stored_length == len(cp) == len(name)
    if class_id == "utf8_encoded_byte_count":
        return payload == utf8 and stored_length == len(utf8)
    if class_id == "utf8_unicode_scalar_or_code_unit_count":
        return payload == utf8 and stored_length == len(name)
    raise ValueError(f"unregistered H4 encoding class {class_id!r}")


def identifier_assignment(
    operations: Sequence[str],
    options: Mapping[str, frozenset[int]],
    lifecycle: str,
    forced: tuple[str, int] | None = None,
) -> dict[str, int] | None:
    """Return one canonical assignment satisfying the lifecycle relation."""
    choices = {operation: set(options[operation]) for operation in operations}
    if forced is not None:
        operation, value = forced
        choices[operation] &= {value}
    equal_t2 = lifecycle == "stable_for_same_physical_name_including_t2_v1_v2"
    if equal_t2:
        common = choices["T2_CREATE"] & choices["T2_RECREATE"]
        choices["T2_CREATE"] = set(common)
        choices["T2_RECREATE"] = set(common)
    order = sorted(operations, key=lambda operation: len(choices[operation]))

    def visit(index: int, assigned: dict[str, int]) -> bool:
        if index == len(order):
            return True
        operation = order[index]
        for value in sorted(choices[operation]):
            if operation in ("T2_CREATE", "T2_RECREATE"):
                other = "T2_RECREATE" if operation == "T2_CREATE" else "T2_CREATE"
                if other in assigned:
                    same = assigned[other] == value
                    if same != equal_t2:
                        continue
            if any(
                existing == value
                and not (
                    equal_t2
                    and {operation, other_operation}
                    == {"T2_CREATE", "T2_RECREATE"}
                )
                and _simultaneously_extant(operation, other_operation)
                for other_operation, existing in assigned.items()
            ):
                continue
            assigned[operation] = value
            if visit(index + 1, assigned):
                return True
            del assigned[operation]
        return False

    if not all(choices.values()):
        return None
    assigned: dict[str, int] = {}
    return assigned if visit(0, assigned) else None


def identifier_assignment_exists(
    operations: Sequence[str],
    options: Mapping[str, frozenset[int]],
    lifecycle: str,
    forced: tuple[str, int] | None = None,
) -> bool:
    """Return whether the seven object groups admit the lifecycle relation."""
    return identifier_assignment(operations, options, lifecycle, forced) is not None


def bitmap_hex(indexes: Sequence[int], maximum: int) -> str:
    """Encode an LSB-first fixed-width occurrence-membership bitmap."""
    raw = bytearray((maximum + 7) // 8)
    for index in indexes:
        if not 0 <= index < maximum:
            raise ValueError("H4 occurrence index is outside its operation bitmap")
        raw[index // 8] |= 1 << (index % 8)
    return bytes(raw).hex()


def bitmap_members(bitmap_hex_value: str) -> frozenset[int]:
    """Decode a fixed-width LSB-first occurrence-membership bitmap."""
    raw = bytes.fromhex(bitmap_hex_value)
    return frozenset(
        index
        for index in range(len(raw) * 8)
        if raw[index // 8] & (1 << (index % 8))
    )


def value_equivalence_key(
    operations: Sequence[str],
    rows: Mapping[str, Sequence[Any]],
    compatible: Mapping[str, Sequence[int]],
    mapping: Mapping[str, int],
    lifecycle: str,
) -> tuple[Any, ...]:
    """Canonical byte-derived values used by tuple-equivalence deduplication."""
    values = tuple(
        (
            operation,
            tuple(
                (row.occurrence.index, row.kind, row.identifier, row.stored_length)
                for row in rows[operation]
                if row.occurrence.index in compatible[operation]
            ),
        )
        for operation in operations
    )
    return values, tuple(sorted(mapping.items())), lifecycle
