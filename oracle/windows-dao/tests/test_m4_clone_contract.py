from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "m4" / "M4.Clone.ps1"
POWERSHELL = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


def ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class M4CloneSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MODULE.read_text(encoding="utf-8")

    def test_clone_is_controller_only_and_has_no_dao_or_publication(self) -> None:
        for prohibited in (
            "[Activator]::CreateInstance",
            "CreateDatabase",
            "OpenDatabase",
            "CompactDatabase",
            "Publish-M1",
            "ConvertTo-Json",
            "[IO.File]::Copy",
            "Copy-Item",
        ):
            self.assertNotIn(prohibited, self.source)

    def test_clone_has_fixed_byte_and_buffer_ceilings(self) -> None:
        self.assertIn("$script:M4CloneMinimumBytes = 2048", self.source)
        self.assertIn("$script:M4CloneMaximumBytes = 1MB", self.source)
        self.assertIn("$script:M4CloneBufferBytes = 65536", self.source)
        self.assertIn(
            "$sourceBefore.bytes -lt $script:M4CloneMinimumBytes",
            self.source,
        )
        self.assertLess(
            self.source.index(
                "$sourceBefore.bytes -lt $script:M4CloneMinimumBytes"
            ),
            self.source.index("$output = New-Object IO.FileStream("),
        )
        self.assertIn("$MaximumBytes -gt $script:M4CloneMaximumBytes", self.source)
        self.assertNotIn("ReadAllBytes", self.source)
        self.assertNotIn("MemoryStream", self.source)

    def test_source_is_opened_exclusively_and_destination_is_create_new(self) -> None:
        self.assertIn("[IO.FileMode]::Open", self.source)
        self.assertIn("[IO.FileAccess]::Read", self.source)
        self.assertIn("[IO.FileShare]::None", self.source)
        self.assertIn("[IO.FileMode]::CreateNew", self.source)
        self.assertIn("[IO.FileAccess]::Write", self.source)
        self.assertIn("[IO.FileOptions]::WriteThrough", self.source)
        self.assertIn("$output.Flush($true)", self.source)

    def test_clone_performs_three_bounded_hash_passes(self) -> None:
        self.assertIn("$sourceShaBefore", self.source)
        self.assertIn("$sourceAfter = Get-M4CloneStreamSha256", self.source)
        self.assertIn("$destinationHash = Get-M4CloneStreamSha256", self.source)
        self.assertIn("$sourceShaBefore -cne $sourceAfter.sha256", self.source)
        self.assertIn("$sourceShaBefore -cne $destinationHash.sha256", self.source)

    def test_identity_comes_from_windows_handles(self) -> None:
        self.assertIn("GetFileInformationByHandle", self.source)
        self.assertIn("GetFinalPathNameByHandle", self.source)
        self.assertIn("VolumeSerialNumber", self.source)
        self.assertIn("FileIndexHigh", self.source)
        self.assertIn("FileIndexLow", self.source)
        self.assertIn("NumberOfLinks", self.source)
        self.assertIn("Test-M4CloneSameIdentity", self.source)

    def test_clone_rejects_path_confusion_and_escape(self) -> None:
        for fragment in (
            "absolute local Windows path, not UNC",
            "alternate data stream paths are forbidden",
            "path aliases and non-canonical paths are forbidden",
            "path escapes the controller root",
            "forbidden reparse point",
            "Source and destination path aliases are forbidden",
        ):
            self.assertIn(fragment, self.source)

    def test_failure_cleans_only_a_created_destination(self) -> None:
        self.assertIn("$destinationCreated = $false", self.source)
        self.assertIn("$destinationCreated = $true", self.source)
        self.assertIn("-not $verified -and $destinationCreated", self.source)
        self.assertIn("Remove-M4ClonePartialDestination", self.source)
        self.assertIn("cleanup refused an identity replacement", self.source)
        self.assertIn('-Phase "after_destination_create"', self.source)

    def test_observation_is_bounded_and_does_not_overclaim_com_state(self) -> None:
        for field in (
            "started_at_utc",
            "completed_at_utc",
            "source_sha256_before_clone",
            "source_sha256_after_clone",
            "destination_sha256",
            "source_file_identity",
            "destination_file_identity",
            "all_hashes_equal",
            "exact_byte_clone",
            "same_volume",
            "distinct_file_identity",
        ):
            self.assertIn(field, self.source)
        self.assertNotIn("reopen_bindings_verified_before_com", self.source)
        self.assertNotIn("completed_before_reopen_com", self.source)
        self.assertNotIn("creator_closed_before_clone", self.source)
        self.assertNotIn("source_immutable_during_clone", self.source)
        self.assertNotIn("source_unchanged_after_clone", self.source)

    def test_module_stays_below_repository_file_limit(self) -> None:
        self.assertLess(len(self.source.splitlines()), 800)


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "Windows required")
class M4CloneWindowsFunctionalTests(unittest.TestCase):
    maxDiff = None

    def run_ps(self, body: str) -> subprocess.CompletedProcess[str]:
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
        )

    def invoke(
        self,
        root: Path,
        source: object,
        destination: object,
        *,
        maximum_bytes: int = 1 << 20,
    ) -> str:
        return (
            "$observation=Invoke-M4BoundedClone "
            f"-ControllerRoot {ps_quote(root)} "
            f"-SourcePath {ps_quote(source)} "
            f"-DestinationPath {ps_quote(destination)} "
            f"-MaximumBytes {maximum_bytes};"
            "$observation|ConvertTo-Json -Depth 10 -Compress"
        )

    def test_exact_clone_returns_three_way_identity_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample = root / "sample"
            sample.mkdir()
            source = sample / "creator.mdb"
            destination = sample / "reopen.mdb"
            payload = bytes(range(256)) * 16
            source.write_bytes(payload)

            result = self.run_ps(self.invoke(root, source, destination))
            self.assertEqual(result.returncode, 0, result.stderr)
            observation = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(
                set(observation),
                {
                    "started_at_utc",
                    "completed_at_utc",
                    "source_path",
                    "destination_path",
                    "source_bytes",
                    "destination_bytes",
                    "source_sha256_before_clone",
                    "source_sha256_after_clone",
                    "destination_sha256",
                    "source_file_identity",
                    "destination_file_identity",
                    "all_hashes_equal",
                    "exact_byte_clone",
                    "source_reparse_free",
                    "destination_reparse_free",
                    "no_hardlink",
                    "same_volume",
                    "distinct_file_identity",
                    "status",
                },
            )
            digest = hashlib.sha256(payload).hexdigest()
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(observation["source_bytes"], len(payload))
            self.assertEqual(observation["destination_bytes"], len(payload))
            self.assertEqual(
                {
                    observation["source_sha256_before_clone"],
                    observation["source_sha256_after_clone"],
                    observation["destination_sha256"],
                },
                {digest},
            )
            self.assertEqual(observation["source_path"], "sample/creator.mdb")
            self.assertEqual(observation["destination_path"], "sample/reopen.mdb")
            self.assertNotEqual(
                observation["source_file_identity"]["file_index"],
                observation["destination_file_identity"]["file_index"],
            )
            self.assertEqual(
                observation["source_file_identity"]["volume_serial_number"],
                observation["destination_file_identity"]["volume_serial_number"],
            )
            self.assertEqual(observation["source_file_identity"]["link_count"], 1)
            self.assertEqual(
                observation["destination_file_identity"]["link_count"], 1
            )
            self.assertLessEqual(
                observation["started_at_utc"], observation["completed_at_utc"]
            )

    def test_existing_destination_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "creator.mdb"
            destination = root / "reopen.mdb"
            source.write_bytes(b"source")
            destination.write_bytes(b"retained")

            result = self.run_ps(
                "try{"
                + self.invoke(root, source, destination)
                + "exit 9}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 7}"
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(destination.read_bytes(), b"retained")

    def test_locked_source_is_rejected_before_destination_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "creator.mdb"
            destination = root / "reopen.mdb"
            source.write_bytes(b"closed-source-required")
            body = (
                f"$lock=New-Object IO.FileStream({ps_quote(source)},"
                "[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);"
                "try{"
                + self.invoke(root, source, destination)
                + "exit 9}catch{exit 7}finally{$lock.Dispose()}"
            )
            result = self.run_ps(body)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse(destination.exists())

    def test_injected_post_create_failure_cleans_partial_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "creator.mdb"
            destination = root / "reopen.mdb"
            source.write_bytes(b"c" * 2048)
            body = (
                "$fault={param($phase)"
                "if($phase -ceq 'after_destination_create'){"
                "throw 'injected clone failure'}};"
                "try{Invoke-M4BoundedClone "
                f"-ControllerRoot {ps_quote(root)} "
                f"-SourcePath {ps_quote(source)} "
                f"-DestinationPath {ps_quote(destination)} "
                "-FaultInjector $fault;exit 9}catch{exit 7}"
            )
            result = self.run_ps(body)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse(destination.exists())

    def test_oversize_and_hardlinked_sources_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oversize = root / "oversize.mdb"
            oversize.write_bytes(b"x" * 4097)
            oversize_destination = root / "oversize-clone.mdb"
            result = self.run_ps(
                "try{"
                + self.invoke(
                    root,
                    oversize,
                    oversize_destination,
                    maximum_bytes=4096,
                )
                + "exit 9}catch{exit 7}"
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse(oversize_destination.exists())

            source = root / "creator.mdb"
            link = root / "creator-link.mdb"
            destination = root / "reopen.mdb"
            source.write_bytes(b"h" * 2048)
            os.link(source, link)
            result = self.run_ps(
                "try{"
                + self.invoke(root, source, destination)
                + "exit 9}catch{exit 7}"
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse(destination.exists())

    def test_source_one_byte_below_database_minimum_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "creator.mdb"
            destination = root / "reopen.mdb"
            source.write_bytes(b"x" * 2047)

            result = self.run_ps(
                "try{"
                + self.invoke(root, source, destination)
                + "exit 9}catch{exit 7}"
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse(destination.exists())

    def test_alias_ads_unc_and_escape_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            root.mkdir()
            source = root / "creator.mdb"
            source.write_bytes(b"path-contract")
            outside = base / "outside.mdb"
            outside.write_bytes(b"outside")
            cases = (
                (source, source),
                (f"{source}:stream", root / "ads-clone.mdb"),
                (r"\\localhost\C$\creator.mdb", root / "unc-clone.mdb"),
                (outside, root / "escape-clone.mdb"),
            )
            for candidate_source, destination in cases:
                with self.subTest(source=candidate_source):
                    result = self.run_ps(
                        "try{"
                        + self.invoke(root, candidate_source, destination)
                        + "exit 9}catch{exit 7}"
                    )
                    self.assertEqual(result.returncode, 7, result.stderr)
                    if Path(destination) != source:
                        self.assertFalse(Path(destination).exists())

    def test_reparse_source_is_rejected_before_destination_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.mdb"
            source = root / "creator-link.mdb"
            destination = root / "reopen.mdb"
            target.write_bytes(b"reparse-target")
            try:
                source.symlink_to(target)
            except OSError as error:
                self.skipTest(f"Windows symlink creation unavailable: {error}")

            result = self.run_ps(
                "try{"
                + self.invoke(root, source, destination)
                + "exit 9}catch{exit 7}"
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
