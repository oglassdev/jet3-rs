"""Focused tests for the separately preregistered M5 set-reference successor."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from m5s1_spec import (  # noqa: E402
    ANALYZED_BYTES,
    CHECKED_PLAN,
    CONDITION_COUNT,
    PLAN_SHA256,
    REPLICAS,
    compile_checked_plan,
    load_checked_plan,
)
from protocol_validation import ValidationError  # noqa: E402


class M5SuccessorPlanContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(CHECKED_PLAN.read_text(encoding="utf-8"))

    def test_exact_plan_derives_complete_factorial_and_rotated_schedule(self) -> None:
        checked = load_checked_plan()
        self.assertEqual(len(checked.condition_ids), CONDITION_COUNT)
        self.assertEqual(len(set(checked.condition_ids)), CONDITION_COUNT)
        self.assertEqual(len(checked.schedule), REPLICAS)
        self.assertEqual(checked.schedule[0], checked.condition_ids)
        self.assertEqual(
            checked.schedule[1], checked.condition_ids[12:] + checked.condition_ids[:12]
        )
        self.assertEqual(
            checked.schedule[2], checked.condition_ids[24:] + checked.condition_ids[:24]
        )
        self.assertEqual(
            set(checked.matched_m4_conditions),
            {"V20-U", "V20-E", "V30-U", "V30-E", "V40-U", "V40-E"},
        )

    def test_plan_is_bound_to_exact_preregistered_bytes(self) -> None:
        self.assertEqual(len(PLAN_SHA256), 64)
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "plan.json"
            changed.write_bytes(CHECKED_PLAN.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValidationError, "bytes differ"):
                load_checked_plan(changed)

    def test_reference_semantics_preserve_every_unstable_m4_value(self) -> None:
        checked = compile_checked_plan(self.document)
        semantics = checked.document["reference_semantics"]
        self.assertEqual(semantics["m4_observations_per_reference_unit"], 12)
        self.assertFalse(semantics["representative_value_selection_allowed"])
        self.assertFalse(semantics["unstable_offsets_may_be_deleted"])
        self.assertFalse(
            checked.document["analysis"]["offset_1264_special_casing_allowed"]
        )
        self.assertEqual(
            checked.document["analysis"]["primary_membership_evaluations"],
            CONDITION_COUNT * REPLICAS * ANALYZED_BYTES,
        )

    def test_new_acquisition_and_fail_closed_execution_gate_are_mandatory(self) -> None:
        changed = copy.deepcopy(self.document)
        changed["preregistration"]["prior_m5_acquisition_may_be_reused"] = True
        with self.assertRaisesRegex(ValidationError, "prior_m5_acquisition"):
            compile_checked_plan(changed)

        changed = copy.deepcopy(self.document)
        changed["execution_gate"]["status"] = "READY"
        with self.assertRaisesRegex(ValidationError, "execution_gate.status"):
            compile_checked_plan(changed)

    def test_factor_or_count_changes_are_rejected(self) -> None:
        changed = copy.deepcopy(self.document)
        changed["acquisition_design"]["destination_version_pairs"].append("40-20")
        with self.assertRaisesRegex(ValidationError, "destination_version_pairs"):
            compile_checked_plan(changed)

        changed = copy.deepcopy(self.document)
        changed["analysis"]["primary_membership_evaluations"] -= 1
        with self.assertRaisesRegex(ValidationError, "primary_membership_evaluations"):
            compile_checked_plan(changed)

    def test_claim_boundary_forbids_format_and_compatibility_claims(self) -> None:
        for key in (
            "format_field_identification",
            "rust_compatibility",
            "mdb_read_write_or_conversion_support",
        ):
            changed = copy.deepcopy(self.document)
            changed["claims"][key] = True
            with self.subTest(key=key), self.assertRaisesRegex(
                ValidationError, "claims"
            ):
                compile_checked_plan(changed)


if __name__ == "__main__":
    unittest.main()
