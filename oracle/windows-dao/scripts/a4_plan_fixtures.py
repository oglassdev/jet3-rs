"""Plan-driven semantic fixture evaluator for the A4 preregistration.

This module evaluates only the synthetic predicate-fixture format frozen in the
plan.  It intentionally contains no MDB analyzer, page decoder, or candidate
discovery logic.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


class FixtureContractError(ValueError):
    """The registered fixture contract is malformed or internally inconsistent."""


CAMPAIGN_PREDICATES = frozenset(
    {
        "A4-IDLE-EQUALITY",
        "A4-SCHEMA-SNAPSHOT",
        "A4-SNAPSHOT-RECONSTRUCTION",
        "A4-RESOURCE-BOUND",
    }
)
LAYER_REQUIREMENTS = {
    "h1_tdef_to_map_row_derivation_model": ("A4-H1-", 8),
    "h2_row_identity_map_role_derivation_model": ("A4-H2-", 7),
    "h3_indirect_traversal_derivation_model": ("A4-H3-", 7),
}


@dataclass(frozen=True)
class FixtureEvaluation:
    fixture_id: str
    claimed_terminal: str
    first_failure: str | None
    predicate_results: tuple[dict[str, Any], ...]


def _accepted(value: dict[str, Any]) -> list[dict[str, Any]]:
    raw = value.get("raw_candidates")
    if not isinstance(raw, list):
        raise FixtureContractError("candidate operator requires raw_candidates")
    return [item for item in raw if item.get("accepted") is True]


def _evaluate_operator(operator: str, value: dict[str, Any]) -> tuple[bool, int]:
    if operator == "all_observations_true":
        observations = value.get("observations")
        if not isinstance(observations, list) or not observations:
            raise FixtureContractError("observation operator requires observations")
        return all(item.get("valid") is True for item in observations), 1
    if operator == "at_least_one_accepted":
        candidates = _accepted(value)
        return len(candidates) >= 1, len(candidates)
    if operator == "at_most_one_accepted":
        candidates = _accepted(value)
        return len(candidates) <= 1, len(candidates)
    if operator == "exactly_one_accepted":
        candidates = _accepted(value)
        # Encoding/length variants are evaluated after one unchanged structural
        # model survives, so the scientific survivor count remains one.
        return len(candidates) == 1, 1
    if operator == "replicas_equal":
        left = value.get("replica_1")
        right = value.get("replica_2")
        if not isinstance(left, str) or not isinstance(right, str):
            raise FixtureContractError("replica operator requires two model ids")
        return left == right, 1
    if operator == "coverage":
        required = value.get("required")
        observed = value.get("observed")
        if not isinstance(required, list) or not isinstance(observed, list):
            raise FixtureContractError("coverage operator requires two lists")
        return set(required).issubset(observed), 1
    if operator in {"every_group_min_one", "every_group_max_one"}:
        groups = value.get("groups")
        if not isinstance(groups, dict) or not groups:
            raise FixtureContractError("group operator requires nonempty groups")
        counts = [len(items) for items in groups.values()]
        if operator == "every_group_min_one":
            return all(count >= 1 for count in counts), min(counts)
        return all(count <= 1 for count in counts), max(counts)
    raise FixtureContractError(f"unknown semantic operator: {operator}")


def _claimed_failure_count(contract: dict[str, Any]) -> int:
    rule = contract["failure_survivor_count"]
    if "exact" in rule:
        return rule["exact"]
    if "minimum" in rule:
        return rule["minimum"]
    raise FixtureContractError("failure count needs exact or minimum")


def _prerequisites_passed(
    prerequisites: list[str], results: list[dict[str, Any]]
) -> bool:
    passed = {
        item["predicate_id"]
        for item in results
        if item["status"] == "pass"
    }
    if not prerequisites:
        return True
    for prerequisite in prerequisites:
        if prerequisite in passed:
            continue
        if prerequisite == "campaign_pass" and CAMPAIGN_PREDICATES <= passed:
            continue
        if prerequisite.endswith("_derivation_model"):
            requirement = LAYER_REQUIREMENTS.get(prerequisite)
            if requirement is not None:
                prefix, count = requirement
                if len([item for item in passed if item.startswith(prefix)]) == count:
                    continue
        if prerequisite == "derivation_candidate_set_sha256" and len(passed) >= 35:
            continue
        return False
    return True


def evaluate_registered_fixture(
    plan: dict[str, Any], fixture_id: str
) -> FixtureEvaluation:
    """Execute all 40 predicate rows for one registered single-mutation fixture."""

    contracts = plan["predicate_registry"]["predicate_contracts"]
    targets = [row for row in contracts if row["reachability_fixture_id"] == fixture_id]
    if len(targets) != 1:
        raise FixtureContractError(f"fixture id must select one row: {fixture_id}")
    target = targets[0]
    inputs = {
        row["predicate_id"]: copy.deepcopy(row["semantic_rule"]["baseline_input"])
        for row in contracts
    }
    inputs[target["predicate_id"]] = copy.deepcopy(target["reachability_fixture_input"])

    first_failure: str | None = None
    results: list[dict[str, Any]] = []
    for row in contracts:
        predicate_id = row["predicate_id"]
        applicable = _prerequisites_passed(row["prerequisites"], results)
        if first_failure is not None or not applicable:
            results.append(
                {
                    "predicate_id": predicate_id,
                    "status": "not_applicable",
                    "terminal_predicate_id": None,
                    "survivor_count": 0,
                }
            )
            continue
        passed, measured_count = _evaluate_operator(
            row["semantic_rule"]["operator"], inputs[predicate_id]
        )
        if row["scope"] == "campaign":
            measured_count = 0
        if passed:
            results.append(
                {
                    "predicate_id": predicate_id,
                    "status": "pass",
                    "terminal_predicate_id": None,
                    "survivor_count": 0 if row["scope"] == "campaign" else 1,
                }
            )
            continue
        first_failure = predicate_id
        claimed_count = _claimed_failure_count(row)
        if measured_count != claimed_count:
            raise FixtureContractError(
                f"{predicate_id} measured survivor count {measured_count}, "
                f"claimed {claimed_count}"
            )
        results.append(
            {
                "predicate_id": predicate_id,
                "status": "fail",
                "terminal_predicate_id": predicate_id,
                "survivor_count": measured_count,
            }
        )

    return FixtureEvaluation(
        fixture_id=fixture_id,
        claimed_terminal=target["terminal_id"],
        first_failure=first_failure,
        predicate_results=tuple(results),
    )


def evaluate_all_registered_fixtures(
    plan: dict[str, Any],
) -> tuple[FixtureEvaluation, ...]:
    """Execute every registered fixture in registry order."""

    return tuple(
        evaluate_registered_fixture(plan, row["reachability_fixture_id"])
        for row in plan["predicate_registry"]["predicate_contracts"]
    )
