"""Small resource and plan-order helpers for independent H4 stages."""

from collections.abc import Mapping, Sequence
from typing import Any

from a4_independent_h3 import H3ValidationError, _row


class H4ValidationError(ValueError):
    """A fail-closed independent H4 recomputation error."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def first_cardinality_failure(
    counts: Mapping[int, Sequence[int]],
) -> tuple[int, int, int] | None:
    """Return replica, count, and NONE/MULTIPLE offset in predicate order."""
    for kind in (0, 1):
        for number in (1, 2):
            count = min(counts[number]) if kind == 0 else max(counts[number])
            if (count == 0) if kind == 0 else (count > 1):
                return number, count, kind
    return None


def catalog_rows(
    page: bytes, mask: int, charge_prefix: tuple[int, str, int] | None = None,
    work: dict[str, int] | None = None, charged: set[tuple[int, str, int, int]] | None = None,
) -> list[tuple[int, int, int, bytes]]:
    """Decode one catalog inventory and charge each physical row identity once."""
    if page[0] != 1:
        return []
    count = int.from_bytes(page[8:10], "little")
    if count > 679:
        raise H4ValidationError("catalog_row_bound")
    result = []
    for ordinal in range(count):
        try:
            raw, start, end = _row(page, ordinal, mask)
        except H3ValidationError as exc:
            raise H4ValidationError("catalog_directory_invalid") from exc
        identity = None if charge_prefix is None else (*charge_prefix, ordinal)
        if identity is not None and work is not None and charged is not None and identity not in charged:
            charged.add(identity)
            work["catalog_raw_rows"] += 1
        result.append((ordinal, start, end, raw))
    return result


def is_resource_error(error: BaseException) -> bool:
    return getattr(error, "code", None) == "resource_bound_breach"
