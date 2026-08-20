from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import verify_preservation_diff as cli  # noqa: E402
from validation import preservation_diff as preservation  # noqa: E402


class BoundedBytesIO(io.BytesIO):
    def __init__(self, value: bytes, maximum_read: int) -> None:
        super().__init__(value)
        self.maximum_read = maximum_read
        self.largest_read = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > self.maximum_read:
            raise AssertionError(f"unbounded read requested: {size}")
        self.largest_read = max(self.largest_read, size)
        return super().read(size)


class GreedyBytesIO(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        return super().read(size + 1)


class FaultingBytesIO(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        raise OSError("synthetic read failure")


class PreservationDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def paths(self, original: bytes, output: bytes) -> tuple[Path, Path]:
        original_path = self.root / "original.mdb"
        output_path = self.root / "output.mdb"
        original_path.write_bytes(original)
        output_path.write_bytes(output)
        return original_path, output_path

    def test_unchanged_files_pass_with_exact_page_count(self) -> None:
        original, output = self.paths(b"A" * 4096, b"A" * 4096)

        report = preservation.verify_files(original, output)

        self.assertEqual(report.file_size, 4096)
        self.assertEqual(report.page_size, 2048)
        self.assertEqual(report.page_count, 2)
        self.assertEqual(report.changed_bytes_within_allowed_intervals, 0)
        self.assertFalse(report.as_dict()["jet_structural_correctness_claimed"])
        self.assertFalse(report.as_dict()["dao_compatibility_claimed"])

    def test_changes_only_inside_canonical_allowed_intervals_pass(self) -> None:
        before = bytearray(range(16))
        after = bytearray(before)
        after[2] = 99
        after[3] = 100
        after[12] = 101
        original, output = self.paths(bytes(before), bytes(after))

        report = preservation.verify_files(
            original,
            output,
            page_size=4,
            allowed_intervals=(
                preservation.AllowedInterval(2, 4),
                preservation.AllowedInterval(12, 13),
            ),
        )

        self.assertEqual(report.page_count, 4)
        self.assertEqual(report.allowed_interval_count, 2)
        self.assertEqual(report.changed_bytes_within_allowed_intervals, 3)

    def test_first_change_outside_intervals_is_reported(self) -> None:
        before = bytearray(range(16))
        after = bytearray(before)
        after[7] = 200
        after[10] = 201
        original, output = self.paths(bytes(before), bytes(after))

        with self.assertRaises(preservation.PreservationMismatch) as raised:
            preservation.verify_files(
                original,
                output,
                page_size=4,
                allowed_intervals=(preservation.AllowedInterval(2, 4),),
            )

        self.assertEqual(raised.exception.code, "change_outside_allowed_intervals")
        self.assertEqual(raised.exception.exit_code, 1)
        self.assertEqual(raised.exception.details["offset"], 7)
        self.assertEqual(raised.exception.details["original_byte"], 7)
        self.assertEqual(raised.exception.details["output_byte"], 200)

    def test_size_mismatch_is_a_preservation_failure(self) -> None:
        original, output = self.paths(b"A" * 8, b"A" * 12)

        with self.assertRaises(preservation.PreservationMismatch) as raised:
            preservation.verify_files(original, output, page_size=4)

        self.assertEqual(raised.exception.code, "size_mismatch")
        self.assertEqual(raised.exception.exit_code, 1)
        self.assertEqual(raised.exception.details["original_size"], 8)
        self.assertEqual(raised.exception.details["output_size"], 12)

    def test_same_path_and_hard_link_are_rejected(self) -> None:
        original, _ = self.paths(b"A" * 8, b"A" * 8)

        with self.assertRaises(preservation.PreservationContractError) as raised:
            preservation.verify_files(original, original, page_size=4)
        self.assertEqual(raised.exception.code, "same_input_file")
        self.assertEqual(raised.exception.exit_code, 2)

        hard_link = self.root / "hard-link.mdb"
        try:
            os.link(original, hard_link)
        except OSError as error:
            self.skipTest(f"hard links are unavailable: {error}")
        with self.assertRaises(preservation.PreservationContractError) as raised:
            preservation.verify_files(original, hard_link, page_size=4)
        self.assertEqual(raised.exception.code, "same_input_file")

    def test_same_size_mutation_is_detected_even_when_comparison_would_fail(self) -> None:
        original, output = self.paths(b"A" * 8, b"A" * 8)
        initial_metadata = output.stat()

        def mutate_then_fail(*args: object, **kwargs: object) -> None:
            output.write_bytes(b"B" * 8)
            os.utime(
                output,
                ns=(initial_metadata.st_atime_ns, initial_metadata.st_mtime_ns),
            )
            raise preservation.PreservationMismatch("synthetic", "synthetic mismatch")

        with mock.patch.object(
            preservation, "verify_streams", side_effect=mutate_then_fail
        ):
            with self.assertRaises(preservation.PreservationIoError) as raised:
                preservation.verify_files(original, output, page_size=4)

        self.assertEqual(raised.exception.code, "input_changed")
        self.assertEqual(raised.exception.exit_code, 3)
        self.assertIn("output", raised.exception.details["changed_inputs"])

    def test_path_replacement_after_open_is_rejected(self) -> None:
        original, output = self.paths(b"A" * 8, b"A" * 8)
        replacement = self.root / "replacement.mdb"
        replacement.write_bytes(b"A" * 8)

        def replace_output(
            *args: object, **kwargs: object
        ) -> preservation.PreservationReport:
            os.replace(replacement, output)
            return preservation.PreservationReport(8, 4, 2, 0, 0)

        with mock.patch.object(
            preservation, "verify_streams", side_effect=replace_output
        ):
            with self.assertRaises(preservation.PreservationIoError) as raised:
                preservation.verify_files(original, output, page_size=4)

        self.assertEqual(raised.exception.code, "input_changed")

    def test_interval_overlap_order_adjacency_and_bounds_are_rejected(self) -> None:
        cases = (
            (
                (
                    preservation.AllowedInterval(2, 6),
                    preservation.AllowedInterval(5, 8),
                ),
                "interval_overlap",
            ),
            (
                (
                    preservation.AllowedInterval(8, 10),
                    preservation.AllowedInterval(2, 4),
                ),
                "interval_order",
            ),
            (
                (
                    preservation.AllowedInterval(2, 4),
                    preservation.AllowedInterval(4, 6),
                ),
                "adjacent_intervals",
            ),
            ((preservation.AllowedInterval(15, 17),), "interval_out_of_bounds"),
        )

        for intervals, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(
                    preservation.PreservationContractError
                ) as raised:
                    preservation.validate_intervals(intervals, 16)
                self.assertEqual(raised.exception.code, expected)
                self.assertEqual(raised.exception.exit_code, 2)

    def test_first_and_last_byte_half_open_boundaries_are_exact(self) -> None:
        before = bytearray(range(8))
        after = bytearray(before)
        after[0] = 100
        after[-1] = 101
        original, output = self.paths(bytes(before), bytes(after))

        report = preservation.verify_files(
            original,
            output,
            page_size=4,
            allowed_intervals=(
                preservation.AllowedInterval(0, 1),
                preservation.AllowedInterval(7, 8),
            ),
        )
        self.assertEqual(report.changed_bytes_within_allowed_intervals, 2)

        with self.assertRaises(preservation.PreservationMismatch) as raised:
            preservation.verify_files(
                original,
                output,
                page_size=4,
                allowed_intervals=(preservation.AllowedInterval(0, 1),),
            )
        self.assertEqual(raised.exception.details["offset"], 7)

    def test_stream_comparison_never_requests_more_than_its_chunk(self) -> None:
        before = bytes(range(64)) * 4
        after = bytearray(before)
        after[200] ^= 0xFF
        original = BoundedBytesIO(before, 7)
        output = BoundedBytesIO(bytes(after), 7)

        report = preservation.verify_streams(
            original,
            output,
            file_size=len(before),
            page_size=64,
            chunk_size=7,
            allowed_intervals=(preservation.AllowedInterval(200, 201),),
        )

        self.assertEqual(report.changed_bytes_within_allowed_intervals, 1)
        self.assertLessEqual(original.largest_read, 7)
        self.assertLessEqual(output.largest_read, 7)

    def test_stream_read_contract_and_io_failures_are_structured(self) -> None:
        with self.assertRaises(preservation.PreservationIoError) as raised:
            preservation.verify_streams(
                GreedyBytesIO(b"A" * 8),
                io.BytesIO(b"A" * 8),
                file_size=8,
                page_size=4,
                chunk_size=4,
            )
        self.assertEqual(raised.exception.code, "invalid_read")
        self.assertEqual(raised.exception.exit_code, 3)

        with self.assertRaises(preservation.PreservationIoError) as raised:
            preservation.verify_streams(
                FaultingBytesIO(b"A" * 8),
                io.BytesIO(b"A" * 8),
                file_size=8,
                page_size=4,
                chunk_size=4,
            )
        self.assertEqual(raised.exception.code, "io_error")

    def test_equal_unaligned_files_are_rejected_as_invalid_page_inputs(self) -> None:
        original, output = self.paths(b"A" * 7, b"A" * 7)

        with self.assertRaises(preservation.PreservationContractError) as raised:
            preservation.verify_files(original, output, page_size=4)

        self.assertEqual(raised.exception.code, "unaligned_file_size")

    def test_cli_uses_structured_exit_codes_and_json_errors(self) -> None:
        original, output = self.paths(b"A" * 8, b"A" * 7 + b"B")
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = cli.main(
                [str(original), str(output), "--page-size", "4"]
            )

        self.assertEqual(exit_code, 1)
        error = json.loads(stderr.getvalue())
        self.assertEqual(error["status"], "fail")
        self.assertEqual(error["code"], "change_outside_allowed_intervals")
        self.assertEqual(error["offset"], 7)

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = cli.main(
                [str(original), str(output), "--allow", "01:2"]
            )
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["status"], "error")

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = cli.main(
                [str(self.root / "missing.mdb"), str(output)]
            )
        self.assertEqual(exit_code, 3)
        self.assertEqual(json.loads(stderr.getvalue())["code"], "io_error")

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = cli.main(
                [str(original), str(original), "--page-size", "4"]
            )
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["code"], "same_input_file")


if __name__ == "__main__":
    unittest.main()
