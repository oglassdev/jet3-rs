# G6 coverage and mutation evidence

G6 is blocked until real coverage and mutation campaigns produce clean,
commit-bound evidence that passes `tools/validate_g6_evidence.py`. The checked
inventory and validator tests are infrastructure, not campaign evidence, and
must never be reported as a G6 pass.

## Checked core boundary

`g6/core-modules.json` is the complete inventory of non-test Rust modules
directly under `crates/jet3/src`. Each entry records its format/safety
classification and exact SHA-256. The validator independently discovers every
`*.rs` file in that directory except `*_tests.rs`; a new module, an omitted
module, an extra entry, a changed source hash, a symlink, or a path outside the
repository fails closed.

This deliberate rule makes exclusions visible. Presentation code and test
modules can remain outside the core aggregate, but a current Jet 3 format or
safety module cannot be silently dropped to improve a percentage. Review and
update the checked inventory whenever a core source file changes.

Validate only the inventory with:

```sh
python3 tools/validate_g6_evidence.py inventory
```

## Evidence envelope

Coverage and mutation runs use the envelope described by
`schema/g6-coverage-evidence.schema.json` or
`schema/g6-mutation-evidence.schema.json`. The envelope records:

- the full Git commit and a clean (`false`) dirty flag;
- the SHA-256 of `rust-toolchain.toml`;
- the exact producer and version in `tool`, plus the complete command line;
- the SHA-256 of the checked inventory and an exact path/hash copy of every
  inventoried source; and
- the repository-relative raw report path, format, and SHA-256.

Validation compares the commit and dirty state with the checkout where the
validator runs. Dirty evidence is never release evidence, even when its dirty
flag accurately says `true`. It also recomputes every checked source,
inventory, toolchain-file, and report hash. Thus a report cannot be moved to a
different commit, regenerated source, inventory, toolchain configuration, or
modified raw result without failing.

The `tool` field must contain verbatim version output, for example the complete
`cargo llvm-cov --version` result, rather than a floating package name. The
validator binds that recorded identity into the immutable envelope; campaign
automation is responsible for capturing it from the same process environment.

No checked evidence envelopes or raw reports are present during bootstrap.
Do not create placeholder reports with invented metrics.

## Coverage report assumptions

Two raw formats are accepted:

- `llvm-cov-json` is the unmodified JSON export written by `cargo llvm-cov
  --json --output-path ...`. The validator expects `type` to be
  `llvm.coverage.json.export`, exactly one item in `data`, and per-file
  `summary.lines.{count,covered}` and
  `summary.regions.{count,covered}` counters. It recomputes the core aggregate
  and requires at least 90% lines and 80% regions.
- `lcov` is the LCOV tracefile written by `cargo llvm-cov --lcov
  --output-path ...`. LCOV does not encode LLVM region summaries, so this path
  uses `LF`/`LH` for the 90% line requirement and `BRF`/`BRH` for the 80%
  branch requirement. Campaigns using this path must enable branch reporting
  in the producer. Missing or zero branch totals fail.

The calculation is integer cross-multiplication (`covered * 100 >= total *
threshold`), so exact 90%, 80%, and 85% boundaries pass without floating-point
rounding. The validator does not trust an export-wide `totals` object. It
selects the checked core paths, rejects duplicate or missing core records,
requires positive counters for every core file, and sums only those per-file
counters. Empty exports, zero-denominator files, and high non-core coverage
therefore cannot make the gate pass.

Validate a retained coverage envelope with:

```sh
python3 tools/validate_g6_evidence.py coverage path/to/coverage-evidence.json
```

## Coverage producer

`tools/run_g6_coverage.py` is the checked Linux campaign producer. It requires
an exact expected commit and a clean checkout, validates the checked inventory
before and after the run, and refuses to publish if the commit, source
inventory, toolchain file, or worktree changes during the campaign. Output is
restricted to the ignored `coverage/` tree so the generated report does not
make its own exact-commit validation dirty.

The reviewed producer is pinned to Rust 1.96.0 and cargo-llvm-cov 0.8.6. It
captures the exact `rustup run 1.96.0 cargo llvm-cov --version` output and runs
this fixed argv-only command shape:

```text
rustup run 1.96.0 cargo llvm-cov --workspace --all-targets --all-features --locked --json --output-path <private-report>
```

Cargo network access and Git credential prompts are disabled for the campaign.
Child processes receive an allowlisted environment, so Git redirection and
build-affecting variables are never inherited. The producer refuses to run when
any ambient Cargo configuration could reach the build: a relative `CARGO_HOME`
or `HOME`, a configuration file in the effective Cargo home or any ancestor
directory, or an in-repository `.cargo` configuration that is not tracked and
byte-identical to `HEAD`. The checked inventory must also be tracked and
byte-identical to `HEAD`, read through a single bounded descriptor.
The subprocess has finite time, stdout, stderr, and report-size limits, with
the report bound enforced in the child by a hard file-size resource limit. A raw
report is checked against every inventoried core module and both coverage
thresholds before a canonical envelope and its hash-bound report are published
with create-new semantics. Existing output is never replaced, and failed,
timed-out, stale, malformed, or below-threshold campaigns publish nothing.

