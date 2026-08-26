#!/usr/bin/env python3
"""Closed, bounded parameter surface for the synthetic A4 generator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from a4_generator_pages import TAG_TDEF
from a4_generator_schedule import PROFILES, SCHEDULE
from a4_spec import BOUNDS, CHECKPOINT_IDS, PAGE_SIZE, PLAN
from protocol_validation import ValidationError


_GRAMMAR = PLAN["candidate_grammars"]
_H1 = _GRAMMAR["h1"]
_STANDARD_SIGNATURE = _H1["table_record_signature"]["signature_id"]
_DUPLICATE_SIGNATURE = _H1["pair_multiple_reachability_signature"]["signature_id"]
_DEFAULT_OFFSETS = tuple(
    interval[0] for interval in _H1["table_record_signature"]["locator_holes"]
)
_TAG05_BITS = (PAGE_SIZE - 4) * 8
_CHECKPOINTS = frozenset(CHECKPOINT_IDS)
_REPLICAS = frozenset(PROFILES)
_ROLES = frozenset(instance.role for instance in SCHEDULE.instances)


def _bounded_int(value: Any, minimum: int, maximum: int, label: str) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"A4 synthetic {label} is outside its bound")


def _closed_keys(
    value: Mapping[Any, Any], allowed: frozenset[Any], maximum: int, label: str
) -> None:
    if not isinstance(value, Mapping) or len(value) > maximum:
        raise ValidationError(f"A4 synthetic {label} exceeds its item bound")
    if any(key not in allowed for key in value):
        raise ValidationError(f"A4 synthetic {label} names an unknown key")


def _closed_members(value: frozenset[str], label: str) -> None:
    if not isinstance(value, frozenset) or len(value) > len(_CHECKPOINTS):
        raise ValidationError(f"A4 synthetic {label} exceeds its item bound")
    if any(item not in _CHECKPOINTS for item in value):
        raise ValidationError(f"A4 synthetic {label} names an unknown checkpoint")


@dataclass(frozen=True)
class SyntheticParameters:
    """Bounded byte-level choices; none is a scientific result label."""

    layout_by_replica: Mapping[int, str] = field(default_factory=dict)
    owned_ordinal_by_replica: Mapping[int, int] = field(default_factory=dict)
    signature_id: str = _STANDARD_SIGNATURE
    locator_offsets: tuple[int, ...] = _DEFAULT_OFFSETS
    type_0_polarity: str = "set_bit_owned_in_use"
    row_flag: int = 0x1000
    force_t3_conversion_checkpoint: str = "T3_ABS_08192"
    force_conversion_checkpoint_by_role: Mapping[str, str] = field(default_factory=dict)
    initial_filler_delta: int = 0
    post_reservation_filler_delta: int = 0
    omit_blob_digest: str | None = None
    user_tdef_tag: int = TAG_TDEF
    tdef_style: str = "signature"
    locator_target: str = "map"
    locator_target_page: int | None = None
    decoy_tdef_pages: Mapping[str, int] = field(default_factory=dict)
    available_equals_owned: bool = False
    conversion: str = "capacity"
    never_convert_roles: frozenset[str] = frozenset()
    type_1_exact_slots: bool = False
    type_1_slot_count: int | None = None
    omit_pages_at_or_above: int | None = None
    omit_pages_below_by_role: Mapping[str, int] = field(default_factory=dict)
    tag_05_mode_by_replica: Mapping[int, str] = field(default_factory=dict)
    tag_05_bit_shift: int = 0
    tag_05_bit_shift_by_replica: Mapping[int, int] = field(default_factory=dict)
    synthetic_boundary_bits: bool = False
    catalog_page_default: str = "catalog"
    catalog_page_override: Mapping[str, str] = field(default_factory=dict)
    catalog_page_override_by_replica: Mapping[int, Mapping[str, str]] = field(default_factory=dict)
    decoy_follows_operations: bool = False
    spare_noise_at: frozenset[str] = frozenset()
    catalog_header_noise_at: frozenset[str] = frozenset()
    skip_catalog_record_at: frozenset[str] = frozenset()
    duplicate_catalog_record_at: frozenset[str] = frozenset()
    catalog_kind_override: Mapping[str, int] = field(default_factory=dict)
    catalog_record_layout: str = "single"
    e_acute_length_override: int | None = None
    e_acute_double_occurrence: bool = False
    t2_identifier_relation_by_replica: Mapping[int, str] = field(default_factory=dict)
    holdout_name_corruption: bool = False

    def layout(self, replica: int) -> str:
        return self.layout_by_replica.get(replica, _H1["locator_layouts"][1])

    def owned_ordinal(self, replica: int) -> int:
        return self.owned_ordinal_by_replica.get(replica, 0)

    def validate(self) -> None:
        _closed_keys(self.layout_by_replica, _REPLICAS, len(_REPLICAS), "layouts")
        if any(layout not in _H1["locator_layouts"] for layout in self.layout_by_replica.values()):
            raise ValidationError("A4 synthetic locator layout is not registered")
        _closed_keys(self.owned_ordinal_by_replica, _REPLICAS, len(_REPLICAS), "owned ordinals")
        if any(type(value) is not int or value not in (0, 1) for value in self.owned_ordinal_by_replica.values()):
            raise ValidationError("A4 synthetic owned ordinal must be zero or one")
        if self.signature_id not in {_STANDARD_SIGNATURE, _DUPLICATE_SIGNATURE}:
            raise ValidationError("A4 synthetic TDEF signature is not registered")
        signature = (
            _H1["table_record_signature"]
            if self.signature_id == _STANDARD_SIGNATURE
            else _H1["pair_multiple_reachability_signature"]
        )
        required = tuple(interval[0] for interval in signature["locator_holes"])
        if self.locator_offsets != required:
            raise ValidationError("A4 synthetic locator offsets differ from the signature")
        if self.type_0_polarity not in _GRAMMAR["h2"]["type_0_polarities"]:
            raise ValidationError("A4 synthetic type-0 polarity is not registered")
        _bounded_int(self.row_flag, 0, 0xF000, "row flag")
        if self.row_flag & 0x0FFF:
            raise ValidationError("A4 synthetic row flag overlaps the offset bits")
        if self.force_t3_conversion_checkpoint not in _CHECKPOINTS:
            raise ValidationError("A4 synthetic conversion checkpoint is unknown")
        _closed_keys(self.force_conversion_checkpoint_by_role, _ROLES, len(_ROLES), "role conversions")
        if any(value not in _CHECKPOINTS for value in self.force_conversion_checkpoint_by_role.values()):
            raise ValidationError("A4 synthetic role conversion checkpoint is unknown")
        _bounded_int(self.initial_filler_delta, 0, 4096, "filler delta")
        _bounded_int(self.post_reservation_filler_delta, 0, 4096, "post-reservation filler")
        if self.omit_blob_digest is not None and (
            not isinstance(self.omit_blob_digest, str)
            or len(self.omit_blob_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.omit_blob_digest)
        ):
            raise ValidationError("A4 omitted blob identity is not canonical SHA-256")
        _bounded_int(self.user_tdef_tag, 0, 255, "TDEF tag")
        if self.tdef_style not in {"signature", "opaque", "broken_signature"}:
            raise ValidationError("A4 synthetic TDEF style is unknown")
        if self.locator_target not in {"map", "tdef"}:
            raise ValidationError("A4 synthetic locator target is unknown")
        if self.locator_target_page is not None:
            _bounded_int(
                self.locator_target_page,
                0,
                int(BOUNDS["max_final_pages_per_replica"]) - 1,
                "locator target page",
            )
        _closed_keys(self.decoy_tdef_pages, _CHECKPOINTS, len(_CHECKPOINTS), "decoy TDEF pages")
        for value in self.decoy_tdef_pages.values():
            _bounded_int(value, 0, 3, "decoy TDEF page count")
        if self.conversion not in {"capacity", "never"}:
            raise ValidationError("A4 synthetic conversion policy is unknown")
        if not isinstance(self.never_convert_roles, frozenset) or not self.never_convert_roles <= _ROLES:
            raise ValidationError("A4 synthetic never-convert roles are not closed")
        if self.type_1_slot_count is not None:
            _bounded_int(self.type_1_slot_count, 1, 64, "type-1 slot count")
        if self.omit_pages_at_or_above is not None:
            _bounded_int(
                self.omit_pages_at_or_above,
                0,
                int(BOUNDS["max_final_pages_per_replica"]),
                "visible-page limit",
            )
        _closed_keys(self.omit_pages_below_by_role, _ROLES, len(_ROLES), "role page floors")
        for value in self.omit_pages_below_by_role.values():
            _bounded_int(value, 0, int(BOUNDS["max_final_pages_per_replica"]), "role page floor")
        _closed_keys(self.tag_05_mode_by_replica, _REPLICAS, len(_REPLICAS), "tag-05 modes")
        if any(value not in {"slot", "equals_slot", "referenced"} for value in self.tag_05_mode_by_replica.values()):
            raise ValidationError("A4 synthetic tag-05 mode is unknown")
        _bounded_int(self.tag_05_bit_shift, -_TAG05_BITS, _TAG05_BITS, "tag-05 bit shift")
        _closed_keys(self.tag_05_bit_shift_by_replica, _REPLICAS, len(_REPLICAS), "replica tag-05 shifts")
        for value in self.tag_05_bit_shift_by_replica.values():
            _bounded_int(value, -_TAG05_BITS, _TAG05_BITS, "replica tag-05 bit shift")
        if self.catalog_page_default not in {"catalog", "spare"}:
            raise ValidationError("A4 synthetic catalog page is unknown")
        _closed_keys(self.catalog_page_override, _CHECKPOINTS, len(_CHECKPOINTS), "catalog overrides")
        if any(value not in {"catalog", "spare"} for value in self.catalog_page_override.values()):
            raise ValidationError("A4 synthetic catalog override is unknown")
        _closed_keys(self.catalog_page_override_by_replica, _REPLICAS, len(_REPLICAS), "replica catalog overrides")
        for overrides in self.catalog_page_override_by_replica.values():
            _closed_keys(overrides, _CHECKPOINTS, len(_CHECKPOINTS), "replica catalog override")
            if any(value not in {"catalog", "spare"} for value in overrides.values()):
                raise ValidationError("A4 synthetic replica catalog override is unknown")
        for value, label in (
            (self.spare_noise_at, "spare noise"),
            (self.catalog_header_noise_at, "catalog header noise"),
            (self.skip_catalog_record_at, "catalog record omission"),
            (self.duplicate_catalog_record_at, "catalog record duplication"),
        ):
            _closed_members(value, label)
        _closed_keys(self.catalog_kind_override, _CHECKPOINTS, len(_CHECKPOINTS), "catalog-kind overrides")
        for value in self.catalog_kind_override.values():
            _bounded_int(value, 0, 255, "catalog kind")
        if self.catalog_record_layout not in {"single", "double"}:
            raise ValidationError("A4 synthetic catalog layout is unknown")
        if self.e_acute_length_override is not None:
            _bounded_int(self.e_acute_length_override, 0, 255, "encoded name length")
        _closed_keys(self.t2_identifier_relation_by_replica, _REPLICAS, len(_REPLICAS), "identifier relations")
        if any(value not in {"same", "distinct"} for value in self.t2_identifier_relation_by_replica.values()):
            raise ValidationError("A4 synthetic identifier relation is unknown")
        for value in (
            self.available_equals_owned,
            self.type_1_exact_slots,
            self.synthetic_boundary_bits,
            self.decoy_follows_operations,
            self.e_acute_double_occurrence,
            self.holdout_name_corruption,
        ):
            if type(value) is not bool:
                raise ValidationError("A4 synthetic boolean parameter is not boolean")
