"""Types and checked document rules for detached release evidence."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

OVERLAY_NAME = "release-evidence.json"
OVERLAY_SCHEMA_PATH = "docs/validation/schema/release-evidence-overlay.schema.json"
POLICY_PATH = "docs/validation/evidence-policy.json"
POLICY_SCHEMA_PATH = "docs/validation/schema/evidence-policy.schema.json"
ACCEPTANCE_UNTRACKED_EXCEPTION = "artifacts/acceptance/**"
HARD_MAX_OVERLAY_BYTES = 16 * 1024 * 1024
HARD_MAX_JSON_DEPTH = 64
HARD_MAX_JSON_NODES = 1_000_000

SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
ADAPTER_ID = re.compile(r"^[a-z][a-z0-9_]*_v[1-9][0-9]*$")
SCENARIO_ID = re.compile(
    r"^(?:DAO-(?:GEN|READ|WRITE|UPDATE)|UT|IT|PROP|GOLD|CORR|REG)-"
    r"[A-Z0-9][A-Z0-9_-]*$"
)
RELATIVE_PATH = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
VERIFICATIONS = {
    "internal_only",
    "independent_check",
    "dao_opened",
    "dao_differential",
}
class ReleaseEvidenceError(RuntimeError):
    """The detached evidence is unsafe, malformed, stale, or unsupported."""


@dataclass(frozen=True)
class Limits:
    """Checked resource limits applied before adapter execution."""

    max_overlay_bytes: int
    max_file_count: int
    max_file_bytes: int
    max_total_file_bytes: int
    max_evidence_count: int
    max_files_per_evidence: int
    max_adapter_file_visits: int
    max_adapter_input_bytes: int
    max_json_depth: int
    max_json_nodes: int


@dataclass(frozen=True)
class ObjectIdentity:
    """Stable identity and mutation fields captured from one filesystem object."""

    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class ObjectBinding:
    """Fields used to bind a path-derived stat to a handle-derived stat.

    Identity is the ``(device, inode)`` pair defined by ``os.stat``, and it
    binds nothing unless ``inode`` is nonzero, so consumers must run it
    through ``release_evidence_tree.identifying_binding`` before comparing.
    ``file_type`` and ``size`` are the two further fields this module compares
    across the two acquisition methods. Timestamps are deliberately absent:
    Windows does not report them consistently between a path stat and a handle
    stat for a file that never changed.
    """

    device: int
    inode: int
    file_type: int
    size: int


@dataclass(frozen=True)
class StableObjectIdentity:
    """Identity fields that remain stable while a directory's contents change."""

    device: int
    inode: int
    file_type: int
    platform_token: int


@dataclass(frozen=True)
class ResolvedFile:
    """One regular file that was identity-checked and hash-bound."""

    relative_path: str
    path: Path
    size: int
    sha256: str
    identity: ObjectIdentity


@dataclass(frozen=True)
class ResolvedOverlay:
    """A fully validated overlay and the exact outputs of its adapters."""

    root: Path
    commit: str
    overlay_size: int
    overlay_sha256: str
    overlay_identity: ObjectIdentity
    files: tuple[ResolvedFile, ...]
    outputs: tuple[tuple[str, dict[str, Any]], ...]


ContractChecker = Callable[[str, str, str], bytes]
AdapterPolicyValidator = Callable[[Any], Any]


def fail(message: str) -> None:
    raise ReleaseEvidenceError(message)


