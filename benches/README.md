# Benchmarks

This standalone Criterion package measures the checked binary foundations,
generic Jet signature recognition, and Jet 3 page-geometry arithmetic without
adding benchmark dependencies to the production workspace.

The checked `manifest.json` defines deterministic datasets and stable benchmark
IDs for:

- `BinaryCursor` exact reads, primitive scans, and truncation rejection;
- in-memory and file-backed `ReadAt` exact reads;
- valid and invalid `PageGeometry` mappings;
- bounded generic Jet Standard-signature recognition and unknown-signature
  rejection;
- Jet 3 page-geometry derivation for representative aligned source lengths;
- cumulative `ReadBudget` charging and limit rejection; and
- mixed operation-wide `ResourceBudget` accounting and exhaustion.

The byte generator is implemented directly in `format_primitives.rs`. It has no
random seed, external fixture, or network access. The two 19-byte header inputs
are project-authored synthetic arrays, not bytes copied from an MDB file. Every
signature iteration constructs a fresh bounded `SliceSource` and `ReadBudget`.
The Jet 3 geometry cases use a small length-only `ReadAt` source, so their
2 KiB through 128 MiB representative lengths do not allocate corresponding
buffers.

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

`manifest.json` is the scenario inventory for this checked-foundations
Criterion suite. It is not an approved-baseline ledger and does not conform to
the binding `docs/validation/schema/benchmark-manifest.schema.json`. That
binding schema remains the only contract for checked G7 baseline entries.

`comparison-input.schema.json` documents the comparator's normalized,
commit-bound input format. It is local tooling structure, not a substitute for
the binding baseline ledger. No approved baseline is checked in yet. Missing
peak RSS, output size, a clean commit, valid commit metadata, or a matching
environment blocks comparison instead of silently passing.

Each normalized input must name at least one raw measurement JSON artifact
retained in its exact Git commit. A raw artifact contains only a
`measurements` array in the normalized format. The comparator reads every
artifact with `git show <commit>:<path>`, verifies its SHA-256, and requires the
combined raw measurements to equal the input measurements.

The comparator also reconstructs one canonical suite digest from the retained
benchmark Cargo manifest and lockfile, harness, scenario manifest, comparison
schema, metadata capture script, comparator, and suite-identity helper. The
baseline and candidate digests must match. A claimed commit, source hash,
artifact hash, or measurement value cannot satisfy comparison unless the
corresponding retained Git blobs prove it.

## Scope limit

This foundation does not satisfy G7. It has no Jet catalog, row, index, CRUD,
Memo/OLE, creation, or semantic-verification benchmarks; no 100,000-row
dataset; no approved baseline; and no integrated peak-RSS or output-size
collector. Synthetic generic Jet signature recognition does not identify a Jet
version, validate a database, or demonstrate application compatibility. Jet 3
geometry cases validate arithmetic from aligned lengths only and do not inspect
page contents. Those remain explicit release blockers.
