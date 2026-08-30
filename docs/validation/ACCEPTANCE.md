# Acceptance

v1 has exactly three release gates:

1. `just ready` passes on the release commit.
2. A validated DAO differential bundle exists for each release leg: read,
   write, and update.
3. Every format constant in `crates/jet3` cites its `SRC-` or `EXP-` entry in
   `docs/PROVENANCE.md`.

## Developer checks

Run the fast, local checks with:

```sh
./scripts/acceptance.sh quick
```

`quick` runs formatting, Clippy, workspace tests, and the parser's 800-line
source limit. It needs no DAO bundle and is also part of `just ready`.

## Full check for one DAO leg

Pass a bundle document as an argument:

```sh
./scripts/acceptance.sh full path/to/canonical-snapshot.json
```

or set `JET3_DAO_BUNDLE`:

```sh
JET3_DAO_BUNDLE=path/to/canonical-snapshot.json just accept
```

`full` runs `quick`, builds documentation with warnings denied, validates the
support matrix, and validates the supplied bundle document with the DAO
protocol 1.2 validator. A missing or invalid bundle exits nonzero with a short
reason. It creates no run ID, manifest, report, or evidence overlay.

Run `full` once with the validated bundle for each applicable release leg.
Together with review of the provenance citations in `crates/jet3`, those runs
cover the three release gates above. Fuzzing, Miri, benchmarks, platform tests,
oracle contract tests, and dependency checks remain useful CI checks; they are
not additional release gates.
