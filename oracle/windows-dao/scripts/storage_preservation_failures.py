#!/usr/bin/env python3
"""Compare terminal native failure/rollback images with byte-exact Rust refusals."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess

import storage_preservation as suite
from storage_preservation_plan import refusals, write


def changed_bytes(before, after):
    return dict(before_size=len(before), after_size=len(after),
                bytes=[dict(offset=i, before=a, after=b) for i,(a,b) in enumerate(zip(before,after)) if a != b],
                appended_hex=after[len(before):].hex(), removed_hex=before[len(after):].hex())


def relationship_residue(before, baseline):
    """EXP-0298/0304: even explicit rollback retains these native counters."""
    expected = bytearray(before)
    suite.require(expected[1538] == 0, "suite starts with zero transaction marker byte")
    expected[1538] = 2
    for name, delta in (("MSysObjects", 1), ("MSysACEs", 2)):
        definition = baseline["tables"][name]["definition"]
        offsets = [(definition["row_count_offset"], delta)]
        offsets += [(index["entry_count_offset"], 1) for index in definition["physical_indexes"]]
        for offset, increment in offsets:
            value = int.from_bytes(before[offset:offset+4], "little") + increment
            expected[offset:offset+4] = value.to_bytes(4, "little")
    return bytes(expected)


def native_failure_bytes(before, baseline, kind, refusal):
    if refusal == "relationship":
        return relationship_residue(before, baseline)
    expected = bytearray(before)
    # EXP-0304: both payload schemas retain this byte, with or without rollback.
    if kind != "wide":
        suite.require(expected[1538] == 0, "suite transaction marker byte")
        expected[1538] = 2
    if kind == "sparse" and refusal == "duplicate":
        # EXP-0237/0304: a refused explicit duplicate consumes a generator value.
        definition = baseline["tables"]["Items"]["definition"]
        suite.require(definition["marker"] == ord("N"), "native AutoNumber definition")
        offset = definition["root"] * 2048 + 16
        value = int.from_bytes(before[offset:offset+4], "little") + 1
        expected[offset:offset+4] = value.to_bytes(4, "little")
    return bytes(expected)


def evaluate(args):
    args.out.mkdir()
    suite.raw.setup(suite.SCRIPTS)
    cli = (args.prepared / "jet3-cli").resolve()
    plan = suite.read(args.prepared / "plan.json")
    native_jobs = suite.read(args.native / "failures.json")
    expected = {(case["name"],refusal["name"]+suffix) for case in plan["cases"]
                for refusal in refusals(case) for suffix in ("", "-rollback")}
    suite.require(len(native_jobs) == len(expected) and
                  {(j["case"],j["name"]) for j in native_jobs} == expected, "exact native failure inventory")
    jobs = {(j["case"],j["name"]): j for j in native_jobs}
    results = []
    for case in plan["cases"]:
        source = args.prepared / "native" / f"native-{case['name']}-original.mdb"
        before = source.read_bytes()
        baseline = suite.raw.observe(source)
        snapshot = suite.read(source.with_suffix(".json"))
        for refusal in refusals(case):
            label = case["name"] + "-" + refusal["name"]
            target = args.out / ("candidate-" + label + ".mdb")
            shutil.copy2(source, target)
            request = suite.request_for(target, refusal["request"])
            write(args.out / (label + ".request.json"), request)
            result = subprocess.run([str(cli), refusal["command"], str(target), "--input", "-"],
                                    input=json.dumps(request), text=True, capture_output=True)
            write(args.out / (label + ".rust.json"), suite.vars_result(result))
            suite.require(result.returncode == 1 and target.read_bytes() == before, "whole-file Rust refusal: " + label)
            error = json.loads(result.stderr)
            reason = {"duplicate": "duplicate unique key", "required": "RequiredValueMissing",
                      "relationship": "RelationshipConstraint"}[refusal["name"]]
            support_boundary = case["kind"] == "sparse" and refusal["name"] == "relationship"
            if support_boundary:
                # The native orphan control has an AutoNumber child, which Rust refuses by type.
                reason = 'Unsupported("relationship column types")'
            suite.require(reason in error["message"] and error["publication_stage"] ==
                          ("Mutation" if refusal["name"] == "relationship" else None), "Rust refusal reason/stage: " + label)
            record = dict(name=label, rust_refusal=suite.vars_result(result), input=suite.identity(source),
                          comparison="rust_support_boundary" if support_boundary else "matching_constraint_refusal", native=[])
            for suffix in ("", "-rollback"):
                job = jobs[case["name"],refusal["name"]+suffix]
                native = args.native / ("failure-" + label + suffix + ".mdb")
                suite.require(job["before"] == suite.identity(source) and job["after"] == suite.identity(native), "native identities")
                suite.require(job["error"] is not None and job["error"]["numbers"] == [refusal["error"]], "exact DAO refusal: " + label + suffix)
                observed = suite.read(native.with_suffix(".json"))
                expected_rows = [suite.model_row(case["columns"], row, dao=True) for row in case["initial"]]
                suite.compare_snapshot(observed, snapshot, expected_rows, native)
                # Native bookkeeping is retained in full, including transaction rollbacks.
                validation = subprocess.run([str(cli), "validate", str(native)], text=True, capture_output=True)
                relationship = refusal["name"] == "relationship"
                expected_bytes = native_failure_bytes(before, baseline, case["kind"], refusal["name"])
                suite.require(native.read_bytes() == expected_bytes, "exact native failure/rollback bookkeeping: " + label + suffix)
                suite.require(validation.returncode == (1 if relationship else 0), "strict native validation result: " + label + suffix)
                deltas = {"MSysObjects": 1, "MSysACEs": 2} if relationship else None
                raw = suite.raw.observe(native, deltas)
                spec = dict(name=label, steps=[])
                raw_diff = suite.differences(suite.semantics(baseline, spec), suite.semantics(raw, spec))
                snapshot_diff = suite.differences(suite.normalized(snapshot), suite.normalized(observed))
                suite.require(not snapshot_diff, "complete native failure snapshot: " + label + suffix)
                write(args.out / (label + suffix + ".raw.json"), raw)
                record["native"].append(dict(rollback=bool(suffix), validation=suite.vars_result(validation),
                                             exact_bytes=changed_bytes(before,native.read_bytes()),
                                             raw_differences=raw_diff, snapshot_differences=snapshot_diff))
            results.append(record)
    write(args.out / "REPORT.json", dict(status="pass", results=results))
    print(f"{len(results)} byte-exact Rust refusals; {len(native_jobs)} terminal native captures")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prepared", "native", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    evaluate(parser.parse_args())
