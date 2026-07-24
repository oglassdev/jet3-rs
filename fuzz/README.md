# Fuzzing

This directory is a standalone
[`cargo-fuzz`](https://github.com/rust-fuzz/cargo-fuzz) package. It is excluded
from the production workspace so ordinary workspace builds do not compile
libFuzzer.

`targets.json` is the checked, non-vacuous target registry and the single
source of truth for target names, sources, corpora, deterministic smoke
durations, input bounds, corpus byte bounds, and peak-RSS limits. The registry,
Cargo fuzz bins, target sources, seed files, and seed manifest must agree
exactly. The checked targets are:

- `binary_cursor`: checked offsets and byte counts, work-budget accounting,
  cursor seeks, exact reads, skips, and little-endian primitive reads; and
- `jet_header`: generic Jet signature recognition, truncation and read-budget
  rejection, and Jet 3 page-geometry arithmetic;
- `jet3_page`: fixed-size Jet 3 page construction and reads, page-reference
  boundaries, repeated reads, and byte-read, page-visit, and aggregate-work
  policies;
- `raw_jet3_candidate`: bounded composition of generic signature recognition,
  exact 2 KiB geometry, and raw page access under input, read, page-visit, and
  aggregate-work policies; and
- `commit_state`: allocation-free capture of the contextual 512-byte commit
  region, exact preservation of all two-byte pairs, narrow pair
  classification, slot-role boundaries, read limits, truncation, and atomic
  destination replacement.

`binary_cursor` treats input as both the cursor's bytes and a stream of
nine-byte commands. It executes at most 256 commands and performs no
input-sized allocation. `jet_header` runs four bounded limit scenarios over
one borrowed payload and performs no payload-sized allocation. `jet3_page`
borrows at most two 2 KiB pages, executes at most 64 nine-byte commands and
128 page-read attempts, and uses only fixed-size page buffers.
`raw_jet3_candidate` borrows at most two pages, expands at most one page into a
fixed stack buffer, and performs eight bounded inspections, each followed by
at most one page-read attempt. `commit_state` borrows at most one page, expands
only fixed 512-byte and 2 KiB stack buffers, performs at most sixteen bounded
region reads, and iterates exactly 256 slots per successful snapshot. The
checked corpus covers zero/tight limits, primitive reads, arithmetic
boundary-shaped values, all documented generic Jet signature kinds, unknown
and truncated signatures, exact/partial Jet 3 geometry,
first/last/out-of-range page references, repeated reads, exact and one-below
budgets, page-visit and aggregate-work policies, candidate inspection order,
contextual commit-state pair preservation and classification, exclusive/shared
slot boundaries, truncated commit regions, and unchanged destinations after
failed reads. `corpus/manifest.json` records each seed's stable ID, purpose,
exact bytes and hash, origin, environment, rights, and reproduction command.

Install the runner and list the target:

```sh
cargo install cargo-fuzz --locked
cargo fuzz list
```

Validate the registry, every seed's metadata/size/hash/reproduction field, and
the campaign-report validator:

```sh
python3 -m unittest discover -s fuzz/tests -v
python3 fuzz/tools/fuzz_campaign.py validate
```

Run the deterministic developer/CI smoke for every registered target:

```sh
python3 fuzz/tools/fuzz_campaign.py smoke
```

The smoke runner copies only manifest-listed seeds into disposable corpora,
rejects corpora over their registered byte bounds, and runs every target for
at least 60 seconds with the registered input and peak-RSS limits. Adding a
target therefore requires its Cargo bin, source, corpus directory, seeds, and
registry entry together; a missing or seedless target fails validation.

`schema/campaign-report.schema.json` defines the durable campaign evidence
shape. Validate a report against both that contract and the current checkout:

```sh
python3 fuzz/tools/fuzz_campaign.py validate-report path/to/report.json
```

Validation rejects stale commit or dirty-state metadata, target registry,
target source, corpus, or seed hash drift, malformed timestamps or campaign
fields, and observed wall-clock or peak-RSS breaches. Reports explicitly
identify `smoke` or `full` campaigns. Smoke reports must cover at least the
target's registered duration, and full reports must cover at least 600
seconds. A campaign report is evidence about one execution only; it does not
by itself change G5 status.

Compile without starting a fuzzing campaign:

```sh
cargo fuzz build
```

These remain foundational targets. Generic signature recognition does not
identify a Jet version or validate a database. Page alignment and content-
agnostic page reads do not establish that a source is a valid Jet 3 database.
Commit-region values are volatile and contextual; without contemporaneous
`.ldb` lock evidence they do not establish validity, corruption, clean
shutdown, Jet generation, user ownership, or compatibility.
These targets do not replace the required database-opening, catalog,
table-definition, row, index, or long-value parsers. Checked malformed-corpus
execution, ten-minute acceptance runs, resource monitoring, and the other
requirements of validation gate G5 remain release blockers.
