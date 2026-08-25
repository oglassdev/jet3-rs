"""Integration coverage for independent A4 derivation-terminal paths."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
TESTS = Path(__file__).resolve().parent

import sys

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import test_a4_independent_bundle as fixture  # noqa: E402
from a4_generator import SyntheticParameters  # noqa: E402
from a4_independent_bundle import BundleLoader, ValidationError  # noqa: E402
from a4_independent_contract import CONTRACT, EXPECTED_TAMPERS  # noqa: E402
from a4_independent_validator import (  # noqa: E402
    execute_tamper_suite,
    recompute_bundle,
    validate_bundle,
)
from test_a4_analyzer import _inputs  # noqa: E402


def _parameters_h1_pair_multiple() -> SyntheticParameters:
    signature = CONTRACT.plan["candidate_grammars"]["h1"][
        "pair_multiple_reachability_signature"
    ]
    return SyntheticParameters(
        signature_id=signature["signature_id"],
        locator_offsets=tuple(interval[0] for interval in signature["locator_holes"]),
    )


def _build(root: Path, parameters: SyntheticParameters) -> None:
    with mock.patch.object(
        fixture, "_inputs", side_effect=lambda: _inputs(parameters)
    ):
        fixture._build_bundle(root)


def _assert_exact_derivation(
    case: unittest.TestCase, bundle: object, recomputed: dict[str, object]
) -> None:
    for field in ("layers", "qualified_pages", "work_charges"):
        case.assertEqual(recomputed[field], bundle.frozen[field])
        case.assertEqual(bundle.report[field], bundle.frozen[field])
    case.assertEqual(
        recomputed["predicate_results"][:35], bundle.report["predicate_results"][:35]
    )


def _assert_t1_t9(
    case: unittest.TestCase, bundle: object, recomputed: dict[str, object]
) -> None:
    results = execute_tamper_suite(bundle, recomputed)
    case.assertEqual(
        [(row["id"], row["discrepancy_code"]) for row in results],
        list(EXPECTED_TAMPERS),
    )
    case.assertTrue(all(row["rejected"] for row in results))


class A4IndependentTerminalPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="a4-independent-terminals-")
        base = Path(cls.temporary.name)
        cls.h1_root = base / "h1-pair-multiple"
        cls.h2_root = base / "h2-replica-disagreement"
        _build(cls.h1_root, _parameters_h1_pair_multiple())
        _build(
            cls.h2_root,
            SyntheticParameters(owned_ordinal_by_replica={2: 1}),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_h1_first_terminal_recomputes_projects_and_rejects_t1_t9(self) -> None:
        bundle = BundleLoader(self.h1_root).load()
        recomputed = recompute_bundle(bundle)
        h1 = recomputed["layers"]["h1_tdef_to_map_row"]
        self.assertEqual(h1["terminal_predicate_id"], "A4-H1-LOCATOR-PAIR-MULTIPLE")
        self.assertTrue(
            all(row["status"] == "not_applicable"
                for row in recomputed["holdout_results"].values())
        )
        _assert_exact_derivation(self, bundle, recomputed)
        validate_bundle(bundle, recomputed)
        _assert_t1_t9(self, bundle, recomputed)

    def test_h2_terminal_exposes_analyzer_holdout_projection_conflict(self) -> None:
        bundle = BundleLoader(self.h2_root).load()
        recomputed = recompute_bundle(bundle)
        h2 = recomputed["layers"]["h2_row_identity_map_role"]
        self.assertEqual(h2["terminal_predicate_id"], "A4-H2-REPLICA-DISAGREEMENT")
        _assert_exact_derivation(self, bundle, recomputed)

        h1_contract = next(
            row for row in CONTRACT.plan["predicate_registry"]["predicate_contracts"]
            if row["predicate_id"] == "A4-H1-HOLDOUT-PREDICTION"
        )
        self.assertEqual(
            h1_contract["prerequisites"], ["derivation_candidate_set_sha256"]
        )
        self.assertEqual(recomputed["holdout_results"]["h1"]["status"], "pass")
        self.assertEqual(bundle.report["holdout_results"]["h1"]["status"], "not_applicable")
        for name in ("h2", "h3", "h4_root", "h4_fields"):
            self.assertEqual(recomputed["holdout_results"][name]["status"], "not_applicable")
            self.assertEqual(bundle.report["holdout_results"][name]["status"], "not_applicable")
        with self.assertRaisesRegex(ValidationError, "holdout_projection_mismatch"):
            validate_bundle(bundle, recomputed)
        _assert_t1_t9(self, bundle, recomputed)


if __name__ == "__main__":
    unittest.main()
