#!/usr/bin/env python3
"""Command-line entry point for checked DAO M4 evidence validation."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from m4r1_analysis import canonical_analysis_bytes
from m4r1_bundle import (
    build_analysis_from_stage,
    validate_bundle,
    validate_one_sample,
    validate_worker_result,
)
from m4r1_records import (
    SCHEMA_SET,
    ValidationError,
    load_checked_plan,
    load_document,
    validate_invocation_document,
)
from m4r1_phase import validate_quiescence_document


def _absolute_directory(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("expected an absolute directory path")
    if not path.is_dir():
        raise argparse.ArgumentTypeError("directory does not exist")
    return path


def _absolute_file(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("expected an absolute file path")
    if not path.is_file():
        raise argparse.ArgumentTypeError("file does not exist")
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("validate-plan")
    plan.add_argument("plan", type=_absolute_file)
    invocation = commands.add_parser("validate-invocation")
    invocation.add_argument("--bundle-root", required=True, type=_absolute_directory)
    invocation.add_argument("--invocation", required=True, type=_absolute_file)
    sample = commands.add_parser("validate-sample")
    sample.add_argument("--bundle-root", required=True, type=_absolute_directory)
    sample.add_argument("--record", required=True, type=_absolute_file)
    worker_result = commands.add_parser("validate-result")
    worker_result.add_argument("--bundle-root", required=True, type=_absolute_directory)
    worker_result.add_argument("--result", required=True, type=_absolute_file)
    quiescence = commands.add_parser("validate-quiescence")
    quiescence.add_argument("--bundle-root", required=True, type=_absolute_directory)
    quiescence.add_argument("--quiescence", required=True, type=_absolute_file)
    quiescence.add_argument("--result", required=True, type=_absolute_file)
    analysis = commands.add_parser("build-analysis")
    analysis.add_argument("--bundle-root", required=True, type=_absolute_directory)
    analysis.add_argument("--output", type=Path)
    bundle = commands.add_parser("validate-bundle")
    bundle.add_argument("bundle_root", type=_absolute_directory)
    return result


def _write_exclusive(path: Path, payload: bytes) -> None:
    """Durably publish complete bytes without ever exposing a partial output."""
    if not path.is_absolute():
        raise ValidationError("--output must be an absolute path")
    temporary: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            metadata = os.fstat(handle.fileno())
            temporary_identity = (metadata.st_dev, metadata.st_ino)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        temporary = None
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except FileExistsError as exc:
        raise ValidationError(f"{path}: refusing to replace existing output") from exc
    except OSError as exc:
        raise ValidationError(f"{path}: cannot create analysis output: {exc}") from exc
    finally:
        if temporary is not None and temporary_identity is not None:
            try:
                metadata = temporary.lstat()
                if (metadata.st_dev, metadata.st_ino) == temporary_identity:
                    temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def run(arguments: argparse.Namespace) -> str:
    SCHEMA_SET.lint()
    if arguments.command == "validate-plan":
        load_checked_plan(arguments.plan)
        return "PASS: checked M4 plan"
    if arguments.command == "validate-invocation":
        plan, plan_hash = load_checked_plan()
        invocation, _, _ = load_document(
            arguments.invocation, 65536, "dao_m4_invocation"
        )
        validate_invocation_document(
            invocation,
            plan,
            plan_hash,
            arguments.bundle_root,
            preflight=True,
        )
        return "PASS: checked M4 invocation"
    if arguments.command == "validate-sample":
        record = validate_one_sample(arguments.bundle_root, arguments.record)
        return f"PASS: checked M4 sample {record['sample_id']}"
    if arguments.command == "validate-result":
        result = validate_worker_result(arguments.bundle_root, arguments.result)
        return (
            f"PASS: checked M4 worker result "
            f"{result['sample_id']} {result['phase_id']}"
        )
    if arguments.command == "validate-quiescence":
        document = validate_quiescence_document(
            arguments.bundle_root, arguments.quiescence, arguments.result
        )
        return (
            f"PASS: checked M4R1 quiescence "
            f"{document['sample_id']} {document['phase_id']}"
        )
    if arguments.command == "build-analysis":
        analysis = build_analysis_from_stage(arguments.bundle_root)
        payload = canonical_analysis_bytes(analysis)
        if arguments.output is None:
            sys.stdout.buffer.write(payload)
            return ""
        _write_exclusive(arguments.output, payload)
        return f"PASS: wrote checked M4 analysis to {arguments.output}"
    validated = validate_bundle(arguments.bundle_root)
    return f"PASS: checked M4 bundle {validated['manifest']['run_id']}"


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        message = run(arguments)
        if message:
            print(message)
        return 0
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
