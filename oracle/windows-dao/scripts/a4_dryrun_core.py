#!/usr/bin/env python3
"""Shared state for the A4 plan-driven reference evaluator.

This evaluator is NOT the production A4 analyzer. It is a plan-driven
reference that decodes fixture bytes under the plan's normative predicate
rules so that the later analyzer and independent-validator lanes must agree
with it on real fixtures. Every place where the plan is ambiguous is recorded
in :data:`AMBIGUITIES` and the reading chosen is stated next to the code.

A4 rule | implementation
--- | ---
One propagated state per fixture, first failure reported | :class:`Context`, :class:`PredicateRow`
Union-once resource charging | :class:`Charges`
Unregistered candidate ids rejected before scientific filtering | :func:`require_registered`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from a4_campaign import Campaign
from a4_pages import tag_of
from a4_spec import (
    BASE_FORMULAS, CHECKPOINTS, CONVERSIONS, ENDIANNESS, ID_WIDTHS, KIND_WIDTHS, LIFECYCLE_RELATIONS,
    LOCATOR_LAYOUTS, ORDINAL, POLARITIES, ROLE_ASSIGNMENTS, ROOT_SIGNATURES, ROW_MASKS, TDEF_SIGNATURES,
)

PASS, FAIL, NOT_APPLICABLE = "pass", "fail", "not_applicable"


class FixtureRejected(Exception):
    """The fixture is not a legitimate campaign (malformed page, unregistered id, ...)."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


@dataclass
class PredicateRow:
    predicate_id: str
    status: str
    measured_count: int
    detail: str = ""


@dataclass
class Charges:
    units: dict[str, int] = field(default_factory=dict)

    def add(self, term: str, amount: int = 1) -> None:
        self.units[term] = self.units.get(term, 0) + amount

    def total(self) -> int:
        return sum(self.units.values())


REGISTERED: dict[str, tuple[Any, ...]] = {
    "locator_layout": LOCATOR_LAYOUTS,
    "tdef_lifecycle_signature": TDEF_SIGNATURES,
    "row_mask": ROW_MASKS,
    "type_0_polarity": POLARITIES,
    "locator_role_assignment": ROLE_ASSIGNMENTS,
    "conversion_candidate": CONVERSIONS,
    "base_formula": BASE_FORMULAS,
    "catalog_root_selection_signature": ROOT_SIGNATURES,
    "kind_width": KIND_WIDTHS,
    "identifier_width": ID_WIDTHS,
    "endianness": ENDIANNESS,
    "identifier_lifecycle_relation": LIFECYCLE_RELATIONS,
}


def require_registered(kind: str, value: Any) -> None:
    if kind not in REGISTERED or value not in REGISTERED[kind]:
        raise FixtureRejected("unregistered_candidate_id", f"unregistered candidate id for {kind}: {value!r}")


def require_all_registered(selection: dict[str, list[Any]] | None) -> None:
    """A fixture may name the grammar members it expects to be enumerated; every one must be registered."""
    for kind, values in (selection or {}).items():
        for value in values:
            require_registered(kind, value)


@dataclass
class Context:
    campaign: Campaign
    charges: Charges = field(default_factory=Charges)
    rows: list[PredicateRow] = field(default_factory=list)
    models: dict[str, Any] = field(default_factory=dict)  # layer key -> frozen derivation model
    bindings: dict[str, Any] = field(default_factory=dict)  # layer key -> per-replica bindings
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- byte access
    def page(self, replica: int, checkpoint: str, number: int) -> bytes | None:
        return self.campaign.page(replica, checkpoint, number)

    def page_count(self, replica: int, checkpoint: str) -> int:
        return self.campaign.page_count(replica, checkpoint)

    def tag(self, replica: int, checkpoint: str, number: int) -> int | None:
        page = self.page(replica, checkpoint, number)
        return None if page is None else tag_of(page)

    def page_hash(self, replica: int, checkpoint: str, number: int) -> str | None:
        hashes = self.campaign.replicas[replica].pages[checkpoint]
        return hashes[number] if 0 <= number < len(hashes) else None

    def changed_pages(self, replica: int, checkpoint: str) -> set[int]:
        return set(self.campaign.replicas[replica].page_indexes[checkpoint]["changed_page_indices"])

    def predecessor(self, checkpoint: str) -> str | None:
        ordinal = ORDINAL[checkpoint]
        return CHECKPOINTS[ordinal - 1] if ordinal else None

    def replicas(self) -> list[int]:
        return sorted(self.campaign.replicas)

    # ------------------------------------------------------------- rows
    def record(self, predicate_id: str, passed: bool, measured: int, detail: str = "") -> bool:
        self.rows.append(PredicateRow(predicate_id, PASS if passed else FAIL, measured, detail))
        return passed

    def skip(self, predicate_ids: tuple[str, ...]) -> None:
        done = {row.predicate_id for row in self.rows}
        for predicate_id in predicate_ids:
            if predicate_id not in done:
                self.rows.append(PredicateRow(predicate_id, NOT_APPLICABLE, 0))

    def first_failure(self) -> str | None:
        for row in self.rows:
            if row.status == FAIL:
                return row.predicate_id
        return None


