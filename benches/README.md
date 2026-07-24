# Benchmarks

This standalone Criterion package measures the existing format-neutral binary
foundations without adding benchmark dependencies to the production workspace.

The checked `manifest.json` defines deterministic datasets and stable benchmark
IDs for:

- `BinaryCursor` exact reads, primitive scans, and truncation rejection;
- in-memory and file-backed `ReadAt` exact reads;
- valid and invalid `PageGeometry` mappings;
- cumulative `ReadBudget` charging and limit rejection; and
- mixed operation-wide `ResourceBudget` accounting and exhaustion.

The byte generator is implemented directly in `format_primitives.rs`. It has no
random seed, external fixture, network access, or Jet-format constant.

## Commands

Compile the suite without measuring:

```sh
cargo bench --manifest-path benches/Cargo.toml \
  --bench format_primitives --locked --no-run
```

Execute every benchmark once as a fast functional check:

```sh
cargo bench --manifest-path benches/Cargo.toml \
  --bench format_primitives --locked -- --test
```

Run the full Criterion measurement:

```sh
cargo bench --manifest-path benches/Cargo.toml \
  --bench format_primitives --locked
```

Capture commit, dirty state, hardware, OS, toolchain, and manifest hashes:

```sh
benches/scripts/capture_metadata.sh \
  artifacts/benchmarks/environment.json
benches/tests/test_capture_metadata.sh
```

Metadata capture requires `jq`, `rustup`, and either `sha256sum` or `shasum`.
Its dirty-tree check excludes only generated `artifacts/benchmarks/**` output;
every tracked or untracked source change elsewhere still makes the report
dirty. The smoke test verifies that repeatability rule with a real output file.

Run the tested 15% comparison policy:

```sh
python3 -m unittest discover -s benches/tests -v
python3 benches/scripts/compare_baseline.py \
  benches/baselines/normalized-approved.json \
  artifacts/benchmarks/normalized-candidate.json \
  --output artifacts/benchmarks/comparison.json
```

`manifest.json` is the scenario inventory for this format-neutral Criterion
suite. It is not an approved-baseline ledger and does not conform to the
binding `docs/validation/schema/benchmark-manifest.schema.json`. That binding
schema remains the only contract for checked G7 baseline entries.

`comparison-input.schema.json` documents the comparator's normalized,
commit-bound input format. It is local tooling structure, not a substitute for
the binding baseline ledger. No approved baseline is checked in yet. Missing
peak RSS, output size, a clean commit, valid commit metadata, or a matching
environment blocks comparison instead of silently passing.

## Scope limit

This foundation does not satisfy G7. It has no Jet catalog, row, index, CRUD,
Memo/OLE, creation, or semantic-verification benchmarks; no 100,000-row
dataset; no approved baseline; and no integrated peak-RSS or output-size
collector. Those remain explicit release blockers.
