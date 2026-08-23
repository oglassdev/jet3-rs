#!/usr/bin/env python3
"""Derivation-only EXP-0042 replay for the A3 dry run (replicas 1 and 2 only).

Retained assertions (R3 `retained_exp_0042` readings):
global record page 1, [1915,2048), set_means_not_in_use, slack 92 | :func:`run_replay`
cross-check stops at leg 3 with first violating page 1021 | :func:`run_replay`
TDEF ordered stages end at no_tdef_record_candidate | :func:`run_replay`
conversion would be A3-CONVERSION-MULTIPLE but is not reached | :func:`run_replay`
T3: hash-relinked contradictory frozen set rejected on parsed values | :func:`tamper_t3`
T5: self-consistent nonterminal fail rejected by the projection | :func:`tamper_t5`
Replica 3 can be neither named nor opened | :class:`RetainedDerivationReplica`
"""

from __future__ import annotations

import copy
import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from protocol_validation import ValidationError, canonical_json_bytes
from a3_layers import CrossCheckTranscript, conversion_checkpoint, derive_tdef_candidates, polarity_cross_check
from a3_model import (
    CHECKPOINT_IDS, PAGE_SIZE, Abort, GlobalRecordModel, View, WorkCounter, candidate_page_space,
    decode_inline, global_start_candidates, qualify_global_pages, qualify_tdef_pages, terminal_suffix_slack,
)
from a3_spec import (
    BOUNDS, PLAN, PREDICATE_IDS, REASON_PREDICATES, load_bounded_json, project_predicate_results,
    validate_predicate_reporting,
)

RETAINED = PLAN.document["analyzer_dry_run_contract"]["retained_exp_0042_input"]
EXPECTED_MANIFEST_SHA = RETAINED["bundle_manifest_sha256"]
EXPECTED_RECORD = {"page": 1, "start": 1915, "end": PAGE_SIZE, "bit_polarity": "set_means_not_in_use", "slack": 92}
EXPECTED_VIOLATION = {"leg": ("L_REL_0512", "L_REL_0768"), "page": 1021, "evaluated_legs": 3}
EXPECTED_TDEF_REASON = "no_tdef_record_candidate"
EXPECTED_HIGHWATERS = {"E0": 29, "D_GROW_0128": 157, "D_REGROW_0128": 285}
LEGACY_START_COUNT = 1935
# R4-B02 / R4-C01 re-measured EXP-0042 figures.
EXPECTED_QUALIFIED = {"global_map": [0, 1, 20, 21], "tdef": [0, 1, 23, 24]}
EXPECTED_BLOB_COUNT = 81
MAX_INPUT_BLOBS = 1800


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
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
    def __init__(self, ceiling: int) -> None:
        self.ceiling = ceiling
        self.cache: dict[str, bytes] = {}

    def read(self, path: Path, digest: str) -> bytes:
        if digest in self.cache:
            return self.cache[digest]
        if len(self.cache) >= self.ceiling:
            raise Abort("A3-RESOURCE-BOUND")
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size != PAGE_SIZE:
            raise ValidationError(f"unsafe retained page blob {path}")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValidationError(f"retained page content-address mismatch {path}")
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
        return CHECKPOINT_IDS

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
        reread = next((row["row_count"] for row in rows["L_DELETE_ALL"]["dao_reread"] if row["role"] == "L"), None)
        return rows["L_REL_1280"]["table_row_counts"]["L"] != 0 and reread == 0


def load_retained_replicas(root: Path) -> tuple[tuple[RetainedDerivationReplica, RetainedDerivationReplica], BlobTracker, dict[str, dict[str, Any]]]:
    manifest_path = root / "bundle-manifest.json"
    if _sha256(manifest_path) != EXPECTED_MANIFEST_SHA:
        raise ValidationError("EXP-0042 bundle manifest differs from the A3 plan pin")
    manifest = load_bounded_json(manifest_path, BOUNDS["max_json_bytes"])
    entries = {row["path"]: row for row in manifest["files"]}
    if len(entries) != len(manifest["files"]):
        raise ValidationError("EXP-0042 manifest has duplicate paths")
    tracker = BlobTracker(BOUNDS["max_unique_page_blobs"])
    replicas = tuple(RetainedDerivationReplica(root, replica, entries, tracker) for replica in (1, 2))
    return (replicas[0], replicas[1]), tracker, entries


