#!/usr/bin/env python3
"""Combine complete DAO observer receipts without dropping any file snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def identity(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", action="append", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    environment = None
    files: dict[str, dict] = {}
    sources = []
    for path in args.result:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        if document.get("status") != "pass":
            raise SystemExit(f"observer did not pass: {path}")
        if environment is None:
            environment = document["environment"]
        elif document["environment"] != environment:
            raise SystemExit(f"provider environment differs: {path}")
        for item in document["files"]:
            name = item["file"]
            if name in files:
                raise SystemExit(f"duplicate observed file: {name}")
            files[name] = item
        sources.append({"path": str(path), "identity": identity(path), "files": len(document["files"])})

    combined = {
        "document_type": "jet3_schema_candidate_complete_readback_combined",
        "status": "pass",
        "environment": environment,
        "sources": sources,
        "files": [files[name] for name in sorted(files)],
    }
    args.out.write_text(json.dumps(combined, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
