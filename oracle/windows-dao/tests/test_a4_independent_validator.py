"""Executable independence and orchestration contracts for the A4 validator."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from a4_independent_bundle import BundleLoader  # noqa: E402
from a4_independent_contract import CONTRACT, EXPECTED_TAMPERS  # noqa: E402
from a4_independent_projection import logical_read_projection, verdict  # noqa: E402
from a4_independent_validator import (  # noqa: E402
    execute_tamper_suite,
    recompute_bundle,
    validate_bundle,
)
from test_a4_independent_bundle import _build_bundle  # noqa: E402


class A4IndependentImportTests(unittest.TestCase):
    def test_every_validator_module_is_closed_against_analyzer_science(self) -> None:
        forbidden = (
            "a4_analysis",
            "a4_model",
            "a4_layer",
            "a4_layers",
            "a4_generator",
            "a4_spec",
            "a4_frozen",
            "a4_derivation",
            "a4_campaign",
            "a4_catalog",
            "a4_predicate",
            "a4_terminal",
        )
        paths = sorted(SCRIPTS.glob("a4_independent_*.py"))
        self.assertGreaterEqual(len(paths), 7)
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    imports.append(node.module)
            with self.subTest(path=path.name):
                self.assertFalse(
                    any(name.startswith(forbidden) for name in imports),
                    imports,
                )

    def test_cli_rejects_conflicting_projection_modes_before_bundle_access(self) -> None:
        script = SCRIPTS / "a4_independent_validator.py"
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                "--bundle-root",
                str(ROOT / "does-not-exist"),
                "--recompute-only",
                "--pair-projection",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("mutually exclusive", completed.stderr)

    def test_cli_failure_is_truthful_diagnostic_not_acceptance_report(self) -> None:
        script = SCRIPTS / "a4_independent_validator.py"
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                "--bundle-root",
                str(ROOT / "does-not-exist"),
                "--validator-commit",
                "1" * 40,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 1)
        document = json.loads(completed.stdout)
        self.assertEqual(
            document["document_type"], "dao_a4_independent_validation_failure"
        )
        self.assertFalse(document["frozen_set_parsed"])
        self.assertEqual(document["tamper_results_executed"], [])


class A4IndependentEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="a4-independent-validator-")
        cls.bundle_root = Path(cls.temporary.name) / "bundle"
        _build_bundle(cls.bundle_root)
        cls.bundle = BundleLoader(cls.bundle_root).load()
        cls.recomputed = recompute_bundle(cls.bundle)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_exact_recomputation_and_schema_valid_verdict(self) -> None:
        self.assertEqual(self.recomputed["layers"], self.bundle.frozen["layers"])
        self.assertEqual(
            self.recomputed["qualified_pages"], self.bundle.frozen["qualified_pages"]
        )
        self.assertEqual(
            self.recomputed["work_charges"], self.bundle.frozen["work_charges"]
        )
        self.assertEqual(
            self.recomputed["predicate_results"], self.bundle.report["predicate_results"]
        )
        self.assertEqual(
            self.recomputed["holdout_results"], self.bundle.report["holdout_results"]
        )
        validate_bundle(self.bundle, self.recomputed)
        tampers = execute_tamper_suite(self.bundle, self.recomputed)
        self.assertEqual(
            [(row["id"], row["discrepancy_code"]) for row in tampers],
            list(EXPECTED_TAMPERS),
        )
        document = verdict(
            self.bundle,
            "1" * 40,
            accepted=True,
            discrepancy_codes=[],
            tamper_results=tampers,
            logical_reads=logical_read_projection(self.bundle),
        )
        CONTRACT.validate_document(document, "dao_a4_independent_validation_report")

    def test_recompute_only_does_not_evaluate_holdout(self) -> None:
        bundle = BundleLoader(self.bundle_root).load(open_holdout=False)
        self.assertEqual(bundle.page_store.logical_read_bytes(3), 0)
        recomputed = recompute_bundle(bundle, open_holdout=False)
        self.assertEqual(bundle.page_store.logical_read_bytes(3), 0)
        self.assertTrue(
            all(
                row["status"] == "not_applicable"
                for row in recomputed["holdout_results"].values()
            )
        )
        self.assertEqual(recomputed["layers"], bundle.frozen["layers"])

    def test_normal_cli_writes_an_accepted_report_outside_bundle(self) -> None:
        output = Path(self.temporary.name) / "independent-report.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "a4_independent_validator.py"),
                "--bundle-root",
                str(self.bundle_root),
                "--validator-commit",
                "1" * 40,
                "--output",
                str(output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(document["accepted"])
        CONTRACT.validate_document(document, "dao_a4_independent_validation_report")


if __name__ == "__main__":
    unittest.main()
