"""Focused tests for predicate-major A4 replica orchestration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from a4_analysis_input import check_analysis_input  # noqa: E402
from a4_analysis_state import freeze_derivation  # noqa: E402
import a4_layers  # noqa: E402
from a4_layers import DerivationLayers, derive_layers  # noqa: E402
from a4_measurements import MeasurementRecorder  # noqa: E402
from a4_model import A4AnalysisError, WorkLedger  # noqa: E402
from a4_predicate_major import evaluate_replica_predicates  # noqa: E402
from a4_spec import LAYER_PREDICATE_IDS  # noqa: E402
from test_a4_analyzer import _COMMIT, _inputs  # noqa: E402


class A4PredicateMajorTests(unittest.TestCase):
    def test_successful_derivation_records_every_layer_predicate_major(self) -> None:
        inputs = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
        result = derive_layers(inputs)
        self.assertIsInstance(result, DerivationLayers)
        assert isinstance(result, DerivationLayers)

        expected: list[tuple[str, int | None]] = []
        for predicates in LAYER_PREDICATE_IDS.values():
            expected.extend(
                (predicate_id, replica)
                for predicate_id in predicates[:-1]
                for replica in (1, 2)
            )
            expected.append((predicates[-1], None))
        self.assertEqual(
            [(row.predicate_id, row.replica) for row in result.measurements],
            expected,
        )

    def test_earlier_replica_two_failure_wins_in_all_four_layers(self) -> None:
        cases = (
            ("h1_tdef_to_map_row", 1, 5, 2),
            ("h2_row_identity_map_role", 1, 4, 1),
            ("h3_indirect_traversal", 1, 4, 1),
            ("h4_catalog_bootstrap", 1, 6, 2),
        )
        for layer, earlier_index, later_index, failure_count in cases:
            with self.subTest(layer=layer):
                predicates = tuple(LAYER_PREDICATE_IDS[layer][:-1])
                earlier = predicates[earlier_index]
                later = predicates[later_index]
                reached: list[tuple[int, str]] = []

                def run(replica: int, _work: WorkLedger, recorder: object) -> str:
                    for predicate_id in predicates:
                        reached.append((replica, predicate_id))
                        fails = (
                            (replica == 2 and predicate_id == earlier)
                            or (replica == 1 and predicate_id == later)
                        )
                        count = failure_count if fails else 1
                        recorder.record(
                            predicate_id, count, not fails, replica=replica
                        )
                        if fails:
                            raise A4AnalysisError(predicate_id, count)
                    return f"replica-{replica}"

                measurements = MeasurementRecorder()
                outcome = evaluate_replica_predicates(
                    predicates, run, WorkLedger(), measurements
                )
                self.assertIsNotNone(outcome.failure)
                assert outcome.failure is not None
                self.assertEqual(
                    (outcome.failure.replica, outcome.failure.error.predicate_id),
                    (2, earlier),
                )
                self.assertFalse(any(predicate == later for _, predicate in reached))
                expected_events = [
                    (predicate, replica)
                    for predicate in predicates[:earlier_index]
                    for replica in (1, 2)
                ] + [(earlier, 1), (earlier, 2)]
                self.assertEqual(
                    [(row.predicate_id, row.replica) for row in measurements.events],
                    expected_events,
                )

    def test_replica_two_directory_failure_uses_one_alternative_work_path(self) -> None:
        predicates = tuple(LAYER_PREDICATE_IDS["h2_row_identity_map_role"][:2])

        def run(replica: int, work: WorkLedger, recorder: object) -> str:
            work.charge(
                "valid_path_row_directory_entries"
                if replica == 1
                else "invalid_path_row_directory_entries"
            )
            passed = replica == 1
            recorder.record(predicates[0], 1, passed, replica=replica)
            if not passed:
                raise A4AnalysisError(predicates[0], 1)
            recorder.record(predicates[1], 1, True, replica=replica)
            return f"replica-{replica}"

        work = WorkLedger()
        outcome = evaluate_replica_predicates(
            predicates, run, work, MeasurementRecorder()
        )
        self.assertIsNotNone(outcome.failure)
        self.assertEqual(work.value("valid_path_row_directory_entries"), 0)
        self.assertEqual(work.value("invalid_path_row_directory_entries"), 2)

    def test_later_replica_one_failure_accounts_replica_two_completed_prefix(self) -> None:
        predicates = ("A4-H1-TDEF-NONE", "A4-H1-TDEF-MULTIPLE")
        reached: list[tuple[int, str]] = []

        def run(replica: int, work: WorkLedger, recorder: object) -> str:
            work.charge("candidate_serializations")
            reached.append((replica, predicates[0]))
            recorder.record(predicates[0], 1, True, replica=replica)
            work.charge("candidate_serializations")
            reached.append((replica, predicates[1]))
            passed = replica != 1
            recorder.record(predicates[1], 2, passed, replica=replica)
            if not passed:
                raise A4AnalysisError(predicates[1], 2)
            return f"replica-{replica}"

        work = WorkLedger()
        outcome = evaluate_replica_predicates(
            predicates, run, work, MeasurementRecorder()
        )
        self.assertEqual(outcome.failure.replica, 1)
        self.assertEqual(work.value("candidate_serializations"), 3)
        self.assertNotIn((2, predicates[1]), reached)

    def test_h4_replica_one_field_failure_never_scans_replica_two_names(self) -> None:
        inputs = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
        scanned: list[int] = []
        original_scan = a4_layers.h4_primitive.scan_name_occurrences

        def scan(record, ledger=None):
            scanned.append(record.replica)
            return original_scan(record, ledger)

        def fail(replica, records, ledger, measurements, evidence):
            measurements.record(
                "A4-H4-FIELD-MODEL-NONE", 0, False, replica=replica
            )
            error = A4AnalysisError("A4-H4-FIELD-MODEL-NONE")
            error.candidates = ()
            raise error

        with patch.object(
            a4_layers.h4_primitive, "scan_name_occurrences", side_effect=scan
        ), patch.object(a4_layers, "derive_structural_fields", side_effect=fail):
            work = WorkLedger()
            result = derive_layers(inputs, work)
            freeze_derivation(inputs, result, work)
        self.assertEqual(result.predicate_id, "A4-H4-FIELD-MODEL-NONE")
        self.assertTrue(scanned)
        self.assertEqual(set(scanned), {1})


if __name__ == "__main__":
    unittest.main()
