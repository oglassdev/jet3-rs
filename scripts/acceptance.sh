#!/usr/bin/env sh
set -eu

usage() {
    echo "usage: $0 quick|full [DAO_BUNDLE]" >&2
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
    dao_bundle=${1:-${JET3_DAO_BUNDLE:-}}
    if [ -z "$dao_bundle" ]; then
        echo "full acceptance requires a DAO bundle path argument or JET3_DAO_BUNDLE" >&2
        return 1
    fi
    if [ ! -e "$dao_bundle" ]; then
        echo "DAO bundle does not exist: $dao_bundle" >&2
        return 1
    fi

    run_quick
    RUSTDOCFLAGS="-D warnings" run_cargo doc --workspace --all-features --no-deps --locked
    "${PYTHON:-python3}" -B tools/validate_contract.py
    "${PYTHON:-python3}" -B \
        oracle/windows-dao/scripts/validate_protocol_v1_2.py document "$dao_bundle"
}

case "${1:-}" in
    quick)
        run_quick
        ;;
    full)
        if [ "$#" -gt 2 ]; then
            usage
            exit 2
        fi
        run_full "${2:-}"
        ;;
    *)
        usage
        exit 2
        ;;
esac
