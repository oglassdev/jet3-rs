"""Mutation-based smoke tests exposed by validate_contract.py --self-test."""

from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path
from typing import Any

from .common import git_blob, git_head, load_json, sha256_file
from .support import validate_support_matrix


def run(repo_root: Path, matrix_path: Path) -> int:
    try:
        current = load_json(matrix_path)
    except (OSError, ValueError) as error:
        print(f"self-test setup failed: {error}", file=sys.stderr)
        return 1
    head_commit = git_head(repo_root)
    if head_commit is None:
        print("self-test setup failed: cannot determine git HEAD", file=sys.stderr)
        return 1
    readme_hash = sha256_file(repo_root / "README.md")
    atomic_path = "crates/jet3/src/atomic.rs"
    atomic_blob = git_blob(repo_root, head_commit, atomic_path)
    atomic_test_path = "crates/jet3/src/atomic_tests.rs"
    atomic_test_blob = git_blob(repo_root, head_commit, atomic_test_path)
    if atomic_blob is None or atomic_test_blob is None:
        print("self-test setup failed: atomic evidence is absent at HEAD", file=sys.stderr)
        return 1
    atomic_hash = hashlib.sha256(atomic_blob).hexdigest()
    atomic_test_hash = hashlib.sha256(atomic_test_blob).hexdigest()
    atomic_scenario = ["UT-ATOMIC-001"]

    def evidence(
        kind: str,
        path: str,
        digest: str,
        capability_id: str = "database.open",
        *,
        scenarios: list[str] | None = None,
        commit: str = head_commit,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "kind": kind,
            "path": path,
            "sha256": digest,
            "commit": commit,
            "capability_id": capability_id,
        }
        if scenarios is not None:
            item["scenario_ids"] = scenarios
        return item

    cases: list[tuple[str, Any, str | None]] = [("current matrix", current, None)]
    omitted = copy.deepcopy(current)
    omitted["capabilities"].pop(0)
    cases.append(
        ("omitted canonical capability", omitted, "capability catalog mismatch")
    )

    renamed = copy.deepcopy(current)
    renamed["capabilities"][0]["id"] = "database.renamed"
    cases.append(
        ("renamed canonical capability", renamed, "capability catalog mismatch")
    )

    inserted = copy.deepcopy(current)
    extra = copy.deepcopy(inserted["capabilities"][0])
    extra["id"] = "database.inserted"
    inserted["capabilities"].append(extra)
    cases.append(
        ("inserted unknown capability", inserted, "capability catalog mismatch")
    )

    duplicate = copy.deepcopy(current)
    duplicate["capabilities"].append(copy.deepcopy(duplicate["capabilities"][0]))
    cases.append(("duplicate ID", duplicate, "duplicate capability ID"))

    vocabulary = copy.deepcopy(current)
    vocabulary["state_vocabulary"]["implementation"].append("complete")
    cases.append(("changed vocabulary", vocabulary, "vocabulary must exactly match"))

    invalid_combo = copy.deepcopy(current)
    invalid_combo["capabilities"][0]["verification"] = "not_applicable"
    cases.append(("invalid state combination", invalid_combo, "in-scope capability cannot"))

    missing_reason = copy.deepcopy(current)
    del missing_reason["capabilities"][-1]["reason"]
    cases.append(("missing reason", missing_reason, "requires a non-empty reason"))

    unsafe = copy.deepcopy(current)
    unsafe["capabilities"][0].update(
        implementation="partial",
        verification="internal_only",
        evidence=[evidence("source", "../outside.json", "0" * 64)],
    )
    cases.append(("unsafe evidence path", unsafe, "unsafe evidence path"))

    missing = copy.deepcopy(current)
    missing["capabilities"][0].update(
        implementation="partial",
        verification="internal_only",
        evidence=[evidence("source", "artifacts/missing.json", "0" * 64)],
    )
    cases.append(("missing evidence path", missing, "evidence path does not exist"))

    false_claim = copy.deepcopy(current)
    false_claim["capabilities"][0]["label"] = "supported"
    cases.append(("false supported claim", false_claim, "unsupported 'supported' claim"))

    existing = copy.deepcopy(current)
    existing["capabilities"][0].update(
        implementation="partial",
        verification="internal_only",
        evidence=[
            evidence("source", "README.md", readme_hash),
            evidence(
                "test",
                atomic_test_path,
                atomic_test_hash,
                scenarios=atomic_scenario,
            ),
        ],
    )
    cases.append(("existing typed evidence", existing, None))

    source_only = copy.deepcopy(current)
    source_only["capabilities"][0].update(
        implementation="partial",
        verification="internal_only",
        evidence=[evidence("source", "README.md", readme_hash)],
    )
    cases.append(
        (
            "internal state needs real test evidence",
            source_only,
            "requires test evidence",
        )
    )

    test_without_scenario = copy.deepcopy(current)
    test_without_scenario["capabilities"][0].update(
        implementation="partial",
        verification="internal_only",
        evidence=[evidence("test", atomic_test_path, atomic_test_hash)],
    )
    cases.append(
        (
            "test evidence needs scenario IDs",
            test_without_scenario,
            "test evidence requires scenario IDs",
        )
    )

    unknown_scenario = copy.deepcopy(current)
    unknown_scenario["capabilities"][0].update(
        implementation="partial",
        verification="internal_only",
        evidence=[
            evidence(
                "test",
                atomic_test_path,
                atomic_test_hash,
                scenarios=["UT-NOT-MANIFESTED"],
            )
        ],
    )
    cases.append(
        (
            "test scenario must be manifested",
            unknown_scenario,
            "is absent from tests/manifest.json",
        )
    )

    mismatched_scenario = copy.deepcopy(current)
    mismatched_scenario["capabilities"][0].update(
        implementation="partial",
        verification="internal_only",
        evidence=[
            evidence(
                "test",
                atomic_test_path,
                atomic_test_hash,
                scenarios=["UT-CANDIDATE-001"],
            )
        ],
    )
    cases.append(
        (
            "test scenario must map to evidence path",
            mismatched_scenario,
            "does not map to",
        )
    )

    production_as_test = copy.deepcopy(current)
    production_as_test["capabilities"][0].update(
        implementation="partial",
        verification="internal_only",
        evidence=[
            evidence(
                "test",
                atomic_path,
                atomic_hash,
                scenarios=atomic_scenario,
            )
        ],
    )
    cases.append(
        (
            "production module is not test evidence",
            production_as_test,
            "test evidence must reference a test-only Rust file",
        )
    )

    stale_test_blob = copy.deepcopy(current)
    stale_test_blob["capabilities"][0].update(
        implementation="partial",
        verification="internal_only",
        evidence=[
            evidence(
                "test",
                atomic_test_path,
                "2adec0ba244a2d46113aef6be89ab5cae57c693dc7e49672b38da3aa704fa9a1",
                scenarios=["UT-ATOMIC-007"],
                commit="ca5371454a465b0f9c314bed924bec6e193f34fc",
            )
        ],
    )
    cases.append(
        (
            "retained test blob must contain manifested function",
            stale_test_blob,
            "retained test blob omits the manifested function",
        )
    )

    readme_as_dao = copy.deepcopy(current)
    readme_as_dao["capabilities"][0].update(
        implementation="implemented",
        verification="dao_differential",
        evidence=[
            evidence("source", "README.md", readme_hash),
            evidence(
                "test",
                atomic_test_path,
                atomic_test_hash,
                scenarios=atomic_scenario,
            ),
            evidence(
                "independent_report",
                "README.md",
                readme_hash,
                scenarios=["IT-README"],
            ),
            evidence(
                "dao_bundle",
                "README.md",
                readme_hash,
                scenarios=["DAO-READ-README"],
            ),
        ],
    )
    cases.append(
        (
            "README cannot satisfy DAO evidence",
            readme_as_dao,
            "DAO bundle semantic validation is not integrated",
        )
    )

    wrong_kind = copy.deepcopy(current)
    wrong_kind["capabilities"][0].update(
        implementation="partial",
        verification="independent_check",
        evidence=[
            evidence("source", "README.md", readme_hash),
            evidence(
                "test",
                atomic_test_path,
                atomic_test_hash,
                scenarios=atomic_scenario,
            ),
        ],
    )
    cases.append(
        (
            "independent state needs independent report",
            wrong_kind,
            "requires an independent report",
        )
    )

    failures = 0
    for name, document, expected_fragment in cases:
        errors = validate_support_matrix(document, repo_root)
        passed = (
            not errors
            if expected_fragment is None
            else any(expected_fragment in error for error in errors)
        )
        if passed:
            print(f"ok - {name}")
        else:
            print(
                f"not ok - {name}: expected {expected_fragment!r}, got {errors!r}",
                file=sys.stderr,
            )
            failures += 1
    print(f"self-test: {len(cases) - failures} passed, {failures} failed")
    return 1 if failures else 0
