"""Shared bounded binary decoders for the A4 layer adapters."""

from __future__ import annotations

from a4_model import A4AnalysisError


def set_bits(payload: bytes) -> frozenset[int]:
    result: set[int] = set()
    for byte_index, value in enumerate(payload):
        for bit in range(8):
            if value & (1 << bit):
                result.add(byte_index * 8 + bit)
    return frozenset(result)


def decode_locator(raw: bytes, layout: str) -> tuple[int, int]:
    if len(raw) != 4:
        raise ValueError("A4 locator must be four bytes")
    if layout == "u24le_page_then_u8_row":
        return int.from_bytes(raw[:3], "little"), raw[3]
    if layout == "u8_row_then_u24le_page":
        return int.from_bytes(raw[1:], "little"), raw[0]
    raise ValueError("A4 frozen locator layout is unknown")


def type0_owned(
    row: bytes, polarity: str, *, maximum: int | None = None
) -> frozenset[int]:
    if len(row) < 5 or row[0] != 0:
        raise ValueError("A4 frozen system usage row is not type 0")
    if maximum is not None and (
        isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1
    ):
        raise ValueError("A4 type-0 admitted-page maximum must be positive")
    base = int.from_bytes(row[1:5], "little")
    selected: set[int] = set()
    for byte_index, value in enumerate(row[5:]):
        for bit in range(8):
            is_set = bool(value & (1 << bit))
            if is_set == (polarity == "set_bit_owned_in_use"):
                page = base + byte_index * 8 + bit
                if page not in selected and maximum is not None and len(selected) == maximum:
                    raise A4AnalysisError(
                        "A4-RESOURCE-BOUND",
                        detail=f"type-0 traversal admits more than {maximum} pages",
                    )
                selected.add(page)
    return frozenset(selected)