def canonical_set_model(values: set[int]) -> list[int]:
    return sorted(values)


AMBIGUITIES: tuple[dict[str, str], ...] = (
    {"id": "AMB-01", "topic": "syntactic decodability",
     "plan": "candidate_grammars.h1.layout_candidate_rule / a3_page_23_recomputed_work",
     "reading": "A four-byte window is syntactically decodable when its u24 page value is <= 65535 (high byte zero). Only this bound reproduces the plan's 1,872 preserved windows per layout on retained A3 page 23; the frozen-model bound 0..20479 yields 1,869."},
    {"id": "AMB-02", "topic": "H1 structural pair requires the exact locator holes",
     "plan": "filter_order.apply_table_record_signature_and_exact_locator_holes",
     "reading": "A pair is structural only at offsets (35,39). Consequently at most one pair exists per layout and A4-H1-LOCATOR-PAIR-MULTIPLE is unreachable by enumeration, contradicting its claimed fixture."},
    {"id": "AMB-03", "topic": "union-once qualified page identity",
     "plan": "record_candidate_procedure.qualified_page_rule / union_once_charging",
     "reading": "Qualified tag-02 pages are identified by page number in the union across derivation replicas; a page exposed by both replicas is charged once."},
    {"id": "AMB-04", "topic": "NONE/MULTIPLE counts across replicas",
     "plan": "predicate_contracts input_candidate_set (per replica versus union)",
     "reading": "Each derivation replica is evaluated independently in order 1 then 2; a cardinality predicate fails on the first replica violating it and stores that replica's measured count. REPLICA-DISAGREEMENT compares the two replica-invariant models."},
    {"id": "AMB-05", "topic": "H2 static role/polarity fit",
     "plan": "A4-H2-ROLE-NONE 'fits their static structure' is undefined",
     "reading": "Static fit at every applicable checkpoint: both type-0 admitted sets lie below page_count, the owned/in-use set is nonempty, and the available set is a subset of the owned set. Type-1 rows are skipped at H2 because their admitted set needs the H3 traversal."},
    {"id": "AMB-06", "topic": "H2 idle transition versus A4-IDLE-EQUALITY",
     "plan": "A4-R19-H2-TRANSITION fixture text 'owned set changes during an idle leg'",
     "reading": "Every registered idle leg is an idle pair whose bytes must already be equal at A4-IDLE-EQUALITY, so the described fixture cannot first fail at H2. The harness reaches A4-H2-TRANSITION-UNEXPLAINED with a grow-leg violation instead."},
    {"id": "AMB-07", "topic": "grow signature 'may remove but not add those same pages'",
     "plan": "candidate_grammars.h2.transition_signature.grow",
     "reading": "On a grow leg the owned set may only gain pages and the available set may not gain any page the owned set gained on that leg; unrelated instances are unconstrained on another role's leg."},
    {"id": "AMB-08", "topic": "H3 base-formula fit and input regions",
     "plan": "A4-H3-BASE-DISCRIMINATION / base_discrimination_rule",
     "reading": "A formula fits when, at every conversion leg, the type-0 owned set is contained in the formula-decoded type-1 set and later legs obey the H2 transition signatures on formula-decoded sets. No extant-page rule is applied to admitted pages (claims.exact_allocation_set_equality is false). Regions: inactive slot, active slot, bit 0 set, nonzero bit set, bit 16351 set in slot s, bit 0 set in slot s+1 with both slots active."},
    {"id": "AMB-09", "topic": "H4 root selection signature",
     "plan": "catalog_root_selection_signature_rules.operation_delta_non_name_structure",
     "reading": "A root candidate is an EMPTY tag-02 page whose frozen-model traversal is valid at every checkpoint and whose admitted stream (admitted set or admitted page hashes) changes at one or more of the seven listed operations. Requiring all seven would make A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED unreachable. The H1 record signature is not applied to system TDEFs."},
    {"id": "AMB-10", "topic": "isolated physical schema delta",
     "plan": "A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED",
     "reading": "At an operation transition the isolated delta is changed_page_indices minus the TDEF and map pages of every user instance extant at that checkpoint. Page 0 is assumed unchanged; a real Jet header page that changes on every operation would fail this predicate on every campaign."},
    {"id": "AMB-11", "topic": "H4 structural stage length field",
     "plan": "raw_tuple_filter_order has no test of the name-length field before count_structural_field_layouts",
     "reading": "A structural tuple's length field must decode to a count some registered encoding/length class could report for the expected name (union of both encodings' byte counts and the scalar count). Without this the length address is a free dimension and A4-H4-FIELD-MODEL-MULTIPLE is the only reachable H4 outcome."},
    {"id": "AMB-12", "topic": "H4 observational equivalence of field tuples",
     "plan": "deduplicate_identical_byte_address_tuple; endianness of one-byte fields",
     "reading": "Tuples that decode identical kind, identifier and length values for every operation record at every checkpoint are one canonical model (as row masks with byte-identical bounds are). Endianness is otherwise unidentifiable by equality relations, making decisive H4 impossible."},
    {"id": "AMB-13", "topic": "campaign predicates consume replica 3 before the freeze",
     "plan": "A4-SCHEMA-SNAPSHOT input '75 canonical DAO snapshots' versus freeze_rule",
     "reading": "Campaign predicates are evaluated over every replica present (75 snapshots); derivation layers read replicas 1 and 2 only; holdout predicates read replica 3 only."},
    {"id": "AMB-14", "topic": "H3 holdout with no type-1 row",
     "plan": "A4-H3-HOLDOUT-PREDICTION pass_iff 'predicts every replica-3 slot'",
     "reading": "If replica 3 exposes no type-1 owned row the frozen H3 model is vacuously confirmed; the harness reports this as pass with a note."},
    {"id": "AMB-15", "topic": "A4-H2-REPLICA-DISAGREEMENT and the H1 role-assignment",
     "plan": "h2 locator_role_assignments versus h1_frozen_model_rule",
     "reading": "The H2 assignment binds locator ordinal (first/second hole) to owned/available; swapping row order in one replica yields a different canonical H2 model while H1 stays replica-invariant."},
    {"id": "AMB-16", "topic": "dispatch-gate transcript schema requires every predicate to be a first failure",
     "plan": "reachability-transcript.schema.json fixtureEntry.first_failure_id (non-null) with minItems/maxItems 40",
     "reading": "The schema admits no asserted-unreachable entry, yet A4-H1-LOCATOR-PAIR-MULTIPLE is unreachable under the exact-hole rule (AMB-02). Either the hole rule or the schema must change; the reference transcript records the unreachable assertion separately."},
    {"id": "AMB-17", "topic": "dispatch-gate transcript needs artifacts that do not exist yet",
     "plan": "reachability_transcript_binding; adversarialOutcome.case_id resource_exact_ceiling / resource_one_over",
     "reading": "The bound transcript requires analyzer and independent-validator commits and results, a provenance entry id, and resource-ceiling cases at exactly 600,000,000 work units. A synthetic campaign cannot reach that ceiling honestly (baseline charges are six orders of magnitude lower), so those two cases need a declared charging-injection mechanism in the plan. This harness writes a reference transcript under a distinct document type instead of claiming the gate artifact."},
)
