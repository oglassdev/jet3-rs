#!/usr/bin/env python3
"""Bounded physical-delta calculations for the M3 DAO campaign."""

from __future__ import annotations

import hashlib
from typing import Any

from protocol_validation import ValidationError

PAGE_SIZE = 2048


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _set_bit(mask: bytearray, index: int) -> None:
    mask[index // 8] |= 1 << (index % 8)


def _clear_padding(mask: bytearray, bit_length: int) -> None:
    remainder = bit_length % 8
    if remainder and mask:
        mask[-1] &= (1 << remainder) - 1


def _mask_pages(mask: bytes, bit_length: int) -> list[int]:
    pages = []
    for page in range((bit_length + PAGE_SIZE - 1) // PAGE_SIZE):
        start = page * PAGE_SIZE
        end = min(bit_length, start + PAGE_SIZE)
        if any(_bit(mask, index) for index in range(start, end)):
            pages.append(page)
    return pages


def _bit(mask: bytes, index: int) -> int:
    return (mask[index // 8] >> (index % 8)) & 1


def _mask_record(path: str, mask: bytes, bit_length: int) -> dict[str, Any]:
    count = sum(byte.bit_count() for byte in mask)
    first = next((index for index in range(bit_length) if _bit(mask, index)), None)
    last = next(
        (index for index in range(bit_length - 1, -1, -1) if _bit(mask, index)),
        None,
    )
    return {
        "bit_count": count,
        "bit_length": bit_length,
        "first_offset": first,
        "last_offset": last,
        "path": path,
        "sha256": _sha256(mask),
        "size_bytes": len(mask),
    }


def _pair_mask(left: bytes, right: bytes, length: int) -> bytes:
    result = bytearray((length + 7) // 8)
    for index in range(length):
        if left[index] != right[index]:
            _set_bit(result, index)
    return bytes(result)


def _cohort_mask(values: list[bytes], length: int) -> bytes:
    result = bytearray((length + 7) // 8)
    first = values[0]
    for index in range(length):
        if any(value[index] != first[index] for value in values[1:]):
            _set_bit(result, index)
    return bytes(result)


def _stable_delta_mask(left: list[bytes], right: list[bytes], length: int) -> bytes:
    result = bytearray((length + 7) // 8)
    for index in range(length):
        if (
            all(value[index] == left[0][index] for value in left[1:])
            and all(value[index] == right[0][index] for value in right[1:])
            and left[0][index] != right[0][index]
        ):
            _set_bit(result, index)
    return bytes(result)


def _aggregate(
    name: str,
    ids: list[str],
    comparison_masks: dict[str, tuple[bytes, int]],
    masks: dict[str, bytes],
) -> dict[str, Any]:
    length = min(comparison_masks[item][1] for item in ids)
    size = (length + 7) // 8
    selected = [comparison_masks[item][0][:size] for item in ids]
    union = bytearray(size)
    intersection = bytearray(b"\xff" * size)
    for selected_mask in selected:
        for index, value in enumerate(selected_mask):
            union[index] |= value
            intersection[index] &= value
    _clear_padding(union, length)
    _clear_padding(intersection, length)
    histogram = {str(count): 0 for count in range(1, len(ids) + 1)}
    for position in range(length):
        count = sum(_bit(mask, position) for mask in selected)
        if count:
            histogram[str(count)] += 1
    result: dict[str, Any] = {
        "comparison_ids": ids,
        "eligible_common_length": length,
        "occurrence_histogram": histogram,
    }
    for label, value in (("intersection", bytes(intersection)), ("union", bytes(union))):
        path = f"analysis/masks/{name}-{label}.bin"
        masks[path] = value
        result[f"{label}_mask"] = _mask_record(path, value, length)
    return result


def _stable_extra_pages(left: list[bytes], right: list[bytes]) -> dict[str, list[int]]:
    left_max = max(map(len, left)) // PAGE_SIZE
    left_min = min(map(len, left)) // PAGE_SIZE
    right_max = max(map(len, right)) // PAGE_SIZE
    right_min = min(map(len, right)) // PAGE_SIZE
    return {
        "baseline_only": list(range(right_max, left_min))
        if left_min > right_max
        else [],
        "indexed_only": list(range(left_max, right_min))
        if right_min > left_max
        else [],
    }


def build_physical_analysis(
    values: dict[str, bytes],
    plan: dict[str, Any],
    maximum_working_bytes: int,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    retained = sum(map(len, values.values()))
    if retained > maximum_working_bytes:
        raise ValidationError("M3 analysis working-set ceiling is too small")
    masks: dict[str, bytes] = {}
    samples = []
    for sample in plan["samples"]:
        sample_id = sample["sample_id"]
        value = values[sample_id]
        samples.append(
            {
                "database_sha256": _sha256(value),
                "page_count": len(value) // PAGE_SIZE,
                "page_sha256": [
                    _sha256(value[offset : offset + PAGE_SIZE])
                    for offset in range(0, len(value), PAGE_SIZE)
                ],
                "sample_id": sample_id,
                "size_bytes": len(value),
            }
        )
    cohorts = []
    for condition_id in ("B", "E", "I"):
        members = [
            item["sample_id"]
            for item in plan["samples"]
            if item["condition_id"] == condition_id
        ]
        cohort_values = [values[item] for item in members]
        minimum = min(map(len, cohort_values))
        maximum = max(map(len, cohort_values))
        mask = _cohort_mask(cohort_values, minimum)
        path = f"analysis/masks/cohort-{condition_id}-variable.bin"
        masks[path] = mask
        cohorts.append(
            {
                "condition_id": condition_id,
                "maximum_page_count": maximum // PAGE_SIZE,
                "maximum_size_bytes": maximum,
                "members": members,
                "minimum_page_count": minimum // PAGE_SIZE,
                "minimum_size_bytes": minimum,
                "replica_length_variance_bytes": maximum - minimum,
                "variable_byte_mask": _mask_record(path, mask, minimum),
                "variable_page_indices": _mask_pages(mask, minimum),
            }
        )
    comparisons = []
    comparison_masks: dict[str, tuple[bytes, int]] = {}
    for item in plan["comparisons"]:
        left = values[item["left_sample_id"]]
        right = values[item["right_sample_id"]]
        common = min(len(left), len(right))
        mask = _pair_mask(left, right, common)
        comparison_masks[item["comparison_id"]] = (mask, common)
        path = f"analysis/masks/{item['comparison_id']}.bin"
        masks[path] = mask
        comparisons.append(
            {
                **item,
                "common_length": common,
                "differing_byte_mask": _mask_record(path, mask, common),
                "differing_page_indices": _mask_pages(mask, common),
                "left_only_bytes": max(0, len(left) - common),
                "right_only_bytes": max(0, len(right) - common),
            }
        )
    baseline = [values[f"M3-SAMPLE-B-0{replica}"] for replica in range(1, 4)]
    indexed = [values[f"M3-SAMPLE-I-0{replica}"] for replica in range(1, 4)]
    treatment_length = min(*(map(len, baseline)), *(map(len, indexed)))
    stable_mask = _stable_delta_mask(baseline, indexed, treatment_length)
    stable_path = "analysis/masks/cohort-stable-delta.bin"
    masks[stable_path] = stable_mask
    paired_ids = [
        item["comparison_id"] for item in plan["comparisons"] if item["paired"]
    ]
    cross_ids = [
        item["comparison_id"]
        for item in plan["comparisons"]
        if item["kind"] == "baseline_index"
    ]
    if retained + sum(map(len, masks.values())) > maximum_working_bytes:
        raise ValidationError("M3 analysis exceeded its working-set ceiling")
    summary = {
        "claim_boundary": (
            "Descriptive absolute physical positions only; no page class, field, "
            "row, index node, allocation structure, stable format offset, Rust "
            "compatibility, or G3 scenario credit is asserted."
        ),
        "cohorts": cohorts,
        "comparisons": comparisons,
        "document_type": "dao_m3_analysis",
        "page_size": PAGE_SIZE,
        "protocol_version": "1.0.0",
        "samples": samples,
        "treatment": {
            "cohort_stable_delta_mask": _mask_record(
                stable_path, stable_mask, treatment_length
            ),
            "cross_comparisons": _aggregate(
                "cross", cross_ids, comparison_masks, masks
            ),
            "paired_comparisons": _aggregate(
                "paired", paired_ids, comparison_masks, masks
            ),
            "stable_extra_pages": _stable_extra_pages(baseline, indexed),
        },
    }
    if retained + sum(map(len, masks.values())) > maximum_working_bytes:
        raise ValidationError("M3 aggregate masks exceeded the working-set ceiling")
    return summary, masks
