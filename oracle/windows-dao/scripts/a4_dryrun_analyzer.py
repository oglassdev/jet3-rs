#!/usr/bin/env python3
"""Production-analyzer process for one serialized A4 dry-run fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from a4_analysis import analyze
from a4_bundle import analyze_bundle, assemble_bundle, finalize_bundle
from a4_dryrun_surface import (
    CAMPAIGN_ID,
    PRODUCER_COMMIT,
    read_replica_tree,
)
from a4_analysis_state import resume_derivation
from a4_model import A4AnalysisError, require_analysis_work_within_limit
from protocol_validation import ValidationError, canonical_json_bytes


_MODEL_STAGES = {
    "h1_tdef_page": "h1_tdef",
    "h1_target_valid_layout": "h1_target_valid_layout",
    "h1_locator_pair": "h1_locator_pair",
    "h2_final_role": "h2_final_role",
    "h3_conversion": "h3_conversion",
    "h3_final_base_formula": "h3_final_base_formula",
    "h4_catalog_root": "h4_catalog_root",
    "h4_operation_record": "h4_operation_record",
    "h4_structural_field": "h4_structural_field",
    "h4_final_encoded_field": "h4_final_encoded_field",
}


def _candidate_hash_for(
    predicate_id: str, layers: Mapping[str, Any], frozen_sha256: str
) -> str:
    def visit(value: Any) -> str | None:
        if isinstance(value, Mapping):
            if value.get("terminal_predicate_id") == predicate_id:
                digest = value.get("canonical_candidates_sha256")
                if isinstance(digest, str):
                    return digest
            for child in value.values():
                found = visit(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = visit(child)
                if found is not None:
                    return found
        return None

    found = visit(layers)
    return found if found is not None else frozen_sha256


def _stage_candidates(layers: Mapping[str, Any]) -> list[dict[str, Any]]:
    found: dict[str, set[str]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            candidates = value.get("candidates")
            if isinstance(candidates, list):
                for candidate in candidates:
                    if not isinstance(candidate, Mapping):
                        continue
                    stage = _MODEL_STAGES.get(str(candidate.get("model_type")))
                    identity = candidate.get("canonical_candidate_id")
                    if stage is not None and isinstance(identity, str):
                        found.setdefault(stage, set()).add(identity)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(layers)
    return [
        {"stage": stage, "candidate_ids": sorted(found[stage])}
        for stage in _MODEL_STAGES.values()
        if stage in found
    ]


def _evaluated(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    evaluated = []
    for row in rows:
        if row["status"] == "not_applicable":
            continue
        evaluated.append(
            {
                "predicate_id": row["predicate_id"],
                "status": row["status"],
                "actual_survivor_count": row[
                    "predicate_measured_survivor_count"
                ],
            }
        )
        if row["status"] == "fail":
            break
    return evaluated


def evaluate(roots: Sequence[Path], workspace: Path) -> dict[str, Any]:
    derivation = {
        replica: read_replica_tree(roots[replica - 1], replica)
        for replica in (1, 2)
    }

    def acquire_holdout(frozen_payload: bytes, frozen_sha256: str) -> Any:
        if hashlib.sha256(frozen_payload).hexdigest() != frozen_sha256:
            raise ValueError("A4 dry-run holdout received unfrozen bytes")
        return read_replica_tree(roots[2], 3)

    try:
        direct = analyze(
            CAMPAIGN_ID,
            PRODUCER_COMMIT,
            derivation,
            acquire_holdout,
        )
    except A4AnalysisError as exc:
        empty = hashlib.sha256(b"[]").hexdigest()
        order = (
            "A4-IDLE-EQUALITY",
            "A4-SCHEMA-SNAPSHOT",
            "A4-SNAPSHOT-RECONSTRUCTION",
            "A4-RESOURCE-BOUND",
        )
        rows = [
            {
                "predicate_id": predicate,
                "status": "fail" if predicate == exc.predicate_id else "pass",
                "actual_survivor_count": exc.survivor_count if predicate == exc.predicate_id else 0,
            }
            for predicate in order[: order.index(exc.predicate_id) + 1]
        ]
        return {
            "first_failure_id": exc.predicate_id,
            "measured_terminal_count": exc.survivor_count,
            "candidate_set_sha256": empty,
            "evaluated_predicates": rows,
            "enumerated_candidate_ids_by_stage": [],
            "bundle_root": None,
        }
    report = dict(direct.report)
    frozen_raw = direct.frozen.canonical_bytes
    frozen = dict(direct.frozen.document)
    failure = next(row for row in report["predicate_results"] if row["status"] == "fail")
    bundle = workspace / "bundle"
    holdout = workspace / "holdout"
    assemble_bundle(roots[:2], bundle, CAMPAIGN_ID, PRODUCER_COMMIT)
    command = (
        sys.executable,
        str(Path(__file__).resolve()),
        "copy-holdout",
        "--source",
        str(roots[2]),
        "--destination",
        str(holdout),
    )
    bundled = analyze_bundle(
        bundle,
        holdout,
        CAMPAIGN_ID,
        PRODUCER_COMMIT,
        holdout_command=command,
    )
    if bundled["derivation_candidate_set_sha256"] != direct.frozen.sha256:
        raise ValidationError("A4 direct and bundled analyzer results disagree")
    finalize_bundle(
        bundle,
        CAMPAIGN_ID,
        PRODUCER_COMMIT,
        "2026-08-25T00:00:00Z",
        created_utc="2026-08-25T00:00:00Z",
    )
    return {
        "first_failure_id": failure["predicate_id"],
        "measured_terminal_count": failure["predicate_measured_survivor_count"],
        "candidate_set_sha256": _candidate_hash_for(
            str(failure["predicate_id"]),
            frozen["layers"],
            direct.frozen.sha256,
        ),
        "evaluated_predicates": _evaluated(report["predicate_results"]),
        "enumerated_candidate_ids_by_stage": _stage_candidates(frozen["layers"]),
        "bundle_root": str(bundle),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("evaluate")
    run.add_argument("--roots", nargs=3, required=True, type=Path)
    run.add_argument("--workspace", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    copy = subparsers.add_parser("copy-holdout")
    copy.add_argument("--source", required=True, type=Path)
    copy.add_argument("--destination", required=True, type=Path)
    frozen = subparsers.add_parser("validate-frozen")
    frozen.add_argument("--input", required=True, type=Path)
    frozen.add_argument("--output", required=True, type=Path)
    work = subparsers.add_parser("check-work")
    work.add_argument("--value", required=True, type=int)
    work.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "copy-holdout":
        shutil.copytree(args.source, args.destination)
        return 0
    if args.command == "validate-frozen":
        payload = args.input.read_bytes()
        result = "accept"
        try:
            resume_derivation(payload, hashlib.sha256(payload).hexdigest(), None)
        except (A4AnalysisError, ValidationError, TypeError, ValueError, KeyError):
            result = "reject"
        args.output.write_bytes(canonical_json_bytes({"result": result}))
        return 0
    if args.command == "check-work":
        result = "accept"
        try:
            require_analysis_work_within_limit(args.value)
        except A4AnalysisError:
            result = "reject"
        args.output.write_bytes(canonical_json_bytes({"result": result}))
        return 0
    args.workspace.mkdir()
    result = evaluate(args.roots, args.workspace)
    args.output.write_bytes(canonical_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
