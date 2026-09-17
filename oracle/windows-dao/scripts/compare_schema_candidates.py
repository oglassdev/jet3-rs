#!/usr/bin/env python3
"""Compare paired DAO readbacks and page-preservation constraints from a manifest."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mask_dates(snapshot: dict, allowed_tables: set[str]) -> dict:
    result = copy.deepcopy(snapshot)
    tables = result.get("tables", [])
    if len(tables) == 1 and isinstance(tables[0], list):
        tables = tables[0]
    for table in tables:
        if table.get("name") not in allowed_tables:
            continue
        for prop in table.get("properties", []):
            if prop.get("name") in {"DateCreated", "LastUpdated"}:
                prop["value"] = "<normalized-mutated-object-date>"
    return result


def expand_pages(spec: list[object]) -> list[int]:
    pages: list[int] = []
    for item in spec:
        if isinstance(item, int):
            pages.append(item)
        else:
            start, end = item
            pages.extend(range(int(start), int(end) + 1))
    return sorted(set(pages))


def page(data: bytes, number: int) -> bytes:
    start = number * 2048
    value = data[start : start + 2048]
    if len(value) != 2048:
        raise ValueError(f"missing page {number}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--readback", required=True, type=Path)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    readback = json.loads(args.readback.read_text(encoding="utf-8"))
    observed = {item["file"]: item for item in readback["files"]}
    expected_files = {
        f"{kind}-{case['name']}.mdb"
        for case in manifest["cases"]
        for kind in ("candidate", "native")
    }
    if set(observed) != expected_files or len(observed) != len(readback["files"]):
        raise SystemExit("readback inventory does not exactly match manifest candidate/native files")
    cases = []
    overall = True
    for spec in manifest["cases"]:
        name = spec["name"]
        candidate_name = f"candidate-{name}.mdb"
        native_name = f"native-{name}.mdb"
        candidate_obs = {k: v for k, v in observed[candidate_name].items() if k not in {"file", "identity"}}
        native_obs = {k: v for k, v in observed[native_name].items() if k not in {"file", "identity"}}
        candidate_path = args.candidate_dir / candidate_name
        native_path = args.candidate_dir / native_name
        candidate_identity = {"size": candidate_path.stat().st_size, "sha256": sha256(candidate_path.read_bytes())}
        native_identity = {"size": native_path.stat().st_size, "sha256": sha256(native_path.read_bytes())}
        identity_equal = (
            observed[candidate_name]["identity"] == candidate_identity
            and observed[native_name]["identity"] == native_identity
        )
        candidate_obs = mask_dates(candidate_obs, set(spec.get("normalize_table_dates", [])))
        native_obs = mask_dates(native_obs, set(spec.get("normalize_table_dates", [])))
        semantic_equal = candidate_obs == native_obs
        input_bytes = Path(spec["input"]).read_bytes()
        candidate_bytes = candidate_path.read_bytes()
        native_bytes = native_path.read_bytes()
        refused_case = any(int(step.get("expected_returncode", 0)) != 0 for step in spec["steps"])
        refused_input_exact = (input_bytes == candidate_bytes) if refused_case else None
        preservation = []
        for number in expand_pages(spec.get("preserve_pages", [])):
            if (number + 1) * 2048 > len(input_bytes):
                preservation.append({"page": number, "applicable": False, "equal": True, "reason": "page absent from input"})
                continue
            before = page(input_bytes, number)
            after = page(candidate_bytes, number)
            preservation.append(
                {"page": number, "applicable": True, "equal": before == after, "before_sha256": sha256(before), "after_sha256": sha256(after)}
            )
        raw_pairs = []
        masks = spec.get("pair_masks", [])
        for number in expand_pages(spec.get("pair_raw_pages", [])):
            left = bytearray(page(candidate_bytes, number))
            right = bytearray(page(native_bytes, number))
            for item in masks:
                if int(item["page"]) == number:
                    start = int(item["offset"])
                    end = start + int(item["length"])
                    left[start:end] = b"\0" * (end - start)
                    right[start:end] = b"\0" * (end - start)
            raw_pairs.append({"page": number, "equal": left == right, "candidate_sha256": sha256(left), "native_sha256": sha256(right)})
        passed = identity_equal and semantic_equal and all(x["equal"] for x in preservation) and all(x["equal"] for x in raw_pairs)
        if refused_case:
            passed = passed and bool(refused_input_exact)
        overall &= passed
        cases.append(
            {
                "name": name,
                "passed": passed,
                "semantic_equal": semantic_equal,
                "identity_equal": identity_equal,
                "refused_input_exact": refused_input_exact,
                "preservation": preservation,
                "raw_pairs": raw_pairs,
            }
        )
    result = {"document_type": "jet3_schema_candidate_comparison", "status": "pass" if overall else "fail", "cases": cases}
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not overall:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
