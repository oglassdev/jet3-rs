#!/usr/bin/env python3
"""Bounded A4 H1 derivation: TDEF lifecycle to allocation-row locators.

H1 intentionally stops at tag-01 existence and row-ordinal validity.  Row
directories, row flags, map tags, bitmap polarity, and role semantics belong to
H2 and are not interpreted here.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from protocol_validation import ValidationError

from a4_measurements import MeasurementRecorder, measure
from a4_model import (
    A4AnalysisError,
    QualifiedPage,
    View,
    WorkLedger,
    canonical_candidate_id,
    canonical_model_id,
)
from a4_spec import (
    BOUNDS,
    CANDIDATE_GRAMMARS,
    CHECKPOINT_IDS,
    CHECKPOINT_ORDINALS,
    LIFECYCLE_RANGES,
    PAGE_SIZE,
    PLAN,
    canonical_json_bytes,
    validate_failure_count,
)

_H1 = CANDIDATE_GRAMMARS["h1"]
_LAYOUTS = tuple(_H1["locator_layouts"])
_LIFECYCLE_SIGNATURES = tuple(_H1["tdef_lifecycle_signatures"])
_INSTANCE_ORDER = tuple(LIFECYCLE_RANGES)
_MAX_PAGE = int(BOUNDS["max_final_pages_per_replica"]) - 1
_MAX_CANDIDATES = int(BOUNDS["max_candidate_models"])
_MAX_CANDIDATE_BYTES = int(BOUNDS["max_canonical_candidate_bytes"])
_MAX_QUALIFIED = int(BOUNDS["max_qualified_pages_per_submodel"])

_SCHEMA_TRANSITIONS = tuple(
    PLAN["checkpoint_design"]["transition_coverage"]["schema_lifecycle"]
)
_IDLE_PAIRS = tuple(
    tuple(pair) for pair in PLAN["checkpoint_design"]["idle_pairs"]
)
_CREATE_CHECKPOINT = {
    "T1-v1": "T1_CREATE_ID",
    "T2-v1": "T2_CREATE",
    "T2-v2": "T2_RECREATE",
    "T3-v1": "T3_CREATE",
    "T4-v1": "T4_CREATE",
}
_OWN_SCHEMA_TRANSITIONS = {
    "T1-v1": frozenset(("T1_CREATE_ID", "T1_ADD_TEXT", "T1_ADD_INDEX")),
    "T2-v1": frozenset(("T2_CREATE", "T2_DROP")),
    "T2-v2": frozenset(("T2_RECREATE",)),
    "T3-v1": frozenset(("T3_CREATE",)),
    "T4-v1": frozenset(("T4_CREATE",)),
}


@dataclass(frozen=True, order=True)
class LocatorTarget:
    page: int
    row: int

    def document(self) -> dict[str, int]:
        return {"page": self.page, "row": self.row}


@dataclass(frozen=True)
class H1Binding:
    replica: int
    logical_role: str
    lifecycle_instance: str
    tdef_page: int
    locator_targets: tuple[LocatorTarget, LocatorTarget] | None = None

    @property
    def checkpoints(self) -> tuple[str, ...]:
        lifecycle = LIFECYCLE_RANGES[self.lifecycle_instance]
        return tuple(
            CHECKPOINT_IDS[lifecycle.first_ordinal : lifecycle.last_ordinal + 1]
        )

    def document(self, *, include_targets: bool) -> dict[str, Any]:
        lifecycle = LIFECYCLE_RANGES[self.lifecycle_instance]
        result: dict[str, Any] = {
            "replica": self.replica,
            "logical_role": self.logical_role,
            "lifecycle_instance": self.lifecycle_instance,
            "tdef_page": self.tdef_page,
        }
        if include_targets:
            if self.locator_targets is None:
                raise ValueError("H1 locator binding is missing its targets")
            result["locator_targets"] = [
                target.document() for target in self.locator_targets
            ]
        result["applicable_checkpoint_range"] = {
            "start": lifecycle.first_checkpoint,
            "end": lifecycle.last_checkpoint,
        }
        return result


@dataclass(frozen=True)
class H1ReplicaCandidate:
    replica: int
    layout: str
    table_signature_id: str
    locator_offsets: tuple[int, int]
    bindings: tuple[H1Binding, ...]

    @property
    def model(self) -> dict[str, Any]:
        return {
            "layout": self.layout,
            "table_signature_id": self.table_signature_id,
            "locator_offsets": list(self.locator_offsets),
        }

    @property
    def canonical_model_id(self) -> str:
        return canonical_model_id("h1_locator_pair", self.model)

    @property
    def canonical_candidate_id(self) -> str:
        return canonical_candidate_id(
            "h1_locator_pair",
            self.model,
            [binding.document(include_targets=True) for binding in self.bindings],
        )

    def document(self) -> dict[str, Any]:
        return _checked_candidate(
            "h1_locator_pair",
            self.model,
            self.bindings,
            include_targets=True,
        )


class H1Terminal(A4AnalysisError):
    """Registered H1 terminal with its schema-directed frozen payload."""

    def __init__(
        self,
        predicate_id: str,
        survivor_count: int,
        *,
        candidate_stage: str | None,
        candidates: Sequence[Mapping[str, Any]] = (),
        terminal_evidence: Mapping[str, Any] | None = None,
        per_replica_counts: Sequence[int] | None = None,
        detail: str | None = None,
    ) -> None:
        validate_failure_count(
            predicate_id,
            survivor_count,
            per_replica_counts=per_replica_counts,
        )
        self.candidate_stage = candidate_stage
        self.candidates = tuple(dict(candidate) for candidate in candidates)
        self.terminal_evidence = (
            None if terminal_evidence is None else dict(terminal_evidence)
        )
        if terminal_evidence is not None:
            self.payload_kind = "replica_pair"
        else:
            self.payload_kind = "candidate_set"
        super().__init__(predicate_id, survivor_count, detail=detail)


def _checked_candidate(
    model_type: str,
    model: Mapping[str, Any],
    bindings: Sequence[H1Binding],
    *,
    include_targets: bool,
) -> dict[str, Any]:
    binding_documents = [
        binding.document(include_targets=include_targets) for binding in bindings
    ]
    document = {
        "model_type": model_type,
        "canonical_model_id": canonical_model_id(model_type, model),
        "canonical_candidate_id": canonical_candidate_id(
            model_type, model, binding_documents
        ),
        "model": dict(model),
        "instance_bindings": binding_documents,
    }
    if len(canonical_json_bytes(document)) > _MAX_CANDIDATE_BYTES:
        raise A4AnalysisError(
            "A4-RESOURCE-BOUND", detail="H1 canonical candidate exceeds its bound"
        )
    return document


def _bounded_candidates(values: Iterable[Any]) -> tuple[Any, ...]:
    retained: list[Any] = []
    for value in values:
        retained.append(value)
        if len(retained) > _MAX_CANDIDATES:
            raise A4AnalysisError(
                "A4-RESOURCE-BOUND", detail="H1 candidate count exceeds its bound"
            )
    return tuple(retained)


def _tag(view: View, checkpoint: str, page: int) -> int | None:
    payload = view.page_optional(checkpoint, page)
    return None if payload is None else payload[0]


def _hash_changed(view: View, left: str, right: str, page: int) -> bool:
    left_hash = view.hash_at(left, page)
    right_hash = view.hash_at(right, page)
    return left_hash is not None and right_hash is not None and left_hash != right_hash


def _lifecycle_holds(
    view: View, instance: str, page: int, signature: str
) -> bool:
    lifecycle = LIFECYCLE_RANGES[instance]
    create = _CREATE_CHECKPOINT[instance]
    create_ordinal = CHECKPOINT_ORDINALS[create]
    before = CHECKPOINT_IDS[create_ordinal - 1]
    checkpoints = CHECKPOINT_IDS[
        lifecycle.first_ordinal : lifecycle.last_ordinal + 1
    ]
    if signature == "new_tag_02_at_role_create":
        if _tag(view, before, page) == 0x02:
            return False
        if any(_tag(view, checkpoint, page) != 0x02 for checkpoint in checkpoints):
            return False
        if instance == "T2-v1" and _tag(view, "T2_DROP", page) == 0x02:
            return False
        return True
    if signature != "preexisting_tag_02_hash_transition":
        raise ValidationError(f"unknown H1 lifecycle signature {signature!r}")
    if _tag(view, before, page) != 0x02 or _tag(view, create, page) != 0x02:
        return False
    if not _hash_changed(view, before, create, page):
        return False
    if any(_tag(view, checkpoint, page) != 0x02 for checkpoint in checkpoints):
        return False

    for right in _SCHEMA_TRANSITIONS:
        right_ordinal = CHECKPOINT_ORDINALS[right]
        if right_ordinal == 0 or right in _OWN_SCHEMA_TRANSITIONS[instance]:
            continue
        left = CHECKPOINT_IDS[right_ordinal - 1]
        if left not in checkpoints or right not in checkpoints:
            continue
        if view.hash_at(left, page) != view.hash_at(right, page):
            return False
    for left, right in _IDLE_PAIRS:
        if left in checkpoints and right in checkpoints:
            if view.hash_at(left, page) != view.hash_at(right, page):
                return False
    if instance == "T2-v1" and not _hash_changed(
        view, "T2_CREATE", "T2_DROP", page
    ):
        return False
    if instance == "T2-v2" and not _hash_changed(
        view, "T2_DROP", "T2_RECREATE", page
    ):
        return False
    return True


def _qualify_pages(pages: Sequence[int]) -> tuple[int, ...]:
    if any(
        isinstance(page, bool)
        or not isinstance(page, int)
        or not 0 <= page <= _MAX_PAGE
        for page in pages
    ):
        raise ValueError("H1 qualified TDEF pages must be bounded integers")
    result = tuple(sorted(set(pages)))
    if len(result) != len(pages):
        raise ValueError("H1 qualified TDEF pages must be duplicate-free")
    if len(result) > _MAX_QUALIFIED:
        raise A4AnalysisError(
            "A4-RESOURCE-BOUND", detail="too many H1 qualified TDEF pages"
        )
    return result


def _tdef_candidates(
    view: View, pages: Sequence[int], ledger: WorkLedger
) -> tuple[dict[str, Any], ...]:
    for page in pages:
        for checkpoint in CHECKPOINT_IDS:
            qualified = QualifiedPage(view.replica, checkpoint, page)
            for signature in _LIFECYCLE_SIGNATURES:
                ledger.charge_qualified(
                    "tdef_lifecycle_signatures", qualified, discriminator=signature
                )

    def candidates() -> Iterable[dict[str, Any]]:
        for signature in _LIFECYCLE_SIGNATURES:
            choices = []
            for instance in _INSTANCE_ORDER:
                choices.append(
                    tuple(
                        page
                        for page in pages
                        if _lifecycle_holds(view, instance, page, signature)
                    )
                )
            if any(not choice for choice in choices):
                continue
            for selected in itertools.product(*choices):
                bindings = tuple(
                    H1Binding(
                        view.replica,
                        LIFECYCLE_RANGES[instance].logical_role,
                        instance,
                        page,
                    )
                    for instance, page in zip(_INSTANCE_ORDER, selected)
                )
                yield _checked_candidate(
                    "h1_tdef",
                    {"tdef_lifecycle_signature": signature},
                    bindings,
                    include_targets=False,
                )

    return _bounded_candidates(candidates())


def _decode_locator(raw: bytes, layout: str) -> LocatorTarget:
    if len(raw) != 4:
        raise ValueError("H1 locator must be exactly four bytes")
    if layout == "u24le_page_then_u8_row":
        return LocatorTarget(int.from_bytes(raw[:3], "little"), raw[3])
    if layout == "u8_row_then_u24le_page":
        return LocatorTarget(int.from_bytes(raw[1:], "little"), raw[0])
    raise ValidationError(f"unknown H1 locator layout {layout!r}")


def _preserved_window(view: View, page: int, layout: str, offset: int) -> bool:
    """Apply AMB-01's fixed page bound at every derivation checkpoint."""
    for checkpoint in CHECKPOINT_IDS:
        payload = view.page_optional(checkpoint, page)
        if payload is None:
            return False
        target = _decode_locator(payload[offset : offset + 4], layout)
        if target.page > _MAX_PAGE:
            return False
    return True


