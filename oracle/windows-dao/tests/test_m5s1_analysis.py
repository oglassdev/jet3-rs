"""Focused tests for bounded M5 successor set-reference analysis."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from m5s1_analysis import (  # noqa: E402
    M4_CONDITIONS,
    CompactPrefixObservation,
    M4PrefixObservation,
    build_analysis,
)
from m5s1_spec import ANALYZED_BYTES, PREFIX_BYTES, load_checked_plan  # noqa: E402
from protocol_validation import ValidationError  # noqa: E402


class M5SuccessorAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = load_checked_plan()

    def m4(self) -> list[M4PrefixObservation]:
        return [
            M4PrefixObservation(condition, replica, phase, bytes(PREFIX_BYTES))
            for condition in M4_CONDITIONS
            for replica in range(1, 7)
            for phase in ("creator", "reopen")
        ]

    def compact(self) -> list[CompactPrefixObservation]:
        return [
            CompactPrefixObservation(condition, replica, bytes(PREFIX_BYTES))
            for condition in self.plan.condition_ids
            for replica in range(1, 4)
        ]

    @staticmethod
    def replace_byte(prefix: bytes, offset: int, value: int) -> bytes:
        changed = bytearray(prefix)
        changed[offset] = value
        return bytes(changed)

    def test_all_memberships_inside_singleton_references_pass(self) -> None:
        report = build_analysis(self.plan, self.m4(), self.compact())
        self.assertEqual(
            report["scientific_outcome"],
            "reference_sets_contain_all_compact_observations",
        )
        self.assertEqual(report["novel_value_condition_offsets"], [])
        self.assertEqual(report["novel_value_occurrence_count"], 0)
        self.assertEqual(
            report["reference_set_cardinality_histogram"],
            {"1": 36 * ANALYZED_BYTES},
        )

    def test_unstable_m4_offset_accepts_every_observed_value(self) -> None:
        m4 = self.m4()
        row = m4.index(
            next(
                item
                for item in m4
                if item.condition_id == "V20-E"
                and item.replica == 6
                and item.phase == "reopen"
            )
        )
        m4[row] = M4PrefixObservation(
            "V20-E",
            6,
            "reopen",
            self.replace_byte(m4[row].prefix, 1264, 1),
        )
        compact = self.compact()
        target = next(
            condition
            for condition, matched in zip(
                self.plan.condition_ids,
                self.plan.matched_m4_conditions,
                strict=True,
            )
            if matched == "V20-E"
        )
        row = compact.index(
            next(
                item
                for item in compact
                if item.condition_id == target and item.replica == 1
            )
        )
        compact[row] = CompactPrefixObservation(
            target, 1, self.replace_byte(compact[row].prefix, 1264, 1)
        )
        report = build_analysis(self.plan, m4, compact)
        self.assertEqual(
            report["scientific_outcome"],
            "reference_sets_contain_all_compact_observations",
        )
        self.assertGreater(report["reference_set_cardinality_histogram"]["2"], 0)

    def test_novel_value_produces_exact_extension_record(self) -> None:
        compact = self.compact()
        first = compact[0]
        compact[0] = CompactPrefixObservation(
            first.condition_id, 1, self.replace_byte(first.prefix, 10, 2)
        )
        report = build_analysis(self.plan, self.m4(), compact)
        self.assertEqual(
            report["scientific_outcome"],
            "compact_observations_extend_reference_sets",
        )
        self.assertEqual(report["novel_value_occurrence_count"], 1)
        self.assertEqual(
            report["novel_value_condition_offsets"],
            [
                {
                    "condition_id": first.condition_id,
                    "absolute_offset": 10,
                    "occurrences": [{"replica": 1, "value": 2}],
                }
            ],
        )

    def test_excluded_region_never_enters_analysis(self) -> None:
        compact = self.compact()
        first = compact[0]
        compact[0] = CompactPrefixObservation(
            first.condition_id, 1, self.replace_byte(first.prefix, 1600, 255)
        )
        report = build_analysis(self.plan, self.m4(), compact)
        self.assertEqual(
            report["scientific_outcome"],
            "reference_sets_contain_all_compact_observations",
        )

    def test_missing_duplicate_and_oversized_inputs_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValidationError, "incomplete"):
            build_analysis(self.plan, self.m4()[:-1], self.compact())

        compact = self.compact()
        compact[-1] = compact[0]
        with self.assertRaisesRegex(ValidationError, "duplicate"):
            build_analysis(self.plan, self.m4(), compact)

        m4 = self.m4()
        first = m4[0]
        m4[0] = M4PrefixObservation(
            first.condition_id, first.replica, first.phase, bytes(PREFIX_BYTES - 1)
        )
        with self.assertRaisesRegex(ValidationError, "2048-byte"):
            build_analysis(self.plan, m4, self.compact())

    def test_input_order_does_not_change_the_report(self) -> None:
        forward = build_analysis(self.plan, self.m4(), self.compact())
        reverse = build_analysis(
            self.plan, reversed(self.m4()), reversed(self.compact())
        )
        self.assertEqual(forward, reverse)


if __name__ == "__main__":
    unittest.main()
