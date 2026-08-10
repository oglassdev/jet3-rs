"""Intrinsic, immutable release-evidence adapter specifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .release_evidence_model import (
    ADAPTER_ID,
    Limits,
    ResolvedFile,
    exact_keys,
    fail,
)

Adapter = Callable[
    [dict[str, Any], tuple[ResolvedFile, ...], str, Limits], dict[str, Any]
]


@dataclass(frozen=True)
class AdapterSpec:
    """Code-owned adapter meaning that checked policy cannot elevate."""

    id: str
    artifact_kind: str
    exact_verification: str
    availability: str
    implementation: Adapter | None = None


@dataclass(frozen=True)
class AdapterSelection:
    """One checked policy choice joined to its intrinsic specification."""

    spec: AdapterSpec
    status: str


def checked_adapter_spec(adapter_id: str) -> AdapterSpec | None:
    """Resolve one adapter without a mutable registry."""

    if adapter_id == "ci_g1_aggregate_v1":
        return AdapterSpec(
            adapter_id, "ci_aggregate", "independent_check", "unavailable"
        )
    if adapter_id == "dao_differential_v1":
        return AdapterSpec(
            adapter_id, "dao_bundle", "dao_differential", "unavailable"
        )
    if adapter_id == "dao_open_v1":
        return AdapterSpec(adapter_id, "dao_bundle", "dao_opened", "unavailable")
    if adapter_id == "independent_writer_v1":
        return AdapterSpec(
            adapter_id,
            "independent_writer_report",
            "independent_check",
            "unavailable",
        )
    if adapter_id in {
        "m1_descriptive_v1",
        "m3_descriptive_v1",
        "m4_descriptive_v1",
    }:
        return AdapterSpec(
            adapter_id,
            "descriptive_experiment",
            "internal_only",
            "forbidden",
        )
    if adapter_id == "structural_manifest_v1":
        return AdapterSpec(
            adapter_id, "structural_manifest", "internal_only", "unavailable"
        )
    return None


def checked_adapter_specs() -> tuple[AdapterSpec, ...]:
    """Return the complete, stable adapter inventory."""

    adapter_ids = (
        "ci_g1_aggregate_v1",
        "dao_differential_v1",
        "dao_open_v1",
        "independent_writer_v1",
        "m1_descriptive_v1",
        "m3_descriptive_v1",
        "m4_descriptive_v1",
        "structural_manifest_v1",
    )
    specs = tuple(checked_adapter_spec(adapter_id) for adapter_id in adapter_ids)
    if any(spec is None for spec in specs):
        fail("intrinsic adapter inventory is incomplete")
    return tuple(spec for spec in specs if spec is not None)


def validate_adapter_policy(value: Any) -> tuple[AdapterSelection, ...]:
    """Join checked policy status to the closed intrinsic adapter catalog."""

    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        fail("policy.adapters: expected 1..64 entries")
    selections: list[AdapterSelection] = []
    observed_ids: list[str] = []
    for index, raw in enumerate(value):
        location = f"policy.adapters[{index}]"
        entry = exact_keys(raw, {"id", "status"}, location)
        adapter_id = entry["id"]
        status = entry["status"]
        if not isinstance(adapter_id, str) or not ADAPTER_ID.fullmatch(adapter_id):
            fail(f"{location}.id: invalid adapter ID")
        if not isinstance(status, str) or status not in {
            "enabled",
            "disabled",
            "forbidden",
        }:
            fail(f"{location}.status: invalid policy status")
        spec = checked_adapter_spec(adapter_id)
        if spec is None:
            fail(f"{location}.id: unknown adapter ID")
        if spec.availability == "forbidden" and status != "forbidden":
            fail(f"{location}.status: intrinsically forbidden adapter")
        if spec.availability == "unavailable" and status != "disabled":
            fail(f"{location}.status: adapter implementation is unavailable")
        if spec.availability == "available" and status == "forbidden":
            fail(f"{location}.status: available adapter may only be enabled or disabled")
        observed_ids.append(adapter_id)
        selections.append(AdapterSelection(spec=spec, status=status))
    expected_ids = [spec.id for spec in checked_adapter_specs()]
    if observed_ids != expected_ids:
        fail("policy.adapters: expected exact sorted intrinsic adapter inventory")
    return tuple(selections)


def selected_adapter(
    selections: tuple[AdapterSelection, ...],
    adapter_id: str,
) -> AdapterSelection | None:
    """Find one selection in the bounded closed catalog."""

    return next(
        (selection for selection in selections if selection.spec.id == adapter_id),
        None,
    )
