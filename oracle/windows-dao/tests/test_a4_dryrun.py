"""Contracts for the deterministic A4 pre-acquisition dry-run harness."""

from __future__ import annotations

import ast
import hashlib
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a4_analysis import analyze  # noqa: E402
from a4_dryrun import (  # noqa: E402
    _reject_serialized_verdict_keys,
    _reported_campaign_rejection,
)
from a4_dryrun_calibration import _OpenedPageStore  # noqa: E402
from a4_dryrun_fixtures import FIXTURES, Fixture, reject_verdict_keys  # noqa: E402
from a4_dryrun_io import BoundedIoError, read_regular, run_bounded_child  # noqa: E402
from a4_dryrun_independent import _campaign_bundle  # noqa: E402
from a4_dryrun_surface import CAMPAIGN_ID, PRODUCER_COMMIT, FixtureInputs  # noqa: E402
from a4_dryrun_surface import write_fixture_trees  # noqa: E402
from a4_generator import SyntheticParameters  # noqa: E402
from a4_independent_campaign import recompute_campaign  # noqa: E402
from a4_spec import PREDICATE_CONTRACTS  # noqa: E402


class A4DryRunTests(unittest.TestCase):
    def test_bounded_regular_read_accepts_equality_and_rejects_one_over(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"abcd")
            self.assertEqual(read_regular(source, 4), b"abcd")
            source.write_bytes(b"abcde")
            with self.assertRaises(BoundedIoError):
                read_regular(source, 4)
            source.unlink()
            source.symlink_to(root / "target.bin")
            (root / "target.bin").write_bytes(b"abcd")
            with self.assertRaises(BoundedIoError):
                read_regular(source, 4)

    def test_bounded_child_output_accepts_equality_and_rejects_one_over(self) -> None:
        command = (
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * int(sys.argv[1]))",
        )
        result = run_bounded_child(
            (*command, "4"),
            cwd=SCRIPTS,
            timeout_seconds=5,
            output_limit=4,
        )
        self.assertEqual(result.output, b"xxxx")
        with self.assertRaises(BoundedIoError):
            run_bounded_child(
                (*command, "5"),
                cwd=SCRIPTS,
                timeout_seconds=5,
                output_limit=4,
            )

    def test_serialized_fixture_verdict_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "fixture.json").write_text('{"accepted":false}')
            with self.assertRaises(Exception):
                _reject_serialized_verdict_keys((root,))

    def test_bounded_campaign_loader_preserves_registered_schema_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            roots = write_fixture_trees(
                root / "roots", FIXTURES["A4-SCHEMA-SNAPSHOT"]
            )
            workspace = root / "workspace"
            workspace.mkdir()
            bundle = _campaign_bundle(roots, workspace)
            result = recompute_campaign(bundle)
            self.assertEqual(
                result.first_failure.predicate_id, "A4-SCHEMA-SNAPSHOT"
            )

    def test_retained_page_store_counts_every_distinct_opened_blob_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store_path = root / "page-store"
            store_path.mkdir()
            payloads = (bytes(2048), bytes([1]) * 2048)
            digests = []
            for payload in payloads:
                digest = hashlib.sha256(payload).hexdigest()
                (store_path / f"{digest}.page").write_bytes(payload)
                digests.append(digest)
            store = _OpenedPageStore(root)
            self.assertEqual(store.read(digests[0]), payloads[0])
            self.assertEqual(store.read(digests[0]), payloads[0])
            self.assertEqual(store.read(digests[1]), payloads[1])
            self.assertEqual(store.page_blob_count, 2)

    def test_malformed_input_accepts_only_process_or_campaign_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            self.assertTrue(_reported_campaign_rejection(1, output))
            output.write_text('{"first_failure_id":"A4-IDLE-EQUALITY"}')
            self.assertTrue(_reported_campaign_rejection(0, output))
            output.write_text('{"first_failure_id":"A4-H1-TDEF-NONE"}')
            self.assertFalse(_reported_campaign_rejection(0, output))

    def test_fixture_registry_is_exact_and_contains_no_verdict_fields(self) -> None:
        predicate_ids = tuple(
            sorted(
                PREDICATE_CONTRACTS,
                key=lambda item: PREDICATE_CONTRACTS[item]["order"],
            )
        )
        self.assertEqual(tuple(FIXTURES), predicate_ids)
        self.assertEqual(len(FIXTURES), 40)
        for predicate_id, fixture in FIXTURES.items():
            self.assertEqual(
                fixture.fixture_id,
                PREDICATE_CONTRACTS[predicate_id]["reachability_fixture_id"],
            )
            reject_verdict_keys(fixture.mutation_document())
            reject_verdict_keys(asdict(fixture.parameters))

    def test_independent_dryrun_process_imports_no_analyzer_modules(self) -> None:
        tree = ast.parse((SCRIPTS / "a4_dryrun_independent.py").read_text())
        imported = {
            module
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for module in (
                [node.module] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]
            )
            if module is not None
        }
        forbidden = {
            name
            for name in imported
            if name == "a4_analysis"
            or name.startswith("a4_analysis_")
            or name in {"a4_bundle", "a4_model", "a4_terminal"}
        }
        self.assertEqual(forbidden, set())

    def test_multiple_terminals_measure_two_three_and_four(self) -> None:
        fixtures = (
            FIXTURES["A4-H1-TDEF-MULTIPLE"],
            Fixture(
                "A4-ADV-MULTIPLE-3",
                SyntheticParameters(decoy_tdef_pages={"T3_CREATE": 2}),
            ),
            Fixture(
                "A4-ADV-MULTIPLE-4",
                SyntheticParameters(decoy_tdef_pages={"T3_CREATE": 3}),
            ),
        )
        for expected, fixture in enumerate(fixtures, start=2):
            with self.subTest(expected=expected):
                result = analyze(
                    CAMPAIGN_ID,
                    PRODUCER_COMMIT,
                    FixtureInputs(fixture),
                )
                failure = next(
                    row
                    for row in result.report["predicate_results"]
                    if row["status"] == "fail"
                )
                self.assertEqual(failure["predicate_id"], "A4-H1-TDEF-MULTIPLE")
                self.assertEqual(
                    failure["predicate_measured_survivor_count"], expected
                )


if __name__ == "__main__":
    unittest.main()
