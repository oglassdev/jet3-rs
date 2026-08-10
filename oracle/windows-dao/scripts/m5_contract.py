#!/usr/bin/env python3
"""Command-line entry point for checked DAO M5R5 evidence validation."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from m5_analysis import canonical_analysis_bytes, validate_m4_identity
from m5_bundle import build_analysis_from_stage, validate_bundle
from m5_phase import validate_quiescence_document, validate_sample_record, validate_worker_result
from m5_records import SCHEMA_SET, ValidationError, load_checked_plan, load_document, validate_invocation_document


def _absolute_directory(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise argparse.ArgumentTypeError("expected an existing absolute directory")
    return path


def _absolute_file(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise argparse.ArgumentTypeError("expected an existing absolute file")
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("validate-plan")
    plan.add_argument("plan", type=_absolute_file)
    invocation = commands.add_parser("validate-invocation")
    invocation.add_argument("--bundle-root", required=True, type=_absolute_directory)
    invocation.add_argument("--invocation", required=True, type=_absolute_file)
    invocation.add_argument("--m4-bundle-root", required=True, type=_absolute_directory)
    worker = commands.add_parser("validate-result")
    worker.add_argument("--bundle-root", required=True, type=_absolute_directory)
    worker.add_argument("--result", required=True, type=_absolute_file)
    quiescence = commands.add_parser("validate-quiescence")
    quiescence.add_argument("--bundle-root", required=True, type=_absolute_directory)
    quiescence.add_argument("--quiescence", required=True, type=_absolute_file)
    quiescence.add_argument("--result", required=True, type=_absolute_file)
    sample = commands.add_parser("validate-sample")
    sample.add_argument("--bundle-root", required=True, type=_absolute_directory)
    sample.add_argument("--record", required=True, type=_absolute_file)
    analysis = commands.add_parser("build-analysis")
    analysis.add_argument("--bundle-root", required=True, type=_absolute_directory)
    analysis.add_argument("--m4-bundle-root", required=True, type=_absolute_directory)
    analysis.add_argument("--output", type=Path)
    bundle = commands.add_parser("validate-bundle")
    bundle.add_argument("bundle_root", type=_absolute_directory)
    bundle.add_argument("--m4-bundle-root", required=True, type=_absolute_directory)
    return result


def _write_exclusive(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        raise ValidationError("--output must be absolute")
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        temporary = None
    except FileExistsError as exc:
        raise ValidationError(f"{path}: refusing to replace output") from exc
    except OSError as exc:
        raise ValidationError(f"{path}: cannot publish analysis: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def run(arguments: argparse.Namespace) -> str:
    SCHEMA_SET.lint()
    if arguments.command == "validate-plan":
        load_checked_plan(arguments.plan)
        return "PASS: checked M5R5 plan"
    if arguments.command == "validate-invocation":
        validate_m4_identity(arguments.m4_bundle_root)
        plan, plan_hash = load_checked_plan()
        invocation, _, _ = load_document(arguments.invocation, 65536, "dao_m5_invocation")
        validate_invocation_document(invocation, plan, plan_hash, arguments.bundle_root, preflight=True)
        return "PASS: checked M5R5 invocation"
    if arguments.command == "validate-result":
        result, _, _ = validate_worker_result(arguments.bundle_root, arguments.result)
        return f"PASS: checked M5R5 worker result {result['sample_id']} {result['phase_id']}"
    if arguments.command == "validate-quiescence":
        document = validate_quiescence_document(arguments.bundle_root, arguments.quiescence, arguments.result)
        return f"PASS: checked M5R5 quiescence {document['sample_id']} {document['database_role']}"
    if arguments.command == "validate-sample":
        document = validate_sample_record(arguments.bundle_root, arguments.record)
        return f"PASS: checked M5R5 sample {document['sample_id']}"
    if arguments.command == "build-analysis":
        analysis = build_analysis_from_stage(arguments.bundle_root, arguments.m4_bundle_root)
        payload = canonical_analysis_bytes(analysis)
        if arguments.output is None:
            sys.stdout.buffer.write(payload)
            return ""
        _write_exclusive(arguments.output, payload)
        return f"PASS: wrote checked M5R5 analysis to {arguments.output}"
    validated = validate_bundle(arguments.bundle_root, arguments.m4_bundle_root)
    return f"PASS: checked M5R5 bundle {validated['manifest']['run_id']}"


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
