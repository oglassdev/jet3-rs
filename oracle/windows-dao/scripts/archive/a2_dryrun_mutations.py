"""Concrete schedule-derived bundle perturbations for A2 Abort reachability."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Callable

from a2_generator import SyntheticBundle, SyntheticParameters
from a2_generator_pages import _extended_page
from a2_model import CHECKPOINT_IDS, D_CHECKPOINTS, PAGE_SIZE, PLAN

BundleSet = tuple[SyntheticBundle, ...]
Mutation = Callable[[BundleSet], BundleSet]
CONVERSION_WINDOW = tuple(
    PLAN["checkpoint_design"]["transition_coverage"][
        "inline_to_indirect_conversion_window"
    ]
)


@dataclass(frozen=True)
class ReachabilityAttempt:
    predicate_id: str
    perturbation: str
    parameters: SyntheticParameters
    mutation: Mutation


def _replace_payload(
    bundle: SyntheticBundle, checkpoint: str, page: int, payload: bytes
) -> SyntheticBundle:
    if len(payload) != PAGE_SIZE:
        raise ValueError("perturbed page must retain the plan page size")
    digest = hashlib.sha256(payload).hexdigest()
    ordered = dict(bundle.ordered_page_sha256)
    hashes = list(ordered[checkpoint])
    hashes[page] = digest
    ordered[checkpoint] = tuple(hashes)
    payloads = dict(bundle._payloads)
    payloads[digest] = payload
    return replace(
        bundle,
        ordered_page_sha256=MappingProxyType(ordered),
        _payloads=MappingProxyType(payloads),
    )


def _replace_index(
    bundle: SyntheticBundle, checkpoint: str, hashes: tuple[str, ...]
) -> SyntheticBundle:
    ordered = dict(bundle.ordered_page_sha256)
    counts = dict(bundle.page_count)
    ordered[checkpoint] = hashes
    counts[checkpoint] = len(hashes)
    return replace(
        bundle,
        ordered_page_sha256=MappingProxyType(ordered),
        page_count=MappingProxyType(counts),
    )


def _all(bundles: BundleSet, change: Callable[[SyntheticBundle], SyntheticBundle]) -> BundleSet:
    return tuple(change(bundle) for bundle in bundles)


def _edit(
    bundle: SyntheticBundle,
    checkpoint: str,
    page: int,
    change: Callable[[bytearray], None],
) -> SyntheticBundle:
    digest = bundle.ordered_page_sha256[checkpoint][page]
    payload = bytearray(bundle.page_bytes(digest))
    change(payload)
    return _replace_payload(bundle, checkpoint, page, bytes(payload))


def _copy_page(
    bundle: SyntheticBundle,
    target_checkpoint: str,
    target_page: int,
    source_checkpoint: str,
    source_page: int,
) -> SyntheticBundle:
    digest = bundle.ordered_page_sha256[source_checkpoint][source_page]
    return _replace_payload(
        bundle, target_checkpoint, target_page, bundle.page_bytes(digest)
    )


def _sync_idle(bundle: SyntheticBundle, page: int) -> SyntheticBundle:
    result = bundle
    for left, right in PLAN["checkpoint_design"]["idle_pairs"]:
        result = _copy_page(result, right, page, left, page)
    return result


def _idle_volatility(bundles: BundleSet) -> BundleSet:
    return _all(bundles, lambda bundle: _edit(bundle, "E0R", 0, lambda body: body.__setitem__(10, body[10] ^ 1)))


def _d_set_relation(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        offset = bundle.global_map.record_start + 5 + bundle.page_count["E0"] // 8
        return _edit(
            bundle,
            "D_DROP",
            bundle.global_map.page,
            lambda body: body.__setitem__(offset, 0),
        )

    return _all(bundles, change)


def _global_page_none(bundles: BundleSet) -> BundleSet:
    return _all(
        bundles,
        lambda bundle: _copy_page(
            bundle,
            "D_GROW_0128",
            bundle.global_map.page,
            "E0",
            bundle.global_map.page,
        ),
    )


def _duplicate_page(bundles: BundleSet, *, global_map: bool) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        source = bundle.global_map.page if global_map else bundle.tdef.page
        target = max(bundle.global_map.page, bundle.tdef.page) + 1
        result = bundle
        for checkpoint in CHECKPOINT_IDS:
            result = _copy_page(result, checkpoint, target, checkpoint, source)
        return result

    return _all(bundles, change)


def _global_record_none(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        for ordinal, checkpoint in enumerate(D_CHECKPOINTS):
            payload = bytearray([0xA5]) * PAGE_SIZE
            payload[0] = 0x80 + ordinal
            result = _replace_payload(result, checkpoint, bundle.global_map.page, bytes(payload))
        return _sync_idle(result, bundle.global_map.page)

    return _all(bundles, change)


def _global_record_multiple(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        starts = (100, 200)
        for checkpoint in D_CHECKPOINTS:
            payload = bytearray([0xFF]) * PAGE_SIZE
            for start in starts:
                payload[start : start + 5] = bytes(5)
            if checkpoint in {"D_GROW_0128", "D_REGROW_0128"}:
                payload[300] &= 0xFE
            if checkpoint == "D_REGROW_0128":
                payload[300] &= 0xFC
            result = _replace_payload(result, checkpoint, bundle.global_map.page, bytes(payload))
        return _sync_idle(result, bundle.global_map.page)

    return _all(bundles, change)


def _global_record_end(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        in_use_raw = 0 if bundle.parameters.bit_polarity == "set_means_not_in_use" else 0xFF
        for checkpoint in D_CHECKPOINTS:
            result = _edit(
                result,
                checkpoint,
                bundle.global_map.page,
                lambda body, raw=in_use_raw: body.__setitem__(PAGE_SIZE - 1, raw),
            )
        return _sync_idle(result, bundle.global_map.page)

    return _all(bundles, change)


def _tdef_page_none(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        for checkpoint in CHECKPOINT_IDS[1:]:
            result = _copy_page(result, checkpoint, bundle.tdef.page, "E0", bundle.tdef.page)
        return result

    return _all(bundles, change)


def _tdef_record_none(bundles: BundleSet) -> BundleSet:
    return _all(
        bundles,
        lambda bundle: _edit(
            bundle,
            "L_REL_0512",
            bundle.tdef.page,
            lambda body: body.__setitem__(bundle.tdef.record_end - 1, body[bundle.tdef.record_end - 1] ^ 1),
        ),
    )


def _tdef_record_multiple(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        for checkpoint in CHECKPOINT_IDS:
            digest = result.ordered_page_sha256[checkpoint][bundle.tdef.page]
            payload = bytearray(result.page_bytes(digest))
            payload[20:28] = payload[0:8]
            result = _replace_payload(result, checkpoint, bundle.tdef.page, bytes(payload))
        return result

    return _all(bundles, change)


def _structural_exclusion(bundles: BundleSet) -> BundleSet:
    return _all(
        bundles,
        lambda bundle: _edit(
            bundle,
            "D_DROP",
            bundle.tdef.page,
            lambda body: body.__setitem__(0, body[0] ^ 1),
        ),
    )


def _polarity_none(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        start = bundle.global_map.record_start
        for ordinal, checkpoint in enumerate(D_CHECKPOINTS):
            payload = bytearray([0xFF]) * PAGE_SIZE
            payload[start : start + 5] = bytes(5)
            payload[0] = 0x90 + ordinal
            result = _replace_payload(result, checkpoint, bundle.global_map.page, bytes(payload))
        return _sync_idle(result, bundle.global_map.page)

    return _all(bundles, change)


def _polarity_multiple(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        start = bundle.global_map.record_start
        values = {
            "E0": 0x01,
            "D_GROW_0128": 0x02,
            "D_DROP": 0x01,
            "D_RECREATE_EMPTY": 0x01,
            "D_REGROW_0128": 0x06,
        }
        for checkpoint in D_CHECKPOINTS:
            payload = bytearray([0xFF]) * PAGE_SIZE
            payload[start : start + 5] = bytes(5)
            payload[start + 5] = values[checkpoint]
            result = _replace_payload(result, checkpoint, bundle.global_map.page, bytes(payload))
        return _sync_idle(result, bundle.global_map.page)

    return _all(bundles, change)


def _polarity_crosscheck(bundles: BundleSet) -> BundleSet:
    return _all(
        bundles,
        lambda bundle: _copy_page(
            bundle,
            "L_REL_0512",
            bundle.global_map.page,
            "L_REL_0064",
            bundle.global_map.page,
        ),
    )


def _remove_pointer_witness(bundles: BundleSet, offset_name: str) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        removing_growth = offset_name == "growth_pointer_offset"
        result = bundle
        for checkpoint in CHECKPOINT_IDS:
            payload = bytearray([0xFF]) * PAGE_SIZE
            raw = bytearray((8, 8, 0, 0))
            if removing_growth:
                offset = PAGE_SIZE - 4
                if checkpoint == "L_DELETE_ALL":
                    raw[3] = 1
            else:
                offset = 0
                if checkpoint == "L_REL_0512":
                    raw[0] += 1
            payload[offset : offset + 4] = raw
            if checkpoint in {"L_REL_0512", "L_DELETE_ALL"}:
                payload[PAGE_SIZE // 2] ^= 1
            result = _replace_payload(
                result, checkpoint, bundle.tdef.page, bytes(payload)
            )
        return result

    return _all(bundles, change)


def _churn_precondition(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        rows = list(bundle.schedule.checkpoints)
        ordinal = CHECKPOINT_IDS.index("L_DELETE_ALL")
        counts = dict(rows[ordinal].table_row_counts)
        counts["L"] = 1
        rows[ordinal] = replace(rows[ordinal], table_row_counts=counts)
        return replace(bundle, schedule=replace(bundle.schedule, checkpoints=tuple(rows)))

    return _all(bundles, change)


def _pointer_multiple(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        for checkpoint in CHECKPOINT_IDS:
            digest = result.ordered_page_sha256[checkpoint][bundle.tdef.page]
            payload = bytearray(result.page_bytes(digest))
            for offset in (
                bundle.tdef.growth_pointer_offset,
                bundle.tdef.delete_reinsert_pointer_offset,
            ):
                original = payload[offset : offset + 4]
                payload[offset : offset + 4] = bytes((original[0], 3, 0, 0))
            result = _replace_payload(result, checkpoint, bundle.tdef.page, bytes(payload))
        return result

    return _all(bundles, change)


def _pointer_validity(bundles: BundleSet) -> BundleSet:
    validity = tuple(
        PLAN["checkpoint_design"]["transition_coverage"]["pointer_validity_checkpoints"]
    )

    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        offset = bundle.tdef.growth_pointer_offset
        for checkpoint in validity:
            result = _edit(
                result,
                checkpoint,
                bundle.tdef.page,
                lambda body, at=offset: body.__setitem__(slice(at, at + 3), b"\xff\xff\xff"),
            )
        return result

    return _all(bundles, change)


def _conversion_multiple(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        start = bundle.global_map.record_start
        for index, checkpoint in enumerate(CONVERSION_WINDOW):
            tag = int(index % 3 != 0)
            result = _edit(
                result,
                checkpoint,
                bundle.global_map.page,
                lambda body, value=tag: body.__setitem__(start, value),
            )
        return _sync_idle(result, bundle.global_map.page)

    return _all(bundles, change)


def _inline_base_zero(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        start = bundle.global_map.record_start
        for checkpoint in CONVERSION_WINDOW:
            digest = result.ordered_page_sha256[checkpoint][bundle.global_map.page]
            if result.page_bytes(digest)[start] != 0:
                continue
            result = _edit(
                result,
                checkpoint,
                bundle.global_map.page,
                lambda body: body.__setitem__(slice(start + 1, start + 5), bytes(4)),
            )
        return _sync_idle(result, bundle.global_map.page)

    return _all(bundles, change)


def _inline_suffix(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        for checkpoint in CONVERSION_WINDOW:
            result = _edit(
                result,
                checkpoint,
                bundle.global_map.page,
                lambda body: body.__setitem__(PAGE_SIZE - 1, 0x7F),
            )
        return _sync_idle(result, bundle.global_map.page)

    return _all(bundles, change)


def _inline_multiple_attempt(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        boundary = bundle.global_map.inline_boundary - 1
        for checkpoint in CONVERSION_WINDOW:
            result = _edit(
                result,
                checkpoint,
                bundle.global_map.page,
                lambda body, at=boundary: body.__setitem__(slice(at, PAGE_SIZE), bytes(PAGE_SIZE - at)),
            )
        return _sync_idle(result, bundle.global_map.page)

    return _all(bundles, change)


def _base_none(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        record = bundle.page_bytes(
            bundle.ordered_page_sha256["P_ABS_16480"][bundle.global_map.page]
        )
        reference = int.from_bytes(
            record[bundle.global_map.record_start + 1 : bundle.global_map.record_start + 5],
            "little",
        )
        return _edit(
            bundle,
            "H_REL_0064",
            reference,
            lambda body: body.__setitem__(1000, body[1000] & 0xFE),
        )

    return _all(bundles, change)


def _base_multiple(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        length = bundle.page_count["P_ABS_16480"] + 1
        start = bundle.global_map.record_start + 1
        record = bundle.page_bytes(
            bundle.ordered_page_sha256["P_ABS_16480"][bundle.global_map.page]
        )
        prior_references = tuple(
            int.from_bytes(record[start + slot * 4 : start + (slot + 1) * 4], "little")
            for slot in range(2)
        )
        for checkpoint in ("H_REL_0064", "H_REL_0896", "H_REL_0904"):
            result = _replace_index(
                result, checkpoint, tuple(result.ordered_page_sha256[checkpoint][:length])
            )
            result = _edit(
                result,
                checkpoint,
                bundle.global_map.page,
                lambda body, references=prior_references: body.__setitem__(
                    slice(start, start + 8),
                    b"".join((reference + 1).to_bytes(4, "little") for reference in references),
                ),
            )
        result = _replace_index(
            result,
            "H_IDLE_REOPEN",
            tuple(result.ordered_page_sha256["H_IDLE_REOPEN"][:length]),
        )
        result = _copy_page(
            result,
            "H_IDLE_REOPEN",
            bundle.global_map.page,
            "H_REL_0904",
            bundle.global_map.page,
        )
        return result

    return _all(bundles, change)


def _replica_disagreement(bundles: BundleSet) -> BundleSet:
    changed = list(bundles)
    bundle = changed[1]
    documents = dict(bundle.documents)
    key = f"observations/replica-{bundle.replica:02d}.json"
    observation = dict(documents[key])
    observation["campaign_id"] += "-perturbed"
    documents[key] = observation
    changed[1] = replace(bundle, documents=MappingProxyType(documents))
    return tuple(changed)


def _snapshot_reconstruction(bundles: BundleSet) -> BundleSet:
    return tuple(
        replace(bundle, checkpoint_ids=CHECKPOINT_IDS[::-1]) if bundle.replica < 3 else bundle
        for bundle in bundles
    )


def _resource_bound(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        result = bundle
        for ordinal, page in enumerate(range(6, 23)):
            grown = bytes((0x31 + ordinal,)) * PAGE_SIZE
            dropped = bytes((0x61 + ordinal,)) * PAGE_SIZE
            result = _replace_payload(result, "D_GROW_0128", page, grown)
            result = _replace_payload(result, "D_DROP", page, dropped)
        return result

    return _all(bundles, change)


def _holdout_prediction(bundles: BundleSet) -> BundleSet:
    changed = list(bundles)
    changed[2] = _remove_base_discriminator((changed[2],))[0]
    return tuple(changed)


def attempts(baseline: SyntheticParameters) -> tuple[ReachabilityAttempt, ...]:
    final = CHECKPOINT_IDS.index("H_REL_0904")
    never = replace(baseline, conversion_ordinal=None)
    zero_slots = replace(baseline, slot_activation_at_conversion=0)
    one_final_slot = replace(
        baseline, conversion_ordinal=final, slot_activation_at_conversion=1
    )
    identity: Mutation = lambda bundles: bundles
    rows = (
        ("A2-IDLE-EQUALITY", "change_page_zero_across_E0_idle_pair", baseline, _idle_volatility),
        ("A2-D-SET-RELATION", "retain_a_D_growth_bit_at_D_DROP", baseline, _d_set_relation),
        ("A2-GLOBAL-PAGE-NONE", "remove_E0_to_D_GROW_global_hash_change", baseline, _global_page_none),
        ("A2-GLOBAL-PAGE-MULTIPLE", "duplicate_global_transition_page", baseline, lambda b: _duplicate_page(b, global_map=True)),
        ("A2-GLOBAL-RECORD-NONE", "remove_all_D_decodable_record_starts", baseline, _global_record_none),
        ("A2-GLOBAL-RECORD-MULTIPLE", "encode_two_D_relation_record_starts", baseline, _global_record_multiple),
        ("A2-GLOBAL-RECORD-END", "replace_terminal_unused_slack_with_in_use_bytes", baseline, _global_record_end),
        ("A2-TDEF-PAGE-NONE", "remove_all_tdef_growth_and_churn_hash_changes", baseline, _tdef_page_none),
        ("A2-TDEF-PAGE-MULTIPLE", "duplicate_tdef_transition_page", baseline, lambda b: _duplicate_page(b, global_map=False)),
        ("A2-TDEF-RECORD-NONE", "make_tdef_record_flank_transition_variant", baseline, _tdef_record_none),
        ("A2-TDEF-RECORD-MULTIPLE", "duplicate_tdef_pointer_pair_at_second_record", baseline, _tdef_record_multiple),
        ("A2-STRUCTURAL-EXCLUSION", "add_D_transition_to_growth_pointer_window", baseline, _structural_exclusion),
        ("A2-POLARITY-NONE", "qualify_page_outside_a_D_record_with_no_growth_direction", baseline, _polarity_none),
        ("A2-POLARITY-MULTIPLE", "encode_opposed_D_growth_directions_in_one_record", baseline, _polarity_multiple),
        ("A2-POLARITY-CROSSCHECK", "remove_one_L_growth_direction_change", baseline, _polarity_crosscheck),
        ("A2-GROWTH-POINTER-NONE", "stabilize_growth_pointer_and_retain_non_pointer_page_transitions", baseline, lambda b: _remove_pointer_witness(b, "growth_pointer_offset")),
        ("A2-CHURN-PRECONDITION", "retain_one_L_row_at_full_delete_checkpoint", baseline, _churn_precondition),
        ("A2-CHURN-POINTER-NONE", "stabilize_churn_pointer_and_retain_non_pointer_page_transitions", baseline, lambda b: _remove_pointer_witness(b, "delete_reinsert_pointer_offset")),
        ("A2-POINTER-MULTIPLE", "encode_both_pointer_layouts_at_same_windows", baseline, _pointer_multiple),
        ("A2-POINTER-VALIDITY", "point_growth_window_beyond_file_at_validity_checkpoints", baseline, _pointer_validity),
        ("A2-CONVERSION-NONE", "set_conversion_parameter_to_never", never, identity),
        ("A2-CONVERSION-MULTIPLE", "alternate_inline_and_indirect_tags", baseline, _conversion_multiple),
        ("A2-SLOT-ACTIVATION", "set_zero_slots_active_at_conversion", zero_slots, identity),
        ("A2-SLOT-FINAL", "convert_at_final_checkpoint_with_one_slot", one_final_slot, identity),
        ("A2-INLINE-BOUNDARY-NONE", "replace_all_inline_base_values_with_zero", baseline, _inline_base_zero),
        ("A2-INLINE-BOUNDARY-MULTIPLE", "zero_from_the_preceding_candidate_boundary", baseline, _inline_multiple_attempt),
        ("A2-INLINE-SUFFIX", "set_nonzero_terminal_byte_across_conversion_window", baseline, _inline_suffix),
        ("A2-BASE-DISCRIMINATION", "remove_slot_zero_H_REL_0064_discriminator", baseline, _remove_base_discriminator),
        ("A2-BASE-NONE", "add_out_of_range_slot_zero_growth_bit", baseline, _base_none),
        ("A2-BASE-MULTIPLE", "remove_all_postconversion_page_count_growth", baseline, _base_multiple),
        ("A2-REPLICA-DISAGREEMENT", "change_replica_two_campaign_binding", baseline, _replica_disagreement),
        ("A2-SNAPSHOT-RECONSTRUCTION", "reverse_derivation_checkpoint_order", baseline, _snapshot_reconstruction),
        ("A2-RESOURCE-BOUND", "qualify_seventeen_global_pages", baseline, _resource_bound),
        ("A2-HOLDOUT-PREDICTION", "change_holdout_D_regrowth_only_after_freeze", baseline, _holdout_prediction),
    )
    return tuple(ReachabilityAttempt(*row) for row in rows)


def _remove_base_discriminator(bundles: BundleSet) -> BundleSet:
    def change(bundle: SyntheticBundle) -> SyntheticBundle:
        reference = bundle.schedule.checkpoint("P_ABS_12288").target_threshold_pages
        if reference is None:
            raise ValueError("plan-derived base reference is missing")
        source = bundle.ordered_page_sha256["P_ABS_16480"][reference]
        result = _replace_payload(
            bundle, "H_REL_0064", reference, bundle.page_bytes(source)
        )
        high_reference = bundle.schedule.checkpoint(
            "P_ABS_16480"
        ).target_threshold_pages
        if high_reference is None:
            raise ValueError("plan-derived high reference is missing")
        return _replace_payload(
            result,
            "H_REL_0064",
            high_reference,
            _extended_page(bundle.parameters.bit_polarity, (0,)),
        )

    return _all(bundles, change)
