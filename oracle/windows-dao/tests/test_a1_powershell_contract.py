from __future__ import annotations

import hashlib
import json
import os
import subprocess
import struct
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
A1 = SCRIPTS / "a1"
ENTRY = SCRIPTS / "run-a1-controlled.ps1"
CONTROLLER = A1 / "A1.Controller.ps1"
WORKER = A1 / "A1.Worker.ps1"
PAGE_STORE = A1 / "A1.PageStore.ps1"
PROGRESS = A1 / "A1.Progress.ps1"
M1 = SCRIPTS / "m1"
DAO_VALUES = M1 / "M1.DaoValues.ps1"
PUBLICATION = M1 / "M1.Publication.ps1"
PLAN = ROOT / "experiments" / "a1" / "a1-allocation-maps.plan.json"
PAGE_BYTES = 2048
WINDOWS_ROOT = Path(os.environ.get("WINDIR", r"C:\Windows"))
POWERSHELL_CANDIDATES = (
    WINDOWS_ROOT
    / "SysWOW64"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe",
    WINDOWS_ROOT
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe",
)
POWERSHELL = next(
    (candidate for candidate in POWERSHELL_CANDIDATES if candidate.is_file()),
    POWERSHELL_CANDIDATES[0],
)


def function_source(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    end = source.find("\nfunction ", start + 1)
    return source[start:] if end < 0 else source[start:end]


def ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def payload(role: str, row_id: int) -> str:
    seed = f"A1|{role}|{row_id:010d}|"
    return (seed * ((240 + len(seed) - 1) // len(seed)))[:240]


def semantic_digest(role: str, row_ids: set[int]) -> tuple[int, str]:
    hasher = hashlib.sha256()
    for row_id in sorted(row_ids):
        encoded = payload(role, row_id).encode("utf-8")
        hasher.update(struct.pack("<iH", row_id, len(encoded)))
        hasher.update(encoded)
    return len(row_ids), hasher.hexdigest()


def naive_snapshot(path: Path, prior: list[str] | None) -> dict[str, object]:
    data = path.read_bytes()
    assert data and len(data) % PAGE_BYTES == 0
    pages = [
        data[index : index + PAGE_BYTES]
        for index in range(0, len(data), PAGE_BYTES)
    ]
    hashes = [hashlib.sha256(page).hexdigest() for page in pages]
    changed = [
        index
        for index, digest in enumerate(hashes)
        if prior is None or index >= len(prior) or prior[index] != digest
    ]
    if prior is not None:
        changed.extend(range(len(hashes), len(prior)))
    return {
        "database_sha256": hashlib.sha256(data).hexdigest(),
        "ordered_page_sha256": hashes,
        "changed_page_indices": changed,
        "pages": pages,
}


class A1PowerShellSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entry = ENTRY.read_text(encoding="utf-8")
        cls.controller = CONTROLLER.read_text(encoding="utf-8")
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.page_store = PAGE_STORE.read_text(encoding="utf-8")
        cls.progress = PROGRESS.read_text(encoding="utf-8")
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))
        cls.combined = "\n".join(
            (cls.entry, cls.controller, cls.worker, cls.page_store, cls.progress)
        )

    def test_exact_replica_and_checkpoint_schedule_is_plan_driven(self) -> None:
        checkpoints = self.plan["checkpoint_design"]["checkpoint_ids"]
        self.assertEqual(len(checkpoints), 71)
        self.assertEqual(len(checkpoints), len(set(checkpoints)))
        self.assertEqual(self.plan["bounds"]["max_checkpoints_per_replica"], 72)
        self.assertIn("checkpoint_design.checkpoint_ids", self.worker)
        self.assertIn("checkpoint_design.count", self.worker)
        self.assertNotIn("$ids.Count -ne 64", self.combined)
        self.assertNotIn("$ids.Count -ne 67", self.combined)
        self.assertIn(
            "a7fa44cdb24b6f6e0d3884d478d7eef74685aa90ea12eacfff4b459b1da6ab80",
            self.controller,
        )
        self.assertIn("A1 plan bytes differ from the frozen", self.controller)

    def test_all_role_rotations_are_bound_by_the_plan(self) -> None:
        bindings = self.plan["tables"]["role_bindings"]
        self.assertEqual(len(bindings), 3)
        self.assertEqual(
            {tuple(row[role] for role in ("D", "L", "P", "H")) for row in bindings},
            {
                ("A1TAB_A", "A1TAB_B", "A1TAB_C", "A1TAB_D"),
                ("A1TAB_B", "A1TAB_C", "A1TAB_D", "A1TAB_A"),
                ("A1TAB_C", "A1TAB_D", "A1TAB_A", "A1TAB_B"),
            },
        )
        self.assertIn("Plan.tables.role_bindings", self.worker)
        self.assertIn("equal-length table design drifted", self.worker)

    def test_dao_schema_and_deterministic_rows_are_exact(self) -> None:
        for fragment in (
            '$script:A1DbLong = 4',
            '$script:A1DbText = 10',
            '$script:A1DbFixedField = 1',
            'CreateField("Id", $script:A1DbLong)',
            'CreateField("Payload", $script:A1DbText, 240)',
            '$payloadField.Attributes = $script:A1DbFixedField',
            '$idField.Value = [Int32]$id',
            '$payloadField.Value = [string](',
            'growth_batch_rows -ne 32',
            '"A1|$Role|$($Id.ToString(\'D10\'))|"',
            "GetBytes([int]$Id)",
            "GetBytes([uint16]$payloadBytes.Length)",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.combined)
        self.assertNotIn('$field.Value = $Value', self.combined)
        self.assertIn("Id, Payload", self.page_store)
        self.assertIn("ORDER BY Id", self.page_store)

    def test_checkpoint_closes_rereads_and_requires_quiescence(self) -> None:
        checkpoint = self.worker.index("function Add-A1Checkpoint")
        semantic = self.worker.index("Read-A1SemanticTables", checkpoint)
        snapshot = self.worker.index("Read-A1PageSnapshot", semantic)
        self.assertLess(checkpoint, semantic)
        self.assertLess(semantic, snapshot)
        self.assertGreaterEqual(
            self.worker[checkpoint:snapshot].count("Assert-A1Quiescent"), 2
        )
        self.assertIn("A1 DAO lock companion remains after close", self.worker)
        self.assertIn("retained_for_physical_analysis = $false", self.worker)

    def test_checkpoint_progress_is_flushed_and_retained_on_worker_failure(self) -> None:
        for field in ("checkpoint_id", "elapsed_seconds", "page_count"):
            with self.subTest(field=field):
                self.assertIn(field, self.progress)
        self.assertIn("$stream.Flush($true)", self.progress)
        self.assertIn("$script:A1ProgressMaximumBytes = 1MB", self.progress)
        self.assertIn("replica-*.progress.jsonl", self.progress)
        self.assertIn("Open-A1WorkerProgress", self.worker)
        checkpoint = self.worker.index("function Add-A1Checkpoint")
        progress = self.worker.index("Add-A1ProgressRecord", checkpoint)
        prior = self.worker.index("$script:A1PriorCheckpoint = $CheckpointId", checkpoint)
        self.assertLess(prior, progress)
        invoke = self.controller.index("function Invoke-A1ReplicaWorker")
        end = self.controller.index("function Get-A1FileRecord", invoke)
        worker = self.controller[invoke:end]
        self.assertLess(worker.index("New-A1ProgressFile"), worker.index("StartSuspendedInJob"))
        self.assertIn("catch { $primary = $_ }", worker)
        finally_block = worker[worker.index("finally {") :]
        self.assertIn("Copy-A1ProgressFile", finally_block)
        self.assertIn("-DiagnosticsRoot $DiagnosticsRoot", finally_block)
        self.assertIn("[Parameter(Mandatory = $true)][string]$DiagnosticsRoot", self.entry)

    def test_growth_targets_are_crossed_only_by_fixed_batches(self) -> None:
        self.assertIn("do {", self.worker)
        self.assertIn("Add-A1RowBatch -Role $Role", self.worker)
        self.assertIn("} while ($pages -lt $ThresholdPages)", self.worker)
        self.assertIn("target_overshoot_pages", self.worker)
        self.assertIn("$baseline + 128", self.worker)
        self.assertIn('^([LH])_REL_([0-9]{4})$', self.worker)
        self.assertIn('^P_ABS_([0-9]{5})$', self.worker)
        for forbidden in ("retune", "adaptive checkpoint", "batch_rows +="):
            self.assertNotIn(forbidden, self.worker.lower())

    def test_closed_state_probe_collects_only_after_a_sharing_failure(self) -> None:
        database_action = function_source(self.worker, "Invoke-A1WithDatabase")
        closed_probe = function_source(self.worker, "Get-A1ClosedPageCount")
        growth = function_source(self.worker, "Add-A1UntilTarget")
        checkpoint = function_source(self.worker, "Add-A1Checkpoint")
        self.assertNotIn("[GC]::Collect()", database_action)
        self.assertIn("[IO.FileShare]::None", closed_probe)
        self.assertIn("$nativeCode -notin @(32, 33)", closed_probe)
        self.assertEqual(closed_probe.count("[GC]::Collect()"), 1)
        self.assertEqual(closed_probe.count("[GC]::WaitForPendingFinalizers()"), 1)
        self.assertLess(
            growth.index("Add-A1RowBatch -Role $Role"),
            growth.index("Assert-A1Quiescent"),
        )
        self.assertLess(
            growth.index("Assert-A1Quiescent"),
            growth.index("Get-A1ClosedPageCount"),
        )
        self.assertGreaterEqual(checkpoint.count("Assert-A1Quiescent"), 2)

    def test_expected_digest_cache_matches_append_delete_and_restore(self) -> None:
        cache: dict[str, tuple[int, str]] = {}
        rows: set[int] = set()

        def cached(role: str) -> tuple[int, str]:
            if role not in cache:
                cache[role] = semantic_digest(role, rows)
            return cache[role]

        for start in (1, 33, 65):
            rows.update(range(start, start + 32))
            cache.pop("L", None)
            self.assertEqual(cached("L"), semantic_digest("L", rows))
        before_delete = cached("L")
        deleted = {row_id for row_id in rows if row_id % 2 == 0}
        rows.difference_update(deleted)
        cache.pop("L", None)
        self.assertEqual(cached("L"), semantic_digest("L", rows))
        rows.update(deleted)
        cache.pop("L", None)
        self.assertEqual(cached("L"), semantic_digest("L", rows))
        self.assertEqual(cached("L"), before_delete)

        for name in ("Add-A1Table", "Remove-A1Table", "Add-A1RowBatch"):
            with self.subTest(function=name):
                self.assertIn(
                    "Set-A1ExpectedSemanticDirty -Role $Role",
                    function_source(self.worker, name),
                )
        for name in ("Remove-A1AlternatingRows", "Restore-A1AlternatingRows"):
            with self.subTest(function=name):
                self.assertIn(
                    'Set-A1ExpectedSemanticDirty -Role "L"',
                    function_source(self.worker, name),
                )
        self.assertIn("ContainsKey($Role)", self.page_store)
        self.assertIn("New-A1SemanticSha256", self.page_store)

    def test_checkpoint_uses_one_database_session_for_all_semantic_tables(self) -> None:
        all_tables = function_source(self.page_store, "Read-A1SemanticTables")
        one_table = function_source(self.page_store, "Read-A1SemanticTable")
        self.assertEqual(all_tables.count("Invoke-A1WithDatabase -Action"), 1)
        self.assertIn('@("D", "L", "P", "H")', all_tables)
        self.assertIn("ORDER BY Id", one_table)
        self.assertIn('$fields = $recordset.Fields', one_table)
        self.assertIn('$idField = $fields.Item("Id")', one_table)
        self.assertIn('$payloadField = $fields.Item("Payload")', one_table)

    def test_page_store_is_exact_content_addressed_and_reconstructable(self) -> None:
        for fragment in (
            '$script:A1PageBytes = 2048L',
            '"page-store/$Sha256.page"',
            "[IO.FileMode]::CreateNew",
            "Assert-A1ExistingPageBlob",
            "page_sha256 = @($snapshot.hashes)",
            "ordered_page_sha256 = @($snapshot.hashes)",
            "changed_page_indices = @(",
            '"page-indexes/replica-{0:D2}/{1:D2}-{2}.json"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.combined)
        self.assertNotIn("page-store/sha256/", self.combined)

    def test_page_snapshot_reuse_is_exact_and_bounded(self) -> None:
        snapshot = function_source(self.page_store, "Read-A1PageSnapshot")
        self.assertIn("StructuralEqualityComparer", snapshot)
        self.assertIn("$sha = [string]$PriorHashes", snapshot)
        self.assertIn("$pageHash.ComputeHash($page)", snapshot)
        self.assertIn("[Buffer]::BlockCopy", snapshot)
        self.assertIn("$fileHash.TransformBlock", snapshot)
        self.assertIn("$Store.ChangedEntries + $changed.Count", snapshot)
        self.assertIn("$script:A1MaximumPagesPerReplica", snapshot)
        self.assertNotIn("IncrementalHash", self.page_store)
        self.assertIn("$Hash.TransformBlock", self.page_store)
        self.assertIn("$Hash.TransformFinalBlock", self.page_store)

    def test_resource_and_process_ceilings_are_fail_closed(self) -> None:
        expected = {
            "20480": self.combined,
            "262144": self.combined,
            "512MB": self.combined,
            "768MB": self.combined,
            "200000": self.combined,
            "1500000": self.combined,
            "8GB": self.combined,
            "1800": self.controller,
            "7200": self.controller,
            "1MB": self.controller,
            "64KB": self.worker,
        }
        for literal, source in expected.items():
            with self.subTest(literal=literal):
                self.assertIn(literal, source)
        self.assertIn("Stop-BoundedProcessJob", self.controller)
        self.assertIn("StartSuspendedInJob", self.controller)
        self.assertIn("Get-A1CampaignAllowance", self.controller)

    def test_python_runtime_and_long_validation_timeouts_are_audited(self) -> None:
        self.assertIn("A1 requires Python 3.10 or newer", self.controller)
        self.assertIn("preflight-bound Python 3.13 runtime", self.controller)
        self.assertIn("python_version = $PythonVersion", self.controller)
        probe = self.controller.index(
            "$pythonVersion = Assert-A1PythonRuntime -Context $context"
        )
        staging = self.controller.index("New-M1PublicationSession", probe)
        self.assertLess(probe, staging)
        self.assertIn(
            "[ValidateRange(1, 1800)][int]$MaximumSeconds = 120",
            self.controller,
        )
        self.assertIn(
            "Get-A1CampaignAllowance -MaximumSeconds $MaximumSeconds",
            self.controller,
        )
        self.assertIn(
            ') -Label "A1 preregistered analysis" -MaximumSeconds 1800',
            self.controller,
        )
        self.assertIn(
            '-Label "A1 complete bundle validation" -MaximumSeconds 1800',
            self.controller,
        )
        # Two Python calls plus the independently frozen worker allowance.
        self.assertEqual(self.controller.count("-MaximumSeconds 1800"), 3)

    def test_clean_pushed_provider_and_source_identity_gates_precede_work(self) -> None:
        for fragment in (
            "status --porcelain=v1",
            "rev-parse --verify HEAD",
            "hash-object -- $path",
            'ls-remote", "--heads"',
            "Assert-M1RuntimeBinding",
            "Assert-M1CurrentRegistration",
            "provider binary differs from its environment digest",
            "x86 Windows PowerShell 5 Desktop",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.combined)
        self.assertIn(
            'Plan.execution_gate.status -cne "BLOCKED"', self.controller
        )
        self.assertIn("checked_windows_acquisition", self.controller)
        self.assertNotIn('execution_gate.status = "READY"', self.controller)

    def test_publication_is_collision_free_and_complete_only(self) -> None:
        manifest = self.controller.index("Write-A1Manifest")
        publish = self.controller.index("Publish-M1Stage", manifest)
        self.assertLess(manifest, publish)
        self.assertIn("validate-bundle", self.controller[manifest:publish])
        self.assertIn("Remove-A1PrivateStaging", self.controller)
        self.assertIn("-MaxEntries 263000", self.controller)
        self.assertIn("cleanup also", self.controller)
        self.assertIn("inventory_closed = $true", self.controller)
        self.assertIn("hashes_verified = $true", self.controller)
        self.assertIn("paths_closed = $true", self.controller)

    def test_acquisition_assigns_no_physical_meaning_to_bytes(self) -> None:
        forbidden = (
            "allocation map",
            "tdef",
            "usage bitmap",
            "record boundary",
            "page[0]",
            "page[1]",
            "bitmask",
        )
        lowered = "\n".join((self.worker, self.page_store)).lower()
        for term in forbidden:
            with self.subTest(term=term):
                self.assertNotIn(term, lowered)

    def test_no_admin_or_destructive_file_command_is_used(self) -> None:
        for forbidden in (
            "Start-Process -Verb RunAs",
            "Remove-Item",
            "[IO.File]::Delete",
            "Format-Volume",
        ):
            with self.subTest(fragment=forbidden):
                self.assertNotIn(forbidden, self.combined)
        self.assertEqual(self.combined.count("[IO.Directory]::Delete"), 1)
        self.assertIn("Refusing cleanup outside", self.controller)

    def test_snapshot_shrink_and_dao_collections_are_explicit(self) -> None:
        self.assertIn(
            "$PriorHashes.Count -gt $pageCount", self.page_store
        )
        self.assertIn("page_index = [long]$index", self.page_store)
        self.assertIn("$fields = $recordset.Fields", self.combined)
        self.assertIn("$tableDefinitions = $database.TableDefs", self.worker)
        self.assertIn("A1 table definitions release", self.worker)
        self.assertIn('Label "A1 table deletion"', self.worker)

    def test_production_scripts_stay_below_800_lines(self) -> None:
        for path in (ENTRY, CONTROLLER, WORKER, PAGE_STORE, PROGRESS):
            with self.subTest(path=path.name):
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()), 800
                )


