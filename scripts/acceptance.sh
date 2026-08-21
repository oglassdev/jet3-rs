#!/usr/bin/env sh
set -eu

usage() {
    echo "usage: $0 quick|full" >&2
}

configure_toolchain() {
    toolchain_rustc=$(rustup which --toolchain 1.96.0 rustc)
    toolchain_bin=${toolchain_rustc%/rustc}
    PATH="${toolchain_bin}:${PATH}"
    export PATH
}

run_cargo() {
    rustup run 1.96.0 cargo "$@"
}

run_quick() {
    configure_toolchain
    run_cargo fmt --all --check
    run_cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
    run_cargo test --workspace --all-targets --all-features --locked
    ./scripts/check-source-size.sh
}

run_full() {
    "${PYTHON:-python3}" tools/run_acceptance.py
}

case "${1:-}" in
    quick)
        run_quick
        ;;
    full)
        run_full
        ;;
    *)
        usage
        exit 2
        ;;
esac
