"""Tracked-source and provenance-ledger validation."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .repository_common import ContractError, resolve_file, sha256

PROVENANCE_HEADING = re.compile(
    r"^### ((?:SRC|OBS|EXP|FIX)-[0-9]{4})\b", re.MULTILINE
)
SOURCE_ID = re.compile(r"\bSRC-[0-9]{4}\b")
USAGE_FIELD = re.compile(r"^- Usage:(.*?)(?=^- Rights:)", re.MULTILINE | re.DOTALL)
BACKTICKED_VALUE = re.compile(r"`([^`\n]+)`")


def tracked_files(root: Path) -> set[str]:
    """Return all UTF-8 repository paths tracked by Git."""
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ContractError("git ls-files failed")
    try:
        return {
            item.decode("utf-8")
            for item in completed.stdout.split(b"\0")
            if item
        }
    except UnicodeDecodeError as error:
        raise ContractError("git returned a non-UTF-8 tracked path") from error


def provenance_sections(text: str) -> dict[str, str]:
    """Index provenance Markdown sections by their stable identifier."""
    matches = list(PROVENANCE_HEADING.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1)] = text[match.start() : end]
    return sections


def validate_source_usage_ledger(
    root: Path, tracked: set[str], provenance_text: str
) -> list[str]:
    """Require every substantive source citation to appear in its Usage field."""
    errors: list[str] = []
    sections = provenance_sections(provenance_text)
    usage_paths: dict[str, set[str]] = {}
    for identifier, section in sections.items():
        if not identifier.startswith("SRC-"):
            continue
        match = USAGE_FIELD.search(section)
        if match is None:
            errors.append(f"{identifier}: missing Usage field")
            continue
        usage_paths[identifier] = {
            value.removeprefix("./")
            for value in BACKTICKED_VALUE.findall(match.group(1))
            if "/" in value
        }

    for relative in sorted(tracked):
        if relative == "docs/PROVENANCE.md" or relative.startswith("tools/tests/"):
            continue
        path = root / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"SRC-" not in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{relative}: source citation appears in a non-UTF-8 file")
            continue
        for identifier in sorted(set(SOURCE_ID.findall(text))):
            if identifier not in sections:
                errors.append(f"{relative}: unknown source provenance ID {identifier}")
                continue
            declared = usage_paths.get(identifier, set())
            if not any(
                relative == candidate
                or (candidate.endswith("/") and relative.startswith(candidate))
                for candidate in declared
            ):
                errors.append(
                    f"{identifier}: Usage does not cover citing path {relative}"
                )
    return errors


def validate_format_knowledge(
    root: Path,
    document: dict[str, Any],
    source_files: set[str],
    provenance_text: str,
) -> list[str]:
    """Hash-bind format-bearing files and validate every cited ledger ID."""
    errors: list[str] = []
    knowledge = document["format_knowledge"]
    assertion_entries = knowledge["assertion_files"]
    reviewed_entries = knowledge["reviewed_non_assertion_files"]
    inventory = {
        entry["path"]: entry for entry in [*assertion_entries, *reviewed_entries]
    }
    if source_files != set(inventory):
        errors.append(
            "format-knowledge inventory mismatch; "
            f"missing={sorted(source_files - set(inventory))}, "
            f"stale={sorted(set(inventory) - source_files)}"
        )

    sections = provenance_sections(provenance_text)
    for entry in [*assertion_entries, *reviewed_entries]:
        relative = entry["path"]
        try:
            path, _ = resolve_file(root, relative, relative)
        except ContractError as error:
            errors.append(str(error))
            continue
        if sha256(path) != entry["sha256"]:
            errors.append(f"{relative}: format-knowledge SHA-256 mismatch")
            continue
        if entry in assertion_entries:
            text = path.read_text(encoding="utf-8")
            for identifier in entry["provenance_ids"]:
                if identifier not in text:
                    errors.append(
                        f"{relative}: cited provenance ID {identifier} is absent "
                        "from source"
                    )

    for entry in assertion_entries:
        relative = entry["path"]
        for identifier in entry["provenance_ids"]:
            if identifier not in sections:
                errors.append(f"{relative}: unknown provenance ID {identifier}")
    return errors
