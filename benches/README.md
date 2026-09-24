# Benchmarks

A standalone Criterion package for the binary foundations (cursor and writer
primitives, signature and geometry checks, page reads, raw page streaming and
resource budgets). It stays out of the workspace so Criterion never enters the
production lockfile. Inputs are generated in code; no MDB files are used.

```sh
just bench                                            # run every benchmark
cargo bench --manifest-path benches/Cargo.toml -- --test   # execute each once
```
