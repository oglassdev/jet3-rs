"""Generator invariants: plan-derived schedules, independent overshoot, byte-level axes."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))

from a3_dryrun_cases import inline_boundary  # noqa: E402
from a3_generator import GLOBAL_PAGE, calibration_parameters, exp_0042_calibration_parameters, generate_replica, generate_replicas  # noqa: E402
from a3_generator_schedule import REPLICA_PROFILES, build_schedule  # noqa: E402


class A3GeneratorTests(unittest.TestCase):
    def test_schedule_is_plan_ordered_with_strictly_larger_regrowth(self) -> None:
        schedule = build_schedule()
        self.assertLess(schedule.page_count("D_GROW_0128"), schedule.page_count("D_REGROW_0128"))
        self.assertEqual([row.ordinal for row in schedule.checkpoints], list(range(25)))

    def test_holdout_overshoot_differs_from_both_derivation_walks(self) -> None:
        replicas = generate_replicas(calibration_parameters())
        overshoot = [tuple(r.schedule.checkpoint(n).target_overshoot_pages for n in ("D_GROW_0128", "L_REL_1280", "P_ABS_16480", "H_REL_0904")) for r in replicas]
        self.assertNotEqual(overshoot[2], overshoot[0])
        self.assertNotEqual(overshoot[2], overshoot[1])
        self.assertNotEqual(replicas[2].ordered_page_sha256["H_REL_0904"], replicas[0].ordered_page_sha256["H_REL_0904"])

    def test_anchor_fill_changes_bytes_but_not_inline_boundary(self) -> None:
        base = calibration_parameters()
        pages = {}
        for fill in ("empty", "partial", "full"):
            parameters = replace(base, anchor_fill_state=fill)
            replica = generate_replica(parameters, 1)
            pages[fill] = replica.payloads[replica.ordered_page_sha256["E0"][GLOBAL_PAGE]]
            self.assertEqual(inline_boundary(parameters, 1, 20), inline_boundary(base, 1, 20))
        self.assertEqual(len({bytes(p) for p in pages.values()}), 3)

    def test_exp0042_calibration_prefix_is_generated_from_parameters(self) -> None:
        parameters = exp_0042_calibration_parameters()
        replica = generate_replica(parameters, 1)
        page = replica.payloads[replica.ordered_page_sha256["P_ABS_16480"][GLOBAL_PAGE]]
        self.assertEqual(page[1915:1924].hex(), "01003a0000e03f0000")
        self.assertEqual([replica.page_count[n] for n in ("E0", "D_GROW_0128", "D_REGROW_0128")], [29, 157, 285])

    def test_profiles_are_distinct(self) -> None:
        self.assertEqual(len({profile.extra_page_period for profile in REPLICA_PROFILES.values()}), 3)


if __name__ == "__main__":
    unittest.main()
