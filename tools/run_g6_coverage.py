#!/usr/bin/env python3
"""Produce immutable, exact-commit G6 coverage evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

try:  # pragma: no cover - Windows has no POSIX resource limits
    import resource
except ImportError:  # pragma: no cover - exercised only off POSIX
    resource = None  # type: ignore[assignment]

import validate_g6_evidence as validator

TOOLCHAIN = "1.96.0"
CARGO_LLVM_COV_VERSION = "0.8.6"
EXPECTED_TOOL = f"cargo-llvm-cov {CARGO_LLVM_COV_VERSION}"
CARGO_LLVM_COV = ("rustup", "run", TOOLCHAIN, "cargo", "llvm-cov")
DEFAULT_TIMEOUT_SECONDS = 30 * 60
MAX_TIMEOUT_SECONDS = 60 * 60
STDOUT_LIMIT = 256 * 1024
STDERR_LIMIT = 1024 * 1024
REPORT_LIMIT = 128 * 1024 * 1024
COMMIT = re.compile(r"^[0-9a-f]{40}$")
TRACKED_FILE_LIMIT = 1024 * 1024
# Test seam only: production walks ancestors to the filesystem root.
ANCESTOR_BOUNDARY: Path | None = None
CARGO_CONFIG_NAMES = ("config.toml", "config")
SIGXFSZ = getattr(signal, "SIGXFSZ", None)
INHERITED_ENVIRONMENT = ("PATH", "HOME", "TMPDIR", "CARGO_HOME", "RUSTUP_HOME")
FORCED_ENVIRONMENT = {
    "CARGO_NET_OFFLINE": "true",
    "CARGO_TERM_COLOR": "never",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
}


class CoverageProducerError(RuntimeError):
    """Coverage cannot be safely produced or published."""


@dataclass(frozen=True)
class ProcessResult:
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class CheckoutSnapshot:
    commit: str
    toolchain_sha256: str
    inventory_sha256: str
    modules: tuple[validator.CoreModule, ...]

    def observed_binding(self) -> dict[str, Any]:
        return {
            "git_commit": self.commit,
            "git_dirty": False,
            "rust_toolchain_sha256": self.toolchain_sha256,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _environment() -> dict[str, str]:
    """Build the child environment from an allowlist, never from inheritance.

    Inherited GIT_* variables can redirect Git at a different repository and
    inherited RUSTFLAGS/RUSTC_WRAPPER/CARGO_* variables can perturb the build,
    so only the toolchain necessities below survive into child processes.
    """
    environment: dict[str, str] = {}
    for name in INHERITED_ENVIRONMENT:
        value = os.environ.get(name)
        if value:
            environment[name] = value
    if not environment.get("PATH"):
        environment["PATH"] = os.defpath
    environment.update(FORCED_ENVIRONMENT)
    return environment


def _file_size_limiter(limit: int) -> Callable[[], None] | None:
    """Return a POSIX child hook capping every file the child writes."""
    if resource is None or os.name == "nt":
        return None
    _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    ceiling = limit if hard == resource.RLIM_INFINITY else min(limit, hard)

    def apply() -> None:  # pragma: no cover - runs after fork, before exec
        # Both limits: lowering the hard limit is irreversible, so the child
        # cannot restore its own soft limit and outgrow the bound.
        resource.setrlimit(resource.RLIMIT_FSIZE, (ceiling, ceiling))

    return apply


def _exceeded_watched_limit(
    watched_file: Path, watched_limit: int, return_code: int
) -> bool:
    """Report whether the child hit the enforced report-size bound.

    RLIMIT_FSIZE kills the child with SIGXFSZ, or fails its write with EFBIG
    when it ignores that signal; either way the report stops exactly at the
    limit, so a failed child at or above the bound counts as a limit hit.
    """
    if SIGXFSZ is not None and return_code == -SIGXFSZ:
        return True
    try:
        size = watched_file.stat().st_size
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return size >= watched_limit if return_code != 0 else size > watched_limit


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def _bounded_process(
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
    environment: dict[str, str],
    watched_file: Path | None = None,
    watched_limit: int | None = None,
) -> ProcessResult:
    """Run an argv-only subprocess with finite time, output, and report size."""
    if not command or not all(isinstance(item, str) and item for item in command):
        raise CoverageProducerError("invalid subprocess argument vector")
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise CoverageProducerError("subprocess timeout is outside the safe range")
    for name, limit in (("stdout", stdout_limit), ("stderr", stderr_limit)):
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 0 <= limit <= 8 * 1024 * 1024
        ):
            raise CoverageProducerError(f"{name} limit is outside the safe range")
    if (watched_file is None) != (watched_limit is None):
        raise CoverageProducerError("watched report path and limit must be paired")
    if watched_limit is not None and (
        not isinstance(watched_limit, int)
        or isinstance(watched_limit, bool)
        or not 1 <= watched_limit <= REPORT_LIMIT
    ):
        raise CoverageProducerError("report limit is outside the safe range")

    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            preexec_fn=(
                None if watched_limit is None else _file_size_limiter(watched_limit)
            ),
        )
    except OSError as error:
        raise CoverageProducerError(
            f"cannot start {shlex.join(command)}: {error}"
        ) from error
    assert process.stdout is not None
    assert process.stderr is not None

    overflow = threading.Event()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def consume(stream: Any, chunks: list[bytes], limit: int) -> None:
        consumed = 0
        while True:
            try:
                chunk = stream.read(min(64 * 1024, limit + 1 - consumed))
            except (OSError, ValueError):
                return
            if not chunk:
                return
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > limit:
                overflow.set()
                return

    readers = (
        threading.Thread(
            target=consume,
            args=(process.stdout, stdout_chunks, stdout_limit),
            daemon=True,
            name="g6-coverage-stdout",
        ),
        threading.Thread(
            target=consume,
            args=(process.stderr, stderr_chunks, stderr_limit),
            daemon=True,
            name="g6-coverage-stderr",
        ),
    )
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + timeout_seconds
    failure: str | None = None
    return_code: int | None = None
    while return_code is None:
        if overflow.is_set():
            failure = "exceeded bounded subprocess output"
            break
        if watched_file is not None:
            # Defense in depth: RLIMIT_FSIZE already caps the child's writes.
            try:
                if watched_file.stat().st_size > watched_limit:
                    failure = "coverage report exceeded bounded size"
                    break
            except FileNotFoundError:
                pass
            except OSError as error:
                failure = f"cannot inspect coverage report: {error}"
                break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failure = f"exceeded {timeout_seconds:g}s timeout"
            break
        try:
            return_code = process.wait(timeout=min(0.1, remaining))
        except subprocess.TimeoutExpired:
            pass

    if failure is not None:
        _terminate(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired as error:
            process.stdout.close()
            process.stderr.close()
            raise CoverageProducerError(
                f"{shlex.join(command)} could not be terminated"
            ) from error

    for reader in readers:
        reader.join(timeout=1)
    if any(reader.is_alive() for reader in readers):
        _terminate(process)
        process.stdout.close()
        process.stderr.close()
        raise CoverageProducerError(
            f"{shlex.join(command)} did not close bounded output pipes"
        )
    process.stdout.close()
    process.stderr.close()

    if failure is not None:
        raise CoverageProducerError(f"{shlex.join(command)} {failure}")
    if overflow.is_set():
        raise CoverageProducerError(
            f"{shlex.join(command)} exceeded bounded subprocess output"
        )
    if (
        watched_file is not None
        and watched_limit is not None
        and return_code is not None
        and _exceeded_watched_limit(watched_file, watched_limit, return_code)
    ):
        raise CoverageProducerError(
            f"{shlex.join(command)} coverage report exceeded bounded size"
        )
    if return_code != 0:
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
        raise CoverageProducerError(
            f"{shlex.join(command)} failed with exit {return_code}: {stderr}"
        )
    return ProcessResult(b"".join(stdout_chunks), b"".join(stderr_chunks))


def _git(root: Path, *arguments: str) -> str:
    result = _bounded_process(
        ("git", *arguments),
        cwd=root,
        timeout_seconds=15,
        stdout_limit=1024 * 1024,
        stderr_limit=64 * 1024,
        environment=_environment(),
    )
    try:
        return result.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise CoverageProducerError("Git returned non-UTF-8 output") from error


def _reject_relative_home(name: str, value: str) -> None:
    """Refuse a relative Cargo home: Cargo resolves it against the child cwd.

    The producer's own working directory is not the campaign's, so a relative
    $CARGO_HOME would be inspected here and read there. Rejecting is airtight
    and costs nothing in CI, where the effective home is always absolute.
    """
    if not Path(value).is_absolute():
        raise CoverageProducerError(f"{name} must be an absolute path: {value}")


def _reject_cargo_config_in(directory: Path) -> None:
    for name in CARGO_CONFIG_NAMES:
        candidate = directory / name
        if candidate.is_symlink() or candidate.exists():
            raise CoverageProducerError(
                f"ambient Cargo configuration is not commit-bound: {candidate}"
            )


def _reject_ambient_cargo_config(
    environment: dict[str, str], root: Path, *, boundary: Path | None = None
) -> None:
    """Refuse to build under any Cargo config that is not bound to the commit.

    Cargo reads $CARGO_HOME/config{,.toml} plus .cargo/config{,.toml} in the
    working directory and every ancestor, so a config above the checkout can
    inject rustflags, a rustc-wrapper, or a target directory that the
    environment allowlist cannot see. Only the in-repo .cargo config survives,
    and only while it is tracked and identical to HEAD. `boundary`, when given,
    is the last ancestor walked; production walks to the filesystem root.
    """
    cargo_home = environment.get("CARGO_HOME")
    home = environment.get("HOME")
    if cargo_home:
        _reject_relative_home("CARGO_HOME", cargo_home)
        _reject_cargo_config_in(Path(cargo_home))
    elif home:
        _reject_relative_home("HOME", home)
        _reject_cargo_config_in(Path(home) / ".cargo")
    else:
        raise CoverageProducerError("cannot resolve the effective Cargo home")
    resolved = root.resolve(strict=True)
    for parent in resolved.parents:
        _reject_cargo_config_in(parent / ".cargo")
        if boundary is not None and parent == boundary:
            break
    for name in CARGO_CONFIG_NAMES:
        candidate = resolved / ".cargo" / name
        if candidate.is_symlink() or candidate.exists():
            _verify_committed_file(root, candidate, "in-repo Cargo configuration")


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    """Read through one descriptor so size cannot change between checks."""
    chunks: list[bytes] = []
    size = 0
    try:
        with path.open("rb") as source:
            while size <= limit:
                chunk = source.read(min(1024 * 1024, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
    except OSError as error:
        raise CoverageProducerError(f"cannot read {label}: {error}") from error
    if size > limit:
        raise CoverageProducerError(f"{label} exceeded bounded size: {path}")
    return b"".join(chunks)


def _verify_committed_file(root: Path, path: Path, label: str) -> str:
    """Bind a file to HEAD: tracked in this checkout and byte-identical."""
    if path.is_symlink():
        raise CoverageProducerError(f"{label} must be a regular file: {path}")
    try:
        relative = path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise CoverageProducerError(
            f"{label} escapes repository root: {path}"
        ) from error
    posix = PurePosixPath(*relative.parts).as_posix()
    try:
        _git(root, "ls-files", "--error-unmatch", "--", posix)
    except CoverageProducerError as error:
        raise CoverageProducerError(
            f"{label} is not tracked in this checkout: {posix}"
        ) from error
    working = _read_bounded(path, TRACKED_FILE_LIMIT, label)
    try:
        committed = _bounded_process(
            ("git", "show", f"HEAD:{posix}"),
            cwd=root,
            timeout_seconds=15,
            stdout_limit=TRACKED_FILE_LIMIT,
            stderr_limit=64 * 1024,
            environment=_environment(),
        ).stdout
    except CoverageProducerError as error:
        raise CoverageProducerError(
            f"{label} is not committed at HEAD: {posix}"
        ) from error
    if working != committed:
        raise CoverageProducerError(
            f"{label} differs from the version committed at HEAD: {posix}"
        )
    return posix


def _snapshot(
    root: Path, inventory_path: Path, expected_commit: str
) -> CheckoutSnapshot:
    commit = _git(root, "rev-parse", "HEAD")
    if COMMIT.fullmatch(commit) is None:
        raise CoverageProducerError("Git HEAD is not a full commit ID")
    if commit != expected_commit:
        raise CoverageProducerError(
            f"stale checkout: expected {expected_commit}, observed {commit}"
        )
    dirty = _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        ".",
    )
    if dirty:
        raise CoverageProducerError("coverage evidence requires a clean checkout")
    _verify_committed_file(root, inventory_path, "inventory")
    try:
        modules = tuple(validator.load_inventory(root, inventory_path))
    except validator.EvidenceError as error:
        raise CoverageProducerError(str(error)) from error
    toolchain = root / "rust-toolchain.toml"
    if not toolchain.is_file() or toolchain.is_symlink():
        raise CoverageProducerError("rust-toolchain.toml must be a regular file")
    return CheckoutSnapshot(
        commit=commit,
        toolchain_sha256=_sha256(toolchain),
        inventory_sha256=_sha256(inventory_path),
        modules=modules,
    )


def _safe_output(root: Path, value: str) -> tuple[str, Path]:
    try:
        relative = validator._repo_path(value, "output")
    except validator.EvidenceError as error:
        raise CoverageProducerError(str(error)) from error
    parts = PurePosixPath(relative).parts
    if len(parts) < 2 or parts[0] != "coverage":
        raise CoverageProducerError(
            "output must be beneath the ignored coverage/ directory"
        )
    absolute = root.joinpath(*parts)
    try:
        absolute.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise CoverageProducerError("output escapes repository root") from error
    return relative, absolute


def _create_parents(root: Path, parent: Path) -> None:
    relative = parent.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        try:
            current.mkdir()
        except FileExistsError:
            if not current.is_dir() or current.is_symlink():
                raise CoverageProducerError(
                    f"output ancestor is not a regular directory: {current}"
                ) from None


def _tool_version(root: Path) -> str:
    command = (*CARGO_LLVM_COV, "--version")
    result = _bounded_process(
        command,
        cwd=root,
        timeout_seconds=30,
        stdout_limit=4096,
        stderr_limit=4096,
        environment=_environment(),
    )
    try:
        version = result.stdout.decode("utf-8", errors="strict").rstrip("\r\n")
    except UnicodeDecodeError as error:
        raise CoverageProducerError("cargo-llvm-cov version is not UTF-8") from error
    if version != EXPECTED_TOOL:
        raise CoverageProducerError(
            f"expected {EXPECTED_TOOL!r}, observed {version!r}"
        )
    return version


def _coverage_command(report_path: str) -> tuple[str, ...]:
    return (
        *CARGO_LLVM_COV,
        "--workspace",
        "--all-targets",
        "--all-features",
        "--locked",
        "--json",
        "--output-path",
        report_path,
    )


def _run_campaign(
    root: Path, command: tuple[str, ...], report_path: Path, timeout_seconds: int
) -> None:
    _bounded_process(
        command,
        cwd=root,
        timeout_seconds=timeout_seconds,
        stdout_limit=STDOUT_LIMIT,
        stderr_limit=STDERR_LIMIT,
        environment=_environment(),
        watched_file=report_path,
        watched_limit=REPORT_LIMIT,
    )
    if (
        not report_path.is_file()
        or report_path.is_symlink()
        or report_path.stat().st_size <= 0
        or report_path.stat().st_size > REPORT_LIMIT
    ):
        raise CoverageProducerError(
            "cargo-llvm-cov did not produce a bounded regular report"
        )


def _canonical_json(document: Any) -> bytes:
    return (
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _write_new(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
    except FileExistsError as error:
        raise CoverageProducerError(
            f"refusing to overwrite immutable coverage artifact: {path}"
        ) from error


def _copy_new(source: Path, destination: Path) -> None:
    try:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            copied = 0
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                copied += len(chunk)
                if copied > REPORT_LIMIT:
                    raise CoverageProducerError("coverage report exceeded bounded size")
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except FileExistsError as error:
        raise CoverageProducerError(
            f"refusing to overwrite immutable coverage artifact: {destination}"
        ) from error


def _remove_private_tree(path: Path) -> None:
    if not path.exists() or path.is_symlink():
        return
    for child in path.iterdir():
        if child.is_file() and not child.is_symlink():
            child.unlink()
    path.rmdir()


def produce(
    *,
    root: Path,
    inventory_path: Path,
    expected_commit: str,
    output: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[Path, dict[str, tuple[int, int]]]:
    """Run, bind, validate, and immutably publish one coverage campaign."""
    root = root.resolve(strict=True)
    if COMMIT.fullmatch(expected_commit) is None:
        raise CoverageProducerError("expected commit must be a full lowercase SHA-1")
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise CoverageProducerError("campaign timeout is outside the safe range")
    output_relative, output_path = _safe_output(root, output)
    _create_parents(root, output_path.parent)
    staging_path = output_path.parent / f".{output_path.name}.staging"
    if output_path.exists() or output_path.is_symlink():
        raise CoverageProducerError(
            f"refusing to overwrite immutable coverage artifact: {output_path}"
        )
    try:
        staging_path.mkdir()
    except FileExistsError as error:
        raise CoverageProducerError(
            f"private coverage staging path already exists: {staging_path}"
        ) from error

    try:
        _reject_ambient_cargo_config(
            _environment(), root, boundary=ANCESTOR_BOUNDARY
        )
        initial = _snapshot(root, inventory_path, expected_commit)
        tool = _tool_version(root)
        staging_report = staging_path / "coverage.json"
        staging_relative = staging_report.relative_to(root).as_posix()
        command = _coverage_command(staging_relative)
        _run_campaign(root, command, staging_report, timeout_seconds)
        final = _snapshot(root, inventory_path, expected_commit)
        if final != initial:
            raise CoverageProducerError(
                "checkout, inventory, sources, or toolchain changed during campaign"
            )
        if _tool_version(root) != tool:
            raise CoverageProducerError("cargo-llvm-cov changed during campaign")
        core_paths = {module.path for module in final.modules}
        try:
            metrics = validator._validate_json_coverage(
                staging_report, root, core_paths
            )
        except validator.EvidenceError as error:
            raise CoverageProducerError(str(error)) from error
        if not validator._meets(metrics["lines"][1], metrics["lines"][0], 90):
            raise CoverageProducerError("coverage report: line coverage is below 90%")
        if not validator._meets(metrics["regions"][1], metrics["regions"][0], 80):
            raise CoverageProducerError("coverage report: regions coverage is below 80%")

        final_report_relative = f"{output_relative}/coverage.json"
        envelope = {
            "schema_version": 1,
            "kind": "coverage",
            "git_commit": final.commit,
            "git_dirty": False,
            "rust_toolchain_sha256": final.toolchain_sha256,
            "tool": tool,
            "command": shlex.join(command),
            "inventory_sha256": final.inventory_sha256,
            "sources": [
                {"path": module.path, "sha256": module.sha256}
                for module in final.modules
            ],
            "report": {
                "path": final_report_relative,
                "sha256": _sha256(staging_report),
                "format": "llvm-cov-json",
            },
        }

        try:
            output_path.mkdir()
        except FileExistsError as error:
            raise CoverageProducerError(
                f"refusing to overwrite immutable coverage artifact: {output_path}"
            ) from error
        published = False
        try:
            final_report = output_path / "coverage.json"
            envelope_path = output_path / "coverage-evidence.json"
            _copy_new(staging_report, final_report)
            _write_new(envelope_path, _canonical_json(envelope))
            closure = _snapshot(root, inventory_path, expected_commit)
            if closure != final:
                raise CoverageProducerError(
                    "checkout, inventory, sources, or toolchain changed "
                    "during publication"
                )
            try:
                validated = validator.validate_coverage(
                    root,
                    envelope_path,
                    inventory_path,
                    closure.observed_binding(),
                )
            except validator.EvidenceError as error:
                raise CoverageProducerError(str(error)) from error
            published = True
        finally:
            if not published:
                _remove_private_tree(output_path)
        return envelope_path, validated
    finally:
        _remove_private_tree(staging_path)


def _confined_inventory(root: Path, inventory: Path | None) -> Path:
    """Resolve the inventory, refusing anything outside the repository root."""
    if inventory is None:
        return root / validator.DEFAULT_INVENTORY
    try:
        resolved = inventory.resolve(strict=True)
        anchor = root.resolve(strict=True)
    except OSError as error:
        raise CoverageProducerError(f"cannot resolve inventory: {error}") from error
    try:
        resolved.relative_to(anchor)
    except ValueError as error:
        raise CoverageProducerError(
            f"inventory escapes repository root: {resolved}"
        ) from error
    return resolved


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--inventory",
        type=Path,
        help="defaults to docs/validation/g6/core-modules.json under repo root",
    )
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    root = arguments.repo_root.resolve()
    try:
        inventory_path = _confined_inventory(root, arguments.inventory)
        envelope, metrics = produce(
            root=root,
            inventory_path=inventory_path,
            expected_commit=arguments.expected_commit,
            output=arguments.output,
            timeout_seconds=arguments.timeout_seconds,
        )
        rendered = ", ".join(
            f"{name}={covered}/{total}"
            for name, (total, covered) in metrics.items()
        )
        print(
            f"G6 coverage evidence published (mutation remains separate): "
            f"{envelope} ({rendered})"
        )
        return 0
    except (CoverageProducerError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
