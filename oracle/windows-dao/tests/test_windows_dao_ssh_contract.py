from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock
import unittest


ROOT = Path(__file__).resolve().parents[3]
CLIENT_PATH = ROOT / "scripts" / "windows-dao-ssh.py"
REMOTE = (
    ROOT
    / "oracle"
    / "windows-dao"
    / "scripts"
    / "remote"
    / "Invoke-Jet3DaoSshJob.ps1"
)
PROCESS = REMOTE.with_name("Remote.Process.ps1")
SPEC = importlib.util.spec_from_file_location("windows_dao_ssh", CLIENT_PATH)
assert SPEC is not None and SPEC.loader is not None
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)


class WindowsDaoSshClientTests(unittest.TestCase):
    def test_exact_binding_requires_clean_and_remotely_advertised_head(self) -> None:
        commit = "a" * 40
        with mock.patch.object(
            CLIENT,
            "git",
            side_effect=(
                "",
                commit,
                "https://github.com/oglassdev/jet3-rs.git",
                f"{commit}\trefs/heads/main",
            ),
        ):
            self.assertEqual(
                CLIENT.exact_pushed_binding("origin", None),
                (commit, "https://github.com/oglassdev/jet3-rs.git"),
            )
        with mock.patch.object(CLIENT, "git", return_value="?? generated"):
            with self.assertRaisesRegex(CLIENT.ClientError, "tracked changes"):
                CLIENT.exact_pushed_binding("origin", None)
        with mock.patch.object(
            CLIENT,
            "git",
            side_effect=(
                "?? artifacts/result.zip\0",
                commit,
                "https://example.test/repo.git",
                f"{commit}\trefs/heads/main",
            ),
        ):
            self.assertEqual(
                CLIENT.exact_pushed_binding("origin", None)[0], commit
            )
        with mock.patch.object(
            CLIENT,
            "git",
            side_effect=("", commit, "https://example.test/repo.git", ""),
        ):
            with self.assertRaisesRegex(CLIENT.ClientError, "not advertised"):
                CLIENT.exact_pushed_binding("origin", None)

    def test_invocation_transports_values_as_base64_json(self) -> None:
        repository = "https://example.test/owner/repository.git"
        script = CLIENT.invocation_script(
            run_id="20260819T120000Z-ssh-dao",
            job="m1-controlled",
            repository_url=repository,
            commit="b" * 40,
            remote_root=r"D:\bounded-runs",
            timeout=120,
            maximum_output=1048576,
            maximum_artifact=314572800,
            entrypoint_sha256="d" * 64,
            process_module_sha256="e" * 64,
        )
        self.assertNotIn(repository, script)
        marker = "FromBase64String('"
        encoded = script.split(marker, 1)[1].split("')", 1)[0]
        config = json.loads(base64.b64decode(encoded))
        self.assertEqual(config["job"], "m1-controlled")
        self.assertEqual(config["repository_url"], repository)
        self.assertEqual(config["entrypoint_sha256"], "d" * 64)
        self.assertEqual(config["process_module_sha256"], "e" * 64)
        self.assertLess(script.index("Get-FileHash"), script.index("& $entry"))
        self.assertNotIn("Invoke-Expression", script)

    def test_uploaded_scripts_must_match_the_bound_commit(self) -> None:
        local_hashes = ("a" * 64, "b" * 64)
        with (
            mock.patch.object(CLIENT, "sha256", side_effect=local_hashes),
            mock.patch.object(
                CLIENT, "committed_file_sha256", side_effect=local_hashes
            ),
        ):
            self.assertEqual(CLIENT.bound_script_hashes("c" * 40), local_hashes)

        with (
            mock.patch.object(CLIENT, "sha256", side_effect=local_hashes),
            mock.patch.object(
                CLIENT,
                "committed_file_sha256",
                side_effect=("a" * 64, "f" * 64),
            ),
        ):
            with self.assertRaisesRegex(CLIENT.ClientError, "bound commit"):
                CLIENT.bound_script_hashes("c" * 40)

    def test_oversized_download_is_terminated_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "partial.zip"
            writer = (
                "import pathlib,sys,time;"
                "p=pathlib.Path(sys.argv[1]);"
                "f=p.open('wb');"
                "[(f.write(b'x'*8192),f.flush(),time.sleep(.02)) "
                "for _ in range(100)]"
            )
            with self.assertRaisesRegex(CLIENT.ClientError, "file limit"):
                CLIENT.run_bounded(
                    (sys.executable, "-c", writer, str(destination)),
                    timeout=5,
                    maximum_output=4096,
                    watched_file=destination,
                    maximum_file_bytes=4096,
                )
            self.assertFalse(destination.exists())

    def test_watched_download_is_removed_on_late_size_timeout_and_output_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            late = root / "late.zip"
            with self.assertRaisesRegex(CLIENT.ClientError, "file limit"):
                CLIENT.run_bounded(
                    (
                        sys.executable,
                        "-c",
                        "import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(b'x'*8192)",
                        str(late),
                    ),
                    timeout=5,
                    maximum_output=4096,
                    watched_file=late,
                    maximum_file_bytes=4096,
                )
            self.assertFalse(late.exists())

            timed = root / "timed.zip"
            with self.assertRaisesRegex(CLIENT.ClientError, "timeout"):
                CLIENT.run_bounded(
                    (
                        sys.executable,
                        "-c",
                        "import pathlib,sys,time;"
                        "pathlib.Path(sys.argv[1]).write_bytes(b'x');time.sleep(5)",
                        str(timed),
                    ),
                    timeout=1,
                    maximum_output=4096,
                    watched_file=timed,
                    maximum_file_bytes=4096,
                )
            self.assertFalse(timed.exists())

            noisy = root / "noisy.zip"
            with self.assertRaisesRegex(CLIENT.ClientError, "output limit"):
                CLIENT.run_bounded(
                    (
                        sys.executable,
                        "-c",
                        "import pathlib,sys;"
                        "pathlib.Path(sys.argv[1]).write_bytes(b'x');print('x'*8192)",
                        str(noisy),
                    ),
                    timeout=5,
                    maximum_output=16,
                    watched_file=noisy,
                    maximum_file_bytes=4096,
                )
            self.assertFalse(noisy.exists())

    def test_cli_has_allowlisted_jobs_and_no_remote_command_option(self) -> None:
        parser = CLIENT.parser()
        job_action = next(action for action in parser._actions if action.dest == "job")
        self.assertEqual(
            tuple(job_action.choices), ("provider-probe", "m1-controlled")
        )
        destinations = {action.dest for action in parser._actions}
        self.assertNotIn("command", destinations)
        self.assertNotIn("remote_command", destinations)

    def test_default_remote_root_is_client_known_and_user_scoped(self) -> None:
        args = CLIENT.parser().parse_args(
            [
                "provider-probe",
                "--host",
                "dao.tailnet.example",
                "--user",
                "jet3runner",
            ]
        )
        CLIENT.validate_args(args)
        self.assertEqual(
            args.remote_root,
            r"C:\Users\jet3runner\AppData\Local\jet3-rs-ssh",
        )

    def test_ssh_commands_require_preverified_host_keys(self) -> None:
        args = CLIENT.parser().parse_args(
            [
                "provider-probe",
                "--host",
                "dao.tailnet.example",
                "--user",
                "jet3runner",
            ]
        )
        self.assertIn("StrictHostKeyChecking=yes", CLIENT.ssh_base(args))
        self.assertIn("StrictHostKeyChecking=yes", CLIENT.scp_base(args))

    def test_repository_urls_are_credential_free_https_only(self) -> None:
        CLIENT.validate_repository_url("https://github.com/owner/repo.git")
        for rejected in (
            "https://token@github.com/owner/repo.git",
            "ssh://git@github.com/owner/repo.git",
            "git@github.com:owner/repo.git",
        ):
            with self.assertRaisesRegex(CLIENT.ClientError, "credential-free"):
                CLIENT.validate_repository_url(rejected)

    def test_archive_path_is_bound_to_complete_remote_root_commit_and_run(self) -> None:
        commit = "c" * 40
        run_id = "20260819T120000Z-ssh-dao"
        remote_root = r"C:\bounded-runs"
        valid = {
            "archive_path": rf"{remote_root}\runs\{commit}\{run_id}\artifacts.zip",
            "archive_sha256": "d" * 64,
            "archive_size": 1024,
            "remote_root": remote_root,
        }
        self.assertEqual(
            CLIENT.validated_archive_identity(
                valid,
                commit=commit,
                run_id=run_id,
                maximum_bytes=2048,
                requested_remote_root=remote_root,
            )[1],
            1024,
        )
        malicious = {
            **valid,
            "archive_path": rf"D:\other\runs\{commit}\{run_id}\artifacts.zip",
        }
        with self.assertRaisesRegex(CLIENT.ClientError, "bound run directory"):
            CLIENT.validated_archive_identity(
                malicious,
                commit=commit,
                run_id=run_id,
                maximum_bytes=2048,
                requested_remote_root=remote_root,
            )
        wrong_root = {**valid, "remote_root": r"D:\other"}
        with self.assertRaisesRegex(CLIENT.ClientError, "does not match"):
            CLIENT.validated_archive_identity(
                wrong_root,
                commit=commit,
                run_id=run_id,
                maximum_bytes=2048,
                requested_remote_root=remote_root,
            )

    def test_remote_result_identity_includes_the_requested_job(self) -> None:
        result = {
            "commit": "c" * 40,
            "run_id": "20260819T120000Z-ssh-dao",
            "job": "provider-probe",
            "exit_code": 0,
        }
        CLIENT.validate_remote_result_identity(
            result,
            commit="c" * 40,
            run_id="20260819T120000Z-ssh-dao",
            job="provider-probe",
            process_exit_code=0,
        )
        with self.assertRaisesRegex(CLIENT.ClientError, "job"):
            CLIENT.validate_remote_result_identity(
                result,
                commit="c" * 40,
                run_id="20260819T120000Z-ssh-dao",
                job="m1-controlled",
                process_exit_code=0,
            )

    def test_downloaded_archive_is_structurally_validated_with_bounded_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "result.zip"
            archive.write_bytes(b"synthetic archive")
            maximum = 2 * 1024 * 1024

            with mock.patch.object(CLIENT, "validate_archive") as validate:
                CLIENT.validate_downloaded_archive(
                    archive,
                    job="provider-probe",
                    commit="c" * 40,
                    run_id="20260819T120000Z-ssh-dao",
                    exit_code=3,
                    maximum_bytes=maximum,
                )

            validate.assert_called_once()
            call = validate.call_args
            self.assertEqual(call.args, (archive,))
            self.assertEqual(call.kwargs["mode"], "provider-probe")
            self.assertEqual(call.kwargs["expected_commit"], "c" * 40)
            self.assertEqual(
                call.kwargs["expected_run_id"], "20260819T120000Z-ssh-dao"
            )
            self.assertEqual(call.kwargs["expected_exit_code"], 3)
            limits = call.kwargs["limits"]
            self.assertLessEqual(limits.maximum_archive_bytes, maximum)
            self.assertLessEqual(limits.maximum_total_compressed_bytes, maximum)
            self.assertLessEqual(limits.maximum_total_uncompressed_bytes, maximum)
            self.assertTrue(archive.exists())

    def test_structurally_invalid_archive_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "result.zip"
            archive.write_bytes(b"invalid archive")
            failure = CLIENT.ArchiveValidationError("invalid inventory")

            with mock.patch.object(
                CLIENT, "validate_archive", side_effect=failure
            ):
                with self.assertRaisesRegex(
                    CLIENT.ClientError, "structurally invalid"
                ) as raised:
                    CLIENT.validate_downloaded_archive(
                        archive,
                        job="m1-controlled",
                        commit="c" * 40,
                        run_id="20260819T120000Z-ssh-dao",
                        exit_code=1,
                        maximum_bytes=2 * 1024 * 1024,
                    )

            self.assertIs(raised.exception.__cause__, failure)
            self.assertFalse(archive.exists())


class WindowsDaoSshRemoteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.remote = REMOTE.read_text(encoding="utf-8")
        cls.process = PROCESS.read_text(encoding="utf-8")

    def test_remote_checkout_is_fresh_clean_and_hash_bound(self) -> None:
        self.assertIn('"clone", "--no-checkout"', self.remote)
        self.assertIn('"checkout", "--detach", $GitCommit', self.remote)
        self.assertIn('"status", "--porcelain=v1"', self.remote)
        self.assertIn("Get-FileHash -LiteralPath $binding[0]", self.remote)
        self.assertIn("Get-FileHash -LiteralPath $binding[1]", self.remote)
        self.assertIn("The remote run directory already exists.", self.remote)

    def test_only_allowlisted_x86_jobs_can_run(self) -> None:
        self.assertIn('[ValidateSet("provider-probe", "m1-controlled")]', self.remote)
        self.assertIn("SysWOW64\\WindowsPowerShell", self.remote)
        self.assertIn("probe-provider.ps1", self.remote)
        self.assertIn("run-m1-controlled.ps1", self.remote)
        self.assertNotIn("Invoke-Expression", self.remote)
        self.assertNotIn("ScriptBlock::Create", self.remote)

    def test_timeout_output_and_artifact_bounds_are_explicit(self) -> None:
        self.assertIn("$TimeoutSeconds -lt 10", self.process)
        self.assertIn("$TimeoutSeconds -gt 120", self.process)
        self.assertIn("$MaximumOutputBytes -gt 1MB", self.process)
        self.assertIn("$MaximumArtifactBytes -gt 1GB", self.remote)
        self.assertIn("Artifact tree exceeds its byte ceiling.", self.remote)
        self.assertIn("Artifact archive exceeds its byte ceiling.", self.remote)
        self.assertIn("taskkill.exe", self.process)
        self.assertIn('/T /F', self.process)
        self.assertIn("Stop-Jet3BootstrapProcessTree", self.process)
        self.assertIn("remote_root = $root", self.remote)

    def test_artifacts_are_packaged_only_for_controlled_exit_codes(self) -> None:
        downloadable = self.remote.index(
            '$downloadable = @(0, 1, 3) -contains [int]$result.exit_code'
        )
        refusal = self.remote.index('if (-not $downloadable)')
        archive = self.remote.index("Compress-Archive")
        self.assertLess(downloadable, refusal)
        self.assertLess(refusal, archive)
        self.assertIn("archive_sha256", self.remote)
        self.assertIn("archive_size", self.remote)


if __name__ == "__main__":
    unittest.main()
