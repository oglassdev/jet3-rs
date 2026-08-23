#!/usr/bin/env python3
"""Plan-driven A4 reference evaluator: campaign, derivation layers, freeze, holdout.

This module is a reference evaluator for the dry-run reachability harness, not
the production A4 analyzer. It decodes campaign bytes under the plan's
normative predicate rules so the analyzer and independent-validator lanes have
a byte-level oracle to agree with on real fixtures.

A4 rule | implementation
--- | ---
Campaign predicates first in registered order; failure makes all 36 scientific predicates not_applicable | :func:`evaluate`
Derivation on replicas 1 and 2 only, layer order H1..H4, downstream layer depends on the unique upstream model | :func:`_layer`
Replica-invariant canonical model comparison at REPLICA-DISAGREEMENT (AMB-04) | :func:`_layer`
Freeze: canonical hash of the four derivation results before replica 3 is read | :func:`evaluate`
Holdout H1, H2, H3, H4 root, H4 fields in order; upstream failure gates downstream | :func:`_holdout`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import a4_dryrun_h1 as h1
import a4_dryrun_h2 as h2
import a4_dryrun_h3 as h3
import a4_dryrun_h4 as h4
from a4_campaign import Campaign
from a4_dryrun_campaign import evaluate_campaign
from a4_dryrun_core import Context, PredicateRow, require_all_registered
from a4_dryrun_h1 import ReplicaLayer
from a4_spec import (
    DERIVATION_REPLICAS, HOLDOUT_PREDICATES, HOLDOUT_REPLICA, LAYER_KEYS, LAYER_PREDICATES, PREDICATE_ORDER, canonical_id,
)


@dataclass
class Evaluation:
    rows: list[PredicateRow]
    first_failure: str | None
    models: dict[str, Any]
    stages: dict[str, Any]
    charges: dict[str, int]
    derivation_sha256: str | None
    notes: list[str] = field(default_factory=list)

    def row(self, predicate_id: str) -> PredicateRow:
        return next(r for r in self.rows if r.predicate_id == predicate_id)

    def as_document(self) -> dict[str, Any]:
        return {
            "ordered_predicates": [r.__dict__ for r in self.rows],
            "first_failure": self.first_failure,
            "derivation_candidate_set_sha256": self.derivation_sha256,
            "models": self.models,
            "stages": self.stages,
            "charges": self.charges,
            "notes": self.notes,
        }


def _layer(ctx: Context, key: str, run: Callable[[int], ReplicaLayer], stages: dict[str, Any]) -> dict[int, ReplicaLayer] | None:
    ids = LAYER_PREDICATES[key]
    results = {replica: run(replica) for replica in DERIVATION_REPLICAS}
    stages[key] = {str(r): res.stages for r, res in results.items()}
    for predicate_id in ids[:-1]:
        for replica in DERIVATION_REPLICAS:
            outcome = next((o for o in results[replica].outcomes if o[0] == predicate_id), None)
            if outcome is None:
                raise AssertionError(f"replica {replica} did not evaluate {predicate_id} in order")
            _, passed, count, detail = outcome
            if not passed:
                ctx.record(predicate_id, False, count, f"replica {replica}: {detail}")
                return None
        ctx.record(predicate_id, True, next(o[2] for o in results[DERIVATION_REPLICAS[0]].outcomes if o[0] == predicate_id))
    models = [results[r].model for r in DERIVATION_REPLICAS]
    if any(m is None for m in models):
        raise AssertionError("layer passed every local predicate without a model")
    ids_equal = len({m["canonical_model_id"] for m in models if m}) == 1
    detail = "" if ids_equal else f"replica models differ: {[m['model'] for m in models if m]}"
    ctx.record(ids[-1], ids_equal, 1, detail)
    if not ids_equal:
        return None
    ctx.models[key] = models[0]
    return results


def _holdout(ctx: Context, layers: dict[str, dict[int, ReplicaLayer]]) -> None:
    replica = HOLDOUT_REPLICA
    models = {"h1": ctx.models["h1_tdef_to_map_row"], "h2": ctx.models["h2_row_identity_map_role"],
              "h3": ctx.models["h3_indirect_traversal"], "h4": ctx.models["h4_catalog_bootstrap"]}
    if replica not in ctx.campaign.replicas:
        ctx.record(HOLDOUT_PREDICATES[0], False, 1, "replica 3 missing")
        return
    ok, detail, bindings = h1.holdout(ctx, replica, models["h1"])
    if not ctx.record(HOLDOUT_PREDICATES[0], ok, 1, detail):
        return
    ok, detail, located = h2.holdout(ctx, replica, models["h2"], bindings)
    if not ctx.record(HOLDOUT_PREDICATES[1], ok, 1, detail) or located is None:
        return
    owned = h2.owned_rows(located, models["h2"]["model"]["locator_role_assignment"])
    ok, detail = h3.holdout(ctx, replica, models["h3"], owned, models["h2"]["model"]["polarity"])
    if not ctx.record(HOLDOUT_PREDICATES[2], ok, 1, detail):
        return
    ok, detail, admitted = h4.holdout_root(ctx, replica, models, bindings)
    if not ctx.record(HOLDOUT_PREDICATES[3], ok, 1, detail) or admitted is None:
        return
    ok, detail = h4.holdout_fields(ctx, replica, models, admitted)
    ctx.record(HOLDOUT_PREDICATES[4], ok, 1, detail)


def evaluate(campaign: Campaign, grammar_selection: dict[str, list[Any]] | None = None) -> Evaluation:
    """Evaluate one shared campaign through the 40 registered predicates in order; first failure is terminal."""
    require_all_registered(grammar_selection)
    ctx = Context(campaign)
    stages: dict[str, Any] = {}
    derivation_sha256 = None
    if evaluate_campaign(ctx):
        layers: dict[str, dict[int, ReplicaLayer]] = {}
        h1_results = _layer(ctx, LAYER_KEYS[0], lambda r: h1.evaluate_replica(ctx, r), stages)
        if h1_results:
            layers[LAYER_KEYS[0]] = h1_results
            h2_results = _layer(ctx, LAYER_KEYS[1], lambda r: h2.evaluate_replica(ctx, r, h1_results[r].bindings), stages)
            if h2_results:
                layers[LAYER_KEYS[1]] = h2_results
                assignment = ctx.models[LAYER_KEYS[1]]["model"]["locator_role_assignment"]
                polarity = ctx.models[LAYER_KEYS[1]]["model"]["polarity"]
                h3_results = _layer(ctx, LAYER_KEYS[2], lambda r: h3.evaluate_replica(
                    ctx, r, h2.owned_rows(h2_results[r].bindings["located"], assignment), polarity), stages)
                if h3_results:
                    layers[LAYER_KEYS[2]] = h3_results
                    models = {"h1": ctx.models[LAYER_KEYS[0]], "h2": ctx.models[LAYER_KEYS[1]], "h3": ctx.models[LAYER_KEYS[2]]}
                    h4_results = _layer(ctx, LAYER_KEYS[3], lambda r: h4.evaluate_replica(ctx, r, models, h1_results[r].bindings), stages)
                    if h4_results:
                        layers[LAYER_KEYS[3]] = h4_results
        # Freeze before any replica-3 read: the four derivation results are hashed whatever their outcome.
        derivation_sha256 = canonical_id({
            "rows": [r.__dict__ for r in ctx.rows],
            "models": {k: ctx.models.get(k) for k in LAYER_KEYS},
        })
        if len(layers) == len(LAYER_KEYS):
            _holdout(ctx, layers)
    ctx.skip(PREDICATE_ORDER)
    order = {p: i for i, p in enumerate(PREDICATE_ORDER)}
    rows = sorted(ctx.rows, key=lambda r: order[r.predicate_id])
    return Evaluation(rows, ctx.first_failure(), dict(ctx.models), stages, dict(ctx.charges.units), derivation_sha256, list(ctx.notes))
