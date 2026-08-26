#!/usr/bin/env python3
"""Run the DAO contract suite in deterministic CI lanes."""

from __future__ import annotations

import argparse
import hashlib
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TEST_ROOT = ROOT / "oracle" / "windows-dao" / "tests"
FOUR_SHARD_COUNT = 4

# These modules account for nearly all portable-suite runtime. Keeping the
# measured heavy modules explicit makes the four CI lanes predictably balanced;
# every other module is assigned by a stable hash.
FOUR_SHARD_OVERRIDES = {
    "test_a4_independent_campaign": 0,
    "test_a4_terminals": 0,
    "test_a4_frozen_projection": 0,
    "test_a4_manifest_closure": 0,
    "test_a4_predicate_major": 0,
    "test_a4_independent_h1_h2": 0,
    "test_a4_bundle": 1,
    "test_a4_independent_validator": 1,
    "test_a3_independent_validator": 1,
    "test_a3_dryrun": 1,
    "test_a4_generator": 1,
    "test_m4r1_bundle_integration": 1,
    "test_a4_analyzer": 2,
    "test_a4_independent_terminal_paths": 2,
    "test_a4_campaign_semantics": 2,
    "test_m4_bundle_integration": 2,
    "test_a4_catalog_accounting": 2,
    "test_a4_measurements": 2,
    "test_a4_independent_bundle": 3,
    "test_a4_dryrun": 3,
    "test_a4_independent_h3_h4": 3,
    "test_a3_bundle": 3,
    "test_a4_frozen_terminal": 3,
    "test_m3_contract": 3,
    "test_m5_contract": 3,
}

# PRs exercise modules whose behavior depends on Windows or PowerShell. The
# complete suite still runs on Windows after merge, while every portable module
# runs in the Linux shards before merge.
WINDOWS_PR_MODULES = frozenset(
    {
        "test_a3_powershell_contract",
        "test_a4_powershell_contract",
        "test_bounded_process_contract",
        "test_m1_dao_adapter",
        "test_m1_executor_preflight",
        "test_m1_preflight_contract",
        "test_m1_publication",
        "test_m1_runner_contract",
        "test_m3_contract",
        "test_m4_clone_contract",
        "test_m4_contract",
        "test_m4_controller_contract",
        "test_m4_phase_contract",
        "test_m4r1_powershell_contract",
        "test_m5_powershell_contract",
        "test_windows_dao_a3_workflow",
        "test_windows_dao_a4_workflow",
        "test_windows_dao_hosted_workflow",
        "test_windows_dao_ssh_contract",
    }
)


def iter_cases(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            yield item


def discover_cases() -> tuple[unittest.TestCase, ...]:
    sys.path.insert(0, str(TEST_ROOT))
    try:
        suite = unittest.defaultTestLoader.discover(
            str(TEST_ROOT), pattern="test_*.py", top_level_dir=str(TEST_ROOT)
        )
        return tuple(iter_cases(suite))
    finally:
        sys.path.pop(0)


def module_name(case: unittest.TestCase) -> str:
    return case.id().split(".", maxsplit=1)[0]


def shard_for_module(module: str, shard_count: int) -> int:
    if shard_count < 1:
        raise ValueError("shard count must be positive")
    if shard_count == FOUR_SHARD_COUNT and module in FOUR_SHARD_OVERRIDES:
        return FOUR_SHARD_OVERRIDES[module]
    digest = hashlib.sha256(module.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shard_count


def discovered_modules(cases: tuple[unittest.TestCase, ...]) -> frozenset[str]:
    return frozenset(module_name(case) for case in cases)


def validate_inventory(cases: tuple[unittest.TestCase, ...]) -> None:
    if not cases:
        raise RuntimeError("DAO contract discovery found no tests")
    modules = discovered_modules(cases)
    missing_overrides = FOUR_SHARD_OVERRIDES.keys() - modules
    missing_windows = WINDOWS_PR_MODULES - modules
    if missing_overrides:
        raise RuntimeError(
            "portable shard overrides name missing modules: "
            + ", ".join(sorted(missing_overrides))
        )
    if missing_windows:
        raise RuntimeError(
            "Windows PR inventory names missing modules: "
            + ", ".join(sorted(missing_windows))
        )


def selected_cases(
    cases: tuple[unittest.TestCase, ...],
    *,
    lane: str,
    shard_index: int | None,
    shard_count: int | None,
) -> tuple[unittest.TestCase, ...]:
    validate_inventory(cases)
    if lane == "windows-pr":
        return tuple(case for case in cases if module_name(case) in WINDOWS_PR_MODULES)
    if shard_index is None or shard_count is None:
        raise ValueError("portable lane requires a shard index and count")
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("portable shard index is outside the configured count")
    return tuple(
        case
        for case in cases
        if shard_for_module(module_name(case), shard_count) == shard_index
    )


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="lane", required=True)
    portable = subparsers.add_parser("portable-shard")
    portable.add_argument("--shard-index", type=int, required=True)
    portable.add_argument("--shard-count", type=int, default=FOUR_SHARD_COUNT)
    subparsers.add_parser("windows-pr")
    return parser


def main() -> int:
    args = argument_parser().parse_args()
    cases = discover_cases()
    selected = selected_cases(
        cases,
        lane=args.lane,
        shard_index=getattr(args, "shard_index", None),
        shard_count=getattr(args, "shard_count", None),
    )
    if not selected:
        raise RuntimeError(f"{args.lane} selected no DAO contract tests")
    modules = discovered_modules(selected)
    print(
        f"DAO contract lane {args.lane}: "
        f"{len(selected)} tests across {len(modules)} modules"
    )
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
