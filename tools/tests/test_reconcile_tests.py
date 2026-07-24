from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import reconcile_tests as reconcile  # noqa: E402


class ReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.runtime = {
            reconcile.RuntimeTest("jet3", "binary::tests::boundary"),
            reconcile.RuntimeTest("jet3", "source::tests::short_read"),
            reconcile.RuntimeTest("jet3_testkit", "tests::fixture_name"),
        }
        self.ignored = {
            reconcile.RuntimeTest("jet3_testkit", "tests::fixture_name")
        }
        self.document = {
            "schema_version": 1,
            "cargo_command": list(reconcile.CARGO_COMMAND),
            "meaningful_case_count": 2,
            "cases": [
                self._case(
                    "UT-BINARY-001",
                    "jet3",
                    "binary::tests::boundary",
                    "Binary cursor accepts the exact byte boundary.",
                ),
                self._case(
                    "UT-SOURCE-001",
                    "jet3",
                    "source::tests::short_read",
                    "Short file reads retain their actual byte count.",
                ),
                self._case(
                    "UT-TESTKIT-001",
                    "jet3_testkit",
                    "tests::fixture_name",
                    "Fixture metadata uses the scoped format name.",
                    ignored=True,
                ),
            ],
        }

    @staticmethod
    def _case(
        test_id: str,
        target: str,
        runtime_name: str,
        invariant: str,
        *,
        ignored: bool = False,
    ) -> dict[str, object]:
        return {
            "id": test_id,
            "target": target,
            "runtime_name": runtime_name,
            "traceability_ids": ["SAFE-01", "TEST-01"],
            "purpose": f"Exercise {invariant}",
            "distinct_invariant": invariant,
            "fixtures": [],
            "expected_result": "The asserted structured result is returned.",
            "ignored": ignored,
            "execution_status": "ignored" if ignored else "listed",
        }

    def _errors(self, document: object | None = None) -> list[str]:
        _, errors = reconcile.reconcile_document(
            self.document if document is None else document,
            self.runtime,
            self.ignored,
            self.repo,
        )
        return errors

    def test_valid_manifest_reconciles_honest_meaningful_count(self) -> None:
        summary, errors = reconcile.reconcile_document(
            self.document, self.runtime, self.ignored, self.repo
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            summary,
            {"ignored": 1, "meaningful": 2, "runtime_total": 3},
        )

    def test_duplicate_ids_runtime_names_and_invariants_fail(self) -> None:
        for field, fragment in (
            ("id", "duplicate test ID"),
            ("runtime_name", "duplicate runtime test"),
            ("distinct_invariant", "duplicate distinct invariant"),
        ):
            with self.subTest(field=field):
                document = copy.deepcopy(self.document)
                document["cases"][1][field] = document["cases"][0][field]
                errors = self._errors(document)
                self.assertTrue(any(fragment in error for error in errors), errors)

    def test_missing_runtime_and_stale_manifest_entries_fail(self) -> None:
        missing = copy.deepcopy(self.document)
        missing["cases"].pop()
        missing["meaningful_case_count"] = 2
        self.assertTrue(
            any("runtime test missing" in error for error in self._errors(missing))
        )

        stale = copy.deepcopy(self.document)
        stale["cases"][0]["runtime_name"] = "binary::tests::removed"
        errors = self._errors(stale)
        self.assertTrue(any("stale manifest test" in error for error in errors))
        self.assertTrue(any("runtime test missing" in error for error in errors))

    def test_ignored_state_and_status_must_match_runtime(self) -> None:
        document = copy.deepcopy(self.document)
        document["cases"][2]["ignored"] = False
        document["cases"][2]["execution_status"] = "listed"
        document["meaningful_case_count"] = 3
        errors = self._errors(document)
        self.assertTrue(any("ignored state differs" in error for error in errors))
        self.assertTrue(any("active runtime count" in error for error in errors))

    def test_inflated_meaningful_count_fails(self) -> None:
        document = copy.deepcopy(self.document)
        document["meaningful_case_count"] = 300
        self.assertTrue(
            any("meaningful_case_count" in error for error in self._errors(document))
        )

    def test_manifest_order_must_be_deterministic(self) -> None:
        document = copy.deepcopy(self.document)
        document["cases"].reverse()
        self.assertTrue(
            any("sorted by stable test ID" in error for error in self._errors(document))
        )

    def test_unknown_traceability_and_command_drift_fail(self) -> None:
        document = copy.deepcopy(self.document)
        document["cases"][0]["traceability_ids"] = ["MADE-UP-99"]
        document["cargo_command"].append("--ignored")
        errors = self._errors(document)
        self.assertTrue(any("unique known IDs" in error for error in errors))
        self.assertTrue(any("binding contract" in error for error in errors))

    def test_fixture_paths_are_safe_existing_and_hash_bound(self) -> None:
        fixture = self.repo / "fixtures" / "case.bin"
        fixture.parent.mkdir()
        fixture.write_bytes(b"fixture")
        digest = hashlib.sha256(b"fixture").hexdigest()

        valid = copy.deepcopy(self.document)
        valid["cases"][0]["fixtures"] = [
            {"path": "fixtures/case.bin", "sha256": digest}
        ]
        self.assertEqual(self._errors(valid), [])

        unsafe = copy.deepcopy(valid)
        unsafe["cases"][0]["fixtures"][0]["path"] = "../case.bin"
        self.assertTrue(any("unsafe" in error for error in self._errors(unsafe)))

        wrong_hash = copy.deepcopy(valid)
        wrong_hash["cases"][0]["fixtures"][0]["sha256"] = "0" * 64
        self.assertTrue(
            any("hash mismatch" in error for error in self._errors(wrong_hash))
        )

    def test_cargo_parser_tracks_targets_and_ignores_summaries(self) -> None:
        output = """
    Running unittests src/lib.rs (target/debug/deps/jet3-0123456789abcdef)
binary::tests::boundary: test

1 test, 0 benchmarks
    Running unittests src/lib.rs (target/debug/deps/jet3_testkit-fedcba9876543210)
tests::fixture_name: test

1 test, 0 benchmarks
"""
        self.assertEqual(
            reconcile.parse_cargo_list(output),
            {
                reconcile.RuntimeTest("jet3", "binary::tests::boundary"),
                reconcile.RuntimeTest("jet3_testkit", "tests::fixture_name"),
            },
        )

    def test_cargo_parser_rejects_unscoped_and_duplicate_tests(self) -> None:
        with self.assertRaisesRegex(ValueError, "no preceding Cargo target"):
            reconcile.parse_cargo_list("tests::orphan: test\n")
        duplicate = """
    Running unittests src/lib.rs (target/debug/deps/jet3-0123456789abcdef)
tests::same: test
tests::same: test
"""
        with self.assertRaisesRegex(ValueError, "duplicate test"):
            reconcile.parse_cargo_list(duplicate)

    def test_cargo_parser_accepts_top_level_integration_test_name(self) -> None:
        output = """
    Running tests/smoke.rs (target/debug/deps/smoke-0123456789abcdef)
opens_database: test
"""
        self.assertEqual(
            reconcile.parse_cargo_list(output),
            {reconcile.RuntimeTest("smoke", "opens_database")},
        )

    def test_cargo_parser_diagnoses_doc_tests_instead_of_misattributing(self) -> None:
        output = """
    Running unittests src/lib.rs (target/debug/deps/jet3-0123456789abcdef)
tests::unit: test
   Doc-tests jet3
crates/jet3/src/lib.rs - example (line 10): test
"""
        with self.assertRaisesRegex(ValueError, "Doc-tests are outside"):
            reconcile.parse_cargo_list(output)


if __name__ == "__main__":
    unittest.main()
