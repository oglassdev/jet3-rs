"""Read-only retained-run projection for the A2 analyzer dry run."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a2_model import (
    CHECKPOINT_IDS,
    MAX_QUALIFIED_PAGES,
    PAGE_SIZE,
    PER_PAGE_CANDIDATES,
    PLAN,
    Abort,
    GlobalRecordModel,
    WorkCounter,
    derive_global_record,
    qualify_global_pages,
)
from protocol_validation import ValidationError

RETAINED = PLAN["analyzer_dry_run_contract"]["retained_a1_input"]
LEGACY_STATE = "legacy_projection_complete_with_tdef_churn_not_applicable"
LEGACY_D_CHECKPOINTS = tuple(
    row["a2_checkpoint"]
    for row in RETAINED["checkpoint_projection"]
    if row["a2_checkpoint"]
    in {"E0", "D_GROW_0128", "D_DROP", "D_RECREATE_EMPTY", "D_REGROW_0128"}
    and row["status"] == "applicable"
)


class RetainedBlobBound(Exception):
    """The next distinct content-addressed read would exceed the legacy ceiling."""

    def __init__(self, opened_count: int) -> None:
        self.opened_count = opened_count
        self.requested_unique_count = opened_count + 1
        super().__init__("retained dry run would exceed its page-blob ceiling")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_json(path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValidationError(f"unsafe retained JSON artifact: {path}")
    payload = path.read_bytes()
    if expected_sha256 is not None and hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValidationError(f"retained artifact hash mismatch: {path}")
    try:
        document = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid retained JSON artifact: {path}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"retained JSON is not an object: {path}")
    return document


class BlobTracker:
    def __init__(self) -> None:
        self.opened: set[str] = set()
        self.cache: dict[str, bytes] = {}

    def retained(self, digest: str) -> bytes | None:
        return self.cache.get(digest)

    def reserve(self, digest: str) -> None:
        if digest in self.opened:
            raise ValidationError("retained page blob cache accounting diverged")
        if len(self.opened) >= RETAINED["max_input_page_blobs"]:
            raise RetainedBlobBound(len(self.opened))
        self.opened.add(digest)

    def store(self, digest: str, payload: bytes) -> None:
        self.cache[digest] = payload


class ProjectedReplica:
    """A2-shaped view over one permitted retained derivation replica."""

    def __init__(
        self,
        root: Path,
        replica: int,
        manifest_entries: dict[str, dict[str, Any]],
        tracker: BlobTracker,
    ) -> None:
        if replica not in PLAN["replicas"]["derivation"]:
            raise ValidationError("retained dry run permits derivation replicas only")
        self.root = root
        self.replica = replica
        self.tracker = tracker
        observation_path = f"observations/replica-{replica:02d}.json"
        observation_entry = manifest_entries[observation_path]
        self.observation = _checked_json(
            root / observation_path, observation_entry["sha256"]
        )
        if self.observation["replica"] != replica:
            raise ValidationError("retained observation replica binding mismatch")
        checkpoints = {
            row["checkpoint_id"]: row for row in self.observation["checkpoints"]
        }
        self._checkpoint_ids = CHECKPOINT_IDS
        self._counts: dict[str, int] = {}
        self._hashes: dict[str, tuple[str, ...]] = {}
        self._statuses: dict[str, str] = {}
        self._allowed_digests: set[str] = set()
        for projection in RETAINED["checkpoint_projection"]:
            a2_checkpoint = projection["a2_checkpoint"]
            source_checkpoint = projection["a1_checkpoint"]
            status = projection["status"]
            self._statuses[a2_checkpoint] = status
            if source_checkpoint is None:
                if status != "not_applicable_missing_a1_checkpoint":
                    raise ValidationError("null retained projection has an unexpected status")
                continue
            if not isinstance(source_checkpoint, str):
                raise ValidationError("retained projection has no usable source checkpoint")
            if status not in {
                "applicable",
                "not_applicable_full_delete_and_churn_pointer",
            }:
                raise ValidationError("retained projection status/source mismatch")
            source = checkpoints[source_checkpoint]
            relative = source["page_index"]["path"]
            expected_prefix = f"page-indexes/replica-{replica:02d}/"
            if not relative.startswith(expected_prefix):
                raise ValidationError("retained page index escapes the permitted replica")
            entry = manifest_entries[relative]
            index = _checked_json(root / relative, entry["sha256"])
            if index["replica"] != replica or index["checkpoint_id"] != source_checkpoint:
                raise ValidationError("retained page-index binding mismatch")
            hashes = tuple(index["ordered_page_sha256"])
            if len(hashes) != index["page_count"]:
                raise ValidationError("retained page-index count mismatch")
            self._counts[a2_checkpoint] = index["page_count"]
            self._hashes[a2_checkpoint] = hashes
            self._allowed_digests.update(hashes)

    @property
    def checkpoint_ids(self) -> tuple[str, ...]:
        return self._checkpoint_ids

    @property
    def page_count(self) -> dict[str, int]:
        return self._counts

    @property
    def ordered_page_sha256(self) -> dict[str, tuple[str, ...]]:
        return self._hashes

    @property
    def projection_status(self) -> dict[str, str]:
        return self._statuses

    def page_bytes(self, digest: str) -> bytes:
        if digest not in self._allowed_digests:
            raise ValidationError("page digest is not referenced by this projected replica")
        retained = self.tracker.retained(digest)
        if retained is not None:
            return retained
        path = self.root / "page-store" / f"{digest}.page"
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size != PAGE_SIZE:
            raise ValidationError("unsafe retained page blob")
        self.tracker.reserve(digest)
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValidationError("retained page blob content-address mismatch")
        self.tracker.store(digest, payload)
        return payload


class ProjectedView:
    """Checked access to applicable rows of the explicit legacy projection."""

    def __init__(self, source: ProjectedReplica, work: WorkCounter) -> None:
        self.source = source
        self.work = work

    def page_count(self, checkpoint: str) -> int:
        try:
            return self.source.page_count[checkpoint]
        except KeyError as exc:
            raise Abort("A2-SNAPSHOT-RECONSTRUCTION") from exc

    def hash_at(self, checkpoint: str, page: int) -> str | None:
        try:
            hashes = self.source.ordered_page_sha256[checkpoint]
        except KeyError as exc:
            raise Abort("A2-SNAPSHOT-RECONSTRUCTION") from exc
        return hashes[page] if 0 <= page < len(hashes) else None

    def page(self, checkpoint: str, page: int) -> bytes:
        digest = self.hash_at(checkpoint, page)
        if digest is None:
            raise Abort("A2-SNAPSHOT-RECONSTRUCTION")
        self.work.opened(digest)
        try:
            return self.source.page_bytes(digest)
        except (OSError, ValueError, ValidationError) as exc:
            raise Abort("A2-SNAPSHOT-RECONSTRUCTION") from exc


@dataclass(frozen=True)
class RetainedResult:
    manifest_sha256: str
    blob_count: int
    work_units: int
    record_candidates: int
    qualified_pages: tuple[int, ...]
    global_model: GlobalRecordModel
    terminal_predicate_ids: tuple[str, ...]


def _manifest(root: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    path = root / "bundle-manifest.json"
    digest = _sha256(path)
    if digest != RETAINED["bundle_manifest_sha256"]:
        raise ValidationError("retained bundle manifest does not match the plan pin")
    document = _checked_json(path, digest)
    entries = {item["path"]: item for item in document["files"]}
    if len(entries) != len(document["files"]):
        raise ValidationError("retained manifest contains duplicate paths")
    return digest, entries


def _numbers(pattern: str, text: str) -> tuple[int, ...]:
    match = re.search(pattern, text)
    if match is None:
        raise ValidationError("retained assertion is not mechanically parseable")
    return tuple(int(value.replace(",", "")) for value in match.groups())


def _schedule_assertions(replicas: tuple[ProjectedReplica, ProjectedReplica]) -> None:
    first, grown, recreated, regrown = _numbers(
        r"baseline ([0-9,]+) to ([0-9,]+).*baseline ([0-9,]+) to ([0-9,]+)",
        RETAINED["schedule_arithmetic_assertion"],
    )
    batch = PLAN["tables"]["row_algorithm"]["growth_batch_rows"]
    for replica in replicas:
        rows = {row["checkpoint_id"]: row for row in replica.observation["checkpoints"]}
        d_grow = rows["D_GROW_0128"]
        d_drop = rows["D_DROP"]
        d_regrow = rows["D_REGROW_0128"]
        if (
            d_grow["target_baseline_pages"] != first
            or d_grow["actual_file_pages"] != grown
            or d_drop["actual_file_pages"] != grown
            or d_regrow["target_baseline_pages"] != recreated
            or d_regrow["actual_file_pages"] != regrown
        ):
            raise ValidationError("retained D schedule arithmetic mismatch")
        if any(
            row["inserted_rows_total"] % batch
            for row in (d_grow, d_drop, d_regrow)
        ):
            raise ValidationError("retained D schedule is not batch-derived")
        before = rows["L_REL_1280"]
        deleted = rows["L_DELETE_ALTERNATING"]
        if deleted["actual_file_pages"] - before["actual_file_pages"] != 1:
            raise ValidationError("retained alternating deletion page delta is not +1")
        if deleted["table_row_counts"]["L"] == 0:
            raise ValidationError("retained alternating deletion unexpectedly met full-delete")


def _expected_qualified_pages() -> tuple[int, ...]:
    text = RETAINED["candidate_bound_assertion"]
    match = re.search(r"pages \{([0-9,]+)\}", text)
    if match is None:
        raise ValidationError("retained qualified-page set is not parseable")
    return tuple(int(value) for value in match.group(1).split(","))


def _growth_direction_agrees(
    left: bytes, right: bytes, model: GlobalRecordModel, *, require_change: bool
) -> None:
    bitmap_start = model.record.start + 5
    pairs = zip(left[bitmap_start:], right[bitmap_start:], strict=True)
    cleared = 0
    set_bits = 0
    for before, after in pairs:
        cleared += (before & ~after & 0xFF).bit_count()
        set_bits += ((~before) & after & 0xFF).bit_count()
    if set_bits or (require_change and not cleared):
        raise ValidationError("retained L/H polarity disagrees with D-selected direction")


def run_retained(root: Path) -> RetainedResult:
    root = root.resolve()
    manifest_sha256, entries = _manifest(root)
    tracker = BlobTracker()
    replicas = tuple(
        ProjectedReplica(root, replica, entries, tracker)
        for replica in PLAN["replicas"]["derivation"]
    )
    if len(replicas) != 2:
        raise ValidationError("retained dry run requires exactly two derivation replicas")
    _schedule_assertions(replicas)
    expected_statuses = {
        row["a2_checkpoint"]: row["status"]
        for row in RETAINED["checkpoint_projection"]
    }
    if any(replica.projection_status != expected_statuses for replica in replicas):
        raise ValidationError("retained checkpoint projection status mismatch")
    if any("D_RECREATE_EMPTY" in replica.page_count for replica in replicas):
        raise ValidationError("missing A1 recreate checkpoint was materialized")
    work = WorkCounter()
    views = tuple(ProjectedView(replica, work) for replica in replicas)
    pages = range(
        max(count for replica in replicas for count in replica.page_count.values())
    )
    qualified = tuple(qualify_global_pages(view, pages) for view in views)
    expected = _expected_qualified_pages()
    if qualified != (expected, expected) or len(expected) > MAX_QUALIFIED_PAGES:
        raise ValidationError("retained global qualification mismatch")

    survivors: list[GlobalRecordModel] = []
    for page in expected:
        try:
            model = derive_global_record(
                views[0], page, d_checkpoints=LEGACY_D_CHECKPOINTS
            )
        except Abort:
            continue
        second = derive_global_record(
            views[1],
            page,
            enumerate_candidates=False,
            d_checkpoints=LEGACY_D_CHECKPOINTS,
        )
        if model != second:
            raise ValidationError("retained global record disagrees across replicas")
        survivors.append(model)
    if len(survivors) != 1:
        raise ValidationError("retained global record is not unique")
    model = survivors[0]
    last_flip, slack = _numbers(
        r"offset ([0-9,]+), ([0-9,]+) following bytes", RETAINED["record_end_assertion"]
    )
    if (
        model.bit_polarity != "set_means_not_in_use"
        or model.record.end != PAGE_SIZE
        or model.zero_suffix_slack_bytes != slack
        or PAGE_SIZE - slack - 1 != last_flip
    ):
        raise ValidationError("retained terminal global record assertion mismatch")

    for replica, view in zip(replicas, views, strict=True):
        before = view.page("L_REL_1280", model.record.page)
        deleted = view.page("L_DELETE_ALL", model.record.page)
        reinserted = view.page("L_REINSERT_SAME", model.record.page)
        suffix = range(last_flip + 1, PAGE_SIZE)
        delete_changes = [index for index in suffix if before[index] != deleted[index]]
        reinsert_changes = [index for index in suffix if deleted[index] != reinserted[index]]
        if len(delete_changes) != 1 or reinsert_changes:
            raise ValidationError("retained legacy churn suffix assertion mismatch")
        l_left = view.page("L_REL_0064", model.record.page)
        l_right = view.page("L_REL_0512", model.record.page)
        h_left = view.page("H_REL_0896", model.record.page)
        h_right = view.page("H_REL_0904", model.record.page)
        _growth_direction_agrees(l_left, l_right, model, require_change=True)
        _growth_direction_agrees(h_left, h_right, model, require_change=False)

    maximum_candidates = PLAN["bounds"]["max_record_candidates"]
    maximum_work = PLAN["bounds"]["max_analysis_work_units"]
    if (
        work.record_candidates != len(expected) * PER_PAGE_CANDIDATES
        or work.record_candidates > maximum_candidates
        or work.value > maximum_work
    ):
        raise ValidationError("retained candidate or work accounting mismatch")
    terminal_ids = tuple(RETAINED["not_applicable_predicates"])
    return RetainedResult(
        manifest_sha256,
        len(tracker.opened),
        work.value,
        work.record_candidates,
        expected,
        model,
        terminal_ids,
    )