def _signature_spec(signature_id: str) -> tuple[bytes, bytes, tuple[tuple[int, int], ...]]:
    base = _H1["table_record_signature"]
    value = bytes.fromhex(base["value_hex"])
    mask = bytearray.fromhex(base["mask_hex"])
    if signature_id == base["signature_id"]:
        holes = tuple(tuple(interval) for interval in base["locator_holes"])
        return value, bytes(mask), holes
    duplicate = _H1["pair_multiple_reachability_signature"]
    if signature_id != duplicate["signature_id"]:
        raise ValidationError(f"unknown H1 table signature {signature_id!r}")
    for override in duplicate["mask_derivation"]["overrides"]:
        start, end = override["interval"]
        replacement = bytes.fromhex(override["mask_hex"])
        if len(replacement) != end - start:
            raise ValidationError("H1 signature mask override has the wrong width")
        mask[start:end] = replacement
    holes = tuple(tuple(interval) for interval in duplicate["locator_holes"])
    return value, bytes(mask), holes


def _signature_matches(payload: bytes, signature_id: str) -> bool:
    value, mask, _ = _signature_spec(signature_id)
    if len(value) != len(mask) or len(payload) < len(value):
        return False
    if any((actual & masked) != (expected & masked) for actual, expected, masked in zip(payload, value, mask)):
        return False
    if signature_id == _H1["table_record_signature"]["signature_id"]:
        return True
    duplicate = _H1["pair_multiple_reachability_signature"]
    for equality in duplicate["equal_byte_intervals"]:
        left_start, left_end = equality["left"]
        right_start, right_end = equality["right"]
        if payload[left_start:left_end] != payload[right_start:right_end]:
            return False
    inequality = duplicate["mutual_exclusion_inequality"]
    left_start, left_end = inequality["left"]
    right = inequality["right"]
    fixed = bytes.fromhex(right["fixed_value_hex"])
    fixed_mask = bytes.fromhex(right["fixed_mask_hex"])
    actual = payload[left_start:left_end]
    if all((byte & masked) == (expected & masked) for byte, expected, masked in zip(actual, fixed, fixed_mask)):
        return False
    return True


