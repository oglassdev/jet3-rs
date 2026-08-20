from __future__ import annotations

import json
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
import warnings
import zipfile


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from validation import windows_dao_archive as archive  # noqa: E402


COMMIT = "a" * 40
RUN_ID = "20260819T120000Z-ssh-dao"


def remote_job(mode: str, exit_code: int = 0, phase: str | None = None) -> bytes:
    recorded_phase = mode if phase is None else phase
    return json.dumps(
        {
            "artifact_limit_bytes": 300 * 1024 * 1024,
            "commit": COMMIT,
            "downloadable": True,
            "exit_code": exit_code,
            "job": mode,
            "phase": recorded_phase,
            "remote_root": r"C:\Users\runner\jet3-rs-ssh",
            "run_id": RUN_ID,
            "timeout_seconds": 120,
        },
        sort_keys=True,
    ).encode()


def base_entries(
    mode: str,
    exit_code: int = 0,
    *,
    phase: str | None = None,
    include_evidence: bool | None = None,
) -> list[tuple[object, bytes, int]]:
    recorded_phase = mode if phase is None else phase
    environment_status = (
        "ready"
        if recorded_phase == archive.M1_CONTROLLED
        else {0: "ready", 1: "error", 3: "blocked"}[exit_code]
    )
    entries: list[tuple[object, bytes, int]] = [
        ("artifacts/", b"", zipfile.ZIP_STORED),
        (
            "artifacts/environment.json",
            json.dumps({"status": environment_status}).encode(),
            zipfile.ZIP_STORED,
        ),
        (
            "artifacts/remote-job.json",
            remote_job(mode, exit_code, recorded_phase),
            zipfile.ZIP_STORED,
        ),
    ]
    if include_evidence is None:
        include_evidence = (
            mode == archive.M1_CONTROLLED
            and recorded_phase == archive.M1_CONTROLLED
            and exit_code in (0, 1)
        )
    if include_evidence:
        entries.append(
            (
                "artifacts/evidence/commit/run/manifest.json",
                b'{"transport_only":true}',
                zipfile.ZIP_STORED,
            )
        )
    return entries


class WindowsDaoArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_archive(
        self,
        entries: list[tuple[object, bytes, int]],
        name: str = "artifacts.zip",
    ) -> Path:
        path = self.root / name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w", allowZip64=False) as output:
                for entry_name, data, compression in entries:
                    output.writestr(entry_name, data, compress_type=compression)
        return path

    def validate(
        self,
        path: Path,
        mode: str = archive.PROVIDER_PROBE,
        exit_code: int = 0,
        limits: archive.ArchiveLimits | None = None,
    ) -> archive.ArchiveValidation:
        return archive.validate_archive(
            path,
            mode=mode,
            expected_commit=COMMIT,
            expected_run_id=RUN_ID,
            expected_exit_code=exit_code,
            limits=limits,
        )

    def assert_rejected(
        self,
        entries: list[tuple[object, bytes, int]],
        pattern: str,
        *,
        mode: str = archive.PROVIDER_PROBE,
        exit_code: int = 0,
        limits: archive.ArchiveLimits | None = None,
    ) -> None:
        with self.assertRaisesRegex(archive.ArchiveValidationError, pattern):
            self.validate(
                self.write_archive(entries),
                mode=mode,
                exit_code=exit_code,
                limits=limits,
            )

    def test_valid_minimal_provider_probe_is_fully_traversed(self) -> None:
        result = self.validate(self.write_archive(base_entries(archive.PROVIDER_PROBE)))
        self.assertEqual(result.mode, archive.PROVIDER_PROBE)
        self.assertEqual(result.entry_count, 3)
        self.assertEqual(result.remote_job["commit"], COMMIT)
        self.assertEqual(result.environment, {"status": "ready"})
        self.assertGreater(result.uncompressed_bytes, 0)

    def test_valid_provider_probe_failure_statuses_match_exit_codes(self) -> None:
        for exit_code, status in ((1, "error"), (3, "blocked")):
            with self.subTest(exit_code=exit_code):
                entries = base_entries(archive.PROVIDER_PROBE, exit_code)
                path = self.write_archive(entries, f"provider-{exit_code}.zip")
                result = self.validate(path, archive.PROVIDER_PROBE, exit_code)
                self.assertEqual(result.environment["status"], status)

    def test_valid_m1_requires_and_accepts_nested_evidence_content(self) -> None:
        path = self.write_archive(base_entries(archive.M1_CONTROLLED))
        result = self.validate(path, archive.M1_CONTROLLED)
        self.assertEqual(result.entry_count, 4)
        self.assertEqual(result.remote_job["phase"], archive.M1_CONTROLLED)

    def test_valid_m1_probe_stage_failures_need_no_evidence(self) -> None:
        for exit_code, status in ((1, "error"), (3, "blocked")):
            with self.subTest(exit_code=exit_code):
                entries = base_entries(
                    archive.M1_CONTROLLED,
                    exit_code,
                    phase=archive.PROVIDER_PROBE,
                )
                path = self.write_archive(entries, f"m1-probe-{exit_code}.zip")
                result = self.validate(path, archive.M1_CONTROLLED, exit_code)
                self.assertEqual(result.remote_job["phase"], archive.PROVIDER_PROBE)
                self.assertEqual(result.environment["status"], status)

    def test_valid_m1_blocked_executor_need_not_publish_evidence(self) -> None:
        entries = base_entries(
            archive.M1_CONTROLLED,
            3,
            phase=archive.M1_CONTROLLED,
            include_evidence=False,
        )
        path = self.write_archive(entries, "m1-executor-blocked.zip")
        result = self.validate(path, archive.M1_CONTROLLED, 3)
        self.assertEqual(result.environment["status"], "ready")

    def test_valid_m1_controlled_failure_requires_published_evidence(self) -> None:
        entries = base_entries(archive.M1_CONTROLLED, 1)
        path = self.write_archive(entries, "m1-controlled-fail.zip")
        result = self.validate(path, archive.M1_CONTROLLED, 1)
        self.assertEqual(result.remote_job["exit_code"], 1)

    def test_absolute_traversal_backslash_drive_and_nul_paths_are_rejected(self) -> None:
        bad_names = (
            "/artifacts/evil",
            "artifacts/../evil",
            r"artifacts\evil",
            "C:/artifacts/evil",
        )
        for index, bad_name in enumerate(bad_names):
            with self.subTest(name=bad_name):
                entries = base_entries(archive.PROVIDER_PROBE)
                entries.append((bad_name, b"x", zipfile.ZIP_STORED))
                with self.assertRaises(archive.ArchiveValidationError):
                    self.validate(self.write_archive(entries, f"bad-{index}.zip"))

        path = self.write_archive(
            base_entries(archive.PROVIDER_PROBE)
            + [("artifacts/nulXpath", b"x", zipfile.ZIP_STORED)],
            "nul.zip",
        )
        data = path.read_bytes().replace(b"nulXpath", b"nul\x00path")
        path.write_bytes(data)
        with self.assertRaisesRegex(archive.ArchiveValidationError, "NUL"):
            self.validate(path)

    def test_duplicate_and_case_colliding_paths_are_rejected(self) -> None:
        duplicate = base_entries(archive.PROVIDER_PROBE)
        duplicate.append(("artifacts/environment.json", b"{}", zipfile.ZIP_STORED))
        self.assert_rejected(duplicate, "duplicate archive path")

        collision = base_entries(archive.PROVIDER_PROBE)
        collision.append(("ARTIFACTS/environment.json", b"{}", zipfile.ZIP_STORED))
        self.assert_rejected(collision, "case-colliding archive path")

    def test_symlink_reparse_encrypted_and_special_entries_are_rejected(self) -> None:
        symlink = zipfile.ZipInfo("artifacts/link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.assert_rejected(
            base_entries(archive.PROVIDER_PROBE)
            + [(symlink, b"environment.json", zipfile.ZIP_STORED)],
            "symlink or reparse-point",
        )

        encrypted_path = self.write_archive(base_entries(archive.PROVIDER_PROBE), "encrypted.zip")
        data = bytearray(encrypted_path.read_bytes())
        local = data.find(b"PK\x03\x04")
        central = data.find(b"PK\x01\x02")
        struct.pack_into("<H", data, local + 6, struct.unpack_from("<H", data, local + 6)[0] | 1)
        struct.pack_into(
            "<H", data, central + 8, struct.unpack_from("<H", data, central + 8)[0] | 1
        )
        encrypted_path.write_bytes(data)
        with self.assertRaisesRegex(archive.ArchiveValidationError, "encrypted"):
            self.validate(encrypted_path)

        device = zipfile.ZipInfo("artifacts/device")
        device.create_system = 3
        device.external_attr = (stat.S_IFCHR | 0o600) << 16
        self.assert_rejected(
            base_entries(archive.PROVIDER_PROBE)
            + [(device, b"x", zipfile.ZIP_STORED)],
            "special filesystem entry",
        )

    def test_entry_and_byte_limits_reject_before_unbounded_expansion(self) -> None:
        entries = base_entries(archive.PROVIDER_PROBE)
        limits = archive.ArchiveLimits(maximum_entries=2)
        self.assert_rejected(entries, "entry-count", limits=limits)

        limits = archive.ArchiveLimits(maximum_entry_uncompressed_bytes=8)
        self.assert_rejected(entries, "uncompressed entry", limits=limits)

        limits = archive.ArchiveLimits(maximum_total_uncompressed_bytes=8)
        self.assert_rejected(entries, "total uncompressed", limits=limits)

        limits = archive.ArchiveLimits(maximum_entry_compressed_bytes=8)
        self.assert_rejected(entries, "compressed entry", limits=limits)

        limits = archive.ArchiveLimits(maximum_total_compressed_bytes=8)
        self.assert_rejected(entries, "total compressed", limits=limits)

        path = self.write_archive(entries, "archive-limit.zip")
        limits = archive.ArchiveLimits(maximum_archive_bytes=path.stat().st_size - 1)
        with self.assertRaisesRegex(archive.ArchiveValidationError, "archive exceeds"):
            self.validate(path, limits=limits)

    def test_suspicious_ratio_and_unsupported_compression_are_rejected(self) -> None:
        ratio_entries = base_entries(archive.M1_CONTROLLED)
        ratio_entries.append(
            (
                "artifacts/evidence/zeros.bin",
                b"0" * 100_000,
                zipfile.ZIP_DEFLATED,
            )
        )
        self.assert_rejected(
            ratio_entries,
            "suspicious compression ratio",
            mode=archive.M1_CONTROLLED,
            limits=archive.ArchiveLimits(maximum_compression_ratio=2),
        )

        unsupported = base_entries(archive.M1_CONTROLLED)
        unsupported.append(
            ("artifacts/evidence/value.bin", b"value", zipfile.ZIP_BZIP2)
        )
        self.assert_rejected(
            unsupported,
            "unsupported ZIP compression",
            mode=archive.M1_CONTROLLED,
        )

    def test_top_level_inventory_is_mode_specific_and_fail_closed(self) -> None:
        unexpected = base_entries(archive.PROVIDER_PROBE)
        unexpected.append(("artifacts/extra.txt", b"x", zipfile.ZIP_STORED))
        self.assert_rejected(unexpected, "unexpected top-level file")

        provider_evidence = base_entries(archive.PROVIDER_PROBE)
        provider_evidence.append(
            ("artifacts/evidence/value.json", b"{}", zipfile.ZIP_STORED)
        )
        self.assert_rejected(provider_evidence, "unexpected nested inventory")

        no_evidence = base_entries(
            archive.M1_CONTROLLED, include_evidence=False
        )
        self.assert_rejected(
            no_evidence,
            "completed M1 archive contains no evidence content",
            mode=archive.M1_CONTROLLED,
        )

    def test_phase_exit_and_evidence_state_machine_rejects_impossible_combinations(self) -> None:
        m1_probe_success = base_entries(
            archive.M1_CONTROLLED,
            0,
            phase=archive.PROVIDER_PROBE,
            include_evidence=False,
        )
        self.assert_rejected(
            m1_probe_success,
            "phase and exit code",
            mode=archive.M1_CONTROLLED,
        )

        provider_m1_phase = base_entries(
            archive.PROVIDER_PROBE,
            0,
            phase=archive.M1_CONTROLLED,
            include_evidence=False,
        )
        self.assert_rejected(provider_m1_phase, "phase and exit code")

        probe_with_evidence = base_entries(
            archive.M1_CONTROLLED,
            3,
            phase=archive.PROVIDER_PROBE,
            include_evidence=True,
        )
        self.assert_rejected(
            probe_with_evidence,
            "unexpectedly contains M1 evidence",
            mode=archive.M1_CONTROLLED,
            exit_code=3,
        )

    def test_environment_status_is_bound_only_to_documented_probe_transition(self) -> None:
        entries = base_entries(
            archive.M1_CONTROLLED,
            3,
            phase=archive.PROVIDER_PROBE,
        )
        entries[1] = (
            "artifacts/environment.json",
            b'{"status":"ready"}',
            zipfile.ZIP_STORED,
        )
        self.assert_rejected(
            entries,
            "status does not match",
            mode=archive.M1_CONTROLLED,
            exit_code=3,
        )

        entries = base_entries(
            archive.M1_CONTROLLED,
            3,
            phase=archive.M1_CONTROLLED,
        )
        entries[1] = (
            "artifacts/environment.json",
            b'{"status":"blocked"}',
            zipfile.ZIP_STORED,
        )
        self.assert_rejected(
            entries,
            "status does not match",
            mode=archive.M1_CONTROLLED,
            exit_code=3,
        )

    def test_required_json_is_bounded_well_formed_and_duplicate_free(self) -> None:
        missing = [
            entry
            for entry in base_entries(archive.PROVIDER_PROBE)
            if entry[0] != "artifacts/environment.json"
        ]
        self.assert_rejected(missing, "missing required JSON")

        malformed = base_entries(archive.PROVIDER_PROBE)
        malformed[1] = ("artifacts/environment.json", b"{", zipfile.ZIP_STORED)
        self.assert_rejected(malformed, "invalid bounded UTF-8 JSON")

        duplicate = base_entries(archive.PROVIDER_PROBE)
        duplicate[1] = (
            "artifacts/environment.json",
            b'{"x":1,"x":2}',
            zipfile.ZIP_STORED,
        )
        self.assert_rejected(duplicate, "duplicate JSON property")

        oversized = base_entries(archive.PROVIDER_PROBE)
        self.assert_rejected(
            oversized,
            "JSON exceeds its byte limit",
            limits=archive.ArchiveLimits(maximum_json_bytes=8),
        )

    def test_remote_job_identity_downloadability_phase_and_exit_are_bound(self) -> None:
        mutations = {
            "commit": "b" * 40,
            "downloadable": False,
            "exit_code": 1,
            "job": archive.M1_CONTROLLED,
            "phase": archive.M1_CONTROLLED,
            "run_id": "20260819T120000Z-other",
        }
        for index, (field, value) in enumerate(mutations.items()):
            with self.subTest(field=field):
                document = json.loads(remote_job(archive.PROVIDER_PROBE))
                document[field] = value
                entries = base_entries(archive.PROVIDER_PROBE)
                entries[2] = (
                    "artifacts/remote-job.json",
                    json.dumps(document).encode(),
                    zipfile.ZIP_STORED,
                )
                with self.assertRaisesRegex(archive.ArchiveValidationError, field):
                    self.validate(self.write_archive(entries, f"binding-{index}.zip"))

        document = json.loads(remote_job(archive.PROVIDER_PROBE))
        document["exit_code"] = False
        entries = base_entries(archive.PROVIDER_PROBE)
        entries[2] = (
            "artifacts/remote-job.json",
            json.dumps(document).encode(),
            zipfile.ZIP_STORED,
        )
        with self.assertRaisesRegex(archive.ArchiveValidationError, "must be an integer"):
            self.validate(self.write_archive(entries, "bool-exit.zip"))

    def test_corrupt_entry_is_rejected_during_complete_streaming_traversal(self) -> None:
        path = self.write_archive(base_entries(archive.M1_CONTROLLED), "corrupt.zip")
        data = bytearray(path.read_bytes())
        marker = b'{"transport_only":true}'
        position = data.find(marker)
        self.assertGreater(position, 0)
        data[position] ^= 0x01
        path.write_bytes(data)
        with self.assertRaisesRegex(archive.ArchiveValidationError, "safely traverse"):
            self.validate(path, archive.M1_CONTROLLED)


if __name__ == "__main__":
    unittest.main()