For a locally installed cargo-llvm-cov 0.8.6:

```sh
commit="$(git rev-parse HEAD)"
python3 tools/run_g6_coverage.py \
  --expected-commit "$commit" \
  --output "coverage/g6/$commit"
python3 tools/validate_g6_evidence.py coverage \
  "coverage/g6/$commit/coverage-evidence.json"
```

This produces coverage evidence only. It does not satisfy the separate G6
mutation requirement and must not be described as a G6 pass.

## Mutation normalization and scoring

Exactly one native producer format is currently accepted:
`cargo-mutants-outcomes-v26-json`, the completed `mutants.out/outcomes.json`
document produced by cargo-mutants 26.x. Its supported shape is recorded in
`schema/g6-cargo-mutants-outcomes-v26.schema.json`. The format is deliberately
version-pinned because cargo-mutants documents its output files as subject to
change. A newer major version requires a separately reviewed parser and
adversarial tests before its output can count.

Preserve that native output separately, then normalize every produced core
mutant into the version 2 document described by
`schema/g6-mutation-report.schema.json`. The normalized report's
`producer_report` binds the native machine report by the exact format literal,
repository-relative path, and SHA-256; the envelope's `tool`, command, and
normalized-report hash identify the normalization run.

The validator parses the native report before scoring. It requires a completed
26.x run with one successful baseline, consistent native summary counters, a
unique native mutant name, and one normalized record for every native mutant
with no extras. Normalized `id`, `path`, `line`, and `producer_status` must
match the native name, file, span start line, and outcome exactly. Thus a
nonempty arbitrary file, omitted native mutant, invented normalized mutant,
rewritten path/line, duplicate identity, forged producer status, or stale
aggregate counter fails closed.

Native outcomes map mechanically as follows: `CaughtMutant` to `killed`,
`MissedMutant` to `survived`, `Timeout` to `timeout`, and `Unviable` to
`unviable`. `producer_status` preserves that exact mapping. Normally `status`
must equal `producer_status`. A tool-confirmed review may reclassify a
`survived` mutant as `equivalent` or `unreachable`, or an `unviable` mutant as
`unreachable`; no other status transformation is accepted. The separately
hash-bound confirmation requirement below still applies before such a record
is removed from scoring.

The report must declare all four required campaign scopes:
`encoding_decoding`, `allocation`, `row_packing`, and `index`. A scope may be
declared only after its implementation exists and the campaign actually
targeted it. Every mutant names one scope, and each scope must contain at least
one scored (`killed`, `survived`, or `timeout`) mutant; unviable, equivalent,
and unreachable records cannot make a scope non-vacuous. Because the bootstrap
repository does not yet implement every scope, it cannot currently produce
passing G6 mutation evidence.

Every checked core path must have at least one reported mutant. Mutant status
is one of:

- `killed`, included in numerator and denominator;
- `survived` or `timeout`, included in the denominator and requiring a complete
  disposition;
- `equivalent` or `unreachable`, excluded from the score only with a complete
  disposition and a separately hash-bound tool-confirmation artifact; or
- `unviable`, excluded from the score and not treated as a survivor.

The score is `killed / (killed + survived + timeout)` and must be at least 85%.
A report with no scored mutants is vacuous and fails. Every survivor and
timeout records a non-empty owner, rationale, risk, and action. There is no
implicit “known survivor” state.

Each mutant explicitly classifies its relationship to an invariant with
`invariant_kind`. `none` requires an empty `invariant_ids`; any format/safety
classification requires at least one invariant ID. A surviving or timed-out
format/safety mutant blocks immediately, regardless of the aggregate score or
disposition.

An `equivalent` or `unreachable` disposition must identify the confirming tool
and a repository-relative confirmation artifact with exact SHA-256. Prose
alone cannot remove a mutant from the denominator. A missing or stale
confirmation artifact fails.

Validate a retained normalized mutation envelope with:

```sh
python3 tools/validate_g6_evidence.py mutation path/to/mutation-evidence.json
```

## Validator verification

The focused unit suite uses synthetic temporary reports only. It mutation-tests
the validator contract itself: complete inventory discovery, stale hashes,
dirty/commit/toolchain/report bindings, excluded and empty coverage, untrusted
totals, JSON-region and LCOV-branch threshold boundaries, missing branch data,
mutation threshold boundaries, vacuous scores, undispositioned survivors,
timeouts, format/safety survivors, hidden invariant IDs, and
equivalent/unreachable confirmation hashes. It also rejects unsupported or
malformed native producer reports, incomplete native runs, inconsistent
producer counters, duplicate/omitted/invented identities, path/line/status
rewrites, declaration-only scopes, and mostly-unviable campaigns.

Run it with:

```sh
python3 -m unittest tools.tests.test_validate_g6_evidence -v
```
