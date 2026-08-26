#!/usr/bin/env python3
"""Generate or byte-for-byte verify the preregistered A4 dry-run disclosure."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from itertools import islice
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from a4_dryrun_calibration import replay
from a4_dryrun_fixtures import FIXTURES, Fixture, reject_verdict_keys
from a4_dryrun_io import (
    inventory_tree,
    read_regular,
    run_bounded_child,
)
from a4_dryrun_surface import write_fixture_trees
from a4_generator import SyntheticParameters
from a4_spec import (
    BOUNDS,
    EXPERIMENT_ID,
    PLAN_SHA256,
    PAGE_SIZE,
    PREDICATE_CONTRACTS,
    REVISION_PLAN_SHA256,
    validate_schema,
)
from protocol_validation import ValidationError, canonical_json_bytes


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parent
ANALYZER = SCRIPTS / "a4_dryrun_analyzer.py"
INDEPENDENT = SCRIPTS / "a4_dryrun_independent.py"
GENERATOR = SCRIPTS / "a4_generator.py"
PROVENANCE_ENTRY_ID = "EXP-0053"
FILENAMES = (
    "a3-calibration-report.json",
    "a4-synthetic-report.json",
    "a4-reachability-transcript.json",
)
PREDICATE_IDS = tuple(
    sorted(PREDICATE_CONTRACTS, key=lambda item: PREDICATE_CONTRACTS[item]["order"])
)
PARAMETER_COVERAGE = {
    name: True
    for name in (
        "moving_row",
        "deleted_row",
        "overflow_row",
        "wrong_locator_target",
        "zero_slot",
        "nonzero_slot",
        "no_inactive_slot",
        "base_ambiguity",
        "catalog_multiplicity",
        "encoding_ambiguity",
        "replica_disagreement",
        "holdout_failure",
        "work_counter_comparator_equality",
        "resource_one_over",
        "campaign_2700_seconds",
        "campaign_2701_seconds",
    )
}
MAX_JSON_BYTES = int(BOUNDS["max_json_bytes"])
MAX_CHILD_LOG_BYTES = int(BOUNDS["max_child_log_bytes"])
MAX_TREE_ENTRIES = int(BOUNDS["max_unique_page_blobs"]) + 128
PROCESS_MARKERS = {
    ANALYZER: b"a4-dryrun-analyzer-process-v1\n",
    INDEPENDENT: b"a4-dryrun-independent-validator-process-v1\n",
}


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _recorded_utc(commit: str) -> str:
    value = _git("show", "-s", "--format=%cI", commit)
    moment = datetime.fromisoformat(value).astimezone(timezone.utc).replace(microsecond=0)
    return moment.isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inventory_hash(roots: Sequence[Path], role: str | None = None) -> str:
    rows = []
    seen_pages: set[str] = set()
    for root in roots:
        files = inventory_tree(
            root,
            maximum_entries=MAX_TREE_ENTRIES,
            maximum_bytes=int(BOUNDS["max_bundle_bytes"]),
            maximum_file_bytes=MAX_JSON_BYTES,
            page_size=PAGE_SIZE,
        )
        for item in files:
            path = item.path
            relative = item.relative
            is_page = relative.startswith("page-store/")
            if role == "page_index" and not relative.startswith("page-indexes/"):
                continue
            if role == "page_blob" and not is_page:
                continue
            if role is None or role == "page_index":
                logical = f"replica-{root.name[-2:]}/{relative}"
            else:
                digest = path.stem
                if digest in seen_pages:
                    continue
                seen_pages.add(digest)
                logical = relative
            payload = read_regular(
                path,
                PAGE_SIZE if is_page else MAX_JSON_BYTES,
                exact_size=PAGE_SIZE if is_page else None,
            )
            rows.append(
                {
                    "path": logical,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                }
            )
    rows.sort(key=lambda row: row["path"])
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def _run(command: Sequence[str], log: Path) -> dict[str, Any]:
    completed = run_bounded_child(
        command,
        cwd=ROOT,
        timeout_seconds=300,
        output_limit=MAX_CHILD_LOG_BYTES,
    )
    log.write_bytes(completed.output)
    script = Path(command[2]).resolve() if len(command) > 2 else None
    marker = PROCESS_MARKERS.get(script)
    if marker is not None and not completed.output.startswith(marker):
        raise ValidationError("A4 dry-run child process marker differs")
    if completed.returncode != 0:
        detail = completed.output[-2000:].decode("utf-8", errors="replace")
        raise ValidationError(
            f"A4 dry-run child failed ({completed.returncode}): "
            f"{' '.join(command[:3])}: {detail}"
        )
    return {
        "script": None if script is None else script.relative_to(ROOT).as_posix(),
        "script_sha256": None if script is None else _sha256(script),
        "returncode": completed.returncode,
        "log_size_bytes": len(completed.output),
        "log_sha256": hashlib.sha256(completed.output).hexdigest(),
    }


def _run_status(command: Sequence[str], log: Path) -> int:
    completed = run_bounded_child(
        command,
        cwd=ROOT,
        timeout_seconds=300,
        output_limit=MAX_CHILD_LOG_BYTES,
    )
    log.write_bytes(completed.output)
    script = Path(command[2]).resolve() if len(command) > 2 else None
    marker = PROCESS_MARKERS.get(script)
    if marker is not None and not completed.output.startswith(marker):
        raise ValidationError("A4 dry-run child process marker differs")
    return completed.returncode


def _reported_campaign_rejection(status: int, output: Path) -> bool:
    if status != 0:
        return True
    return output.exists() and _read_json(output).get("first_failure_id") in PREDICATE_IDS[:4]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(read_regular(path, MAX_JSON_BYTES))
    if not isinstance(value, dict):
        raise ValidationError(f"A4 dry-run output is not an object: {path}")
    return value


def _reject_serialized_verdict_keys(roots: Sequence[Path]) -> None:
    for root in roots:
        for item in inventory_tree(
            root,
            maximum_entries=MAX_TREE_ENTRIES,
            maximum_bytes=int(BOUNDS["max_bundle_bytes"]),
            maximum_file_bytes=MAX_JSON_BYTES,
            page_size=PAGE_SIZE,
        ):
            if item.path.suffix == ".json":
                reject_verdict_keys(_read_json(item.path))


def _fixture_entry(
    predicate_id: str, baseline_fixture_sha256: str
) -> dict[str, Any]:
    fixture = FIXTURES[predicate_id]
    return _evaluate_fixture(predicate_id, fixture, baseline_fixture_sha256)


def _evaluate_fixture(
    predicate_id: str, fixture: Fixture, baseline_fixture_sha256: str
) -> dict[str, Any]:
    reject_verdict_keys(fixture.mutation_document())
    with tempfile.TemporaryDirectory(prefix="a4-dryrun-fixture-") as temporary:
        root = Path(temporary)
        roots = write_fixture_trees(root / "roots", fixture)
        _reject_serialized_verdict_keys(roots)
        analyzer_output = root / "analyzer.json"
        analyzer_workspace = root / "analyzer-workspace"
        analyzer_receipt = _run(
            (
                sys.executable,
                "-B",
                str(ANALYZER),
                "evaluate",
                "--roots",
                *(str(path) for path in roots),
                "--workspace",
                str(analyzer_workspace),
                "--output",
                str(analyzer_output),
            ),
            root / "analyzer.log",
        )
        analyzer = _read_json(analyzer_output)
        independent_output = root / "independent.json"
        independent_command = [
            sys.executable,
            "-B",
            str(INDEPENDENT),
            "--output",
            str(independent_output),
        ]
        if PREDICATE_CONTRACTS[predicate_id]["scope"] == "campaign":
            independent_command.extend(
                ("--campaign-roots", *(str(path) for path in roots))
            )
        else:
            bundle_root = analyzer.get("bundle_root")
            if not isinstance(bundle_root, str):
                raise ValidationError("A4 scientific fixture did not retain a bundle")
            independent_command.extend(("--bundle-root", bundle_root))
        independent_receipt = _run(independent_command, root / "independent.log")
        if (
            analyzer_receipt["script"] == independent_receipt["script"]
            or analyzer_receipt["script_sha256"] == independent_receipt["script_sha256"]
            or analyzer_receipt["log_sha256"] == independent_receipt["log_sha256"]
        ):
            raise ValidationError("A4 evaluator process sources are not independent")
        independent = _read_json(independent_output)
        evaluator_keys = {
            "first_failure_id",
            "measured_terminal_count",
            "candidate_set_sha256",
        }
        analyzer_result = {key: analyzer[key] for key in evaluator_keys}
        independent_result = {key: independent[key] for key in evaluator_keys}
        if analyzer_result != independent_result:
            raise ValidationError(f"A4 evaluator disagreement for {predicate_id}")
        if analyzer_result["first_failure_id"] != predicate_id:
            raise ValidationError(f"A4 fixture first failure mismatch for {predicate_id}")
        expected_prefix = PREDICATE_IDS[: PREDICATE_IDS.index(predicate_id) + 1]
        evaluated = analyzer["evaluated_predicates"]
        if evaluated != independent["evaluated_predicates"]:
            raise ValidationError(f"A4 predicate transcript disagreement for {predicate_id}")
        if tuple(row["predicate_id"] for row in evaluated) != expected_prefix:
            raise ValidationError(f"A4 predicate prefix mismatch for {predicate_id}")
        if any(row["status"] != "pass" for row in evaluated[:-1]) or evaluated[-1]["status"] != "fail":
            raise ValidationError(f"A4 first-failure semantics mismatch for {predicate_id}")
        entry = {
            "order": PREDICATE_CONTRACTS[predicate_id]["order"],
            "predicate_id": predicate_id,
            "reachability_fixture_id": fixture.fixture_id,
            "baseline_fixture_sha256": baseline_fixture_sha256,
            "mutation_sha256": fixture.mutation_sha256(),
            "page_index_inventory_sha256": _inventory_hash(roots, "page_index"),
            "page_blob_inventory_sha256": _inventory_hash(roots, "page_blob"),
            "enumerated_candidate_ids_by_stage": analyzer[
                "enumerated_candidate_ids_by_stage"
            ],
            "evaluated_predicates": evaluated,
            "first_failure_id": predicate_id,
            "analyzer_result": analyzer_result,
            "independent_validator_result": independent_result,
            "agreement": True,
            "unreachable_assertion": None,
        }
        return entry


def _outcome(
    expected: str, analyzer_result: str, independent_result: str
) -> dict[str, Any]:
    return {
        "expected": expected,
        "analyzer_result": analyzer_result,
        "independent_validator_result": independent_result,
        "agreement": analyzer_result == independent_result,
    }


def _count_outcome(entry: Mapping[str, Any], expected_count: int) -> dict[str, Any]:
    def result(role: str) -> str:
        measured = entry[role]["measured_terminal_count"]
        return "accept" if measured == expected_count else "reject"

    return _outcome(
        "accept", result("analyzer_result"), result("independent_validator_result")
    )


def _rejection_outcome(entry: Mapping[str, Any], predicate_id: str) -> dict[str, Any]:
    def result(role: str) -> str:
        failure = entry[role]["first_failure_id"]
        return "reject" if failure == predicate_id else "accept"

    return _outcome(
        "reject", result("analyzer_result"), result("independent_validator_result")
    )


def _adversarial_outcomes(
    entries: Mapping[str, Mapping[str, Any]], baseline_fixture_sha256: str
) -> dict[str, Any]:
    extra = {
        "multiple_count_3": (
            "A4-H1-TDEF-MULTIPLE",
            Fixture(
                "A4-ADV-MULTIPLE-3",
                SyntheticParameters(decoy_tdef_pages={"T3_CREATE": 2}),
            ),
            3,
        ),
        "multiple_count_4": (
            "A4-H1-TDEF-MULTIPLE",
            Fixture(
                "A4-ADV-MULTIPLE-4",
                SyntheticParameters(decoy_tdef_pages={"T3_CREATE": 3}),
            ),
            4,
        ),
        "encoding_count_2": (
            "A4-H4-ENCODING-AMBIGUOUS",
            Fixture(
                "A4-ADV-ENCODING-2",
                SyntheticParameters(e_acute_double_occurrence=True),
            ),
            2,
        ),
    }
    extra_entries = {}
    for name, (predicate, fixture, expected_count) in extra.items():
        entry = _evaluate_fixture(predicate, fixture, baseline_fixture_sha256)
        extra_entries[name] = _count_outcome(entry, expected_count)
    rejection_outcomes = _run_adversarial_rejections(baseline_fixture_sha256)
    return {
        "multiple_count_2": _count_outcome(
            entries["A4-H1-TDEF-MULTIPLE"], 2
        ),
        "multiple_count_3": extra_entries["multiple_count_3"],
        "multiple_count_4": extra_entries["multiple_count_4"],
        "encoding_count_0": _count_outcome(
            entries["A4-H4-ENCODING-AMBIGUOUS"], 0
        ),
        "encoding_count_2": extra_entries["encoding_count_2"],
        "unregistered_candidate_id": rejection_outcomes[
            "unregistered_candidate_id"
        ],
        "malformed_page": rejection_outcomes["malformed_page"],
        "earlier_predicate_invalidated": rejection_outcomes[
            "earlier_predicate_invalidated"
        ],
        "resource_one_over": _rejection_outcome(
            entries["A4-RESOURCE-BOUND"], "A4-RESOURCE-BOUND"
        ),
        "work_counter_comparator_equality": rejection_outcomes[
            "work_counter_comparator_equality"
        ],
    }


def _child_result(command: Sequence[str], output: Path, log: Path) -> str:
    _run(command, log)
    result = _read_json(output).get("result")
    if result not in ("accept", "reject"):
        raise ValidationError("A4 adversarial child returned an invalid result")
    return str(result)


def _replace_first_candidate_id(value: Any) -> bool:
    if isinstance(value, dict):
        identity = value.get("canonical_candidate_id")
        if isinstance(identity, str):
            value["canonical_candidate_id"] = "0" * 64
            return True
        return any(_replace_first_candidate_id(child) for child in value.values())
    if isinstance(value, list):
        return any(_replace_first_candidate_id(child) for child in value)
    return False


def _run_adversarial_rejections(
    baseline_fixture_sha256: str,
) -> dict[str, dict[str, Any]]:
    earlier = Fixture(
        "A4-ADV-EARLIER-INVALIDATED",
        patches_by_replica={1: ("deleted_row", "directory_overlap")},
    )
    entry = _evaluate_fixture(
        "A4-H2-ROW-DIRECTORY-INVALID", earlier, baseline_fixture_sha256
    )
    earlier_outcome = _rejection_outcome(
        entry, "A4-H2-ROW-DIRECTORY-INVALID"
    )

    with tempfile.TemporaryDirectory(prefix="a4-dryrun-adversarial-") as temporary:
        root = Path(temporary)
        roots = write_fixture_trees(
            root / "candidate-roots", FIXTURES["A4-H2-ROLE-NONE"]
        )
        analyzer_output = root / "fixture-analyzer.json"
        _run(
            (
                sys.executable,
                "-B",
                str(ANALYZER),
                "evaluate",
                "--roots",
                *(str(path) for path in roots),
                "--workspace",
                str(root / "candidate-workspace"),
                "--output",
                str(analyzer_output),
            ),
            root / "fixture-analyzer.log",
        )
        bundle = Path(str(_read_json(analyzer_output)["bundle_root"]))
        frozen = json.loads(
            read_regular(
                bundle / "analysis/derivation-candidates.json", MAX_JSON_BYTES
            )
        )
        if not _replace_first_candidate_id(frozen):
            raise ValidationError("A4 adversary found no candidate id to mutate")
        tampered = root / "unregistered-candidate.json"
        tampered.write_bytes(canonical_json_bytes(frozen))
        results = []
        for script, flag, name in (
            (ANALYZER, "validate-frozen", "unregistered-analyzer"),
            (INDEPENDENT, "--frozen", "unregistered-independent"),
        ):
            output = root / f"{name}.json"
            command = (
                (sys.executable, "-B", str(script), flag, "--input", str(tampered), "--output", str(output))
                if script == ANALYZER
                else (sys.executable, "-B", str(script), flag, str(tampered), "--output", str(output))
            )
            results.append(_child_result(command, output, root / f"{name}.log"))
        unregistered_outcome = _outcome("reject", results[0], results[1])

        malformed_roots = write_fixture_trees(
            root / "malformed-roots", Fixture("A4-ADV-MALFORMED")
        )
        page_index = _read_json(
            sorted((malformed_roots[0] / "page-indexes/replica-01").glob("*.json"))[0]
        )
        page = malformed_roots[0] / "page-store" / (
            str(page_index["ordered_page_sha256"][0]) + ".page"
        )
        page.write_bytes(read_regular(page, PAGE_SIZE, exact_size=PAGE_SIZE)[:-1])
        malformed_analyzer = root / "malformed-analyzer.json"
        analyzer_status = _run_status(
            (
                sys.executable,
                "-B",
                str(ANALYZER),
                "evaluate",
                "--roots",
                *(str(path) for path in malformed_roots),
                "--workspace",
                str(root / "malformed-workspace"),
                "--output",
                str(malformed_analyzer),
            ),
            root / "malformed-analyzer.log",
        )
        malformed_independent = root / "malformed-independent.json"
        independent_status = _run_status(
            (
                sys.executable,
                "-B",
                str(INDEPENDENT),
                "--campaign-roots",
                *(str(path) for path in malformed_roots),
                "--output",
                str(malformed_independent),
            ),
            root / "malformed-independent.log",
        )
        malformed_outcome = _outcome(
            "reject",
            "reject"
            if _reported_campaign_rejection(analyzer_status, malformed_analyzer)
            else "accept",
            "reject"
            if _reported_campaign_rejection(independent_status, malformed_independent)
            else "accept",
        )

        work_results = []
        for script, arguments, name in (
            (ANALYZER, ("check-work", "--value", "800000000"), "work-analyzer"),
            (INDEPENDENT, ("--work-value", "800000000"), "work-independent"),
        ):
            output = root / f"{name}.json"
            work_results.append(
                _child_result(
                (sys.executable, "-B", str(script), *arguments, "--output", str(output)),
                output,
                root / f"{name}.log",
            )
            )
        return {
            "earlier_predicate_invalidated": earlier_outcome,
            "unregistered_candidate_id": unregistered_outcome,
            "malformed_page": malformed_outcome,
            "work_counter_comparator_equality": _outcome(
                "accept", work_results[0], work_results[1]
            ),
        }


def _reachability(commit: str) -> tuple[dict[str, Any], str]:
    if tuple(FIXTURES) != PREDICATE_IDS or len(FIXTURES) != 40:
        raise ValidationError("A4 dry-run fixture registry is not the plan registry")
    with tempfile.TemporaryDirectory(prefix="a4-dryrun-baseline-") as temporary:
        roots = write_fixture_trees(
            Path(temporary) / "roots", Fixture("A4-ALL-PASS-BASELINE")
        )
        baseline_sha256 = _inventory_hash(roots)
    workers = min(3, max(1, os.cpu_count() or 1))
    entries: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
        pending = {
            executor.submit(_fixture_entry, predicate, baseline_sha256): predicate
            for predicate in PREDICATE_IDS
        }
        for future in concurrent.futures.as_completed(pending):
            predicate = pending[future]
            try:
                entry = future.result()
            except Exception as exc:
                for remaining in pending:
                    remaining.cancel()
                raise ValidationError(
                    f"A4 dry-run fixture failed: {predicate}: {exc}"
                ) from exc
            entries[predicate] = entry
            print(f"A4 dry run: {predicate} reached", flush=True)
    ordered = [entries[predicate] for predicate in PREDICATE_IDS]
    document = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_reachability_transcript",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "harness_commit": commit,
        "independent_validator_commit": commit,
        "provenance_entry_id": PROVENANCE_ENTRY_ID,
        "registry_order": list(PREDICATE_IDS),
        "fixture_entries": ordered,
        "adversarial_case_outcomes": _adversarial_outcomes(
            entries, baseline_sha256
        ),
    }
    validate_schema(document, "dao_a4_reachability_transcript")
    return document, baseline_sha256


def _report(
    *,
    commit: str,
    recorded_utc: str,
    source_kind: str,
    source_sha256: str,
    generator_sha256: str | None,
    checkpoint_source: str,
    input_page_blob_count: int,
    transcript_reference: Mapping[str, Any],
    assertions: Sequence[str],
) -> dict[str, Any]:
    document = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_analyzer_dry_run_report",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "analyzer_commit": commit,
        "recorded_utc": recorded_utc,
        "source_kind": source_kind,
        "source_identity": {
            "manifest_or_fixture_sha256": source_sha256,
            "generator_sha256": generator_sha256,
        },
        "checkpoint_schedule_source": checkpoint_source,
        "input_page_blob_count": input_page_blob_count,
        "holdout_opened": False,
        "parameter_coverage": PARAMETER_COVERAGE,
        "predicted_terminal_states": list(PREDICATE_IDS),
        "terminal_predicate_ids": list(PREDICATE_IDS),
        "reachability_transcript": dict(transcript_reference),
        "result": "pass",
        "assertions": list(assertions),
        "scientific_evidence": False,
        "acquisition_authorized": False,
        "capability_advancement_authorized": False,
    }
    validate_schema(document, "dao_a4_analyzer_dry_run_report")
    return document


def build(retained_root: Path, commit: str) -> dict[str, bytes]:
    calibration = replay(retained_root)
    if calibration["result"] != "pass" or calibration["holdout_opened"]:
        raise ValidationError("A4 retained A3 calibration did not pass derivation-only")
    transcript, baseline_sha256 = _reachability(commit)
    transcript_bytes = canonical_json_bytes(transcript)
    reference = {
        "path": "dry-run/a4-reachability-transcript.json",
        "sha256": hashlib.sha256(transcript_bytes).hexdigest(),
        "size_bytes": len(transcript_bytes),
        "harness_commit": commit,
        "independent_validator_commit": commit,
        "provenance_entry_id": PROVENANCE_ENTRY_ID,
    }
    recorded = _recorded_utc(commit)
    calibration_report = _report(
        commit=commit,
        recorded_utc=recorded,
        source_kind="retained_a3_exp_0051_calibration_derivation_only",
        source_sha256=calibration["retained_manifest_sha256"],
        generator_sha256=None,
        checkpoint_source="explicit_exp_0051_checkpoint_projection",
        input_page_blob_count=calibration["page_blob_count"],
        transcript_reference=reference,
        assertions=(
            "a3_page_23_calibration_recomputed",
            "a3_replica_3_never_opened",
            "all_40_terminals_reached",
            "analyzer_validator_process_agreement",
        ),
    )
    synthetic_report = _report(
        commit=commit,
        recorded_utc=recorded,
        source_kind="a4_schedule_synthetic",
        source_sha256=baseline_sha256,
        generator_sha256=_sha256(GENERATOR),
        checkpoint_source="hash_pinned_a4_plan_checkpoint_design",
        input_page_blob_count=0,
        transcript_reference=reference,
        assertions=(
            "all_40_terminals_reached",
            "analyzer_validator_process_agreement",
            "fixture_verdict_fields_absent",
            "registered_prefix_order_enforced",
            "required_adversarial_cases_passed",
        ),
    )
    output = {
        "a3-calibration-report.json": canonical_json_bytes(calibration_report),
        "a4-synthetic-report.json": canonical_json_bytes(synthetic_report),
        "a4-reachability-transcript.json": transcript_bytes,
    }
    output["checksums.sha256"] = "".join(
        f"{hashlib.sha256(output[name]).hexdigest()}  {name}\n" for name in FILENAMES
    ).encode()
    return output


def _write_new(output: Path, artifacts: Mapping[str, bytes]) -> None:
    if output.exists():
        raise ValidationError(f"A4 dry-run output already exists: {output}")
    output.mkdir(parents=True)
    for name, payload in artifacts.items():
        (output / name).write_bytes(payload)


def _verify(artifacts: Path, generated: Mapping[str, bytes]) -> None:
    expected = set(generated)
    actual_paths = tuple(islice(artifacts.iterdir(), len(expected) + 1))
    actual = {path.name for path in actual_paths}
    if (
        len(actual_paths) != len(expected)
        or actual != expected
        or any(path.is_symlink() or not path.is_file() for path in actual_paths)
    ):
        raise ValidationError("A4 dry-run artifact inventory differs")
    for name, payload in generated.items():
        if read_regular(artifacts / name, len(payload), exact_size=len(payload)) != payload:
            raise ValidationError(f"A4 dry-run artifact differs: {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--retained-root", required=True, type=Path)
    generate.add_argument("--output", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--retained-root", required=True, type=Path)
    verify.add_argument("--artifacts", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    commit = _git("rev-parse", "HEAD")
    if args.command == "verify":
        transcript = _read_json(args.artifacts / "a4-reachability-transcript.json")
        commit = str(transcript["harness_commit"])
    artifacts = build(args.retained_root, commit)
    if args.command == "generate":
        _write_new(args.output, artifacts)
    else:
        _verify(args.artifacts, artifacts)
    print(f"A4 dry run {args.command}: PASS ({len(PREDICATE_IDS)}/{len(PREDICATE_IDS)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