def _structural_bindings(
    view: View,
    tdef_bindings: Sequence[H1Binding],
    layout: str,
    signature_id: str,
    offsets: tuple[int, int],
) -> tuple[H1Binding, ...] | None:
    result: list[H1Binding] = []
    for binding in tdef_bindings:
        targets: list[LocatorTarget] = []
        for checkpoint in binding.checkpoints:
            payload = view.page_optional(checkpoint, binding.tdef_page)
            if payload is None or not _signature_matches(payload, signature_id):
                return None
            decoded = tuple(
                _decode_locator(payload[offset : offset + 4], layout)
                for offset in offsets
            )
            if any(target.page > _MAX_PAGE for target in decoded):
                return None
            if not targets:
                targets.extend(decoded)
            elif tuple(targets) != decoded:
                return None
        if len(targets) != 2:
            return None
        result.append(
            H1Binding(
                binding.replica,
                binding.logical_role,
                binding.lifecycle_instance,
                binding.tdef_page,
                (targets[0], targets[1]),
            )
        )
    return tuple(result)


def _target_valid(
    view: View,
    bindings: Sequence[H1Binding],
    layout: str,
    ledger: WorkLedger,
) -> bool:
    for binding in bindings:
        if binding.locator_targets is None:
            return False
        first, second = binding.locator_targets
        distinct_targets = tuple(sorted(set((first, second))))
        if len(distinct_targets) != 2:
            return False
        for checkpoint in binding.checkpoints:
            for target in distinct_targets:
                if view.replica in (1, 2):
                    ledger.charge_qualified(
                        "h1_target_validity_checks",
                        QualifiedPage(view.replica, checkpoint, target.page),
                        discriminator=(layout, target.page, target.row),
                    )
                payload = view.page_optional(checkpoint, target.page)
                if payload is None or payload[0] != 0x01:
                    return False
                row_count = int.from_bytes(payload[8:10], "little")
                if target.row >= row_count:
                    return False
    return True


