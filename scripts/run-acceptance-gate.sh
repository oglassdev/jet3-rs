#!/usr/bin/env sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$repository_root"

blocked() {
    echo "BLOCKED: $1" >&2
    exit 3
}

configure_toolchain() {
    if ! command -v rustup >/dev/null 2>&1; then
        blocked "rustup is unavailable, so the pinned Rust 1.96.0 checks cannot run"
    fi
    toolchain_rustc=$(rustup which --toolchain 1.96.0 rustc) || {
        blocked "pinned Rust toolchain 1.96.0 is unavailable"
    }
    toolchain_bin=${toolchain_rustc%/rustc}
    PATH="${toolchain_bin}:${PATH}"
    export PATH
}

run_cargo() {
    rustup run 1.96.0 cargo "$@"
}

case "${1:-}" in
    G0)
        python3 tools/validate_contract.py
        python3 tools/validate_contract.py --self-test
        python3 tools/validate_repository_contract.py
        if ! command -v cargo-deny >/dev/null 2>&1; then
            blocked "G0 cargo-deny is unavailable"
        fi
        configure_toolchain
        run_cargo deny check
        ;;
    G1)
        configure_toolchain
        ./scripts/acceptance.sh quick
        RUSTDOCFLAGS="-D warnings" \
            run_cargo doc --workspace --all-features --no-deps --locked
        python3 -m unittest discover -s tools/tests -v
        if [ -z "${JET3_G1_EVIDENCE:-}" ]; then
            blocked "G1 requires JET3_G1_EVIDENCE to name a downloaded, exact-commit Linux/macOS/Windows aggregate; local checks alone are not release evidence"
        fi
        evidence_commit=$(git rev-parse HEAD)
        python3 tools/ci_evidence.py verify-aggregate \
            "$JET3_G1_EVIDENCE" \
            --expected-commit "$evidence_commit"
        ;;
    G2)
        configure_toolchain
        python3 tools/reconcile_tests.py --repo-root "$repository_root"
        blocked "G2 has fewer than 300 reconciled meaningful tests and lacks the required complete property, golden, capacity, corruption, deterministic-creation, cross-platform, and Miri evidence"
        ;;
    G3)
        python3 oracle/windows-dao/scripts/validate_protocol.py schemas
        python3 oracle/windows-dao/scripts/build_m1_examples.py --check
        python3 oracle/windows-dao/scripts/validate_m1_protocol.py schemas
        python3 oracle/windows-dao/scripts/validate_m1_protocol.py document \
            oracle/windows-dao/examples/m1-inventory.json
        python3 -m unittest discover -s oracle/windows-dao/tests -v
        blocked "G3 has validated M0 and reviewed seven-scenario M1 DAO-only evidence, but lacks the required 100-scenario DAO-versus-Rust differential inventory and exact-release-commit evidence"
        ;;
    G4)
        configure_toolchain
        run_cargo test --package jet3 atomic::tests --locked
        run_cargo test --package jet3 --test atomic_publication --locked
        blocked "G4 lacks an independent structural verifier for Rust-written MDB files and complete commit-bound atomic-update fault and platform recovery evidence"
        ;;
    G5)
        configure_toolchain
        python3 fuzz/tools/fuzz_campaign.py validate
        if ! command -v cargo-fuzz >/dev/null 2>&1; then
            blocked "G5 cannot compile the checked fuzz package because cargo-fuzz is unavailable; the required parser targets and full campaigns are also incomplete"
        fi
        RUSTC_BOOTSTRAP=1 run_cargo fuzz build --fuzz-dir fuzz
        blocked "G5 lacks the required open, catalog, table-definition, row, index, and long-value targets, ten-minute campaigns, malformed-corpus resource enforcement, and adversarial complexity evidence"
        ;;
    G6)
        python3 tools/validate_g6_evidence.py inventory
        if [ -z "${JET3_G6_COVERAGE_EVIDENCE:-}" ] ||
            [ -z "${JET3_G6_MUTATION_EVIDENCE:-}" ]; then
            blocked "G6 requires explicit commit-bound JET3_G6_COVERAGE_EVIDENCE and JET3_G6_MUTATION_EVIDENCE reports"
        fi
        python3 tools/validate_g6_evidence.py coverage \
            "$JET3_G6_COVERAGE_EVIDENCE"
        python3 tools/validate_g6_evidence.py mutation \
            "$JET3_G6_MUTATION_EVIDENCE"
        ;;
    G7)
        configure_toolchain
        run_cargo bench --manifest-path benches/Cargo.toml \
            --bench format_primitives --locked --no-run
        python3 -m unittest discover -s benches/tests -v
        blocked "G7 lacks the required semantic and CRUD benchmark cases, 100000-row datasets, approved commit-bound baselines, peak-RSS and output-size measurements, and regression comparison"
        ;;
    G8)
        test -f .github/workflows/ci.yml
        test -f docs/validation/support-matrix.json
        test -f docs/PROVENANCE.md
        source_status=$(git status --porcelain=v1 --untracked-files=all -- \
            . ':(exclude)artifacts/acceptance/**')
        if [ -n "$source_status" ]; then
            blocked "G8 source tree is dirty; release evidence requires the exact clean commit, and complete CI, release-artifact, DAO, and consumer-project bundles are also absent"
        fi
        blocked "G8 lacks complete clean commit-matched CI evidence, reproducible release artifacts, a Windows DAO bundle, and a clean external-software-free consumer-project report"
        ;;
    *)
        echo "usage: $0 G0|G1|G2|G3|G4|G5|G6|G7|G8" >&2
        exit 2
        ;;
esac
