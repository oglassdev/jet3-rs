# Run `just` to list recipes.

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

quick:
    ./scripts/acceptance.sh quick

# Full release contract; by design exits BLOCKED until every gate has current-commit evidence.
accept:
    ./scripts/acceptance.sh full

# Everything currently green-able; run before publishing changes.
ready: fmt-check lint test doc quick
