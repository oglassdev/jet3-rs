"""Bounded, format-neutral verification of an allowed byte-only mutation.

This module proves only that two regular files have equal page-aligned lengths
and that every changed byte lies in a caller-declared interval. It does not
parse Jet structures and makes no structural-correctness or DAO-compatibility
claim.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import BinaryIO, Iterable

DEFAULT_PAGE_SIZE = 2048
READ_CHUNK_BYTES = 64 * 1024
MAX_ALLOWED_INTERVALS = 65_536
CLAIM_BOUNDARY = (
    "byte preservation only; no Jet structural correctness or DAO "
    "compatibility is established"
)


class PreservationError(Exception):
    """A structured verifier failure with a stable process exit code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.details = details or {}

    def as_dict(self) -> dict[str, object]:
        """Return a stable JSON-compatible error record."""

        return {
            "status": "fail" if self.exit_code == 1 else "error",
            "code": self.code,
            "message": str(self),
            **self.details,
            "claim_boundary": CLAIM_BOUNDARY,
        }


class PreservationMismatch(PreservationError):
    """The candidate violates the requested preservation invariant."""

    def __init__(
        self, code: str, message: str, *, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(code, message, exit_code=1, details=details)


class PreservationContractError(PreservationError):
    """The verifier invocation or declared interval contract is invalid."""

    def __init__(
        self, code: str, message: str, *, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(code, message, exit_code=2, details=details)


class PreservationIoError(PreservationError):
    """A file could not be read reliably for the complete comparison."""

    def __init__(
        self, code: str, message: str, *, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(code, message, exit_code=3, details=details)


@dataclass(frozen=True, order=True)
class AllowedInterval:
    """One half-open byte interval in the candidate that may differ."""

    start: int
    end: int

    def as_dict(self) -> dict[str, int]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class PreservationReport:
    """Successful byte-preservation comparison summary."""

    file_size: int
    page_size: int
    page_count: int
    allowed_interval_count: int
    changed_bytes_within_allowed_intervals: int

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "pass",
            "file_size": self.file_size,
            "page_size": self.page_size,
            "page_count": self.page_count,
            "allowed_interval_count": self.allowed_interval_count,
            "changed_bytes_within_allowed_intervals": (
                self.changed_bytes_within_allowed_intervals
            ),
            "claim_boundary": CLAIM_BOUNDARY,
            "jet_structural_correctness_claimed": False,
            "dao_compatibility_claimed": False,
        }


@dataclass(frozen=True)
class _FileSnapshot:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int

    @classmethod
    def capture(cls, source: BinaryIO) -> _FileSnapshot:
        observed = os.fstat(source.fileno())
        return cls(
            observed.st_dev,
            observed.st_ino,
            observed.st_mode,
            observed.st_size,
            observed.st_mtime_ns,
            observed.st_ctime_ns,
        )

    def identity(self) -> tuple[int, int]:
        return self.device, self.inode

    def as_dict(self) -> dict[str, int]:
        return {
            "device": self.device,
            "inode": self.inode,
            "mode": self.mode,
            "size": self.size,
            "modified_ns": self.modified_ns,
            "changed_ns": self.changed_ns,
        }


def _assert_path_identity(path: Path, expected: _FileSnapshot, label: str) -> None:
    try:
        observed = path.stat()
    except OSError as error:
        raise PreservationIoError(
            "input_changed", f"{label} path changed during comparison"
        ) from error
    identity = (observed.st_dev, observed.st_ino)
    if identity != expected.identity():
        raise PreservationIoError(
            "input_changed",
            f"{label} path names a different file after comparison",
            details={
                "label": label,
                "expected_device": expected.device,
                "expected_inode": expected.inode,
                "observed_device": observed.st_dev,
                "observed_inode": observed.st_ino,
            },
        )


def _integer(value: object, location: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PreservationContractError(
            "invalid_integer", f"{location} must be an integer >= {minimum}"
        )
    return value


def _page_count(file_size: int, page_size: int) -> int:
    size = _integer(file_size, "file size")
    page = _integer(page_size, "page size", minimum=1)
    if size % page != 0:
        raise PreservationContractError(
            "unaligned_file_size",
            "file size is not an exact multiple of the declared page size",
            details={"file_size": size, "page_size": page},
        )
    return size // page


def validate_intervals(
    intervals: Iterable[AllowedInterval], file_size: int
) -> tuple[AllowedInterval, ...]:
    """Validate and retain one canonical interval sequence.

    Canonical intervals are non-empty, sorted, disjoint, and non-adjacent.
    Adjacent intervals must be merged so one allowed byte set has one encoding.
    """

    size = _integer(file_size, "file size")
    retained: list[AllowedInterval] = []
    previous: AllowedInterval | None = None
    for index, interval in enumerate(intervals):
        if index >= MAX_ALLOWED_INTERVALS:
            raise PreservationContractError(
                "too_many_intervals",
                f"allowed intervals exceed the {MAX_ALLOWED_INTERVALS}-entry limit",
            )
        if not isinstance(interval, AllowedInterval):
            raise PreservationContractError(
                "invalid_interval", f"allowed interval {index} has the wrong type"
            )
        start = _integer(interval.start, f"allowed interval {index} start")
        end = _integer(interval.end, f"allowed interval {index} end")
        if start >= end:
            raise PreservationContractError(
                "empty_interval",
                f"allowed interval {index} must have start < end",
                details={"interval_index": index, "start": start, "end": end},
            )
        if end > size:
            raise PreservationContractError(
                "interval_out_of_bounds",
                f"allowed interval {index} exceeds the file",
                details={
                    "interval_index": index,
                    "start": start,
                    "end": end,
                    "file_size": size,
                },
            )
        if previous is not None:
            if start < previous.start:
                code = "interval_order"
                message = "allowed intervals must be sorted by start offset"
            elif start < previous.end:
                code = "interval_overlap"
                message = "allowed intervals must not overlap"
            elif start == previous.end:
                code = "adjacent_intervals"
                message = "adjacent allowed intervals must be merged"
            else:
                code = ""
                message = ""
            if code:
                raise PreservationContractError(
                    code,
                    message,
                    details={
                        "interval_index": index,
                        "previous": previous.as_dict(),
                        "current": interval.as_dict(),
                    },
                )
        retained.append(interval)
        previous = interval
    return tuple(retained)


def _read(source: BinaryIO, size: int, label: str) -> bytes:
    try:
        return source.read(size)
    except OSError as error:
        raise PreservationIoError(
            "io_error", f"could not read {label}: {error}"
        ) from error


def _read_exact(source: BinaryIO, size: int, label: str) -> bytes:
    buffer = bytearray()
    while len(buffer) < size:
        requested = size - len(buffer)
        chunk = _read(source, requested, label)
        if not chunk:
            raise PreservationIoError(
                "input_changed",
                f"{label} ended before its recorded size",
            )
        if len(chunk) > requested:
            raise PreservationIoError(
                "invalid_read",
                f"{label} returned more bytes than requested",
            )
        buffer.extend(chunk)
    return bytes(buffer)


def verify_streams(
    original: BinaryIO,
    output: BinaryIO,
    *,
    file_size: int,
    allowed_intervals: Iterable[AllowedInterval] = (),
    page_size: int = DEFAULT_PAGE_SIZE,
    chunk_size: int = READ_CHUNK_BYTES,
) -> PreservationReport:
    """Compare two positioned binary streams without retaining whole files."""

    size = _integer(file_size, "file size")
    pages = _page_count(size, page_size)
    chunk_limit = _integer(chunk_size, "chunk size", minimum=1)
    if chunk_limit > READ_CHUNK_BYTES:
        raise PreservationContractError(
            "chunk_size_too_large",
            f"chunk size exceeds the {READ_CHUNK_BYTES}-byte verifier ceiling",
        )
    intervals = validate_intervals(allowed_intervals, size)
    interval_index = 0
    offset = 0
    changed = 0

    while offset < size:
        read_size = min(chunk_limit, size - offset)
        original_chunk = _read_exact(original, read_size, "original")
        output_chunk = _read_exact(output, read_size, "output")
        if original_chunk != output_chunk:
            for relative, (original_byte, output_byte) in enumerate(
                zip(original_chunk, output_chunk)
            ):
                absolute = offset + relative
                while (
                    interval_index < len(intervals)
                    and absolute >= intervals[interval_index].end
                ):
                    interval_index += 1
                if original_byte == output_byte:
                    continue
                if (
                    interval_index >= len(intervals)
                    or absolute < intervals[interval_index].start
                ):
                    raise PreservationMismatch(
                        "change_outside_allowed_intervals",
                        f"first disallowed changed byte is at offset {absolute}",
                        details={
                            "offset": absolute,
                            "original_byte": original_byte,
                            "output_byte": output_byte,
                        },
                    )
                changed += 1
        offset += read_size

    if _read(original, 1, "original") or _read(output, 1, "output"):
        raise PreservationIoError(
            "input_changed", "an input grew beyond its recorded size"
        )
    return PreservationReport(size, page_size, pages, len(intervals), changed)


def verify_files(
    original_path: Path,
    output_path: Path,
    *,
    allowed_intervals: Iterable[AllowedInterval] = (),
    page_size: int = DEFAULT_PAGE_SIZE,
) -> PreservationReport:
    """Open and compare two regular files through fixed-size streaming reads."""

    try:
        with original_path.open("rb") as original, output_path.open("rb") as output:
            original_before = _FileSnapshot.capture(original)
            output_before = _FileSnapshot.capture(output)
            if not stat.S_ISREG(original_before.mode) or not stat.S_ISREG(
                output_before.mode
            ):
                raise PreservationContractError(
                    "non_regular_input", "both inputs must be regular files"
                )
            if original_before.identity() == output_before.identity():
                raise PreservationContractError(
                    "same_input_file",
                    "original and output must be distinct underlying files",
                    details={
                        "device": original_before.device,
                        "inode": original_before.inode,
                    },
                )
            if original_before.size != output_before.size:
                raise PreservationMismatch(
                    "size_mismatch",
                    "original and output file sizes differ",
                    details={
                        "original_size": original_before.size,
                        "output_size": output_before.size,
                    },
                )
            report: PreservationReport | None = None
            pending_error: Exception | None = None
            try:
                report = verify_streams(
                    original,
                    output,
                    file_size=original_before.size,
                    allowed_intervals=allowed_intervals,
                    page_size=page_size,
                )
            except Exception as error:
                pending_error = error

            original_after = _FileSnapshot.capture(original)
            output_after = _FileSnapshot.capture(output)
            changed_inputs = {
                label: {"before": before.as_dict(), "after": after.as_dict()}
                for label, before, after in (
                    ("original", original_before, original_after),
                    ("output", output_before, output_after),
                )
                if before != after
            }
            if changed_inputs:
                raise PreservationIoError(
                    "input_changed",
                    "an input changed during comparison",
                    details={"changed_inputs": changed_inputs},
                )
            _assert_path_identity(original_path, original_after, "original")
            _assert_path_identity(output_path, output_after, "output")
            if pending_error is not None:
                raise pending_error
            assert report is not None
            return report
    except PreservationError:
        raise
    except OSError as error:
        raise PreservationIoError(
            "io_error", f"could not compare input files: {error}"
        ) from error
