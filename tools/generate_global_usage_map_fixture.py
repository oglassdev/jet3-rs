#!/usr/bin/env python3
"""Generate FIX-0005 from the SRC-0020 layout and EXP-0051 location.

Generator environment: Python 3.9 or newer, standard library only. The output
uses only explicitly assigned bytes, so it is platform, locale, and time-zone
independent. It is synthetic parser input, not a hosted A3 bundle or DAO output.
"""

from __future__ import annotations

import argparse
from pathlib import Path

PAGE_SIZE = 2_048
DATA_PAGE_TAG = 0x01
RECORD_START = 1_915
INLINE_HEADER_SIZE = 5
ZERO_SUFFIX_SLACK = 92
NOT_IN_USE_BYTE = 0xFF


def build_page() -> bytes:
    """Return one deterministic classified page-1 payload."""
    page = bytearray(PAGE_SIZE)
    page[0] = DATA_PAGE_TAG
    page[RECORD_START - 1] = 0xA5
    page[RECORD_START] = 0x00
    page[RECORD_START + 1 : RECORD_START + INLINE_HEADER_SIZE] = (0).to_bytes(
        4, "little"
    )

    bitmap = memoryview(page)[RECORD_START + INLINE_HEADER_SIZE :]
    bitmap[:] = bytes([NOT_IN_USE_BYTE]) * len(bitmap)
    bitmap[0] = 0b1111_1010
    bitmap[-ZERO_SUFFIX_SLACK - 1] = 0x7F
    return bytes(page)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("fixtures/generated/global-usage-map-page1.bin"),
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(build_page())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
