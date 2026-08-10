"""Command-line adapter for the standard-library DAO protocol validator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate DAO protocol documents and evidence bundles."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("schemas", help="lint all protocol schema files")
    document = subparsers.add_parser("document", help="validate one document")
    document.add_argument("path", type=Path)
    bundle = subparsers.add_parser("bundle", help="validate an evidence bundle")
    bundle.add_argument("path", type=Path)
    return parser.parse_args()


def main(
    *,
    schema_count: int,
    validate_schemas: Callable[[], None],
    validate_document_path: Callable[[Path], str],
    validate_bundle: Callable[[Path], None],
    validation_error: type[Exception],
) -> int:
    args = _parse_args()
    try:
        if args.command == "schemas":
            validate_schemas()
            print(f"PASS: {schema_count} protocol schemas")
        elif args.command == "document":
            document_type = validate_document_path(args.path)
            print(f"PASS: {args.path} ({document_type})")
        else:
            validate_bundle(args.path)
            print(f"PASS: {args.path} (immutable evidence bundle)")
    except validation_error as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0
