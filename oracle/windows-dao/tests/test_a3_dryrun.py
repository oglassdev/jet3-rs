"""Retained replay and bounded synthetic reachability contracts for A3."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a3_dryrun import DEFAULT_RETAINED_ROOT, registry_reachability, run_replay  # noqa: E402
from a3_generator import FREE, iter_parameter_cases  # noqa: E402
from a3_spec import PREDICATE_IDS  # noqa: E402


class A3DryRunTests(unittest.TestCase):
    @unittest.skipUnless(DEFAULT_RETAINED_ROOT.is_dir(), "EXP-0042 design-input bundle is absent")
    def test_exp0042_replay_is_derivation_only_and_exact(self) -> None:
        result = run_replay(DEFAULT_RETAINED_ROOT)
        self.assertEqual(result.model.record.document(), {"page": 1, "start": 1915, "end": 2048})
        self.assertEqual(result.model.bit_polarity, "set_means_not_in_use")
        self.assertEqual(result.model.zero_suffix_slack_bytes, 92)
        self.assertEqual(result.legacy_start_count, 1935)
        self.assertEqual(result.transcript.first_violating_page, 1021)
        self.assertEqual(len(result.transcript.evaluated_legs), 3)
        self.assertIsNone(result.transcript.representation_change_stop)
        self.assertEqual(result.layer_outcomes["tdef_pointer_pair"], "no_tdef_record_candidate")
        self.assertTrue(result.t3_rejected)
        self.assertTrue(result.t5_rejected)

    def test_each_registered_predicate_has_one_reachability_case(self) -> None:
        rows = registry_reachability()
        self.assertEqual([row["predicate_id"] for row in rows], list(PREDICATE_IDS))
        self.assertEqual({row["status"] for row in rows}, {"reached"})
        self.assertEqual(len({row["perturbation"] for row in rows}), len(PREDICATE_IDS))

    def test_parameter_enumerator_covers_all_axes(self) -> None:
        cases = list(iter_parameter_cases())
        parameters = [row[1] for row in cases]
        self.assertEqual({row.conversion_ordinal for row in parameters}, set(range(1, 25)) | {None})
        self.assertEqual({row.slot_activation_at_conversion for row in parameters}, set(FREE["slot_activation_at_conversion"]))
        self.assertEqual({row.bit_polarity for row in parameters}, set(FREE["bit_polarity"]))
        self.assertEqual({row.anchor_fill_state for row in parameters}, set(FREE["anchor_fill_state"]))
        self.assertEqual({row.record_end_uniform_slack_bytes for row in parameters}, set(FREE["record_end_uniform_slack_bytes"]))
        self.assertEqual({row.global_record_start for row in parameters}, set(FREE["global_record_start"]))
        self.assertEqual({row.global_record_base for row in parameters}, set(FREE["global_record_base"]))
        self.assertEqual({row.inline_tag_at_anchor for row in parameters}, set(FREE["inline_tag_at_anchor"]))


if __name__ == "__main__":
    unittest.main()
