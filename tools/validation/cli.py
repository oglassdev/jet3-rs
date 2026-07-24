"""CLI orchestration for support-contract validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import load_json
from .self_test import run as run_self_test
from .support import validate_support_matrix


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate docs/validation/support-matrix.json"
    )
    parser.add_argument(
        "matrix",
        nargs="?",
        type=Path,
        help="support matrix path (default: docs/validation/support-matrix.json)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="repository root used to resolve evidence paths",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run validator corruption tests instead of normal validation",
    )
    return parser.parse_args()


def main(tool_file: Path) -> int:
    args = _parse_args()
    default_root = tool_file.resolve().parent.parent
    repo_root = (args.repo_root or default_root).resolve()
    matrix_path = (
        args.matrix or repo_root / "docs/validation/support-matrix.json"
    ).resolve()
    if args.self_test:
        return run_self_test(repo_root, matrix_path)
    try:
        document = load_json(matrix_path)
    except OSError as error:
        print(f"ERROR: cannot read {matrix_path}: {error}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as error:
        print(f"ERROR: invalid JSON in {matrix_path}: {error}", file=sys.stderr)
        return 1

    errors = validate_support_matrix(document, repo_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(
            f"support-matrix validation failed with {len(errors)} error(s)",
            file=sys.stderr,
        )
        return 1
    print(
        f"support-matrix validation passed: "
        f"{len(document['capabilities'])} capabilities"
    )
    return 0
