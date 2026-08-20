"""Bounded structural validation for Windows DAO SSH artifact archives.

This module validates transport structure and request identity only. It does not
extract archives, import evidence into tracked fixtures, validate the M1
protocol, or establish any DAO or Jet compatibility claim.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
from typing import Any, BinaryIO
import unicodedata
import zipfile


PROVIDER_PROBE = "provider-probe"
M1_CONTROLLED = "m1-controlled"
SUPPORTED_MODES = frozenset((PROVIDER_PROBE, M1_CONTROLLED))
SUPPORTED_COMPRESSION = frozenset((zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))
ROOT = "artifacts"
REQUIRED_JSON = frozenset(("environment.json", "remote-job.json"))
OPTIONAL_TOP_LEVEL_FILES = frozenset(("stdout.log", "stderr.log"))
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")
EOCD_SIGNATURE = b"PK\x05\x06"
EOCD_SIZE = 22
ZIP64_EXTRA_ID = 0x0001
COPY_CHUNK = 64 * 1024


class ArchiveValidationError(ValueError):
    """A downloaded archive violates its bounded transport contract."""


@dataclass(frozen=True)
class ArchiveLimits:
    """Independent ceilings applied before and during ZIP traversal."""

    maximum_archive_bytes: int = 300 * 1024 * 1024
    maximum_entries: int = 10_000
    maximum_central_directory_bytes: int = 16 * 1024 * 1024
    maximum_entry_uncompressed_bytes: int = 64 * 1024 * 1024
    maximum_entry_compressed_bytes: int = 64 * 1024 * 1024
    maximum_total_uncompressed_bytes: int = 300 * 1024 * 1024
    maximum_total_compressed_bytes: int = 300 * 1024 * 1024
    maximum_json_bytes: int = 1024 * 1024
    maximum_compression_ratio: int = 200
    maximum_path_characters: int = 1024
    maximum_path_depth: int = 32

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class ArchiveValidation:
    """Transport-level facts retained after complete bounded traversal."""

    archive: Path
    mode: str
    entry_count: int
    compressed_bytes: int
    uncompressed_bytes: int
    remote_job: dict[str, Any]
    environment: dict[str, Any]


def _regular_archive_size(path: Path, source: BinaryIO, maximum: int) -> int:
    try:
        path_metadata = path.lstat()
        metadata = os.fstat(source.fileno())
    except OSError as error:
        raise ArchiveValidationError(f"cannot inspect archive: {error}") from error
    if (
        not stat.S_ISREG(path_metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or not os.path.samestat(path_metadata, metadata)
    ):
        raise ArchiveValidationError("archive must be a regular non-symlink file")
    if metadata.st_size < EOCD_SIZE:
        raise ArchiveValidationError("archive is too short to contain a ZIP directory")
    if metadata.st_size > maximum:
        raise ArchiveValidationError("archive exceeds its byte limit")
    return metadata.st_size


def _preflight_directory(source: BinaryIO, size: int, limits: ArchiveLimits) -> int:
    """Bound central-directory allocation before ``zipfile`` constructs entries."""

    try:
        source.seek(size - EOCD_SIZE)
        end = source.read(EOCD_SIZE)
    except OSError as error:
        raise ArchiveValidationError(f"cannot read archive directory: {error}") from error
    if len(end) != EOCD_SIZE or end[:4] != EOCD_SIGNATURE:
        raise ArchiveValidationError("archive must have an uncommented non-ZIP64 directory")
    (
        _signature,
        disk_number,
        directory_disk,
        entries_on_disk,
        entries,
        directory_size,
        directory_offset,
        comment_size,
    ) = struct.unpack("<4s4H2LH", end)
    if comment_size != 0:
        raise ArchiveValidationError("archive comments are not supported")
    if disk_number != 0 or directory_disk != 0 or entries_on_disk != entries:
        raise ArchiveValidationError("multi-disk ZIP archives are not supported")
    if entries == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        raise ArchiveValidationError("ZIP64 archives are not supported")
    if entries > limits.maximum_entries:
        raise ArchiveValidationError("archive exceeds its entry-count limit")
    if directory_size > limits.maximum_central_directory_bytes:
        raise ArchiveValidationError("archive central directory exceeds its byte limit")
    if directory_offset + directory_size != size - EOCD_SIZE:
        raise ArchiveValidationError("archive directory placement is not canonical")
    return entries


def _extra_field_ids(extra: bytes, name: str) -> set[int]:
    identifiers: set[int] = set()
    position = 0
    while position < len(extra):
        if len(extra) - position < 4:
            raise ArchiveValidationError(f"{name}: truncated ZIP extra field")
        identifier, length = struct.unpack_from("<HH", extra, position)
        position += 4
        end = position + length
        if end > len(extra):
            raise ArchiveValidationError(f"{name}: truncated ZIP extra field payload")
        identifiers.add(identifier)
        position = end
    return identifiers


def _safe_entry_name(info: zipfile.ZipInfo, limits: ArchiveLimits) -> tuple[str, bool]:
    original = info.orig_filename
    if "\x00" in original or original != info.filename:
        raise ArchiveValidationError("archive entry path contains a NUL byte")
    name = info.filename
    if not name or len(name) > limits.maximum_path_characters:
        raise ArchiveValidationError("archive entry path length is invalid")
    if "\\" in name:
        raise ArchiveValidationError(f"{name!r}: backslashes are not allowed")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise ArchiveValidationError(f"{name!r}: absolute or drive path is not allowed")
    is_directory = name.endswith("/")
    trimmed = name[:-1] if is_directory else name
    if not trimmed or "//" in trimmed:
        raise ArchiveValidationError(f"{name!r}: empty path component is not allowed")
    parts = trimmed.split("/")
    if len(parts) > limits.maximum_path_depth:
        raise ArchiveValidationError(f"{name!r}: path depth exceeds its limit")
    for part in parts:
        if part in ("", ".", ".."):
            raise ArchiveValidationError(f"{name!r}: traversal component is not allowed")
        if ":" in part or part.endswith((" ", ".")):
            raise ArchiveValidationError(f"{name!r}: Windows-ambiguous component is not allowed")
        if any(ord(character) < 32 or ord(character) == 127 for character in part):
            raise ArchiveValidationError(f"{name!r}: control character is not allowed")
    path = PurePosixPath(trimmed)
    if path.is_absolute():
        raise ArchiveValidationError(f"{name!r}: absolute path is not allowed")
    return path.as_posix(), is_directory


def _reject_special_entry(info: zipfile.ZipInfo, name: str, is_directory: bool) -> None:
    if info.flag_bits & 0x1:
        raise ArchiveValidationError(f"{name}: encrypted entries are not supported")
    if info.compress_type not in SUPPORTED_COMPRESSION:
        raise ArchiveValidationError(f"{name}: unsupported ZIP compression method")
    if ZIP64_EXTRA_ID in _extra_field_ids(info.extra, name):
        raise ArchiveValidationError(f"{name}: ZIP64 entry metadata is not supported")

    unix_mode = info.external_attr >> 16
    unix_type = stat.S_IFMT(unix_mode)
    dos_attributes = info.external_attr & 0xFFFF
    if unix_type == stat.S_IFLNK or dos_attributes & 0x400:
        raise ArchiveValidationError(f"{name}: symlink or reparse-point entry is not allowed")
    allowed_type = stat.S_IFDIR if is_directory else stat.S_IFREG
    if unix_type not in (0, allowed_type):
        raise ArchiveValidationError(f"{name}: special filesystem entry is not allowed")
    if bool(dos_attributes & 0x10) != is_directory and dos_attributes & 0x10:
        raise ArchiveValidationError(f"{name}: directory attributes disagree with its path")
    if is_directory and (info.file_size != 0 or info.compress_size != 0):
        raise ArchiveValidationError(f"{name}: directory entry carries file content")


def _check_entry_sizes(info: zipfile.ZipInfo, name: str, limits: ArchiveLimits) -> None:
    if info.file_size < 0 or info.compress_size < 0:
        raise ArchiveValidationError(f"{name}: negative ZIP size is invalid")
    if info.file_size > limits.maximum_entry_uncompressed_bytes:
        raise ArchiveValidationError(f"{name}: uncompressed entry exceeds its byte limit")
    if info.compress_size > limits.maximum_entry_compressed_bytes:
        raise ArchiveValidationError(f"{name}: compressed entry exceeds its byte limit")
    if info.file_size and info.compress_size == 0:
        raise ArchiveValidationError(f"{name}: nonempty entry has zero compressed bytes")
    if (
        info.compress_size
        and info.file_size
        > info.compress_size * limits.maximum_compression_ratio
    ):
        raise ArchiveValidationError(f"{name}: suspicious compression ratio")


def _inventory_role(name: str, is_directory: bool, mode: str) -> str | None:
    if name == ROOT:
        if not is_directory:
            raise ArchiveValidationError("artifacts must be the archive root directory")
        return None
    prefix = f"{ROOT}/"
    if not name.startswith(prefix):
        raise ArchiveValidationError(f"{name}: entry is outside the artifacts root")
    relative = name[len(prefix) :]
    if "/" not in relative:
        if is_directory:
            if mode == M1_CONTROLLED and relative == "evidence":
                return "evidence-directory"
            raise ArchiveValidationError(f"{name}: unexpected top-level directory")
        if relative in REQUIRED_JSON or relative in OPTIONAL_TOP_LEVEL_FILES:
            return relative
        raise ArchiveValidationError(f"{name}: unexpected top-level file")
    if mode != M1_CONTROLLED or not relative.startswith("evidence/"):
        raise ArchiveValidationError(f"{name}: unexpected nested inventory")
    return "evidence-directory" if is_directory else "evidence-content"


def _decode_json(data: bytes, name: str) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ArchiveValidationError(f"{name}: duplicate JSON property {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ArchiveValidationError(f"{name}: non-finite JSON number {value}")

    try:
        text = data.decode("utf-8", "strict")
        document = json.loads(
            text,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except ArchiveValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ArchiveValidationError(f"{name}: invalid bounded UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ArchiveValidationError(f"{name}: JSON root must be an object")
    return document


def _validate_remote_job(
    document: dict[str, Any],
    *,
    mode: str,
    commit: str,
    run_id: str,
    exit_code: int,
) -> str:
    expected = {
        "commit": commit,
        "downloadable": True,
        "exit_code": exit_code,
        "job": mode,
        "run_id": run_id,
    }
    for key, value in expected.items():
        actual = document.get(key)
        if key == "exit_code" and (not isinstance(actual, int) or isinstance(actual, bool)):
            raise ArchiveValidationError("remote-job.json: exit_code must be an integer")
        if actual != value:
            raise ArchiveValidationError(
                f"remote-job.json: {key} does not match the requested job"
            )
    phase = document.get("phase")
    if mode == PROVIDER_PROBE:
        valid_phase = phase == PROVIDER_PROBE
    else:
        valid_phase = phase == M1_CONTROLLED or (
            phase == PROVIDER_PROBE and exit_code in (1, 3)
        )
    if not valid_phase:
        raise ArchiveValidationError(
            "remote-job.json: phase and exit code do not form a valid job state"
        )
    return phase


def _validate_environment_status(
    document: dict[str, Any], *, phase: str, exit_code: int
) -> None:
    if phase == M1_CONTROLLED:
        expected = "ready"
    else:
        expected = {0: "ready", 1: "error", 3: "blocked"}[exit_code]
    if document.get("status") != expected:
        raise ArchiveValidationError(
            "environment.json: status does not match the recorded execution phase"
        )


def _validated_expectations(mode: str, commit: str, run_id: str, exit_code: int) -> None:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported Windows DAO archive mode: {mode!r}")
    if COMMIT.fullmatch(commit) is None:
        raise ValueError("expected commit must be 40 lowercase hexadecimal characters")
    if RUN_ID.fullmatch(run_id) is None:
        raise ValueError("expected run ID is not protocol-valid")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code not in (0, 1, 3):
        raise ValueError("expected exit code must be 0, 1, or 3")


def validate_archive(
    archive_path: os.PathLike[str] | str,
    *,
    mode: str,
    expected_commit: str,
    expected_run_id: str,
    expected_exit_code: int,
    limits: ArchiveLimits | None = None,
) -> ArchiveValidation:
    """Validate a downloaded archive without extracting or claiming DAO correctness."""

    _validated_expectations(mode, expected_commit, expected_run_id, expected_exit_code)
    policy = limits if limits is not None else ArchiveLimits()
    path = Path(archive_path)

    names: set[str] = set()
    collision_keys: set[str] = set()
    required: dict[str, bytes] = {}
    evidence_content = 0
    total_uncompressed = 0
    total_compressed = 0
    try:
        with path.open("rb") as archive_source:
            archive_size = _regular_archive_size(
                path, archive_source, policy.maximum_archive_bytes
            )
            expected_entries = _preflight_directory(archive_source, archive_size, policy)
            with zipfile.ZipFile(archive_source, "r", allowZip64=False) as archive:
                entries = archive.infolist()
                if len(entries) != expected_entries:
                    raise ArchiveValidationError(
                        "ZIP directory entry count changed during parsing"
                    )
                checked: list[tuple[zipfile.ZipInfo, str, bool, str | None]] = []
                for info in entries:
                    name, is_directory = _safe_entry_name(info, policy)
                    if name in names:
                        raise ArchiveValidationError(f"{name}: duplicate archive path")
                    names.add(name)
                    collision_key = unicodedata.normalize("NFC", name).casefold()
                    if collision_key in collision_keys:
                        raise ArchiveValidationError(
                            f"{name}: case-colliding archive path"
                        )
                    collision_keys.add(collision_key)
                    _reject_special_entry(info, name, is_directory)
                    _check_entry_sizes(info, name, policy)
                    role = _inventory_role(name, is_directory, mode)
                    total_uncompressed += info.file_size
                    total_compressed += info.compress_size
                    if total_uncompressed > policy.maximum_total_uncompressed_bytes:
                        raise ArchiveValidationError(
                            "archive exceeds total uncompressed byte limit"
                        )
                    if total_compressed > policy.maximum_total_compressed_bytes:
                        raise ArchiveValidationError(
                            "archive exceeds total compressed byte limit"
                        )
                    checked.append((info, name, is_directory, role))
                if total_compressed > archive_size:
                    raise ArchiveValidationError("compressed entry sizes exceed archive size")

                observed_uncompressed = 0
                for info, name, is_directory, role in checked:
                    if is_directory:
                        continue
                    capture = bytearray() if role in REQUIRED_JSON else None
                    entry_bytes = 0
                    with archive.open(info, "r") as entry_source:
                        while True:
                            chunk = entry_source.read(COPY_CHUNK)
                            if not chunk:
                                break
                            entry_bytes += len(chunk)
                            observed_uncompressed += len(chunk)
                            if entry_bytes > policy.maximum_entry_uncompressed_bytes:
                                raise ArchiveValidationError(
                                    f"{name}: expanded data exceeds its byte limit"
                                )
                            if (
                                observed_uncompressed
                                > policy.maximum_total_uncompressed_bytes
                            ):
                                raise ArchiveValidationError(
                                    "expanded archive exceeds total uncompressed byte limit"
                                )
                            if capture is not None:
                                if len(capture) + len(chunk) > policy.maximum_json_bytes:
                                    raise ArchiveValidationError(
                                        f"{name}: JSON exceeds its byte limit"
                                    )
                                capture.extend(chunk)
                    if entry_bytes != info.file_size:
                        raise ArchiveValidationError(
                            f"{name}: expanded size disagrees with ZIP metadata"
                        )
                    if capture is not None:
                        required[role] = bytes(capture)
                    if role == "evidence-content":
                        evidence_content += 1
    except ArchiveValidationError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ArchiveValidationError(f"cannot safely traverse archive: {error}") from error

    missing = REQUIRED_JSON - required.keys()
    if missing:
        raise ArchiveValidationError(f"archive is missing required JSON: {sorted(missing)}")
    remote_job = _decode_json(required["remote-job.json"], "remote-job.json")
    environment = _decode_json(required["environment.json"], "environment.json")
    phase = _validate_remote_job(
        remote_job,
        mode=mode,
        commit=expected_commit,
        run_id=expected_run_id,
        exit_code=expected_exit_code,
    )
    _validate_environment_status(
        environment, phase=phase, exit_code=expected_exit_code
    )
    if phase == PROVIDER_PROBE and evidence_content:
        raise ArchiveValidationError("probe-stage archive unexpectedly contains M1 evidence")
    if (
        phase == M1_CONTROLLED
        and expected_exit_code in (0, 1)
        and evidence_content == 0
    ):
        raise ArchiveValidationError("completed M1 archive contains no evidence content")
    return ArchiveValidation(
        archive=path,
        mode=mode,
        entry_count=expected_entries,
        compressed_bytes=total_compressed,
        uncompressed_bytes=total_uncompressed,
        remote_job=remote_job,
        environment=environment,
    )
