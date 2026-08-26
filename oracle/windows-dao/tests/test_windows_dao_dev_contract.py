from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest import mock
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
        self.assertEqual(tuple(job.choices), ("provider-probe", "create-empty"))
        destinations = {action.dest for action in parser._actions}
        self.assertNotIn("command", destinations)
        self.assertNotIn("script", destinations)

    def test_invocation_uses_x86_powershell_and_encoded_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            CLIENT.validate_args(args)
            script = CLIENT.invocation_script(args)
            self.assertIn("SysWOW64", script)
            self.assertNotIn(str(root), script)
            command = CLIENT.ssh_command(args)
            encoded = command[-1]
            decoded = base64.b64decode(encoded).decode("utf-16-le")
            self.assertEqual(decoded, script)
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertIn("BatchMode=yes", command)

    def test_staged_request_hashes_sources_and_marks_dirty_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            CLIENT.validate_args(args)
            with mock.patch.object(
                CLIENT, "git", side_effect=("a" * 40, " M local-change")
            ):
                request_dir = CLIENT.stage_request(args)
            request = json.loads((request_dir / "request.json").read_text())
            self.assertIs(request["development_only"], True)
            self.assertIs(request["client"]["dirty"], True)
            self.assertEqual(
                request["sources"]["runner"]["sha256"],
                CLIENT.sha256(request_dir / CLIENT.REMOTE_RUNNER.name),
            )

    def test_manifest_validation_recomputes_every_file_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity"
            identity.write_text("private", encoding="utf-8")
            args = self.args(root, identity)
            CLIENT.validate_args(args)
            output = root / "outbox" / args.run_id
            output.mkdir(parents=True)
            result = output / "result.json"
            result.write_text("{}\n", encoding="utf-8")
            manifest = {
                "document_type": "jet3_windows_dev_manifest",
                "development_only": True,
                "job": args.job,
                "run_id": args.run_id,
                "status": "blocked",
                "files": [
                    {
                        "path": result.name,
                        "size": result.stat().st_size,
                        "sha256": CLIENT.sha256(result),
                    }
                ],
            }
            (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(CLIENT.validated_manifest(args)["status"], "blocked")
            result.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(CLIENT.DevClientError, "identity differs"):
                CLIENT.validated_manifest(args)

            result.write_text("{}\n", encoding="utf-8")
            manifest["files"][0]["size"] = result.stat().st_size
            manifest["files"][0]["sha256"] = CLIENT.sha256(result)
            manifest["status"] = "ready-ish"
            (output / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(CLIENT.DevClientError, "invalid status"):
                CLIENT.validated_manifest(args)

    def test_validation_rejects_missing_identity_and_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = CLIENT.parser().parse_args(
                [
                    "provider-probe",
                    "--user",
                    "jet3runner",
                    "--shared-root",
                    str(root),
                    "--remote-shared-root",
                    r"Z:\safe\..\escape",
                ]
            )
            with self.assertRaises(CLIENT.DevClientError):
                CLIENT.validate_args(args)


class WindowsDaoDevRemoteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.remote = REMOTE_PATH.read_text(encoding="utf-8")

    def test_remote_is_exploratory_allowlisted_and_hash_bound(self) -> None:
        self.assertIn('$AllowedJobs = @("provider-probe", "create-empty")', self.remote)
        self.assertIn("development_only = $true", self.remote)
        self.assertIn("Get-LowerSha256 -Path $PSCommandPath", self.remote)
        self.assertIn("sources.provider_probe.sha256", self.remote)
        self.assertNotIn("Invoke-Expression", self.remote)
        self.assertNotIn("ScriptBlock::Create", self.remote)

    def test_database_is_closed_before_shared_publication(self) -> None:
        close = self.remote.index("$database.Close()")
        publish = self.remote.index("Publish-DevelopmentOutput -Source")
        self.assertLess(close, publish)
        self.assertIn("[IO.Directory]::Move($staging, $Destination)", self.remote)


if __name__ == "__main__":
    unittest.main()
