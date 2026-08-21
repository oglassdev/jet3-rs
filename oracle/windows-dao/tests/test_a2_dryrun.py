"""Contracts for the committed, non-evidential A2 dry-run artifacts."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
EXPERIMENT = ROOT / "oracle" / "windows-dao" / "experiments" / "a2"
DRY_RUN = EXPERIMENT / "dry-run"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a2_dryrun import (  # noqa: E402
    CASE_TRANSCRIPT,
    CHECKSUMS,
    RETAINED_REPORT,
    SYNTHETIC_REPORT,
)
from a2_dryrun_retained import LEGACY_STATE, ProjectedReplica  # noqa: E402
from a2_dryrun_synthetic import REQUIRED_CASES, source_contract_checks  # noqa: E402
from a2_model import PLAN, PLAN_SHA256, PREDICATES  # noqa: E402
from a2_spec import PREDICATE_IDS, validate_dry_run_report  # noqa: E402
from protocol_validation import ValidationError, canonical_json_bytes  # noqa: E402


def load(name: str) -> dict[str, object]:
    document = json.loads((DRY_RUN / name).read_bytes())
    if not isinstance(document, dict):
        raise AssertionError(f"{name} is not a JSON object")
    return document


class A2DryRunArtifactTests(unittest.TestCase):
    def test_reports_validate_and_remain_non_evidential(self) -> None:
        retained = load(RETAINED_REPORT)
        synthetic = load(SYNTHETIC_REPORT)
        validate_dry_run_report(retained)
        validate_dry_run_report(synthetic)
        for report in (retained, synthetic):
            self.assertEqual(report["plan_sha256"], PLAN_SHA256)
            self.assertEqual(report["result"], "pass")
            self.assertFalse(report["holdout_opened"])
            self.assertFalse(report["scientific_evidence"])
            self.assertFalse(report["acquisition_authorized"])
            self.assertFalse(report["capability_advancement_authorized"])

    def test_retained_report_records_the_named_legacy_state_and_bounds(self) -> None:
        report = load(RETAINED_REPORT)
        retained = PLAN["analyzer_dry_run_contract"]["retained_a1_input"]
        self.assertEqual(report["predicted_terminal_states"], [LEGACY_STATE])
        self.assertEqual(
            set(report["terminal_predicate_ids"]),
            set(retained["not_applicable_predicates"]),
        )
        self.assertLessEqual(report["input_page_blob_count"], retained["max_input_page_blobs"])
        self.assertEqual(
            report["source_identity"]["manifest_or_fixture_sha256"],
            retained["bundle_manifest_sha256"],
        )

    def test_synthetic_report_and_transcript_cover_every_case_and_predicate(self) -> None:
        report = load(SYNTHETIC_REPORT)
        transcript = load(CASE_TRANSCRIPT)
        payload = canonical_json_bytes(transcript)
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            report["source_identity"]["manifest_or_fixture_sha256"],
        )
        self.assertEqual(set(report["predicted_terminal_states"]), set(REQUIRED_CASES))
        self.assertEqual(set(report["terminal_predicate_ids"]), set(PREDICATE_IDS))
        reachability = transcript["predicate_reachability"]
        self.assertEqual(
            {row["predicate_ids"][0] for row in reachability}, set(PREDICATE_IDS)
        )
        self.assertEqual(
            {row["outcome"] for row in reachability},
            {reason for reason, _ in PREDICATES.values()},
        )
        case_names = {row["case"] for row in transcript["cases"]}
        self.assertIn("all_layers_decisive", case_names)
        self.assertIn("partial_layer_outcome", case_names)
        self.assertEqual(
            transcript["decisive_validator"]["bundle_status"],
            "decisive_pending_independent_validation",
        )

    def test_checksum_inventory_is_exact(self) -> None:
        lines = (DRY_RUN / CHECKSUMS).read_text(encoding="ascii").splitlines()
        recorded = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in lines}
        expected_names = {RETAINED_REPORT, SYNTHETIC_REPORT, CASE_TRANSCRIPT}
        self.assertEqual(set(recorded), expected_names)
        for name, expected in recorded.items():
            self.assertEqual(hashlib.sha256((DRY_RUN / name).read_bytes()).hexdigest(), expected)

    def test_source_contract_checks_pass_and_modules_remain_bounded(self) -> None:
        self.assertTrue(all(source_contract_checks().values()))
        for path in sorted(SCRIPTS.glob("a2_*.py")):
            with self.subTest(path=path.name):
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 800)

    def test_replica_three_is_rejected_before_any_path_access(self) -> None:
        with self.assertRaises(ValidationError):
            ProjectedReplica(Path("/not-opened"), 3, {}, object())


if __name__ == "__main__":
    unittest.main()
