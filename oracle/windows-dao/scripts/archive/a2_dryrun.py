#!/usr/bin/env python3
"""Generate or fail-closed verify the preregistered A2 dry-run artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a2_dryrun_retained import LEGACY_STATE, RetainedBlobBound, run_retained
from a2_dryrun_synthetic import run_synthetic
from a2_model import EXPERIMENT_DIR, PLAN, PLAN_SHA256
from a2_spec import (
    LEGACY_CONVERSION_ORDINALS,
    RUN12_CALIBRATION,
    validate_dry_run_report,
)
from protocol_validation import ValidationError, canonical_json_bytes

DEFAULT_RETAINED_ROOT = Path(
    os.environ.get(
        "A2_RETAINED_BUNDLE",
        "/private/tmp/claude-501/-Users-oglass-Development-Misc-access97-rs/"
        "77df2993-62f0-4041-97d5-19885072a109/scratchpad/run12/"
        "windows-dao-a1-bundle-947038265f6898c55b39da99340220e548836594-"
        "20260821T132025Z-a1-gh32486063559-1",
    )
)
DEFAULT_OUTPUT = EXPERIMENT_DIR / "dry-run"
RETAINED_REPORT = "a1-run12-report.json"
SYNTHETIC_REPORT = "a2-synthetic-report.json"
CASE_TRANSCRIPT = "a2-synthetic-cases.json"
CHECKSUMS = "checksums.sha256"

RETAINED_ASSERTIONS = [
    "legacy_relative_d_is_abac",
    "legacy_projection_table_exact",
    "legacy_churn_precondition_identifier_exercised",
    "retained_input_blob_bound_respected",
    "holdout_never_opened",
    "no_a2_scientific_outcome_emitted_for_a1_input",
    "page_qualification_precedes_interval_enumeration",
    "candidate_page_union_exercised",
    "run12_qualified_page_ceiling_respected",
    "run12_record_candidate_ceiling_respected",
    "run12_analysis_work_ceiling_respected",
    "prefix_sum_interval_queries_are_o1",
    "record_source_independent_of_change_envelope",
    "global_record_end_unique_with_polarity_relative_uniform_slack",
    "shorter_equivalent_record_ends_rejected",
    "transition_structural_exclusion_is_page_agnostic",
    "run12_control_bytes_not_pointer_candidates",
    "no_page_or_offset_blacklist",
    "no_a1_hand_typed_counts_imported",
    "d_record_set_relation_arithmetically_possible",
    "d_drop_does_not_assume_truncation",
    "d_alone_selects_bit_polarity",
    "global_delimitation_uses_d_only",
    "tdef_model_is_pointer_only",
    "run12_calibration_case_non_evidential",
]

def _empty_coverage() -> dict[str, Any]:
    return {
        "conversion_ordinals": [],
        "conversion_never": False,
        "slot_activation_counts": [],
        "bit_polarities": [],
        "anchor_fill_states": [],
        "run12_calibration": None,
        "record_end_uniform_slack_bytes": [],
    }


def _base_report(analyzer_commit: str, recorded_utc: str) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a2_analyzer_dry_run_report",
        "experiment_id": PLAN["experiment_id"],
        "plan_sha256": PLAN_SHA256,
        "analyzer_commit": analyzer_commit,
        "recorded_utc": recorded_utc,
        "holdout_opened": False,
        "result": "pass",
        "scientific_evidence": False,
        "acquisition_authorized": False,
        "capability_advancement_authorized": False,
    }


def _validate_metadata(analyzer_commit: str, recorded_utc: str) -> None:
    if len(analyzer_commit) != 40 or any(
        character not in "0123456789abcdef" for character in analyzer_commit
    ):
        raise ValidationError("analyzer commit must be a lowercase 40-digit Git SHA")
    try:
        parsed = datetime.fromisoformat(recorded_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("recorded UTC is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValidationError("recorded UTC must include a timezone")


def build_artifacts(
    retained_root: Path, analyzer_commit: str, recorded_utc: str
) -> dict[str, bytes]:
    _validate_metadata(analyzer_commit, recorded_utc)
    retained_breach: RetainedBlobBound | None = None
    try:
        retained = run_retained(retained_root)
    except RetainedBlobBound as exc:
        retained = None
        retained_breach = exc
    synthetic = run_synthetic(analyzer_commit)
    retained_report = {
        **_base_report(analyzer_commit, recorded_utc),
        "source_kind": "retained_a1_run12_exploratory",
        "source_identity": {
            "manifest_or_fixture_sha256": (
                retained.manifest_sha256
                if retained is not None
                else PLAN["analyzer_dry_run_contract"]["retained_a1_input"][
                    "bundle_manifest_sha256"
                ]
            ),
            "generator_sha256": None,
        },
        "checkpoint_schedule_source": "explicit_a1_legacy_projection",
        "input_page_blob_count": (
            retained.blob_count
            if retained is not None
            else retained_breach.opened_count
        ),
        "parameter_coverage": _empty_coverage(),
        "predicted_terminal_states": (
            [LEGACY_STATE] if retained is not None else ["resource_bound_breach"]
        ),
        "terminal_predicate_ids": (
            list(retained.terminal_predicate_ids)
            if retained is not None
            else ["A2-RESOURCE-BOUND"]
        ),
        "assertions": (
            RETAINED_ASSERTIONS
            if retained is not None
            else [
                "legacy_projection_table_exact",
                "holdout_never_opened",
                "no_a2_scientific_outcome_emitted_for_a1_input",
            ]
        ),
        "result": "pass" if retained is not None else "fail",
    }
    free = SYNTHETIC_FREE_PARAMETERS
    synthetic_report = {
        **_base_report(analyzer_commit, recorded_utc),
        "result": synthetic.result,
        "source_kind": "a2_schedule_synthetic",
        "source_identity": {
            "manifest_or_fixture_sha256": synthetic.transcript_sha256,
            "generator_sha256": synthetic.generator_sha256,
        },
        "checkpoint_schedule_source": "hash_pinned_a2_plan_checkpoint_design",
        "input_page_blob_count": 0,
        "parameter_coverage": {
            "conversion_ordinals": list(LEGACY_CONVERSION_ORDINALS),
            "conversion_never": True,
            "slot_activation_counts": free["slot_activation_at_conversion"],
            "bit_polarities": free["bit_polarity"],
            "anchor_fill_states": free["anchor_fill_state"],
            "run12_calibration": dict(RUN12_CALIBRATION),
            "record_end_uniform_slack_bytes": free["record_end_uniform_slack_bytes"],
        },
        "predicted_terminal_states": list(synthetic.terminal_states),
        "terminal_predicate_ids": list(synthetic.terminal_predicate_ids),
        "assertions": list(synthetic.assertions),
    }
    validate_dry_run_report(retained_report)
    validate_dry_run_report(synthetic_report)
    artifacts = {
        RETAINED_REPORT: canonical_json_bytes(retained_report),
        SYNTHETIC_REPORT: canonical_json_bytes(synthetic_report),
        CASE_TRANSCRIPT: canonical_json_bytes(synthetic.transcript),
    }
    checksum_lines = [
        f"{hashlib.sha256(payload).hexdigest()}  {name}"
        for name, payload in sorted(artifacts.items())
    ]
    artifacts[CHECKSUMS] = ("\n".join(checksum_lines) + "\n").encode("ascii")
    return artifacts


SYNTHETIC_FREE_PARAMETERS = PLAN["analyzer_dry_run_contract"]["synthetic_input"][
    "free_parameters"
]


def _write_new_or_same(
    root: Path, artifacts: dict[str, bytes], *, replace_existing: bool = False
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, payload in artifacts.items():
        path = root / name
        if path.exists():
            if not path.is_file():
                raise ValidationError(f"refusing to replace non-file artifact: {path}")
            if path.read_bytes() == payload:
                continue
            if not replace_existing:
                raise ValidationError(f"refusing to overwrite differing artifact: {path}")
        path.write_bytes(payload)


def _read_report(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read committed dry-run report: {path}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"committed dry-run report is not an object: {path}")
    return document


def verify(root: Path, retained_root: Path) -> None:
    retained_report = _read_report(root / RETAINED_REPORT)
    synthetic_report = _read_report(root / SYNTHETIC_REPORT)
    metadata = {
        (retained_report.get("analyzer_commit"), retained_report.get("recorded_utc")),
        (synthetic_report.get("analyzer_commit"), synthetic_report.get("recorded_utc")),
    }
    if len(metadata) != 1:
        raise ValidationError("committed dry-run report metadata disagrees")
    analyzer_commit, recorded_utc = metadata.pop()
    if not isinstance(analyzer_commit, str) or not isinstance(recorded_utc, str):
        raise ValidationError("committed dry-run report metadata is malformed")
    expected = build_artifacts(retained_root, analyzer_commit, recorded_utc)
    for name, payload in expected.items():
        path = root / name
        if not path.is_file() or path.read_bytes() != payload:
            raise ValidationError(f"committed dry-run artifact mismatch: {path}")


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[4],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("generate", "verify"):
        child = commands.add_parser(command)
        child.add_argument("--retained-root", type=Path, default=DEFAULT_RETAINED_ROOT)
        child.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
        if command == "generate":
            child.add_argument("--analyzer-commit", default=None)
            child.add_argument("--recorded-utc", default=None)
            child.add_argument(
                "--replace-existing",
                action="store_true",
                help="replace differing generated artifacts after recomputation",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "verify":
            verify(arguments.output, arguments.retained_root)
            print("PASS: A2 dry-run artifacts reproduce exactly")
            return 0
        analyzer_commit = arguments.analyzer_commit or _git_head()
        recorded_utc = arguments.recorded_utc or datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        artifacts = build_artifacts(arguments.retained_root, analyzer_commit, recorded_utc)
        _write_new_or_same(
            arguments.output,
            artifacts,
            replace_existing=arguments.replace_existing,
        )
    except (OSError, subprocess.CalledProcessError, ValidationError) as exc:
        print(f"A2 dry run failed: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: wrote {len(artifacts)} A2 dry-run artifacts to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
