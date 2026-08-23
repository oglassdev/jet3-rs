#!/usr/bin/env python3
"""Plan-bound constants for the DAO-A4-ROW-ANCHORED-MAPS-001 dry-run harness.

Everything here is read from the checked base plan; nothing is a hand-authored
expected status. The plan hash is verified against the README pin so every
transcript is bound to the exact plan bytes it was evaluated under.

A4 rule | implementation
--- | ---
Plan/README hash binding | :func:`load_plan`
40 predicate contracts in registry order | :data:`PREDICATE_CONTRACTS`
Closed candidate grammars | :data:`GRAMMAR`
Lifecycle instances and inclusive checkpoint ranges (P4-B3 text) | :data:`INSTANCES`
Checkpoint events derived from ``checkpoint_operations`` | :data:`EVENTS`
Measured count versus ``exact``/``minimum``/``allowed_ranges`` | :func:`count_satisfies`
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

DAO_ROOT = Path(__file__).resolve().parents[1]
A4_ROOT = DAO_ROOT / "experiments" / "a4"
PLAN_PATH = A4_ROOT / "a4-row-anchored-maps.plan.json"
README_PATH = A4_ROOT / "README.md"
EXPERIMENT_ID = "DAO-A4-ROW-ANCHORED-MAPS-001"
PAGE_SIZE = 2048
DERIVATION_REPLICAS = (1, 2)
HOLDOUT_REPLICA = 3


class SpecError(Exception):
    """The checked plan is absent, altered, or not pinned by the README."""


def canonical_json_bytes(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_id(document: Any) -> str:
    return sha256_hex(canonical_json_bytes(document))


def _readme_pin(text: str) -> str:
    match = re.search(r"`a4-row-anchored-maps\.plan\.json`, SHA-256\s*\n?`([0-9a-f]{64})`", text)
    if match is None:
        raise SpecError("README does not pin the base plan SHA-256")
    return match.group(1)


def load_plan() -> tuple[dict[str, Any], str]:
    """Return the plan document and its SHA-256, rejecting a README/plan mismatch."""
    raw = PLAN_PATH.read_bytes()
    digest = sha256_hex(raw)
    pinned = _readme_pin(README_PATH.read_text(encoding="utf-8"))
    if pinned != digest:
        raise SpecError(f"plan SHA-256 {digest} differs from README pin {pinned}")
    document = json.loads(raw)
    if document.get("experiment_id") != EXPERIMENT_ID:
        raise SpecError("plan experiment id mismatch")
    return document, digest


PLAN, PLAN_SHA256 = load_plan()
# Until an additive revision exists both bindings equal the base hash (revision_binding_rule).
REVISION_PLAN_SHA256 = PLAN_SHA256

CHECKPOINTS: tuple[str, ...] = tuple(PLAN["checkpoint_design"]["checkpoint_ids"])
ORDINAL: Mapping[str, int] = MappingProxyType({cp: i for i, cp in enumerate(CHECKPOINTS)})
IDLE_PAIRS: tuple[tuple[str, str], ...] = tuple(tuple(p) for p in PLAN["checkpoint_design"]["idle_pairs"])
TRANSITIONS = PLAN["checkpoint_design"]["transition_coverage"]
SCHEMA_LIFECYCLE: tuple[str, ...] = tuple(TRANSITIONS["schema_lifecycle"])
EXPECTED_SCHEMA: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {cp: tuple(v) for cp, v in PLAN["tables"]["expected_schema_by_checkpoint"].items()}
)
ROLES: tuple[str, ...] = tuple(PLAN["tables"]["logical_roles"])
PHYSICAL_NAMES: tuple[str, ...] = tuple(PLAN["tables"]["physical_names"])
ROLE_BINDINGS: Mapping[int, Mapping[str, str]] = MappingProxyType(
    {b["replica"]: MappingProxyType({r: b[r] for r in ROLES}) for b in PLAN["tables"]["role_bindings"]}
)
FIELD_DEFS = PLAN["tables"]["definition"]["fields"]
INDEX_DEF = PLAN["tables"]["definition"]["index"]
ROW_BATCH = int(PLAN["tables"]["row_algorithm"]["growth_batch_rows"])
MAX_ROWS = int(PLAN["bounds"]["max_inserted_rows_per_replica"])
BOUNDS = PLAN["bounds"]
MAX_QUALIFIED_PAGES = int(BOUNDS["max_qualified_pages_per_submodel"])
GRAMMAR = PLAN["candidate_grammars"]
H1_SIGNATURE = GRAMMAR["h1"]["table_record_signature"]
SIGNATURE_VALUE = bytes.fromhex(H1_SIGNATURE["value_hex"])
SIGNATURE_MASK = bytes.fromhex(H1_SIGNATURE["mask_hex"])
LOCATOR_HOLES: tuple[tuple[int, int], ...] = tuple(tuple(h) for h in H1_SIGNATURE["locator_holes"])
LOCATOR_LAYOUTS: tuple[str, ...] = tuple(GRAMMAR["h1"]["locator_layouts"])
TDEF_SIGNATURES: tuple[str, ...] = tuple(GRAMMAR["h1"]["tdef_lifecycle_signatures"])
ROW_MASKS: tuple[int, ...] = tuple(int(m) for m in GRAMMAR["h2"]["row_masks"])
POLARITIES: tuple[str, ...] = tuple(GRAMMAR["h2"]["type_0_polarities"])
ROLE_ASSIGNMENTS: tuple[str, ...] = tuple(GRAMMAR["h2"]["locator_role_assignments"])
CONVERSIONS: tuple[str, ...] = tuple(GRAMMAR["h3"]["conversion_candidates"])
BASE_FORMULAS: tuple[str, ...] = tuple(GRAMMAR["h3"]["base_formulas"])
ROOT_SIGNATURES: tuple[str, ...] = tuple(GRAMMAR["h4"]["catalog_root_selection_signatures"])
KIND_WIDTHS: tuple[int, ...] = tuple(GRAMMAR["h4"]["kind_widths"])
ID_WIDTHS: tuple[int, ...] = tuple(GRAMMAR["h4"]["identifier_widths"])
ENDIANNESS: tuple[str, ...] = tuple(GRAMMAR["h4"]["endianness"])
LIFECYCLE_RELATIONS: tuple[str, ...] = tuple(GRAMMAR["h4"]["identifier_lifecycle_relations"])
NAME_ENCODINGS: tuple[dict[str, str], ...] = tuple(GRAMMAR["h4"]["name_encodings"])
LENGTH_CLASSES: tuple[dict[str, Any], ...] = tuple(GRAMMAR["h4"]["name_length_equivalence_classes"])
FIELD_DELTA_RANGE = range(1, 17)
MAX_CANDIDATES = int(BOUNDS["max_candidate_models"])
TAG05_BITS = (PAGE_SIZE - 4) * 8
DELETED_FLAG, OVERFLOW_FLAG = 0x8000, 0x4000

PREDICATE_IDS: tuple[str, ...] = tuple(PLAN["predicate_registry"]["ids"])
_CONTRACT_ROWS = sorted(PLAN["predicate_registry"]["predicate_contracts"], key=lambda c: c["order"])
PREDICATE_CONTRACTS: Mapping[str, dict[str, Any]] = MappingProxyType({c["predicate_id"]: c for c in _CONTRACT_ROWS})
PREDICATE_ORDER: tuple[str, ...] = tuple(c["predicate_id"] for c in _CONTRACT_ROWS)
CAMPAIGN_PREDICATES: tuple[str, ...] = tuple(PLAN["predicate_registry"]["campaign_evaluated_before_any_layer"])
LAYER_PREDICATES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {k: tuple(v) for k, v in PLAN["predicate_registry"]["per_layer_ordered_predicates"].items()}
)
LAYER_KEYS: tuple[str, ...] = tuple(LAYER_PREDICATES)
HOLDOUT_PREDICATES: tuple[str, ...] = tuple(PLAN["predicate_registry"]["holdout_phase_ordered_predicates"])
if len(PREDICATE_ORDER) != 40 or set(PREDICATE_ORDER) != set(PREDICATE_IDS):
    raise SpecError("predicate contracts do not cover the 40 registered ids exactly once")


@dataclass(frozen=True)
class Instance:
    """A lifecycle instance with the inclusive checkpoint range derived from expected_schema_by_checkpoint."""

    id: str
    role: str
    version: str
    create_checkpoint: str
    last_checkpoint: str

    @property
    def checkpoints(self) -> tuple[str, ...]:
        return CHECKPOINTS[ORDINAL[self.create_checkpoint]: ORDINAL[self.last_checkpoint] + 1]


def _instances() -> tuple[Instance, ...]:
    spans: dict[str, list[str]] = {}
    for cp in CHECKPOINTS:
        for token in EXPECTED_SCHEMA[cp]:
            parts = token.split(":")
            role = parts[0]
            version = parts[1] if parts[1].startswith("v") else "v1"
            spans.setdefault(f"{role}-{version}", []).append(cp)
    out = []
    for key, cps in spans.items():
        role, version = key.split("-")
        out.append(Instance(key, role, version, cps[0], cps[-1]))
    return tuple(sorted(out, key=lambda i: ORDINAL[i.create_checkpoint]))


INSTANCES: tuple[Instance, ...] = _instances()
INSTANCE_BY_ID: Mapping[str, Instance] = MappingProxyType({i.id: i for i in INSTANCES})
if tuple(i.id for i in INSTANCES) != ("T1-v1", "T2-v1", "T2-v2", "T3-v1", "T4-v1"):
    raise SpecError("lifecycle instances derived from the plan are not the five registered instances")


@dataclass(frozen=True)
class Event:
    """What the listed operation between the previous checkpoint and this one does."""

    checkpoint: str
    kind: str  # create | add_field | add_index | drop | grow | delete_all | reinsert | idle | empty
    role: str | None
    instance: str | None


def _events() -> tuple[Event, ...]:
    ops = PLAN["tables"]["checkpoint_operations"]
    out = []
    for cp in CHECKPOINTS:
        text = ops[cp]
        role = cp[:2] if cp[:2] in ROLES else None
        instance = None
        if role is not None:
            live = [i for i in INSTANCES if i.role == role and cp in i.checkpoints]
            instance = live[0].id if live else None
        if cp == "EMPTY":
            kind = "empty"
        elif "Close and reopen" in text:
            kind = "idle"
        elif "TableDefs.Delete" in text:
            kind = "drop"
        elif "TableDefs.Append" in text:
            kind = "create"
        elif "Fields.Append" in text:
            kind = "add_field"
        elif "Indexes.Append" in text:
            kind = "add_index"
        elif "Delete every" in text:
            kind = "delete_all"
        elif "Reinsert" in text:
            kind = "reinsert"
        else:
            kind = "grow"
        out.append(Event(cp, kind, role, instance))
    return tuple(out)


EVENTS: tuple[Event, ...] = _events()
EVENT_BY_CHECKPOINT: Mapping[str, Event] = MappingProxyType({e.checkpoint: e for e in EVENTS})
# The seven H4 operation instances in checkpoint order (expected_kind_relations).
OPERATION_INSTANCES: tuple[tuple[str, str, str], ...] = tuple(
    (e.checkpoint, e.instance or "", {"create": "table", "add_field": "field", "add_index": "index"}[e.kind])
    for e in EVENTS if e.kind in ("create", "add_field", "add_index")
)
if len(OPERATION_INSTANCES) != 7:
    raise SpecError("plan does not list exactly seven catalog operation instances")
FIELD_NAME = FIELD_DEFS[1]["name"]
INDEX_NAME = INDEX_DEF["name"]


def expected_name(replica: int, checkpoint: str) -> str:
    """Object name created by the listed operation at ``checkpoint`` under the replica's role rotation."""
    event = EVENT_BY_CHECKPOINT[checkpoint]
    if event.kind == "create":
        return ROLE_BINDINGS[replica][event.role or ""]
    return FIELD_NAME if event.kind == "add_field" else INDEX_NAME


