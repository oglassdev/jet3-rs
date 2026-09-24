# Run `just` to list recipes.

PYTHON := env_var_or_default("PYTHON", "python3")
NIGHTLY := "nightly-2026-07-20"

default:
    @just --list

fmt:
    cargo fmt --all

fmt-check:
    cargo fmt --all --check

lint:
    cargo clippy --workspace --all-targets --all-features --locked -- -D warnings

test:
    cargo test --workspace --all-targets --all-features --locked

doc:
    RUSTDOCFLAGS="-D warnings" cargo doc --workspace --all-features --no-deps --locked

# Parser source files stay under 800 lines.
source-size:
    ./scripts/check-source-size.sh

# Support matrix, protocol contract and portable Python tests.
contracts:
    "{{PYTHON}}" -B tools/validate_contract.py
    "{{PYTHON}}" -B -m unittest discover -s tools/tests
    "{{PYTHON}}" -B oracle/windows-dao/scripts/build_v1_2_inventory.py --check
    "{{PYTHON}}" -B oracle/windows-dao/scripts/validate_protocol_v1_2.py schemas
    "{{PYTHON}}" -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory oracle/windows-dao/protocol/v1_2/scenarios.json
    "{{PYTHON}}" -B -m unittest discover -s oracle/windows-dao/tests

# Everything CI checks; run once before publishing changes.
ready: fmt-check lint test doc source-size contracts

bench:
    cargo bench --manifest-path benches/Cargo.toml --locked

# Run every fuzz target for the given number of seconds.
fuzz seconds="60":
    for target in $(cargo +{{NIGHTLY}} fuzz list --fuzz-dir fuzz); do mkdir -p "fuzz/target/corpus/$target" && cargo +{{NIGHTLY}} fuzz run --fuzz-dir fuzz "$target" "fuzz/target/corpus/$target" "fuzz/corpus/$target" -- -max_total_time={{seconds}} || exit 1; done

# Run one PowerShell script under x86 DAO in the local VM.
windows-dev-ps script *args:
    "{{PYTHON}}" scripts/windows-dao-ps.py {{script}} {{args}}