def derive_h1_replica(
    view: View,
    qualified_tdef_pages: Sequence[int],
    ledger: WorkLedger,
    measurements: MeasurementRecorder | None = None,
) -> H1ReplicaCandidate:
    """Run H1's eight non-holdout predicates for one derivation replica."""
    if view.replica not in (1, 2):
        raise ValueError("H1 derivation accepts only replicas 1 and 2")
    pages = _qualify_pages(qualified_tdef_pages)
    tdef = _tdef_candidates(view, pages, ledger)
    measure(measurements, "A4-H1-TDEF-NONE", len(tdef), bool(tdef), replica=view.replica)
    if not tdef:
        raise H1Terminal("A4-H1-TDEF-NONE", 0, candidate_stage="h1_tdef")
    measure(measurements, "A4-H1-TDEF-MULTIPLE", len(tdef), len(tdef) == 1, replica=view.replica)
    if len(tdef) > 1:
        raise H1Terminal(
            "A4-H1-TDEF-MULTIPLE",
            len(tdef),
            candidate_stage="h1_tdef",
            candidates=tdef,
        )

    bindings = tuple(
        H1Binding(
            binding["replica"],
            binding["logical_role"],
            binding["lifecycle_instance"],
            binding["tdef_page"],
        )
        for binding in tdef[0]["instance_bindings"]
    )
    for page in pages:
        ledger.charge_once("raw_locator_windows", (view.replica, page), 4090)
        ledger.charge_once("raw_locator_pairs", (view.replica, page), 4_167_722)

    syntactic_layouts = tuple(
        layout
        for layout in _LAYOUTS
        if any(
            any(
                _preserved_window(view, binding.tdef_page, layout, offset)
                for offset in range(PAGE_SIZE - 3)
            )
            for binding in bindings
        )
    )
    measure(measurements, "A4-H1-LOCATOR-LAYOUT-NONE", len(syntactic_layouts), bool(syntactic_layouts), replica=view.replica)
    if not syntactic_layouts:
        raise H1Terminal(
            "A4-H1-LOCATOR-LAYOUT-NONE",
            0,
            candidate_stage="h1_target_valid_layout",
        )

    structural: list[tuple[str, str, tuple[int, int], tuple[H1Binding, ...]]] = []
    signature_specs = (
        _H1["table_record_signature"],
        _H1["pair_multiple_reachability_signature"],
    )
    for layout in syntactic_layouts:
        for signature in signature_specs:
            signature_id = signature["signature_id"]
            holes = tuple(tuple(interval) for interval in signature["locator_holes"])
            offsets = tuple(interval[0] for interval in holes)
            for first, second in itertools.combinations(offsets, 2):
                if second - first < 4:
                    continue
                pair = (first, second)
                candidate_bindings = _structural_bindings(
                    view, bindings, layout, signature_id, pair
                )
                if candidate_bindings is not None:
                    structural.append(
                        (layout, signature_id, pair, candidate_bindings)
                    )
    measure(measurements, "A4-H1-LOCATOR-PAIR-NONE", len(structural), bool(structural), replica=view.replica)
    if not structural:
        raise H1Terminal(
            "A4-H1-LOCATOR-PAIR-NONE", 0, candidate_stage="h1_locator_pair"
        )

    target_valid = tuple(
        candidate
        for candidate in structural
        if _target_valid(view, candidate[3], candidate[0], ledger)
    )
    measure(measurements, "A4-H1-TARGET-ROW-INVALID", len(target_valid), bool(target_valid), replica=view.replica)
    if not target_valid:
        raise H1Terminal(
            "A4-H1-TARGET-ROW-INVALID", 0, candidate_stage="h1_locator_pair"
        )

    layouts = tuple(dict.fromkeys(candidate[0] for candidate in target_valid))
    measure(measurements, "A4-H1-LOCATOR-LAYOUT-MULTIPLE", len(layouts), len(layouts) == 1, replica=view.replica)
    if len(layouts) > 1:
        layout_by_id: dict[str, dict[str, Any]] = {}
        for layout, signature_id, _pair, candidate_bindings in target_valid:
            document = _checked_candidate(
                "h1_target_valid_layout",
                {"layout": layout, "table_signature_id": signature_id},
                candidate_bindings,
                include_targets=True,
            )
            layout_by_id.setdefault(document["canonical_candidate_id"], document)
        layout_candidates = tuple(layout_by_id.values())
        raise H1Terminal(
            "A4-H1-LOCATOR-LAYOUT-MULTIPLE",
            len(layouts),
            candidate_stage="h1_target_valid_layout",
            candidates=layout_candidates,
        )

    surviving = tuple(candidate for candidate in target_valid if candidate[0] == layouts[0])
    pair_documents = tuple(
        _checked_candidate(
            "h1_locator_pair",
            {
                "layout": layout,
                "table_signature_id": signature_id,
                "locator_offsets": list(pair),
            },
            candidate_bindings,
            include_targets=True,
        )
        for layout, signature_id, pair, candidate_bindings in surviving
    )
    measure(measurements, "A4-H1-LOCATOR-PAIR-MULTIPLE", len(surviving), len(surviving) == 1, replica=view.replica)
    if len(surviving) > 1:
        raise H1Terminal(
            "A4-H1-LOCATOR-PAIR-MULTIPLE",
            len(surviving),
            candidate_stage="h1_locator_pair",
            candidates=pair_documents,
        )
    layout, signature_id, pair, final_bindings = surviving[0]
    return H1ReplicaCandidate(
        view.replica, layout, signature_id, pair, final_bindings
    )