@unittest.skipUnless(
    sys.platform == "win32" and POWERSHELL.is_file(),
    "Windows PowerShell 5.1 required",
)
class A1PowerShellWindowsFunctionalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = WORKER.read_text(encoding="utf-8")

    def run_ps(
        self, body: str, *, timeout: int = 60
    ) -> subprocess.CompletedProcess[str]:
        source = (
            "$ErrorActionPreference = 'Stop'\n"
            "Set-StrictMode -Version Latest\n"
            "if ($PSVersionTable.PSVersion.Major -ne 5) { "
            "throw 'Windows PowerShell 5.1 is required.' }\n"
            + body
        )
        with tempfile.TemporaryDirectory(prefix="a1-ps51-test-") as directory:
            script = Path(directory) / "contract.ps1"
            script.write_text(source, encoding="utf-8-sig")
            return subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )

    def test_real_page_snapshot_matches_naive_rehash_and_reconstruction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a1-page-reuse-") as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            database = root / "synthetic.mdb"
            second_path = root / "second.mdb"
            third_path = root / "third.mdb"
            first_bytes = (
                b"A" * PAGE_BYTES + b"B" * PAGE_BYTES + b"C" * PAGE_BYTES
            )
            second_bytes = (
                b"A" * PAGE_BYTES
                + b"X" * PAGE_BYTES
                + b"C" * PAGE_BYTES
                + b"D" * PAGE_BYTES
            )
            third_bytes = b"A" * PAGE_BYTES + b"X" * PAGE_BYTES
            database.write_bytes(first_bytes)
            second_path.write_bytes(second_bytes)
            third_path.write_bytes(third_bytes)

            first_expected = naive_snapshot(database, None)
            first_hashes = first_expected["ordered_page_sha256"]
            self.assertIsInstance(first_hashes, list)
            second_expected = naive_snapshot(second_path, first_hashes)
            second_hashes = second_expected["ordered_page_sha256"]
            self.assertIsInstance(second_hashes, list)
            third_expected = naive_snapshot(third_path, second_hashes)

            body = (
                f". {ps_quote(PUBLICATION)}\n"
                f". {ps_quote(PAGE_STORE)}\n"
                "function Convert-A1TestSnapshot {\n"
                "  param([pscustomobject]$Snapshot)\n"
                "  return [ordered]@{\n"
                "    database_sha256 = [string]$Snapshot.file_sha256\n"
                "    ordered_page_sha256 = @($Snapshot.hashes)\n"
                "    changed_page_indices = @(\n"
                "      $Snapshot.changed_pages | ForEach-Object { "
                "[long]$_.page_index }\n"
                "    )\n"
                "  }\n"
                "}\n"
                f"$session = [pscustomobject]@{{ StagingBundle = "
                f"{ps_quote(bundle)} }}\n"
                "$store = New-A1PageStore -Session $session\n"
                "$first = Read-A1PageSnapshot -Store $store "
                f"-DatabasePath {ps_quote(database)} "
                "-PriorHashes $null -PriorPages $null\n"
                f"[IO.File]::WriteAllBytes({ps_quote(database)}, "
                f"[IO.File]::ReadAllBytes({ps_quote(second_path)}))\n"
                "$second = Read-A1PageSnapshot -Store $store "
                f"-DatabasePath {ps_quote(database)} "
                "-PriorHashes ([string[]]$first.hashes) "
                "-PriorPages ([byte[][]]$first.pages)\n"
                f"[IO.File]::WriteAllBytes({ps_quote(database)}, "
                f"[IO.File]::ReadAllBytes({ps_quote(third_path)}))\n"
                "$third = Read-A1PageSnapshot -Store $store "
                f"-DatabasePath {ps_quote(database)} "
                "-PriorHashes ([string[]]$second.hashes) "
                "-PriorPages ([byte[][]]$second.pages)\n"
                "[ordered]@{\n"
                "  first = Convert-A1TestSnapshot $first\n"
                "  second = Convert-A1TestSnapshot $second\n"
                "  third = Convert-A1TestSnapshot $third\n"
                "} | ConvertTo-Json -Depth 8 -Compress\n"
            )
            result = self.run_ps(body)
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(result.stdout.strip().splitlines()[-1])

            expected_snapshots = {
                "first": first_expected,
                "second": second_expected,
                "third": third_expected,
            }
            raw_snapshots = {
                "first": first_bytes,
                "second": second_bytes,
                "third": third_bytes,
            }
            for name, expected in expected_snapshots.items():
                with self.subTest(snapshot=name):
                    projection = {
                        key: expected[key]
                        for key in (
                            "database_sha256",
                            "ordered_page_sha256",
                            "changed_page_indices",
                        )
                    }
                    self.assertEqual(observed[name], projection)
                    reconstructed = b"".join(
                        (
                            bundle / "page-store" / f"{digest}.page"
                        ).read_bytes()
                        for digest in observed[name]["ordered_page_sha256"]
                    )
                    self.assertEqual(reconstructed, raw_snapshots[name])

    def test_real_one_session_dao_reread_matches_per_table_sessions(self) -> None:
        definitions = "\n\n".join(
            function_source(self.worker, name)
            for name in (
                "Get-A1Payload",
                "Invoke-A1WithDatabase",
                "Add-A1Table",
                "Add-A1RowBatch",
            )
        )
        with tempfile.TemporaryDirectory(prefix="a1-semantic-reread-") as directory:
            database = Path(directory) / "semantic.mdb"
            body = (
                f". {ps_quote(DAO_VALUES)}\n"
                f". {ps_quote(PAGE_STORE)}\n"
                + definitions
                + "\n"
                + "if ([IntPtr]::Size -ne 4) { "
                "throw 'The controlled DAO test requires x86 PowerShell.' }\n"
                "$script:A1DbVersion30 = 32\n"
                "$script:A1DbLong = 4\n"
                "$script:A1DbText = 10\n"
                "$script:A1DbFixedField = 1\n"
                "$script:A1DbOpenSnapshot = 4\n"
                "$script:A1Locale = ';LANGID=0x0409;CP=1252;COUNTRY=0'\n"
                f"$script:A1DatabasePath = {ps_quote(database)}\n"
                "$script:A1RoleNames = @{ D = 'A1TAB_A'; L = 'A1TAB_B'; "
                "P = 'A1TAB_C'; H = 'A1TAB_D' }\n"
                "$script:A1Extant = @{ D = $false; L = $false; "
                "P = $false; H = $false }\n"
                "$script:A1Rows = @{}\n"
                "$script:A1NextId = @{}\n"
                "$script:A1ExpectedSemanticCache = @{}\n"
                "foreach ($role in @('D', 'L', 'P', 'H')) {\n"
                "  $script:A1Rows[$role] = "
                "New-Object 'Collections.Generic.HashSet[int]'\n"
                "  $script:A1NextId[$role] = 1\n"
                "}\n"
                "$script:A1InsertedRows = 0\n"
                "$engine = $null\n"
                "$workspaces = $null\n"
                "$workspace = $null\n"
                "$output = $null\n"
                "try {\n"
                "  $providerType = [Type]::GetTypeFromProgID("
                "'DAO.DBEngine.36', $true)\n"
                "  $engine = [Activator]::CreateInstance($providerType)\n"
                "  $workspaces = $engine.Workspaces\n"
                "  $workspace = $workspaces.Item([int]0)\n"
                "  $script:A1Workspace = $workspace\n"
                "  Invoke-A1WithDatabase -Create -Action { "
                "param($database) } | Out-Null\n"
                "  Add-A1Table -Role 'D'\n"
                "  Add-A1RowBatch -Role 'D'\n"
                "  Add-A1Table -Role 'L'\n"
                "  Add-A1RowBatch -Role 'L'\n"
                "  $script:A1OriginalDatabaseAction = "
                "(Get-Command Invoke-A1WithDatabase).ScriptBlock\n"
                "  function Invoke-A1WithDatabase {\n"
                "    param([scriptblock]$Action, [switch]$Create)\n"
                "    $script:A1MeasuredOpenCount++\n"
                "    & $script:A1OriginalDatabaseAction "
                "-Action $Action -Create:$Create\n"
                "  }\n"
                "  $script:A1MeasuredOpenCount = 0\n"
                "  $current = @(Read-A1SemanticTables)\n"
                "  $currentOpens = $script:A1MeasuredOpenCount\n"
                "  $script:A1MeasuredOpenCount = 0\n"
                "  $legacy = New-Object Collections.ArrayList\n"
                "  foreach ($role in @('D', 'L', 'P', 'H')) {\n"
                "    if ([bool]$script:A1Extant[$role]) {\n"
                "      $expected = Get-A1ExpectedSemanticResult "
                "-Role $role -Rows $script:A1Rows[$role]\n"
                "      $document = Invoke-A1WithDatabase -Action {\n"
                "        param($database)\n"
                "        Read-A1SemanticTable -Database $database "
                "-Role $role -Expected $expected\n"
                "      }\n"
                "      [void]$legacy.Add($document)\n"
                "    }\n"
                "  }\n"
                "  $output = [ordered]@{\n"
                "    current = @($current)\n"
                "    legacy = @($legacy)\n"
                "    current_open_count = $currentOpens\n"
                "    legacy_open_count = $script:A1MeasuredOpenCount\n"
                "  } | ConvertTo-Json -Depth 8 -Compress\n"
                "}\n"
                "finally {\n"
                "  Release-M1ComObject -Value $workspace\n"
                "  Release-M1ComObject -Value $workspaces\n"
                "  Release-M1ComObject -Value $engine\n"
                "}\n"
                "[Console]::Write($output)\n"
            )
            result = self.run_ps(body, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(observed["current_open_count"], 1)
            self.assertEqual(observed["legacy_open_count"], 2)
            self.assertEqual(observed["current"], observed["legacy"])
            expected = [
                {
                    "role": role,
                    "row_count": 32,
                    "rolling_sha256": semantic_digest(role, set(range(1, 33)))[1],
                }
                for role in ("D", "L")
            ]
            self.assertEqual(observed["current"], expected)


if __name__ == "__main__":
    unittest.main()
