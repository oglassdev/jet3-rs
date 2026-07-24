from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import inspect_external_corpus as inspect_corpus  # noqa: E402


class InspectExternalCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.repo = base / "repo"
        self.corpus = base / "corpus"
        self.repo.mkdir()
        self.corpus.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Corpus Test")
        self._git("config", "user.email", "corpus@example.invalid")
        self.fixture_bytes = (
            b"\x00\x01\x02\x03Standard Jet DB"
            + bytes(range(256)) * 17
            + b"tail"
        )
        self.fixture_path = self.corpus / "backups/example.mdb"
        self.fixture_path.parent.mkdir()
        self.fixture_path.write_bytes(self.fixture_bytes)
        self.manifest_path = self.repo / "docs/validation/external-corpus.json"
        self.manifest_path.parent.mkdir(parents=True)
        self.manifest = self._manifest(
            "backups/example.mdb", self.fixture_bytes, len(self.fixture_bytes)
        )
        self._write_manifest()
        (self.repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "test: initialize corpus verifier repository")
        self.commit = self._git("rev-parse", "HEAD").stdout.strip()

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _manifest(path: str, content: bytes, size: int) -> dict[str, object]:
        return {
            "schema_version": 2,
            "environment_variable": inspect_corpus.ENVIRONMENT_VARIABLE,
            "purpose": inspect_corpus.PURPOSE,
            "fixtures": [
                {
                    "id": "FIX-0001",
                    "path": path,
                    "size_bytes": size,
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ],
            "comparisons": [],
        }

    def _add_pair_comparison(
        self, left: bytes, right: bytes
    ) -> tuple[Path, Path]:
        left_path = self.corpus / "backups/left.mdb"
        right_path = self.corpus / "backups/right.mdb"
        left_path.write_bytes(left)
        right_path.write_bytes(right)
        self.manifest = {
            "schema_version": 2,
            "environment_variable": inspect_corpus.ENVIRONMENT_VARIABLE,
            "purpose": inspect_corpus.PURPOSE,
            "fixtures": [
                {
                    "id": "FIX-0001",
                    "path": "backups/left.mdb",
                    "size_bytes": len(left),
                    "sha256": hashlib.sha256(left).hexdigest(),
                },
                {
                    "id": "FIX-0002",
                    "path": "backups/right.mdb",
                    "size_bytes": len(right),
                    "sha256": hashlib.sha256(right).hexdigest(),
                },
            ],
            "comparisons": [
                {
                    "id": "CMP-0001",
                    "left_fixture_id": "FIX-0001",
                    "right_fixture_id": "FIX-0002",
                    "page_size_bytes": inspect_corpus.PAGE_BOUNDARY_BYTES,
                }
            ],
        }
        self._write_manifest()
        return left_path, right_path

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _observe(self) -> dict[str, object]:
        return inspect_corpus.build_observation(self.repo, self.corpus)

    def test_valid_corpus_reports_signature_hash_size_and_stride_sets(self) -> None:
        observation = self._observe()
        self.assertEqual(observation["schema_version"], 2)
        self.assertEqual(observation["git_commit"], self.commit)
        self.assertFalse(observation["dirty"])
        fixture = observation["fixtures"][0]
        self.assertEqual(fixture["id"], "FIX-0001")
        self.assertEqual(fixture["path"], "backups/example.mdb")
        self.assertEqual(
            fixture["sha256"], hashlib.sha256(self.fixture_bytes).hexdigest()
        )
        self.assertEqual(fixture["size_bytes"], len(self.fixture_bytes))
        self.assertEqual(
            fixture["offset_4_signature"],
            {
                "ascii": "Standard Jet DB",
                "hex": b"Standard Jet DB".hex(),
            },
        )
        for stride in (1024, 2048):
            stride_result = next(
                item
                for item in fixture["stride_observations"]
                if item["stride_bytes"] == stride
            )
            expected = {
                self.fixture_bytes[offset]
                for offset in range(0, len(self.fixture_bytes), stride)
            }
            self.assertEqual(stride_result["unique_first_bytes"], sorted(expected))
            self.assertEqual(stride_result["unique_count"], len(expected))
            self.assertEqual(
                stride_result["sample_count"],
                len(range(0, len(self.fixture_bytes), stride)),
            )

        boundary_samples = [
            self.fixture_bytes[offset : offset + 2]
            for offset in range(
                0, len(self.fixture_bytes), inspect_corpus.PAGE_BOUNDARY_BYTES
            )
        ]
        first_byte_counts = {
            first_byte: sum(sample[0] == first_byte for sample in boundary_samples)
            for first_byte in sorted({sample[0] for sample in boundary_samples})
        }
        self.assertEqual(
            fixture["page_boundary_observation"],
            {
                "first_byte_counts": [
                    {"count": count, "first_byte": first_byte}
                    for first_byte, count in first_byte_counts.items()
                ],
                "nonzero_first_byte_count": sum(
                    sample[0] != 0 for sample in boundary_samples
                ),
                "nonzero_first_byte_with_second_byte_0x01_count": sum(
                    sample[0] != 0 and len(sample) == 2 and sample[1] == 0x01
                    for sample in boundary_samples
                ),
                "sample_count": len(boundary_samples),
                "stride_bytes": inspect_corpus.PAGE_BOUNDARY_BYTES,
            },
        )
        self.assertEqual(observation["comparisons"], [])

    def test_declared_pair_reports_exact_same_index_page_comparison(self) -> None:
        page_size = inspect_corpus.PAGE_BOUNDARY_BYTES
        left = bytearray(page_size * 3)
        right = bytearray(left)
        left[4:19] = b"Standard Jet DB"
        right[4:19] = b"Standard Jet DB"
        left[page_size] = 0x01
        right[page_size] = 0x09
        left[page_size + 7] = 0x11
        right[page_size + 7] = 0x22
        left[page_size * 2] = 0x02
        right[page_size * 2] = 0x02
        self._add_pair_comparison(bytes(left), bytes(right))

        comparison = self._observe()["comparisons"][0]

        changed_by_offset = [0] * page_size
        changed_by_offset[0] = 1
        changed_by_offset[7] = 1
        self.assertEqual(
            comparison,
            {
                "changed_byte_count_by_offset": changed_by_offset,
                "changed_page_count": 1,
                "equal_page_count": 2,
                "id": "CMP-0001",
                "left_fixture_id": "FIX-0001",
                "page_count": 3,
                "page_size_bytes": page_size,
                "right_fixture_id": "FIX-0002",
                "same_index_first_byte_transitions": [
                    {
                        "left_first_byte": 0,
                        "page_count": 1,
                        "right_first_byte": 0,
                    },
                    {
                        "left_first_byte": 1,
                        "page_count": 1,
                        "right_first_byte": 9,
                    },
                    {
                        "left_first_byte": 2,
                        "page_count": 1,
                        "right_first_byte": 2,
                    },
                ],
            },
        )

    def test_declared_pair_with_unequal_lengths_is_contract_error(self) -> None:
        page_size = inspect_corpus.PAGE_BOUNDARY_BYTES
        left = bytearray(page_size)
        right = bytearray(page_size * 2)
        left[4:19] = b"Standard Jet DB"
        right[4:19] = b"Standard Jet DB"
        self._add_pair_comparison(bytes(left), bytes(right))

        with self.assertRaisesRegex(
            inspect_corpus.ContractError, "fixtures must have equal sizes"
        ):
            self._observe()

    def test_page_boundary_metrics_count_short_final_boundary_exactly(self) -> None:
        fixture = bytearray(inspect_corpus.PAGE_BOUNDARY_BYTES * 3 + 1)
        fixture[4:19] = b"Standard Jet DB"
        fixture[0:2] = b"\x02\x01"
        first_boundary = inspect_corpus.PAGE_BOUNDARY_BYTES
        fixture[first_boundary : first_boundary + 2] = b"\x02\x00"
        second_boundary = inspect_corpus.PAGE_BOUNDARY_BYTES * 2
        fixture[second_boundary : second_boundary + 2] = b"\x00\x01"
        fixture[-1] = 0x03
        self.fixture_bytes = bytes(fixture)
        self.fixture_path.write_bytes(self.fixture_bytes)
        self.manifest = self._manifest(
            "backups/example.mdb", self.fixture_bytes, len(self.fixture_bytes)
        )
        self._write_manifest()

        metrics = self._observe()["fixtures"][0]["page_boundary_observation"]

        self.assertEqual(
            metrics,
            {
                "first_byte_counts": [
                    {"count": 1, "first_byte": 0},
                    {"count": 2, "first_byte": 2},
                    {"count": 1, "first_byte": 3},
                ],
                "nonzero_first_byte_count": 3,
                "nonzero_first_byte_with_second_byte_0x01_count": 1,
                "sample_count": 4,
                "stride_bytes": 2048,
            },
        )

    def test_missing_environment_is_blocked(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        result = inspect_corpus.main([], environ={}, stdout=stdout, stderr=stderr)
        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            f"BLOCKED: {inspect_corpus.ENVIRONMENT_VARIABLE} is not set\n",
        )

    def test_missing_fixture_is_blocked(self) -> None:
        self.fixture_path.unlink()
        with self.assertRaisesRegex(
            inspect_corpus.CorpusBlockedError, "FIX-0001 is missing"
        ):
            self._observe()

    def test_hash_mismatch_is_blocked(self) -> None:
        self.fixture_path.write_bytes(self.fixture_bytes[:-1] + b"!")
        with self.assertRaisesRegex(
            inspect_corpus.CorpusBlockedError, "SHA-256 mismatch"
        ):
            self._observe()

    def test_size_mismatch_is_blocked_before_hashing(self) -> None:
        self.fixture_path.write_bytes(self.fixture_bytes + b"x")
        with self.assertRaisesRegex(
            inspect_corpus.CorpusBlockedError, "size mismatch"
        ):
            self._observe()

    def test_manifest_path_escape_is_an_invalid_repository_contract(self) -> None:
        self.manifest["fixtures"][0]["path"] = "../outside.mdb"
        self._write_manifest()
        with self.assertRaisesRegex(inspect_corpus.ContractError, "safe relative path"):
            self._observe()

    def test_symlink_to_outside_corpus_is_blocked(self) -> None:
        outside = Path(self.temporary.name) / "outside.mdb"
        outside.write_bytes(self.fixture_bytes)
        self.fixture_path.unlink()
        self.fixture_path.symlink_to(outside)
        with self.assertRaisesRegex(
            inspect_corpus.CorpusBlockedError, "escapes the external corpus root"
        ):
            self._observe()

    def test_non_regular_fixture_is_blocked(self) -> None:
        self.fixture_path.unlink()
        self.fixture_path.mkdir()
        with self.assertRaisesRegex(
            inspect_corpus.CorpusBlockedError, "not a regular file"
        ):
            self._observe()

    def test_short_file_is_blocked(self) -> None:
        short = b"too short"
        self.fixture_path.write_bytes(short)
        self.manifest = self._manifest("backups/example.mdb", short, len(short))
        self._write_manifest()
        with self.assertRaisesRegex(
            inspect_corpus.ContractError, "at least 19"
        ):
            self._observe()

    def test_short_read_after_valid_manifest_is_blocked(self) -> None:
        fake_stat = os.stat_result(
            (
                0o100644,
                1,
                1,
                1,
                0,
                0,
                len(self.fixture_bytes),
                0,
                0,
                0,
            )
        )
        source = mock.MagicMock()
        source.__enter__.return_value = source
        source.__exit__.return_value = False
        source.fileno.return_value = 10
        source.read.side_effect = [self.fixture_bytes, b"", b"short"]
        with (
            mock.patch.object(Path, "open", return_value=source),
            mock.patch.object(inspect_corpus.os, "fstat", return_value=fake_stat),
            self.assertRaisesRegex(
                inspect_corpus.CorpusBlockedError, "offset-4 signature"
            ),
        ):
            inspect_corpus._observe_fixture(
                self.corpus.resolve(), self.manifest["fixtures"][0]
            )

    def test_short_page_boundary_read_is_blocked(self) -> None:
        fake_stat = os.stat_result(
            (
                0o100644,
                1,
                1,
                1,
                0,
                0,
                len(self.fixture_bytes),
                0,
                0,
                0,
            )
        )
        stride_1024_samples = len(range(0, len(self.fixture_bytes), 1024))
        source = mock.MagicMock()
        source.__enter__.return_value = source
        source.__exit__.return_value = False
        source.fileno.return_value = 10
        source.read.side_effect = [
            self.fixture_bytes,
            b"",
            b"Standard Jet DB",
            *([b"\x00"] * stride_1024_samples),
            b"\x00",
        ]
        with (
            mock.patch.object(Path, "open", return_value=source),
            mock.patch.object(inspect_corpus.os, "fstat", return_value=fake_stat),
            self.assertRaisesRegex(
                inspect_corpus.CorpusBlockedError,
                "changed during stride inspection",
            ),
        ):
            inspect_corpus._observe_fixture(
                self.corpus.resolve(), self.manifest["fixtures"][0]
            )

    def test_short_declared_pair_page_read_is_blocked(self) -> None:
        page_size = inspect_corpus.PAGE_BOUNDARY_BYTES
        left_bytes = bytearray(page_size * 2)
        right_bytes = bytearray(page_size * 2)
        left_bytes[4:19] = b"Standard Jet DB"
        right_bytes[4:19] = b"Standard Jet DB"
        self._add_pair_comparison(bytes(left_bytes), bytes(right_bytes))
        fake_stat = os.stat_result(
            (
                0o100644,
                1,
                1,
                1,
                0,
                0,
                len(left_bytes),
                0,
                0,
                0,
            )
        )
        left_source = mock.MagicMock()
        left_source.__enter__.return_value = left_source
        left_source.__exit__.return_value = False
        left_source.fileno.return_value = 10
        left_source.read.side_effect = [
            bytes(left_bytes),
            b"",
            bytes(left_bytes[:page_size]),
            b"short",
        ]
        right_source = mock.MagicMock()
        right_source.__enter__.return_value = right_source
        right_source.__exit__.return_value = False
        right_source.fileno.return_value = 11
        right_source.read.side_effect = [
            bytes(right_bytes),
            b"",
            bytes(right_bytes[:page_size]),
            bytes(right_bytes[page_size:]),
        ]
        fixtures_by_id = {
            fixture["id"]: fixture for fixture in self.manifest["fixtures"]
        }
        with (
            mock.patch.object(
                Path, "open", side_effect=[left_source, right_source]
            ),
            mock.patch.object(
                inspect_corpus.os,
                "fstat",
                side_effect=[fake_stat, fake_stat],
            ),
            self.assertRaisesRegex(
                inspect_corpus.CorpusBlockedError,
                "changed during page comparison at page index 1",
            ),
        ):
            inspect_corpus._observe_comparison(
                self.corpus.resolve(),
                self.manifest["comparisons"][0],
                fixtures_by_id,
            )

    def test_canonical_output_is_sorted_and_deterministic(self) -> None:
        observation = self._observe()
        first = inspect_corpus._canonical_json(observation)
        second = inspect_corpus._canonical_json(self._observe())
        self.assertEqual(first, second)
        self.assertEqual(
            first, json.dumps(observation, indent=2, sort_keys=True) + "\n"
        )
        self.assertTrue(first.endswith("\n"))

    def test_git_commit_and_dirty_shape(self) -> None:
        clean = self._observe()
        self.assertRegex(clean["git_commit"], r"^[0-9a-f]{40}$")
        self.assertIs(type(clean["dirty"]), bool)
        (self.repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        dirty = self._observe()
        self.assertEqual(dirty["git_commit"], clean["git_commit"])
        self.assertTrue(dirty["dirty"])

    def test_observation_does_not_expose_absolute_external_root(self) -> None:
        rendered = inspect_corpus._canonical_json(self._observe())
        self.assertNotIn(str(self.corpus), rendered)
        self.assertNotIn(str(self.repo), rendered)

    def test_inspection_does_not_modify_corpus(self) -> None:
        before_bytes = self.fixture_path.read_bytes()
        before = self.fixture_path.stat()
        self._observe()
        after = self.fixture_path.stat()
        self.assertEqual(self.fixture_path.read_bytes(), before_bytes)
        self.assertEqual(after.st_size, before.st_size)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(after.st_mode, before.st_mode)

    def test_invalid_manifest_is_repository_contract_error(self) -> None:
        self.manifest["purpose"] = "redistributable"
        self._write_manifest()
        with self.assertRaisesRegex(inspect_corpus.ContractError, "invalid purpose"):
            self._observe()


if __name__ == "__main__":
    unittest.main()