def agree_h1_replicas(
    replica_1: H1ReplicaCandidate,
    replica_2: H1ReplicaCandidate,
    measurements: MeasurementRecorder | None = None,
) -> H1ReplicaCandidate:
    """Apply H1 replica agreement and merge only agreeing physical bindings."""
    if (replica_1.replica, replica_2.replica) != (1, 2):
        raise ValueError("H1 replica agreement requires replicas 1 then 2")
    model_count = len({replica_1.canonical_model_id, replica_2.canonical_model_id})
    agrees = model_count == 1
    measure(measurements, "A4-H1-REPLICA-DISAGREEMENT", model_count, agrees)
    if not agrees:
        entries = []
        for candidate in (replica_1, replica_2):
            entries.append(
                {
                    "replica": candidate.replica,
                    "canonical_model_id": candidate.canonical_model_id,
                    "canonical_candidate_id": candidate.canonical_candidate_id,
                    "complete_candidate": candidate.document(),
                }
            )
        raise H1Terminal(
            "A4-H1-REPLICA-DISAGREEMENT",
            2,
            candidate_stage="h1_locator_pair",
            terminal_evidence={"kind": "replica_pair", "entries": entries},
            per_replica_counts=(1, 1),
        )
    return H1ReplicaCandidate(
        0,
        replica_1.layout,
        replica_1.table_signature_id,
        replica_1.locator_offsets,
        replica_1.bindings + replica_2.bindings,
    )


