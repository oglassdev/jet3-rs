# Run `just` to list recipes.

PYTHON := env_var_or_default("PYTHON", "python3")

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
    PYTHON="{{PYTHON}}" ./scripts/acceptance.sh quick

# Full release contract; by design exits BLOCKED until every gate has current-commit evidence.
accept:
    PYTHON="{{PYTHON}}" ./scripts/acceptance.sh full

# Everything currently green-able; run before publishing changes.
ready: fmt-check lint test doc quick

# Exploratory only; output cannot satisfy release evidence or compatibility claims.
windows-dev-probe:
    "{{PYTHON}}" scripts/windows-dao-dev.py provider-probe

# Create and reopen one private empty Jet 3 database through local DAO.
windows-dev-empty:
    "{{PYTHON}}" scripts/windows-dao-dev.py create-empty
