#!/usr/bin/env python3
"""Read-only A3 page-23 calibration replay for the A4 dry run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from protocol_validation import ValidationError


EXPECTED_MANIFEST_SHA256 = (
    "f1a644abae1585d8ed0531f45a0544d3264d2449f6d5973ef2ef0bb3d5fefaab"
)
PAGE_SIZE = 2048
MAX_PAGE_NUMBER = 20479


def _read_bound(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"A4 calibration path is not a regular file: {path}")
    size = path.stat().st_size
    if size < 1 or size > maximum:
        raise ValidationError(f"A4 calibration file exceeds its bound: {path}")
    return path.read_bytes()


def _json(path: Path) -> dict[str, Any]:
    raw = _read_bound(path, 64 * 1024 * 1024)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"A4 calibration JSON is malformed: {path}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"A4 calibration JSON is not an object: {path}")
    return value


def _page(root: Path, digest: str) -> bytes:
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValidationError("A4 calibration page digest is not canonical SHA-256")
    payload = _read_bound(root / "page-store" / f"{digest}.page", PAGE_SIZE)
    if len(payload) != PAGE_SIZE or hashlib.sha256(payload).hexdigest() != digest:
        raise ValidationError("A4 calibration page bytes do not match their digest")
    return payload


def _decode(page: bytes, offset: int, layout: str) -> tuple[int, int]:
    raw = page[offset : offset + 4]
    if layout == "page_then_row":
        return int.from_bytes(raw[:3], "little"), raw[3]
    return int.from_bytes(raw[1:], "little"), raw[0]


def replay(retained_root: Path) -> dict[str, Any]:
    """Recompute the frozen A3 calibration values without opening replica 3."""
    root = retained_root.absolute()
    manifest_raw = _read_bound(root / "bundle-manifest.json", 64 * 1024 * 1024)
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    if manifest_sha256 != EXPECTED_MANIFEST_SHA256:
        raise ValidationError("A4 calibration bundle manifest identity mismatch")
    manifest = _json(root / "bundle-manifest.json")
    entries = {entry["path"]: entry for entry in manifest.get("files", ())}
    indexes: list[dict[str, Any]] = []
    pages: list[bytes] = []
    prefix = "page-indexes/replica-01/"
    paths = sorted(path for path in entries if path.startswith(prefix) and path.endswith(".json"))
    if len(paths) != 25:
        raise ValidationError("A4 calibration requires exactly 25 replica-1 page indexes")
    if any(path.startswith("page-indexes/replica-03/") for path in paths):
        raise ValidationError("A4 calibration attempted to open the holdout")
    for relative in paths:
        raw = _read_bound(root / relative, 64 * 1024 * 1024)
        entry = entries[relative]
        if len(raw) != entry["size_bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValidationError("A4 calibration page index differs from the manifest")
        index = _json(root / relative)
        digest = index["ordered_page_sha256"][23]
        indexes.append(index)
        pages.append(_page(root, digest))

    preserved: dict[str, list[int]] = {}
    for layout in ("page_then_row", "row_then_page"):
        offsets = []
        for offset in range(PAGE_SIZE - 3):
            if all(_decode(page, offset, layout)[0] <= MAX_PAGE_NUMBER for page in pages):
                offsets.append(offset)
        preserved[layout] = offsets
    pair_counts = {
        layout: sum(
            1
            for position, first in enumerate(offsets)
            for second in offsets[position + 1 :]
            if second - first >= 4
        )
        for layout, offsets in preserved.items()
    }

    def target_valid(index: dict[str, Any], target: tuple[int, int]) -> bool:
        page_number, row = target
        digests = index["ordered_page_sha256"]
        if page_number >= len(digests):
            return False
        page = _page(root, digests[page_number])
        return page[0] == 1 and row < int.from_bytes(page[8:10], "little")

    valid_counts = {}
    for layout in ("page_then_row", "row_then_page"):
        valid_counts[layout] = sum(
            1
            for page, index in zip(pages, indexes, strict=True)
            if len({(_decode(page, offset, layout)) for offset in (35, 39)}) == 2
            and all(target_valid(index, _decode(page, offset, layout)) for offset in (35, 39))
        )
    expected = {
        "preserved_window_count": {"page_then_row": 1872, "row_then_page": 1872},
        "canonical_nonoverlapping_pair_count": {
            "page_then_row": 1745696,
            "row_then_page": 1745696,
        },
        "target_valid_checkpoint_count": {"page_then_row": 7, "row_then_page": 25},
    }
    measured = {
        "preserved_window_count": {name: len(values) for name, values in preserved.items()},
        "canonical_nonoverlapping_pair_count": pair_counts,
        "target_valid_checkpoint_count": valid_counts,
    }
    return {
        "retained_manifest_sha256": manifest_sha256,
        "opened_replicas": [1],
        "holdout_opened": False,
        "page_number_bound_inclusive": MAX_PAGE_NUMBER,
        "checkpoint_count": len(indexes),
        "page_blob_count": len(set(index["ordered_page_sha256"][23] for index in indexes)),
        "measured": measured,
        "expected": expected,
        "result": "pass" if measured == expected else "fail",
    }
