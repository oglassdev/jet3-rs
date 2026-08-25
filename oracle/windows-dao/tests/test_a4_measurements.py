"""Focused tests for measured A4 primitive predicate boundaries."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))

from a4_analysis_input import check_analysis_input  # noqa: E402
from a4_generator import SyntheticParameters  # noqa: E402
from a4_layer_h1 import H1Terminal, agree_h1_replicas, derive_h1_replica  # noqa: E402
from a4_layer_h2 import (  # noqa: E402
    agree_h2_replicas,
    decode_frozen_owned_rows,
    derive_h2_replica,
)
from a4_layer_h3 import agree_h3, derive_h3  # noqa: E402
from a4_layer_h4 import derive_catalog_root  # noqa: E402
from a4_layers import catalog_root_observations, h3_observations  # noqa: E402
from a4_measurements import MeasurementRecorder  # noqa: E402
from a4_model import WorkLedger  # noqa: E402
from a4_spec import PLAN, PREDICATE_CONTRACTS  # noqa: E402
from protocol_validation import ValidationError  # noqa: E402
from test_a4_analyzer import _COMMIT, _inputs  # noqa: E402


class A4MeasurementTests(unittest.TestCase):
    def test_recorder_derives_metadata_and_rejects_invalid_accounting(self) -> None:
        recorder = MeasurementRecorder()
        row = recorder.record(
            "A4-H1-LOCATOR-LAYOUT-NONE", 2, True, replica=1
        )
        contract = PREDICATE_CONTRACTS[row.predicate_id]
        self.assertEqual(row.order, contract["order"])
        self.assertEqual(row.scope, contract["scope"])
        self.assertEqual(row.counted_set_kind, contract["counted_set_kind"])
        self.assertEqual(row.document()["predicate_measured_survivor_count"], 2)
        with self.assertRaisesRegex(ValidationError, "duplicated"):
            recorder.record(row.predicate_id, 2, True, replica=1)
        with self.assertRaisesRegex(ValidationError, "unregistered"):
            MeasurementRecorder().record("A4-H9-INVENTED", 1, True)
        with self.assertRaises(ValidationError):
            MeasurementRecorder().record("A4-H1-TDEF-NONE", 1, False, replica=1)

    def test_successful_primitives_retain_intermediate_cardinalities(self) -> None:
        inputs = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
        recorder = MeasurementRecorder()
        work = WorkLedger()
        h1 = {
            replica: derive_h1_replica(
                inputs.views[replica],
                inputs.qualified_tdef_pages[replica],
                work,
                recorder,
            )
            for replica in (1, 2)
        }
        frozen_h1 = agree_h1_replicas(h1[1], h1[2], recorder)
        h2 = {
            replica: derive_h2_replica(
                inputs.views[replica],
                h1[replica],
                inputs.replicas[replica].table_row_counts,
                work,
                recorder,
            )
            for replica in (1, 2)
        }
        frozen_h2 = agree_h2_replicas(h2[1], h2[2], recorder)
        h3 = {}
        for replica in (1, 2):
            frozen_rows = decode_frozen_owned_rows(
                inputs.views[replica], h1[replica], frozen_h2, work
            )
            observations = h3_observations(inputs.views[replica], frozen_rows, work)
            h3[replica] = derive_h3(
                replica,
                observations,
                inputs.replicas[replica].source.page_count,
                work,
                recorder,
            )
        frozen_h3 = agree_h3(h3[1], h3[2], recorder)
        for replica in (1, 2):
            roots = catalog_root_observations(
                inputs.views[replica],
                inputs.qualified_tdef_pages[replica],
                h1[replica],
                frozen_h2,
                frozen_h3,
                work,
            )
            derive_catalog_root(replica, roots, work, recorder)

        layouts = recorder.for_predicate("A4-H1-LOCATOR-LAYOUT-NONE")
        self.assertEqual([(row.replica, row.measured_count) for row in layouts], [(1, 2), (2, 2)])
        for prefix, expected in (
            ("A4-H1-", 15),
            ("A4-H2-", 13),
            ("A4-H3-", 13),
            ("A4-H4-", 4),
        ):
            rows = [row for row in recorder.events if row.predicate_id.startswith(prefix)]
            self.assertEqual(len(rows), expected)
            self.assertTrue(all(row.passed for row in rows))
        self.assertEqual(frozen_h1.replica, 0)

    def test_terminal_retains_real_pair_cardinality(self) -> None:
        signature = PLAN["candidate_grammars"]["h1"][
            "pair_multiple_reachability_signature"
        ]
        parameters = SyntheticParameters(
            signature_id=signature["signature_id"],
            locator_offsets=tuple(interval[0] for interval in signature["locator_holes"]),
        )
        inputs = check_analysis_input("a4-synthetic", _COMMIT, _inputs(parameters))
        recorder = MeasurementRecorder()
        with self.assertRaises(H1Terminal) as raised:
            derive_h1_replica(
                inputs.views[1], inputs.qualified_tdef_pages[1], WorkLedger(), recorder
            )
        row = recorder.events[-1]
        self.assertEqual(raised.exception.predicate_id, row.predicate_id)
        self.assertEqual(raised.exception.survivor_count, 2)
        self.assertEqual((row.measured_count, row.passed), (2, False))


if __name__ == "__main__":
    unittest.main()
