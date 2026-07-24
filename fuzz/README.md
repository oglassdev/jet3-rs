# Fuzzing

This directory is a standalone
[`cargo-fuzz`](https://github.com/rust-fuzz/cargo-fuzz) package. It is excluded
from the production workspace so ordinary workspace builds do not compile
libFuzzer.

The first format-neutral target is:

- `binary_cursor`: checked offsets and byte counts, work-budget accounting,
  cursor seeks, exact reads, skips, and little-endian primitive reads.

The target treats input as both the cursor's bytes and a stream of nine-byte
commands. It executes at most 256 commands and performs no input-sized
allocation. Its small checked corpus covers zero/tight limits, primitive reads,
and arithmetic boundary-shaped values. `corpus/manifest.json` records each
seed's stable ID, purpose, exact bytes and hash, origin, environment, rights,
and reproduction command.

Install the runner and list the target:

```sh
cargo install cargo-fuzz --locked
cargo fuzz list
```

Run the deterministic 60-second developer smoke:

```sh
cargo fuzz run binary_cursor -- \
  -max_len=4096 -max_total_time=60 -seed=789231
```

Compile without starting a fuzzing campaign:

```sh
cargo fuzz build binary_cursor
```

This is only a foundational target. It does not implement or claim the six
Jet-specific targets, checked malformed corpus, ten-minute acceptance runs,
resource monitoring, or any other completion of validation gate G5.
