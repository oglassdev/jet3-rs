#!/usr/bin/env python3
"""Validate the fail-closed G0 repository, dependency, and provenance contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from validation import validate_support_matrix
from validation.repository_common import (
    FIXTURE_ROOTS,
    FUZZ_ID,
    PROVENANCE_ID,
    SAFE_PATH,
    SCENARIO_ID,
    SHA256,
    ContractError,
    exact_keys as _exact_keys,
    load_object as _load_object,
    nonempty as _nonempty,
    resolve_file as _resolve_file,
    safe_path as _safe_path,
    sha256 as _sha256,
    unique_strings as _unique_strings,
)
from validation.repository_fixture_external import (
    fixture_candidates as _fixture_candidates,
    validate_external_observational_corpus,
    validate_repository_fixtures,
    validate_seed_manifest,
)
from validation.repository_provenance import (
    PROVENANCE_HEADING,
    provenance_sections as _provenance_sections,
    tracked_files as _tracked_files,
    validate_format_knowledge,
)
from validation.repository_shape import validate_contract_shape
from validation.repository_workspace_dependency import (
    FFI_OR_UNSAFE,
    PROCESS_EXECUTION,
    PROHIBITED_PACKAGE_NAMES,
    PROHIBITED_RUNTIME_TEXT,
    _production_source_files,
    _toml,
    cargo_metadata as _cargo_metadata,
    validate_dependency_graph,
    validate_workspace_and_sources,
)

CONTRACT_PATH = Path("docs/validation/repository-contract.json")
PROVENANCE_PATH = Path("docs/PROVENANCE.md")
SUPPORT_MATRIX_PATH = Path("docs/validation/support-matrix.json")


def validate_repository(
    root: Path,
    *,
    metadata: dict[str, Any] | None = None,
    tracked: set[str] | None = None,
) -> list[str]:
    """Return all G0 repository-contract violations in deterministic order."""
    root = root.resolve()
    contract = _load_object(root / CONTRACT_PATH, "repository contract")
    errors = validate_contract_shape(contract)
    if errors:
        return sorted(errors)

    workspace_errors, source_files = validate_workspace_and_sources(root, contract)
    errors.extend(workspace_errors)
    if metadata is None:
        metadata = _cargo_metadata(root)
    errors.extend(validate_dependency_graph(root, contract, metadata))
    if tracked is None:
        tracked = _tracked_files(root)

    provenance_path, _ = _resolve_file(
        root, PROVENANCE_PATH.as_posix(), "provenance"
    )
    provenance_text = provenance_path.read_text(encoding="utf-8")
    provenance_sections = _provenance_sections(provenance_text)
    errors.extend(
        validate_format_knowledge(root, contract, source_files, provenance_text)
    )

    fixture_path, _ = _resolve_file(
        root,
        contract["fixtures"]["repository_manifest"],
        "repository fixture manifest",
    )
    seed_path, _ = _resolve_file(
        root, contract["fixtures"]["seed_manifest"], "seed manifest"
    )
    test_path, _ = _resolve_file(root, "tests/manifest.json", "test manifest")
    errors.extend(
        validate_repository_fixtures(
            root,
            _load_object(fixture_path, "repository fixture manifest"),
            tracked,
            provenance_sections,
            _load_object(test_path, "test manifest"),
        )
    )
    errors.extend(
        validate_seed_manifest(
            root,
            _load_object(seed_path, "seed manifest"),
            tracked,
        )
    )

    external_policy = contract["fixtures"]["external_observational"]
    external_manifest_path, _ = _resolve_file(
        root, external_policy["manifest"], "external corpus manifest"
    )
    external_docs_path, _ = _resolve_file(
        root, external_policy["documentation"], "external corpus documentation"
    )
    errors.extend(
        validate_external_observational_corpus(
            _load_object(external_manifest_path, "external corpus manifest"),
            external_docs_path.read_text(encoding="utf-8"),
            provenance_sections,
            external_policy,
        )
    )

    support_path, _ = _resolve_file(
        root, SUPPORT_MATRIX_PATH.as_posix(), "support matrix"
    )
    support = _load_object(support_path, "support matrix")
    errors.extend(
        f"support matrix: {error}"
        for error in validate_support_matrix(support, root)
    )
    return sorted(set(errors))


def _parse_args(arguments: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    return parser.parse_args(arguments)


def main(arguments: Iterable[str] | None = None) -> int:
    args = _parse_args(arguments)
    try:
        errors = validate_repository(args.repo_root)
    except (ContractError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(
            f"repository-contract validation failed with {len(errors)} error(s)",
            file=sys.stderr,
        )
        return 1
    print("repository-contract validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
