#!/usr/bin/env python3
"""A4 byte-level reachability harness: executes every fixture and writes the hash-bound transcript.

This harness executes the plan's ``dry_run_honesty_clause`` reachability
contract against the plan-driven reference evaluator (not the production
analyzer). For every registered predicate it builds the shared synthetic
campaign plus the fixture's disclosed mutation, evaluates the 40 predicates in
normative order from bytes, and accepts the fixture only when the target
predicate is the measured first failure and the measured survivor count
satisfies the predicate's ``exact``/``minimum``/``allowed_ranges`` contract.
Unreachable terminals are asserted by enumeration across the whole sweep.

The output is a *reference* transcript, deliberately distinct from the
dispatch-gate ``dry-run/a4-reachability-transcript.json`` bound by
``reachability-transcript.schema.json``: that document needs the production
analyzer, an independent validator, and an additive provenance entry, none of
which exist yet (see AMB-16/AMB-17).

A4 rule | implementation
--- | ---
Every claimed-reachable terminal demonstrated as measured first failure | :func:`run_fixture`, :func:`reachability_rows`
Unreachable terminal asserted by enumeration | :func:`unreachable_rows`
Adversarial cases rejected unless a legitimate measured outcome | :func:`run_fixture`
Transcript bound to plan SHA-256, harness sources, campaign inventories | :func:`build_transcript`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a4_dryrun_core import AMBIGUITIES, FixtureRejected
from a4_dryrun_eval import evaluate
from a4_dryrun_fixtures import ADVERSARIAL, BASELINE, REGISTRY_FIXTURES, UNREACHABLE, Fixture, all_fixtures
from a4_spec import (
    EXPERIMENT_ID, PLAN_SHA256, PREDICATE_CONTRACTS, PREDICATE_ORDER, REVISION_PLAN_SHA256, canonical_json_bytes,
    count_satisfies, sha256_hex,
)

SCRIPTS = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SCRIPTS.parent / "experiments" / "a4" / "dry-run"
HARNESS_FILES = (
    "a4_spec.py", "a4_pages.py", "a4_campaign.py", "a4_generator.py", "a4_dryrun_core.py", "a4_dryrun_campaign.py",
    "a4_dryrun_h1.py", "a4_dryrun_h2.py", "a4_dryrun_h3.py", "a4_dryrun_h4.py", "a4_dryrun_eval.py",
    "a4_dryrun_fixtures.py", "a4_dryrun.py",
)
GENERATOR_FILES = ("a4_spec.py", "a4_pages.py", "a4_campaign.py", "a4_generator.py")
TRANSCRIPT_NAME = "a4-reference-reachability-transcript.json"
# reachability-transcript.schema.json adversarial case ids -> fixture demonstrating each outcome
ADVERSARIAL_CASE_IDS = {
    "multiple_count_2": "A4-R06-H1-TDEF-MULTIPLE", "multiple_count_3": "A4-ADV-TDEF-MULTIPLE-3",
    "multiple_count_4": "A4-ADV-TDEF-MULTIPLE-4", "encoding_count_0": "A4-R37-H4-ENCODING",
    "encoding_count_2": "A4-ADV-ENCODING-2", "unregistered_candidate_id": "A4-ADV-UNREGISTERED-ID",
    "malformed_page": "A4-ADV-MALFORMED-PAGE", "earlier_predicate_invalidated": "A4-ADV-EARLIER-PREDICATE",
}


def _sha256_files(names: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in names:
        digest.update((SCRIPTS / name).read_bytes())
    return digest.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=SCRIPTS, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "0" * 40


def run_fixture(fixture: Fixture) -> dict[str, Any]:
    """Build, evaluate, and judge one fixture; the judgement is measured, never assumed."""
    entry: dict[str, Any] = {
        "fixture_id": fixture.fixture_id, "target_predicate_id": fixture.predicate_id, "description": fixture.description,
        "mutation": fixture.mutation(), "mutation_sha256": fixture.mutation_sha256(),
    }
    try:
        campaign = fixture.build()
        entry["campaign"] = campaign.inventory()
        evaluation = evaluate(campaign, fixture.grammar_selection)
    except FixtureRejected as error:
        entry.update({"rejected": True, "rejection": str(error), "accepted": fixture.expect_rejection is not None
                      and fixture.expect_rejection in ("unregistered_candidate_id", "malformed_page")})
        return entry
    rows = [r.__dict__ for r in evaluation.rows]
    first = evaluation.first_failure
    entry.update({
        "ordered_predicates": rows, "first_failure": first, "derivation_candidate_set_sha256": evaluation.derivation_sha256,
        "models": evaluation.models, "stages": evaluation.stages, "charges": evaluation.charges, "notes": evaluation.notes,
        "evaluated_counts": {r["predicate_id"]: r["measured_count"] for r in rows if r["status"] != "not_applicable"},
    })
    if fixture.expect_rejection == "first_failure_is_not_target":
        entry["rejected"] = first != fixture.predicate_id
        entry["rejection"] = f"first failure {first} is not the target {fixture.predicate_id}" if entry["rejected"] else ""
        entry["accepted"] = entry["rejected"]
        return entry
    if fixture.predicate_id is None:
        entry["accepted"] = first is None
        entry["rejection"] = "" if first is None else f"unexpected first failure {first}"
        return entry
    target_row = evaluation.row(fixture.predicate_id)
    contract = PREDICATE_CONTRACTS[fixture.predicate_id]["failure_survivor_count"]
    entry["count_contract"] = contract
    entry["measured_count"] = target_row.measured_count
    problems = []
    if first != fixture.predicate_id:
        problems.append(f"first failure {first} is not the target")
    if not count_satisfies(contract, target_row.measured_count):
        problems.append(f"measured count {target_row.measured_count} violates {contract}")
    if fixture.legitimate_count is not None and target_row.measured_count != fixture.legitimate_count:
        problems.append(f"measured count {target_row.measured_count} differs from the fixture's stated {fixture.legitimate_count}")
    entry["accepted"] = not problems
    entry["rejection"] = "; ".join(problems)
    return entry


def _run_named(fixture_id: str) -> dict[str, Any]:
    fixture = next(f for f in all_fixtures() if f.fixture_id == fixture_id)
    return run_fixture(fixture)


def reachability_rows(entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for predicate_id in PREDICATE_ORDER:
        fixture = next(f for f in REGISTRY_FIXTURES if f.predicate_id == predicate_id)
        entry = entries[fixture.fixture_id]
        reaching = sorted(fid for fid, e in entries.items() if e.get("first_failure") == predicate_id)
        if predicate_id in UNREACHABLE:
            rows.append({"predicate_id": predicate_id, "classification": "unreachable", "attempt_fixture": fixture.fixture_id,
                         "attempt_first_failure": entry.get("first_failure"), "fixtures_reaching": reaching,
                         "status": "asserted_unreachable" if not reaching else "REACHED_CONTRARY_TO_ASSERTION"})
            continue
        rows.append({"predicate_id": predicate_id, "classification": "claimed_reachable", "designated_fixture": fixture.fixture_id,
                     "measured_first_failure": entry.get("first_failure"), "measured_count": entry.get("measured_count"),
                     "count_contract": entry.get("count_contract"), "fixtures_reaching": reaching,
                     "status": "reached" if entry.get("accepted") else "NOT_REACHED"})
    return rows


def unreachable_rows(entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for predicate_id in UNREACHABLE:
        counts = {fid: e["evaluated_counts"].get(predicate_id) for fid, e in entries.items() if "evaluated_counts" in e}
        evaluated = {fid: c for fid, c in counts.items() if c is not None}
        out.append({
            "predicate_id": predicate_id,
            "enumeration_argument": "structural pairs must sit at the exact registered locator holes [35,39)/[39,43); one layout therefore admits at most one canonical pair (a<b, b-a>=4) and the pair count under the unique layout can never exceed one",
            "max_measured_count_across_sweep": max(evaluated.values(), default=0),
            "fixtures_evaluating_predicate": sorted(evaluated),
            "terminal_in_any_fixture": any(e.get("first_failure") == predicate_id for e in entries.values()),
        })
    return out


def build_transcript(entries: dict[str, dict[str, Any]], commit: str, recorded_utc: str) -> dict[str, Any]:
    ordered = [entries[f.fixture_id] for f in all_fixtures()]
    rows = reachability_rows(entries)
    unreachable = unreachable_rows(entries)
    adversarial = []
    for case_id, fixture_id in ADVERSARIAL_CASE_IDS.items():
        e = entries[fixture_id]
        legitimate = e.get("first_failure") is not None and not e.get("rejected")
        adversarial.append({"case_id": case_id, "fixture_id": fixture_id, "description": e["description"],
                            "expected": "accept" if legitimate else "reject", "reference_evaluator_result": "accept" if legitimate else "reject",
                            "accepted": e["accepted"], "rejection": e.get("rejection"), "first_failure": e.get("first_failure"),
                            "measured_count": e.get("measured_count")})
    reached = sum(1 for r in rows if r["status"] == "reached")
    asserted = sum(1 for r in rows if r["status"] == "asserted_unreachable")
    baseline = entries[BASELINE.fixture_id]
    # The attempt fixture of an asserted-unreachable terminal must NOT reach it; its rejection is the evidence.
    accepted = all(e["accepted"] != (f.predicate_id in UNREACHABLE) for f, e in zip(all_fixtures(), ordered))
    result = (reached + asserted == len(PREDICATE_ORDER) and baseline["accepted"] and accepted
              and all(not u["terminal_in_any_fixture"] and u["max_measured_count_across_sweep"] <= 1 for u in unreachable))
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_reference_reachability_transcript",
        "not_the_dispatch_gate_transcript": "reachability-transcript.schema.json requires analyzer and independent-validator results, a provenance entry, and a non-null first failure on all 40 entries; this reference transcript has none of those (AMB-16, AMB-17)",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "harness_commit": commit,
        "recorded_utc": recorded_utc,
        "harness_sources_sha256": _sha256_files(HARNESS_FILES),
        "generator_sha256": _sha256_files(GENERATOR_FILES),
        "evaluator_role": "plan-driven reference evaluator decoding fixture bytes; not the production A4 analyzer",
        "baseline": {"fixture_id": BASELINE.fixture_id, "campaign": baseline.get("campaign"), "first_failure": baseline.get("first_failure"),
                     "derivation_candidate_set_sha256": baseline.get("derivation_candidate_set_sha256"), "models": baseline.get("models"),
                     "charges": baseline.get("charges")},
        "reachable_total": len(PREDICATE_ORDER) - len(UNREACHABLE),
        "reached_count": reached,
        "unreachable_asserted": unreachable,
        "rows": rows,
        "adversarial_cases": adversarial,
        "fixtures": [{k: v for k, v in e.items() if k not in ("models", "stages")} for e in ordered],
        "plan_ambiguities": list(AMBIGUITIES),
        "result": "pass" if result else "fail",
        "scientific_evidence": False,
        "acquisition_authorized": False,
        "capability_advancement_authorized": False,
    }


def run_all(jobs: int) -> dict[str, dict[str, Any]]:
    fixtures = all_fixtures()
    if jobs <= 1:
        return {f.fixture_id: run_fixture(f) for f in fixtures}
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(_run_named, [f.fixture_id for f in fixtures]))
    return {e["fixture_id"]: e for e in results}


def write_outputs(output: Path, transcript: dict[str, Any]) -> dict[str, str]:
    output.mkdir(parents=True, exist_ok=True)
    documents = {TRANSCRIPT_NAME: transcript}
    checksums = {}
    for name, document in documents.items():
        payload = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
        (output / name).write_bytes(payload)
        checksums[name] = {"sha256": sha256_hex(payload), "bytes": len(payload)}
    (output / "checksums.json").write_bytes(canonical_json_bytes(checksums) + b"\n")
    return {k: v["sha256"] for k, v in checksums.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--fixture", action="append", help="run only the named fixture id(s) and print their entries")
    args = parser.parse_args(argv)
    if args.fixture:
        for fixture_id in args.fixture:
            entry = _run_named(fixture_id)
            print(json.dumps({k: v for k, v in entry.items() if k not in ("models", "stages", "ordered_predicates", "mutation")}, indent=2, default=str))
        return 0
    entries = run_all(args.jobs)
    transcript = build_transcript(entries, _git_commit(), datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    checksums = write_outputs(args.output, transcript)
    for row in transcript["rows"]:
        print(f"{row['status']:<22} {row['predicate_id']}")
    for case in transcript["adversarial_cases"]:
        print(f"{'adversarial-ok' if case['accepted'] else 'ADVERSARIAL-FAIL':<22} {case['case_id']} ({case['fixture_id']}): {case['rejection'] or case['first_failure']}")
    print(f"result={transcript['result']} reached={transcript['reached_count']}/{transcript['reachable_total']} plan={PLAN_SHA256[:12]} transcript={checksums[TRANSCRIPT_NAME][:12]}")
    return 0 if transcript["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
