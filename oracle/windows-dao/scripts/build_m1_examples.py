#!/usr/bin/env python3
"""Reproducibly build or check the protocol 1.1 controlled recipe examples."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
LADDER = (1, 2047, 2048, 2049, 32767, 32768, 32769)


def _base(scenario_id: str, title: str, purpose: str, recipe: str) -> dict[str, Any]:
    return {
        "protocol_version": "1.1.0",
        "document_type": "dao_scenario",
        "scenario_id": scenario_id,
        "title": title,
        "purpose": purpose,
        "recipe": recipe,
        "capabilities": ["oracle.dao_generate.controlled_recipe"],
        "traceability": ["ORACLE-01", "TEST-01"],
        "mode": "dao_generate_fixture",
        "requirements": {
            "database_version": "dbVersion30",
            "provider_api": "DAO COM",
            "provider_bitness": "either",
        },
        "database": {
            "input_role": "none",
            "output_path": f"databases/{scenario_id}.mdb",
        },
        "steps": [
            {
                "step_id": "create",
                "action": "create_database",
                "arguments": {
                    "locale": ";LANGID=0x0409;CP=1252;COUNTRY=0",
                    "version": "dbVersion30",
                },
            }
        ],
        "expected": {"outcome": "success", "reopen_before_snapshot": True},
    }


def _close(document: dict[str, Any]) -> dict[str, Any]:
    document["steps"].append(
        {"step_id": "close", "action": "close_database", "arguments": {}}
    )
    return document


def _empty(suffix: str) -> dict[str, Any]:
    return _close(
        _base(
            f"DAO-GEN-EMPTY-REPEAT-{suffix}",
            f"Empty database repeat {suffix}",
            "Repeat the identical empty dbVersion30 creation recipe independently.",
            "repeat_empty",
        )
    )


def _binary_marker() -> dict[str, Any]:
    document = _base(
        "DAO-GEN-BINARY-MARKER-001",
        "Fixed binary marker",
        "Create one fixed-size dbBinary field and insert one deterministic marker row.",
        "binary_marker",
    )
    document["steps"].extend(
        [
            {
                "step_id": "create_table",
                "action": "create_table",
                "arguments": {
                    "name": "BinaryMarker",
                    "fields": [
                        {
                            "name": "marker",
                            "dao_type": "dbBinary",
                            "required": True,
                        }
                    ],
                    "indexes": [],
                },
            },
            {
                "step_id": "insert_marker",
                "action": "insert_row",
                "arguments": {
                    "table": "BinaryMarker",
                    "values": [
                        {
                            "field": "marker",
                            "dao_type": "dbBinary",
                            "encoding": "lowercase_hex",
                            "value": "0011223344556677",
                        }
                    ],
                },
            },
        ]
    )
    return _close(document)


def _text(indexed: bool) -> dict[str, Any]:
    suffix = "INDEXED" if indexed else "BASELINE"
    recipe = "text_index_nonunique" if indexed else "text_index_baseline"
    document = _base(
        f"DAO-GEN-TEXT8-{suffix}-001",
        f"dbText(8) {'nonunique index' if indexed else 'baseline'}",
        "Create the controlled dbText(8) table with exactly one deterministic row"
        + (" and one nonunique secondary index." if indexed else "."),
        recipe,
    )
    indexes = []
    if indexed:
        indexes.append(
            {
                "name": "ix_marker",
                "fields": ["marker"],
                "primary": False,
                "unique": False,
                "required": False,
                "ignore_nulls": False,
            }
        )
    document["steps"].extend(
        [
            {
                "step_id": "create_table",
                "action": "create_table",
                "arguments": {
                    "name": "TextMarker",
                    "fields": [
                        {
                            "name": "marker",
                            "dao_type": "dbText",
                            "size": 8,
                            "required": True,
                        }
                    ],
                    "indexes": indexes,
                },
            },
            {
                "step_id": "insert_marker",
                "action": "insert_row",
                "arguments": {
                    "table": "TextMarker",
                    "values": [
                        {
                            "field": "marker",
                            "dao_type": "dbText",
                            "encoding": "unicode_string",
                            "value": "JET3M1",
                        }
                    ],
                },
            },
        ]
    )
    return _close(document)


def _ladder(binary: bool) -> dict[str, Any]:
    dao_type = "dbLongBinary" if binary else "dbMemo"
    recipe = "long_binary_ladder" if binary else "memo_ladder"
    label = "LONGBINARY" if binary else "MEMO"
    document = _base(
        f"DAO-GEN-{label}-LADDER-001",
        f"{dao_type} boundary ladder",
        "Insert exact lengths 1, 2047, 2048, 2049, 32767, 32768, and 32769.",
        recipe,
    )
    document["steps"].append(
        {
            "step_id": "create_table",
            "action": "create_table",
            "arguments": {
                "name": "LongValue",
                "fields": [
                    {"name": "payload", "dao_type": dao_type, "required": True}
                ],
                "indexes": [],
            },
        }
    )
    for length in LADDER:
        value = {
            "field": "payload",
            "dao_type": dao_type,
            "encoding": "repeat_byte" if binary else "repeat_ascii",
            "length": length,
        }
        value["byte" if binary else "ascii_character"] = 165 if binary else "M"
        document["steps"].append(
            {
                "step_id": f"insert_{length}",
                "action": "insert_row",
                "arguments": {"table": "LongValue", "values": [value]},
            }
        )
    return _close(document)


def _pair(
    pair_id: str,
    title: str,
    purpose: str,
    kind: str,
    left: str,
    right: str,
    paths: list[str],
) -> dict[str, Any]:
    return {
        "protocol_version": "1.1.0",
        "document_type": "dao_pair",
        "pair_id": pair_id,
        "title": title,
        "purpose": purpose,
        "comparison_kind": kind,
        "left_scenario_id": left,
        "right_scenario_id": right,
        "allowed_difference_paths": paths,
    }


def _documents() -> dict[str, dict[str, Any]]:
    documents = {
        "DAO-GEN-EMPTY-REPEAT-A.scenario.json": _empty("A"),
        "DAO-GEN-EMPTY-REPEAT-B.scenario.json": _empty("B"),
        "DAO-GEN-BINARY-MARKER-001.scenario.json": _binary_marker(),
        "DAO-GEN-TEXT8-BASELINE-001.scenario.json": _text(False),
        "DAO-GEN-TEXT8-INDEXED-001.scenario.json": _text(True),
        "DAO-GEN-MEMO-LADDER-001.scenario.json": _ladder(False),
        "DAO-GEN-LONGBINARY-LADDER-001.scenario.json": _ladder(True),
    }
    documents["DAO-PAIR-EMPTY-REPEAT-001.pair.json"] = _pair(
        "DAO-PAIR-EMPTY-REPEAT-001",
        "Repeated empty creation",
        "Require independent empty snapshots to be identical except for identity fields.",
        "repeat_equivalence",
        "DAO-GEN-EMPTY-REPEAT-A",
        "DAO-GEN-EMPTY-REPEAT-B",
        ["/database_sha256", "/scenario_id"],
    )
    documents["DAO-PAIR-TEXT8-INDEX-001.pair.json"] = _pair(
        "DAO-PAIR-TEXT8-INDEX-001",
        "Single nonunique dbText(8) index",
        "Require the indexed snapshot to differ only by the declared index and identity.",
        "single_nonunique_index",
        "DAO-GEN-TEXT8-BASELINE-001",
        "DAO-GEN-TEXT8-INDEXED-001",
        ["/database_sha256", "/scenario_id", "/tables/0/indexes"],
    )
    return documents


def _bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _desired() -> dict[Path, bytes]:
    desired = {EXAMPLES / name: _bytes(value) for name, value in _documents().items()}
    inventory = {
        "protocol_version": "1.1.0",
        "document_type": "dao_example_inventory",
        "generator": "oracle/windows-dao/scripts/build_m1_examples.py",
        "files": [
            {
                "path": path.name,
                "document_type": json.loads(content)["document_type"],
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in sorted(desired.items())
        ],
    }
    desired[EXAMPLES / "m1-inventory.json"] = _bytes(inventory)
    return desired


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    desired = _desired()
    managed = set(desired)
    if args.check:
        mismatches = [
            path
            for path, content in desired.items()
            if not path.is_file() or path.read_bytes() != content
        ]
        if mismatches:
            for path in mismatches:
                print(f"out of date: {path.relative_to(ROOT.parent.parent)}")
            return 1
        return 0
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    for path, content in desired.items():
        path.write_bytes(content)
    for path in EXAMPLES.glob("*.scenario.json"):
        if path.name != "DAO-GEN-PROBE-001.scenario.json" and path not in managed:
            raise SystemExit(f"unmanaged M1 scenario: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
