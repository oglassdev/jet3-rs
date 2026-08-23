"""Synthetic dry-run case catalog with plan-derived expected outcomes.

Every case names one schedule-derived fixture (baseline, one parameter-axis
value, or one named perturbation) and the per-layer outcome the plan's R3
rules predict for it. Expectations are written from the plan and from the
generator's own schedule arithmetic, never from an implementation's output;
the dry run asserts the analyzer's produced terminal against them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterator

from a3_generator import FREE, PERTURBATIONS, SyntheticParameters, calibration_parameters, exp_0042_calibration_parameters
from a3_generator_schedule import REPLICA_PROFILES, build_schedule, e0_baseline_pages
from a3_spec import CHECKPOINT_IDS, CHECKPOINT_ORDINALS, LAYER_KEYS, PAGE_SIZE, PLAN, PREDICATES

TRANSITIONS = PLAN.document["checkpoint_design"]["transition_coverage"]
CONVERSION_WINDOW = tuple(TRANSITIONS["inline_to_indirect_conversion_window"])
CROSS_CHECK_LEGS = tuple(tuple(pair) for pair in TRANSITIONS["polarity_cross_check_legs"])
DECISIVE, NOT_APPLICABLE, HOLDOUT_FAILURE = "decisive", "not_applicable", "holdout_prediction_failure"
LEGACY_PROJECTION = "legacy_projection_complete_with_tdef_churn_not_applicable"
# Bundle-contract rejections the validator raises before any derivation runs.
VALIDATOR_REJECTIONS = {
    "missing_page_blob": "snapshot_page_blob_missing",
    "seventeen_qualified_pages": "resource_bound_breach",
}

(GLOBAL, CONVERSION, BASE, TDEF) = LAYER_KEYS


@dataclass(frozen=True)
class Expectation:
    layers: dict[str, str]
    campaign_terminal: str | None = None
    representation_change_stop: tuple[str, str] | None | str = "unspecified"
    first_violation: tuple[tuple[str, str], int] | None | str = "unspecified"
    model_fields: dict[str, dict[str, Any]] | None = None
    outcome_label: str | None = None

    def document(self) -> dict[str, Any]:
        return {
            "layers": dict(self.layers),
            "campaign_terminal": self.campaign_terminal,
            "representation_change_stop": None if self.representation_change_stop in (None, "unspecified") else list(self.representation_change_stop),
            "first_violation": None if self.first_violation in (None, "unspecified") else {"leg": list(self.first_violation[0]), "page": self.first_violation[1]},
            "model_fields": self.model_fields,
            "outcome_label": self.outcome_label,
        }


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    parameters: SyntheticParameters
    expectation: Expectation
    reaches: str | None = None
    expected_validator_rejection: str | None = None

    @property
    def perturbation(self) -> str | None:
        return self.parameters.perturbation

    def document(self) -> dict[str, Any]:
        parameters = {
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in self.parameters.__dict__.items()
        }
        return {
            "case_id": self.case_id, "category": self.category, "parameters": parameters,
            "perturbation_description": None if self.perturbation is None else PERTURBATIONS[self.perturbation],
            "expected": self.expectation.document(), "reaches": self.reaches,
            "expected_validator_rejection": self.expected_validator_rejection,
        }


def _layers(global_map: str = DECISIVE, conversion: str = DECISIVE, base: str = DECISIVE, tdef: str = DECISIVE) -> dict[str, str]:
    return {GLOBAL: global_map, CONVERSION: conversion, BASE: base, TDEF: tdef}


def _campaign(predicate: str) -> Expectation:
    return Expectation(_layers(*(NOT_APPLICABLE,) * 4), campaign_terminal=predicate)


def _global_terminal(predicate: str) -> Expectation:
    return Expectation(_layers(predicate, NOT_APPLICABLE, NOT_APPLICABLE, DECISIVE))


def _conversion_terminal(predicate: str, **kwargs: Any) -> Expectation:
    return Expectation(_layers(DECISIVE, predicate, NOT_APPLICABLE, DECISIVE), **kwargs)


def _base_terminal(predicate: str) -> Expectation:
    return Expectation(_layers(DECISIVE, DECISIVE, predicate, DECISIVE))


def _tdef_terminal(predicate: str, label: str | None = None) -> Expectation:
    return Expectation(_layers(DECISIVE, DECISIVE, DECISIVE, predicate), outcome_label=label)


def inline_boundary(parameters: SyntheticParameters, replica: int, conversion: int) -> int:
    """R3-G04's b* from this replica's own schedule walk."""
    schedule = build_schedule(
        profile=REPLICA_PROFILES[replica],
        initial_pages=parameters.e0_baseline_pages or e0_baseline_pages(parameters.anchor_fill_state),
    )
    inline = [name for name in CONVERSION_WINDOW if CHECKPOINT_ORDINALS[name] < conversion]
    largest = max(schedule.page_count(name) for name in inline)
    return parameters.global_record_start + 5 + (largest - parameters.global_record_base) // 8 + 1


def _effective_conversion(parameters: SyntheticParameters) -> int | None:
    """The first conversion-window checkpoint at or after the generated conversion ordinal."""
    conversion = parameters.effective_conversion(1)
    if conversion is None:
        return None
    return next((CHECKPOINT_ORDINALS[name] for name in CONVERSION_WINDOW if CHECKPOINT_ORDINALS[name] >= conversion), None)


def conversion_expectation(parameters: SyntheticParameters, *, stop: tuple[str, str] | None | str = "unspecified") -> Expectation:
    """Apply R3-G02/G04/G09 to the generator's intended per-checkpoint classes."""
    conversion = _effective_conversion(parameters)
    capacity = (PAGE_SIZE - parameters.global_record_start - 5) * 8
    schedule = build_schedule(profile=REPLICA_PROFILES[1], initial_pages=parameters.e0_baseline_pages or e0_baseline_pages(parameters.anchor_fill_state))
    raw_conversion = parameters.effective_conversion(1)
    base, e0, regrow = parameters.global_record_base, schedule.page_count("E0"), schedule.page_count("D_REGROW_0128")
    # The global page only qualifies if its bytes change on D growth and again on D drop.
    if (raw_conversion is not None and raw_conversion <= CHECKPOINT_ORDINALS["D_GROW_0128"]) or base + capacity <= e0 or base > regrow:
        return _global_terminal("A3-GLOBAL-PAGE-NONE")
    anchors_inline = raw_conversion is None or raw_conversion > CHECKPOINT_ORDINALS["D_REGROW_0128"]
    if base > e0 or capacity <= regrow - base:
        anchors_inline = False
    if not anchors_inline:
        return _global_terminal("A3-GLOBAL-RECORD-NONE")
    classes = []
    for name in CONVERSION_WINDOW:
        ordinal = CHECKPOINT_ORDINALS[name]
        if conversion is not None and ordinal >= conversion:
            classes.append("indirect")
        elif schedule.page_count(name) - parameters.global_record_base < capacity:
            classes.append("inline")
        else:
            classes.append("neither")
    indirect = [index for index, kind in enumerate(classes) if kind == "indirect"]
    if not indirect or "inline" not in classes[: indirect[0]]:
        return _conversion_terminal("A3-CONVERSION-NONE", representation_change_stop=stop)
    if sum(left != right for left, right in zip(classes, classes[1:])) != 1:
        return _conversion_terminal("A3-CONVERSION-MULTIPLE", representation_change_stop=stop)
    assert conversion is not None
    boundaries = {replica: inline_boundary(parameters, replica, conversion) for replica in (1, 2, 3)}
    model = {"conversion_ordinal": conversion, "conversion_checkpoint_id": CHECKPOINT_IDS[conversion], "inline_boundary": boundaries[1]}
    if boundaries[1] != boundaries[2]:
        return _conversion_terminal("A3-REPLICA-DISAGREEMENT", representation_change_stop=stop)
    conversion_outcome = DECISIVE if boundaries[3] == boundaries[1] else HOLDOUT_FAILURE
    return Expectation(
        _layers(DECISIVE, conversion_outcome, DECISIVE, DECISIVE), representation_change_stop=stop,
        model_fields={CONVERSION: model}, first_violation=None,
    )


