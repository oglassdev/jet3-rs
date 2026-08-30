from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dao_allocation_a9 as a9  # noqa: E402


ACQUISITION = ROOT / "oracle" / "windows-dao" / "acquisition"
PLAN = ACQUISITION / "a9-allocation.plan.json"
SYNTHETIC = ACQUISITION / "a9-allocation.synthetic.json"
GENERATOR = SCRIPTS / "Invoke-DaoAllocationA9.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "windows-dao-allocation-a9.yml"
PROVENANCE = ROOT / "docs" / "PROVENANCE.md"
APPROVED_PLAN_SHA256 = (
    "045f25cdeec93060776ab494e9a7c462ebee634ce533e96074c5e0070ab17ea8"
)
ACCEPTED_REPORT_SHA256 = (
    "75d6b39351c1e13c18039e416464fe28224a7c75ce837136a6a6759371388151"
)
ACCEPTED_SOURCE_REVISION = "e6a7b2c24afa2ef386031a2e70cdedb120180a3e"


def mutate_map_bit(
    manifest: dict,
    root: Path,
    *,
    replica: int,
    checkpoint_name: str,
    row: int,
    page: int,
    set_bit: bool,
) -> None:
    entry = next(
        item
        for item in manifest["checkpoints"]
        if item["replica"] == replica
        and item["question"] == "Q4"
        and item["name"] == checkpoint_name
    )
    document = a9.load_json(root / entry["path"])
    map_image = next(item for item in document["pages"] if item["page"] == 21)
    image = bytearray.fromhex(map_image["hex"])
    start = int.from_bytes(image[10 + 2 * row : 12 + 2 * row], "little") & 0x1FFF
    base = int.from_bytes(image[start + 1 : start + 5], "little")
    relative = page - base
    if relative < 0:
        raise AssertionError("test page precedes the type-0 map base")
    byte = start + 5 + relative // 8
    mask = 1 << (relative % 8)
    if set_bit:
        image[byte] |= mask
    else:
        image[byte] &= ~mask
    encoded = bytes(image)
    map_image["hex"] = encoded.hex()
    map_image["sha256"] = a9.sha256(encoded)
    raw = a9.canonical_bytes(document)
    (root / entry["path"]).write_bytes(raw)
    entry["sha256"] = a9.sha256(raw)


class A9PlanTests(unittest.TestCase):
    def test_consumed_plan_remains_immutable(self) -> None:
        plan_bytes = PLAN.read_bytes()
        self.assertEqual(a9.sha256(plan_bytes), APPROVED_PLAN_SHA256)
        plan = json.loads(plan_bytes)
        self.assertEqual(plan["issue"], 99)
        self.assertEqual(plan["execution"]["attempts"], 1)
        self.assertEqual(plan["execution"]["replicas"], 3)
        self.assertFalse(plan["publication"]["mdb_bytes_committed"])
        for relative in (
            ".github/workflows/windows-dao-allocation-a9.yml",
            "oracle/windows-dao/scripts/Invoke-DaoAllocationA9.ps1",
            "oracle/windows-dao/scripts/dao_allocation_a9.py",
            "oracle/windows-dao/scripts/probe-provider.ps1",
        ):
            self.assertIn(relative, plan["inputs"])

    def test_generator_is_bounded_and_covers_every_question(self) -> None:
        generator = GENERATOR.read_text(encoding="utf-8")
        for name in ("$MaximumRows", "$MaximumPages", "$MaximumTaggedPages", "Assert-Budget"):
            self.assertIn(name, generator)
        for question in a9.QUESTIONS:
            self.assertIn(f'-Question "{question}"', generator)
        self.assertIn("dao_allocation_a9_manifest", generator)
        self.assertNotIn("Invoke-Expression", generator)
        self.assertNotIn("CompactDatabase", generator)


