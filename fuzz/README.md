# Fuzzing

This directory is a standalone
[`cargo-fuzz`](https://github.com/rust-fuzz/cargo-fuzz) package. It is excluded
from the production workspace so ordinary workspace builds do not compile
libFuzzer.

The checked targets are:

- `binary_cursor`: checked offsets and byte counts, work-budget accounting,
  cursor seeks, exact reads, skips, and little-endian primitive reads; and
- `jet_header`: generic Jet signature recognition, truncation and read-budget
  rejection, and Jet 3 page-geometry arithmetic; and
- `jet3_page`: fixed-size Jet 3 page construction and reads, page-reference
  boundaries, repeated reads, and byte-read, page-visit, and aggregate-work
  policies.

`binary_cursor` treats input as both the cursor's bytes and a stream of
nine-byte commands. It executes at most 256 commands and performs no
input-sized allocation. `jet_header` runs four bounded limit scenarios over
one borrowed payload and performs no payload-sized allocation. `jet3_page`
borrows at most two 2 KiB pages, executes at most 64 nine-byte commands and
128 page-read attempts, and uses only fixed-size page buffers. The checked
corpus covers zero/tight limits, primitive reads, arithmetic boundary-shaped
values, all documented generic Jet signature kinds, unknown and truncated
signatures, exact/partial Jet 3 geometry, first/last/out-of-range page
references, repeated reads, and exact, one-below, page-visit, and aggregate
work policies. `corpus/manifest.json` records each seed's stable ID, purpose,
exact bytes and hash, origin, environment, rights, and reproduction command.

Install the runner and list the target:

```sh
cargo install cargo-fuzz --locked
cargo fuzz list
```

Run a deterministic 60-second developer smoke:

```sh
cargo fuzz run binary_cursor -- \
  -max_len=4096 -max_total_time=60 -seed=789231
cargo fuzz run jet_header -- \
  -max_len=4096 -max_total_time=60 -seed=789231
cargo fuzz run jet3_page -- \
  -max_len=8192 -max_total_time=60 -seed=789231
```

Compile without starting a fuzzing campaign:

```sh
cargo fuzz build
```

These remain foundational targets. Generic signature recognition does not
identify a Jet version or validate a database. Page alignment and content-
agnostic page reads do not establish that a source is a valid Jet 3 database.
These targets do not replace the required database-opening, catalog,
table-definition, row, index, or long-value parsers. Checked malformed-corpus
execution, ten-minute acceptance runs, resource monitoring, and the other
requirements of validation gate G5 remain release blockers.
