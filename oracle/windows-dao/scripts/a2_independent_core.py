"""Independent A2 record and pointer recomputation primitives.

This module is intentionally standalone.  It was derived only from the frozen
A2 plan, its R2 revision, README, schemas, and EXP-0040/EXP-0041.  It does not
import ``a2_spec.py`` or any analyzer, layer, model, dry-run, or generator code.

The plan fixes a five-byte prefix before an inline bitmap by making every
inline-boundary candidate start at ``global_map.start + 5``.  Bitmap page
ordinals are decoded least-significant bit first.  A candidate start is tied to
the closed E0 extent by requiring exactly the E0 physical-page prefix to decode
in-use and the remaining capacity to decode not-in-use.  This is the direct,
independent allocation-sequence interpretation of the plan's represented-page
set relation; no changed-byte envelope supplies either record boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import pairwise

PAGE_SIZE = 2048
GLOBAL_PREFIX_BYTES = 5
D_CHECKPOINTS = (
    "E0",
    "D_GROW_0128",
    "D_DROP",
    "D_RECREATE_EMPTY",
    "D_REGROW_0128",
)
POLARITIES = ("set_means_in_use", "set_means_not_in_use")


@dataclass(frozen=True)
class ReplicaView:
    replica: int
    checkpoint_ids: tuple[str, ...]
    page_hashes: dict[str, tuple[str, ...]]
    page_counts: dict[str, int]
    load_page: Callable[[str], bytes]

    def state(self, checkpoint: str, page: int) -> str | None:
        hashes = self.page_hashes[checkpoint]
        return hashes[page] if page < len(hashes) else None

    def page(self, checkpoint: str, page: int) -> bytes | None:
        digest = self.state(checkpoint, page)
        return None if digest is None else self.load_page(digest)


@dataclass(frozen=True, order=True)
class GlobalModel:
    page: int
    start: int
    end: int
    bit_polarity: str
    zero_suffix_slack_bytes: int

    def report_value(self) -> dict[str, object]:
        return {
            "record": {"page": self.page, "start": self.start, "end": self.end},
            "bit_polarity": self.bit_polarity,
            "zero_suffix_slack_bytes": self.zero_suffix_slack_bytes,
        }


@dataclass(frozen=True, order=True)
class TdefModel:
    page: int
    start: int
    end: int
    pointer_layout: str
    growth_pointer_offset: int
    delete_reinsert_pointer_offset: int


def candidate_page_space(views: Iterable[ReplicaView]) -> range:
    maximum = 0
    for view in views:
        maximum = max(maximum, max(map(len, view.page_hashes.values()), default=0))
    return range(maximum)


def _different(view: ReplicaView, page: int, left: str, right: str) -> bool:
    return view.state(left, page) != view.state(right, page)


def global_qualifying_pages(views: tuple[ReplicaView, ...]) -> tuple[int, ...]:
    """Apply the plan's hash-only global qualification before byte access."""
    return tuple(
        page
        for page in candidate_page_space(views)
        if all(
            _different(view, page, "E0", "D_GROW_0128")
            and _different(view, page, "D_GROW_0128", "D_DROP")
            for view in views
        )
    )


def growth_pairs(
    checkpoint_ids: tuple[str, ...], *, include_p: bool
) -> tuple[tuple[str, str], ...]:
    prefixes = ("L_REL_", "H_REL_") + (("P_ABS_",) if include_p else ())
    result: list[tuple[str, str]] = []
    for left, right in pairwise(checkpoint_ids):
        if right.startswith(prefixes):
            result.append((left, right))
    return tuple(result)


def tdef_qualifying_pages(views: tuple[ReplicaView, ...]) -> tuple[int, ...]:
    """Apply the separate TDEF hash qualification from the plan."""
    pairs = growth_pairs(views[0].checkpoint_ids, include_p=False)
    return tuple(
        page
        for page in candidate_page_space(views)
        if all(
            view.state("E0", page) is not None
            and any(_different(view, page, left, right) for left, right in pairs)
            and _different(view, page, "L_REL_1280", "L_DELETE_ALL")
            and _different(view, page, "L_DELETE_ALL", "L_REINSERT_SAME")
            for view in views
        )
    )


def _is_in_use(bit: int, polarity: str) -> bool:
    if polarity == "set_means_in_use":
        return bit == 1
    if polarity == "set_means_not_in_use":
        return bit == 0
    raise ValueError(f"unknown polarity: {polarity}")


def _raw_values(polarity: str) -> tuple[int, int]:
    """Return (in-use byte, not-in-use byte) for a uniform byte."""
    return (0xFF, 0x00) if polarity == "set_means_in_use" else (0x00, 0xFF)


