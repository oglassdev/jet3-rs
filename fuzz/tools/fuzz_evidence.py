"""Measured producer evidence primitives for fuzz campaigns."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

PROGRESS_RE = re.compile(r"(?m)^#(\d+)\s")
RSS_RE = re.compile(r"\brss:\s*(\d+)Mb\b")
EXECUTABLE_RES = (
    re.compile(r"(?m)^\s*Running `([^`\s]+)"),
    re.compile(r"(?m)^\s*Running ([^\s]+)"),
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
SANITIZER_MARKERS = (
    "ERROR: AddressSanitizer",
    "ERROR: MemorySanitizer",
    "ERROR: LeakSanitizer",
    "ERROR: ThreadSanitizer",
    "runtime error:",
)
PANIC_MARKERS = ("panicked at", "thread '", "thread \"")
LIMIT_MARKERS = ("out-of-memory", "rss limit exceeded", "libFuzzer: timeout")
BUILD_ENVIRONMENT_PREFIXES = (
    "CARGO_BUILD_",
    "CARGO_PROFILE_",
    "CARGO_TARGET_",
)
BUILD_ENVIRONMENT_NAMES = {
    "AR", "CC", "CFLAGS", "CXX", "CXXFLAGS", "RUSTC", "RUSTC_WRAPPER",
    "RUSTC_WORKSPACE_WRAPPER", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS",
    "CARGO_INCREMENTAL",
}


class EvidenceError(ValueError):
    """A measured fuzz-evidence artifact is inconsistent or malformed."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with path.open("rb") as written:
        os.fsync(written.fileno())


def date_time_text(value: datetime.datetime) -> str:
    return value.astimezone(datetime.timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def parse_date_time(value: Any, context: str) -> datetime.datetime:
    if not isinstance(value, str):
        raise EvidenceError(f"{context} must be a date-time string")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceError(f"{context} is not an ISO 8601 date-time") from error
    if parsed.tzinfo is None:
        raise EvidenceError(f"{context} must include a timezone")
    return parsed


def parse_runs(log_text: str) -> int:
    runs = [int(value) for value in PROGRESS_RE.findall(ANSI_RE.sub("", log_text))]
    if not runs:
        raise EvidenceError("producer log contains no libFuzzer progress counter")
    return max(runs)


def parse_reported_rss(log_text: str) -> int:
    samples = [int(value) * 1024 * 1024 for value in RSS_RE.findall(ANSI_RE.sub("", log_text))]
    return max(samples, default=0)


def parse_executable(log_text: str) -> str:
    plain = ANSI_RE.sub("", log_text)
    for pattern in EXECUTABLE_RES:
        match = pattern.search(plain)
        if match:
            return match.group(1)
    raise EvidenceError("producer log contains no cargo-fuzz executable identity")


def classify_result(log_text: str, exit_code: int | None, timed_out: bool) -> str:
    plain = ANSI_RE.sub("", log_text)
    if timed_out:
        return "hang"
    if any(marker in plain for marker in SANITIZER_MARKERS):
        return "sanitizer_finding"
    if any(marker in plain for marker in LIMIT_MARKERS):
        return "limit_exceeded"
    if "panicked at" in plain or (
        any(marker in plain for marker in PANIC_MARKERS) and "stack backtrace:" in plain
    ):
        return "panic"
    return "clean" if exit_code == 0 else "crash"


def tool_identity(executable: str, version_args: list[str]) -> dict[str, str]:
    resolved = shutil.which(executable)
    if resolved is None:
        raise EvidenceError(f"required executable is unavailable: {executable}")
    path = Path(resolved).resolve()
    if not path.is_file() or path.is_symlink():
        raise EvidenceError(f"tool identity is not a regular non-symlink file: {path}")
    process = subprocess.run(
        [str(path), *version_args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if process.returncode:
        raise EvidenceError(f"cannot identify tool {path}: {process.stdout.strip()}")
    return {"path": str(path), "sha256": sha256(path), "version": process.stdout.strip()}


def sanitized_process_environment(overrides: dict[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    for name in list(environment):
        if name in BUILD_ENVIRONMENT_NAMES or name.startswith(BUILD_ENVIRONMENT_PREFIXES):
            environment.pop(name)
    environment.update(overrides)
    return environment


def _process_tree_rss(root_pid: int) -> int:
    process = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,rss="],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode:
        raise EvidenceError(f"cannot sample producer RSS: {process.stderr.strip()}")
    rows: dict[int, tuple[int, int]] = {}
    for line in process.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            pid, parent, rss_kib = (int(field) for field in fields)
        except ValueError:
            continue
        rows[pid] = (parent, rss_kib)
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (parent, _) in rows.items():
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return sum(rows[pid][1] for pid in descendants if pid in rows) * 1024


def observe_producer(
    root: Path,
    log_path: Path,
    command: list[str],
    timeout_seconds: float,
    toolchain: dict[str, Any],
    build_environment: dict[str, str],
) -> dict[str, Any]:
    environment = sanitized_process_environment(build_environment)
    environment["CARGO_TERM_COLOR"] = "never"
    started_at = datetime.datetime.now(datetime.timezone.utc)
    started_clock = time.monotonic()
    peak_rss = 0
    timed_out = False
    with log_path.open("wb") as log:
        producer = subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = started_clock + timeout_seconds
        try:
            while producer.poll() is None:
                peak_rss = max(peak_rss, _process_tree_rss(producer.pid))
                if time.monotonic() >= deadline:
                    timed_out = True
                    os.killpg(producer.pid, signal.SIGKILL)
                    producer.wait()
                    break
                time.sleep(0.05)
        except BaseException:
            if producer.poll() is None:
                os.killpg(producer.pid, signal.SIGKILL)
                producer.wait()
            raise
        exit_code = None if timed_out else producer.returncode
        log.flush()
        os.fsync(log.fileno())
    finished_clock = time.monotonic()
    finished_at = datetime.datetime.now(datetime.timezone.utc)
    if peak_rss <= 0:
        raise EvidenceError("producer completed without an observable positive RSS sample")
    try:
        log_text = log_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceError(f"producer log is not valid UTF-8: {error}") from error
    executable_path = Path(parse_executable(log_text)).resolve()
    if not executable_path.is_file() or executable_path.is_symlink():
        raise EvidenceError(
            f"producer-reported executable is missing, a symlink, or not regular: {executable_path}"
        )
    return {
        "schema_version": 1,
        "producer_log_sha256": sha256(log_path),
        "command": command,
        "started_at": date_time_text(started_at),
        "finished_at": date_time_text(finished_at),
        "wall_clock_seconds": round(finished_clock - started_clock, 6),
        "peak_rss_bytes": peak_rss,
        "runs": parse_runs(log_text),
        "result": classify_result(log_text, exit_code, timed_out),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "toolchain": toolchain,
        "build_environment": build_environment,
        "executable": {"path": str(executable_path), "sha256": sha256(executable_path)},
    }


def publish_directory(temporary: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise EvidenceError(f"refusing to replace existing evidence path: {output}")
    os.replace(temporary, output)
    directory_fd = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
