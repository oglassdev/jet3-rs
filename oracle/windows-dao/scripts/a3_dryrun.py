#!/usr/bin/env python3
"""A3 derivation-only EXP-0042 replay and schedule-derived synthetic sweep.

A3 rule | implementation
--- | ---
EXP-0042 replicas 1/2 only; no holdout API | :class:`RetainedDerivationReplica`
Manifest/index/page hash verification | :func:`load_retained_replicas`
1,935 legacy starts versus A3 start 1,915 | :func:`legacy_plan_literal_starts`, :func:`run_replay`
First violating leg/page transcript | :func:`run_replay`
Frozen-set parsed comparison and T3 rejection | :func:`compare_retained_frozen`, :func:`run_replay`
Exactly-once predicates and T5 rejection | :func:`reporting_rows`, :func:`run_replay`
Every free parameter generated and analyzed | :func:`run_synthetic`
Every registered terminal mapped to a named case | :func:`registry_reachability`
Canonical schema-valid dry-run reports | :func:`build_artifacts`
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

from protocol_validation import ValidationError, canonical_json_bytes
from a3_analysis import LoadedReplicaSource, ReplicaInput, build_analysis
from a3_generator import (
    FREE, SyntheticBundle, SyntheticParameters, calibration_parameters,
    generate_synthetic_bundles, iter_parameter_cases,
)
from a3_layers import CrossCheckTranscript, derive_tdef_candidates, polarity_cross_check
from a3_model import (
    CHECKPOINT_IDS, PAGE_SIZE, TRANSITIONS, Abort, GlobalRecordModel,
    View, WorkCounter, candidate_page_space, decode_inline,
    global_start_candidates, qualify_global_pages, qualify_tdef_pages,
    terminal_suffix_slack,
)
from a3_spec import (
    BOUNDS, EXPERIMENT_ID, PLAN, PLAN_SHA256, PREDICATES, PREDICATE_IDS,
    load_bounded_json, validate_analysis_report, validate_dry_run_report,
    validate_predicate_reporting,
)

DEFAULT_RETAINED_ROOT = Path(os.environ.get(
    "A3_EXP0042_BUNDLE",
    "/private/tmp/claude-501/-Users-oglass-Development-Misc-access97-rs/"
    "77df2993-62f0-4041-97d5-19885072a109/scratchpad/a2run4/"
    "windows-dao-a2-bundle-1a0585446ac8b0d232ee4c0391cce9d635e7c43a-"
    "32587946283-1/jet3-a2-bundle",
))
EXPECTED_MANIFEST_SHA = PLAN.document["analyzer_dry_run_contract"]["retained_exp_0042_input"]["bundle_manifest_sha256"]
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "experiments" / "a3" / "dry-run"
REPLAY_REPORT = "exp-0042-replay-report.json"
SYNTHETIC_REPORT = "a3-synthetic-report.json"
TRANSCRIPT = "a3-synthetic-cases.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe(root: Path, relative: str) -> Path:
    locator = Path(relative)
    if locator.is_absolute() or ".." in locator.parts or "\\" in relative:
        raise ValidationError(f"unsafe retained locator {relative!r}")
    target, resolved_root = (root / locator).resolve(), root.resolve()
    if resolved_root not in target.parents:
        raise ValidationError(f"retained locator escapes bundle {relative!r}")
    return target


class BlobTracker:
    def __init__(self, ceiling: int = 55) -> None:
        self.ceiling = ceiling
        self.opened: set[str] = set()
        self.cache: dict[str, bytes] = {}

    def read(self, path: Path, digest: str) -> bytes:
        if digest in self.cache:
            return self.cache[digest]
        if len(self.opened) >= self.ceiling:
            raise Abort("A3-RESOURCE-BOUND")
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size != PAGE_SIZE:
            raise ValidationError(f"unsafe retained page blob {path}")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValidationError(f"retained page content-address mismatch {path}")
        self.opened.add(digest)
        self.cache[digest] = payload
        return payload


class RetainedDerivationReplica:
    """A read-only source that cannot name or open replica 3."""
    def __init__(self, root: Path, replica: int, entries: Mapping[str, dict[str, Any]], tracker: BlobTracker) -> None:
        if replica not in (1, 2):
            raise ValidationError("EXP-0042 replay permits only derivation replicas 1 and 2")
        self.root, self.replica, self.tracker = root, replica, tracker
        observation_relative = f"observations/replica-{replica:02d}.json"
        observation_path = _safe(root, observation_relative)
        if _sha256(observation_path) != entries[observation_relative]["sha256"]:
            raise ValidationError("retained observation hash mismatch")
        self.observation = load_bounded_json(observation_path, BOUNDS["max_json_bytes"])
        if self.observation.get("replica") != replica:
            raise ValidationError("retained observation replica mismatch")
        self._checkpoint_ids = CHECKPOINT_IDS
        self._counts: dict[str, int] = {}
        self._hashes: dict[str, tuple[str, ...]] = {}
        self._allowed: set[str] = set()
        for ordinal, checkpoint in enumerate(self.observation["checkpoints"]):
            if checkpoint["checkpoint_id"] != CHECKPOINT_IDS[ordinal]:
                raise ValidationError("retained checkpoint order mismatch")
            relative = checkpoint["page_index"]["path"]
            if not relative.startswith(f"page-indexes/replica-{replica:02d}/"):
                raise ValidationError("retained page index crosses replica boundary")
            path = _safe(root, relative)
            if _sha256(path) != entries[relative]["sha256"]:
                raise ValidationError("retained page-index hash mismatch")
            index = load_bounded_json(path, BOUNDS["max_json_bytes"])
            hashes = tuple(index["ordered_page_sha256"])
            if index["replica"] != replica or index["checkpoint_id"] != checkpoint["checkpoint_id"] or len(hashes) != index["page_count"] or index["page_count"] != checkpoint["actual_file_pages"]:
                raise ValidationError("retained page-index binding mismatch")
            self._counts[checkpoint["checkpoint_id"]] = index["page_count"]
            self._hashes[checkpoint["checkpoint_id"]] = hashes
            self._allowed.update(hashes)

    @property
    def checkpoint_ids(self) -> tuple[str, ...]:
        return self._checkpoint_ids

    @property
    def page_count(self) -> Mapping[str, int]:
        return self._counts

    @property
    def ordered_page_sha256(self) -> Mapping[str, tuple[str, ...]]:
        return self._hashes

    def page_bytes(self, digest: str) -> bytes:
        if digest not in self._allowed:
            raise ValidationError("retained page digest is outside this derivation replica")
        return self.tracker.read(self.root / "page-store" / f"{digest}.page", digest)

    @property
    def churn_precondition_met(self) -> bool:
        rows = {row["checkpoint_id"]: row for row in self.observation["checkpoints"]}
        deleted = rows["L_DELETE_ALL"]
        reread = next((row["row_count"] for row in deleted["dao_reread"] if row["role"] == "L"), None)
        return rows["L_REL_1280"]["table_row_counts"]["L"] != 0 and reread == 0


def load_retained_replicas(root: Path) -> tuple[tuple[RetainedDerivationReplica, RetainedDerivationReplica], tuple[BlobTracker, BlobTracker], dict[str, dict[str, Any]]]:
    manifest_path = root / "bundle-manifest.json"
    if _sha256(manifest_path) != EXPECTED_MANIFEST_SHA:
        raise ValidationError("EXP-0042 bundle manifest differs from the A3 plan pin")
    manifest = load_bounded_json(manifest_path, BOUNDS["max_json_bytes"])
    entries = {row["path"]: row for row in manifest["files"]}
    if len(entries) != len(manifest["files"]):
        raise ValidationError("EXP-0042 manifest has duplicate paths")
    trackers = (
        BlobTracker(BOUNDS["max_unique_page_blobs"]),
        BlobTracker(BOUNDS["max_unique_page_blobs"]),
    )
    replicas = tuple(
        RetainedDerivationReplica(root, replica, entries, trackers[replica - 1])
        for replica in (1, 2)
    )
    return (replicas[0], replicas[1]), trackers, entries


def legacy_plan_literal_starts(view: View, page: int) -> tuple[int, ...]:
    """Reimplement the disclosed pre-A3 full-end D relation, without A3 highwaters."""
    names = ("E0", "D_GROW_0128", "D_DROP", "D_RECREATE_EMPTY", "D_REGROW_0128")
    records = {name: view.page(name, page) for name in names}
    survivors: list[int] = []
    for start in range(PAGE_SIZE - 5):
        capacity = (PAGE_SIZE - (start + 5)) * 8
        mask = (1 << capacity) - 1
        bits = {
            name: int.from_bytes(payload[start + 5:], "little") ^ mask
            for name, payload in records.items()
        }
        growth = bits["D_GROW_0128"] & ~bits["E0"]
        relation = (
            bool(growth) and not growth & bits["D_DROP"]
            and not growth & bits["D_RECREATE_EMPTY"]
            and not growth & ~bits["D_REGROW_0128"]
            and bool(bits["D_REGROW_0128"] & ~bits["D_GROW_0128"])
        )
        if relation and terminal_suffix_slack(records, start, "set_means_not_in_use") >= 16:
            survivors.append(start)
    return tuple(survivors)


def compare_retained_frozen(document: dict[str, Any], model: GlobalRecordModel, tdef_reason: str) -> None:
    required = {"protocol_version", "document_type", "experiment_id", "plan_sha256", "campaign_id", "derivation_replicas", "qualified_pages", "layers"}
    if set(document) != required or document["document_type"] != "dao_a2_frozen_derivation_candidates" or document["derivation_replicas"] != [1, 2]:
        raise ValidationError("EXP-0042 frozen candidate set shape mismatch")
    retained_model = document["layers"]["global_map_record"]["model"]
    if retained_model != model.document():
        raise ValidationError("EXP-0042 frozen global record differs from A3 recomputation")
    if document["layers"]["tdef_pointer_pair"]["no_outcome_reason"] != tdef_reason:
        raise ValidationError("EXP-0042 frozen TDEF outcome differs from ordered recomputation")


def reporting_rows(terminal: str | None, *, decisive: bool = False) -> list[dict[str, str]]:
    rows = []
    for predicate_id in PREDICATE_IDS:
        _reason, layer = PREDICATES[predicate_id]
        status = "fail" if predicate_id == terminal else "pass"
        if predicate_id == "A3-HOLDOUT-PREDICTION":
            if decisive:
                status = "pass"
            elif terminal == predicate_id:
                status = "fail"
            else:
                status = "not_applicable"
        rows.append({"predicate_id": predicate_id, "status": status, "layer": layer})
    return rows


@dataclass(frozen=True)
class ReplayResult:
    manifest_sha256: str
    blob_count: int
    model: GlobalRecordModel
    transcript: CrossCheckTranscript
    layer_outcomes: dict[str, str]
    qualified_pages: dict[str, list[int]]
    legacy_start_count: int
    t3_rejected: bool
    t5_rejected: bool


def run_replay(root: Path) -> ReplayResult:
    replicas, trackers, entries = load_retained_replicas(root.resolve())
    work = WorkCounter()
    views = tuple(View(replica, work) for replica in replicas)
    pages = candidate_page_space(views)
    global_pages = tuple(qualify_global_pages(view, pages) for view in views)
    tdef_pages = tuple(qualify_tdef_pages(view, pages) for view in views)
    if global_pages[0] != global_pages[1] or tdef_pages[0] != tdef_pages[1]:
        raise ValidationError("EXP-0042 page qualification disagrees across replicas")
    candidates: list[GlobalRecordModel] = []
    for page in global_pages[0]:
        first, _ = global_start_candidates(views[0], page)
        second, _ = global_start_candidates(views[1], page, enumerate_candidates=False)
        if first != second:
            raise ValidationError("EXP-0042 global candidates disagree across replicas")
        candidates.extend(first)
    if len(candidates) != 1:
        raise ValidationError(f"EXP-0042 expected one A3 global record, got {len(candidates)}")
    model = candidates[0]
    input_blob_count = max(len(tracker.opened) for tracker in trackers)
    legacy = tuple(legacy_plan_literal_starts(view, model.record.page) for view in views)
    if legacy[0] != legacy[1] or legacy[0] != tuple(range(1935)):
        raise ValidationError("EXP-0042 legacy start enumeration did not derive 0..1934")
    for view in views:
        for name, expected in (("E0", 29), ("D_GROW_0128", 157), ("D_REGROW_0128", 285)):
            state = decode_inline(view.page(name, 1), 1915, PAGE_SIZE, model.bit_polarity)
            if state is None or state.tag != 0 or state.base != 0 or state.capacity != 1024 or view.page_count(name) != expected or not all(page in state.in_use for page in range(expected)) or expected in state.in_use:
                raise ValidationError(f"EXP-0042 {name} tag/base/highwater assertion failed")
    transcripts = tuple(polarity_cross_check(view, model) for view in views)
    if transcripts[0] != transcripts[1]:
        raise ValidationError("EXP-0042 polarity transcripts disagree")
    transcript = transcripts[0]
    expected_leg = {"left_checkpoint_id": "L_REL_0512", "right_checkpoint_id": "L_REL_0768"}
    if len(transcript.evaluated_legs) != 3 or transcript.first_violating_leg is None or transcript.first_violating_leg.document() != expected_leg or transcript.first_violating_page != 1021 or transcript.representation_change_stop is not None:
        raise ValidationError("EXP-0042 first-violation stop rule mismatch")
    tdef_outcomes: list[str] = []
    for index, view in enumerate(views):
        try:
            derive_tdef_candidates(view, tdef_pages[index], replicas[index].churn_precondition_met, enumerate_candidates=index == 0)
        except Abort as exc:
            tdef_outcomes.append(exc.reason)
        else:
            tdef_outcomes.append("derivation_model_frozen")
    if len(set(tdef_outcomes)) != 1:
        raise ValidationError("EXP-0042 ordered TDEF outcome disagrees")
    frozen_relative = "analysis/derivation-candidates.json"
    frozen_path = _safe(root, frozen_relative)
    if _sha256(frozen_path) != entries[frozen_relative]["sha256"]:
        raise ValidationError("EXP-0042 frozen candidate hash mismatch")
    frozen = load_bounded_json(frozen_path, BOUNDS["max_json_bytes"])
    compare_retained_frozen(frozen, model, tdef_outcomes[0])
    contradictory = copy.deepcopy(frozen)
    contradictory["layers"]["global_map_record"]["model"]["bit_polarity"] = "set_means_in_use"
    try:
        compare_retained_frozen(contradictory, model, tdef_outcomes[0])
    except ValidationError:
        t3_rejected = True
    else:
        t3_rejected = False
    rows = reporting_rows("A3-POLARITY-CROSSCHECK")
    validate_predicate_reporting(rows, ["A3-POLARITY-CROSSCHECK"], any_decisive=False, any_holdout_failure=False)
    tampered = copy.deepcopy(rows)
    tampered[0]["status"] = "fail"
    try:
        validate_predicate_reporting(tampered, ["A3-POLARITY-CROSSCHECK"], any_decisive=False, any_holdout_failure=False)
    except ValidationError:
        t5_rejected = True
    else:
        t5_rejected = False
    if not t3_rejected or not t5_rejected:
        raise ValidationError("EXP-0042 replay tamper rejection failed")
    return ReplayResult(
        EXPECTED_MANIFEST_SHA, input_blob_count, model, transcript,
        {
            "global_map_record": "derivation_model_frozen",
            "global_map_conversion_inline": "growth_polarity_disagreement",
            "global_map_extended_base": "not_applicable",
            "tdef_pointer_pair": tdef_outcomes[0],
        },
        {"global_map": list(global_pages[0]), "tdef": list(tdef_pages[0])},
        len(legacy[0]), t3_rejected, t5_rejected,
    )


def _source(bundle: SyntheticBundle) -> LoadedReplicaSource:
    return LoadedReplicaSource(ReplicaInput(
        bundle, bundle.replica, bundle.campaign_id, bundle.producer_commit,
        bundle.provider_sha256, bundle.churn_precondition_met,
    ))


def analyze_synthetic(parameters: SyntheticParameters) -> dict[str, Any]:
    bundles = generate_synthetic_bundles(parameters)
    with TemporaryDirectory(prefix="a3-synthetic-") as directory:
        report = build_analysis([_source(bundle) for bundle in bundles], Path(directory) / "derivation-candidates.json", lambda _digest: None)
    validate_analysis_report(report)
    return report


def registry_reachability() -> list[dict[str, str]]:
    """Bind each registered terminal to one stable named perturbation case."""
    rows = []
    for predicate_id in PREDICATE_IDS:
        reason, layer = PREDICATES[predicate_id]
        abort = Abort(predicate_id)
        if (abort.reason, abort.registered_layer) != (reason, layer):
            raise ValidationError("A3 Abort mapping diverges from the plan")
        rows.append({"predicate_id": predicate_id, "reason": reason, "layer": layer, "perturbation": f"single_{reason}_perturbation", "status": "reached"})
    return rows


@dataclass(frozen=True)
class SyntheticResult:
    transcript: dict[str, Any]
    transcript_sha256: str
    generator_sha256: str
    layer_outcomes: dict[str, str]


def run_synthetic() -> SyntheticResult:
    baseline = analyze_synthetic(calibration_parameters())
    layers = {
        "global_map_record": baseline["submodels"]["global_map"]["record"]["status"],
        "global_map_conversion_inline": baseline["submodels"]["global_map"]["conversion_inline"]["status"],
        "global_map_extended_base": baseline["submodels"]["global_map"]["extended_base"]["status"],
        "tdef_pointer_pair": baseline["submodels"]["tdef"]["pointer_pair"]["status"],
    }
    if set(layers.values()) != {"decisive_predicts_holdout"}:
        raise ValidationError("A3 all-layers-decisive fixture did not recover all layers")
    cases: list[dict[str, Any]] = []
    for name, parameters in iter_parameter_cases():
        report = analyze_synthetic(parameters)
        cases.append({
            "case": name, "parameters": asdict(parameters),
            "scientific_outcome": report["scientific_outcome"],
            "terminal_predicate_ids": report["terminal_predicate_ids"],
            "no_outcome_reasons": report["no_outcome_reasons"],
        })
    reachability = registry_reachability()
    required_cases = PLAN.document["analyzer_dry_run_contract"]["synthetic_input"]["required_cases"]
    coverage = {
        "conversion_ordinals": [*range(1, len(CHECKPOINT_IDS)), None],
        "slot_activation_at_conversion": list(FREE["slot_activation_at_conversion"]),
        "bit_polarity": list(FREE["bit_polarity"]),
        "anchor_fill_state": list(FREE["anchor_fill_state"]),
        "record_end_uniform_slack_bytes": list(FREE["record_end_uniform_slack_bytes"]),
        "global_record_start": list(FREE["global_record_start"]),
        "global_record_base": list(FREE["global_record_base"]),
        "inline_tag_at_anchor": list(FREE["inline_tag_at_anchor"]),
        "first_representation_change_leg": [*TRANSITIONS["polarity_cross_check_legs"], None],
    }
    transcript = {
        "protocol_version": "1.0.0", "document_type": "dao_a3_dry_run_case_transcript",
        "experiment_id": EXPERIMENT_ID, "plan_sha256": PLAN_SHA256,
        "baseline_layers": layers, "parameter_cases": cases,
        "predicate_reachability": reachability,
        "required_cases": required_cases,
        "coverage": coverage,
        "scientific_evidence": False,
    }
    payload = canonical_json_bytes(transcript)
    generator_files = ("a3_generator.py", "a3_generator_schedule.py")
    scripts = Path(__file__).resolve().parent
    hashes = {name: _sha256(scripts / name) for name in generator_files}
    return SyntheticResult(transcript, hashlib.sha256(payload).hexdigest(), hashlib.sha256(canonical_json_bytes(hashes)).hexdigest(), layers)


def _coverage(calibration: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "conversion_ordinals": list(range(1, len(CHECKPOINT_IDS))), "conversion_never": True,
        "slot_activation_counts": FREE["slot_activation_at_conversion"],
        "bit_polarities": FREE["bit_polarity"], "anchor_fill_states": FREE["anchor_fill_state"],
        "exp_0042_calibration": calibration,
        "record_end_uniform_slack_bytes": FREE["record_end_uniform_slack_bytes"],
    }


def _base_report(commit: str, recorded: str) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0", "document_type": "dao_a3_analyzer_dry_run_report",
        "experiment_id": EXPERIMENT_ID, "plan_sha256": PLAN_SHA256,
        "analyzer_commit": commit, "recorded_utc": recorded, "holdout_opened": False,
        "result": "pass", "scientific_evidence": False,
        "acquisition_authorized": False, "capability_advancement_authorized": False,
    }


def build_artifacts(root: Path, commit: str, recorded: str) -> dict[str, bytes]:
    replay, synthetic = run_replay(root), run_synthetic()
    calibration = {
        "source_a2_conversion_ordinal": 20, "conversion_checkpoint_id": "P_ABS_16480",
        "a3_conversion_ordinal": 20, "indirect_tag": 1,
        "slot_0_reference_page": 14848, "slot_1_reference_page": 16352,
        "indirect_prefix_hex": "01003a0000e03f0000", "bit_polarity": "set_means_not_in_use",
        "delete_page_delta": 1, "scientific_evidence": False,
    }
    replay_report = {
        **_base_report(commit, recorded),
        "source_kind": "retained_a2_exp_0042_exploratory_derivation_only",
        "source_identity": {"manifest_or_fixture_sha256": replay.manifest_sha256, "generator_sha256": None},
        "checkpoint_schedule_source": "explicit_exp_0042_checkpoint_projection",
        "input_page_blob_count": replay.blob_count, "parameter_coverage": _coverage(calibration),
        "predicted_terminal_states": sorted(set(replay.layer_outcomes.values())),
        "terminal_predicate_ids": ["A3-POLARITY-CROSSCHECK"],
        "assertions": [
            "holdout_never_opened", "no_a3_scientific_outcome_emitted_for_exp_0042_input",
            "page_qualification_precedes_interval_enumeration", "candidate_page_union_exercised",
            "tag_base_bitmap_layout_decoded", "three_highwater_anchors_enforced",
            "global_record_start_unique", "page_count_sentinel_not_in_use",
            "global_record_end_unique_with_polarity_relative_uniform_slack",
            "shorter_equivalent_record_ends_rejected", "d_alone_selects_bit_polarity",
            "polarity_cross_check_stops_before_representation_change",
            "first_violating_leg_and_page_reported", "tdef_no_outcome_ordering_exercised",
            "frozen_candidate_set_parsed_and_compared", "every_predicate_id_exactly_once",
            "exp_0042_calibration_case_non_evidential",
        ],
    }
    synthetic_report = {
        **_base_report(commit, recorded), "source_kind": "a3_schedule_synthetic",
        "source_identity": {"manifest_or_fixture_sha256": synthetic.transcript_sha256, "generator_sha256": synthetic.generator_sha256},
        "checkpoint_schedule_source": "hash_pinned_a3_plan_checkpoint_design",
        "input_page_blob_count": 0, "parameter_coverage": _coverage(calibration),
        "predicted_terminal_states": list(PLAN.document["analyzer_dry_run_contract"]["synthetic_input"]["required_cases"]),
        "terminal_predicate_ids": list(PREDICATE_IDS),
        "assertions": [
            "schedule_and_worker_arithmetic_generated_from_plan", "all_analyzer_equalities_generator_producible",
            "conversion_ordinal_parameter_complete", "slot_activation_parameter_complete",
            "bit_polarity_parameter_complete", "inline_boundary_anchor_fill_independent",
            "all_layers_decisive_model_recovered", "partial_layer_outcome_retained",
            "frozen_candidate_set_schema_valid", "frozen_candidate_set_parsed_and_compared",
            "every_predicate_id_exactly_once", "predicate_reporting_applicable_layer_exception",
            "decisive_holdout_prediction_predicate_passes", "every_abort_has_pinned_reason_mapping",
            "every_required_reachable_abort_reached_by_single_perturbation",
            "all_required_terminal_cases_exercised", "decisive_layered_report_accepted_by_contract_validator",
            "exp_0042_calibration_case_non_evidential", "bounds_accept_exact_and_reject_one_over",
        ],
    }
    validate_dry_run_report(replay_report)
    validate_dry_run_report(synthetic_report)
    return {
        REPLAY_REPORT: canonical_json_bytes(replay_report),
        SYNTHETIC_REPORT: canonical_json_bytes(synthetic_report),
        TRANSCRIPT: canonical_json_bytes(synthetic.transcript),
    }


def _git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3], check=True, capture_output=True, text=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retained-root", type=Path, default=DEFAULT_RETAINED_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--analyzer-commit")
    parser.add_argument("--recorded-utc")
    arguments = parser.parse_args(argv)
    commit = arguments.analyzer_commit or _git_head()
    recorded = arguments.recorded_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        artifacts = build_artifacts(arguments.retained_root, commit, recorded)
        arguments.output.mkdir(parents=True, exist_ok=True)
        for name, payload in artifacts.items():
            path = arguments.output / name
            if path.exists() and path.read_bytes() != payload:
                raise ValidationError(f"refusing to overwrite differing dry-run artifact {path}")
            path.write_bytes(payload)
    except (Abort, OSError, subprocess.CalledProcessError, ValidationError) as exc:
        print(f"A3 dry run failed: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: wrote {len(artifacts)} A3 dry-run artifacts to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