def _initial_extent_matches(
    data: bytes, offset: int, pages: int, polarity: str
) -> bool:
    capacity = (PAGE_SIZE - offset) * 8
    if pages >= capacity:
        return False
    used, free = _raw_values(polarity)
    full, remainder = divmod(pages, 8)
    if data[offset : offset + full] != bytes((used,)) * full:
        return False
    cursor = offset + full
    if remainder:
        mask = (1 << remainder) - 1
        expected = mask if polarity == "set_means_in_use" else 0xFF ^ mask
        if data[cursor] != expected:
            return False
        cursor += 1
    return data[cursor:] == bytes((free,)) * (PAGE_SIZE - cursor)


def _decode_set(data: bytes, offset: int, polarity: str) -> set[int]:
    decoded: set[int] = set()
    for byte_ordinal, value in enumerate(data[offset:]):
        for bit in range(8):
            if _is_in_use((value >> bit) & 1, polarity):
                decoded.add(byte_ordinal * 8 + bit)
    return decoded


def _last_d_flip(pages: dict[str, bytes], start: int) -> int | None:
    changed = [
        offset
        for offset in range(start, PAGE_SIZE)
        if len({pages[checkpoint][offset] for checkpoint in D_CHECKPOINTS}) > 1
    ]
    return max(changed) if changed else None


def _global_candidate_at(
    view: ReplicaView, page: int, start: int, polarity: str
) -> GlobalModel | None:
    pages = {checkpoint: view.page(checkpoint, page) for checkpoint in D_CHECKPOINTS}
    if any(value is None for value in pages.values()):
        return None
    present = {key: value for key, value in pages.items() if value is not None}
    offset = start + GLOBAL_PREFIX_BYTES
    if offset >= PAGE_SIZE or not _initial_extent_matches(
        present["E0"], offset, view.page_counts["E0"], polarity
    ):
        return None

    decoded = {
        key: _decode_set(value, offset, polarity) for key, value in present.items()
    }
    first_growth = decoded["D_GROW_0128"] - decoded["E0"]
    if not first_growth:
        return None
    if first_growth & decoded["D_DROP"] or first_growth & decoded["D_RECREATE_EMPTY"]:
        return None
    if not first_growth <= decoded["D_REGROW_0128"]:
        return None
    beyond = decoded["D_REGROW_0128"] - decoded["D_GROW_0128"]
    if not any(index >= view.page_counts["D_GROW_0128"] for index in beyond):
        return None

    last_flip = _last_d_flip(present, start)
    if last_flip is None:
        return None
    slack = PAGE_SIZE - last_flip - 1
    if slack < 16:
        return None
    free = _raw_values(polarity)[1]
    if any(
        value[last_flip + 1 :] != bytes((free,)) * slack for value in present.values()
    ):
        return None
    return GlobalModel(page, start, PAGE_SIZE, polarity, slack)


def global_page_models(view: ReplicaView, page: int) -> tuple[GlobalModel, ...]:
    """Resolve page-terminal candidates after testing every fixed start."""
    models = {
        model
        for start in range(PAGE_SIZE - GLOBAL_PREFIX_BYTES)
        for polarity in POLARITIES
        if (model := _global_candidate_at(view, page, start, polarity)) is not None
    }
    return tuple(sorted(models))


def derive_global_models(
    views: tuple[ReplicaView, ...], qualified_pages: tuple[int, ...]
) -> tuple[GlobalModel, ...]:
    """Intersect independently derived replica models without majority voting."""
    per_replica: list[set[GlobalModel]] = []
    for view in views:
        models = {
            model
            for page in qualified_pages
            for model in global_page_models(view, page)
        }
        per_replica.append(models)
    return tuple(sorted(set.intersection(*per_replica))) if per_replica else ()


def global_model_predicts(view: ReplicaView, model: GlobalModel) -> bool:
    candidate = _global_candidate_at(view, model.page, model.start, model.bit_polarity)
    return candidate == model


def growth_polarity_violations(
    view: ReplicaView, model: GlobalModel
) -> tuple[tuple[str, str], ...]:
    """Return growth legs containing an in-use to not-in-use record-bit flip."""
    violations: list[tuple[str, str]] = []
    offset = model.start + GLOBAL_PREFIX_BYTES
    for left, right in growth_pairs(view.checkpoint_ids, include_p=True):
        before = view.page(left, model.page)
        after = view.page(right, model.page)
        if before is None or after is None:
            violations.append((left, right))
            continue
        disagrees = False
        for old, new in zip(before[offset : model.end], after[offset : model.end]):
            for bit in range(8):
                was = _is_in_use((old >> bit) & 1, model.bit_polarity)
                now = _is_in_use((new >> bit) & 1, model.bit_polarity)
                if was and not now:
                    disagrees = True
                    break
            if disagrees:
                break
        if disagrees:
            violations.append((left, right))
    return tuple(violations)


