#!/usr/bin/env python3
"""Validate and run the repository's format-neutral fuzz campaigns."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fuzz_build_identity import (
    BASE_ENVIRONMENT_KEYS,
    WINDOWS_ENVIRONMENT_KEYS,
    copy_seeds,
    prepare_isolated_fuzz_build,
    require_external_output,
    require_clean_snapshot,
    validate_build_manifest,
)
from fuzz_evidence import (
    EvidenceError,
    classify_result,
    exact_process_environment,
    observe_producer,
    parse_date_time,
    parse_reported_rss,
    parse_runs,
    publish_directory,
    sha256,
    tool_identity,
    write_json,
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TARGET_RE = re.compile(r"^[a-z][a-z0-9_]*$")
REQUIRED_SEED_TEXT = ("id", "path", "purpose", "origin", "generator", "rights", "reproduction_command")
REQUIRED_ENVIRONMENT = ("os", "architecture", "encoding", "line_endings")
RESULTS = {"clean", "crash", "panic", "hang", "sanitizer_finding", "limit_exceeded"}
SANITIZERS = {"address", "memory", "leak", "thread", "undefined", "none"}
DEFAULT_SMOKE_JOBS = min(4, os.cpu_count() or 1)


ValidationError = EvidenceError


def fuzz_target_runtime_command(
    executable_path: str,
    corpus: Path | str,
    duration_seconds: int,
    deterministic_seed: int,
    max_len: int,
    peak_rss_limit_bytes: int,
    artifacts: Path | str,
) -> list[str]:
    """Construct the canonical direct libFuzzer target invocation.

    Crash, timeout, and out-of-memory artifacts are routed into the external
    campaign directory so a finding never dirties the clean Git checkout.
    """
    return [
        executable_path,
        str(corpus),
        f"-max_total_time={duration_seconds}",
        f"-seed={deterministic_seed}",
        f"-max_len={max_len}",
        f"-rss_limit_mb={peak_rss_limit_bytes // (1024 * 1024)}",
        f"-artifact_prefix={artifacts}/",
    ]


def validate_fuzz_target_runtime_command(
    command: Any,
    target_directory: str,
    duration_seconds: int,
    deterministic_seed: int,
    max_len: int,
    peak_rss_limit_bytes: int,
) -> None:
    """Require the exact direct target path, corpus, and libFuzzer limits."""
    if not isinstance(command, list) or not command or any(
        not isinstance(argument, str) or not argument for argument in command
    ):
        raise ValidationError("observer command must be a non-empty string array")
    if len(command) < 2:
        raise ValidationError(
            "observer command is not the canonical direct fuzz-target invocation"
        )
    campaign_directory = Path(target_directory).resolve().parent
    if Path(command[1]).resolve() != campaign_directory / "corpus":
        raise ValidationError("observer command does not name the disposable corpus")
    expected_command = fuzz_target_runtime_command(
        command[0],
        command[1],
        duration_seconds,
        deterministic_seed,
        max_len,
        peak_rss_limit_bytes,
        campaign_directory / "artifacts",
    )
    if command != expected_command:
        raise ValidationError(
            "observer command is not the canonical direct fuzz-target invocation"
        )


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


def _bundle_file(bundle: Path, raw: Any, context: str) -> Path:
    if not isinstance(raw, str) or not raw or Path(raw).parts != (raw,):
        raise ValidationError(f"{context} must be a single relative file name")
    path = bundle / raw
    if not path.is_file() or path.is_symlink():
        raise ValidationError(f"{context} is missing, a symlink, or not a regular file")
    return path


def _sha256_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValidationError(f"{context} must be a lowercase SHA-256")
    return value


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{context} must be non-empty text")
    return value


def _validate_tool(tool: Any, context: str) -> dict[str, Any]:
    value = _object(tool, context)
    _exact_keys(value, {"path", "sha256", "version"}, context)
    _text(value["path"], f"{context}.path")
    _sha256_text(value["sha256"], f"{context}.sha256")
    _text(value["version"], f"{context}.version")
    return value


def _validate_observer(
    observer_path: Path,
    producer_log: Path,
    report: dict[str, Any],
    target: dict[str, Any],
    deterministic_seed: int,
) -> dict[str, Any]:
    observer = _object(_load_json(observer_path), "observer record")
    observer_fields = {
        "schema_version", "producer_log_sha256", "command", "started_at", "finished_at",
        "wall_clock_seconds", "peak_rss_bytes", "runs", "result", "exit_code",
        "timed_out", "toolchain", "build_environment", "executable",
    }
    _exact_keys(observer, observer_fields, "observer record")
    if observer["schema_version"] != 1:
        raise ValidationError("observer record schema_version must be 1")
    if observer["producer_log_sha256"] != sha256(producer_log):
        raise ValidationError("observer record producer-log hash is stale")
    try:
        log_text = producer_log.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValidationError(f"producer log is not valid UTF-8: {error}") from error
    if re.search(r"\bCompiling\b", log_text):
        raise ValidationError(
            "runtime producer rebuilt code after the retained isolated prebuild"
        )

    command = observer["command"]
    validate_fuzz_target_runtime_command(
        command,
        observer["build_environment"]["CARGO_TARGET_DIR"],
        report["campaign"]["duration_seconds"],
        deterministic_seed,
        target["max_len"],
        target["peak_rss_limit_bytes"],
    )

    started = parse_date_time(observer["started_at"], "observer.started_at")
    finished = parse_date_time(observer["finished_at"], "observer.finished_at")
    if finished < started:
        raise ValidationError("observer.finished_at precedes observer.started_at")
    wall = observer["wall_clock_seconds"]
    if (
        isinstance(wall, bool)
        or not isinstance(wall, (int, float))
        or not math.isfinite(wall)
        or wall < 0
    ):
        raise ValidationError("observer.wall_clock_seconds must be non-negative")
    elapsed = (finished - started).total_seconds()
    if abs(elapsed - wall) > max(1.0, wall * 0.02):
        raise ValidationError("observer timestamps and monotonic wall clock disagree")
    rss = _positive_int(observer["peak_rss_bytes"], "observer.peak_rss_bytes")
    if rss < parse_reported_rss(log_text):
        raise ValidationError("observer peak RSS is below producer-reported RSS")
    runs = _positive_int(observer["runs"], "observer.runs")
    if runs != parse_runs(log_text):
        raise ValidationError("observer run count disagrees with producer log")
    if not isinstance(observer["timed_out"], bool):
        raise ValidationError("observer.timed_out must be boolean")
    exit_code = observer["exit_code"]
    if exit_code is not None and (
        isinstance(exit_code, bool) or not isinstance(exit_code, int)
    ):
        raise ValidationError("observer.exit_code must be an integer or null")
    if observer["timed_out"] != (exit_code is None):
        raise ValidationError("observer timeout and exit-code fields disagree")
    result = _text(observer["result"], "observer.result")
    if result not in RESULTS or result != classify_result(
        log_text, exit_code, observer["timed_out"]
    ):
        raise ValidationError("observer result disagrees with producer output and exit status")

    toolchain = _object(observer["toolchain"], "observer.toolchain")
    _exact_keys(toolchain, {"cargo", "cargo_fuzz", "rustc"}, "observer.toolchain")
    cargo = _validate_tool(toolchain["cargo"], "observer.toolchain.cargo")
    _validate_tool(toolchain["cargo_fuzz"], "observer.toolchain.cargo_fuzz")
    _validate_tool(toolchain["rustc"], "observer.toolchain.rustc")
    build_environment = _object(
        observer["build_environment"], "observer.build_environment"
    )
    _exact_keys(
        build_environment,
        BASE_ENVIRONMENT_KEYS | (
            WINDOWS_ENVIRONMENT_KEYS if os.name == "nt" else set()
        ),
        "observer.build_environment",
    )
    if any(
        not isinstance(value, str) or not value
        for value in build_environment.values()
    ):
        raise ValidationError("observer build environment values must be non-empty text")
    if Path(build_environment["CARGO"]).resolve().as_posix() != Path(
        cargo["path"]
    ).resolve().as_posix():
        raise ValidationError("observer cargo identity disagrees with build environment")
    executable = _object(observer["executable"], "observer.executable")
    _exact_keys(executable, {"path", "sha256"}, "observer.executable")
    executable_path = _text(executable["path"], "observer.executable.path")
    _sha256_text(executable["sha256"], "observer.executable.sha256")
    if (
        Path(executable_path).resolve().as_posix()
        != Path(command[0]).resolve().as_posix()
    ):
        raise ValidationError("observer executable identity disagrees with producer command")

    producer = _object(report["producer"], "producer")
    copied_identity = {
        "command": command,
        "toolchain": toolchain,
        "build_environment": build_environment,
        "executable": executable,
    }
    for field, expected in copied_identity.items():
        if producer[field] != expected:
            raise ValidationError(f"campaign report producer.{field} disagrees with observer")
    observed = report["observed"]
    expected_observed = {
        "wall_clock_seconds": wall,
        "peak_rss_bytes": rss,
        "runs": runs,
        "started_at": observer["started_at"],
        "finished_at": observer["finished_at"],
        "exit_code": exit_code,
    }
    if observed != expected_observed:
        raise ValidationError("campaign report observations disagree with observer record")
    if report["result"] != result:
        raise ValidationError("campaign report result disagrees with observer record")
    return observer


def validate_report(root: Path, report_path: Path) -> None:
    registry, manifest = validate_repository(root)
    report = _object(_load_json(report_path), "campaign report")
    required = {
        "schema_version", "commit", "target", "target_registry_sha256",
        "target_source_sha256", "corpus", "campaign", "result", "limits", "observed",
        "producer",
    }
    _exact_keys(report, required, "campaign report")
    if report["schema_version"] != 3:
        raise ValidationError(
            "campaign report schema_version must be 3; dirty or build-unbound wrappers are not evidence"
        )

    commit = _object(report["commit"], "commit")
    _exact_keys(commit, {"sha", "tree", "clean"}, "commit")
    if not isinstance(commit["sha"], str) or not COMMIT_RE.fullmatch(commit["sha"]):
        raise ValidationError("commit.sha must be a lowercase 40-character hexadecimal SHA")
    if not isinstance(commit["tree"], str) or not COMMIT_RE.fullmatch(commit["tree"]):
        raise ValidationError("commit.tree must be a lowercase 40-character hexadecimal SHA")
    if commit["clean"] is not True or commit != require_clean_snapshot(root):
        raise ValidationError("campaign report is not bound to the current clean Git tree")

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
        "duration_seconds", "kind", "deterministic_seed", "sanitizer",
    }
    _exact_keys(campaign, campaign_fields, "campaign")
    duration = _positive_int(campaign["duration_seconds"], "campaign.duration_seconds")
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
    if not isinstance(report["result"], str) or report["result"] not in RESULTS:
        raise ValidationError("campaign result is unsupported")

    limits = _object(report["limits"], "limits")
    observed = _object(report["observed"], "observed")
    _exact_keys(limits, {"wall_clock_seconds", "peak_rss_bytes"}, "limits")
    _exact_keys(
        observed,
        {
            "wall_clock_seconds", "peak_rss_bytes", "runs", "started_at", "finished_at",
            "exit_code",
        },
        "observed",
    )
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
    if wall_limit != duration + 90:
        raise ValidationError("campaign report wall-clock limit is not canonical")
    if rss_observed > rss_limit:
        raise ValidationError(
            "campaign exceeded its peak-RSS limit: "
            f"observed {rss_observed} bytes > {rss_limit} bytes"
        )
    if report["result"] == "clean" and wall_observed < campaign["duration_seconds"]:
        raise ValidationError("clean campaign ended before its recorded duration")

    producer = _object(report["producer"], "producer")
    _exact_keys(
        producer,
        {
            "log", "observer", "build_manifest", "cargo_metadata", "command",
            "toolchain", "build_environment", "executable",
        },
        "producer",
    )
    log_ref = _object(producer["log"], "producer.log")
    observer_ref = _object(producer["observer"], "producer.observer")
    build_ref = _object(producer["build_manifest"], "producer.build_manifest")
    metadata_ref = _object(producer["cargo_metadata"], "producer.cargo_metadata")
    _exact_keys(log_ref, {"path", "sha256"}, "producer.log")
    _exact_keys(observer_ref, {"path", "sha256"}, "producer.observer")
    _exact_keys(build_ref, {"path", "sha256"}, "producer.build_manifest")
    _exact_keys(metadata_ref, {"path", "sha256"}, "producer.cargo_metadata")
    bundle = report_path.resolve().parent
    producer_log = _bundle_file(bundle, log_ref["path"], "producer.log.path")
    observer_path = _bundle_file(bundle, observer_ref["path"], "producer.observer.path")
    build_path = _bundle_file(
        bundle, build_ref["path"], "producer.build_manifest.path"
    )
    metadata_path = _bundle_file(
        bundle, metadata_ref["path"], "producer.cargo_metadata.path"
    )
    if _sha256_text(log_ref["sha256"], "producer.log.sha256") != sha256(producer_log):
        raise ValidationError("campaign report producer-log hash is stale")
    if _sha256_text(
        observer_ref["sha256"], "producer.observer.sha256"
    ) != sha256(observer_path):
        raise ValidationError("campaign report observer-record hash is stale")
    if _sha256_text(
        build_ref["sha256"], "producer.build_manifest.sha256"
    ) != sha256(build_path):
        raise ValidationError("campaign report build-manifest hash is stale")
    if _sha256_text(
        metadata_ref["sha256"], "producer.cargo_metadata.sha256"
    ) != sha256(metadata_path):
        raise ValidationError("campaign report Cargo-metadata hash is stale")
    observer = _validate_observer(
        observer_path,
        producer_log,
        report,
        target,
        registry["deterministic_seed"],
    )
    validate_build_manifest(
        root, build_path, metadata_path, report, observer
    )


def run_campaign(
    root: Path,
    target_name: str,
    kind: str,
    sanitizer: str,
    cargo: str,
    cargo_fuzz: str,
    output: Path,
) -> str:
    snapshot = require_clean_snapshot(root)
    registry, manifest = validate_repository(root)
    targets = {target["name"]: target for target in registry["targets"]}
    if target_name not in targets:
        raise ValidationError(f"unknown fuzz target: {target_name}")
    if kind not in {"smoke", "full"}:
        raise ValidationError("campaign kind must be smoke or full")
    if sanitizer not in SANITIZERS:
        raise ValidationError("unsupported sanitizer")
    target = targets[target_name]
    duration = target["smoke_seconds"] if kind == "smoke" else 600
    output = require_external_output(root, output)
    cargo_identity = tool_identity(cargo, ["--version", "--verbose"])
    cargo_fuzz_identity = tool_identity(cargo_fuzz, ["--version"])
    rustc_identity = tool_identity(os.environ.get("RUSTC", "rustc"), ["-vV"])
    toolchain = {
        "cargo": cargo_identity,
        "cargo_fuzz": cargo_fuzz_identity,
        "rustc": rustc_identity,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise ValidationError(f"refusing to replace existing evidence path: {output}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        build = prepare_isolated_fuzz_build(
            root,
            temporary,
            snapshot,
            toolchain,
            target_name,
            sanitizer,
            Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")),
        )
        build_environment = build.environment
        prebuild = build.prebuild
        build_path = build.manifest_path
        metadata_path = build.metadata_path
        target_directory = build.target_directory
        corpus = temporary / "corpus"
        seeds = copy_seeds(root, corpus, manifest, target_name)
        artifacts = temporary / "artifacts"
        artifacts.mkdir()
        log_path = temporary / "producer.log"
        command = fuzz_target_runtime_command(
            prebuild["executable"]["path"],
            corpus,
            duration,
            registry["deterministic_seed"],
            target["max_len"],
            target["peak_rss_limit_bytes"],
            artifacts,
        )
        observer = observe_producer(
            root,
            log_path,
            command,
            duration + 90,
            toolchain,
            build_environment,
        )
        if observer["executable"] != prebuild["executable"]:
            raise ValidationError(
                "runtime executable identity disagrees with the isolated prebuild"
            )
        if require_clean_snapshot(root) != snapshot:
            raise ValidationError("clean Git snapshot changed during fuzz campaign")
        shutil.rmtree(corpus)
        shutil.rmtree(target_directory)
        shutil.rmtree(Path(build_environment["TMPDIR"]))
        observer_path = temporary / "observer.json"
        write_json(observer_path, observer)
        report = {
            "schema_version": 3,
            "commit": snapshot,
            "target": target_name,
            "target_registry_sha256": sha256(root / "fuzz/targets.json"),
            "target_source_sha256": sha256(_repo_path(root, target["source"], "target.source")),
            "corpus": {
                "manifest_sha256": sha256(root / "fuzz/corpus/manifest.json"),
                "seeds": seeds,
            },
            "campaign": {
                "duration_seconds": duration,
                "kind": kind,
                "deterministic_seed": registry["deterministic_seed"],
                "sanitizer": sanitizer,
            },
            "result": observer["result"],
            "limits": {
                "wall_clock_seconds": duration + 90,
                "peak_rss_bytes": target["peak_rss_limit_bytes"],
            },
            "observed": {
                "wall_clock_seconds": observer["wall_clock_seconds"],
                "peak_rss_bytes": observer["peak_rss_bytes"],
                "runs": observer["runs"],
                "started_at": observer["started_at"],
                "finished_at": observer["finished_at"],
                "exit_code": observer["exit_code"],
            },
            "producer": {
                "log": {"path": "producer.log", "sha256": sha256(log_path)},
                "observer": {"path": "observer.json", "sha256": sha256(observer_path)},
                "build_manifest": {
                    "path": "build.json",
                    "sha256": sha256(build_path),
                },
                "cargo_metadata": {
                    "path": "cargo-metadata.json",
                    "sha256": sha256(metadata_path),
                },
                "command": observer["command"],
                "toolchain": observer["toolchain"],
                "build_environment": observer["build_environment"],
                "executable": observer["executable"],
            },
        }
        report_path = temporary / "report.json"
        write_json(report_path, report)
        validate_report(root, report_path)
        publish_directory(temporary, output)
        return observer["result"]
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def run_smoke(
    root: Path,
    cargo: str,
    cargo_fuzz: str,
    sanitizer: str,
    output: Path,
    jobs: int = DEFAULT_SMOKE_JOBS,
) -> list[str]:
    if jobs < 1:
        raise ValidationError("smoke jobs must be at least 1")
    require_clean_snapshot(root)
    registry, _ = validate_repository(root)
    output = require_external_output(root, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise ValidationError(f"refusing to replace existing evidence path: {output}")
    suite = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        targets = sorted(registry["targets"], key=lambda target: target["name"])
        futures: dict[str, concurrent.futures.Future[str]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            for target in targets:
                name = target["name"]
                print(f"running {name} for {target['smoke_seconds']} seconds", flush=True)
                futures[name] = executor.submit(
                    run_campaign,
                    root,
                    name,
                    "smoke",
                    sanitizer,
                    cargo,
                    cargo_fuzz,
                    suite / name,
                )
            results = [futures[target["name"]].result() for target in targets]
        publish_directory(suite, output)
        return results
    except BaseException:
        shutil.rmtree(suite, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    report_parser = subparsers.add_parser("validate-report")
    report_parser.add_argument("report", type=Path)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("target")
    run_parser.add_argument("--kind", choices=("smoke", "full"), default="smoke")
    run_parser.add_argument("--sanitizer", choices=sorted(SANITIZERS), default="address")
    run_parser.add_argument("--cargo", default="cargo")
    run_parser.add_argument("--cargo-fuzz", default="cargo-fuzz")
    run_parser.add_argument("--output", type=Path, required=True)
    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--cargo", default="cargo")
    smoke_parser.add_argument("--cargo-fuzz", default="cargo-fuzz")
    smoke_parser.add_argument("--sanitizer", choices=sorted(SANITIZERS), default="address")
    smoke_parser.add_argument("--output", type=Path, required=True)
    smoke_parser.add_argument("--jobs", type=int, default=DEFAULT_SMOKE_JOBS)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "validate":
            validate_repository(root)
        elif args.command == "validate-report":
            validate_report(root, args.report.resolve())
        elif args.command == "run":
            result = run_campaign(
                root,
                args.target,
                args.kind,
                args.sanitizer,
                args.cargo,
                args.cargo_fuzz,
                args.output,
            )
            if result != "clean":
                print(f"error: fuzz campaign recorded {result}", file=sys.stderr)
                return 1
        else:
            results = run_smoke(
                root, args.cargo, args.cargo_fuzz, args.sanitizer, args.output, args.jobs
            )
            if any(result != "clean" for result in results):
                print("error: one or more fuzz campaigns recorded a finding", file=sys.stderr)
                return 1
    except (ValidationError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