def predict_h1(
    view: View,
    qualified_tdef_pages: Sequence[int],
    frozen: H1ReplicaCandidate,
    ledger: WorkLedger,
) -> H1ReplicaCandidate | None:
    """Apply only the frozen H1 model to holdout lifecycle bindings."""
    if view.replica != 3 or frozen.replica not in (0, 3):
        raise ValueError("H1 holdout prediction requires replica 3 and a frozen model")
    pages = _qualify_pages(qualified_tdef_pages)
    located: list[tuple[H1Binding, ...]] = []
    for lifecycle_signature in _LIFECYCLE_SIGNATURES:
        choices = [
            tuple(
                page
                for page in pages
                if _lifecycle_holds(view, instance, page, lifecycle_signature)
            )
            for instance in _INSTANCE_ORDER
        ]
        if any(not choice for choice in choices):
            continue
        for selected in itertools.product(*choices):
            bindings = tuple(
                H1Binding(
                    3,
                    LIFECYCLE_RANGES[instance].logical_role,
                    instance,
                    page,
                )
                for instance, page in zip(_INSTANCE_ORDER, selected)
            )
            complete = _structural_bindings(
                view,
                bindings,
                frozen.layout,
                frozen.table_signature_id,
                frozen.locator_offsets,
            )
            if complete is not None and _target_valid(
                view, complete, frozen.layout, ledger
            ):
                located.append(complete)
                if len(located) > 1:
                    return None
    if len(located) != 1:
        return None
    result = H1ReplicaCandidate(
        3,
        frozen.layout,
        frozen.table_signature_id,
        frozen.locator_offsets,
        located[0],
    )
    return result if result.canonical_model_id == frozen.canonical_model_id else None