def baseline_case() -> Case:
    expectation = replace(
        conversion_expectation(calibration_parameters(), stop=("P_ABS_12288", "P_ABS_16480")),
        outcome_label="all_layers_decisive",
    )
    return Case("baseline", "baseline", calibration_parameters(), expectation)


def iter_axis_cases() -> Iterator[Case]:
    baseline = calibration_parameters()
    for ordinal in (*range(1, len(CHECKPOINT_IDS)), None):
        parameters = replace(baseline, conversion_ordinal=ordinal)
        yield Case(
            f"conversion_{'never' if ordinal is None else ordinal}", "axis:conversion_ordinal", parameters,
            conversion_expectation(parameters), reaches="A3-CONVERSION-NONE" if ordinal is None else None,
        )
    for count in FREE["slot_activation_at_conversion"]:
        parameters = replace(baseline, slot_activation_at_conversion=count)
        expectation = _conversion_terminal("A3-SLOT-ACTIVATION") if count == 0 else conversion_expectation(parameters)
        if count:
            expectation = replace(expectation, model_fields={CONVERSION: {**expectation.model_fields[CONVERSION], "active_slot_count_at_conversion": count}})
        yield Case(f"slots_{count}", "axis:slot_activation_at_conversion", parameters, expectation, reaches="A3-SLOT-ACTIVATION" if count == 0 else None)
    for polarity in FREE["bit_polarity"]:
        parameters = replace(baseline, bit_polarity=polarity)
        expectation = conversion_expectation(parameters)
        expectation = replace(expectation, model_fields={**expectation.model_fields, GLOBAL: {"bit_polarity": polarity}})
        yield Case(f"polarity_{polarity}", "axis:bit_polarity", parameters, expectation)
    for fill in FREE["anchor_fill_state"]:
        parameters = replace(baseline, anchor_fill_state=fill)
        yield Case(f"fill_{fill}", "axis:anchor_fill_state", parameters, conversion_expectation(parameters))
    for slack in FREE["record_end_uniform_slack_bytes"]:
        parameters = replace(baseline, record_end_uniform_slack_bytes=slack)
        expectation = conversion_expectation(parameters)
        expectation = replace(expectation, model_fields={**expectation.model_fields, GLOBAL: {"zero_suffix_slack_bytes": slack}})
        yield Case(f"slack_{slack}", "axis:record_end_uniform_slack_bytes", parameters, expectation)
    for start in FREE["global_record_start"]:
        parameters = replace(baseline, global_record_start=start)
        yield Case(f"start_{start}", "axis:global_record_start", parameters, conversion_expectation(parameters))
    for base in FREE["global_record_base"]:
        parameters = replace(baseline, global_record_base=base)
        yield Case(f"base_{base}", "axis:global_record_base", parameters, conversion_expectation(parameters))
    for tag in FREE["inline_tag_at_anchor"]:
        parameters = replace(baseline, inline_tag_at_anchor=tag)
        expectation = _global_terminal("A3-GLOBAL-RECORD-NONE") if tag else conversion_expectation(parameters)
        yield Case(f"anchor_tag_{tag}", "axis:inline_tag_at_anchor", parameters, expectation)
    for leg in (*CROSS_CHECK_LEGS, None):
        parameters = replace(baseline, conversion_ordinal=None, first_representation_change_leg=leg)
        label = "never" if leg is None else f"{leg[0]}_{leg[1]}"
        yield Case(f"representation_{label}", "axis:first_representation_change_leg", parameters, conversion_expectation(parameters, stop=leg))
    calibration = exp_0042_calibration_parameters()
    yield Case("exp_0042_calibration", "calibration", calibration, conversion_expectation(calibration))


