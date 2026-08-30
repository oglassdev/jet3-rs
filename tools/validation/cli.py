"""Command-line entry point for support-matrix validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import load_json
from .support import validate_support_matrix


def main(tool_file: Path) -> int:
    parser = argparse.ArgumentParser(description="Validate the support matrix")
    parser.add_argument("matrix", nargs="?", type=Path)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args()
    repo_root = (args.repo_root or tool_file.resolve().parent.parent).resolve()
    matrix_path = (
        args.matrix or repo_root / "docs/validation/support-matrix.json"
    ).resolve()
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
        return 1
    print(f"support-matrix validation passed: {len(document['capabilities'])} capabilities")
    return 0
