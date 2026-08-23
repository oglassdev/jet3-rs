#!/usr/bin/env python3
"""Command-line phase driver for the A3 analyzer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from a3_analysis import (
    MAX_JSON_BYTES, CountingReplicaSource, _load_freeze_state, freeze_analysis,
    recompute_only, resume_analysis, write_freeze_state,
)
from a3_analysis_input import BundleReplicaSource
from a3_model import Abort
from a3_spec import PLAN, load_bounded_json
from protocol_validation import ValidationError, canonical_json_bytes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--replica", action="append", type=Path)
    parser.add_argument("--candidate-output", type=Path)
    parser.add_argument("--holdout-receipt", type=Path)
    parser.add_argument("--freeze-state", type=Path)
    parser.add_argument("--holdout-artifact-path", type=Path)
    parser.add_argument("--output", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--recompute-only", action="store_true")
    mode.add_argument("--freeze-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    arguments = parser.parse_args(argv)
    root, artifacts = arguments.bundle_root, PLAN.document["artifacts"]
    default_replicas = [root / relative for relative in artifacts["replica_observations"]]
    defaults = default_replicas[:2] if arguments.recompute_only or arguments.freeze_only else default_replicas[2:]
    replicas = arguments.replica or defaults
    candidate = arguments.candidate_output or root / artifacts["frozen_candidate_set"]
    receipt = arguments.holdout_receipt or root / artifacts["holdout_structure_receipt"]
    output = arguments.output or root / artifacts["analysis_report"]
    try:
        if arguments.recompute_only:
            if len(replicas) != 2:
                raise ValidationError("recompute-only requires exactly two replica observations")
            inputs = [BundleReplicaSource(path, root).open() for path in replicas]
            result = recompute_only(inputs)
            payload = canonical_json_bytes(result)
            if arguments.output is not None:
                arguments.output.parent.mkdir(parents=True, exist_ok=True)
                with arguments.output.open("xb") as handle:
                    handle.write(payload)
            else:
                sys.stdout.buffer.write(payload)
            return 0
        if arguments.freeze_only:
            if len(replicas) != 2 or arguments.freeze_state is None or arguments.holdout_artifact_path is None:
                raise ValidationError("freeze-only requires two replicas, --freeze-state, and --holdout-artifact-path")
            if arguments.holdout_artifact_path.exists() or arguments.holdout_artifact_path.is_symlink():
                raise ValidationError("replica-3 artifact existed before freeze phase started")
            result = freeze_analysis(
                [BundleReplicaSource(path, root) for path in replicas], candidate
            )
            write_freeze_state(
                arguments.freeze_state, result, arguments.holdout_artifact_path
            )
            print(json.dumps({"candidate_output": str(candidate), "derivation_candidate_set_sha256": result.frozen_sha256}, sort_keys=True))
            return 0
        if len(replicas) != 1 or arguments.freeze_state is None:
            raise ValidationError("resume requires the holdout replica and --freeze-state")
        frozen_result, bindings, marker_opens = _load_freeze_state(
            arguments.freeze_state, candidate
        )
        counted_holdout = CountingReplicaSource(BundleReplicaSource(replicas[0], root))
        receipt_document = load_bounded_json(receipt, MAX_JSON_BYTES)
        if counted_holdout.open_count != marker_opens:
            raise ValidationError("A3 analyzer holdout-open count differs from freeze marker")
        report = resume_analysis(
            frozen_result, counted_holdout, candidate, receipt_document, bindings,
            counted_holdout.open_count,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(canonical_json_bytes(report))
    except (Abort, OSError, ValidationError) as exc:
        print(f"A3 analysis failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(output), "scientific_outcome": report["scientific_outcome"]}, sort_keys=True))
    return 0