def _cross_check_page(parameters: SyntheticParameters, leg_index: int, offset: int) -> int:
    schedule = build_schedule(profile=REPLICA_PROFILES[1], initial_pages=e0_baseline_pages(parameters.anchor_fill_state))
    return schedule.page_count(CROSS_CHECK_LEGS[leg_index][0]) + offset


def iter_perturbation_cases() -> Iterator[Case]:
    baseline = calibration_parameters()
    table: list[tuple[str, Expectation, str | None]] = [
        ("idle_pair_volatile", _campaign("A3-IDLE-EQUALITY"), "A3-IDLE-EQUALITY"),
        ("missing_page_blob", _campaign("A3-SNAPSHOT-RECONSTRUCTION"), "A3-SNAPSHOT-RECONSTRUCTION"),
        ("seventeen_qualified_pages", _campaign("A3-RESOURCE-BOUND"), "A3-RESOURCE-BOUND"),
        ("sixteen_qualified_pages", replace(conversion_expectation(baseline), outcome_label="all_layers_decisive"), None),
        ("global_page_static_on_drop", _global_terminal("A3-GLOBAL-PAGE-NONE"), "A3-GLOBAL-PAGE-NONE"),
        ("anchor_highwater_hole", _global_terminal("A3-GLOBAL-RECORD-NONE"), "A3-GLOBAL-RECORD-NONE"),
        ("anchor_sentinel_in_use", _global_terminal("A3-GLOBAL-RECORD-NONE"), None),
        ("truncated_base_field", _global_terminal("A3-GLOBAL-RECORD-NONE"), None),
        ("d_drop_keeps_growth_pages", _global_terminal("A3-D-SET-RELATION"), "A3-D-SET-RELATION"),
        ("record_end_not_uniform", _global_terminal("A3-GLOBAL-RECORD-END"), "A3-GLOBAL-RECORD-END"),
        ("duplicate_global_start", _global_terminal("A3-GLOBAL-RECORD-MULTIPLE"), "A3-GLOBAL-RECORD-MULTIPLE"),
        ("second_global_page_same_polarity", _global_terminal("A3-GLOBAL-PAGE-MULTIPLE"), "A3-GLOBAL-PAGE-MULTIPLE"),
        ("second_global_page_opposite_polarity", _global_terminal("A3-POLARITY-MULTIPLE"), "A3-POLARITY-MULTIPLE"),
        ("cross_check_first_page_violation", _conversion_terminal("A3-POLARITY-CROSSCHECK", first_violation=(CROSS_CHECK_LEGS[2], _cross_check_page(baseline, 2, 0)), representation_change_stop=None), "A3-POLARITY-CROSSCHECK"),
        ("cross_check_later_page_violation", _conversion_terminal("A3-POLARITY-CROSSCHECK", first_violation=(CROSS_CHECK_LEGS[9], _cross_check_page(baseline, 9, 3)), representation_change_stop=None), None),
        ("conversion_reverts", _conversion_terminal("A3-CONVERSION-MULTIPLE"), "A3-CONVERSION-MULTIPLE"),
        ("slot_one_never_activates", _conversion_terminal("A3-SLOT-FINAL"), "A3-SLOT-FINAL"),
        ("inline_suffix_byte", _conversion_terminal("A3-INLINE-SUFFIX"), "A3-INLINE-SUFFIX"),
        ("slot_reference_not_0x05", _conversion_terminal("A3-POINTER-VALIDITY"), "A3-POINTER-VALIDITY"),
        ("no_slot0_flip", replace(_base_terminal("A3-BASE-DISCRIMINATION"), outcome_label="partial_layer_outcome"), "A3-BASE-DISCRIMINATION"),
        ("extended_self_bit_clear", _base_terminal("A3-BASE-NONE"), "A3-BASE-NONE"),
        ("extended_off_by_one_ambiguous", _base_terminal("A3-BASE-MULTIPLE"), "A3-BASE-MULTIPLE"),
        ("replica_two_converts_earlier", _conversion_terminal("A3-REPLICA-DISAGREEMENT"), "A3-REPLICA-DISAGREEMENT"),
        ("holdout_converts_earlier", Expectation(_layers(DECISIVE, HOLDOUT_FAILURE, DECISIVE, DECISIVE)), None),
        # A3-HOLDOUT-PREDICTION is terminal only when no layer is decisive (R3 projection).
        ("holdout_contradicts_every_layer", Expectation(_layers(*(HOLDOUT_FAILURE,) * 4)), "A3-HOLDOUT-PREDICTION"),
        ("tdef_churn_pointer_static", _tdef_terminal("A3-TDEF-PAGE-NONE"), "A3-TDEF-PAGE-NONE"),
        ("second_tdef_page", _tdef_terminal("A3-TDEF-PAGE-MULTIPLE"), "A3-TDEF-PAGE-MULTIPLE"),
        ("tdef_record_gap_changes", _tdef_terminal("A3-TDEF-RECORD-NONE"), "A3-TDEF-RECORD-NONE"),
        ("second_churn_window", _tdef_terminal("A3-TDEF-RECORD-MULTIPLE"), "A3-TDEF-RECORD-MULTIPLE"),
        ("churn_changes_two_bytes", _tdef_terminal("A3-POINTER-MULTIPLE"), "A3-POINTER-MULTIPLE"),
        ("growth_pointer_changes_on_delete", _tdef_terminal("A3-GROWTH-POINTER-NONE"), "A3-GROWTH-POINTER-NONE"),
        ("churn_pointer_no_return", _tdef_terminal("A3-CHURN-POINTER-NONE"), "A3-CHURN-POINTER-NONE"),
        ("growth_target_not_0x05", _tdef_terminal("A3-POINTER-VALIDITY"), None),
        ("churn_pointer_changes_on_p_growth", _tdef_terminal("A3-STRUCTURAL-EXCLUSION"), "A3-STRUCTURAL-EXCLUSION"),
        ("delete_reread_nonzero", _tdef_terminal("A3-CHURN-PRECONDITION"), "A3-CHURN-PRECONDITION"),
        ("legacy_alternating_delete", _tdef_terminal("A3-CHURN-PRECONDITION", LEGACY_PROJECTION), None),
    ]
    for name, expectation, reaches in table:
        yield Case(
            name, "perturbation", replace(baseline, perturbation=name), expectation, reaches=reaches,
            expected_validator_rejection=VALIDATOR_REJECTIONS.get(name),
        )


