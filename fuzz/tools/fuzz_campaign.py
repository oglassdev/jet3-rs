#!/usr/bin/env python3
"""Validate and run the repository's format-neutral fuzz campaigns."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TARGET_RE = re.compile(r"^[a-z][a-z0-9_]*$")
REQUIRED_SEED_TEXT = ("id", "path", "purpose", "origin", "generator", "rights", "reproduction_command")
REQUIRED_ENVIRONMENT = ("os", "architecture", "encoding", "line_endings")
RESULTS = {"clean", "crash", "panic", "hang", "sanitizer_finding", "limit_exceeded"}
SANITIZERS = {"address", "memory", "leak", "thread", "undefined", "none"}


class ValidationError(ValueError):
    """A checked fuzz artifact is inconsistent or malformed."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"cannot read valid JSON from {path}: {error}") from error


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{context} must be an object")
    return value


def _exact_keys(value: dict[str, Any], required: set[str], context: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - required
    if missing:
        raise ValidationError(f"{context} missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise ValidationError(f"{context} has unknown fields: {', '.join(sorted(extra))}")


def _positive_int(value: Any, context: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{context} must be an integer >= {minimum}")
    return value


def _repo_path(root: Path, raw: Any, context: str) -> Path:
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
        raise ValidationError(f"{context} must be a repository-relative path without '..'")
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValidationError(f"{context} escapes the repository") from error
    return candidate


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_registry(root: Path) -> dict[str, Any]:
    root = root.resolve()
    registry = _object(_load_json(root / "fuzz/targets.json"), "target registry")
    _exact_keys(registry, {"schema_version", "deterministic_seed", "targets"}, "target registry")
    if registry["schema_version"] != 1:
        raise ValidationError("target registry schema_version must be 1")
    _positive_int(registry["deterministic_seed"], "deterministic_seed", minimum=0)
    targets = registry["targets"]
    if not isinstance(targets, list) or not targets:
        raise ValidationError("target registry must contain at least one target")

    names: set[str] = set()
    required = {
        "name", "source", "corpus", "smoke_seconds", "max_len",
        "max_corpus_bytes", "peak_rss_limit_bytes",
    }
    for index, raw_target in enumerate(targets):
        target = _object(raw_target, f"target[{index}]")
        _exact_keys(target, required, f"target[{index}]")
        name = target["name"]
        if not isinstance(name, str) or not TARGET_RE.fullmatch(name):
            raise ValidationError(f"target[{index}].name is malformed")
        if name in names:
            raise ValidationError(f"duplicate target {name}")
        names.add(name)
        source = _repo_path(root, target["source"], f"{name}.source")
        corpus = _repo_path(root, target["corpus"], f"{name}.corpus")
        if source != (root / f"fuzz/fuzz_targets/{name}.rs").resolve() or not source.is_file():
            raise ValidationError(f"target {name} has no matching fuzz target source")
        if corpus != (root / f"fuzz/corpus/{name}").resolve() or not corpus.is_dir():
            raise ValidationError(f"target {name} has no matching corpus directory")
        _positive_int(target["smoke_seconds"], f"{name}.smoke_seconds", minimum=60)
        _positive_int(target["max_len"], f"{name}.max_len")
        _positive_int(target["max_corpus_bytes"], f"{name}.max_corpus_bytes")
        _positive_int(target["peak_rss_limit_bytes"], f"{name}.peak_rss_limit_bytes")

    cargo = (root / "fuzz/Cargo.toml").read_text(encoding="utf-8")
    cargo_names = set(re.findall(r'(?m)^name = "([a-z][a-z0-9_]*)"$', cargo))
    cargo_names.discard("jet3-fuzz")
    source_names = {path.stem for path in (root / "fuzz/fuzz_targets").glob("*.rs")}
    if names != cargo_names or names != source_names:
        raise ValidationError(
            "registry, Cargo fuzz bins, and fuzz_targets sources disagree: "
            f"registry={sorted(names)}, cargo={sorted(cargo_names)}, sources={sorted(source_names)}"
        )
    return registry


def validate_manifest(root: Path, registry: dict[str, Any]) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / "fuzz/corpus/manifest.json"
    manifest = _object(_load_json(manifest_path), "seed manifest")
    _exact_keys(manifest, {"schema_version", "protocol_version", "seeds"}, "seed manifest")
    if manifest["schema_version"] != 1 or manifest["protocol_version"] != 1:
        raise ValidationError("seed manifest schema_version and protocol_version must be 1")
    seeds = manifest["seeds"]
    if not isinstance(seeds, list) or not seeds:
        raise ValidationError("seed manifest must contain at least one seed")

    targets = {target["name"]: target for target in registry["targets"]}
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    seeds_by_target: dict[str, int] = {name: 0 for name in targets}
    required = set(REQUIRED_SEED_TEXT) | {"size_bytes", "sha256", "environment"}
    for index, raw_seed in enumerate(seeds):
        seed = _object(raw_seed, f"seed[{index}]")
        _exact_keys(seed, required, f"seed[{index}]")
        for field in REQUIRED_SEED_TEXT:
            if not isinstance(seed[field], str) or not seed[field].strip():
                raise ValidationError(f"seed[{index}].{field} must be non-empty text")
        if seed["id"] in seen_ids:
            raise ValidationError(f"duplicate seed id {seed['id']}")
        if seed["path"] in seen_paths:
            raise ValidationError(f"duplicate seed path {seed['path']}")
        seen_ids.add(seed["id"])
        seen_paths.add(seed["path"])
        path = _repo_path(root, seed["path"], f"seed[{index}].path")
        if not path.is_file() or path.is_symlink():
            raise ValidationError(f"seed {seed['id']} is missing or is not a regular file")
        relative = path.relative_to((root / "fuzz/corpus").resolve())
        if len(relative.parts) != 2 or relative.parts[0] not in targets:
            raise ValidationError(f"seed {seed['id']} does not belong to a registered target")
        target_name = relative.parts[0]
        seeds_by_target[target_name] += 1
        size = _positive_int(seed["size_bytes"], f"{seed['id']}.size_bytes", minimum=0)
        if path.stat().st_size != size:
            raise ValidationError(f"seed {seed['id']} size drift")
        if not isinstance(seed["sha256"], str) or not SHA256_RE.fullmatch(seed["sha256"]):
            raise ValidationError(f"seed {seed['id']} has malformed sha256")
        if sha256(path) != seed["sha256"]:
            raise ValidationError(f"seed {seed['id']} hash drift")
        environment = _object(seed["environment"], f"{seed['id']}.environment")
        _exact_keys(environment, set(REQUIRED_ENVIRONMENT), f"{seed['id']}.environment")
        for field in REQUIRED_ENVIRONMENT:
            if not isinstance(environment[field], str) or not environment[field].strip():
                raise ValidationError(f"{seed['id']}.environment.{field} must be non-empty text")
        if seed["path"] not in seed["reproduction_command"]:
            raise ValidationError(f"seed {seed['id']} reproduction command does not name its path")

    corpus_entries = [
        path
        for target in targets.values()
        for path in _repo_path(root, target["corpus"], "corpus").rglob("*")
    ]
    invalid_entries = [path for path in corpus_entries if not path.is_file() or path.is_symlink()]
    if invalid_entries:
        raise ValidationError(
            "corpora may contain only regular seed files: "
            + ", ".join(path.relative_to(root).as_posix() for path in invalid_entries)
        )
    disk_paths = {path.relative_to(root).as_posix() for path in corpus_entries}
    if disk_paths != seen_paths:
        missing = sorted(disk_paths - seen_paths)
        stale = sorted(seen_paths - disk_paths)
        raise ValidationError(f"seed manifest/file mismatch: unmanifested={missing}, missing={stale}")
    for name, count in seeds_by_target.items():
        if count == 0:
            raise ValidationError(f"registered target {name} has no checked seeds")
        total = sum(
            seed["size_bytes"] for seed in seeds
            if Path(seed["path"]).parts[-2] == name
        )
        if total > targets[name]["max_corpus_bytes"]:
            raise ValidationError(f"target {name} corpus exceeds its checked byte bound")
    return manifest


def validate_repository(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    registry = load_registry(root)
    return registry, validate_manifest(root, registry)


def _git(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args], cwd=root, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if process.returncode:
        raise ValidationError(f"git {' '.join(args)} failed: {process.stderr.strip()}")
    return process.stdout.strip()


def _date_time(value: Any, context: str) -> datetime.datetime:
    if not isinstance(value, str):
        raise ValidationError(f"{context} must be a date-time string")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError(f"{context} is not an ISO 8601 date-time") from error
    if parsed.tzinfo is None:
        raise ValidationError(f"{context} must include a timezone")
    return parsed


def validate_report(root: Path, report_path: Path) -> None:
    registry, manifest = validate_repository(root)
    report = _object(_load_json(report_path), "campaign report")
    required = {
        "schema_version", "commit", "target", "target_registry_sha256",
        "target_source_sha256", "corpus", "campaign", "result", "limits", "observed",
    }
    _exact_keys(report, required, "campaign report")
    if report["schema_version"] != 1:
        raise ValidationError("campaign report schema_version must be 1")

    commit = _object(report["commit"], "commit")
    _exact_keys(commit, {"sha", "dirty"}, "commit")
    if not isinstance(commit["sha"], str) or not COMMIT_RE.fullmatch(commit["sha"]):
        raise ValidationError("commit.sha must be a lowercase 40-character hexadecimal SHA")
    if not isinstance(commit["dirty"], bool):
        raise ValidationError("commit.dirty must be boolean")
    actual_sha = _git(root, "rev-parse", "HEAD")
    actual_dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=all"))
    if commit["sha"] != actual_sha or commit["dirty"] != actual_dirty:
        raise ValidationError("campaign report commit or dirty state is stale")

    targets = {target["name"]: target for target in registry["targets"]}
    if report["target"] not in targets:
        raise ValidationError(f"campaign report names unknown target {report['target']!r}")
    target = targets[report["target"]]
    if report["target_registry_sha256"] != sha256(root / "fuzz/targets.json"):
        raise ValidationError("campaign report target registry hash is stale")
    target_source = _repo_path(root, target["source"], "target.source")
    if report["target_source_sha256"] != sha256(target_source):
        raise ValidationError("campaign report target source hash is stale")

    corpus = _object(report["corpus"], "corpus")
    _exact_keys(corpus, {"manifest_sha256", "seeds"}, "corpus")
    expected_manifest_hash = sha256(root / "fuzz/corpus/manifest.json")
    if corpus["manifest_sha256"] != expected_manifest_hash:
        raise ValidationError("campaign report seed manifest hash is stale")
    expected_seeds = [
        {"id": seed["id"], "path": seed["path"], "sha256": seed["sha256"]}
        for seed in manifest["seeds"]
        if Path(seed["path"]).parts[-2] == report["target"]
    ]
    if corpus["seeds"] != expected_seeds:
        raise ValidationError("campaign report corpus hashes do not exactly match checked seeds")

    campaign = _object(report["campaign"], "campaign")
    campaign_fields = {
        "duration_seconds", "runs", "kind", "deterministic_seed", "sanitizer",
        "started_at", "finished_at",
    }
    _exact_keys(campaign, campaign_fields, "campaign")
    duration = _positive_int(campaign["duration_seconds"], "campaign.duration_seconds")
    _positive_int(campaign["runs"], "campaign.runs")
    if campaign["kind"] == "smoke":
        minimum_duration = target["smoke_seconds"]
    elif campaign["kind"] == "full":
        minimum_duration = 600
    else:
        raise ValidationError("campaign.kind must be smoke or full")
    if duration < minimum_duration:
        raise ValidationError(
            f"{campaign['kind']} campaign duration must be at least {minimum_duration} seconds"
        )
    _positive_int(campaign["deterministic_seed"], "campaign.deterministic_seed", minimum=0)
    if campaign["deterministic_seed"] != registry["deterministic_seed"]:
        raise ValidationError("campaign deterministic seed differs from target registry")
    if not isinstance(campaign["sanitizer"], str) or campaign["sanitizer"] not in SANITIZERS:
        raise ValidationError("campaign.sanitizer is unsupported")
    started = _date_time(campaign["started_at"], "campaign.started_at")
    finished = _date_time(campaign["finished_at"], "campaign.finished_at")
    if finished < started:
        raise ValidationError("campaign.finished_at precedes started_at")
    if not isinstance(report["result"], str) or report["result"] not in RESULTS:
        raise ValidationError("campaign result is unsupported")

    limits = _object(report["limits"], "limits")
    observed = _object(report["observed"], "observed")
    _exact_keys(limits, {"wall_clock_seconds", "peak_rss_bytes"}, "limits")
    _exact_keys(observed, {"wall_clock_seconds", "peak_rss_bytes"}, "observed")
    wall_limit = limits["wall_clock_seconds"]
    wall_observed = observed["wall_clock_seconds"]
    if (
        isinstance(wall_limit, bool)
        or not isinstance(wall_limit, (int, float))
        or not math.isfinite(wall_limit)
        or wall_limit <= 0
    ):
        raise ValidationError("limits.wall_clock_seconds must be positive")
    if (
        isinstance(wall_observed, bool)
        or not isinstance(wall_observed, (int, float))
        or not math.isfinite(wall_observed)
        or wall_observed < 0
    ):
        raise ValidationError("observed.wall_clock_seconds must be non-negative")
    rss_limit = _positive_int(limits["peak_rss_bytes"], "limits.peak_rss_bytes")
    rss_observed = _positive_int(observed["peak_rss_bytes"], "observed.peak_rss_bytes", minimum=0)
    if rss_limit != target["peak_rss_limit_bytes"]:
        raise ValidationError("campaign report RSS limit differs from target registry")
    if wall_observed > wall_limit:
        raise ValidationError("campaign exceeded its wall-clock limit")
    if rss_observed > rss_limit:
        raise ValidationError("campaign exceeded its peak-RSS limit")
    if report["result"] == "clean" and wall_observed < campaign["duration_seconds"]:
        raise ValidationError("clean campaign ended before its recorded duration")


def run_smoke(root: Path, cargo_fuzz: str) -> None:
    registry, manifest = validate_repository(root)
    seeds_by_target = {
        target["name"]: [
            seed for seed in manifest["seeds"]
            if Path(seed["path"]).parts[-2] == target["name"]
        ]
        for target in registry["targets"]
    }
    with tempfile.TemporaryDirectory(prefix="access97-rs-fuzz-") as temporary:
        temporary_root = Path(temporary)
        for target in registry["targets"]:
            corpus = temporary_root / target["name"]
            corpus.mkdir()
            for seed in seeds_by_target[target["name"]]:
                shutil.copyfile(root / seed["path"], corpus / Path(seed["path"]).name)
            command = [
                cargo_fuzz, "fuzz", "run", "--fuzz-dir", "fuzz", target["name"], str(corpus), "--",
                f"-max_total_time={target['smoke_seconds']}",
                f"-seed={registry['deterministic_seed']}",
                f"-max_len={target['max_len']}",
                f"-rss_limit_mb={target['peak_rss_limit_bytes'] // (1024 * 1024)}",
            ]
            print(f"running {target['name']} for {target['smoke_seconds']} seconds", flush=True)
            subprocess.run(command, cwd=root, check=True, timeout=target["smoke_seconds"] + 90)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    report_parser = subparsers.add_parser("validate-report")
    report_parser.add_argument("report", type=Path)
    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--cargo", default="cargo")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "validate":
            validate_repository(root)
        elif args.command == "validate-report":
            validate_report(root, args.report.resolve())
        else:
            run_smoke(root, args.cargo)
    except (ValidationError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
