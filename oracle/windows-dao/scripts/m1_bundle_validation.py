#!/usr/bin/env python3
"""Bounds and value-observation helpers for DAO protocol 1.1 bundles."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import Counter
from pathlib import Path
from typing import Any

from protocol_validation import ValidationError

MAX_JSON_BYTES = 1024 * 1024
MAX_DATABASE_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
MAX_PAYLOAD_FILES = 33
MAX_BUNDLE_ENTRIES = 64
MAX_BUNDLE_DEPTH = 4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValidationError(f"duplicate JSON object key {key!r}")
        document[key] = value
    return document


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _stable_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def bounded_file_identity(
    path: Path, maximum_bytes: int, *, retain: bool = False
) -> tuple[int, str, bytes | None]:
    """Hash one stable regular non-reparse file within an exact byte ceiling."""
    digest = hashlib.sha256()
    retained: list[bytes] | None = [] if retain else None
    try:
        before_path = path.lstat()
        if (
            not stat.S_ISREG(before_path.st_mode)
            or _is_reparse(before_path)
            or path.is_symlink()
        ):
            raise ValidationError(f"{path}: payload is not a regular file")
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or _is_reparse(before)
                or _stable_identity(before) != _stable_identity(before_path)
            ):
                raise ValidationError(f"{path}: payload identity changed")
            total = 0
            while True:
                chunk = handle.read(min(64 * 1024, maximum_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum_bytes:
                    raise ValidationError(
                        f"{path}: payload exceeds {maximum_bytes} bytes"
                    )
                digest.update(chunk)
                if retained is not None:
                    retained.append(chunk)
            after = os.fstat(handle.fileno())
        after_path = path.lstat()
        if (
            _stable_identity(before) != _stable_identity(after)
            or _stable_identity(after) != _stable_identity(after_path)
            or _is_reparse(after_path)
        ):
            raise ValidationError(f"{path}: payload changed while being read")
        data = b"".join(retained) if retained is not None else None
        return total, digest.hexdigest(), data
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"{path}: cannot read payload: {exc}") from exc


def load_json(path: Path) -> Any:
    """Load one bounded M1 JSON document and reject duplicate object keys."""
    try:
        _, _, retained = bounded_file_identity(
            path, MAX_JSON_BYTES, retain=True
        )
        assert retained is not None
        if retained.startswith(b"\xef\xbb\xbf"):
            raise ValidationError(
                f"{path}: UTF-8 byte-order marks are forbidden"
            )
        return json.loads(
            retained.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except ValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path}: cannot load JSON: {exc}") from exc


def discover_bundle_files(bundle: Path) -> set[str]:
    """Walk a bounded tree without following links, junctions, or reparses."""
    discovered: set[str] = set()
    pending: list[tuple[Path, int]] = [(bundle, 0)]
    visited = 0
    try:
        while pending:
            directory, depth = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > MAX_BUNDLE_ENTRIES:
                        raise ValidationError(
                            "bundle exceeds directory-entry limit"
                        )
                    metadata = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or _is_reparse(metadata):
                        raise ValidationError(
                            "bundle symlinks and junctions are forbidden: "
                            f"{entry.path}"
                        )
                    candidate = Path(entry.path)
                    if stat.S_ISDIR(metadata.st_mode):
                        if depth >= MAX_BUNDLE_DEPTH:
                            raise ValidationError(
                                "bundle exceeds directory-depth limit"
                            )
                        pending.append((candidate, depth + 1))
                    elif stat.S_ISREG(metadata.st_mode):
                        discovered.add(
                            candidate.relative_to(bundle).as_posix()
                        )
                    else:
                        raise ValidationError(
                            f"bundle contains a non-regular entry: {entry.path}"
                        )
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"cannot enumerate bundle: {exc}") from exc
    return discovered


def validate_counts(
    document: dict[str, Any], key: str, results: list[dict[str, Any]]
) -> None:
    """Require exact selected and per-status result counts."""
    counts = document[key]
    observed = Counter(result["status"] for result in results)
    if counts["selected"] != len(results):
        raise ValidationError(f"$.{key}.selected: does not match result list")
    for status in ("pass", "fail", "blocked", "error", "skipped"):
        if counts[status] != observed[status]:
            raise ValidationError(f"$.{key}.{status}: does not match result list")


def derived_report_status(document: dict[str, Any]) -> str:
    """Derive the report status with deterministic failure precedence."""
    statuses = [
        result["status"]
        for collection in (document["scenarios"], document["pairs"])
        for result in collection
    ]
    if "error" in statuses:
        return "error"
    if "fail" in statuses:
        return "fail"
    if "blocked" in statuses or "skipped" in statuses:
        return "blocked"
    return "pass"


def _declared_payload(value: dict[str, Any]) -> tuple[bytes, str, str | None]:
    dao_type = value["dao_type"]
    if dao_type == "dbBinary":
        payload = bytes.fromhex(value["value"])
        return payload, "System.Byte[]", value["value"]
    if dao_type == "dbText":
        return value["value"].encode("utf-8"), "System.String", None
    if dao_type == "dbMemo":
        payload = (value["ascii_character"] * value["length"]).encode("ascii")
        return payload, "System.String", None
    payload = bytes([value["byte"]]) * value["length"]
    return payload, "System.Byte[]", None


def expected_value_observation(
    value: dict[str, Any], row_ordinal: int
) -> dict[str, Any]:
    """Return the exact structured observation for one passing value."""
    payload, runtime_type, exact_hex = _declared_payload(value)
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "dao_type": value["dao_type"],
        "field": value["field"],
        "row_ordinal": row_ordinal,
        "input_runtime_type": runtime_type,
        "readback_runtime_type": runtime_type,
        "input_length": len(payload),
        "readback_length": len(payload),
        "input_sha256": digest,
        "readback_sha256": digest,
        "input_hex": exact_hex,
        "readback_hex": exact_hex,
    }


def validate_log_details(
    scenario: dict[str, Any], log: dict[str, Any], result_status: str
) -> None:
    """Bind a retained log to the exact controlled action and value sequence."""
    full_actions = (
        ["activate_provider"]
        + [step["action"] for step in scenario["steps"]]
        + ["reopen_database", "snapshot", "finalize"]
    )
    actions = [entry["action"] for entry in log["entries"]]
    if result_status == "pass":
        if actions != full_actions:
            raise ValidationError(
                f"{scenario['scenario_id']}: operation action order differs"
            )
    else:
        attempted = actions[:-1]
        if (
            not actions
            or actions[-1] != "finalize"
            or not attempted
            or attempted != full_actions[: len(attempted)]
        ):
            raise ValidationError(
                f"{scenario['scenario_id']}: operation action prefix differs"
            )
        nonpassing = [
            index
            for index, entry in enumerate(log["entries"])
            if entry["status"] != "pass"
        ]
        permitted = (
            [len(log["entries"]) - 1],
            [len(log["entries"]) - 2, len(log["entries"]) - 1],
        )
        if nonpassing not in permitted:
            raise ValidationError(
                f"{scenario['scenario_id']}: operations continued after failure"
            )

    row_ordinal = 0
    aligned_steps = [None] + scenario["steps"] + [None, None, None]
    for position, (entry, step) in enumerate(zip(log["entries"], aligned_steps)):
        if "value_observations" not in entry or "error" not in entry:
            raise ValidationError(
                f"{scenario['scenario_id']}: log entry {position} "
                "lacks structured fields"
            )
        if (entry["status"] == "pass") != (entry["error"] is None):
            raise ValidationError(
                f"{scenario['scenario_id']}: log entry error/status differs"
            )
        if result_status == "pass":
            expected: list[dict[str, Any]] = []
            if step is not None and step["action"] == "insert_row":
                expected = [
                    expected_value_observation(value, row_ordinal)
                    for value in step["arguments"]["values"]
                ]
                row_ordinal += 1
            if entry["value_observations"] != expected:
                raise ValidationError(
                    f"{scenario['scenario_id']}: log value observation differs"
                )
