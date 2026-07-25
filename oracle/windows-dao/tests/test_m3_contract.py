from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

SPEC = importlib.util.spec_from_file_location(
    "m3_contract", SCRIPTS / "m3_contract.py"
)
M3 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(M3)

import test_validate_m1_protocol as M1_TEST  # noqa: E402

COMMIT = "1" * 40
RUN_ID = "20260725T120000Z-m3-test"
TIMESTAMP = "2026-07-25T12:00:00+00:00"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def symlink_or_skip(
    case: unittest.TestCase,
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            case.skipTest("Windows symlink privilege is unavailable")
        raise


class M3ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = M3.load_json(M3.M3 / "m3-index-isolation.plan.json")

    def _database(self, condition: str, replica: int) -> bytes:
        pages = {"E": 20, "B": 24, "I": 25}[condition]
        fill = {"E": 0x00, "B": 0x10, "I": 0x20}[condition]
        value = bytearray([fill] * (pages * M3.PAGE_SIZE))
        value[100 + replica] ^= replica
        return bytes(value)

    def _build_bundle(
        self, root: Path, *, recorded_windows_paths: bool = False
    ) -> Path:
        bundle = root / COMMIT / RUN_ID
        bundle.mkdir(parents=True)
        (bundle / "plan.json").write_bytes(
            (M3.M3 / "m3-index-isolation.plan.json").read_bytes()
        )
        write_json(bundle / "environment.json", M1_TEST.ready_environment())
        samples = []
        retained_databases: set[str] = set()
        for sample in self.plan["samples"]:
            sample_id = sample["sample_id"]
            condition = sample["condition_id"]
            scenario_name = next(
                item["scenario_path"].split("/")[-1]
                for item in self.plan["conditions"]
                if item["condition_id"] == condition
            )
            scenario = M1_TEST.load_example(scenario_name)
            database = self._database(condition, sample["replica"])
            digest = hashlib.sha256(database).hexdigest()
            database_relative = f"databases/{digest}.mdb"
            if database_relative not in retained_databases:
                path = bundle / database_relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(database)
                retained_databases.add(database_relative)
            snapshot = M1_TEST.snapshot_for_scenario(scenario, digest)
            snapshot["producer"]["source_revision"] = COMMIT
            snapshot_relative = f"samples/{sample_id}/dao-snapshot.json"
            write_json(bundle / snapshot_relative, snapshot)
            operation_log = M1_TEST.operation_log_for_scenario(scenario)
            operation_log["git_commit"] = COMMIT
            operation_log["run_id"] = (
                f"20260725T120000Z-m3-w{sample['launch_ordinal']:02d}"
            )
            log_relative = f"samples/{sample_id}/operation-log.json"
            write_json(bundle / log_relative, operation_log)
            invocation_relative = f"samples/{sample_id}/invocation.json"
            condition_record = next(
                item
                for item in self.plan["conditions"]
                if item["condition_id"] == condition
            )
            if recorded_windows_paths:
                repository_root = r"C:\Users\tester\Development\jet3-rs"
                stage_root = r"C:\Users\tester\AppData\Local\Temp\m3-stage"
                working_path = stage_root + rf"\working\{sample_id}"
                environment_path = (
                    r"C:\Users\tester\AppData\Local\Temp\dao-environment.json"
                )
                output_root = stage_root + rf"\worker-preflight\{sample_id}"
                plan_path = (
                    repository_root
                    + r"\oracle\windows-dao\experiments\m3"
                    + r"\m3-index-isolation.plan.json"
                )
                scenario_path = repository_root + "\\" + condition_record[
                    "scenario_path"
                ].replace("/", "\\")
                result_path = working_path + r"\result.json"
            else:
                working = root / "private-stage" / sample_id / "working"
                repository_root = str(M3.REPOSITORY)
                stage_root = str(root / "private-stage")
                working_path = str(working)
                environment_path = str(bundle / "environment.json")
                output_root = str(root / "private-stage" / "worker-preflight")
                plan_path = str(M3.M3 / "m3-index-isolation.plan.json")
                scenario_path = str(
                    M3.REPOSITORY / condition_record["scenario_path"]
                )
                result_path = str(working / "result.json")
            invocation = {
                "block": sample["block"],
                "campaign_run_id": RUN_ID,
                "condition_id": condition,
                "environment_path": environment_path,
                "environment_sha256": sha256(bundle / "environment.json"),
                "git_commit": COMMIT,
                "launch_nonce": (
                    f"00000000-0000-0000-0000-{sample['launch_ordinal']:012d}"
                ),
                "launch_ordinal": sample["launch_ordinal"],
                "output_root": output_root,
                "plan_path": plan_path,
                "plan_sha256": sha256(bundle / "plan.json"),
                "remote_ref": self.plan["remote_ref"],
                "replica": sample["replica"],
                "repository_root": repository_root,
                "repository_url": self.plan["repository_url"],
                "result_path": result_path,
                "run_id": operation_log["run_id"],
                "sample_id": sample_id,
                "scenario_id": condition_record["scenario_id"],
                "scenario_path": scenario_path,
                "scenario_sha256": condition_record["scenario_sha256"],
                "stage_root": stage_root,
                "working_path": working_path,
            }
            write_json(bundle / invocation_relative, invocation)
            accepted = M1_TEST.ready_environment()["accepted_provider"]
            record = {
                "block": sample["block"],
                "condition_id": condition,
                "database": {
                    "path": database_relative,
                    "sha256": digest,
                    "size_bytes": len(database),
                },
                "document_type": "dao_m3_sample_record",
                "git_commit": COMMIT,
                "invocation": {
                    "path": invocation_relative,
                    "sha256": sha256(bundle / invocation_relative),
                },
                "launch_nonce": f"00000000-0000-0000-0000-{sample['launch_ordinal']:012d}",
                "launch_ordinal": sample["launch_ordinal"],
                "operation_log": {
                    "path": log_relative,
                    "sha256": sha256(bundle / log_relative),
                },
                "process": {
                    "architecture": "x86",
                    "id": 1000 + sample["launch_ordinal"],
                    "powershell_version": M1_TEST.ready_environment()["runtime"][
                        "powershell_version"
                    ],
                    "provider_clsid": "{00000100-0000-0010-8000-00AA006D2EA4}",
                    "provider_prog_id": "DAO.DBEngine.36",
                    "provider_server_path": accepted["server_path"],
                    "provider_server_sha256": "3" * 64,
                    "started_at_utc": (
                        f"2026-07-25T12:00:{sample['launch_ordinal']:02d}+00:00"
                    ),
                },
                "protocol_version": "1.0.0",
                "replica": sample["replica"],
                "run_id": RUN_ID,
                "sample_id": sample_id,
                "scenario_id": condition_record["scenario_id"],
                "scenario_sha256": condition_record["scenario_sha256"],
                "snapshot": {
                    "path": snapshot_relative,
                    "sha256": sha256(bundle / snapshot_relative),
                },
                "status": "pass",
                "worker_run_id": operation_log["run_id"],
            }
            write_json(bundle / f"samples/{sample_id}/record.json", record)
            samples.append(
                {"database_sha256": digest, "sample_id": sample_id, "status": "pass"}
            )
        summary, masks = M3.build_analysis(bundle, self.plan)
        write_json(bundle / "analysis/summary.json", summary)
        for relative, value in masks.items():
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        report = {
            "comparison_count": 18,
            "document_type": "dao_m3_report",
            "environment_sha256": sha256(bundle / "environment.json"),
            "git_commit": COMMIT,
            "plan_sha256": sha256(bundle / "plan.json"),
            "remote_ref": "refs/heads/codex/windows-dao-oracle",
            "repository_url": "https://github.com/oglassdev/jet3-rs.git",
            "run_id": RUN_ID,
            "sample_count": 9,
            "samples": samples,
            "status": "pass",
        }
        write_json(bundle / "report.json", report)
        files = []
        payload_paths = [
            path
            for path in bundle.rglob("*")
            if path.is_file() and path.name != "bundle-manifest.json"
        ]
        payload_paths.sort(key=lambda path: path.relative_to(bundle).as_posix())
        for path in payload_paths:
            relative = path.relative_to(bundle).as_posix()
            expected = M3._expected_role(relative)
            assert expected is not None
            role, media = expected
            files.append(
                {
                    "media_type": media,
                    "path": relative,
                    "role": role,
                    "sha256": sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
        manifest = {
            "created_at_utc": TIMESTAMP,
            "dirty": False,
            "document_type": "dao_m3_campaign_manifest",
            "files": files,
            "git_commit": COMMIT,
            "plan": {"path": "plan.json", "sha256": sha256(bundle / "plan.json")},
            "protocol_version": "1.0.0",
            "report_path": "report.json",
            "run_id": RUN_ID,
            "status": "pass",
        }
        write_json(bundle / "bundle-manifest.json", manifest)
        return bundle

    def test_checked_plan_and_exact_inventory_validate(self) -> None:
        M3.validate_plan(self.plan)
        for mutation, message in (
            (lambda plan: plan.update(replica_count=2), "const"),
            (
                lambda plan: plan["samples"].__setitem__(
                    0, {**plan["samples"][0], "condition_id": "B"}
                ),
                "cyclic",
            ),
            (
                lambda plan: plan["comparisons"].pop(),
                "too few",
            ),
        ):
            with self.subTest(message=message):
                corrupted = copy.deepcopy(self.plan)
                mutation(corrupted)
                with self.assertRaisesRegex(M3.ValidationError, message):
                    M3.validate_plan(corrupted)

    def test_analysis_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._build_bundle(Path(temporary))
            first, first_masks = M3.build_analysis(bundle, self.plan)
            second, second_masks = M3.build_analysis(bundle, self.plan)
            self.assertEqual(M3.canonical(first), M3.canonical(second))
            self.assertEqual(first_masks, second_masks)
            cohorts = {item["condition_id"]: item for item in first["cohorts"]}
            self.assertEqual(cohorts["B"]["variable_byte_mask"]["bit_count"], 3)
            self.assertEqual(cohorts["I"]["variable_byte_mask"]["bit_count"], 3)
            self.assertEqual(
                first["treatment"]["cohort_stable_delta_mask"]["bit_count"],
                (24 * M3.PAGE_SIZE) - 3,
            )
            self.assertEqual(
                first["treatment"]["cross_comparisons"]["occurrence_histogram"]["9"],
                24 * M3.PAGE_SIZE,
            )

    def test_complete_bundle_validates_and_corruption_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._build_bundle(Path(temporary))
            M3.validate_bundle(bundle)
            mask = bundle / "analysis/masks/cohort-E-variable.bin"
            mask.write_bytes(mask.read_bytes() + b"x")
            with self.assertRaisesRegex(M3.ValidationError, "manifest identity"):
                M3.validate_bundle(bundle)

    def test_retained_windows_invocations_validate_on_posix(self) -> None:
        if os.name == "nt":
            self.skipTest("cross-platform retained-path case requires POSIX")
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._build_bundle(
                Path(temporary), recorded_windows_paths=True
            )
            M3.validate_bundle(bundle)

    def test_retained_invocation_rejects_substitution_escape_and_cross_binding(
        self,
    ) -> None:
        cases = (
            ("plan_substitution", "plan identity"),
            ("environment_substitution", "environment hash"),
            ("result_escape", "escapes recorded parent"),
            ("plan_cross_binding", "plan path"),
            ("scenario_cross_binding", "scenario path"),
        )
        for case, message in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle = self._build_bundle(root, recorded_windows_paths=True)
                invocation_path = (
                    bundle / "samples/M3-SAMPLE-E-01/invocation.json"
                )
                invocation = M3.load_json(invocation_path)
                retained_plan = bundle / "plan.json"
                retained_environment = bundle / "environment.json"
                if case == "plan_substitution":
                    retained_plan = root / "substituted-plan.json"
                    write_json(retained_plan, self.plan)
                elif case == "environment_substitution":
                    retained_environment = root / "substituted-environment.json"
                    substituted = M3.load_json(bundle / "environment.json")
                    retained_environment.write_text(
                        json.dumps(substituted, indent=2) + "\n",
                        encoding="utf-8",
                    )
                elif case == "result_escape":
                    invocation["result_path"] = (
                        r"C:\Users\tester\AppData\Local\Temp\outside\result.json"
                    )
                elif case == "plan_cross_binding":
                    invocation["plan_path"] = (
                        r"C:\Users\tester\Development\other"
                        r"\oracle\windows-dao\experiments\m3"
                        r"\m3-index-isolation.plan.json"
                    )
                else:
                    invocation["scenario_path"] = (
                        r"C:\Users\tester\Development\jet3-rs"
                        r"\oracle\windows-dao\examples"
                        r"\DAO-GEN-TEXT8-INDEXED-001.scenario.json"
                    )
                with self.assertRaisesRegex(M3.ValidationError, message):
                    M3.validate_invocation(
                        invocation,
                        invocation_path,
                        retained_plan,
                        retained_environment,
                    )

    def test_live_invocation_keeps_strict_local_path_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._build_bundle(root)
            retained_invocation = (
                bundle / "samples/M3-SAMPLE-E-01/invocation.json"
            )
            invocation = M3.load_json(retained_invocation)
            invocation_path = root / "private-stage/invocation.json"
            write_json(invocation_path, invocation)
            M3.validate_invocation(invocation, invocation_path)
            invocation["plan_path"] = str(bundle / "plan.json")
            with self.assertRaisesRegex(M3.ValidationError, "plan path"):
                M3.validate_invocation(invocation, invocation_path)

    def test_database_alignment_and_size_bounds_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._build_bundle(Path(temporary))
            record_path = bundle / "samples/M3-SAMPLE-E-01/record.json"
            record = M3.load_json(record_path)
            database = bundle / record["database"]["path"]
            database.write_bytes(b"x")
            with self.assertRaisesRegex(M3.ValidationError, "manifest identity"):
                M3.validate_bundle(bundle)
            with self.assertRaisesRegex(M3.ValidationError, "aligned"):
                M3.page_hashes(b"x")

    def test_bounded_json_rejects_duplicate_keys_bom_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            duplicate = root / "duplicate.json"
            duplicate.write_text('{"x":1,"x":2}', encoding="utf-8")
            with self.assertRaisesRegex(M3.ValidationError, "duplicate"):
                M3.load_json(duplicate)
            bom = root / "bom.json"
            bom.write_bytes(b"\xef\xbb\xbf{}")
            with self.assertRaisesRegex(M3.ValidationError, "byte-order"):
                M3.load_json(bom)
            nonfinite = root / "nonfinite.json"
            nonfinite.write_text('{"value":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(M3.ValidationError, "non-finite"):
                M3.load_json(nonfinite)
            large = root / "large.bin"
            large.write_bytes(b"x" * (M3.MAX_DATABASE_BYTES + 1))
            with self.assertRaisesRegex(M3.ValidationError, "exceeds"):
                M3.bounded_file_identity(large, M3.MAX_DATABASE_BYTES)

    def test_environment_invocation_log_and_process_bindings_fail_closed(self) -> None:
        cases = ("provider", "invocation", "log", "reused_nonce")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                bundle = self._build_bundle(Path(temporary))
                first = bundle / "samples/M3-SAMPLE-E-01/record.json"
                record = M3.load_json(first)
                if case == "provider":
                    record["process"]["provider_server_sha256"] = "4" * 64
                    write_json(first, record)
                elif case == "invocation":
                    path = bundle / record["invocation"]["path"]
                    invocation = M3.load_json(path)
                    invocation["campaign_run_id"] = "20260725T120000Z-wrong"
                    write_json(path, invocation)
                    record["invocation"]["sha256"] = sha256(path)
                    write_json(first, record)
                elif case == "log":
                    path = bundle / record["operation_log"]["path"]
                    log = M3.load_json(path)
                    log["run_id"] = "20260725T120000Z-wrong"
                    write_json(path, log)
                    record["operation_log"]["sha256"] = sha256(path)
                    write_json(first, record)
                else:
                    second = bundle / "samples/M3-SAMPLE-B-01/record.json"
                    second_record = M3.load_json(second)
                    second_record["launch_nonce"] = record["launch_nonce"]
                    write_json(second, second_record)
                with self.assertRaises(M3.ValidationError):
                    M3.build_analysis(bundle, self.plan)

    def test_manifest_traversal_roles_unreferenced_and_hardlinks_reject(self) -> None:
        cases = ("traversal", "role", "unreferenced", "hardlink")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                bundle = self._build_bundle(Path(temporary))
                manifest_path = bundle / "bundle-manifest.json"
                manifest = M3.load_json(manifest_path)
                if case == "traversal":
                    manifest["plan"]["path"] = "../plan.json"
                elif case == "role":
                    manifest["files"][0]["role"] = "report"
                elif case == "unreferenced":
                    extra = bundle / "samples/UNREFERENCED/record.json"
                    write_json(extra, {"unexpected": True})
                    manifest["files"].append(
                        {
                            "media_type": "application/json",
                            "path": "samples/UNREFERENCED/record.json",
                            "role": "sample_record",
                            "sha256": sha256(extra),
                            "size_bytes": extra.stat().st_size,
                        }
                    )
                    manifest["files"].sort(key=lambda item: item["path"])
                else:
                    source = bundle / "analysis/summary.json"
                    os.link(source, bundle / "analysis/hardlink.json")
                    with self.assertRaisesRegex(M3.ValidationError, "hard links"):
                        M3.validate_bundle(bundle)
                    continue
                write_json(manifest_path, manifest)
                with self.assertRaises(M3.ValidationError):
                    M3.validate_bundle(bundle)

    def test_bundle_root_and_internal_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._build_bundle(root / "real")
            alias = root / "bundle-alias"
            symlink_or_skip(
                self,
                alias,
                bundle,
                target_is_directory=True,
            )
            with self.assertRaisesRegex(M3.ValidationError, "root"):
                M3.discover_files(alias)

            plan = bundle / "plan.json"
            external = root / "external-plan.json"
            external.write_bytes(plan.read_bytes())
            plan.unlink()
            symlink_or_skip(self, plan, external)
            with self.assertRaisesRegex(M3.ValidationError, "reparse point"):
                M3.validate_bundle(bundle)

    def test_analysis_work_ceiling_and_symmetric_stable_tails(self) -> None:
        values = {}
        for sample in self.plan["samples"]:
            pages = 2 if sample["condition_id"] == "B" else 1
            values[sample["sample_id"]] = bytes(
                [sample["replica"]] * (pages * M3.PAGE_SIZE)
            )
        with self.assertRaisesRegex(M3.ValidationError, "working-set"):
            M3.build_physical_analysis(values, self.plan, 1)
        summary, _ = M3.build_physical_analysis(
            values, self.plan, M3.MAX_ANALYSIS_WORKING_BYTES
        )
        self.assertEqual(
            summary["treatment"]["stable_extra_pages"],
            {"baseline_only": [1], "indexed_only": []},
        )


class M3RunnerSourceContractTests(unittest.TestCase):
    def test_exact_x86_winps_subprocess_launch_without_com(self) -> None:
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        winps32 = (
            windir
            / "SysWOW64"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not winps32.is_file():
            self.skipTest("required Windows PowerShell hosts are unavailable")
        with tempfile.TemporaryDirectory(prefix="m3 process ") as temporary:
            probe = Path(temporary) / "argument probe.ps1"
            probe.write_text(
                "param([string]$Value,[string]$Empty)"
                "[Console]::Write($Value+'|'+$Empty+'|'+[IntPtr]::Size)",
                encoding="utf-8",
            )
            module = SCRIPTS / "m3/M3.Process.ps1"
            module_quoted = str(module).replace("'", "''")
            winps_quoted = str(winps32).replace("'", "''")
            probe_quoted = str(probe).replace("'", "''")
            command = (
                f". '{module_quoted}';"
                f"$r=Invoke-M3ChildProcess -Executable "
                f"'{winps_quoted}' -Arguments @("
                "'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',"
                f"'{probe_quoted}',"
                "'-Value','a \"b\"\\','-Empty','') -TimeoutSeconds 10;"
                "[Console]::Write(($r|ConvertTo-Json -Compress))"
            )
            result = subprocess.run(
                [
                    str(winps32),
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
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout),
                {"stdout": 'a "b"\\||4', "stderr": ""},
            )

    def test_exact_x86_helper_terminates_oversized_output_early(self) -> None:
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        winps32 = (
            windir
            / "SysWOW64"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not winps32.is_file():
            self.skipTest("x86 Windows PowerShell is unavailable")
        with tempfile.TemporaryDirectory(prefix="m3 output ") as temporary:
            probe = Path(temporary) / "oversized output.ps1"
            probe.write_text(
                "[Console]::Out.Write(('x'*2097152));"
                "Start-Sleep -Seconds 30",
                encoding="utf-8",
            )
            module = SCRIPTS / "m3/M3.Process.ps1"
            module_quoted = str(module).replace("'", "''")
            winps_quoted = str(winps32).replace("'", "''")
            probe_quoted = str(probe).replace("'", "''")
            command = (
                f". '{module_quoted}';"
                "$clock=[Diagnostics.Stopwatch]::StartNew();"
                "try{"
                f"Invoke-M3ChildProcess -Executable '{winps_quoted}' "
                "-Arguments @('-NoProfile','-NonInteractive',"
                "'-ExecutionPolicy','Bypass','-File',"
                f"'{probe_quoted}') -TimeoutSeconds 20 "
                "-MaximumOutputBytes 4096|Out-Null;exit 9"
                "}catch{"
                "$clock.Stop();"
                "[Console]::Write($_.Exception.Message+'|'+"
                "[int]$clock.Elapsed.TotalSeconds);exit 0}"
            )
            result = subprocess.run(
                [
                    str(winps32),
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
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            message, seconds = result.stdout.rsplit("|", 1)
            self.assertIn("byte ceiling", message)
            self.assertLess(int(seconds), 10)

    def test_controller_uses_nine_fresh_workers_and_hardened_publication(self) -> None:
        controller = (SCRIPTS / "run-m3-controlled.ps1").read_text(encoding="utf-8")
        worker = (SCRIPTS / "run-m3-sample.ps1").read_text(encoding="utf-8")
        process = (SCRIPTS / "m3/M3.Process.ps1").read_text(encoding="utf-8")
        shared = (SCRIPTS / "shared/BoundedProcess.ps1").read_text(encoding="utf-8")
        publisher = (SCRIPTS / "m1/M1.Publication.ps1").read_text(encoding="utf-8")
        self.assertIn("foreach ($sample in $plan.samples)", controller)
        self.assertIn("Invoke-M3ChildProcess", controller)
        self.assertIn(
            '"oracle/windows-dao/scripts/shared/BoundedProcess.ps1"', controller
        )
        self.assertIn(
            '"oracle/windows-dao/scripts/shared/BoundedProcess.ps1"', worker
        )
        self.assertIn("shared/BoundedProcess.ps1", process)
        self.assertIn('CallerLabel "M3"', process)
        self.assertIn("[Diagnostics.Process]::Start", shared)
        self.assertNotIn(".ArgumentList", shared)
        self.assertNotIn("Kill($true)", shared)
        self.assertIn("Invoke-M1Preflight", worker)
        self.assertIn("Assert-M1RuntimeBinding", worker)
        self.assertIn("ls-remote --heads $RepositoryUrl", controller)
        self.assertIn("remote get-url origin", controller)
        self.assertIn("Publish-M1Stage", controller)
        self.assertIn("Assert-M1NoReparseComponents -Path $Session.CommitDirectory", publisher)

    def test_controller_retains_collision_and_timeout_bounds(self) -> None:
        controller = (SCRIPTS / "run-m3-controlled.ps1").read_text(encoding="utf-8")
        self.assertIn("New-M1PublicationSession", controller)
        self.assertIn("worker_timeout_seconds", controller)
        self.assertIn("Remove-M1PublicationStaging", controller)


if __name__ == "__main__":
    unittest.main()