def all_cases() -> list[Case]:
    cases = [baseline_case(), *iter_axis_cases(), *iter_perturbation_cases()]
    identifiers = [case.case_id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate A3 dry-run case id")
    unknown = {name for name in PERTURBATIONS} - {case.perturbation for case in cases if case.perturbation}
    if unknown:
        raise ValueError(f"perturbations without a case: {sorted(unknown)}")
    return cases


def reachability_targets(cases: list[Case]) -> dict[str, Case]:
    """The single designated fixture for every predicate id that has one."""
    targets: dict[str, Case] = {}
    for case in cases:
        if case.reaches is not None:
            if case.reaches in targets:
                raise ValueError(f"{case.reaches} has two designated fixtures")
            targets[case.reaches] = case
    return targets


def outcome_labels(expectation: Expectation, produced_reasons: list[str], layer_statuses: dict[str, str]) -> list[str]:
    """Outcome names for predicted_terminal_states, from what the analyzer produced."""
    labels = list(produced_reasons)
    decisive = [name for name, status in layer_statuses.items() if status == "decisive_predicts_holdout"]
    if len(decisive) == len(LAYER_KEYS):
        labels.append("all_layers_decisive")
    elif decisive:
        labels.append("partial_layer_outcome")
    if expectation.outcome_label == LEGACY_PROJECTION and "legacy_churn_precondition_not_met" in produced_reasons and len(decisive) == 3:
        labels.append(LEGACY_PROJECTION)
    return labels


def expected_reason(predicate: str) -> str:
    return PREDICATES[predicate][0]