def exact_keys(value: Any, expected: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{location}: expected object")
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing:
        fail(f"{location}: missing properties {missing!r}")
    if unknown:
        fail(f"{location}: unknown properties {unknown!r}")
    return value


def positive_int(value: Any, minimum: int, maximum: int, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        fail(f"{location}: expected integer")
    if value < minimum or value > maximum:
        fail(f"{location}: value outside [{minimum}, {maximum}]")
    return value


def require_integer(value: Any, expected: int, location: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        fail(f"{location}: expected integer {expected}")


def canonical_relative_path(value: Any, location: str) -> str:
    if not isinstance(value, str) or not RELATIVE_PATH.fullmatch(value):
        fail(f"{location}: expected canonical repository-style relative path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        fail(f"{location}: unsafe relative path")
    if any(
        part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
        for part in parsed.parts
    ):
        fail(f"{location}: Windows reserved path component")
    return value


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"JSON document: duplicate property {key!r}")
        result[key] = value
    return result


def parse_json(content: bytes, location: str) -> Any:
    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_duplicate_rejecting_object,
            parse_constant=lambda value: fail(
                f"{location}: non-finite JSON number {value!r}"
            ),
        )
    except UnicodeDecodeError as error:
        fail(f"{location}: expected UTF-8 JSON: {error}")
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        fail(f"{location}: invalid JSON: {error}")


def bound_json(
    value: Any,
    max_depth: int,
    max_nodes: int,
    location: str,
) -> None:
    nodes = 0
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        node, depth = pending.pop()
        nodes += 1
        if nodes > max_nodes:
            fail(f"{location}: JSON node limit exceeded")
        if depth > max_depth:
            fail(f"{location}: JSON depth limit exceeded")
        if isinstance(node, dict):
            pending.extend((item, depth + 1) for item in node.values())
        elif isinstance(node, list):
            pending.extend((item, depth + 1) for item in node)
        elif isinstance(node, float) and not math.isfinite(node):
            fail(f"{location}: non-finite JSON number")
        elif node is not None and not isinstance(node, (str, int, float, bool)):
            fail(f"{location}: unsupported JSON value")


def bound_json_hard(value: Any, location: str) -> None:
    bound_json(value, HARD_MAX_JSON_DEPTH, HARD_MAX_JSON_NODES, location)


def bound_json_with_policy(value: Any, limits: Limits, location: str) -> None:
    bound_json(value, limits.max_json_depth, limits.max_json_nodes, location)


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def parse_limits(value: Any) -> Limits:
    fields = {
        "max_overlay_bytes": (1024, HARD_MAX_OVERLAY_BYTES),
        "max_file_count": (1, 16384),
        "max_file_bytes": (1, 1024 * 1024 * 1024),
        "max_total_file_bytes": (1, 4 * 1024 * 1024 * 1024),
        "max_evidence_count": (1, 4096),
        "max_files_per_evidence": (1, 4096),
        "max_adapter_file_visits": (1, 65536),
        "max_adapter_input_bytes": (1, 4 * 1024 * 1024 * 1024),
        "max_json_depth": (1, HARD_MAX_JSON_DEPTH),
        "max_json_nodes": (1, HARD_MAX_JSON_NODES),
    }
    document = exact_keys(value, set(fields), "policy.limits")
    parsed = {
        key: positive_int(
            document[key], bounds[0], bounds[1], f"policy.limits.{key}"
        )
        for key, bounds in fields.items()
    }
    if parsed["max_file_bytes"] > parsed["max_total_file_bytes"]:
        fail("policy.limits: max_file_bytes exceeds max_total_file_bytes")
    return Limits(**parsed)


def validate_policy(
    value: Any,
    check_contract: ContractChecker,
    validate_adapters: AdapterPolicyValidator,
) -> tuple[Limits, Any]:
    policy = exact_keys(
        value,
        {"schema_version", "schema", "cleanliness", "limits", "adapters"},
        "policy",
    )
    require_integer(policy["schema_version"], 1, "policy.schema_version")
    schema = exact_keys(policy["schema"], {"path", "sha256"}, "policy.schema")
    if schema["path"] != POLICY_SCHEMA_PATH:
        fail(f"policy.schema.path: expected {POLICY_SCHEMA_PATH!r}")
    check_contract(schema["path"], schema["sha256"], "policy.schema")
    cleanliness = exact_keys(
        policy["cleanliness"],
        {"tracked", "untracked_exceptions"},
        "policy.cleanliness",
    )
    if cleanliness["tracked"] != "exact_head_index_and_worktree":
        fail("policy.cleanliness.tracked: unsupported cleanliness contract")
    if cleanliness["untracked_exceptions"] != [ACCEPTANCE_UNTRACKED_EXCEPTION]:
        fail(
            "policy.cleanliness.untracked_exceptions: expected exact "
            "acceptance-output exception"
        )
    limits = parse_limits(policy["limits"])
    return limits, validate_adapters(policy["adapters"])
