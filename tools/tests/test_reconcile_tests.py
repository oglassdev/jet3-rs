from __future__ import annotations

import copy
import hashlib
import json
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
        self.ignored = {reconcile.RuntimeTest("jet3_testkit", "tests::fixture_name")}
        registry = {
            "schema_version": 1,
            "requirements": [
                {
                    "id": "SAFE-01",
                    "requirement": "Safe parsing",
                    "acceptance_gates": ["G1", "G2"],
                    "required_evidence": "bounded tests",
                },
                {
                    "id": "TEST-01",
                    "requirement": "Test inventory",
                    "acceptance_gates": ["G2"],
                    "required_evidence": "reconciled manifest",
                },
            ],
        }
        path = self.repo / "docs/validation/traceability-ids.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(registry), encoding="utf-8")
        self.observation = reconcile.build_runtime_observation(
            self.runtime,
            self.ignored,
            git_commit="0" * 40,
            dirty=False,
        )
        self.document = {
            "schema_version": 2,
            "inventory_policy": {"include_ignored": True},
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
                ),
            ],
        }

    @staticmethod
    def _case(
        test_id: str, target: str, runtime_name: str, invariant: str
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
        }

    def _errors(
        self, document: object | None = None, observation: object | None = None
    ) -> list[str]:
        _, errors = reconcile.reconcile_document(
            self.document if document is None else document,
            self.observation if observation is None else observation,
            self.repo,
            expected_commit="0" * 40,
            expected_dirty=False,
        )
        return errors

    def test_valid_inventory_reconciles_separate_observation(self) -> None:
        summary, errors = reconcile.reconcile_document(
            self.document,
            self.observation,
            self.repo,
            expected_commit="0" * 40,
            expected_dirty=False,
        )
        self.assertEqual(errors, [])
        self.assertEqual(summary, {"ignored": 1, "meaningful": 2, "runtime_total": 3})
        self.assertNotIn("execution_status", self.document["cases"][0])
        self.assertNotIn("ignored", self.document["cases"][0])

    def test_duplicate_ids_runtime_names_and_invariants_fail(self) -> None:
        for field, fragment in (
            ("id", "duplicate test ID"),
            ("runtime_name", "duplicate runtime test"),
            ("distinct_invariant", "duplicate distinct invariant"),
        ):
            with self.subTest(field=field):
                document = copy.deepcopy(self.document)
                document["cases"][1][field] = document["cases"][0][field]
                self.assertTrue(
                    any(fragment in error for error in self._errors(document))
                )

    def test_missing_runtime_and_stale_inventory_entries_fail(self) -> None:
        missing = copy.deepcopy(self.document)
        missing["cases"].pop()
        self.assertTrue(any("runtime test missing" in error for error in self._errors(missing)))
        stale = copy.deepcopy(self.document)
        stale["cases"][0]["runtime_name"] = "binary::tests::removed"
        errors = self._errors(stale)
        self.assertTrue(any("stale manifest test" in error for error in errors))
        self.assertTrue(any("runtime test missing" in error for error in errors))

    def test_platform_specific_case_is_required_only_on_its_platform(self) -> None:
        document = copy.deepcopy(self.document)
        other_platform = (
            "unix" if reconcile.CURRENT_PLATFORM == "windows" else "windows"
        )
        document["cases"][0]["platforms"] = [other_platform]
        runtime = set(self.runtime)
        runtime.remove(reconcile.RuntimeTest("jet3", "binary::tests::boundary"))
        observation = reconcile.build_runtime_observation(
            runtime,
            self.ignored,
            git_commit="0" * 40,
            dirty=False,
        )
        self.assertEqual(self._errors(document, observation), [])

        invalid = copy.deepcopy(document)
        invalid["cases"][0]["platforms"] = ["plan9"]
        self.assertTrue(
            any(".platforms:" in error for error in self._errors(invalid, observation))
        )

    def test_platforms_are_a_sorted_unique_nonempty_known_subset(self) -> None:
        for platforms in (["unix"], ["windows"], ["unix", "windows"]):
            with self.subTest(platforms=platforms):
                document = copy.deepcopy(self.document)
                document["cases"][0]["platforms"] = platforms
                self.assertFalse(
                    any(".platforms:" in error for error in self._errors(document))
                )
        for platforms in (
            [],
            ["unix", "unix"],
            ["windows", "unix"],
            ["plan9"],
        ):
            with self.subTest(platforms=platforms):
                document = copy.deepcopy(self.document)
                document["cases"][0]["platforms"] = platforms
                self.assertTrue(
                    any(".platforms:" in error for error in self._errors(document))
                )

    def test_normative_schema_declares_the_exact_platform_subsets(self) -> None:
        schema_path = (
            TOOLS.parent
            / "docs"
            / "validation"
            / "schema"
            / "test-manifest.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["$defs"]["case"]["properties"]["platforms"],
            {
                "oneOf": [
                    {"const": ["unix"]},
                    {"const": ["windows"]},
                    {"const": ["unix", "windows"]},
                ]
            },
        )

    def test_runtime_state_fields_are_rejected_from_inventory(self) -> None:
        document = copy.deepcopy(self.document)
        document["cases"][0]["ignored"] = False
        document["cases"][0]["execution_status"] = "listed"
        self.assertTrue(any("unknown=" in error for error in self._errors(document)))

    def test_observation_owns_ignored_state_and_counts(self) -> None:
        runtime, ignored, counts, errors = reconcile.validate_runtime_observation(
            self.observation
        )
        self.assertEqual(errors, [])
        self.assertEqual(runtime, self.runtime)
        self.assertEqual(ignored, self.ignored)
        self.assertEqual(counts["meaningful"], 2)
        self.assertEqual(self.observation["git_commit"], "0" * 40)
        self.assertFalse(self.observation["dirty"])
        corrupted = copy.deepcopy(self.observation)
        corrupted["counts"]["meaningful"] = 300
        self.assertTrue(
            any("counts" in error for error in self._errors(observation=corrupted))
        )

    def test_observation_requires_exact_commit_and_dirty_state(self) -> None:
        corrupted = copy.deepcopy(self.observation)
        corrupted["git_commit"] = "HEAD"
        corrupted["dirty"] = "false"
        errors = self._errors(observation=corrupted)
        self.assertTrue(any("git_commit" in error for error in errors))
        self.assertTrue(any("dirty" in error for error in errors))

    def test_observation_rejects_duplicates_and_nondeterministic_order(self) -> None:
        duplicate = copy.deepcopy(self.observation)
        duplicate["tests"].append(copy.deepcopy(duplicate["tests"][0]))
        errors = self._errors(observation=duplicate)
        self.assertTrue(any("duplicate runtime test" in error for error in errors))
        reversed_observation = copy.deepcopy(self.observation)
        reversed_observation["tests"].reverse()
        self.assertTrue(
            any("entries must be sorted" in error for error in self._errors(observation=reversed_observation))
        )

    def test_manifest_order_must_be_deterministic(self) -> None:
        document = copy.deepcopy(self.document)
        document["cases"].reverse()
        self.assertTrue(
            any("sorted by stable test ID" in error for error in self._errors(document))
        )

    def test_registry_is_authoritative_and_validated(self) -> None:
        document = copy.deepcopy(self.document)
        document["cases"][0]["traceability_ids"] = ["MADE-UP-99"]
        self.assertTrue(
            any("registered IDs" in error for error in self._errors(document))
        )
        registry_path = self.repo / "docs/validation/traceability-ids.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["requirements"][1]["id"] = "SAFE-01"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        self.assertTrue(any("duplicate requirement ID" in error for error in self._errors()))

    def test_fixture_paths_are_safe_existing_and_hash_bound(self) -> None:
        fixture = self.repo / "fixtures/case.bin"
        fixture.parent.mkdir()
        fixture.write_bytes(b"fixture")
        digest = hashlib.sha256(b"fixture").hexdigest()
        valid = copy.deepcopy(self.document)
        valid["cases"][0]["fixtures"] = [{"path": "fixtures/case.bin", "sha256": digest}]
        self.assertEqual(self._errors(valid), [])
        unsafe = copy.deepcopy(valid)
        unsafe["cases"][0]["fixtures"][0]["path"] = "../case.bin"
        self.assertTrue(any("unsafe" in error for error in self._errors(unsafe)))
        wrong_hash = copy.deepcopy(valid)
        wrong_hash["cases"][0]["fixtures"][0]["sha256"] = "0" * 64
        self.assertTrue(any("hash mismatch" in error for error in self._errors(wrong_hash)))

    def test_cargo_parser_tracks_targets_and_ignores_summaries(self) -> None:
        output = """
    Running unittests src/lib.rs (target/debug/deps/jet3-0123456789abcdef)
binary::tests::boundary: test
1 test, 0 benchmarks
    Running unittests src/lib.rs (target/debug/deps/jet3_testkit-fedcba9876543210)
tests::fixture_name: test
"""
        self.assertEqual(
            reconcile.parse_cargo_list(output),
            {
                reconcile.RuntimeTest("jet3", "binary::tests::boundary"),
                reconcile.RuntimeTest("jet3_testkit", "tests::fixture_name"),
            },
        )

    def test_cargo_parser_keeps_reordered_streams_attributed_by_block(self) -> None:
        stdout = """\
binary::tests::boundary: test
1 test, 0 benchmarks
tests::fixture_name: test
1 test, 0 benchmarks
"""
        stderr = """\
   Compiling jet3 v0.0.0
    Running unittests src/lib.rs (target/debug/deps/jet3-0123456789abcdef)
    Running unittests src/lib.rs (target/debug/deps/jet3_testkit-fedcba9876543210)
"""
        self.assertEqual(
            reconcile.parse_cargo_list(stdout, stderr),
            {
                reconcile.RuntimeTest("jet3", "binary::tests::boundary"),
                reconcile.RuntimeTest("jet3_testkit", "tests::fixture_name"),
            },
        )

    def test_cargo_parser_ignores_forced_ansi_color(self) -> None:
        stdout = (
            "\x1b[32mbinary::tests::boundary: test\x1b[0m\n"
            "1 test, 0 benchmarks\n"
        )
        stderr = (
            "\x1b[1m\x1b[92m     Running\x1b[0m unittests src/lib.rs "
            "(target/debug/deps/jet3-0123456789abcdef)\n"
        )
        self.assertEqual(
            reconcile.parse_cargo_list(stdout, stderr),
            {reconcile.RuntimeTest("jet3", "binary::tests::boundary")},
        )

    def test_cargo_parser_rejects_separate_stream_count_mismatch(self) -> None:
        stdout = """\
binary::tests::boundary: test
1 test, 0 benchmarks
"""
        stderr = """\
    Running unittests src/lib.rs (target/debug/deps/jet3-0123456789abcdef)
    Running unittests src/lib.rs (target/debug/deps/jet3_testkit-fedcba9876543210)
"""
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            reconcile.parse_cargo_list(stdout, stderr)

    def test_cargo_parser_rejects_unscoped_duplicate_and_doctests(self) -> None:
        with self.assertRaisesRegex(ValueError, "no preceding Cargo target"):
            reconcile.parse_cargo_list("tests::orphan: test\n")
        duplicate = """
    Running unittests src/lib.rs (target/debug/deps/jet3-0123456789abcdef)
tests::same: test
tests::same: test
"""
        with self.assertRaisesRegex(ValueError, "duplicate test"):
            reconcile.parse_cargo_list(duplicate)
        doctest = """
   Doc-tests jet3
crates/jet3/src/lib.rs - example (line 10): test
"""
        with self.assertRaisesRegex(ValueError, "Doc-tests are outside"):
            reconcile.parse_cargo_list(doctest)

    def test_cargo_parser_accepts_top_level_integration_test_name(self) -> None:
        output = """
    Running tests/smoke.rs (target/debug/deps/smoke-0123456789abcdef)
opens_database: test
"""
        self.assertEqual(
            reconcile.parse_cargo_list(output),
            {reconcile.RuntimeTest("smoke", "opens_database")},
        )


if __name__ == "__main__":
    unittest.main()
