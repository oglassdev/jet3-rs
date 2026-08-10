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

    def test_existing_source_leaf_is_authoritatively_named_before_open(
        self,
    ) -> None:
        regular = self.source.index(
            "Assert-M4CloneRegularFile -Path $source -Label \"Source\""
        )
        canonical = self.source.index(
            "Assert-M4CloneCanonicalExistingLeaf -Path $source "
            "-Label \"Source\""
        )
        opened = self.source.index(
            "$input = New-Object IO.FileStream("
        )
        self.assertLess(regular, canonical)
        self.assertLess(canonical, opened)
        self.assertIn(
            "path alias resolved to a different canonical leaf.",
            self.source,
        )
        # The leaf is judged by GetLongPathNameW, whose documented purpose is
        # converting a path to its long form. On hosted Windows a
        # FindFirstFileW query whose pattern is the 8.3 alias was observed to
        # report that alias in cFileName, so the query corroborates but must
        # not establish canonicality.
        assertion = self.source.index("function Assert-M4CloneCanonicalExistingLeaf")
        expansion = self.source.index(
            "$expanded = Get-M4CloneLongPathString", assertion
        )
        query = self.source.index("FindFirstFile($queryPath", assertion)
        self.assertLess(expansion, query)
        self.assertIn("canonical leaf query returned no name.", self.source)

    def test_clone_performs_three_bounded_hash_passes(self) -> None:
        self.assertIn("$sourceShaBefore", self.source)
        self.assertIn("$sourceAfter = Get-M4CloneStreamSha256", self.source)
        self.assertIn("$destinationHash = Get-M4CloneStreamSha256", self.source)
        self.assertIn("$sourceShaBefore -cne $sourceAfter.sha256", self.source)
        self.assertIn("$sourceShaBefore -cne $destinationHash.sha256", self.source)

    def test_identity_comes_from_windows_handles(self) -> None:
        self.assertIn("GetFileInformationByHandle", self.source)
        self.assertIn("GetFinalPathNameByHandle", self.source)
        self.assertIn("GetLongPathName", self.source)
        self.assertIn("FindFirstFileW", self.source)
        self.assertIn("FindClose", self.source)
        self.assertIn("AlternateFileName", self.source)
        self.assertIn("-ExpandLeafAlias", self.source)
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

    def short_path(self, path: Path) -> Path:
        import ctypes
        from ctypes import wintypes

        query = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        query.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        query.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        characters = query(str(path), buffer, len(buffer))
        if characters == 0 or characters >= len(buffer):
            self.skipTest("Windows did not provide a bounded short path")
        return Path(buffer.value)

    def native_path_probe(self, entry: str, path: object) -> dict:
        """Round-trip one kernel32 path converter without judging the answer."""
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        query = getattr(kernel32, entry)
        query.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        query.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        ctypes.set_last_error(0)
        characters = query(str(path), buffer, len(buffer))
        error = ctypes.get_last_error()
        return {
            "input": str(path),
            "characters": int(characters),
            "value": buffer.value if characters else "",
            "leaf": os.path.basename(buffer.value) if characters else "",
            "last_error": int(error),
        }

    def volume_information(self, path: object) -> dict:
        """Report the filesystem carrying ``path`` so alias support is visible."""
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel32.GetVolumeInformationW
        query.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        query.restype = wintypes.BOOL
        drive = os.path.splitdrive(str(path))[0]
        root = f"{drive}\\" if drive else str(path)
        label = ctypes.create_unicode_buffer(261)
        system = ctypes.create_unicode_buffer(261)
        serial = wintypes.DWORD()
        component = wintypes.DWORD()
        flags = wintypes.DWORD()
        ctypes.set_last_error(0)
        accepted = query(
            root,
            label,
            len(label),
            ctypes.byref(serial),
            ctypes.byref(component),
            ctypes.byref(flags),
            system,
            len(system),
        )
        return {
            "root": root,
            "accepted": bool(accepted),
            "filesystem": system.value,
            "label": label.value,
            "serial": serial.value,
            "maximum_component": component.value,
            "flags": hex(flags.value),
            "last_error": int(ctypes.get_last_error()),
        }

    def short_name_policy(self, path: object) -> dict:
        """Read the 8.3 creation policy; GetVolumeInformationW omits it.

        ``fsutil 8dot3name query`` is read-only but may require elevation, so
        every outcome including access denial is returned as evidence text.
        """
        drive = os.path.splitdrive(str(path))[0]
        report: dict = {}
        targets = [("registry", []), ("volume", [f"{drive}\\"] if drive else [])]
        targets.append(("directory", [str(path)]))
        for label, arguments in targets:
            if not arguments and label != "registry":
                report[label] = "no drive component"
                continue
            try:
                query = subprocess.run(
                    ["fsutil.exe", "8dot3name", "query", *arguments],
                    text=True,
                    capture_output=True,
                    check=False,
                )
            except Exception as error:  # elevation, absence, anything else
                report[label] = f"error={error!r}"
                continue
            report[label] = {
                "returncode": query.returncode,
                "stdout": query.stdout,
                "stderr": query.stderr,
            }
        return report

    def module_alias_probe(self, path: object, long_path: object) -> str:
        """Print what the module computes for ``path`` without asserting.

        ``long_path`` is the known long-named file the alias stands for. It is
        queried alongside the alias so the FindFirstFileW answers for an alias
        pattern and for a long pattern can be compared side by side.
        """
        quoted = ps_quote(path)
        body = (
            f"$supplied={quoted};"
            f"$known={ps_quote(long_path)};"
            "$canonical=$null;"
            "$expandleaf=$null;"
            "Write-Output ('psversion=' + $PSVersionTable.PSVersion.ToString());"
            "Write-Output ('supplied=' + $supplied);"
            "Write-Output ('supplied_leaf=' + [IO.Path]::GetFileName($supplied));"
            "try{Write-Output ('getfullpath=' + [IO.Path]::GetFullPath($supplied))}"
            "catch{Write-Output ('getfullpath_error=' + $_.Exception.Message)};"
            "try{Write-Output ('file_exists=' + [IO.File]::Exists($supplied))}"
            "catch{Write-Output ('file_exists_error=' + $_.Exception.Message)};"
            "try{$canonical=Get-M4CloneLocalFullPath -Path $supplied "
            "-Label 'Diagnostic';"
            "Write-Output ('localfullpath=' + $canonical)}"
            "catch{Write-Output ('localfullpath_error=' + $_.Exception.Message)};"
            "try{$expandleaf=Get-M4CloneLocalFullPath -Path $supplied "
            "-Label 'Diagnostic' -ExpandLeafAlias;"
            "Write-Output ('localfullpath_expandleaf=' + $expandleaf)}"
            "catch{Write-Output ("
            "'localfullpath_expandleaf_error=' + $_.Exception.Message)};"
            "Write-Output ('known_long_path=' + $known);"
            "try{Write-Output ('longpath_supplied=' + "
            "(Get-M4CloneLongPathString -Path $supplied "
            "-Failure 'diagnostic supplied expansion failed'))}"
            "catch{Write-Output ("
            "'longpath_supplied_error=' + $_.Exception.Message)};"
            "if($null -ne $canonical){"
            "try{Write-Output ('longpath_canonical=' + "
            "(Get-M4CloneLongPathString -Path $canonical "
            "-Failure 'diagnostic canonical expansion failed'))}"
            "catch{Write-Output ("
            "'longpath_canonical_error=' + $_.Exception.Message)};"
            "try{Assert-M4CloneCanonicalExistingLeaf -Path $canonical "
            "-Label 'Diagnostic';"
            "Write-Output 'assert_canonical_leaf=accepted'}"
            "catch{Write-Output ("
            "'assert_canonical_leaf_threw=' + $_.Exception.Message)}};"
            # The alias pattern, every path the module derived from it, and the
            # known long pattern are all queried so an alias-pattern answer can
            # be compared against a long-pattern answer for the same file.
            "$candidates=@($supplied,$canonical,$expandleaf,$known,"
            r"('\\?\' + $supplied),('\\?\' + $known));"
            "$probes=@();"
            "foreach($candidate in $candidates){"
            "if([string]::IsNullOrEmpty($candidate)){continue};"
            "$seen=$false;"
            "foreach($existing in $probes){"
            "if($existing.Equals("
            "$candidate,[StringComparison]::OrdinalIgnoreCase)){$seen=$true}};"
            "if(-not $seen){$probes+=$candidate}};"
            "foreach($probe in $probes){"
            "$data=New-Object M4Clone.FindData;"
            "$search=$null;"
            "try{$search=[M4Clone.NativeMethods]::FindFirstFile("
            "$probe,[ref]$data);"
            "if($null -eq $search -or $search.IsInvalid){"
            "Write-Output ('find|' + $probe + '|invalid|' + "
            "[Runtime.InteropServices.Marshal]::GetLastWin32Error())}"
            "else{Write-Output ('find|' + $probe + '|cFileName=' + "
            "[string]$data.FileName + '|cAlternateFileName=' + "
            "[string]$data.AlternateFileName)}}"
            "catch{Write-Output ('find|' + $probe + '|error|' + "
            "$_.Exception.Message)}"
            "finally{if($null -ne $search){$search.Dispose()}}}"
        )
        try:
            probe = self.run_ps(body)
        except Exception as error:  # diagnostics must never mask the assertion
            return f"probe_launch_error={error!r}"
        return (
            f"probe_returncode={probe.returncode!r}\n"
            f"probe_stdout={probe.stdout!r}\n"
            f"probe_stderr={probe.stderr!r}"
        )

    def alias_rejection_evidence(
        self,
        *,
        root: Path,
        source: Path,
        short_source: Path,
        aliased_leaf: Path,
        result: subprocess.CompletedProcess,
    ) -> str:
        """Build failure-path-only evidence for a leaf alias that was accepted."""
        import platform
        import sys

        lines = ["short 8.3 leaf alias was not rejected; evidence follows"]

        def record(label: str, producer) -> None:
            try:
                lines.append(f"{label}={producer()!r}")
            except Exception as error:  # never let evidence mask the failure
                lines.append(f"{label}_error={error!r}")

        record("python_version", lambda: sys.version)
        record("platform", platform.platform)
        record("root", lambda: str(root))
        record("source", lambda: str(source))
        record("short_source", lambda: str(short_source))
        record("aliased_leaf", lambda: str(aliased_leaf))
        record("aliased_leaf_exists", lambda: os.path.exists(str(aliased_leaf)))
        record(
            "samefile",
            lambda: os.path.samefile(str(source), str(aliased_leaf)),
        )
        record("listdir", lambda: sorted(os.listdir(str(root))))
        record(
            "long_of_aliased_leaf",
            lambda: self.native_path_probe("GetLongPathNameW", aliased_leaf),
        )
        record(
            "long_of_source",
            lambda: self.native_path_probe("GetLongPathNameW", source),
        )
        record(
            "long_of_root",
            lambda: self.native_path_probe("GetLongPathNameW", root),
        )
        record(
            "short_of_source",
            lambda: self.native_path_probe("GetShortPathNameW", source),
        )
        record(
            "short_of_aliased_leaf",
            lambda: self.native_path_probe("GetShortPathNameW", aliased_leaf),
        )
        record("volume", lambda: self.volume_information(root))
        record("short_name_policy", lambda: self.short_name_policy(root))
        record(
            "dir_slash_x",
            lambda: subprocess.run(
                ["cmd.exe", "/c", "dir", "/x", str(root)],
                text=True,
                capture_output=True,
                check=False,
            ).stdout,
        )
        lines.append(self.module_alias_probe(aliased_leaf, source))
        lines.append(f"clone_returncode={result.returncode!r}")
        lines.append(f"clone_stdout={result.stdout!r}")
        lines.append(f"clone_stderr={result.stderr!r}")
        return "\n".join(lines)

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

    def test_platform_provided_short_ancestor_alias_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "controller-root-for-short-alias"
            root.mkdir()
            short_root = self.short_path(root)
            if os.path.normcase(str(short_root)) == os.path.normcase(str(root)):
                self.skipTest("8.3 aliases are unavailable on this volume")
            source = root / "creator.mdb"
            source.write_bytes(b"s" * 2048)
            aliased_source = short_root / source.name
            aliased_destination = short_root / "reopen.mdb"

            result = self.run_ps(
                self.invoke(short_root, aliased_source, aliased_destination)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "reopen.mdb").read_bytes(), b"s" * 2048)

    def test_platform_short_file_leaf_alias_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "creator-database-with-long-name.mdb"
            destination = root / "reopen.mdb"
            source.write_bytes(b"l" * 2048)
            short_source = self.short_path(source)
            aliased_leaf = root / short_source.name
            if os.path.normcase(str(aliased_leaf)) == os.path.normcase(
                str(source)
            ):
                self.skipTest("8.3 file aliases are unavailable on this volume")

            result = self.run_ps(
                "try{"
                + self.invoke(root, aliased_leaf, destination)
                + ";exit 9}catch{"
                "[Console]::Error.WriteLine($_.Exception.Message);exit 7}"
            )
            # Evidence is gathered only when the rejection did not happen, so
            # the passing path performs exactly the work it always did.
            if result.returncode != 7 or "path alias" not in result.stderr:
                evidence = self.alias_rejection_evidence(
                    root=root,
                    source=source,
                    short_source=short_source,
                    aliased_leaf=aliased_leaf,
                    result=result,
                )
            else:
                evidence = result.stderr
            self.assertEqual(result.returncode, 7, evidence)
            self.assertIn("path alias", result.stderr, evidence)
            self.assertFalse(destination.exists(), evidence)

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
