#!/usr/bin/env python3
"""Validate the machine-readable jet3-rs support contract."""

from pathlib import Path

from validation import load_json, validate_support_matrix
from validation.cli import main as _main

__all__ = ["load_json", "validate_support_matrix"]


def main() -> int:
    return _main(Path(__file__))


if __name__ == "__main__":
    raise SystemExit(main())
