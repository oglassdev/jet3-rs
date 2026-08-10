#!/usr/bin/env python3
"""Compare one checked M1 snapshot pair without executing DAO."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from protocol_validation import ValidationError, load_json
from validate_m1_protocol import compare_snapshots, validate_document


def main(arguments: list[str]) -> int:
    if len(arguments) != 3:
        print(
            "usage: m1_pair_compare.py PAIR LEFT_SNAPSHOT RIGHT_SNAPSHOT",
            file=sys.stderr,
        )
        return 2
    pair_path, left_path, right_path = map(Path, arguments)
    try:
        pair = load_json(pair_path)
        left = load_json(left_path)
        right = load_json(right_path)
        if validate_document(pair) != "dao_pair":
            raise ValidationError("pair input has the wrong document type")
        if validate_document(left) != "canonical_snapshot":
            raise ValidationError("left input has the wrong document type")
        if validate_document(right) != "canonical_snapshot":
            raise ValidationError("right input has the wrong document type")
        if left["scenario_id"] != pair["left_scenario_id"]:
            raise ValidationError("left snapshot scenario differs from pair")
        if right["scenario_id"] != pair["right_scenario_id"]:
            raise ValidationError("right snapshot scenario differs from pair")
        observed = compare_snapshots(
            left, right, pair["allowed_difference_paths"]
        )
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(observed, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
