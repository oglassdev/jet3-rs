#!/usr/bin/env python3
"""Plan check, evaluator, and synthetic dry run for the A9 allocation experiment.

The evaluator decodes retained page images with only the primitives recorded in
docs/PROVENANCE.md: the page grammar and usage-record shapes of SRC-0020, the
global map record of EXP-0051, the table-map locators and extended-map base of
EXP-0057, and the empty-database layout of EXP-0058. Every question answer is
derived from page bytes; nothing here claims compatibility.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PAGE_SIZE = 2048
ISSUE = 99
REPLICAS = 3
# EXP-0051: global usage record on page 1 at [1915, 2048); set bit = not in use.
GLOBAL_MAP_PAGE = 1
GLOBAL_RECORD_START = 1915
# EXP-0057: owned-map and free-map locators at table-definition offsets 35 and 39,
# one row byte followed by a three-byte little-endian page number.
OWNED_LOCATOR = 35
FREE_LOCATOR = 39
# SRC-0020 / EXP-0057: one complete type-05 page maps 16,352 pages.
TYPE05_BITS = 16352
# EXP-0058: an empty database has 20 pages and system table definitions at 2-5.
EMPTY_PAGE_COUNT = 20
SYSTEM_TDEF_PAGES = frozenset((2, 3, 4, 5))
MAX_PAGES = 65536
MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024
QUESTIONS = ("Q1", "Q2", "Q3", "Q4", "Q5")


class EvaluationError(Exception):
    """Artifact integrity or plan failure; the acquisition is rejected."""


class NoOutcome(Exception):
    """The artifact is intact but the question cannot be decided from it."""


def load_json(path: Path) -> Any:
    return json.loads(path.read_bytes().decode("utf-8"))


def canonical_bytes(document: Any) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def write_canonical(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(document))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- artifact loading -------------------------------------------------------


class Checkpoint:
    def __init__(self, entry: dict[str, Any], document: dict[str, Any]) -> None:
        self.replica = int(entry["replica"])
        self.question = str(entry["question"])
        self.name = str(entry["name"])
        self.capture = str(entry["capture"])
        self.page_count = int(entry["page_count"])
        self.pages: dict[int, bytes] = {}
        for item in document["pages"]:
            number = int(item["page"])
            image = bytes.fromhex(item["hex"])
            if len(image) != PAGE_SIZE or number in self.pages or number >= self.page_count:
                raise EvaluationError(f"{self.label}: malformed page image {number}")
            if sha256(image) != item["sha256"]:
                raise EvaluationError(f"{self.label}: page {number} digest differs")
            self.pages[number] = image
        if self.capture == "full" and sorted(self.pages) != list(range(self.page_count)):
            raise EvaluationError(f"{self.label}: full capture is incomplete")
        for required in (0, GLOBAL_MAP_PAGE):
            if required not in self.pages:
                raise EvaluationError(f"{self.label}: page {required} was not captured")

    @property
    def label(self) -> str:
        return f"r{self.replica}/{self.question}/{self.name}"

    def tag(self, page: int) -> int:
        return self.pages[page][0]

    def tagged(self, tag: int) -> list[int]:
        return sorted(page for page, image in self.pages.items() if image[0] == tag)


def load_checkpoints(manifest: dict[str, Any], root: Path) -> dict[tuple[int, str, str], Checkpoint]:
    result: dict[tuple[int, str, str], Checkpoint] = {}
    for entry in manifest["checkpoints"]:
        relative = Path(str(entry["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise EvaluationError(f"unsafe checkpoint path {entry['path']!r}")
        raw = (root / relative).read_bytes()
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise EvaluationError(f"{entry['path']}: checkpoint exceeds the size bound")
        if sha256(raw) != entry["sha256"]:
            raise EvaluationError(f"{entry['path']}: checkpoint digest differs")
        document = json.loads(raw.decode("utf-8"))
        if document.get("document_type") != "dao_allocation_a9_checkpoint":
            raise EvaluationError(f"{entry['path']}: wrong checkpoint document type")
        for field in ("replica", "question", "name", "capture", "page_count"):
            if document.get(field) != entry.get(field):
                raise EvaluationError(f"{entry['path']}: {field} differs from manifest")
        if not 1 <= int(entry["page_count"]) <= MAX_PAGES:
            raise EvaluationError(f"{entry['path']}: page count out of bounds")
        checkpoint = Checkpoint(entry, document)
        key = (checkpoint.replica, checkpoint.question, checkpoint.name)
        if key in result:
            raise EvaluationError(f"{checkpoint.label}: duplicate checkpoint")
        result[key] = checkpoint
    return result


# --- page primitives (SRC-0020, EXP-0051, EXP-0057) --------------------------


def bitmap_pages(data: bytes, base: int) -> set[int]:
    """Low-bit-first bitmap positions offset by base (SRC-0020)."""
    return {
        base + index * 8 + bit
        for index, value in enumerate(data)
        if value
        for bit in range(8)
        if value & (1 << bit)
    }


def row_bytes(page: bytes, row: int) -> bytes | None:
    """One data-page row via the SRC-0020 row directory; None when malformed."""
    if page[0] != 0x01:
        return None
    count = int.from_bytes(page[8:10], "little")
    if row >= count or 10 + 2 * count > PAGE_SIZE:
        return None
    end = PAGE_SIZE
    for ordinal in range(row + 1):
        raw = int.from_bytes(page[10 + 2 * ordinal : 12 + 2 * ordinal], "little")
        start = raw & 0x1FFF
        if start >= end or start < 10 + 2 * count:
            return None
        if ordinal == row:
            if raw & 0xC000:
                return None
            return page[start:end]
        end = start
    return None


def usage_record(record: bytes, pages: dict[int, bytes]) -> dict[str, Any]:
    """Decode a type-0 inline or type-1 indirect usage record (SRC-0020, EXP-0057)."""
    if not record:
        raise NoOutcome("empty usage record")
    if record[0] == 0x00:
        if len(record) < 5:
            raise NoOutcome("type-0 usage record is truncated")
        start = int.from_bytes(record[1:5], "little")
        return {
            "kind": "type0_inline",
            "start_page": start,
            "bitmap_bytes": len(record) - 5,
            "pages": sorted(bitmap_pages(record[5:], start)),
        }
    if record[0] == 0x01:
        if (len(record) - 1) % 4:
            raise NoOutcome("type-1 usage record is not slot aligned")
        refs = [
            int.from_bytes(record[1 + 4 * slot : 5 + 4 * slot], "little")
            for slot in range((len(record) - 1) // 4)
        ]
        mapped: set[int] = set()
        missing: list[int] = []
        for slot, ref in enumerate(refs):
            if ref == 0:
                continue
            image = pages.get(ref)
            if image is None:
                missing.append(ref)
                continue
            if image[0] != 0x05:
                raise NoOutcome(f"type-1 reference {ref} is not a type-05 page")
            mapped |= bitmap_pages(image[4:], slot * TYPE05_BITS)
        return {
            "kind": "type1_indirect",
            "slot_count": len(refs),
            "references": [ref for ref in refs if ref],
            "uncaptured_references": missing,
            "pages": sorted(mapped),
        }
    raise NoOutcome(f"unknown usage record tag 0x{record[0]:02x}")


def locator(tdef: bytes, offset: int) -> tuple[int, int]:
    return tdef[offset], int.from_bytes(tdef[offset + 1 : offset + 4], "little")


def table_maps(checkpoint: Checkpoint, tdef_page: int) -> dict[str, Any]:
    tdef = checkpoint.pages[tdef_page]
    result: dict[str, Any] = {"tdef_page": tdef_page}
    for name, offset in (("owned", OWNED_LOCATOR), ("free", FREE_LOCATOR)):
        row, page = locator(tdef, offset)
        image = checkpoint.pages.get(page)
        if image is None:
            raise NoOutcome(f"{checkpoint.label}: {name}-map page {page} was not captured")
        record = row_bytes(image, row)
        if record is None:
            raise NoOutcome(f"{checkpoint.label}: {name}-map row {page}/{row} is malformed")
        decoded = usage_record(record, checkpoint.pages)
        decoded["locator"] = {"row": row, "page": page}
        result[name] = decoded
    return result


def global_map(checkpoint: Checkpoint) -> dict[str, Any]:
    """EXP-0051 global record decoded as a SRC-0020 type-0 usage record."""
    record = checkpoint.pages[GLOBAL_MAP_PAGE][GLOBAL_RECORD_START:]
    decoded = usage_record(record, {})
    if decoded["kind"] != "type0_inline":
        raise NoOutcome(f"{checkpoint.label}: global record is not a type-0 record")
    free = set(decoded["pages"])
    start = decoded["start_page"]
    coverage = range(start, start + decoded["bitmap_bytes"] * 8)
    in_use = {page for page in coverage if page < checkpoint.page_count and page not in free}
    return {"start_page": start, "coverage_end": coverage.stop, "free": free, "in_use": in_use}


def user_tdef_pages(checkpoint: Checkpoint) -> list[int]:
    return [page for page in checkpoint.tagged(0x02) if page not in SYSTEM_TDEF_PAGES]


def differing_ranges(images: list[bytes]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for offset in range(PAGE_SIZE):
        if all(image[offset] == images[0][offset] for image in images):
            continue
        if ranges and ranges[-1][1] == offset:
            ranges[-1][1] = offset + 1
        else:
            ranges.append([offset, offset + 1])
    return ranges


def global_transitions(before: Checkpoint, after: Checkpoint) -> dict[str, list[int]]:
    prior, later = global_map(before), global_map(after)
    return {
        "became_in_use": sorted(later["in_use"] - prior["in_use"]),
        "became_free": sorted(later["free"] - prior["free"]),
    }


def page_classes(checkpoint: Checkpoint, page: int, marker: bytes) -> str:
    tag = checkpoint.tag(page)
    if tag == 0x03:
        return "index_intermediate"
    if tag == 0x04:
        return "index_leaf"
    if tag == 0x05:
        return "usage_bitmap"
    if tag == 0x02:
        return "table_definition"
    if tag == 0x01:
        return "long_value" if marker in checkpoint.pages[page] else "data"
    return f"tag_{tag:02x}"


def require_same(values: list[Any], what: str) -> Any:
    if any(canonical_bytes(value) != canonical_bytes(values[0]) for value in values):
        raise NoOutcome(f"{what}: replicas disagree")
    return values[0]


# --- questions --------------------------------------------------------------


class Evaluation:
    def __init__(self, manifest: dict[str, Any], root: Path) -> None:
        self.manifest = manifest
        self.checkpoints = load_checkpoints(manifest, root)
        self.marker = bytes.fromhex(str(manifest["memo_marker_hex"]))

    def get(self, replica: int, question: str, name: str) -> Checkpoint:
        try:
            return self.checkpoints[(replica, question, name)]
        except KeyError as error:
            raise NoOutcome(f"missing checkpoint r{replica}/{question}/{name}") from error

    def q1_empty_template(self) -> dict[str, Any]:
        replicas = [self.get(replica, "Q1", "00-empty") for replica in range(1, REPLICAS + 1)]
        page_count = require_same([item.page_count for item in replicas], "Q1 page count")
        varying = {
            str(page): ranges
            for page in range(page_count)
            if (ranges := differing_ranges([item.pages[page] for item in replicas]))
        }
        return {
            "page_count": page_count,
            "matches_exp_0058_page_count": page_count == EMPTY_PAGE_COUNT,
            "page_tags": [replicas[0].tag(page) for page in range(page_count)],
            "varying_byte_ranges": varying,
            "constant_pages": [page for page in range(page_count) if str(page) not in varying],
        }

    def q2_append(self) -> dict[str, Any]:
        def transition(before: Checkpoint, after: Checkpoint) -> dict[str, Any]:
            appended = list(range(before.page_count, after.page_count))
            return {
                "appended_pages": appended,
                "appended_first_16_hex": {
                    str(page): after.pages[page][:16].hex() for page in appended
                },
                "global_map": global_transitions(before, after),
                "header_page_changed_ranges": differing_ranges([before.pages[0], after.pages[0]]),
                "global_map_page_changed_ranges": differing_ranges(
                    [before.pages[GLOBAL_MAP_PAGE], after.pages[GLOBAL_MAP_PAGE]]
                ),
            }

        per_replica = [
            {
                "table_create": transition(
                    self.get(replica, "Q2", "00-empty"), self.get(replica, "Q2", "01-table-created")
                ),
                "data_page_append": transition(
                    self.get(replica, "Q2", "02-before-data-page"),
                    self.get(replica, "Q2", "03-after-data-page"),
                ),
            }
            for replica in range(1, REPLICAS + 1)
        ]
        return require_same(per_replica, "Q2 transitions")

    def q3_reuse(self) -> dict[str, Any]:
        per_replica = []
        for replica in range(1, REPLICAS + 1):
            populated = self.get(replica, "Q3", "02-populated")
            freed = self.get(replica, "Q3", "03-freed")
            first_tdef = require_same([user_tdef_pages(self.get(replica, "Q3", "00-first-table"))], "")
            if len(first_tdef) != 1:
                raise NoOutcome(f"r{replica}/Q3: first table definition is ambiguous")
            freed_pages = set(global_transitions(populated, freed)["became_free"])
            steps = []
            previous = freed
            for step in range(1, 5):
                current = self.get(replica, "Q3", f"04-reinsert-{step}")
                used = global_transitions(previous, current)["became_in_use"]
                appended = list(range(previous.page_count, current.page_count))
                steps.append(
                    {
                        "step": step,
                        "newly_in_use": used,
                        "reused_freed_pages": [page for page in used if page in freed_pages],
                        "appended_pages": appended,
                    }
                )
                previous = current
            reused = any(step["reused_freed_pages"] for step in steps)
            appended = any(step["appended_pages"] for step in steps)
            per_replica.append(
                {
                    "freed_pages": sorted(freed_pages),
                    "page_count_after_free": freed.page_count,
                    "owned_map_after_free": table_maps(freed, first_tdef[0])["owned"]["pages"],
                    "free_map_after_free": table_maps(freed, first_tdef[0])["free"]["pages"],
                    "reinsert_steps": steps,
                    "verdict": {
                        (True, False): "reuse",
                        (False, True): "append",
                        (True, True): "mixed",
                        (False, False): "none",
                    }[(reused, appended)],
                }
            )
        verdict = require_same([item["verdict"] for item in per_replica], "Q3 verdict")
        return {"verdict": verdict, "replicas": per_replica}

    def q4_table_map_extension(self) -> dict[str, Any]:
        names = (
            "00-created",
            "01-before-first-type05",
            "02-after-first-type05",
            "03-before-second-type05",
            "04-after-second-type05",
        )
        per_replica = []
        for replica in range(1, REPLICAS + 1):
            states = {}
            for name in names:
                checkpoint = self.get(replica, "Q4", name)
                tdefs = user_tdef_pages(checkpoint)
                if len(tdefs) != 1:
                    raise NoOutcome(f"{checkpoint.label}: user table definition is ambiguous")
                maps = table_maps(checkpoint, tdefs[0])
                states[name] = {
                    "page_count": checkpoint.page_count,
                    "tdef_page": tdefs[0],
                    "type05_pages": checkpoint.tagged(0x05),
                    "owned": {key: value for key, value in maps["owned"].items() if key != "pages"},
                    "owned_page_count": len(maps["owned"]["pages"]),
                    "free": {key: value for key, value in maps["free"].items() if key != "pages"},
                }
            per_replica.append(states)
        shape = require_same(
            [
                {name: (state["owned"]["kind"], len(state["type05_pages"])) for name, state in states.items()}
                for states in per_replica
            ],
            "Q4 map kinds",
        )
        return {"map_kind_and_type05_count": shape, "replicas": per_replica}

    def q5_ownership(self) -> dict[str, Any]:
        per_replica = []
        for replica in range(1, REPLICAS + 1):
            created = self.get(replica, "Q5", "00-created")
            populated = self.get(replica, "Q5", "01-populated")
            tdefs = user_tdef_pages(populated)
            if len(tdefs) != 1:
                raise NoOutcome(f"{populated.label}: user table definition is ambiguous")
            maps = table_maps(populated, tdefs[0])
            owned, free = set(maps["owned"]["pages"]), set(maps["free"]["pages"])
            in_use = global_map(populated)["in_use"]
            new_pages = sorted(
                (in_use - global_map(created)["in_use"])
                | set(range(created.page_count, populated.page_count))
            )
            pages = [
                {
                    "page": page,
                    "class": page_classes(populated, page, self.marker),
                    "in_table_owned_map": page in owned,
                    "in_table_free_map": page in free,
                    "in_global_map_in_use": page in in_use,
                }
                for page in new_pages
            ]
            summary: dict[str, dict[str, str]] = {}
            for cls in sorted({item["class"] for item in pages}):
                members = [item for item in pages if item["class"] == cls]
                summary[cls] = {
                    key: {0: "none", len(members): "all"}.get(sum(item[key] for item in members), "some")
                    for key in ("in_table_owned_map", "in_table_free_map", "in_global_map_in_use")
                }
            per_replica.append({"tdef_page": tdefs[0], "pages": pages, "summary": summary})
        summary = require_same([item["summary"] for item in per_replica], "Q5 ownership")
        return {"summary": summary, "replicas": per_replica}


def evaluate(manifest_path: Path, root: Path, output: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    if manifest.get("document_type") != "dao_allocation_a9_manifest" or manifest.get("issue") != ISSUE:
        raise EvaluationError("manifest has the wrong document type or issue")
    if manifest.get("replica_count") != REPLICAS:
        raise EvaluationError("manifest does not declare three replicas")
    report: dict[str, Any] = {
        "document_type": "dao_allocation_a9_report",
        "issue": ISSUE,
        "compatibility_claim": False,
        "source_revision": manifest.get("source_revision"),
        "provider": manifest.get("provider"),
        "generator_status": manifest.get("status"),
        "checkpoint_count": len(manifest.get("checkpoints", [])),
        "questions": {},
    }
    evaluation = Evaluation(manifest, root)
    answers = {
        "Q1": evaluation.q1_empty_template,
        "Q2": evaluation.q2_append,
        "Q3": evaluation.q3_reuse,
        "Q4": evaluation.q4_table_map_extension,
        "Q5": evaluation.q5_ownership,
    }
    for question in QUESTIONS:
        try:
            report["questions"][question] = {"status": "answered", "answer": answers[question]()}
        except NoOutcome as reason:
            report["questions"][question] = {"status": "no_outcome", "reason": str(reason)}
    all_answered = all(item["status"] == "answered" for item in report["questions"].values())
    report["status"] = "accepted" if all_answered and manifest.get("status") == "complete" else "no_outcome"
    if manifest.get("status") != "complete":
        report["generator_detail"] = manifest.get("detail")
    write_canonical(output, report)
    return report


# --- plan -------------------------------------------------------------------


def validate_plan(plan_path: Path, repository_root: Path) -> None:
    plan = load_json(plan_path)
    if plan.get("document_type") != "dao_allocation_a9_plan" or plan.get("issue") != ISSUE:
        raise EvaluationError("plan has the wrong document type or issue")
    execution = plan.get("execution", {})
    if execution.get("attempts") != 1 or execution.get("replicas") != REPLICAS:
        raise EvaluationError("plan must permit one attempt of three replicas")
    if set(plan.get("questions", {})) != set(QUESTIONS):
        raise EvaluationError("plan must state exactly Q1-Q5")
    inputs = plan.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise EvaluationError("plan has no pinned inputs")
    for relative, expected in inputs.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise EvaluationError(f"unsafe plan input path {relative!r}")
        if not isinstance(expected, str) or len(expected) != 64 or set(expected) - set("0123456789abcdef"):
            raise EvaluationError(f"invalid plan input digest for {relative}")
        if sha256((repository_root / path).read_bytes()) != expected:
            raise EvaluationError(f"plan input digest differs for {relative}")


# --- synthetic dry run ------------------------------------------------------


class SyntheticDatabase:
    """Pages fabricated to the decoding assumptions above; no DAO involved."""

    MARKER = b"L" * 64

    def __init__(self, page_count: int, header_variant: int = 0) -> None:
        self.page_count = page_count
        self.free: set[int] = set()
        self.pages: dict[int, bytes] = {}
        header = bytearray(PAGE_SIZE)
        header[0:15] = b"\x00Standard Jet DB"[:15]
        header[100] = header_variant
        self.pages[0] = bytes(header)
        for page in range(2, page_count):
            self.pages[page] = self.page(0x02 if page in SYSTEM_TDEF_PAGES else 0x01)

    @staticmethod
    def page(tag: int, body: bytes = b"", at: int = 1) -> bytes:
        image = bytearray(PAGE_SIZE)
        image[0] = tag
        image[at : at + len(body)] = body
        return bytes(image)

    def set_tdef(self, page: int, map_page: int) -> None:
        body = bytearray(PAGE_SIZE)
        body[0] = 0x02
        body[OWNED_LOCATOR : OWNED_LOCATOR + 4] = bytes([0]) + map_page.to_bytes(3, "little")
        body[FREE_LOCATOR : FREE_LOCATOR + 4] = bytes([1]) + map_page.to_bytes(3, "little")
        self.pages[page] = bytes(body)

    def set_map_page(self, page: int, owned: bytes, free: bytes) -> None:
        image = bytearray(PAGE_SIZE)
        image[0] = 0x01
        image[8:10] = (2).to_bytes(2, "little")
        start0 = PAGE_SIZE - len(owned)
        start1 = start0 - len(free)
        image[10:12] = start0.to_bytes(2, "little")
        image[12:14] = start1.to_bytes(2, "little")
        image[start0:] = owned
        image[start1:start0] = free
        self.pages[page] = bytes(image)

    @staticmethod
    def type0(start: int, pages: set[int], size: int = 16) -> bytes:
        bitmap = bytearray(size)
        for page in pages:
            bitmap[(page - start) // 8] |= 1 << ((page - start) % 8)
        return b"\x00" + start.to_bytes(4, "little") + bytes(bitmap)

    @staticmethod
    def type1(refs: list[int]) -> bytes:
        return b"\x01" + b"".join(ref.to_bytes(4, "little") for ref in refs)

    def set_type05(self, page: int, slot: int, pages: set[int]) -> None:
        bitmap = bytearray(PAGE_SIZE - 4)
        for owned in pages:
            bit = owned - slot * TYPE05_BITS
            bitmap[bit // 8] |= 1 << (bit % 8)
        self.pages[page] = self.page(0x05, b"\x01\x00\x00" + bytes(bitmap))

    def images(self, selected: bool = False) -> dict[int, bytes]:
        free = set(self.free) | set(range(self.page_count, 1024))
        record = self.type0(0, free, 128)
        page1 = bytearray(self.page(0x01))
        page1[GLOBAL_RECORD_START:] = record
        self.pages[GLOBAL_MAP_PAGE] = bytes(page1)
        if not selected:
            return dict(self.pages)
        keep = {0, 1} | {page for page, image in self.pages.items() if image[0] in (0x02, 0x05)}
        for page in list(keep):
            if self.pages[page][0] == 0x02:
                keep.add(locator(self.pages[page], OWNED_LOCATOR)[1])
        return {page: self.pages[page] for page in sorted(keep)}


def synthetic_checkpoints(replica: int) -> list[tuple[str, str, str, SyntheticDatabase]]:
    """A tiny consistent Q1-Q5 checkpoint sequence for one replica."""
    result = []
    empty = SyntheticDatabase(EMPTY_PAGE_COUNT, header_variant=replica)
    result.append(("Q1", "00-empty", "full", empty))

    def user_table(db: SyntheticDatabase, tdef: int, owned: set[int], free: set[int]) -> None:
        db.set_tdef(tdef, tdef + 1)
        db.set_map_page(tdef + 1, db.type0(tdef, owned), db.type0(tdef, free))

    q2_empty = SyntheticDatabase(20, replica)
    q2_created = SyntheticDatabase(22, replica)
    user_table(q2_created, 20, {21}, set())
    q2_after = SyntheticDatabase(23, replica)
    user_table(q2_after, 20, {21, 22}, {22})
    result += [
        ("Q2", "00-empty", "full", q2_empty),
        ("Q2", "01-table-created", "full", q2_created),
        ("Q2", "02-before-data-page", "full", q2_created),
        ("Q2", "03-after-data-page", "full", q2_after),
    ]

    q3_first = SyntheticDatabase(22, replica)
    user_table(q3_first, 20, {21}, set())
    q3_second = SyntheticDatabase(24, replica)
    user_table(q3_second, 20, {21}, set())
    user_table(q3_second, 22, {23}, set())
    q3_populated = SyntheticDatabase(30, replica)
    user_table(q3_populated, 20, {21, 24, 25, 26, 27, 28, 29}, {29})
    user_table(q3_populated, 22, {23}, set())
    q3_freed = SyntheticDatabase(30, replica)
    user_table(q3_freed, 20, {21, 24, 25, 26}, {26})
    q3_freed.pages[22] = q3_freed.page(0x01)
    q3_freed.free = {22, 23, 27, 28, 29}
    result += [
        ("Q3", "00-first-table", "full", q3_first),
        ("Q3", "01-second-table", "full", q3_second),
        ("Q3", "02-populated", "full", q3_populated),
        ("Q3", "03-freed", "full", q3_freed),
    ]
    remaining = {22, 23, 27, 28, 29}
    for step, reused in enumerate((27, 28, 29, 22), start=1):
        remaining.discard(reused)
        db = SyntheticDatabase(30, replica)
        user_table(db, 20, {21, 24, 25, 26} | ({27, 28, 29, 22} - remaining), {reused})
        db.free = set(remaining)
        result.append(("Q3", f"04-reinsert-{step}", "full", db))

    def q4_state(page_count: int, refs: list[int] | None) -> SyntheticDatabase:
        db = SyntheticDatabase(min(page_count, 32), replica)
        db.page_count = page_count
        db.set_tdef(20, 21)
        if refs is None:
            db.set_map_page(21, db.type0(20, {21, 22}), db.type0(20, {22}))
        else:
            db.set_map_page(21, db.type1(refs + [0] * (4 - len(refs))), db.type0(20, {22}))
            for slot, ref in enumerate(refs):
                db.set_type05(ref, slot, {slot * TYPE05_BITS + 21})
        return db

    result += [
        ("Q4", "00-created", "selected", q4_state(22, None)),
        ("Q4", "01-before-first-type05", "selected", q4_state(1000, None)),
        ("Q4", "02-after-first-type05", "selected", q4_state(1002, [1001])),
        ("Q4", "03-before-second-type05", "selected", q4_state(16352, [1001])),
        ("Q4", "04-after-second-type05", "selected", q4_state(16354, [1001, 16353])),
    ]

    q5_created = SyntheticDatabase(23, replica)
    user_table(q5_created, 20, {21, 22}, set())
    q5_created.pages[22] = q5_created.page(0x04)
    q5_populated = SyntheticDatabase(26, replica)
    user_table(q5_populated, 20, {21, 22, 23, 24, 25}, {23})
    q5_populated.pages[22] = q5_populated.page(0x03)
    q5_populated.pages[23] = q5_populated.page(0x01)
    q5_populated.pages[24] = q5_populated.page(0x01, SyntheticDatabase.MARKER, at=32)
    q5_populated.pages[25] = q5_populated.page(0x04)
    result += [("Q5", "00-created", "full", q5_created), ("Q5", "01-populated", "full", q5_populated)]
    return result


def write_synthetic_artifact(root: Path) -> Path:
    entries = []
    for replica in range(1, REPLICAS + 1):
        for question, name, capture, db in synthetic_checkpoints(replica):
            images = db.images(selected=capture == "selected")
            document = {
                "document_type": "dao_allocation_a9_checkpoint",
                "replica": replica,
                "question": question,
                "name": name,
                "capture": capture,
                "page_count": db.page_count,
                "pages": [
                    {"page": page, "sha256": sha256(image), "hex": image.hex()}
                    for page, image in sorted(images.items())
                ],
            }
            relative = Path(f"r{replica}") / question / f"{name}.json"
            raw = canonical_bytes(document)
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_bytes(raw)
            entries.append(
                {
                    "replica": replica,
                    "question": question,
                    "name": name,
                    "capture": capture,
                    "page_count": db.page_count,
                    "path": relative.as_posix(),
                    "sha256": sha256(raw),
                }
            )
    manifest_path = root / "manifest.raw.json"
    write_canonical(
        manifest_path,
        {
            "document_type": "dao_allocation_a9_manifest",
            "issue": ISSUE,
            "source_revision": "synthetic",
            "status": "complete",
            "detail": "synthetic",
            "provider": None,
            "replica_count": REPLICAS,
            "memo_marker_hex": SyntheticDatabase.MARKER.hex(),
            "checkpoints": entries,
        },
    )
    return manifest_path


def synthetic_dry_run(output: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest_path = write_synthetic_artifact(root)
        report = evaluate(manifest_path, root, root / "report.json")
        accepted = report["status"] == "accepted"
        # Controlled inconsistency: one page image no longer matches its digest.
        tampered = copy.deepcopy(load_json(manifest_path))
        target = root / tampered["checkpoints"][0]["path"]
        document = load_json(target)
        document["pages"][0]["hex"] = "ff" + document["pages"][0]["hex"][2:]
        raw = canonical_bytes(document)
        target.write_bytes(raw)
        tampered["checkpoints"][0]["sha256"] = sha256(raw)
        write_canonical(manifest_path, tampered)
        rejected = False
        try:
            evaluate(manifest_path, root, root / "report-tampered.json")
        except EvaluationError:
            rejected = True
    if not accepted or not rejected:
        raise EvaluationError("synthetic dry run did not behave as required")
    write_canonical(
        output,
        {
            "compatibility_claim": False,
            "consistent_input_accepted": accepted,
            "document_type": "dao_allocation_a9_synthetic_dry_run",
            "inconsistent_input_rejected": rejected,
            "issue": ISSUE,
            "question_statuses": {q: report["questions"][q]["status"] for q in QUESTIONS},
        },
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("plan", type=Path)
    plan.add_argument("repository_root", type=Path)
    evaluate_command = commands.add_parser("evaluate")
    evaluate_command.add_argument("manifest", type=Path)
    evaluate_command.add_argument("artifact_root", type=Path)
    evaluate_command.add_argument("output", type=Path)
    dry_run = commands.add_parser("synthetic-dry-run")
    dry_run.add_argument("output", type=Path)
    return result


def main(arguments: list[str]) -> int:
    args = parser().parse_args(arguments)
    try:
        if args.command == "plan":
            validate_plan(args.plan, args.repository_root)
        elif args.command == "evaluate":
            report = evaluate(args.manifest, args.artifact_root, args.output)
            print(f"{report['status']}: {args.output}")
            if report["status"] != "accepted":
                return 2
        else:
            synthetic_dry_run(args.output)
    except (OSError, ValueError, KeyError, TypeError, EvaluationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
