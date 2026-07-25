#!/usr/bin/env python3
"""Exact checked-plan and worker-invocation contracts for DAO M3."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from m1_bundle_validation import bounded_file_identity
from protocol_validation import ValidationError, lint_schema, validate_schema_value
from validate_m1_protocol import validate_document

HERE = Path(__file__).resolve().parent
ORACLE = HERE.parent
REPOSITORY = ORACLE.parent.parent
M3 = ORACLE / "experiments" / "m3"
PLAN_SCHEMA = M3 / "plan.schema.json"
CHECKED_PLAN = M3 / "m3-index-isolation.plan.json"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_DATABASE_BYTES = 1024 * 1024
MAX_TOTAL_DATABASE_BYTES = 9 * 1024 * 1024
REPOSITORY_URL = "https://github.com/oglassdev/jet3-rs.git"
EXPECTED_ORDER = ("E", "B", "I", "B", "I", "E", "I", "E", "B")
EXPECTED_CONDITIONS = {
    "B": (
        "DAO-GEN-TEXT8-BASELINE-001",
        "oracle/windows-dao/examples/DAO-GEN-TEXT8-BASELINE-001.scenario.json",
        "7815cbf40b79f55e87dc90841fa2a09a0ec6a6c9a8e667e6cbb7678bfe7665c6",
    ),
    "E": (
        "DAO-GEN-EMPTY-REPEAT-A",
        "oracle/windows-dao/examples/DAO-GEN-EMPTY-REPEAT-A.scenario.json",
        "4f8e90cc3df76ca898ce85cab24978baa858f9a275e67006c5b15965bba29be6",
    ),
    "I": (
        "DAO-GEN-TEXT8-INDEXED-001",
        "oracle/windows-dao/examples/DAO-GEN-TEXT8-INDEXED-001.scenario.json",
        "46d5be32bba4fa73cf3620a4cc7205e636b0b2a866815587c72324df3cd6e84b",
    ),
}
JsonLoader = Callable[[Path], Any]


def worker_run_id(launch_ordinal: int, campaign_run_id: str) -> str:
    return campaign_run_id[:16] + f"-m3-w{launch_ordinal:02d}"


def validate_plan(document: dict[str, Any], load_json: JsonLoader) -> None:
    schema = load_json(PLAN_SCHEMA)
    lint_schema(schema)
    validate_schema_value(document, schema, schema, "$")
    if document["bounds"] != {
        "max_database_bytes": MAX_DATABASE_BYTES,
        "max_total_database_bytes": MAX_TOTAL_DATABASE_BYTES,
        "max_analysis_bytes": MAX_JSON_BYTES,
        "max_pages_per_sample": 512,
        "worker_timeout_seconds": 120,
    }:
        raise ValidationError("$.bounds: differs from the reviewed M3 ceiling")
    if document["repository_url"] != REPOSITORY_URL:
        raise ValidationError("$.repository_url: checked private repository differs")
    conditions = {item["condition_id"]: item for item in document["conditions"]}
    if list(conditions) != ["B", "E", "I"]:
        raise ValidationError("$.conditions: must use canonical B, E, I order")
    for condition_id, expected in EXPECTED_CONDITIONS.items():
        item = conditions.get(condition_id)
        if item is None or (
            item["scenario_id"],
            item["scenario_path"],
            item["scenario_sha256"],
        ) != expected:
            raise ValidationError(f"$.conditions[{condition_id}]: checked identity differs")
        path = REPOSITORY / item["scenario_path"]
        _, digest, _ = bounded_file_identity(path, 1024 * 1024)
        if digest != item["scenario_sha256"]:
            raise ValidationError(f"{path}: checked scenario hash differs")
    samples = document["samples"]
    if tuple(item["condition_id"] for item in samples) != EXPECTED_ORDER:
        raise ValidationError("$.samples: cyclic launch order differs")
    if [item["launch_ordinal"] for item in samples] != list(range(1, 10)):
        raise ValidationError("$.samples: launch ordinals must be one through nine")
    ids = [item["sample_id"] for item in samples]
    if len(set(ids)) != 9:
        raise ValidationError("$.samples: sample IDs must be unique")
    for condition_id in ("B", "E", "I"):
        replicas = sorted(
            item["replica"] for item in samples if item["condition_id"] == condition_id
        )
        if replicas != [1, 2, 3]:
            raise ValidationError(f"$.samples: {condition_id} replicas differ")
    comparisons = document["comparisons"]
    if len({item["comparison_id"] for item in comparisons}) != 18:
        raise ValidationError("$.comparisons: comparison IDs must be unique")
    sample_map = {item["sample_id"]: item for item in samples}
    counts = [0, 0, 0]
    seen_pairs: set[tuple[str, str]] = set()
    for item in comparisons:
        left = sample_map[item["left_sample_id"]]
        right = sample_map[item["right_sample_id"]]
        pair = (left["sample_id"], right["sample_id"])
        if pair in seen_pairs:
            raise ValidationError("$.comparisons: duplicate directional pair")
        seen_pairs.add(pair)
        if item["kind"] == "within_condition":
            if left["condition_id"] != right["condition_id"] or item["paired"]:
                raise ValidationError("$.comparisons: invalid within-condition pair")
            counts[0] += 1
        else:
            if (left["condition_id"], right["condition_id"]) != ("B", "I"):
                raise ValidationError("$.comparisons: B/I direction differs")
            counts[1] += 1
            if item["paired"]:
                if left["replica"] != right["replica"]:
                    raise ValidationError("$.comparisons: paired replicas differ")
                counts[2] += 1
    if tuple(counts) != (9, 9, 3):
        raise ValidationError("$.comparisons: expected 9 within, 9 B/I, 3 paired")
    if document != load_json(CHECKED_PLAN):
        raise ValidationError("M3 plan differs from the exact checked campaign")


def validate_invocation(
    document: Any,
    invocation_path: Path,
    load_json: JsonLoader,
    retained_environment_path: Path | None = None,
) -> None:
    required = {
        "block", "campaign_run_id", "condition_id", "environment_path",
        "environment_sha256", "git_commit", "launch_nonce", "launch_ordinal",
        "output_root", "plan_path", "plan_sha256", "remote_ref", "replica",
        "repository_root", "repository_url", "result_path", "run_id",
        "sample_id", "scenario_id", "scenario_path", "scenario_sha256",
        "stage_root", "working_path",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ValidationError("M3 invocation keys differ")
    if (
        not isinstance(document["git_commit"], str)
        or len(document["git_commit"]) != 40
        or any(character not in "0123456789abcdef" for character in document["git_commit"])
        or document["repository_url"] != REPOSITORY_URL
    ):
        raise ValidationError("M3 invocation repository binding differs")
    try:
        uuid.UUID(document["launch_nonce"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("M3 invocation launch nonce is malformed") from exc
    repository = Path(document["repository_root"]).resolve()
    plan_path = Path(document["plan_path"]).resolve()
    if plan_path != (repository / CHECKED_PLAN.relative_to(REPOSITORY)).resolve():
        raise ValidationError("M3 invocation plan path differs")
    plan = load_json(plan_path)
    validate_plan(plan, load_json)
    _, plan_hash, _ = bounded_file_identity(plan_path, MAX_JSON_BYTES)
    if (
        plan_hash != document["plan_sha256"]
        or document["remote_ref"] != plan["remote_ref"]
        or document["repository_url"] != plan["repository_url"]
    ):
        raise ValidationError("M3 invocation plan identity differs")
    matches = [item for item in plan["samples"] if item["sample_id"] == document["sample_id"]]
    if len(matches) != 1:
        raise ValidationError("M3 invocation sample is not checked")
    sample = matches[0]
    for key in ("block", "condition_id", "launch_ordinal", "replica", "sample_id"):
        if document[key] != sample[key]:
            raise ValidationError(f"M3 invocation sample {key} differs")
    condition = next(
        item for item in plan["conditions"] if item["condition_id"] == sample["condition_id"]
    )
    scenario_path = (repository / condition["scenario_path"]).resolve()
    if (
        Path(document["scenario_path"]).resolve() != scenario_path
        or document["scenario_id"] != condition["scenario_id"]
        or document["scenario_sha256"] != condition["scenario_sha256"]
    ):
        raise ValidationError("M3 invocation scenario binding differs")
    _, scenario_hash, _ = bounded_file_identity(scenario_path, MAX_JSON_BYTES)
    if scenario_hash != document["scenario_sha256"]:
        raise ValidationError("M3 invocation scenario hash differs")
    environment_path = (
        retained_environment_path.resolve()
        if retained_environment_path is not None
        else Path(document["environment_path"]).resolve()
    )
    _, environment_hash, _ = bounded_file_identity(environment_path, MAX_JSON_BYTES)
    if environment_hash != document["environment_sha256"]:
        raise ValidationError("M3 invocation environment hash differs")
    environment = load_json(environment_path)
    if validate_document(environment) != "dao_environment" or environment["status"] != "ready":
        raise ValidationError("M3 invocation environment is not ready protocol-1.1")
    if document["run_id"] != worker_run_id(
        document["launch_ordinal"], document["campaign_run_id"]
    ):
        raise ValidationError("M3 invocation worker run ID differs")
    if retained_environment_path is None:
        stage = Path(document["stage_root"]).resolve()
        working = Path(document["working_path"]).resolve()
        result = Path(document["result_path"]).resolve()
        try:
            working.relative_to(stage)
            result.relative_to(working)
            invocation_path.resolve().relative_to(stage)
            Path(document["output_root"]).resolve().relative_to(stage)
        except ValueError as exc:
            raise ValidationError("M3 invocation private paths escape staging") from exc
        if result != working / "result.json":
            raise ValidationError("M3 invocation result path differs")
