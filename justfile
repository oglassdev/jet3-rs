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

# Full release check for one DAO differential bundle.
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

# Create the private Jet 3/4, encryption, and password opening matrix.
windows-dev-opening:
    "{{PYTHON}}" scripts/windows-dao-dev.py opening-matrix

# Discover allocation-map transitions without assuming a conversion threshold.
windows-dev-allocation:
    "{{PYTHON}}" scripts/windows-dao-dev.py allocation-map --timeout 900

# Discover catalog location and records with bounded create/drop/recreate checkpoints.
windows-dev-catalog:
    "{{PYTHON}}" scripts/windows-dao-dev.py catalog --timeout 180

# Discover table-definition, column, index, and relationship metadata.
windows-dev-table-definition:
    "{{PYTHON}}" scripts/windows-dao-dev.py table-definition --timeout 300

windows-dev-row:
    "{{PYTHON}}" scripts/windows-dao-dev.py row --timeout 300

windows-dev-value:
    "{{PYTHON}}" scripts/windows-dao-dev.py value --timeout 600

# Discover index-tree pages, key encodings, and relationship metadata.
windows-dev-index:
    "{{PYTHON}}" scripts/windows-dao-dev.py index --timeout 600

# Run the preregistered writer-bootstrap layout experiment in the local VM.
windows-dev-bootstrap-layout:
    "{{PYTHON}}" scripts/windows-dao-dev.py bootstrap-layout --timeout 900

# Run the preregistered system-catalog semantics experiment in the local VM.
windows-dev-system-catalog:
    "{{PYTHON}}" scripts/windows-dao-dev.py system-catalog --timeout 900

# Run the preregistered long-value column-map experiment in the local VM.
windows-dev-long-value-maps:
    "{{PYTHON}}" scripts/windows-dao-dev.py long-value-maps --timeout 900

# Run the preregistered corrected long-value column-map experiment.
windows-dev-long-value-maps-followup:
    "{{PYTHON}}" scripts/windows-dao-dev.py long-value-maps-followup --timeout 900

# Run the preregistered fixed bootstrap-composer semantics experiment.
windows-dev-bootstrap-composer-semantics:
    "{{PYTHON}}" scripts/windows-dao-dev.py bootstrap-composer-semantics --timeout 900

# Run the preregistered schema-generalization experiment in the local VM.
windows-dev-schema-generalization:
    "{{PYTHON}}" scripts/windows-dao-dev.py schema-generalization --timeout 900

# Run the preregistered null-LvProp acceptance experiment in the local VM.
windows-dev-lvprop-null:
    "{{PYTHON}}" scripts/windows-dao-dev.py lvprop-null --timeout 900

# Run one ad-hoc PowerShell script under x86 DAO in the local VM (discovery only).
windows-dev-ps script *args:
    "{{PYTHON}}" scripts/windows-dao-ps.py {{script}} {{args}}
