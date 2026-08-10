import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "m1" / "M1.Publication.ps1"
PATH_MODULE = ROOT / "scripts" / "m1" / "M1.PublicationPaths.ps1"
POWERSHELL = Path(
    os.environ.get(
        "WINDIR", r"C:\Windows"
    )
) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
COMMIT = "1" * 40
RUN_ID = "20260724T120000Z-m1-publication"


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


class M1PublicationTests(unittest.TestCase):
    maxDiff = None

    def run_ps(self, body, env=None):
        if not POWERSHELL.is_file():
            self.skipTest("Windows PowerShell is unavailable")
        command = (
            "$ErrorActionPreference='Stop';"
            "Set-StrictMode -Version Latest;"
            f". {ps_quote(MODULE)};"
            + body
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
            env=env,
        )

    def make_roots(self, temporary):
        base = Path(temporary)
        repository = base / "repository"
        output = base / "evidence"
        repository.mkdir()
        return repository, output

    def invocation(
        self,
        repository,
        output,
        *,
        fault_phase=None,
        validation_fails=False,
        recheck="$true",
        max_file=1024,
        max_total=4096,
        build=None,
    ):
        if build is None:
            build = (
                "$build={param($s)"
                "Write-M1DurableUtf8 -Session $s "
                "-RelativePath 'payload.json' -Text (\"{\"\"ok\"\":true}`n\");"
                "Write-M1DurableUtf8 -Session $s "
                "-RelativePath 'bundle-manifest.json' -Text '{}';"
                "};"
            )
        validator = (
            "$validate={param($bundle)throw 'synthetic validation failure'};"
            if validation_fails
            else (
                "$validate={param($bundle)"
                "if(-not (Test-Path -LiteralPath "
                "(Join-Path $bundle 'bundle-manifest.json') -PathType Leaf))"
                "{throw 'manifest absent'}};"
            )
        )
        fault = "$fault=$null;"
        if fault_phase is not None:
            fault = (
                f"$fault={{param($phase,$s)if($phase -ceq "
                f"{ps_quote(fault_phase)}){{throw 'fault:{fault_phase}'}}}};"
            )
        return (
            build
            + validator
            + f"$recheck={{param($s){recheck}}};"
            + fault
            + "Invoke-M1AtomicPublication "
            + f"-RepositoryRoot {ps_quote(repository)} "
            + f"-OutputRoot {ps_quote(output)} "
            + f"-GitCommit '{COMMIT}' -RunId '{RUN_ID}' "
            + "-BuildBundleScriptBlock $build "
            + "-RecheckScriptBlock $recheck "
            + "-ValidationScriptBlock $validate "
            + f"-MaxFileBytes {max_file} -MaxTotalBytes {max_total} "
            + "-FaultInjector $fault;"
        )

    def assert_no_staging(self, output):
        if output.exists():
            self.assertEqual(
                list(output.glob(".m1-stage-*")),
                [],
                "private staging directory leaked",
            )

    def test_happy_path_is_private_same_volume_and_has_one_commit_point(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, output = self.make_roots(temporary)
            script = self.invocation(repository, output)
            script += (
                f"$final=Join-Path (Join-Path {ps_quote(output)} '{COMMIT}') "
                f"'{RUN_ID}';"
                "$result=[ordered]@{"
                "final=(Test-Path -LiteralPath $final -PathType Container);"
                "payload=(Get-Content -LiteralPath "
                "(Join-Path $final 'payload.json') -Raw);"
                "stages=@(Get-ChildItem -LiteralPath "
                f"{ps_quote(output)} -Force -Filter '.m1-stage-*').Count"
                "};$result|ConvertTo-Json -Compress"
            )
            result = self.run_ps(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(observed["final"])
            payload = observed["payload"]
            if isinstance(payload, dict):
                payload = payload["value"]
            self.assertEqual(payload, '{"ok":true}\n')
            self.assertEqual(observed["stages"], 0)
            final = output / COMMIT / RUN_ID
            self.assertEqual(final.drive.lower(), output.drive.lower())
            self.assertFalse(any(".m1-stage-" in part for part in final.parts))

    def test_existing_final_collision_is_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, output = self.make_roots(temporary)
            final = output / COMMIT / RUN_ID
            final.mkdir(parents=True)
            sentinel = final / "sentinel.txt"
            sentinel.write_text("retained", encoding="utf-8")
            script = (
                "try{"
                + self.invocation(repository, output)
                + "exit 9}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 7}"
            )
            result = self.run_ps(script)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertIn("already exists", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "retained")
            self.assert_no_staging(output)

    def test_faults_before_commit_clean_staging_and_publish_nothing(self):
        phases = (
            "after_stage_created",
            "before_file_create",
            "before_file_flush",
            "after_file_flush",
            "before_validation",
            "after_validation",
            "after_recheck",
        )
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                repository, output = self.make_roots(temporary)
                script = (
                    "try{"
                    + self.invocation(
                        repository, output, fault_phase=phase
                    )
                    + "exit 9}catch{exit 7}"
                )
                result = self.run_ps(script)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertFalse((output / COMMIT / RUN_ID).exists())
                self.assert_no_staging(output)

    def test_validation_and_recheck_failure_clean_staging(self):
        cases = (
            {"validation_fails": True},
            {"recheck": "$false"},
            {"recheck": "throw 'recheck failure'"},
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                repository, output = self.make_roots(temporary)
                script = (
                    "try{"
                    + self.invocation(repository, output, **case)
                    + "exit 9}catch{exit 7}"
                )
                result = self.run_ps(script)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertFalse((output / COMMIT / RUN_ID).exists())
                self.assert_no_staging(output)

    def test_racing_destination_is_not_overwritten_and_stage_is_cleaned(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, output = self.make_roots(temporary)
            fault = (
                "$fault={param($phase,$s)"
                "if($phase -ceq 'before_move'){"
                "[IO.Directory]::CreateDirectory($s.FinalDirectory)|Out-Null;"
                "[IO.File]::WriteAllText("
                "(Join-Path $s.FinalDirectory 'sentinel.txt'),'race')}};"
            )
            script = self.invocation(repository, output).replace(
                "$fault=$null;", fault
            )
            script = f"try{{{script}exit 9}}catch{{exit 7}}"
            result = self.run_ps(script)
            self.assertEqual(result.returncode, 7, result.stderr)
            final = output / COMMIT / RUN_ID
            self.assertEqual(
                (final / "sentinel.txt").read_text(encoding="utf-8"), "race"
            )
            self.assert_no_staging(output)

    def test_commit_parent_reparse_replacement_is_rejected_before_move(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, output = self.make_roots(temporary)
            target = Path(temporary) / "redirect-target"
            target.mkdir()
            fault = (
                "$fault={param($phase,$s)"
                "if($phase -ceq 'before_move'){"
                "[IO.Directory]::Delete($s.CommitDirectory,$false);"
                f"New-Item -ItemType Junction -Path $s.CommitDirectory "
                f"-Target {ps_quote(target)}|Out-Null"
                "}};"
            )
            script = self.invocation(repository, output).replace(
                "$fault=$null;", fault
            )
            script = f"try{{{script}exit 9}}catch{{exit 7}}"
            result = self.run_ps(script)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse((target / RUN_ID).exists())
            self.assert_no_staging(output)

    def test_per_file_and_total_byte_ceilings_fail_closed(self):
        builds = (
            (
                "$build={param($s)"
                "Write-M1DurableUtf8 -Session $s -RelativePath 'big' "
                "-Text '12345'};",
                4,
                20,
            ),
            (
                "$build={param($s)"
                "Write-M1DurableUtf8 -Session $s -RelativePath 'a' "
                "-Text '1234';"
                "Write-M1DurableUtf8 -Session $s -RelativePath 'b' "
                "-Text '5678'};",
                10,
                7,
            ),
        )
        for build, max_file, max_total in builds:
            with self.subTest(build=build), tempfile.TemporaryDirectory() as temporary:
                repository, output = self.make_roots(temporary)
                script = (
                    "try{"
                    + self.invocation(
                        repository,
                        output,
                        build=build,
                        max_file=max_file,
                        max_total=max_total,
                    )
                    + "exit 9}catch{exit 7}"
                )
                result = self.run_ps(script)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertFalse((output / COMMIT / RUN_ID).exists())
                self.assert_no_staging(output)

    def test_durable_copy_and_closed_database_sync_publish_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, output = self.make_roots(temporary)
            source = Path(temporary) / "source.json"
            source.write_bytes(b"copied-exactly")
            build = (
                "$build={param($s)"
                f"Copy-M1DurableFile -Session $s -SourcePath "
                f"{ps_quote(source)} -RelativePath 'copied.json';"
                "$database=Join-Path $s.BundlePath 'databases/fake.mdb';"
                "[IO.Directory]::CreateDirectory("
                "[IO.Path]::GetDirectoryName($database))|Out-Null;"
                "[IO.File]::WriteAllBytes($database,[byte[]](1,2,3,4));"
                "Sync-M1DurableFile -Session $s "
                "-RelativePath 'databases/fake.mdb';"
                "Write-M1DurableUtf8 -Session $s "
                "-RelativePath 'bundle-manifest.json' -Text '{}';"
                "};"
            )
            result = self.run_ps(
                self.invocation(repository, output, build=build)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            final = output / COMMIT / RUN_ID
            self.assertEqual(
                (final / "copied.json").read_bytes(), b"copied-exactly"
            )
            self.assertEqual(
                (final / "databases" / "fake.mdb").read_bytes(),
                b"\x01\x02\x03\x04",
            )
            self.assert_no_staging(output)

    def test_create_new_refuses_duplicate_payload_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, output = self.make_roots(temporary)
            build = (
                "$build={param($s)"
                "Write-M1DurableUtf8 -Session $s -RelativePath 'same' "
                "-Text 'first';"
                "Write-M1DurableUtf8 -Session $s -RelativePath 'same' "
                "-Text 'second'};"
            )
            script = (
                "try{"
                + self.invocation(repository, output, build=build)
                + "exit 9}catch{exit 7}"
            )
            result = self.run_ps(script)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse((output / COMMIT / RUN_ID).exists())
            self.assert_no_staging(output)

    def test_output_inside_repository_and_ads_are_rejected_without_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, output = self.make_roots(temporary)
            bad_outputs = (
                repository / "artifacts",
                Path(str(output) + ":stream"),
            )
            for bad in bad_outputs:
                with self.subTest(output=bad):
                    script = (
                        "try{"
                        + self.invocation(repository, bad)
                        + "exit 9}catch{exit 7}"
                    )
                    result = self.run_ps(script)
                    self.assertEqual(result.returncode, 7, result.stderr)
                    self.assertFalse((repository / "artifacts").exists())
                    self.assert_no_staging(output)

    def test_private_stage_acl_grants_only_current_user(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, output = self.make_roots(temporary)
            script = (
                f"$s=New-M1PublicationSession -RepositoryRoot "
                f"{ps_quote(repository)} -OutputRoot {ps_quote(output)} "
                f"-GitCommit '{COMMIT}' -RunId '{RUN_ID}';"
                "$acl=(New-Object IO.DirectoryInfo("
                "$s.StagingRoot)).GetAccessControl();"
                "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
                "$rules=@($acl.Access|ForEach-Object{$_.IdentityReference.Translate("
                "[Security.Principal.SecurityIdentifier]).Value}|Select-Object -Unique);"
                "$result=[ordered]@{protected=$acl.AreAccessRulesProtected;"
                "rules=$rules;sid=$sid};"
                "Remove-M1PublicationStaging -Session $s;"
                "$result|ConvertTo-Json -Compress"
            )
            result = self.run_ps(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(observed["protected"])
            rules = observed["rules"]
            if isinstance(rules, str):
                rules = [rules]
            self.assertEqual(rules, [observed["sid"]])
            self.assert_no_staging(output)

    def test_source_contract_has_durability_validation_and_single_move(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn("[IO.FileMode]::CreateNew", source)
        self.assertIn("[IO.FileOptions]::WriteThrough", source)
        self.assertGreaterEqual(source.count("$stream.Flush($true)"), 2)
        self.assertIn("$output.Flush($true)", source)
        self.assertIn("& $python -B $validator bundle $Session.StagingBundle", source)
        self.assertIn("Directory-handle fsync", source)
        self.assertIn("power loss", source)
        self.assertEqual(
            len(re.findall(r"\[IO\.Directory\]::Move\(", source)),
            1,
        )
        move = source.index("[IO.Directory]::Move(")
        commit_comment = source.index("sole publication commit point")
        self.assertLess(commit_comment, move)

    def test_drive_root_is_rejected_without_drive_relative_normalization(self):
        source = PATH_MODULE.read_text(encoding="utf-8")
        self.assertIn("Drive-root publication paths are forbidden.", source)
        result = self.run_ps(
            "$root=[IO.Path]::GetPathRoot((Get-Location).Path);"
            "try { Get-M1FullPath -Path $root | Out-Null; exit 9 }"
            "catch { [Console]::WriteLine($_.Exception.Message); exit 0 }"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Drive-root publication paths are forbidden", result.stdout)


if __name__ == "__main__":
    unittest.main()