def _decode_pointer(raw: bytes, layout: str) -> tuple[int, int]:
    if len(raw) != 4:
        raise ValueError("pointer window must be four bytes")
    if layout == "u24le_page_then_u8_slot":
        return int.from_bytes(raw[:3], "little"), raw[3]
    if layout == "u8_slot_then_u24le_page":
        return int.from_bytes(raw[1:], "little"), raw[0]
    raise ValueError(f"unknown pointer layout: {layout}")


def _reference_valid(view: ReplicaView, checkpoint: str, page: int) -> bool:
    if page == 0:
        return True
    target = view.page(checkpoint, page)
    return target is not None and target[0] == 0x05


def _window_series(
    view: ReplicaView, page: int, offset: int
) -> dict[str, bytes] | None:
    result: dict[str, bytes] = {}
    for checkpoint in view.checkpoint_ids:
        data = view.page(checkpoint, page)
        if data is None:
            return None
        result[checkpoint] = data[offset : offset + 4]
    return result


def _window_changes(series: dict[str, bytes], pairs: Iterable[tuple[str, str]]) -> bool:
    return any(series[left] != series[right] for left, right in pairs)


def _valid_growth_window(
    view: ReplicaView, page: int, offset: int, layout: str
) -> bool:
    series = _window_series(view, page, offset)
    if series is None:
        return False
    permitted = set(growth_pairs(view.checkpoint_ids, include_p=False))
    transitions = tuple(zip(view.checkpoint_ids, view.checkpoint_ids[1:]))
    if not _window_changes(series, permitted):
        return False
    if any(series[a] != series[b] for a, b in transitions if (a, b) not in permitted):
        return False
    return all(
        _reference_valid(view, checkpoint, _decode_pointer(raw, layout)[0])
        for checkpoint, raw in series.items()
    )


def _valid_churn_window(view: ReplicaView, page: int, offset: int, layout: str) -> bool:
    series = _window_series(view, page, offset)
    if series is None:
        return False
    before = series["L_REL_1280"]
    deleted = series["L_DELETE_ALL"]
    reinserted = series["L_REINSERT_SAME"]
    if before == deleted or reinserted != before:
        return False
    churn = {
        ("L_REL_1280", "L_DELETE_ALL"),
        ("L_DELETE_ALL", "L_REINSERT_SAME"),
    }
    transitions = tuple(zip(view.checkpoint_ids, view.checkpoint_ids[1:]))
    if any(series[a] != series[b] for a, b in transitions if (a, b) not in churn):
        return False
    return all(
        _reference_valid(view, checkpoint, _decode_pointer(raw, layout)[0])
        for checkpoint, raw in series.items()
    )


def _stable_byte(view: ReplicaView, page: int, offset: int) -> bool:
    values = {view.page(checkpoint, page)[offset] for checkpoint in view.checkpoint_ids}  # type: ignore[index]
    return len(values) == 1


def _minimal_tdef_record(
    view: ReplicaView, page: int, growth: int, churn: int
) -> tuple[int, int] | None:
    low = min(growth, churn)
    high = max(growth + 4, churn + 4)
    start = low - 1 if low else 0
    end = high + 1 if high < PAGE_SIZE else PAGE_SIZE
    pointer_offsets = set(range(growth, growth + 4)) | set(range(churn, churn + 4))
    for offset in range(start, end):
        if offset not in pointer_offsets and not _stable_byte(view, page, offset):
            return None
    if low and not _stable_byte(view, page, low - 1):
        return None
    if high < PAGE_SIZE and not _stable_byte(view, page, high):
        return None
    return start, end


def tdef_page_models(view: ReplicaView, page: int) -> tuple[TdefModel, ...]:
    layouts = ("u24le_page_then_u8_slot", "u8_slot_then_u24le_page")
    models: set[TdefModel] = set()
    for layout in layouts:
        growth = [
            offset
            for offset in range(PAGE_SIZE - 3)
            if _valid_growth_window(view, page, offset, layout)
        ]
        churn = [
            offset
            for offset in range(PAGE_SIZE - 3)
            if _valid_churn_window(view, page, offset, layout)
        ]
        for growth_offset in growth:
            for churn_offset in churn:
                if abs(growth_offset - churn_offset) < 4:
                    continue
                record = _minimal_tdef_record(view, page, growth_offset, churn_offset)
                if record is not None:
                    models.add(
                        TdefModel(
                            page,
                            record[0],
                            record[1],
                            layout,
                            growth_offset,
                            churn_offset,
                        )
                    )
    return tuple(sorted(models))


def derive_tdef_models(
    views: tuple[ReplicaView, ...], qualified_pages: tuple[int, ...]
) -> tuple[TdefModel, ...]:
    per_replica = [
        {model for page in qualified_pages for model in tdef_page_models(view, page)}
        for view in views
    ]
    return tuple(sorted(set.intersection(*per_replica))) if per_replica else ()