class A9EvaluatorTests(unittest.TestCase):
    def test_synthetic_report_is_reproducible_and_non_evidentiary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary) / "synthetic.json"
            a9.synthetic_dry_run(generated)
            self.assertEqual(generated.read_bytes(), SYNTHETIC.read_bytes())
        report = json.loads(SYNTHETIC.read_text(encoding="utf-8"))
        self.assertFalse(report["compatibility_claim"])
        self.assertTrue(report["consistent_input_accepted"])
        self.assertTrue(report["inconsistent_input_rejected"])

    def test_replica_disagreement_is_no_outcome_not_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            entry = next(
                item
                for item in a9.load_json(manifest_path)["checkpoints"]
                if item["replica"] == 3 and item["question"] == "Q3" and item["name"] == "04-reinsert-4"
            )
            document = a9.load_json(root / entry["path"])
            # Replica 3 appends a page instead of reusing one: verdicts differ.
            document["page_count"] += 1
            document["pages"].append(
                {"page": 30, "sha256": a9.sha256(bytes([1]) + bytes(2047)), "hex": (bytes([1]) + bytes(2047)).hex()}
            )
            raw = a9.canonical_bytes(document)
            (root / entry["path"]).write_bytes(raw)
            manifest = a9.load_json(manifest_path)
            for item in manifest["checkpoints"]:
                if item["path"] == entry["path"]:
                    item["sha256"], item["page_count"] = a9.sha256(raw), document["page_count"]
            a9.write_canonical(manifest_path, manifest)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(report["questions"]["Q3"]["status"], "no_outcome")
        self.assertEqual(report["questions"]["Q1"]["status"], "answered")

    def test_uncaptured_indirect_reference_is_no_outcome(self) -> None:
        with self.assertRaisesRegex(a9.NoOutcome, "reference 1001 was not captured"):
            a9.usage_record(a9.SyntheticDatabase.type1([1001]), {})

    def test_complete_manifest_requires_exact_preregistered_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            manifest = a9.load_json(manifest_path)
            manifest["checkpoints"].pop()
            a9.write_canonical(manifest_path, manifest)
            with self.assertRaisesRegex(a9.EvaluationError, "every preregistered checkpoint"):
                a9.evaluate(manifest_path, root, root / "report.json")

    def test_manifest_cannot_select_the_q5_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            manifest = a9.load_json(manifest_path)
            manifest["memo_marker_hex"] = ""
            a9.write_canonical(manifest_path, manifest)
            with self.assertRaisesRegex(a9.EvaluationError, "memo marker differs"):
                a9.evaluate(manifest_path, root, root / "report.json")

    def test_q2_requires_both_preregistered_page_growth_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            manifest = a9.load_json(manifest_path)
            for entry in manifest["checkpoints"]:
                if entry["question"] == "Q2" and entry["name"] == "01-table-created":
                    empty = next(
                        item
                        for item in manifest["checkpoints"]
                        if item["replica"] == entry["replica"]
                        and item["question"] == "Q2"
                        and item["name"] == "00-empty"
                    )
                    document = a9.load_json(root / empty["path"])
                    document["name"] = entry["name"]
                    raw = a9.canonical_bytes(document)
                    (root / entry["path"]).write_bytes(raw)
                    entry["page_count"] = document["page_count"]
                    entry["sha256"] = a9.sha256(raw)
            a9.write_canonical(manifest_path, manifest)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        self.assertEqual(report["status"], "no_outcome")
        self.assertIn("page count did not grow", report["questions"]["Q2"]["reason"])

    def test_q4_classifies_global_and_primary_map_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        transitions = report["questions"]["Q4"]["answer"]["replicas"][0]["transitions"]
        self.assertEqual(
            [transition["classification"] for transition in transitions],
            ["other_type05_growth", "primary_owned_map_extension"],
        )
        self.assertEqual(transitions[0]["new_type05_pages"], [1001])
        self.assertEqual(transitions[1]["new_type05_pages"], [16353])

    def test_q4_requires_at_least_one_primary_owned_map_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            manifest = a9.load_json(manifest_path)
            for replica in range(1, a9.REPLICAS + 1):
                before_entry = next(
                    item
                    for item in manifest["checkpoints"]
                    if item["replica"] == replica
                    and item["question"] == "Q4"
                    and item["name"] == "03-before-second-type05"
                )
                after_entry = next(
                    item
                    for item in manifest["checkpoints"]
                    if item["replica"] == replica
                    and item["question"] == "Q4"
                    and item["name"] == "04-after-second-type05"
                )
                before = a9.load_json(root / before_entry["path"])
                after = a9.load_json(root / after_entry["path"])
                before_map = next(page for page in before["pages"] if page["page"] == 21)
                after["pages"] = [
                    before_map if page["page"] == 21 else page for page in after["pages"]
                ]
                raw = a9.canonical_bytes(after)
                (root / after_entry["path"]).write_bytes(raw)
                after_entry["sha256"] = a9.sha256(raw)
            a9.write_canonical(manifest_path, manifest)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(report["questions"]["Q4"]["status"], "no_outcome")
        self.assertIn("neither captured transition", report["questions"]["Q4"]["reason"])

    def test_q4_primary_extension_must_reference_the_new_type05_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            manifest = a9.load_json(manifest_path)
            for replica in range(1, a9.REPLICAS + 1):
                entry = next(
                    item
                    for item in manifest["checkpoints"]
                    if item["replica"] == replica
                    and item["question"] == "Q4"
                    and item["name"] == "04-after-second-type05"
                )
                document = a9.load_json(root / entry["path"])
                map_page = next(page for page in document["pages"] if page["page"] == 21)
                image = bytearray.fromhex(map_page["hex"])
                owned_start = int.from_bytes(image[10:12], "little") & 0x1FFF
                self.assertEqual(image[owned_start], 0x01)
                image[owned_start + 1 : owned_start + 5] = (1001).to_bytes(4, "little")
                encoded = bytes(image)
                map_page["hex"] = encoded.hex()
                map_page["sha256"] = a9.sha256(encoded)
                raw = a9.canonical_bytes(document)
                (root / entry["path"]).write_bytes(raw)
                entry["sha256"] = a9.sha256(raw)
            a9.write_canonical(manifest_path, manifest)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(report["questions"]["Q4"]["status"], "no_outcome")
        self.assertIn("neither captured transition", report["questions"]["Q4"]["reason"])

    def test_q4_requires_replica_consistent_free_map_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            manifest = a9.load_json(manifest_path)
            mutate_map_bit(
                manifest,
                root,
                replica=3,
                checkpoint_name="00-created",
                row=1,
                page=21,
                set_bit=True,
            )
            a9.write_canonical(manifest_path, manifest)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        self.assertEqual(report["status"], "no_outcome")
        self.assertIn("replicas disagree", report["questions"]["Q4"]["reason"])

    def test_q4_requires_replica_consistent_free_map_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            manifest = a9.load_json(manifest_path)
            for replica in range(1, a9.REPLICAS + 1):
                mutate_map_bit(
                    manifest,
                    root,
                    replica=replica,
                    checkpoint_name="00-created",
                    row=1,
                    page=20,
                    set_bit=True,
                )
            mutate_map_bit(
                manifest,
                root,
                replica=3,
                checkpoint_name="00-created",
                row=1,
                page=20,
                set_bit=False,
            )
            mutate_map_bit(
                manifest,
                root,
                replica=3,
                checkpoint_name="00-created",
                row=1,
                page=21,
                set_bit=True,
            )
            a9.write_canonical(manifest_path, manifest)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        self.assertEqual(report["status"], "no_outcome")
        self.assertIn("replicas disagree", report["questions"]["Q4"]["reason"])

    def test_q4_rejects_mapped_page_outside_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            manifest = a9.load_json(manifest_path)
            mutate_map_bit(
                manifest,
                root,
                replica=1,
                checkpoint_name="00-created",
                row=0,
                page=22,
                set_bit=True,
            )
            a9.write_canonical(manifest_path, manifest)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        self.assertEqual(report["status"], "no_outcome")
        self.assertIn("outside the checkpoint", report["questions"]["Q4"]["reason"])

    def test_q4_rejects_free_page_not_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            manifest = a9.load_json(manifest_path)
            mutate_map_bit(
                manifest,
                root,
                replica=1,
                checkpoint_name="00-created",
                row=0,
                page=20,
                set_bit=False,
            )
            mutate_map_bit(
                manifest,
                root,
                replica=1,
                checkpoint_name="00-created",
                row=1,
                page=20,
                set_bit=True,
            )
            a9.write_canonical(manifest_path, manifest)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        self.assertEqual(report["status"], "no_outcome")
        self.assertIn("not present in the owned map", report["questions"]["Q4"]["reason"])

    def test_row_directory_rejects_deleted_and_overflow_rows(self) -> None:
        page = bytearray(a9.PAGE_SIZE)
        page[0] = 0x01
        page[8:10] = (1).to_bytes(2, "little")
        page[10:12] = (0x8000 | 2040).to_bytes(2, "little")
        self.assertIsNone(a9.row_bytes(bytes(page), 0))
        page[10:12] = (2040).to_bytes(2, "little")
        self.assertEqual(len(a9.row_bytes(bytes(page), 0)), 8)