def legacy_plan_literal_starts(view: View, page: int) -> tuple[int, ...]:
    """The disclosed pre-A3 full-end D relation without the A3 highwater anchors."""
    names = ("E0", "D_GROW_0128", "D_DROP", "D_RECREATE_EMPTY", "D_REGROW_0128")
    records = {name: view.page(name, page) for name in names}
    survivors: list[int] = []
    for start in range(PAGE_SIZE - 5):
        capacity = (PAGE_SIZE - (start + 5)) * 8
        mask = (1 << capacity) - 1
        bits = {name: int.from_bytes(payload[start + 5:], "little") ^ mask for name, payload in records.items()}
        growth = bits["D_GROW_0128"] & ~bits["E0"]
        relation = (
            bool(growth) and not growth & bits["D_DROP"] and not growth & bits["D_RECREATE_EMPTY"]
            and not growth & ~bits["D_REGROW_0128"] and bool(bits["D_REGROW_0128"] & ~bits["D_GROW_0128"])
        )
        if relation and terminal_suffix_slack(records, start, "set_means_not_in_use") >= 16:
            survivors.append(start)
    return tuple(survivors)


def _frozen_rejects(document: dict[str, Any], model: GlobalRecordModel, tdef_reason: str) -> str | None:
    """Parsed comparison of the retained A2 frozen set against the A3 recomputation."""
    required = {"protocol_version", "document_type", "experiment_id", "plan_sha256", "campaign_id", "derivation_replicas", "qualified_pages", "layers"}
    if set(document) != required or document["document_type"] != "dao_a2_frozen_derivation_candidates" or document["derivation_replicas"] != [1, 2]:
        return "frozen set shape mismatch"
    if document["layers"]["global_map_record"]["model"] != model.document():
        return "frozen global record differs from recomputation"
    if document["layers"]["tdef_pointer_pair"]["no_outcome_reason"] != tdef_reason:
        return "frozen TDEF outcome differs from ordered recomputation"
    return None


