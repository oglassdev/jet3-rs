# Fuzzing

A standalone [`cargo-fuzz`](https://github.com/rust-fuzz/cargo-fuzz) package,
kept out of the workspace so ordinary builds never compile libFuzzer. Each
target in `fuzz_targets/` has synthetic seeds under `corpus/<target>/`.

```sh
just fuzz 60                                   # every target, 60 s each
cargo +nightly-2026-07-20 fuzz run --fuzz-dir fuzz row_parsing -- -max_total_time=300
```

`just fuzz` writes new corpus entries under `fuzz/target/corpus/` so the
checked seeds stay unchanged. Crashes land in `fuzz/artifacts/`.
