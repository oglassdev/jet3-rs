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
  read, page-visit, and aggregate-work policy boundaries;
- `commit_state`: allocation-free capture of the contextual 512-byte commit
  region, exact preservation of all two-byte pairs, narrow pair
  classification, slot-role boundaries, read limits, truncation, and atomic
  destination replacement; and
- `page_classification`: allocation-free, contextual classification of a
  complete fixed page from byte zero only, including every documented tag,
  lossless unknown tags, and exact classification-work boundaries
  (`SRC-0020`); and
- `allocation`: detached, allocation-free decoding of caller-delimited inline
  and indirect allocation maps plus already-classified extended bitmap pages,
  including truncation, unsupported types, bit and reference boundaries, and
  exact item/work limits, plus bounded page-chain following of input-selected
  page numbers over a synthetic database with chain-depth, page-visit,
  repeat, and required-kind boundaries (`SRC-0020`); and
- `usage_map_traverse`: fixed-memory, end-to-end owned-page traversal over a
  synthetic Jet 3 database, including table-definition map locators, inline
  and indirect records, direct type-05 references, slot-relative extended
  bases, null slots, repeats, and out-of-capture references (`EXP-0057`).

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
region reads, and iterates exactly 256 slots per successful snapshot.
`page_classification` expands one fixed 2 KiB stack page, performs two
selected-policy classifications, and checks twelve fixed contextual mappings.
`allocation` borrows at most one 4 KiB input, uses fixed stack records, one
fixed 2 KiB page, and one fixed nine-page synthetic database, performs at most
65 calls in each input-selected cursor and at most eight page-chain steps per
traversal, and scans at most 16,352 fixed extended-bitmap bits in its exact
boundary check.
`usage_map_traverse` borrows at most one 4 KiB input, constructs one fixed
nine-page synthetic database, retains two fixed page buffers, follows at most
the 33 indirect slots encoded in its fixed map row, and returns after at
most 65 owned pages while all reads, visits, items, work, and allocations stay
under input-selected limits.
The checked corpus covers zero/tight limits, primitive reads, arithmetic
boundary-shaped values, all documented generic Jet signature kinds, unknown
and truncated signatures, exact/partial Jet 3 geometry,
first/last/out-of-range page references, repeated reads, exact and one-below
budgets, page-visit and aggregate-work policies, candidate inspection order,
contextual commit-state pair preservation and classification, exclusive/shared
slot boundaries, truncated commit regions, and unchanged destinations after
failed reads. It covers page-zero and nonzero tag contexts, all six documented
byte-zero tag values, explicitly unknown tag eight, arbitrary unknown tags,
and zero/exact/repeated classification work limits. It also covers writer
primitive sequences, zero/exact/past-end
capacity boundaries, rewrites after seeks, and separate encoded-byte and
aggregate-work failures. Detached allocation coverage includes type-0 lengths
zero through four, unsupported record types, caller-sized inline final bits,
aligned and misaligned indirect references, preserved zero references,
extended bits zero, seven, eight, and 16,351, ignored unknown extended-header
bytes, and retry without advancement after tight resource failures.
End-to-end usage-map coverage includes inline boundaries, all-zero indirect
slots, repeated direct references, references beyond captured input, and
slot-relative type-05 bitmap traversal.
`corpus/manifest.json` records each seed's stable ID,
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
  --jobs 4 \
  --output /absolute/path/to/new-fuzz-evidence-suite
```

The smoke runner copies only manifest-listed seeds into per-target disposable
corpora, rejects corpora over their registered byte bounds, and runs every
target for at least 60 seconds with the registered input and peak-RSS limits.
It runs up to `min(4, os.cpu_count())` targets concurrently by default; use
`--jobs 1` for the previous serial behavior. Each target retains its own
observer and process resource accounting, and suite reports are ordered by
target name. The output path must not exist and must be outside the Git
checkout. Campaigns refuse to start unless the checkout is completely clean;
the complete suite is built beside its destination and atomically renamed only
after every target bundle validates. Run one smoke or ten-minute campaign with
the same retained-evidence path:

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
shape. Each version-3 bundle contains `report.json`, the unmodified libFuzzer
`producer.log`, a hashed `observer.json`, `build.json`, and the raw locked Cargo
metadata result. After an isolated cargo-fuzz prebuild, the observer executes
the retained target binary directly and records the exact command,
cargo, cargo-fuzz, and rustc paths/versions/hashes, executed fuzz binary
path/hash, UTC timestamps, monotonic elapsed time, sampled process-tree peak
RSS combined with the child's OS high-water RSS, exit status, classified
outcome, and run count. Every run uses a fresh,
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
and requires its run count, RSS floor, and outcome to agree with both the
observer and report, while the direct command and retained prebuild bind the
executable path and hash. Older wrappers without the retained build
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
Page classification is experimental, inspects only byte zero, does not
validate any remaining page header or payload, and is not DAO-verified.
Allocation-map decoding is likewise experimental and detached: it does not
locate global or per-table records, follow indirect references, infer an
extended bitmap's database-page base, or establish ownership/free-space
semantics, and it is not DAO-verified.
The database-opening target does not establish format correctness or replace
the required catalog, table-definition, row, index, or long-value parsers.
Checked malformed-corpus
execution, ten-minute acceptance runs, resource monitoring, and the other
requirements of validation gate G5 remain release blockers.
