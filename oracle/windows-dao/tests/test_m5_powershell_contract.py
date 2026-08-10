from __future__ import annotations

import os
import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
MODULES = SCRIPTS / "m5"
ENTRY = SCRIPTS / "run-m5r2-controlled.ps1"
WORKER = SCRIPTS / "run-m5r2-phase.ps1"
BUNDLE = MODULES / "M5.Bundle.ps1"
CONTROLLER = MODULES / "M5.Controller.ps1"
RUNTIME = MODULES / "M5.ControllerRuntime.ps1"
QUIESCENCE = MODULES / "M5.Quiescence.ps1"
WORKER_HELPERS = MODULES / "M5.Worker.ps1"
PLAN = ROOT / "experiments" / "m5" / "m5-compact-confirm-r4.plan.json"
PRIOR_PLANS = {
    ROOT / "experiments" / "m5" / "m5-compact-confirm.plan.json":
        "beeb6277af6b7224038e5a70ee20238dce907a35f7778b2f2f21f13f1f04d0a4",
    ROOT / "experiments" / "m5" / "m5-compact-confirm-r2.plan.json":
        "7fee21985173b1c5fb9758fd98cdf60dd671eae4b98d723a400be8cf8d3ce59b",
    ROOT / "experiments" / "m5" / "m5-compact-confirm-r3.plan.json":
        "92779d51660569635872f36f3c97769b0cb4043b775751569ecd38978dc06f8a",
}
POWERSHELL = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


def ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class M5PowerShellSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entry = ENTRY.read_text(encoding="utf-8")
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.bundle = BUNDLE.read_text(encoding="utf-8")
        cls.controller = CONTROLLER.read_text(encoding="utf-8")
        cls.runtime = RUNTIME.read_text(encoding="utf-8")
        cls.quiescence = QUIESCENCE.read_text(encoding="utf-8")
        cls.plan = PLAN.read_text(encoding="utf-8")

    def test_exact_experiment_remote_and_m4_binding(self) -> None:
        combined = "\n".join((self.entry, self.worker, self.bundle, self.runtime))
        self.assertIn("DAO-M5-COMPACT-CONFIRM-004", combined)
        self.assertIn("refs/heads/codex/m5r3-timeout-bounded", combined)
        self.assertNotIn("DAO-M5-COMPACT-CONFIRM-003", combined)
        self.assertNotIn("refs/heads/codex/m5r2-m4r2-bound", combined)
        self.assertIn(
            "0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d",
            combined,
        )

    def test_transitive_exact_source_set_is_bootstrap_bound(self) -> None:
        required = (
            "m5_phase.py",
            "m5_snapshot.py",
            "m4r1_campaign.py",
            "m4r1_phase.py",
            "m4r1_snapshot.py",
            "m4r1_analysis.py",
            "validate_m1_protocol.py",
            "protocol/v1_1/environment.schema.json",
            "experiments/m4r2/m4-header-discriminator-r2.plan.json",
            "experiments/m4r2/bundle-manifest.schema.json",
            "experiments/m5r3/bundle-manifest.schema.json",
        )
        for relative in required:
            with self.subTest(relative=relative):
                self.assertIn(relative, self.entry)
                self.assertIn(relative, self.controller)

    def test_preregistered_blocked_history_is_preserved(self) -> None:
        self.assertIn('"status": "BLOCKED"', self.plan)
        self.assertIn(
            'Plan.execution_gate.status -cne "BLOCKED"', self.controller
        )
        self.assertNotIn('execution_gate.status = "READY"', self.controller)
        plan = json.loads(self.plan)
        self.assertEqual(
            plan["execution_gate"]["blocking_requirements"],
            ["windows_dao_host_bound_to_the_exact_clean_pushed_producer_commit"],
        )
        self.assertNotIn(
            "checked_m5_controller_and_isolated_phase_workers",
            plan["execution_gate"]["blocking_requirements"],
        )

    def test_prior_preregistration_artifacts_are_immutable(self) -> None:
        for path, expected in PRIOR_PLANS.items():
            with self.subTest(path=path.name):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)
        self.assertIn("m5-compact-confirm-r4.plan.json", self.controller)
        self.assertIn("m5-compact-confirm-r4.plan.json", self.entry)

    def test_every_bounded_process_timeout_is_at_most_120_seconds(self) -> None:
        sources = (
            self.entry,
            self.worker,
            self.runtime,
            self.controller,
            self.bundle,
            self.quiescence,
        )
        combined = "\n".join(sources)
        literal_timeouts = [
            int(value)
            for value in re.findall(r"-TimeoutSeconds\s+([0-9]+)", combined)
        ]
        self.assertTrue(literal_timeouts)
        self.assertLessEqual(max(literal_timeouts), 120)
        self.assertIn("$script:M5HardProcessTimeoutSeconds = 120", self.runtime)
        self.assertIn(
            "$TimeoutSeconds -gt $script:M5HardProcessTimeoutSeconds",
            self.runtime,
        )
        self.assertIn(
            "$workerTimeout -gt $script:M5HardProcessTimeoutSeconds",
            self.runtime,
        )
        self.assertNotIn("-TimeoutSeconds 180", combined)

    def test_m4_validation_precedes_any_worker_launch(self) -> None:
        validated = self.controller.index("Assert-M5M4BundleReadOnly")
        loop = self.controller.index("foreach ($sample in $plan.samples)")
        phase = self.controller.index("Invoke-M5CheckedPhase", loop)
        self.assertLess(validated, loop)
        self.assertLess(loop, phase)
        self.assertIn(
            '"validate-bundle", $root', self.runtime
        )

    def test_worker_uses_checked_decrypt_value_and_three_phases(self) -> None:
        self.assertIn("[int]$condition.compact_encryption_api_value -ne 4", WORKER_HELPERS.read_text(encoding="utf-8"))
        self.assertIn("$engine.CompactDatabase", (MODULES / "M5.Dao.ps1").read_text(encoding="utf-8"))
        for phase in ("source", "compact", "verify"):
            self.assertIn(f'"{phase}"', self.worker)

    def test_companions_are_bounded_exclusive_and_never_mutated(self) -> None:
        for fragment in (
            "[IO.FileShare]::None",
            "NumberOfLinks",
            'state = "absent"',
            'state = "present"',
            '-Role "companion"',
        ):
            self.assertIn(fragment, self.quiescence)
        for forbidden in (
            "Remove-Item",
            "[IO.File]::Delete",
            "[IO.File]::Copy",
            "[IO.File]::Move",
            "FileAccess]::Write",
            "FileMode]::Truncate",
        ):
            self.assertNotIn(forbidden, self.quiescence)

    def test_exact_uppercase_database_basenames_are_bound(self) -> None:
        helpers = WORKER_HELPERS.read_text(encoding="utf-8")
        for name in (
            "SOURCE.MDB",
            "COMPACT-INPUT.MDB",
            "COMPACTED.MDB",
            "VERIFY.MDB",
        ):
            self.assertIn(name, helpers)
        self.assertIn('EndsWith(".MDB"', self.bundle)
        self.assertIn('+ ".ldb"', self.bundle)

    def test_publication_has_exact_metadata_barrier(self) -> None:
        manifest = self.controller.rindex("Write-M5Manifest")
        barrier = self.controller.index("Wait-M5DirectoryMetadataBarrier", manifest)
        publish = self.controller.index("Publish-M1Stage", barrier)
        self.assertLess(manifest, barrier)
        self.assertLess(barrier, publish)
        self.assertIn("$stable -ge 3", self.controller)

    def test_production_files_remain_below_800_lines(self) -> None:
        for path in (ENTRY, WORKER, *MODULES.glob("*.ps1")):
            with self.subTest(path=path.name):
                self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 800)


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "Windows required")
class M5PowerShellWindowsNoComTests(unittest.TestCase):
    def run_ps(self, body: str) -> subprocess.CompletedProcess[str]:
        command = (
            "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;"
            "function Assert-M1NoReparseComponents{param($Path);"
            "$i=Get-Item -LiteralPath $Path -Force;"
            "if(($i.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne 0)"
            "{throw 'reparse'}};"
            "function Get-M1PayloadPath{param($Session,$RelativePath);"
            "[IO.Path]::GetFullPath((Join-Path $Session.StagingBundle "
            "$RelativePath.Replace('/','\\')))};"
            f". {ps_quote(BUNDLE)};. {ps_quote(WORKER_HELPERS)};" + body
        )
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )

    def test_first_manifest_insertion_accepts_empty_arraylist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-first-entry-") as temporary:
            root = Path(temporary)
            payload = root / "plan.json"
            payload.write_text("{}\n", encoding="utf-8")
            result = self.run_ps(
                f"$s=[pscustomobject]@{{StagingBundle={ps_quote(root)}}};"
                "$e=New-Object Collections.ArrayList;"
                "Add-M5ManifestEntry -Entries $e -Session $s "
                "-RelativePath 'plan.json' -Role plan;"
                "[Console]::Write(('{0}|{1}'-f $e.Count,$e[0].path))"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "1|plan.json")

    def test_path_projection_and_companion_case(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-paths-") as temporary:
            root = Path(temporary)
            result = self.run_ps(
                "$i=[pscustomobject]@{database_paths=[pscustomobject]@{"
                "source_database='evidence/samples/M5-X-01/SOURCE.MDB'}};"
                f"$p=Get-M5WorkerPaths -Invocation $i -BundleRoot {ps_quote(root)};"
                "$c=Get-M5CompanionLocator -DatabaseLocator "
                "'evidence/samples/M5-X-01/SOURCE.MDB';"
                "[Console]::Write(('{0}|{1}'-f "
                "[IO.Path]::GetFileName($p.source_database),$c))"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                "SOURCE.MDB|evidence/samples/M5-X-01/SOURCE.ldb",
            )


if __name__ == "__main__":
    unittest.main()
