#!/usr/bin/env python3
"""Run the analyzer and the independent validator on one identical on-disk bundle.

The analyzer runs through its public ``build_analysis`` entry point with the
spawned holdout receipt the workflow uses; the validator runs as its own CLI
process. Both read the same bytes; the pair verdict compares their per-layer
terminal predicate ids, models, cross-check transcripts, and 34 predicate
statuses, and requires ``accepted=true`` unless the fixture is declared to be
rejected by the validator's bundle contract by construction.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from protocol_validation import ValidationError, canonical_json_bytes
from a3_analysis import BundleReplicaSource, build_analysis
from a3_dryrun_bundle import BundlePaths, finalize_manifest, run_receipt_process, write_bundle
from a3_generator import SyntheticParameters, SyntheticReplica, generate_replicas
from a3_model import Abort
from a3_spec import BOUNDS, LAYER_KEYS, load_bounded_json

VALIDATOR_SCRIPT = Path(__file__).resolve().parent / "a3_independent_validator.py"
VALIDATOR_TIMEOUT_SECONDS = BOUNDS["fan_in_timeout_seconds"]
TAMPER_SUITE_NOT_APPLICABLE = "tamper_suite_not_executable"
CAMPAIGN_PREDICATES = frozenset({"A3-IDLE-EQUALITY", "A3-SNAPSHOT-RECONSTRUCTION", "A3-RESOURCE-BOUND"})
LAYER_PATHS = {
    "global_map_record": ("global_map", "record"),
    "global_map_conversion_inline": ("global_map", "conversion_inline"),
    "global_map_extended_base": ("global_map", "extended_base"),
    "tdef_pointer_pair": ("tdef", "pointer_pair"),
}


@dataclass(frozen=True)
class LayerView:
    status: str
    terminal_predicate_id: str | None
    model: dict[str, Any] | None
    survivor_count: int

    def document(self) -> dict[str, Any]:
        return {"status": self.status, "terminal_predicate_id": self.terminal_predicate_id, "model": self.model, "derivation_survivor_count": self.survivor_count}


@dataclass(frozen=True)
class AnalyzerResult:
    report: dict[str, Any] | None
    frozen: dict[str, Any] | None
    error: str | None

    def layers(self) -> dict[str, LayerView]:
        if self.report is None:
            return {}
        return {
            name: LayerView(row["status"], row["terminal_predicate_id"], row["model"], row["derivation_survivor_count"])
            for name, row in _report_layers(self.report).items()
        }


@dataclass(frozen=True)
class ValidatorResult:
    verdict: dict[str, Any] | None
    recomputation: dict[str, Any] | None
    exit_code: int
    stderr: str

    @property
    def accepted(self) -> bool:
        return bool(self.verdict and self.verdict.get("accepted") is True)

    @property
    def discrepancy_codes(self) -> list[str]:
        return list(self.verdict.get("discrepancy_codes", [])) if self.verdict else ["no_verdict"]

    def layers(self) -> dict[str, LayerView]:
        if self.recomputation is None:
            return {}
        views = {}
        for name, row in self.recomputation["layers"].items():
            if not row["applicable"]:
                status = "not_applicable"
            elif row["model"] is not None:
                status = "derivation_model"
            else:
                status = "no_outcome"
            views[name] = LayerView(status, row["terminal_predicate_id"], row["model"], row["derivation_survivor_count"])
        return views


@dataclass(frozen=True)
class PairOutcome:
    case_id: str
    analyzer: AnalyzerResult
    validator: ValidatorResult
    expected_validator_rejection: str | None
    disagreements: list[str] = field(default_factory=list)

    @property
    def agreed(self) -> bool:
        return not self.disagreements

    def document(self) -> dict[str, Any]:
        analyzer_layers = {name: view.document() for name, view in self.analyzer.layers().items()}
        validator_layers = {name: view.document() for name, view in self.validator.layers().items()}
        return {
            "case_id": self.case_id,
            "analyzer": {
                "error": self.analyzer.error,
                "terminal_predicate_ids": None if self.analyzer.report is None else self.analyzer.report["terminal_predicate_ids"],
                "layers": analyzer_layers,
                "polarity_cross_check": None if self.analyzer.report is None else self.analyzer.report["polarity_cross_check"],
            },
            "validator": {
                "accepted": self.validator.accepted,
                "exit_code": self.validator.exit_code,
                "discrepancy_codes": self.validator.discrepancy_codes,
                "layers": validator_layers,
                "polarity_cross_check": None if self.validator.recomputation is None else self.validator.recomputation.get("polarity_cross_check"),
                "tamper_results": None if self.validator.verdict is None else self.validator.verdict.get("tamper_results"),
            },
            "expected_validator_rejection": self.expected_validator_rejection,
            "tamper_suite": (
                "executed" if self.validator.accepted
                else "not_applicable_no_decisive_global_model" if tamper_suite_not_applicable(self.analyzer, self.validator)
                else "not_executed"
            ),
            "agreement": self.agreed,
            "disagreements": list(self.disagreements),
        }


def _report_layers(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: report["submodels"][path[0]][path[1]] for name, path in LAYER_PATHS.items()}


def run_analyzer(paths: BundlePaths) -> AnalyzerResult:
    sources = [BundleReplicaSource(path, paths.root) for path in paths.observations]

    def receipt(frozen_sha256: str) -> None:
        run_receipt_process(paths, frozen_sha256)
        document = load_bounded_json(paths.receipt, BOUNDS["max_json_bytes"])
        if document["derivation_candidate_set_sha256"] != frozen_sha256:
            raise ValidationError("spawned receipt is not bound to the frozen set")

    try:
        report = build_analysis(sources, paths.candidate_set, receipt)
    except (Abort, OSError, ValidationError) as exc:
        return AnalyzerResult(None, None, f"{type(exc).__name__}: {exc}")
    paths.report.parent.mkdir(parents=True, exist_ok=True)
    paths.report.write_bytes(canonical_json_bytes(report))
    frozen = load_bounded_json(paths.candidate_set, BOUNDS["max_json_bytes"])
    return AnalyzerResult(report, frozen, None)


def _validator_command(root: Path, output: Path, commit: str, *, recompute_only: bool) -> list[str]:
    command = [sys.executable, "-B", str(VALIDATOR_SCRIPT), "--bundle-root", str(root), "--output", str(output), "--validator-commit", commit]
    if recompute_only:
        command.append("--recompute-only")
    return command


def run_validator(root: Path, commit: str, scratch: Path) -> ValidatorResult:
    """Run the full verdict and, separately, the derivation-only recomputation."""
    scratch.mkdir(parents=True, exist_ok=True)
    verdict_path, recompute_path = scratch / "verdict.json", scratch / "recompute.json"
    try:
        verdict_run = subprocess.run(
            _validator_command(root, verdict_path, commit, recompute_only=False),
            capture_output=True, text=True, timeout=VALIDATOR_TIMEOUT_SECONDS, check=False,
        )
        recompute_run = subprocess.run(
            _validator_command(root, recompute_path, commit, recompute_only=True),
            capture_output=True, text=True, timeout=VALIDATOR_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ValidatorResult(None, None, -1, f"validator process failed: {exc}")
    verdict = json.loads(verdict_path.read_bytes()) if verdict_path.exists() else None
    recomputation = json.loads(recompute_path.read_bytes()) if recompute_run.returncode == 0 and recompute_path.exists() else None
    stderr = (verdict_run.stderr or "") + (recompute_run.stderr or "")
    return ValidatorResult(verdict, recomputation, verdict_run.returncode, stderr[-4000:])


def tamper_suite_not_applicable(analyzer: AnalyzerResult, validator: ValidatorResult) -> bool:
    """T1-T5 mutate a decisive global-record model (independent_validator_contract.tamper_cases);
    without one the suite has no subject. The validator reaches that point only after every
    untampered recomputation, predicate status, and bound check passed, so the pair gate reads
    its ``tamper_suite_not_executable`` as "validated, tamper suite not applicable"."""
    decisive_global = analyzer.frozen is not None and analyzer.frozen["layers"]["global_map_record"]["model"] is not None
    return validator.discrepancy_codes == [TAMPER_SUITE_NOT_APPLICABLE] and not decisive_global


def compare_pair(case_id: str, analyzer: AnalyzerResult, validator: ValidatorResult, expected_rejection: str | None) -> PairOutcome:
    disagreements: list[str] = []
    if analyzer.report is None:
        disagreements.append(f"analyzer produced no report: {analyzer.error}")
        return PairOutcome(case_id, analyzer, validator, expected_rejection, disagreements)
    if expected_rejection is not None:
        if validator.accepted or validator.discrepancy_codes != [expected_rejection]:
            disagreements.append(
                f"validator was expected to reject with {expected_rejection} only; got accepted={validator.accepted} codes={validator.discrepancy_codes}"
            )
        return PairOutcome(case_id, analyzer, validator, expected_rejection, disagreements)
    if not validator.accepted and not tamper_suite_not_applicable(analyzer, validator):
        disagreements.append(f"validator accepted=false codes={validator.discrepancy_codes}")
    if validator.recomputation is None:
        disagreements.append("validator recompute-only produced no document")
        return PairOutcome(case_id, analyzer, validator, expected_rejection, disagreements)
    analyzer_layers, validator_layers = analyzer.layers(), validator.layers()
    for name in LAYER_KEYS:
        left, right = analyzer_layers[name], validator_layers[name]
        if left.terminal_predicate_id != right.terminal_predicate_id and not (
            left.terminal_predicate_id == "A3-HOLDOUT-PREDICTION" and right.terminal_predicate_id is None and right.model == left.model
        ):
            disagreements.append(f"{name}: analyzer terminal {left.terminal_predicate_id} vs validator {right.terminal_predicate_id}")
        if left.model != right.model:
            disagreements.append(f"{name}: analyzer model {left.model} vs validator {right.model}")
        if left.survivor_count != right.survivor_count:
            disagreements.append(f"{name}: analyzer survivor count {left.survivor_count} vs validator {right.survivor_count}")
    if analyzer.report["polarity_cross_check"] != validator.recomputation.get("polarity_cross_check"):
        disagreements.append("polarity_cross_check transcripts differ")
    analyzer_campaign = [item for item in analyzer.report["terminal_predicate_ids"] if item in CAMPAIGN_PREDICATES]
    validator_layers_inapplicable = all(view.status == "not_applicable" for view in validator_layers.values())
    if bool(analyzer_campaign) != validator_layers_inapplicable:
        disagreements.append(f"campaign terminal: analyzer {analyzer_campaign} vs validator layers inapplicable={validator_layers_inapplicable}")
    return PairOutcome(case_id, analyzer, validator, expected_rejection, disagreements)


@dataclass(frozen=True)
class FixtureRun:
    case_id: str
    parameters: SyntheticParameters
    replicas: tuple[SyntheticReplica, ...]
    pair: PairOutcome
    report_sha256: str | None


def run_fixture(
    case_id: str, parameters: SyntheticParameters, workspace: Path, commit: str, created_utc: str,
    *, expected_rejection: str | None = None, keep: bool = False,
) -> FixtureRun:
    """Generate, materialise, analyze, close, and independently validate one fixture."""
    import hashlib

    replicas = generate_replicas(parameters)
    root = workspace / case_id / "bundle"
    if root.parent.exists():
        shutil.rmtree(root.parent)
    paths = write_bundle(root, replicas, f"a3-dryrun-{case_id}", commit)
    analyzer = run_analyzer(paths)
    report_sha256 = None
    if analyzer.report is not None:
        finalize_manifest(paths, analyzer.report, created_utc)
        report_sha256 = hashlib.sha256(paths.report.read_bytes()).hexdigest()
        validator = run_validator(root, commit, workspace / case_id / "validator")
    else:
        validator = ValidatorResult(None, None, -1, "analyzer produced no report; validator not run")
    pair = compare_pair(case_id, analyzer, validator, expected_rejection)
    if not keep:
        shutil.rmtree(root.parent, ignore_errors=True)
    return FixtureRun(case_id, parameters, replicas, pair, report_sha256)