def tamper_t3(root: Path, entries: Mapping[str, dict[str, Any]], frozen: dict[str, Any], model: GlobalRecordModel, tdef_reason: str) -> dict[str, Any]:
    """Relink every hash to a contradictory frozen set in memory, then reject it on parsed values."""
    contradictory = copy.deepcopy(frozen)
    contradictory["layers"]["global_map_record"]["model"]["bit_polarity"] = "set_means_in_use"
    payload = canonical_json_bytes(contradictory)
    digest = hashlib.sha256(payload).hexdigest()
    frozen_relative, report_relative, receipt_relative = "analysis/derivation-candidates.json", "analysis/analysis-report.json", "analysis/holdout-structure-receipt.json"
    report = load_bounded_json(_safe(root, report_relative), BOUNDS["max_json_bytes"])
    receipt = load_bounded_json(_safe(root, receipt_relative), BOUNDS["max_json_bytes"])
    manifest = load_bounded_json(root / "bundle-manifest.json", BOUNDS["max_json_bytes"])
    relinked = {"report": copy.deepcopy(report), "receipt": copy.deepcopy(receipt), "manifest": copy.deepcopy(manifest)}
    relinked["report"]["derivation_candidate_set_sha256"] = digest
    relinked["receipt"]["derivation_candidate_set_sha256"] = digest
    linked_entries = {row["path"]: row for row in relinked["manifest"]["files"]}
    linked_entries[frozen_relative].update({"sha256": digest, "size_bytes": len(payload)})
    for relative, document in ((report_relative, relinked["report"]), (receipt_relative, relinked["receipt"])):
        raw = canonical_json_bytes(document)
        linked_entries[relative].update({"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)})
    relinked["manifest"]["holdout_structure_receipt_sha256"] = linked_entries[receipt_relative]["sha256"]
    linkage_consistent = (
        relinked["report"]["derivation_candidate_set_sha256"] == digest == relinked["receipt"]["derivation_candidate_set_sha256"]
        and linked_entries[frozen_relative]["sha256"] == digest
        and relinked["manifest"]["holdout_structure_receipt_sha256"] == hashlib.sha256(canonical_json_bytes(relinked["receipt"])).hexdigest()
        and entries[frozen_relative]["sha256"] != digest
    )
    rejection = _frozen_rejects(contradictory, model, tdef_reason)
    return {
        "variant": "T3", "executed": True, "mutation": "bit_polarity flipped; report, receipt, and manifest links recomputed in memory",
        "contradictory_frozen_sha256": digest, "hash_linkage_internally_consistent": linkage_consistent,
        "retained_bundle_modified": False, "rejected": rejection is not None and linkage_consistent, "rejection": rejection,
    }


def tamper_t5(rows: list[dict[str, str]], terminal_ids: list[str], layers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Set a nonterminal predicate to fail with self-consistent terminal ids; the projection must reject."""
    target = next(row["predicate_id"] for row in rows if row["status"] == "pass" and row["predicate_id"] != "A3-HOLDOUT-PREDICTION")
    tampered = copy.deepcopy(rows)
    next(row for row in tampered if row["predicate_id"] == target)["status"] = "fail"
    consistent_terminals = sorted({*terminal_ids, target})
    try:
        validate_predicate_reporting(tampered, terminal_ids, any_decisive=False, any_holdout_failure=False)
        fail_rule_rejects = False
    except ValidationError:
        fail_rule_rejects = True
    expected_rows, expected_terminals = project_predicate_results(layers)
    projection_rejects = tampered != expected_rows or consistent_terminals != expected_terminals
    return {
        "variant": "T5", "executed": True, "mutation": f"{target} set to fail; terminal ids made self-consistent",
        "rejected_by_fail_iff_terminal_rule": fail_rule_rejects, "rejected_by_layer_projection": projection_rejects,
        "rejected": fail_rule_rejects and projection_rejects,
    }


@dataclass(frozen=True)
class ReplayResult:
    manifest_sha256: str
    blob_count: int
    model: GlobalRecordModel
    transcript: CrossCheckTranscript
    layer_outcomes: dict[str, Any]
    terminal_predicate_ids: tuple[str, ...]
    qualified_pages: dict[str, list[int]]
    legacy_start_count: int
    tamper: list[dict[str, Any]]
    checks: dict[str, bool]

    def document(self) -> dict[str, Any]:
        return {
            "bundle_manifest_sha256": self.manifest_sha256, "replicas_opened": [1, 2], "replica_3_opened": False,
            "input_page_blob_count": self.blob_count, "global_record": self.model.document(),
            "polarity_cross_check": self.transcript.document(), "layer_outcomes": self.layer_outcomes,
            "terminal_predicate_ids": list(self.terminal_predicate_ids), "qualified_pages": self.qualified_pages,
            "legacy_plan_literal_start_count": self.legacy_start_count, "tamper": self.tamper, "checks": self.checks,
        }


def run_replay(root: Path) -> ReplayResult:
    replicas, tracker, entries = load_retained_replicas(root.resolve())
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
    checks: dict[str, bool] = {}
    checks["record_1915_2048_not_in_use_slack_92"] = model.document() == {
        "record": {"page": EXPECTED_RECORD["page"], "start": EXPECTED_RECORD["start"], "end": EXPECTED_RECORD["end"]},
        "bit_polarity": EXPECTED_RECORD["bit_polarity"], "zero_suffix_slack_bytes": EXPECTED_RECORD["slack"],
    }
    legacy = tuple(legacy_plan_literal_starts(view, model.record.page) for view in views)
    checks["legacy_relation_leaves_1935_starts"] = legacy[0] == legacy[1] == tuple(range(LEGACY_START_COUNT))
    highwaters = True
    for view in views:
        for name, expected in EXPECTED_HIGHWATERS.items():
            state = decode_inline(view.page(name, 1), 1915, PAGE_SIZE, model.bit_polarity)
            highwaters &= bool(state) and state.tag == 0 and state.base == 0 and state.capacity == 1024 and view.page_count(name) == expected and all(page in state.in_use for page in range(expected)) and expected not in state.in_use
    checks["tag_base_highwaters_29_157_285"] = highwaters
    transcripts = tuple(polarity_cross_check(view, model) for view in views)
    transcript = transcripts[0]
    checks["cross_check_transcripts_identical"] = transcripts[0] == transcripts[1]
    checks["cross_check_stops_leg_3_page_1021"] = (
        len(transcript.evaluated_legs) == EXPECTED_VIOLATION["evaluated_legs"] and transcript.first_violating_leg is not None
        and (transcript.first_violating_leg.left_checkpoint_id, transcript.first_violating_leg.right_checkpoint_id) == EXPECTED_VIOLATION["leg"]
        and transcript.first_violating_page == EXPECTED_VIOLATION["page"] and transcript.representation_change_stop is None
    )
    would_be: list[str] = []
    for view in views:
        try:
            conversion_checkpoint(view, model)
            would_be.append("model")
        except Abort as exc:
            would_be.append(exc.predicate_id)
    checks["conversion_would_be_multiple_not_reached"] = would_be == ["A3-CONVERSION-MULTIPLE"] * 2
    tdef_outcomes: list[str] = []
    for index, view in enumerate(views):
        try:
            derive_tdef_candidates(view, tdef_pages[index], replicas[index].churn_precondition_met, enumerate_candidates=index == 0)
            tdef_outcomes.append("derivation_model")
        except Abort as exc:
            tdef_outcomes.append(exc.reason)
    checks["tdef_no_tdef_record_candidate"] = tdef_outcomes == [EXPECTED_TDEF_REASON] * 2
    frozen_relative = "analysis/derivation-candidates.json"
    frozen_path = _safe(root, frozen_relative)
    checks["frozen_set_hash_matches_manifest"] = _sha256(frozen_path) == entries[frozen_relative]["sha256"]
    frozen = load_bounded_json(frozen_path, BOUNDS["max_json_bytes"])
    checks["frozen_set_parsed_and_compared"] = _frozen_rejects(frozen, model, tdef_outcomes[0]) is None
    t3 = tamper_t3(root, entries, frozen, model, tdef_outcomes[0])
    tdef_terminal = REASON_PREDICATES[tdef_outcomes[0]]
    layers = {
        "global_map_record": {"status": "no_outcome", "terminal_predicate_id": None},
        "global_map_conversion_inline": {"status": "no_outcome", "terminal_predicate_id": "A3-POLARITY-CROSSCHECK"},
        "global_map_extended_base": {"status": "not_applicable", "terminal_predicate_id": None},
        "tdef_pointer_pair": {"status": "no_outcome", "terminal_predicate_id": tdef_terminal},
    }
    rows, terminal_ids = project_predicate_results(layers)
    validate_predicate_reporting(rows, terminal_ids, any_decisive=False, any_holdout_failure=False)
    checks["every_predicate_id_exactly_once"] = [row["predicate_id"] for row in rows] == list(PREDICATE_IDS)
    t5 = tamper_t5(rows, terminal_ids, layers)
    checks["t3_rejected"], checks["t5_rejected"] = t3["rejected"], t5["rejected"]
    checks["qualified_pages_0_1_20_21_and_0_1_23_24"] = {"global_map": list(global_pages[0]), "tdef": list(tdef_pages[0])} == EXPECTED_QUALIFIED
    checks["exactly_81_blobs_opened_below_1800"] = len(tracker.cache) == EXPECTED_BLOB_COUNT <= MAX_INPUT_BLOBS
    outcomes = {
        "global_map_record": {"derivation": "model", "terminal_predicate_id": None, "holdout": "not_opened"},
        "global_map_conversion_inline": {"derivation": "no_outcome", "terminal_predicate_id": "A3-POLARITY-CROSSCHECK", "conversion_attribution_if_reached": would_be[0], "reached": False},
        "global_map_extended_base": {"derivation": "not_applicable", "terminal_predicate_id": None},
        "tdef_pointer_pair": {"derivation": "no_outcome", "terminal_predicate_id": tdef_terminal, "reason": tdef_outcomes[0]},
    }
    return ReplayResult(
        EXPECTED_MANIFEST_SHA, len(tracker.cache), model, transcript, outcomes, tuple(terminal_ids),
        {"global_map": list(global_pages[0]), "tdef": list(tdef_pages[0])}, len(legacy[0]), [t3, t5], checks,
    )
