#!/usr/bin/env sh
set -eu

toolchain_rustc=$(rustup which --toolchain 1.96.0 rustc)
toolchain_bin=${toolchain_rustc%/rustc}
PATH="${toolchain_bin}:${PATH}"
export PATH

usage() {
    echo "usage: $0 quick|full" >&2
}

run_cargo() {
    rustup run 1.96.0 cargo "$@"
}

run_quick() {
    run_cargo fmt --all --check
    run_cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
    run_cargo test --workspace --all-targets --all-features --locked
    ./scripts/check-source-size.sh
}

run_full() {
    run_quick
    RUSTDOCFLAGS="-D warnings" \
        run_cargo doc --workspace --all-features --no-deps --locked

    echo "BLOCKED: the full v1 gates in docs/validation/ are not wired yet" >&2
    exit 3
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
