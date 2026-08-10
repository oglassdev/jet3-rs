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
  cursor seeks, exact reads, skips, and little-endian primitive reads;
- `binary_writer`: arbitrary bounded sequences of fixed-capacity seeks, exact
  writes, little-endian primitive writes, cumulative encoding/work limits, and
  failure atomicity;
- `jet_header`: generic Jet signature recognition, truncation and read-budget
  rejection, and Jet 3 page-geometry arithmetic;
- `jet3_page`: fixed-size Jet 3 page construction and reads, page-reference
  boundaries, repeated reads, and byte-read, page-visit, and aggregate-work
  policies;
- `raw_jet3_candidate`: bounded composition of generic signature recognition,
  exact 2 KiB geometry, and raw page access under input, read, page-visit, and
  aggregate-work policies;
- `database_opening`: bounded initial database opening, including the generic
  signature, exact page geometry, complete retained page zero, and input,
  read, page-visit, and aggregate-work policy boundaries; and
- `commit_state`: allocation-free capture of the contextual 512-byte commit
  region, exact preservation of all two-byte pairs, narrow pair
  classification, slot-role boundaries, read limits, truncation, and atomic
  destination replacement.

`binary_cursor` treats input as both the cursor's bytes and a stream of
nine-byte commands. It executes at most 256 commands and performs no
input-sized allocation. `binary_writer` executes at most 128 seventeen-byte
commands against a fixed 256-byte stack buffer, borrows exact-write payloads
from the input, and checks each operation against an independent model. Every
failed seek or write must preserve output bytes, position, encoded-byte
accounting, and aggregate-work accounting. `jet_header` runs four bounded
limit scenarios over one borrowed payload and performs no payload-sized
allocation. `jet3_page` borrows at most two 2 KiB pages, executes at most 64
nine-byte commands and 128 page-read attempts, and uses only fixed-size page
buffers.
`raw_jet3_candidate` borrows at most two pages, expands at most one page into a
fixed stack buffer, and performs eight bounded inspections, each followed by
at most one page-read attempt. `database_opening` borrows at most two pages,
expands at most one fixed page, and performs nine bounded open attempts without
input-sized allocation. `commit_state` borrows at most one page, expands
only fixed 512-byte and 2 KiB stack buffers, performs at most sixteen bounded
region reads, and iterates exactly 256 slots per successful snapshot. The
checked corpus covers zero/tight limits, primitive reads, arithmetic
boundary-shaped values, all documented generic Jet signature kinds, unknown
and truncated signatures, exact/partial Jet 3 geometry,
first/last/out-of-range page references, repeated reads, exact and one-below
budgets, page-visit and aggregate-work policies, candidate inspection order,
contextual commit-state pair preservation and classification, exclusive/shared
slot boundaries, truncated commit regions, and unchanged destinations after
failed reads. It also covers writer primitive sequences, zero/exact/past-end
capacity boundaries, rewrites after seeks, and separate encoded-byte and
aggregate-work failures. `corpus/manifest.json` records each seed's stable ID,
purpose, exact bytes and hash, origin, environment, rights, and reproduction
command.

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
python3 fuzz/tools/fuzz_campaign.py smoke \
  --output /absolute/path/to/new-fuzz-evidence-suite
```

The smoke runner copies only manifest-listed seeds into disposable corpora,
rejects corpora over their registered byte bounds, and runs every target for
at least 60 seconds with the registered input and peak-RSS limits. The output
path must not exist and must be outside the Git checkout. Campaigns refuse to
start unless the checkout is completely clean; the complete suite is built
beside its destination and atomically renamed only after every target bundle
validates. Run one smoke or ten-minute campaign with the same retained-evidence
path:

```sh
python3 fuzz/tools/fuzz_campaign.py run binary_cursor --kind smoke \
  --output /absolute/path/to/new-binary-cursor-bundle
python3 fuzz/tools/fuzz_campaign.py run binary_cursor --kind full \
  --output /absolute/path/to/new-binary-cursor-full-bundle
```

Adding a target therefore requires its Cargo bin, source, corpus directory,
seeds, and registry entry together; a missing or seedless target fails
validation.

`schema/campaign-report.schema.json` defines the durable campaign evidence
shape. Each version-3 bundle contains `report.json`, the unmodified combined
cargo-fuzz/libFuzzer `producer.log`, a hashed `observer.json`, `build.json`, and
the raw locked Cargo metadata result. The observer records the exact command,
cargo, cargo-fuzz, and rustc paths/versions/hashes, executed fuzz binary
path/hash, UTC timestamps, monotonic elapsed time, sampled process-tree peak
RSS, exit status, classified outcome, and run count. Every run uses a fresh,
isolated target directory with incremental compilation disabled. Subprocesses
receive only the fully recorded build environment: exact Cargo and rustc
paths, Cargo home, isolated target/temp paths, deterministic locale/color
settings, and a controlled tool/system `PATH`. Ambient SDK, deployment-target,
compiler, linker, include/library, sanitizer, and pkg-config variables are not
passed. `build.json`
binds that binary to the clean commit and tree, complete Git index blob
inventory, fuzz lockfile, applicable Cargo configuration hashes, dependency
closure, controlled build environment, and tool identities. Validate a report
against its retained inputs and the exact clean checkout:

```sh
python3 fuzz/tools/fuzz_campaign.py validate-report path/to/bundle/report.json
```

Validation rejects every dirty checkout or dirty wrapper, stale commit/tree,
target registry, target source, corpus, lockfile, Cargo configuration, Git
index, dependency, or seed drift, malformed timestamps or campaign fields,
mutated or symlinked raw artifacts, non-canonical commands and limits, and
observed wall-clock or peak-RSS breaches. It reparses the retained producer log
and requires its run count, executable path, RSS floor, and outcome to agree
with both the observer and report. Older wrappers without the retained build
closure are not evidence. Reports explicitly identify `smoke` or `full`
campaigns. Smoke reports must cover at least the target's registered duration,
and full reports must cover at least 600 seconds. A campaign report is evidence
about one execution only; it does not by itself change G5 status.

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
The database-opening target does not establish format correctness or replace
the required catalog, table-definition, row, index, or long-value parsers.
Checked malformed-corpus
execution, ten-minute acceptance runs, resource monitoring, and the other
requirements of validation gate G5 remain release blockers.
