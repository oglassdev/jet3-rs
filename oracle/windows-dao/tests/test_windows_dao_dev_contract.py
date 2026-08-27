from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


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


class WindowsDaoDevRemoteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.remote = REMOTE_PATH.read_text(encoding="utf-8")
        cls.dispatch = DISPATCH_PATH.read_text(encoding="utf-8")
        cls.publication = PUBLICATION_PATH.read_text(encoding="utf-8")

    def test_remote_is_exploratory_and_allowlisted(self) -> None:
        self.assertIn(
            '[ValidateSet("provider-probe", "create-empty", "opening-matrix", "allocation-map", "catalog", "table-definition", "row", "value")]',
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
        self.assertIn('$Job -in @("catalog", "table-definition", "row", "value")', self.remote)
        self.assertIn('[ValidateSet("catalog", "table-definition", "row", "value")]', self.dispatch)
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
        self.assertIn('$Job -in @("catalog", "table-definition", "row", "value")', self.remote)
        self.assertIn('[ValidateSet("catalog", "table-definition", "row", "value")]', self.dispatch)
        self.assertIn("$MaximumDatabaseBytes = 4MB", value)
        self.assertIn("foreach ($replica in 1..3)", value)
        self.assertIn("$LongLengths = @(32, 512, 2048, 4096)", value)
        for scenario in ("scalars", "cp1252", "cp1251", "memo", "ole"):
            self.assertIn(scenario, value.lower())
            self.assertIn(scenario, self.publication.lower())
        self.assertNotIn("CompactDatabase", value)


if __name__ == "__main__":
    unittest.main()
