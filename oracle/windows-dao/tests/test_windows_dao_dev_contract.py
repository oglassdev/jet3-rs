from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
CLIENT_PATH = ROOT / "scripts" / "windows-dao-dev.py"
REMOTE_PATH = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "scripts"
    / "dev"
    / "Invoke-Jet3DaoDevJob.ps1"
)
DISPATCH_PATH = REMOTE_PATH.with_name("Dispatch.DevJob.ps1")
PUBLICATION_PATH = REMOTE_PATH.with_name("Publish.DevJob.ps1")
CONSUMED_BOOTSTRAP_PLAN = (
    ROOT / "oracle" / "windows-dao" / "acquisition" / "bootstrap-layout.plan.json"
)
CONSUMED_BOOTSTRAP_FLOOR_PLAN = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "acquisition"
    / "bootstrap-layout-floor.plan.json"
)
SPEC = importlib.util.spec_from_file_location("windows_dao_dev", CLIENT_PATH)
assert SPEC is not None and SPEC.loader is not None
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)


class WindowsDaoDevClientTests(unittest.TestCase):
    def args(self, root: Path, identity: Path):
        return CLIENT.parser().parse_args(
            [
                "provider-probe",
                "--user",
                "jet3runner",
                "--identity",
                str(identity),
                "--shared-root",
                str(root),
                "--run-id",
                "20260826T120000Z-dev-dao",
            ]
        )

    def test_cli_exposes_only_allowlisted_jobs(self) -> None:
        parser = CLIENT.parser()
        job = next(action for action in parser._actions if action.dest == "job")
        self.assertEqual(
            tuple(job.choices),
            (
                "provider-probe",
                "create-empty",
                "opening-matrix",
                "allocation-map",
                "catalog",
                "table-definition",
                "row",
                "value",
                "index",
                "bootstrap-layout",
                "system-catalog",
                "long-value-maps",
                "long-value-maps-followup",
                "bootstrap-composer-semantics",
                "bootstrap-composer-validation",
                "schema-generalization",
                "multiple-indexes",
                "definition-continuation",
                "extended-names",
                "lvprop-null",
            ),
        )
        self.assertNotIn("command", {action.dest for action in parser._actions})

    def test_invocation_uses_x86_powershell_and_strict_ssh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            CLIENT.validate_args(args)
            command = CLIENT.ssh_command(args)
            remote = CLIENT.remote_job_command(args)
            self.assertIn("SysWOW64", remote[0])
            self.assertIn("-Job", remote)
            self.assertIn(args.job, remote)
            serialized = " ".join(remote)
            self.assertLessEqual(len(serialized.encode("utf-16-le")) // 2, 8000)
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertIn("BatchMode=yes", command)

    def test_staging_copies_only_the_allowlisted_runner_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            CLIENT.validate_args(args)
            staged = CLIENT.stage_job(args)
            expected = {
                CLIENT.REMOTE_RUNNER.name,
                CLIENT.PROVIDER_PROBE.name,
                CLIENT.CATALOG_JOB.name,
                CLIENT.TABLE_DEFINITION_JOB.name,
                CLIENT.TABLE_DEFINITION_TYPES.name,
                CLIENT.STAGED_DISPATCH.name,
                CLIENT.STAGED_PUBLICATION.name,
                CLIENT.ROW_JOB.name,
                CLIENT.VALUE_JOB.name,
                CLIENT.INDEX_JOB.name,
                CLIENT.BOOTSTRAP_LAYOUT_JOB.name,
                CLIENT.SYSTEM_CATALOG_JOB.name,
                CLIENT.BOOTSTRAP_COMPOSER_VALIDATION_JOB.name,
                CLIENT.SCHEMA_GENERALIZATION_JOB.name,
                CLIENT.DEFINITION_CONTINUATION_JOB.name,
                CLIENT.EXTENDED_NAMES_JOB.name,
                CLIENT.LVPROP_NULL_JOB.name,
            }
            if CLIENT.MULTIPLE_INDEXES_JOB.is_file():
                expected.add(CLIENT.MULTIPLE_INDEXES_JOB.name)
            self.assertEqual({path.name for path in staged.iterdir()}, expected)

    def test_result_must_match_job_and_exit_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            CLIENT.validate_args(args)
            output = root / "outbox" / args.run_id
            output.mkdir(parents=True)
            result = {
                "development_only": True,
                "job": args.job,
                "run_id": args.run_id,
                "status": "pass",
            }
            (output / "result.json").write_text(json.dumps(result), encoding="utf-8")
            self.assertEqual(CLIENT.validated_result(args, 0), result)
            with self.assertRaises(CLIENT.DevClientError):
                CLIENT.validated_result(args, 3)

    def test_validation_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            args.remote_shared_root = r"Z:\safe\..\escape"
            with self.assertRaises(CLIENT.DevClientError):
                CLIENT.validate_args(args)

    def test_validation_rejects_remote_shell_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            for remote_root in (
                r"Z:\safe root",
                r"Z:\safe&whoami",
                r"Z:\safe|whoami",
                'Z:\\safe"quoted',
                r"Z:\safe$variable",
            ):
                args = self.args(root, identity)
                args.remote_shared_root = remote_root
                with self.subTest(remote_root=remote_root):
                    with self.assertRaisesRegex(
                        CLIENT.DevClientError, "remote-shell-unsafe"
                    ):
                        CLIENT.validate_args(args)

            args = self.args(root, identity)
            args.remote_shared_root = r"Z:\safe_root\safe-child.1"
            CLIENT.validate_args(args)
            self.assertEqual(args.remote_shared_root, r"Z:\safe_root\safe-child.1")

    def test_maximum_plan_bound_remote_command_is_ordered_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            CLIENT.validate_args(args)
            args.job = "schema-generalization"
            args.plan_sha256 = "a" * 64
            args.remote_shared_root = "\\\\" + "h" * 118 + "\\" + "D" * 119
            self.assertEqual(len(args.remote_shared_root), 240)
            command = CLIENT.remote_job_command(args)

            self.assertEqual(
                command[0],
                r"%WINDIR%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe",
            )
            remote_input = CLIENT.ntpath.join(
                args.remote_shared_root, "inbox", args.run_id
            )
            staged = lambda path: CLIENT.ntpath.join(remote_input, path.name)
            expected = [
                r"%WINDIR%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                staged(CLIENT.REMOTE_RUNNER),
                "-Job",
                args.job,
                "-RunId",
                args.run_id,
                "-ProviderProbePath",
                staged(CLIENT.PROVIDER_PROBE),
                "-SharedOutputPath",
                CLIENT.ntpath.join(args.remote_shared_root, "outbox", args.run_id),
                "-CatalogJobPath",
                staged(CLIENT.CATALOG_JOB),
                "-TableDefinitionJobPath",
                staged(CLIENT.TABLE_DEFINITION_JOB),
                "-TableDefinitionTypeInputPath",
                staged(CLIENT.TABLE_DEFINITION_TYPES),
                "-DispatchPath",
                staged(CLIENT.STAGED_DISPATCH),
                "-PublicationPath",
                staged(CLIENT.STAGED_PUBLICATION),
                "-RowJobPath",
                staged(CLIENT.ROW_JOB),
                "-ValueJobPath",
                staged(CLIENT.VALUE_JOB),
                "-IndexJobPath",
                staged(CLIENT.INDEX_JOB),
                "-BootstrapLayoutJobPath",
                staged(CLIENT.BOOTSTRAP_LAYOUT_JOB),
                "-SystemCatalogJobPath",
                staged(CLIENT.SYSTEM_CATALOG_JOB),
                "-BootstrapComposerValidationJobPath",
                staged(CLIENT.BOOTSTRAP_COMPOSER_VALIDATION_JOB),
                "-BootstrapComposerEmptyPath",
                CLIENT.ntpath.join(remote_input, "bootstrap-composer-empty.mdb"),
                "-BootstrapComposerAlphaPath",
                CLIENT.ntpath.join(remote_input, "bootstrap-composer-alpha.mdb"),
                "-SchemaGeneralizationJobPath",
                staged(CLIENT.SCHEMA_GENERALIZATION_JOB),
                "-MultipleIndexesJobPath",
                staged(CLIENT.MULTIPLE_INDEXES_JOB),
                "-DefinitionContinuationJobPath",
                staged(CLIENT.DEFINITION_CONTINUATION_JOB),
                "-ExtendedNamesJobPath",
                staged(CLIENT.EXTENDED_NAMES_JOB),
                "-LvPropNullJobPath",
                staged(CLIENT.LVPROP_NULL_JOB),
                "-LvPropFixedAlphaPath",
                CLIENT.ntpath.join(remote_input, "lvprop-fixed-alpha.mdb"),
                "-LvPropNullAlphaPath",
                CLIENT.ntpath.join(remote_input, "lvprop-null-alpha.mdb"),
                "-PlanSha256",
                args.plan_sha256,
                "-PlanPath",
                staged(CLIENT.SCHEMA_GENERALIZATION_PLAN),
            ]
            self.assertEqual(command, expected)
            serialized = " ".join(command)
            self.assertLessEqual(len(serialized.encode("utf-16-le")) // 2, 8000)

            args.job = "provider-probe"
            expected_non_plan = expected[:-4]
            expected_non_plan[expected_non_plan.index("-Job") + 1] = args.job
            self.assertEqual(CLIENT.remote_job_command(args), expected_non_plan)

            args.job = "schema-generalization"
            args.remote_shared_root = "\\\\" + "h" * 1000 + "\\D"
            with self.assertRaisesRegex(CLIENT.DevClientError, "8,000-unit bound"):
                CLIENT.remote_job_command(args)

    def test_consumed_bootstrap_plan_remains_immutable(self) -> None:
        expected_inputs = {
            "scripts/windows-dao-dev.py": "029c871b9fbeef228f015e5561f7b8a9980645f605c398157cc75bf35c623715",
            "oracle/windows-dao/scripts/probe-provider.ps1": "695e357959f7882f2608dfcc32cf9d6bc5d1fd128126552d656daabbfe0b0ebd",
            "oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1": "f5ed1b5d04f632ef20483aa6526e51693a7ea9a0170821f869ea93faf0534381",
            "oracle/windows-dao/scripts/dev/Dispatch.DevJob.ps1": "42ead0007ff899b7f586cdc897b2e037a8d155931f8ec48988c46c16701b26c3",
            "oracle/windows-dao/scripts/dev/Publish.DevJob.ps1": "e7e07d560484ea6399275a963591a115c9c7ac3ec069a4ca80432d8f5a403759",
            "oracle/windows-dao/scripts/dev/BootstrapLayout.DevJob.ps1": "e04cecec6a3678b76bd1b54bd2d77fc52b94316c1e38e584bc373654e59a4a88",
            "oracle/windows-dao/scripts/bootstrap_layout.py": "f78e4986f00e4e303e26038bb4ee012fb2352dbe7f443fa3a5e0337f7b868d06",
        }
        document = json.loads(CONSUMED_BOOTSTRAP_PLAN.read_text(encoding="utf-8"))

        self.assertEqual(
            hashlib.sha256(CONSUMED_BOOTSTRAP_PLAN.read_bytes()).hexdigest(),
            "73e402a255795eb6bd08bffa5e3611ceef219f6e810e99f9715f0e69b4aef8fc",
        )
        self.assertEqual(document["inputs"], expected_inputs)

    def test_consumed_bootstrap_floor_plan_remains_immutable(self) -> None:
        document = json.loads(
            CONSUMED_BOOTSTRAP_FLOOR_PLAN.read_text(encoding="utf-8")
        )

        self.assertEqual(
            hashlib.sha256(CONSUMED_BOOTSTRAP_FLOOR_PLAN.read_bytes()).hexdigest(),
            "c0161be2ba1189249d743c9198bcd004dd9d927edcc7d753cffe79c421677773",
        )
        self.assertEqual(document["document_type"], "dao_bootstrap_layout_floor_plan")

    def pinned_plan_copy(self, root: Path, binding, name: str) -> Path:
        """Copy a plan with its input pins recomputed from the working tree."""
        plan = json.loads(binding.plan.read_text(encoding="utf-8"))
        plan["inputs"] = {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in plan["inputs"]
        }
        copy = root / name
        copy.write_text(json.dumps(plan), encoding="utf-8")
        return copy

    def assert_plan_bound_job(self, job: str, plan_attribute: str) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            binding = CLIENT.plan_binding(job)
            if not binding.analyzer.is_file():
                self.skipTest(f"{binding.analyzer.name} is not present yet")
            pinned = self.pinned_plan_copy(root, binding, "pinned.plan.json")
            with mock.patch.object(CLIENT, plan_attribute, pinned):
                args = self.args(root, identity)
                args.job = job
                CLIENT.validate_args(args)
                self.assertEqual(
                    args.plan_sha256, hashlib.sha256(pinned.read_bytes()).hexdigest()
                )
                invocation = CLIENT.remote_job_command(args)
                self.assertIn("-PlanSha256", invocation)
                self.assertIn("-PlanPath", invocation)
                self.assertIn("-SystemCatalogJobPath", invocation)
                staged = CLIENT.stage_job(args)
                self.assertTrue((staged / pinned.name).is_file())
                self.assertTrue((staged / binding.analyzer.name).is_file())

            altered = json.loads(pinned.read_text(encoding="utf-8"))
            altered["inputs"]["scripts/windows-dao-dev.py"] = "0" * 64
            pinned.write_text(json.dumps(altered), encoding="utf-8")
            with mock.patch.object(CLIENT, plan_attribute, pinned):
                with self.assertRaisesRegex(CLIENT.DevClientError, "differs from its plan"):
                    CLIENT.verified_plan_sha256(CLIENT.plan_binding(job))

    def test_bootstrap_layout_binds_and_verifies_a_pinned_plan(self) -> None:
        self.assert_plan_bound_job("bootstrap-layout", "BOOTSTRAP_LAYOUT_PLAN")

    def test_system_catalog_binds_and_verifies_a_pinned_plan(self) -> None:
        self.assert_plan_bound_job("system-catalog", "SYSTEM_CATALOG_PLAN")

    def test_long_value_maps_binds_and_verifies_a_pinned_plan(self) -> None:
        self.assert_plan_bound_job("long-value-maps", "LONG_VALUE_MAPS_PLAN")

    def test_long_value_maps_followup_binds_and_verifies_a_pinned_plan(self) -> None:
        self.assert_plan_bound_job(
            "long-value-maps-followup", "LONG_VALUE_MAPS_FOLLOWUP_PLAN"
        )

    def test_bootstrap_composer_semantics_binds_and_verifies_a_pinned_plan(self) -> None:
        self.assert_plan_bound_job(
            "bootstrap-composer-semantics", "BOOTSTRAP_COMPOSER_SEMANTICS_PLAN"
        )

    def test_schema_generalization_binds_and_verifies_a_pinned_plan(self) -> None:
        self.assert_plan_bound_job("schema-generalization", "SCHEMA_GENERALIZATION_PLAN")

    def test_multiple_indexes_binds_issue_150_and_verifies_a_pinned_plan(self) -> None:
        binding = CLIENT.plan_binding("multiple-indexes")
        self.assertEqual(binding.issue, 150)
        self.assertEqual(binding.document_type, "dao_multiple_indexes_plan")
        self.assertEqual(binding.job_result_name, "multiple-indexes-job-result.json")
        self.assertEqual(binding.report_name, "multiple-indexes-report.json")
        self.assert_plan_bound_job("multiple-indexes", "MULTIPLE_INDEXES_PLAN")

    def test_definition_continuation_binds_issue_151(self) -> None:
        binding = CLIENT.plan_binding("definition-continuation")
        self.assertEqual(binding.issue, 151)
        self.assertEqual(binding.document_type, "dao_definition_continuation_plan")
        self.assertEqual(
            binding.job_result_name, "definition-continuation-job-result.json"
        )
        self.assertEqual(binding.report_name, "definition-continuation-report.json")
        self.assert_plan_bound_job(
            "definition-continuation", "DEFINITION_CONTINUATION_PLAN"
        )

    def test_extended_names_binds_issue_152(self) -> None:
        binding = CLIENT.plan_binding("extended-names")
        self.assertEqual(binding.issue, 152)
        self.assertEqual(binding.document_type, "dao_extended_names_plan")
        self.assertEqual(binding.job_result_name, "extended-names-job-result.json")
        self.assertEqual(binding.report_name, "extended-names-report.json")
        self.assert_plan_bound_job("extended-names", "EXTENDED_NAMES_PLAN")

    def test_lvprop_null_binds_issue_149_and_verifies_a_pinned_plan(self) -> None:
        binding = CLIENT.plan_binding("lvprop-null")
        self.assertEqual(binding.issue, 149)
        self.assertEqual(binding.document_type, "dao_lvprop_null_plan")
        self.assert_plan_bound_job("lvprop-null", "LVPROP_NULL_PLAN")

    def test_plan_binding_requires_its_exact_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = CLIENT.plan_binding("lvprop-null")
            pinned = root / "wrong-issue.plan.json"
            pinned.write_text(
                json.dumps(
                    {
                        "document_type": binding.document_type,
                        "issue": 100,
                        "development_only": True,
                        "inputs": {
                            "scripts/windows-dao-dev.py": hashlib.sha256(
                                CLIENT_PATH.read_bytes()
                            ).hexdigest()
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CLIENT.DevClientError, "plan is malformed"):
                CLIENT.verified_plan_sha256(binding._replace(plan=pinned))

    def test_consumed_bootstrap_composer_validation_retains_candidate_pins(self) -> None:
        plan = json.loads(
            CLIENT.BOOTSTRAP_COMPOSER_VALIDATION_PLAN.read_text(encoding="utf-8")
        )
        self.assertEqual(
            plan["candidates"]["empty"]["sha256"],
            "8fad368409747adadf47704074a77e79e0bd0c5eae656566bdc72a5876f479e7",
        )
        self.assertEqual(
            plan["candidates"]["alpha"]["sha256"],
            "b798de9209637361245703b0132f59c06dd7cb3d051d214415d6ed6a76768df2",
        )

    def test_consumed_bootstrap_composer_validation_plan_refuses_to_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            args.job = "bootstrap-composer-validation"
            with self.assertRaisesRegex(CLIENT.DevClientError, "differs from its plan"):
                CLIENT.validate_args(args)

    def test_bootstrap_composer_analysis_rechecks_staged_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            args.job = "bootstrap-composer-semantics"
            binding = CLIENT.plan_binding(args.job)
            pinned = self.pinned_plan_copy(root, binding, "pinned.plan.json")
            with mock.patch.object(CLIENT, "BOOTSTRAP_COMPOSER_SEMANTICS_PLAN", pinned):
                CLIENT.validate_args(args)
                staged = CLIENT.stage_job(args)
                (staged / "system_catalog.py").write_text("tampered", encoding="utf-8")

                with mock.patch.object(CLIENT.subprocess, "run") as run:
                    with self.assertRaisesRegex(
                        CLIENT.DevClientError, "differs before analysis"
                    ):
                        CLIENT.analyze_plan_bound_output(
                            args, CLIENT.plan_binding(args.job)
                        )
                    run.assert_not_called()

    def test_consumed_bootstrap_sufficiency_plan_refuses_to_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            args.job = "bootstrap-layout"
            with self.assertRaisesRegex(CLIENT.DevClientError, "differs from its plan"):
                CLIENT.validate_args(args)


class WindowsDaoDevRemoteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.remote = REMOTE_PATH.read_text(encoding="utf-8")
        cls.dispatch = DISPATCH_PATH.read_text(encoding="utf-8")
        cls.publication = PUBLICATION_PATH.read_text(encoding="utf-8")

    def test_remote_is_exploratory_and_allowlisted(self) -> None:
        self.assertIn(
            '[ValidateSet("provider-probe", "create-empty", "opening-matrix", "allocation-map", "catalog", "table-definition", "row", "value", "index", "bootstrap-layout", "system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics", "bootstrap-composer-validation", "schema-generalization", "multiple-indexes", "definition-continuation", "extended-names", "lvprop-null")]',
            self.remote,
        )
        self.assertIn("development_only = $true", self.remote)
        for name in ("v30-u-n", "v30-e-n", "v30-u-p", "v30-e-p"):
            self.assertIn(name, self.publication)
        for name in ("v40-u-n", "v40-e-n", "v40-u-p", "v40-e-p"):
            self.assertIn(name, self.publication)
        self.assertNotIn("Invoke-Expression", self.remote)
        self.assertNotIn("ScriptBlock::Create", self.remote)

    def test_catalog_job_is_staged_and_bounded(self) -> None:
        catalog = CLIENT.CATALOG_JOB.read_text(encoding="utf-8")
        self.assertIn('$Job -ceq "catalog"', self.remote)
        self.assertIn("CatalogJobPath", self.remote)
        self.assertIn("development_only = $true", catalog)
        self.assertIn("$count -gt 128", catalog)
        self.assertIn("Release-ComObject -Value $definitions", catalog)
        for name in (
            "00-empty",
            "01-ascii-created",
            "02-ascii-dropped",
            "03-ascii-recreated",
            "04-cp1252-created",
            "05-cp1252-dropped",
            "06-cp1252-recreated",
        ):
            self.assertIn(name, catalog)
        self.assertLess(
            catalog.index("Get-TableSnapshot -Path $Source"),
            catalog.index("Copy-Item -LiteralPath $Source"),
        )

    def test_allocation_job_is_bounded_and_publishes_closed_checkpoints(self) -> None:
        self.assertIn('$Job -ceq "allocation-map"', self.remote)
        self.assertIn("$maximumRows = 32768", self.remote)
        self.assertIn("$allocationBatchRows = 256", self.remote)
        self.assertIn("$allocationPayloadBytes = 1800", self.remote)
        self.assertIn("No new type-05 page appeared", self.remote)
        self.assertIn("function Get-MultiSlotUsageMap", self.remote)
        self.assertIn("No type-1 row with two valid type-05 references", self.remote)
        for name in (
            "allocation-00-empty.mdb",
            "allocation-01-created.mdb",
            "allocation-02-seeded.mdb",
            "allocation-03-before-extended.mdb",
            "allocation-04-after-extended.mdb",
            "allocation-05-grown.mdb",
            "allocation-06-deleted.mdb",
            "allocation-07-reinserted.mdb",
        ):
            self.assertIn(name, self.publication)

    def test_table_definition_job_is_staged_checked_and_bounded(self) -> None:
        job = CLIENT.TABLE_DEFINITION_JOB.read_text(encoding="utf-8")
        inputs = json.loads(CLIENT.TABLE_DEFINITION_TYPES.read_text(encoding="utf-8"))
        self.assertIn('$Job -ceq "table-definition"', self.remote)
        self.assertIn("TableDefinitionJobPath", self.remote)
        self.assertIn("TableDefinitionTypeInputPath", self.remote)
        self.assertIn("development_only = $true", job)
        self.assertEqual(inputs["schema_version"], 1)
        self.assertEqual(len(inputs["candidates"]), 31)
        self.assertEqual(len({item["name"] for item in inputs["candidates"]}), 31)
        self.assertEqual(len({item["value"] for item in inputs["candidates"]}), 31)
        self.assertIn("$MaximumTypes = 32", job)
        self.assertIn("$MaximumFields = 64", job)
        self.assertIn("$MaximumIndexes = 32", job)
        self.assertIn("$ordinal -lt 64", job)
        for name in (
            "00-empty",
            "01-type-inventory",
            "02-column-probe",
            "03-boundary-probe",
            "04-index-base",
            "05-index-primary",
            "06-index-composite",
            "07-index-required",
            "08-relationship-base",
            "09-relationship-created",
        ):
            self.assertIn(name, job)
        self.assertLess(
            job.index("Get-SchemaSnapshot -Path $Source"),
            job.index("Copy-Item -LiteralPath $Source"),
        )

    def test_database_is_closed_before_atomic_publication(self) -> None:
        self.assertLess(
            self.remote.index("$database.Close()"),
            self.remote.index("-File $PublicationPath"),
        )
        self.assertIn("[IO.Directory]::Move($staging, $Destination)", self.publication)

    def test_row_job_is_repeated_bounded_and_never_compacts(self) -> None:
        row = CLIENT.ROW_JOB.read_text(encoding="utf-8")
        self.assertIn('$Job -in @("catalog", "table-definition", "row", "value", "index", "bootstrap-layout", "system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics", "bootstrap-composer-validation", "schema-generalization", "multiple-indexes", "definition-continuation", "extended-names", "lvprop-null")', self.remote)
        self.assertIn('[ValidateSet("catalog", "table-definition", "row", "value", "index", "bootstrap-layout", "system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics", "bootstrap-composer-validation", "schema-generalization", "multiple-indexes", "definition-continuation", "extended-names", "lvprop-null")]', self.dispatch)
        self.assertIn("$MaximumRows = 64", row)
        self.assertIn("foreach ($replica in 1..3)", row)
        for scenario in (
            "fixed-only",
            "variable-only",
            "mixed",
            "all-null",
            "page-boundary",
            "growing",
            "shrinking",
            "deleted",
            "overflowing",
        ):
            self.assertIn(f'"{scenario}"', row)
            self.assertIn(f'"{scenario}"', self.publication)
        self.assertNotIn("CompactDatabase", row)

    def test_value_job_is_repeated_bounded_and_never_compacts(self) -> None:
        value = CLIENT.VALUE_JOB.read_text(encoding="utf-8")
        self.assertIn('$Job -in @("catalog", "table-definition", "row", "value", "index", "bootstrap-layout", "system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics", "bootstrap-composer-validation", "schema-generalization", "multiple-indexes", "definition-continuation", "extended-names", "lvprop-null")', self.remote)
        self.assertIn('[ValidateSet("catalog", "table-definition", "row", "value", "index", "bootstrap-layout", "system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics", "bootstrap-composer-validation", "schema-generalization", "multiple-indexes", "definition-continuation", "extended-names", "lvprop-null")]', self.dispatch)
        self.assertIn("$MaximumDatabaseBytes = 4MB", value)
        self.assertIn("foreach ($replica in 1..3)", value)
        self.assertIn("$LongLengths = @(32, 512, 2048, 4096)", value)
        for scenario in ("scalars", "cp1252", "cp1251", "memo", "ole"):
            self.assertIn(scenario, value.lower())
            self.assertIn(scenario, self.publication.lower())
        self.assertNotIn("CompactDatabase", value)

    def test_index_job_is_staged_bounded_and_never_compacts(self) -> None:
        index = CLIENT.INDEX_JOB.read_text(encoding="utf-8")
        self.assertIn('$Job -in @("catalog", "table-definition", "row", "value", "index", "bootstrap-layout", "system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics", "bootstrap-composer-validation", "schema-generalization", "multiple-indexes", "definition-continuation", "extended-names", "lvprop-null")', self.remote)
        self.assertIn('[ValidateSet("catalog", "table-definition", "row", "value", "index", "bootstrap-layout", "system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics", "bootstrap-composer-validation", "schema-generalization", "multiple-indexes", "definition-continuation", "extended-names", "lvprop-null")]', self.dispatch)
        self.assertIn("$MaximumRows = 4096", index)
        self.assertIn("$MaximumDatabaseBytes = 16MB", index)
        for scenario in (
            "long-ascending",
            "long-descending",
            "long-permuted",
            "composite-descending",
            "key-types",
            "relationship-base",
            "relationship-created",
            "relationship-update",
            "relationship-delete",
            "relationship-cascade",
            "relationship-deleted",
        ):
            self.assertIn(f'"{scenario}"', index)
            self.assertIn(f'"{scenario}"', self.publication)
        self.assertNotIn("CompactDatabase", index)

    def test_bootstrap_layout_is_plan_bound_bounded_and_development_only(self) -> None:
        job = CLIENT.BOOTSTRAP_LAYOUT_JOB.read_text(encoding="utf-8")
        plan = json.loads(CLIENT.BOOTSTRAP_LAYOUT_PLAN.read_text(encoding="utf-8"))
        self.assertIn('$Job -ceq "bootstrap-layout"', self.remote)
        self.assertIn("BootstrapLayoutJobPath", self.remote)
        self.assertIn("PlanSha256", self.remote)
        self.assertIn("foreach ($replica in 1..3)", job)
        self.assertIn("development_only = $true", job)
        self.assertIn("$MaximumPages = 64", job)
        self.assertIn("$MaximumVariants = 64", job)
        self.assertNotIn("CompactDatabase", job)
        self.assertIn("$referenced.Count -gt 210", self.publication)
        self.assertIn("210-database bound", self.publication)
        self.assertIn("WITH OWNERACCESS OPTION", job)
        self.assertIn("$TimestampAnchorWindowBytes = 64", job)
        self.assertIn("-AllowLastUpdatedAnchor", job)
        self.assertIn("-LastUpdatedAnchor $null", job)
        self.assertIn('Name "property-set"', job)
        self.assertIn("$State.sufficiency", job)
        for field in (
            "size_before",
            "size_after",
            "sha256_before",
            "sha256_after",
        ):
            self.assertIn(field, job)
        self.assertNotIn("sha256_before_open", job)
        self.assertNotIn("sha256_after_open", job)
        self.assertIn("replica.sufficiency.database", self.publication)
        self.assertTrue(plan["development_only"])
        self.assertEqual(plan["issue"], 100)
        self.assertEqual(
            plan["execution"]["bounds"]["maximum_published_databases"],
            3 * (4 + 1 + 1 + 64),
        )
        # The sufficiency plan is consumed: the shared dev tooling has moved on,
        # so its pins must no longer verify and the job must refuse to run.
        with self.assertRaisesRegex(CLIENT.DevClientError, "differs from its plan"):
            CLIENT.verified_plan_sha256(CLIENT.plan_binding("bootstrap-layout"))

    def test_system_catalog_is_plan_bound_bounded_and_development_only(self) -> None:
        job = CLIENT.SYSTEM_CATALOG_JOB.read_text(encoding="utf-8")
        plan = json.loads(CLIENT.SYSTEM_CATALOG_PLAN.read_text(encoding="utf-8"))
        self.assertIn('"system-catalog" = "oracle/windows-dao/scripts/dev/SystemCatalog.DevJob.ps1"', self.remote)
        self.assertIn("SystemCatalogJobPath", self.remote)
        self.assertIn("system_catalog_replicas", self.remote)
        self.assertIn('"system-catalog" { $SystemCatalogJobPath }', self.dispatch)
        self.assertIn('"-PlanSha256", $PlanSha256, "-RunId", $RunId', self.dispatch)
        self.assertIn("15-database bound", self.publication)
        self.assertIn("foreach ($replica in 1..3)", job)
        self.assertIn("development_only = $true", job)
        self.assertIn('else { "dao_system_catalog_job_result" }', job)
        self.assertIn("$MaximumPages = 64", job)
        self.assertIn("$MaximumTables = 16", job)
        self.assertIn("$MaximumPropertyValueCharacters = 256", job)
        self.assertIn("sha256_after_metadata", job)
        self.assertNotIn("CompactDatabase", job)
        for name in ("empty", "table1", "table2", "query", "relationship"):
            self.assertIn(f'-Name "{name}"', job)
        self.assertEqual(plan["document_type"], "dao_system_catalog_plan")
        self.assertEqual(plan["issue"], 100)
        self.assertTrue(plan["development_only"])
        self.assertEqual(
            plan["execution"]["checkpoints"],
            ["empty", "table1", "table2", "query", "relationship"],
        )
        bounds = plan["execution"]["bounds"]
        self.assertEqual(bounds["maximum_pages_per_database"], 64)
        self.assertEqual(bounds["maximum_replicas"], 3)
        self.assertEqual(bounds["maximum_checkpoints"], 5)
        self.assertEqual(bounds["maximum_property_value_characters"], 256)
        self.assertEqual(
            set(plan["inputs"]),
            {
                "scripts/windows-dao-dev.py",
                "oracle/windows-dao/scripts/probe-provider.ps1",
                "oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1",
                "oracle/windows-dao/scripts/dev/Dispatch.DevJob.ps1",
                "oracle/windows-dao/scripts/dev/Publish.DevJob.ps1",
                "oracle/windows-dao/scripts/dev/SystemCatalog.DevJob.ps1",
                "oracle/windows-dao/scripts/system_catalog.py",
            },
        )

    def test_long_value_maps_is_plan_bound_bounded_and_development_only(self) -> None:
        job = CLIENT.SYSTEM_CATALOG_JOB.read_text(encoding="utf-8")
        plan = json.loads(CLIENT.LONG_VALUE_MAPS_PLAN.read_text(encoding="utf-8"))
        self.assertIn('"long-value-maps" = "oracle/windows-dao/scripts/dev/SystemCatalog.DevJob.ps1"', self.remote)
        self.assertIn('"long-value-maps" { $SystemCatalogJobPath }', self.dispatch)
        self.assertIn('"long-value-maps" {', self.publication)
        self.assertIn('New-GammaTable -Path $workingPath', job)
        self.assertIn('Add-GammaLongMemoRow -Path $workingPath', job)
        self.assertIn('-Name "table"', job)
        self.assertIn('-Name "row"', job)
        self.assertIn('"memo-" + ("x" * 4096)', job)
        self.assertEqual(plan["document_type"], "dao_long_value_maps_plan")
        self.assertEqual(plan["execution"]["checkpoints"], ["empty", "table", "row"])
        self.assertEqual(plan["execution"]["bounds"]["maximum_replicas"], 3)
        self.assertEqual(plan["execution"]["bounds"]["maximum_checkpoints"], 3)
        with self.assertRaisesRegex(CLIENT.DevClientError, "differs from its plan"):
            CLIENT.verified_plan_sha256(CLIENT.plan_binding("long-value-maps"))

    def test_long_value_maps_followup_is_pinned_and_bounded(self) -> None:
        plan = json.loads(
            CLIENT.LONG_VALUE_MAPS_FOLLOWUP_PLAN.read_text(encoding="utf-8")
        )
        self.assertIn(
            '"long-value-maps-followup" = "oracle/windows-dao/scripts/dev/SystemCatalog.DevJob.ps1"',
            self.remote,
        )
        self.assertIn(
            '"long-value-maps-followup" { $SystemCatalogJobPath }',
            self.dispatch,
        )
        self.assertIn('"long-value-maps-followup" {', self.publication)
        self.assertEqual(
            plan["document_type"], "dao_long_value_maps_followup_plan"
        )
        self.assertEqual(plan["execution"]["checkpoints"], ["empty", "table", "row"])
        self.assertEqual(plan["execution"]["bounds"]["maximum_replicas"], 3)
        self.assertEqual(plan["execution"]["bounds"]["maximum_checkpoints"], 3)
        with self.assertRaisesRegex(CLIENT.DevClientError, "differs from its plan"):
            CLIENT.verified_plan_sha256(
                CLIENT.plan_binding("long-value-maps-followup")
            )

    def test_bootstrap_composer_semantics_is_bounded_and_exactly_routed(self) -> None:
        job = CLIENT.SYSTEM_CATALOG_JOB.read_text(encoding="utf-8")
        plan = json.loads(
            CLIENT.BOOTSTRAP_COMPOSER_SEMANTICS_PLAN.read_text(encoding="utf-8")
        )
        self.assertIn(
            '"bootstrap-composer-semantics" = "oracle/windows-dao/scripts/dev/SystemCatalog.DevJob.ps1"',
            self.remote,
        )
        self.assertIn(
            '"bootstrap-composer-semantics" { $SystemCatalogJobPath }',
            self.dispatch,
        )
        self.assertIn(
            '"bootstrap-composer-semantics" { "bootstrap-composer-semantics-job-result.json" }',
            self.dispatch,
        )
        self.assertIn('"bootstrap-composer-semantics" {', self.publication)
        self.assertIn("$referenced.Count -gt 6", self.publication)
        self.assertIn(
            "^bootstrap-composer-semantics-r[1-3]-(empty|alpha)\\.mdb$",
            self.publication,
        )
        self.assertIn(
            '$Experiment -ceq "bootstrap-composer-semantics"',
            job,
        )
        self.assertIn('-Name "alpha"', job)
        self.assertEqual(job.count("foreach ($replica in 1..3)"), 1)
        self.assertEqual(
            plan["document_type"], "dao_bootstrap_composer_semantics_plan"
        )
        self.assertEqual(plan["execution"]["checkpoints"], ["empty", "alpha"])
        self.assertEqual(plan["execution"]["bounds"]["maximum_replicas"], 3)
        self.assertEqual(plan["execution"]["bounds"]["maximum_checkpoints"], 2)

    def test_schema_generalization_is_bounded_and_exactly_routed(self) -> None:
        job = CLIENT.SCHEMA_GENERALIZATION_JOB.read_text(encoding="utf-8")
        plan = json.loads(
            CLIENT.SCHEMA_GENERALIZATION_PLAN.read_text(encoding="utf-8")
        )
        self.assertIn(
            '"schema-generalization" = "oracle/windows-dao/scripts/dev/SchemaGeneralization.DevJob.ps1"',
            self.remote,
        )
        self.assertIn(
            '"schema-generalization" { $SchemaGeneralizationJobPath }',
            self.dispatch,
        )
        self.assertIn(
            '"schema-generalization" { "schema-generalization-job-result.json" }',
            self.dispatch,
        )
        self.assertIn('"schema-generalization" {', self.publication)
        self.assertIn("$referenced.Count -gt 18", self.publication)
        self.assertIn(
            "^schema-generalization-r[1-3]-(empty|alpha|beta|gamma|delta|names)\\.mdb$",
            self.publication,
        )
        self.assertEqual(job.count("foreach ($replica in 1..3)"), 1)
        self.assertNotIn("Compact", job)
        self.assertEqual(plan["document_type"], "dao_schema_generalization_plan")
        self.assertEqual(
            plan["execution"]["checkpoints"],
            ["empty", "alpha", "beta", "gamma", "delta", "names"],
        )
        self.assertEqual(plan["execution"]["replicas"], 3)
        self.assertEqual(plan["execution"]["attempts_per_replica"], 1)
        self.assertEqual(plan["preregistration"]["acquisition_started"], False)
        self.assertFalse(plan["publication"]["compatibility_claim"])
        self.assertFalse(plan["publication"]["support_matrix_movement"])
        self.assertFalse(plan["publication"]["mdb_bytes_committed"])

    def test_multiple_indexes_is_bounded_and_exactly_routed(self) -> None:
        self.assertIn(
            '"multiple-indexes" = "oracle/windows-dao/scripts/dev/MultipleIndexes.DevJob.ps1"',
            self.remote,
        )
        self.assertIn(
            '"multiple-indexes" { $MultipleIndexesJobPath }', self.dispatch
        )
        self.assertIn(
            '"multiple-indexes" { "multiple-indexes-job-result.json" }',
            self.dispatch,
        )
        self.assertIn("multiple_indexes_replicas", self.dispatch)
        self.assertIn("multiple_indexes_replicas", self.remote)
        self.assertIn('"multiple-indexes" {', self.publication)
        self.assertIn(
            '$checkpointNames = @("empty", "one", "two", "three", "composite")',
            self.publication,
        )
        self.assertIn("checkpoints are not an ordered prefix", self.publication)
        self.assertIn("recovery artifact is not the next checkpoint", self.publication)
        self.assertIn("$preMutationAbort", self.publication)
        self.assertIn("15-database bound", self.publication)
        self.assertIn('Get-ChildItem -LiteralPath $Source -File -Filter "*.mdb"', self.publication)
        self.assertIn("$item.Length % 2048", self.publication)
        self.assertIn("$item.Length -gt 131072", self.publication)
        self.assertIn("result exceeds the 4-MiB bound", self.publication)
        self.assertIn("contains a reparse-point MDB", self.publication)
        binding = CLIENT.plan_binding("multiple-indexes")
        self.assertEqual(
            binding.plan.name,
            "multiple-indexes.plan.json",
        )
        self.assertEqual(binding.analyzer.name, "multiple_indexes.py")
        if not binding.plan.is_file() or not binding.analyzer.is_file():
            self.skipTest("Multiple-indexes experiment artifacts are not present yet")
        plan = json.loads(binding.plan.read_text(encoding="utf-8"))
        self.assertEqual(plan["document_type"], "dao_multiple_indexes_plan")
        self.assertEqual(plan["issue"], 150)
        self.assertEqual(plan["execution"]["replicas"], 3)
        self.assertEqual(
            plan["execution"]["checkpoints"],
            ["empty", "one", "two", "three", "composite"],
        )
        self.assertEqual(
            plan["execution"]["bounds"]["maximum_published_databases"], 15
        )
        self.assertIn(
            "oracle/windows-dao/scripts/dev/MultipleIndexes.DevJob.ps1",
            plan["inputs"],
        )
        self.assertIn("oracle/windows-dao/scripts/system_catalog.py", plan["inputs"])
        job = CLIENT.MULTIPLE_INDEXES_JOB.read_text(encoding="utf-8")
        self.assertIn("[ref]$MutationStarted", job)
        self.assertIn("mutation_started = $false", job)
        self.assertIn('phase = "before_create_database"', job)
        self.assertIn("size_after_metadata", job)

    def test_definition_continuation_is_bounded_and_exactly_routed(self) -> None:
        self.assertIn(
            '"definition-continuation" = "oracle/windows-dao/scripts/dev/DefinitionContinuation.DevJob.ps1"',
            self.remote,
        )
        self.assertIn(
            '"definition-continuation" { $DefinitionContinuationJobPath }',
            self.dispatch,
        )
        self.assertIn(
            '"definition-continuation" { "definition-continuation-job-result.json" }',
            self.dispatch,
        )
        self.assertIn("definition_continuation_replicas", self.dispatch)
        self.assertIn("definition_continuation_replicas", self.remote)
        self.assertIn('"definition-continuation" {', self.publication)
        self.assertIn(
            '$checkpointNames = @("empty", "zero", "one", "two")',
            self.publication,
        )
        self.assertIn("checkpoints are not an ordered prefix", self.publication)
        self.assertIn("recovery is not the active next checkpoint", self.publication)
        self.assertIn("12-database bound", self.publication)
        self.assertIn("64-page bound", self.publication)
        binding = CLIENT.plan_binding("definition-continuation")
        self.assertEqual(binding.plan.name, "definition-continuation.plan.json")
        self.assertEqual(binding.analyzer.name, "definition_continuation.py")
        plan = json.loads(binding.plan.read_text(encoding="utf-8"))
        self.assertEqual(plan["issue"], 151)
        self.assertEqual(plan["execution"]["checkpoints"], ["empty", "zero", "one", "two"])
        self.assertEqual(
            plan["execution"]["bounds"]["maximum_published_databases"], 12
        )
        self.assertTrue(
            all(
                isinstance(value, str)
                and len(value) == 64
                and value != "0" * 64
                and all(character in "0123456789abcdef" for character in value)
                for value in plan["inputs"].values()
            )
        )
        job = CLIENT.DEFINITION_CONTINUATION_JOB.read_text(encoding="utf-8")
        self.assertIn('$ScenarioFields = @{ zero = 69; one = 70; two = 140 }', job)

    def test_extended_names_is_batched_bounded_and_exactly_routed(self) -> None:
        self.assertIn(
            '"extended-names" = "oracle/windows-dao/scripts/dev/ExtendedNames.DevJob.ps1"',
            self.remote,
        )
        self.assertIn('"extended-names" { $ExtendedNamesJobPath }', self.dispatch)
        self.assertIn(
            '"extended-names" { "extended-names-job-result.json" }', self.dispatch
        )
        self.assertIn("extended_names_replicas", self.dispatch)
        self.assertIn("extended_names_replicas", self.remote)
        self.assertIn('"extended-names" {', self.publication)
        self.assertIn("Extended-names result must not be a reparse point.", self.publication)
        self.assertIn("43-checkpoint bound", self.publication)
        self.assertIn("129-database bound", self.publication)
        self.assertIn("128-page bound", self.publication)
        binding = CLIENT.plan_binding("extended-names")
        plan = json.loads(binding.plan.read_text(encoding="utf-8"))
        self.assertEqual(plan["issue"], 152)
        self.assertEqual(plan["execution"]["bounds"]["maximum_published_databases"], 129)
        self.assertEqual(plan["execution"]["bounds"]["maximum_pages_per_database"], 128)
        self.assertEqual(len(plan["inputs"]), 9)
        self.assertEqual(
            plan["inputs"],
            {
                relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                for relative in plan["inputs"]
            },
        )
        job = CLIENT.EXTENDED_NAMES_JOB.read_text(encoding="utf-8")
        self.assertIn("$UndefinedSlots = @(0x81, 0x8D, 0x8F, 0x90, 0x9D)", job)
        self.assertIn("for ($batchIndex = 0; $batchIndex -lt 41; $batchIndex++)", job)
        self.assertIn("foreach ($replica in 1..3)", job)
        self.assertIn("[ref]$MutationStarted", job)
        self.assertIn('phase = "before_create_database"', job)
        self.assertIn("size_after_metadata", job)
        self.assertIn("$MaximumPages = 128", job)
        self.assertIn('$failureOperation = "create_tabledef"', job)
        self.assertIn('$failureOperation = "tabledefs_append"', job)
        self.assertIn("$indexes = $table.Indexes", job)

    def test_lvprop_null_is_bounded_and_exactly_routed(self) -> None:
        self.assertIn(
            '"lvprop-null" = "oracle/windows-dao/scripts/dev/LvPropNull.DevJob.ps1"',
            self.remote,
        )
        self.assertIn('"lvprop-null" { $LvPropNullJobPath }', self.dispatch)
        self.assertIn(
            '"lvprop-null" { "lvprop-null-job-result.json" }', self.dispatch
        )
        self.assertIn('"lvprop-null" {', self.publication)
        self.assertIn("9-database bound", self.publication)
        for filename in (
            "candidate-r$replica-fixed.mdb",
            "candidate-r$replica-null.mdb",
            "control-r$replica-alpha.mdb",
        ):
            self.assertIn(filename, self.publication)
        self.assertIn("-FixedCandidatePath", self.dispatch)
        self.assertIn("-NullCandidatePath", self.dispatch)
        if not CLIENT.LVPROP_NULL_JOB.is_file() or not CLIENT.LVPROP_NULL_PLAN.is_file():
            self.skipTest("LvProp-null experiment artifacts are not present yet")
        job = CLIENT.LVPROP_NULL_JOB.read_text(encoding="utf-8")
        plan = json.loads(CLIENT.LVPROP_NULL_PLAN.read_text(encoding="utf-8"))
        manifest = json.loads(
            (ROOT / plan["candidate_source_manifest"]["path"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertIn('OpenDatabase($Path, $false, $true)', job)
        self.assertIn("foreach ($replica in 1..3)", job)
        self.assertLess(
            job.index("$preparedPaths = @{}"), job.index("$replicas = New-Object")
        )
        self.assertIn(
            "Prepared candidate differs from its preregistered identity.", job
        )
        self.assertIn("Recovery observation failed for", job)
        self.assertIn("LvProp-null output MDB exceeds the 64-page bound.", self.publication)
        self.assertIn("LvProp-null output contains a reparse-point MDB.", self.publication)
        self.assertNotIn("CompactDatabase", job)
        self.assertEqual(plan["document_type"], "dao_lvprop_null_plan")
        self.assertEqual(plan["issue"], 149)
        self.assertEqual(plan["execution"]["replicas"], 3)
        self.assertEqual(
            plan["execution"]["bounds"]["maximum_published_databases"], 9
        )
        self.assertEqual(
            set(plan["candidates"]), {"candidate_fixed", "candidate_null"}
        )
        self.assertEqual(
            {entry["filename"] for entry in plan["candidates"].values()},
            {"lvprop-fixed-alpha.mdb", "lvprop-null-alpha.mdb"},
        )
        self.assertEqual(
            {entry["size"] for entry in plan["candidates"].values()}, {23 * 2048}
        )
        self.assertEqual(
            plan["candidates"]["candidate_fixed"]["sha256"],
            "b798de9209637361245703b0132f59c06dd7cb3d051d214415d6ed6a76768df2",
        )
        self.assertEqual(
            plan["candidates"]["candidate_null"]["sha256"],
            "c9d012d6277a0a35ae4248581fc9458d9b270e56277819e84dc7f1f5e8009e21",
        )
        self.assertEqual(manifest["document_type"], "lvprop_null_candidate_sources")
        manifest_path = ROOT / plan["candidate_source_manifest"]["path"]
        self.assertEqual(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            plan["candidate_source_manifest"]["sha256"],
        )

    def test_bootstrap_composer_validation_is_bounded_and_exactly_routed(self) -> None:
        job = CLIENT.BOOTSTRAP_COMPOSER_VALIDATION_JOB.read_text(encoding="utf-8")
        plan = json.loads(
            CLIENT.BOOTSTRAP_COMPOSER_VALIDATION_PLAN.read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (ROOT / plan["candidate_source_manifest"]["path"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(
            '"bootstrap-composer-validation" = "oracle/windows-dao/scripts/dev/BootstrapComposerValidation.DevJob.ps1"',
            self.remote,
        )
        self.assertIn(
            '"bootstrap-composer-validation" { $BootstrapComposerValidationJobPath }',
            self.dispatch,
        )
        self.assertIn('"bootstrap-composer-validation" {', self.publication)
        self.assertIn("9-database bound", self.publication)
        self.assertIn('OpenDatabase($Path, $false, $true)', job)
        self.assertIn('detail.Replace($fullPath, "<DATABASE>")', job)
        self.assertIn("foreach ($replica in 1..3)", job)
        self.assertNotIn("CompactDatabase", job)
        self.assertEqual(plan["execution"]["attempts"], 1)
        self.assertEqual(plan["execution"]["replicas"], 3)
        self.assertEqual(
            plan["execution"]["bounds"]["maximum_published_databases"], 9
        )
        self.assertEqual(
            hashlib.sha256(
                (ROOT / plan["candidate_source_manifest"]["path"]).read_bytes()
            ).hexdigest(),
            plan["candidate_source_manifest"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256(
                CLIENT.BOOTSTRAP_COMPOSER_VALIDATION_PLAN.read_bytes()
            ).hexdigest(),
            "11fd306504782f6403f6fa44d6ec805bb1385b5725f1dad2dfc841c3863634ec",
        )

    def test_bootstrap_detail_normalization_covers_failure_and_repair_surfaces(self) -> None:
        job = CLIENT.BOOTSTRAP_LAYOUT_JOB.read_text(encoding="utf-8")
        helper_start = job.index("function ConvertTo-BoundedDetail")
        helper_end = job.index("function New-ArtifactObservation")
        helper = job[helper_start:helper_end]

        self.assertIn("$MaximumDetailCharacters = 512", job)
        self.assertIn("No additional detail was reported.", helper)
        self.assertIn("$maximumSuffixCharacters = 192", helper)
        self.assertIn(
            "$text.Substring(0, $MaximumDetailCharacters - $ellipsis.Length)",
            helper,
        )
        self.assertIn("$suffixText = $suffixText.Substring(", helper)
        self.assertIn("-Suffix $repairSuffix", job)
        self.assertIn(
            '-Suffix ("Working-file cleanup failed: " + $_.Exception.Message)',
            job,
        )
        self.assertNotIn("$Observation.detail +=", job)

        exception_detail = '$_.Exception.GetType().FullName + ": " + $_.Exception.Message'
        self.assertEqual(job.count(exception_detail), 4)
        offset = 0
        while (position := job.find(exception_detail, offset)) >= 0:
            self.assertIn(
                "ConvertTo-BoundedDetail",
                job[max(0, position - 120) : position],
            )
            offset = position + len(exception_detail)

    def test_bootstrap_timestamp_pages_use_floor_division(self) -> None:
        job = CLIENT.BOOTSTRAP_LAYOUT_JOB.read_text(encoding="utf-8")

        self.assertEqual(38381 // 2048, 18)
        self.assertIn(
            "[int][Math]::Floor([double]$Start / [double]$PageSize)", job
        )
        self.assertEqual(job.count("Get-PageForRange -Start $offset"), 2)
        self.assertIn("Variant range crosses its declared page.", job)
        self.assertNotIn("[int]($offset / $PageSize)", job)


if __name__ == "__main__":
    unittest.main()
