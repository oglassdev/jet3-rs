from __future__ import annotations

import base64
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
            decoded = base64.b64decode(command[-1]).decode("utf-16-le")
            self.assertIn("SysWOW64", decoded)
            self.assertIn("-Job ([string]$c.job)", decoded)
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
            self.assertEqual(
                {path.name for path in staged.iterdir()},
                {
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
                },
            )

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

    def test_bootstrap_layout_binds_and_verifies_the_active_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            args.job = "bootstrap-layout"
            CLIENT.validate_args(args)
            self.assertEqual(
                args.plan_sha256,
                hashlib.sha256(CLIENT.BOOTSTRAP_LAYOUT_PLAN.read_bytes()).hexdigest(),
            )
            invocation = CLIENT.invocation_script(args)
            self.assertIn("-PlanSha256 ([string]$c.plan_sha256)", invocation)
            self.assertIn("-PlanPath ([string]$c.plan)", invocation)
            staged = CLIENT.stage_job(args)
            self.assertTrue((staged / CLIENT.BOOTSTRAP_LAYOUT_PLAN.name).is_file())
            self.assertTrue((staged / CLIENT.BOOTSTRAP_LAYOUT_ANALYZER.name).is_file())

            altered_plan = root / "altered.plan.json"
            altered_plan.write_text(
                json.dumps(
                    {
                        "document_type": "dao_bootstrap_layout_sufficiency_plan",
                        "issue": 100,
                        "development_only": True,
                        "inputs": {"scripts/windows-dao-dev.py": "0" * 64},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(CLIENT, "BOOTSTRAP_LAYOUT_PLAN", altered_plan):
                with self.assertRaisesRegex(
                    CLIENT.DevClientError, "differs from its plan"
                ):
                    CLIENT.verified_bootstrap_plan_sha256()


class WindowsDaoDevRemoteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.remote = REMOTE_PATH.read_text(encoding="utf-8")
        cls.dispatch = DISPATCH_PATH.read_text(encoding="utf-8")
        cls.publication = PUBLICATION_PATH.read_text(encoding="utf-8")

    def test_remote_is_exploratory_and_allowlisted(self) -> None:
        self.assertIn(
            '[ValidateSet("provider-probe", "create-empty", "opening-matrix", "allocation-map", "catalog", "table-definition", "row", "value", "index", "bootstrap-layout")]',
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
        self.assertIn('$Job -in @("catalog", "table-definition", "row", "value", "index", "bootstrap-layout")', self.remote)
        self.assertIn('[ValidateSet("catalog", "table-definition", "row", "value", "index", "bootstrap-layout")]', self.dispatch)
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
        self.assertIn('$Job -in @("catalog", "table-definition", "row", "value", "index", "bootstrap-layout")', self.remote)
        self.assertIn('[ValidateSet("catalog", "table-definition", "row", "value", "index", "bootstrap-layout")]', self.dispatch)
        self.assertIn("$MaximumDatabaseBytes = 4MB", value)
        self.assertIn("foreach ($replica in 1..3)", value)
        self.assertIn("$LongLengths = @(32, 512, 2048, 4096)", value)
        for scenario in ("scalars", "cp1252", "cp1251", "memo", "ole"):
            self.assertIn(scenario, value.lower())
            self.assertIn(scenario, self.publication.lower())
        self.assertNotIn("CompactDatabase", value)

    def test_index_job_is_staged_bounded_and_never_compacts(self) -> None:
        index = CLIENT.INDEX_JOB.read_text(encoding="utf-8")
        self.assertIn('$Job -in @("catalog", "table-definition", "row", "value", "index", "bootstrap-layout")', self.remote)
        self.assertIn('[ValidateSet("catalog", "table-definition", "row", "value", "index", "bootstrap-layout")]', self.dispatch)
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
        for relative, expected in plan["inputs"].items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, relative)

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