class A9WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_is_manual_only_with_read_only_permissions(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("push:", self.workflow)
        self.assertNotIn("pull_request:", self.workflow)
        self.assertRegex(self.workflow, r"permissions:\n  contents: read\n")
        self.assertIn("windows-2022", self.workflow)

    def test_gating_matches_the_read_differential(self) -> None:
        for token in (
            "accept_microsoft_access_runtime_license:",
            "run_acquisition:",
            "approve_acquisition:",
            "plan_sha256:",
            "a9-allocation.plan.json",
            "dao_allocation_a9.py plan",
            "Get-AuthenticodeSignature",
            "Invoke-DaoAllocationA9.ps1",
            "dao_allocation_a9.py evaluate",
            "if-no-files-found: error",
            'GITHUB_RUN_ATTEMPT -cne "1"',
            "WaitForExit(7200 * 1000)",
            "--expected-source-revision",
            "--expected-plan-sha256",
        ):
            self.assertIn(token, self.workflow)
        self.assertLess(
            self.workflow.index("Verify preregistration and human approval"),
            self.workflow.index("Probe the untouched runner image"),
        )
        self.assertNotIn("cargo", self.workflow)
        uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", self.workflow, re.MULTILINE)
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_redirected_generator_caches_handle_before_reading_exit_code(self) -> None:
        start = self.workflow.index("$generator = Start-Process")
        process_try = self.workflow.index("try {", start)
        cache_handle = self.workflow.index("$null = $generator.Handle", process_try)
        timed_wait = self.workflow.index(
            "$generator.WaitForExit(7200 * 1000)", cache_handle
        )
        drain_redirects = self.workflow.index("$generator.WaitForExit()", timed_wait)
        read_exit = self.workflow.index(
            "$generatorExitCode = $generator.ExitCode", drain_redirects
        )
        self.assertLess(start, process_try)
        self.assertLess(process_try, cache_handle)
        self.assertLess(cache_handle, timed_wait)
        self.assertLess(timed_wait, drain_redirects)
        self.assertLess(drain_redirects, read_exit)
        self.assertIn("if ($null -eq $generatorExitCode)", self.workflow)
        self.assertIn("if ($generatorExitCode -ne 0)", self.workflow)
        self.assertNotIn("if ($generator.ExitCode -ne 0)", self.workflow)


class A9ResultTests(unittest.TestCase):
    def test_accepted_result_is_bound_once_in_provenance(self) -> None:
        provenance = PROVENANCE.read_text(encoding="utf-8")
        marker = "### EXP-0065 — Accepted hosted A9 writer-allocation observations"
        self.assertEqual(provenance.count(marker), 1)
        entry = provenance.split(marker, 1)[1].split("\n## Fixtures", 1)[0]
        for token in (
            "33338088173",
            APPROVED_PLAN_SHA256,
            ACCEPTED_REPORT_SHA256,
            ACCEPTED_SOURCE_REVISION,
            "status = accepted",
            "all Q1-Q5 statuses",
            "No retry or redispatch occurred",
        ):
            self.assertIn(token, entry)


if __name__ == "__main__":
    unittest.main()