def name_bytes(name: str) -> dict[str, bytes]:
    """Both registered expected byte sequences for a name; strict, no best-fit."""
    return {
        NAME_ENCODINGS[0]["id"]: name.encode("cp1252"),
        NAME_ENCODINGS[1]["id"]: name.encode("utf-8"),
    }


def row_payload(role: str, row_id: int) -> bytes:
    text = f"A4|{role}|{row_id:010d}|"
    return (text * (240 // len(text) + 1))[:240].encode("ascii")


_ROLLING_CACHE: dict[tuple[str, int], str] = {}


def rolling_sha256(role: str, row_count: int) -> str:
    key = (role, row_count)
    if key not in _ROLLING_CACHE:
        digest = hashlib.sha256()
        for row_id in range(1, row_count + 1):
            payload = row_payload(role, row_id)
            digest.update(row_id.to_bytes(4, "little", signed=True))
            digest.update(len(payload).to_bytes(2, "little"))
            digest.update(payload)
        _ROLLING_CACHE[key] = digest.hexdigest()
    return _ROLLING_CACHE[key]


def count_satisfies(contract: dict[str, Any], measured: int) -> bool:
    """R4-S01 semantics: exact equality, minimum >=, or membership in one allowed range."""
    if "allowed_ranges" in contract:
        return any(count_satisfies(r, measured) for r in contract["allowed_ranges"])
    if "exact" in contract:
        return measured == int(contract["exact"])
    if "minimum" in contract:
        return measured >= int(contract["minimum"])
    raise SpecError(f"unrecognised survivor count contract {contract}")


def layer_of(predicate_id: str) -> str | None:
    for key, ids in LAYER_PREDICATES.items():
        if predicate_id in ids:
            return key
    return None
