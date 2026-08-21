"""Contracts for the committed, non-evidential A2 dry-run artifacts."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
EXPERIMENT = ROOT / "oracle" / "windows-dao" / "experiments" / "a2"
DRY_RUN = EXPERIMENT / "dry-run"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a2_dryrun import (  # noqa: E402
    CASE_TRANSCRIPT,
    CHECKSUMS,
    DEFAULT_RETAINED_ROOT,
    RETAINED_REPORT,
    SYNTHETIC_REPORT,
)
from a2_dryrun_retained import (  # noqa: E402
    BlobTracker,
    LEGACY_STATE,
    ProjectedReplica,
    _manifest,
    run_retained,
)
from a2_model import PLAN, PLAN_SHA256  # noqa: E402
from a2_dryrun_synthetic import UNREACHABLE_BY_CONSTRUCTION  # noqa: E402
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
            self.assertFalse(report["holdout_opened"])
            self.assertFalse(report["scientific_evidence"])
            self.assertFalse(report["acquisition_authorized"])
            self.assertFalse(report["capability_advancement_authorized"])
        self.assertEqual(retained["result"], "pass")
        transcript = load(CASE_TRANSCRIPT)
        self.assertEqual(synthetic["result"], transcript["acceptance"]["result"])

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
        reachability = transcript["predicate_reachability"]
        self.assertEqual(
            {row["target_predicate_id"] for row in reachability}, set(PREDICATE_IDS)
        )
        reached = {row["target_predicate_id"] for row in reachability if row["status"] == "reached"}
        excluded = {
            row["target_predicate_id"]
            for row in reachability
            if row["status"] == "unreachable_by_construction"
        }
        self.assertEqual(
            set(report["terminal_predicate_ids"]), reached
        )
        for row in reachability:
            with self.subTest(predicate=row["target_predicate_id"]):
                if row["status"] == "unreachable_by_construction":
                    self.assertNotIn(row["target_predicate_id"], row["actual_predicate_ids"])
                else:
                    self.assertEqual(
                        row["status"] == "reached",
                        row["target_predicate_id"] in row["actual_predicate_ids"],
                    )
                self.assertEqual(row["reported_layer"], row["layer"])
        self.assertEqual(excluded, set(UNREACHABLE_BY_CONSTRUCTION))
        self.assertEqual(
            set(transcript["acceptance"]["unreachable_predicate_ids"]),
            set(PREDICATE_IDS) - reached - excluded,
        )
        self.assertEqual(
            set(transcript["acceptance"]["unreachable_by_construction_predicate_ids"]),
            excluded,
        )
        self.assertIn(
            "every_required_reachable_abort_reached_by_single_perturbation",
            report["assertions"],
        )
        self.assertNotIn(
            "every_abort_reached_by_single_perturbation", report["assertions"]
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

    def test_source_contract_checks_are_evidenced_and_modules_remain_bounded(self) -> None:
        transcript = load(CASE_TRANSCRIPT)
        checks = transcript["source_contract_checks"]
        for name, result in checks.items():
            with self.subTest(check=name):
                self.assertIn(result["status"], {"pass", "fail", "not_implemented"})
                self.assertIn("evidence", result)
                self.assertEqual(result["status"], "pass")
        for path in sorted(SCRIPTS.glob("a2_*.py")):
            with self.subTest(path=path.name):
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 800)

    def test_pointer_none_sites_are_reached_and_inline_multiple_is_classified(self) -> None:
        rows = {
            row["target_predicate_id"]: row
            for row in load(CASE_TRANSCRIPT)["predicate_reachability"]
        }
        for predicate_id in ("A2-GROWTH-POINTER-NONE", "A2-CHURN-POINTER-NONE"):
            with self.subTest(predicate_id=predicate_id):
                self.assertEqual(rows[predicate_id]["status"], "reached")
                self.assertEqual(rows[predicate_id]["actual_predicate_ids"], [predicate_id])
        inline = rows["A2-INLINE-BOUNDARY-MULTIPLE"]
        self.assertEqual(inline["status"], "unreachable_by_construction")
        self.assertEqual(inline["actual_predicate_ids"], ["A2-INLINE-SUFFIX"])

    def test_replica_three_is_rejected_before_any_path_access(self) -> None:
        with self.assertRaises(ValidationError):
            ProjectedReplica(Path("/not-opened"), 3, {}, object())

    def test_run_retained_never_accesses_replica_three_or_holdout_and_caches_reads(self) -> None:
        if not DEFAULT_RETAINED_ROOT.is_dir():
            self.skipTest("pinned retained run-12 copy is not available")
        opened_pages: list[Path] = []
        original_open = Path.open
        original_read_bytes = Path.read_bytes

        def assert_allowed(path: Path) -> None:
            text = path.as_posix().lower()
            self.assertNotIn("replica-03", text)
            self.assertNotIn("/holdout", text)

        def trapped_open(path: Path, *args: object, **kwargs: object):
            assert_allowed(path)
            return original_open(path, *args, **kwargs)

        def trapped_read_bytes(path: Path) -> bytes:
            assert_allowed(path)
            if path.suffix == ".page":
                opened_pages.append(path)
            return original_read_bytes(path)

        with patch.object(Path, "open", trapped_open), patch.object(
            Path, "read_bytes", trapped_read_bytes
        ):
            result = run_retained(DEFAULT_RETAINED_ROOT)
        self.assertEqual(len(opened_pages), result.blob_count)
        self.assertEqual(len(set(opened_pages)), result.blob_count)
        self.assertLessEqual(
            result.blob_count,
            PLAN["analyzer_dry_run_contract"]["retained_a1_input"]["max_input_page_blobs"],
        )

    def test_missing_recreate_projection_remains_null(self) -> None:
        if not DEFAULT_RETAINED_ROOT.is_dir():
            self.skipTest("pinned retained run-12 copy is not available")
        _, entries = _manifest(DEFAULT_RETAINED_ROOT)
        replica = ProjectedReplica(
            DEFAULT_RETAINED_ROOT, 1, entries, BlobTracker()
        )
        self.assertEqual(
            replica.projection_status["D_RECREATE_EMPTY"],
            "not_applicable_missing_a1_checkpoint",
        )
        self.assertNotIn("D_RECREATE_EMPTY", replica.page_count)
        self.assertNotIn("D_RECREATE_EMPTY", replica.ordered_page_sha256)

    def test_every_legacy_ordinal_row_is_an_analyzer_result(self) -> None:
        transcript = load(CASE_TRANSCRIPT)
        rows = [row for row in transcript["cases"] if row.get("input_schedule")]
        self.assertEqual(len(rows), 71)
        self.assertEqual(
            {row["legacy_source_conversion_ordinal"] for row in rows},
            set(range(1, 71)) | {None},
        )
        for row in rows:
            self.assertIn(
                row["scientific_outcome"],
                {"one_or_more_submodels_predict_holdout", "no_submodel_predicts_holdout"},
            )
            self.assertTrue(row["layer_outcomes"])


if __name__ == "__main__":
    unittest.main()
