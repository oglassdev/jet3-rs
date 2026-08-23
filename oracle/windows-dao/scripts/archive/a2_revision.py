"""Checked additive reachability revision for the A2 dry-run contract."""

from __future__ import annotations

import hashlib
import json

from a2_model import PLAN, PLAN_PATH, PLAN_SHA256, PREDICATES

REVISION_PATH = PLAN_PATH.with_name("a2-allocation-maps-r2.plan.json")
REVISION_BYTES = REVISION_PATH.read_bytes()
REVISION_SHA256 = hashlib.sha256(REVISION_BYTES).hexdigest()
EXPECTED_REVISION_SHA256 = "977d352b6b7c042cf4d0f0cab793086842b3ad2b7da13b9c217020f00c5193c4"
if REVISION_SHA256 != EXPECTED_REVISION_SHA256:
    raise RuntimeError("A2 analyzer dry-run revision hash does not match the preregistration")
REVISION = json.loads(REVISION_BYTES)
if (
    REVISION["preregistration"]["revision_of"] != PLAN["experiment_id"]
    or REVISION["preregistration"]["original_plan"]["sha256"] != PLAN_SHA256
):
    raise RuntimeError("A2 analyzer dry-run revision does not bind the checked original plan")
UNREACHABLE_BY_CONSTRUCTION = {
    row["predicate_id"]: row
    for row in REVISION["analyzer_dry_run_reconciliation"][
        "unreachable_by_construction"
    ]
}
if not UNREACHABLE_BY_CONSTRUCTION.keys() <= PREDICATES.keys():
    raise RuntimeError("A2 analyzer dry-run revision names an unregistered predicate")
REQUIRED_REACHABLE_PREDICATE_IDS = tuple(
    predicate_id
    for predicate_id in PLAN["predicate_registry"]["ids"]
    if predicate_id not in UNREACHABLE_BY_CONSTRUCTION
)
_EXCLUDED_REASONS = {
    PREDICATES[predicate_id][0] for predicate_id in UNREACHABLE_BY_CONSTRUCTION
}
EFFECTIVE_REQUIRED_CASES = tuple(
    reason
    for reason in PLAN["analyzer_dry_run_contract"]["synthetic_input"]["required_cases"]
    if reason not in _EXCLUDED_REASONS
)
