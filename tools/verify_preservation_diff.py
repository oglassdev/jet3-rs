#!/usr/bin/env python3
"""Verify that file changes are confined to declared half-open byte intervals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Iterable

from validation.preservation_diff import (
    DEFAULT_PAGE_SIZE,
    AllowedInterval,
    PreservationContractError,
    PreservationError,
    verify_files,
)

CANONICAL_INTERVAL = re.compile(r"(0|[1-9][0-9]*):(0|[1-9][0-9]*)")
CANONICAL_INTEGER = re.compile(r"[1-9][0-9]*")


class StructuredArgumentParser(argparse.ArgumentParser):
    """Convert argparse failures into the verifier's structured contract error."""

    def error(self, message: str) -> None:
        raise PreservationContractError("invalid_arguments", message)


def _page_size(value: str) -> int:
    if CANONICAL_INTEGER.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            "page size must be a canonical positive decimal integer"
        )
    return int(value)


def _interval(value: str) -> AllowedInterval:
    matched = CANONICAL_INTERVAL.fullmatch(value)
    if matched is None:
        raise argparse.ArgumentTypeError(
            "interval must use canonical half-open START:END decimal syntax"
        )
    return AllowedInterval(int(matched.group(1)), int(matched.group(2)))


def _parser() -> argparse.ArgumentParser:
    parser = StructuredArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        type=_interval,
        metavar="START:END",
        help="allow changes in one canonical half-open byte interval",
    )
    parser.add_argument("--page-size", type=_page_size, default=DEFAULT_PAGE_SIZE)
    return parser


def main(arguments: Iterable[str] | None = None) -> int:
    try:
        args = _parser().parse_args(arguments)
        report = verify_files(
            args.original,
            args.output,
            allowed_intervals=args.allow,
            page_size=args.page_size,
        )
    except PreservationError as error:
        print(json.dumps(error.as_dict(), sort_keys=True), file=sys.stderr)
        return error.exit_code
    print(json.dumps(report.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
