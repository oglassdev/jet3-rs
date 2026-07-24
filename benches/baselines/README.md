# Benchmark baselines

No performance baseline is established yet.

An approved baseline must validate against the binding
`../../docs/validation/schema/benchmark-manifest.schema.json`. The
format-neutral `../manifest.json` is only a scenario inventory and does not
conform to that binding baseline schema.

Before comparison, approved results and candidates are normalized into the
local `../comparison-input.schema.json` structure. Each input refers to a clean
exact commit and contains all required latency, throughput, peak-RSS, and
output-size measurements. Generate the environment portion with
`../scripts/capture_metadata.sh`; do not copy Criterion results between
machines or commits.

Raw measurement JSON must be retained in the named commit and referenced by
repository path and SHA-256. Comparison re-reads those exact Git blobs and
reconstructs the canonical suite digest from retained benchmark sources.

Compare a candidate only with a matching, approved baseline:

```sh
python3 benches/scripts/compare_baseline.py \
  benches/baselines/normalized-approved.json \
  artifacts/benchmarks/normalized-candidate.json \
  --output artifacts/benchmarks/comparison.json
```

The comparator fails when any declared metric regresses by more than 15%.
Exactly 15% is accepted, matching the “greater than 15%” contract language.
