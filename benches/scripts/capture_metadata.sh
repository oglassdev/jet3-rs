#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 OUTPUT.json" >&2
    exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "BLOCKED: jq is required for benchmark metadata capture" >&2
    exit 3
fi
if ! command -v rustup >/dev/null 2>&1; then
    echo "BLOCKED: rustup is required for the pinned benchmark toolchain" >&2
    exit 3
fi

repository_root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
output=$1
output_directory=$(dirname "$output")
temporary="${output}.tmp.$$"

cleanup() {
    rm -f "$temporary"
}
trap cleanup EXIT HUP INT TERM

toolchain_rustc=$(rustup which --toolchain 1.96.0 rustc) || {
    echo "BLOCKED: pinned Rust toolchain 1.96.0 is unavailable" >&2
    exit 3
}
toolchain_bin=${toolchain_rustc%/rustc}
PATH="${toolchain_bin}:${PATH}"
export PATH

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        echo "BLOCKED: sha256sum or shasum is required" >&2
        exit 3
    fi
}

case "$(uname -s)" in
    Darwin)
        os_description="$(sw_vers -productName) $(sw_vers -productVersion) ($(sw_vers -buildVersion))"
        cpu_description=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)
        logical_cpus=$(sysctl -n hw.logicalcpu)
        memory_bytes=$(sysctl -n hw.memsize)
        ;;
    Linux)
        if [ -r /etc/os-release ]; then
            os_description=$(awk -F= '$1 == "PRETTY_NAME" {gsub(/^"|"$/, "", $2); print $2}' /etc/os-release)
        else
            os_description=$(uname -srv)
        fi
        cpu_description=$(awk -F: '$1 ~ /^model name/ {sub(/^[ \t]+/, "", $2); print $2; exit}' /proc/cpuinfo)
        logical_cpus=$(getconf _NPROCESSORS_ONLN)
        memory_kib=$(awk '$1 == "MemTotal:" {print $2}' /proc/meminfo)
        memory_bytes=$((memory_kib * 1024))
        ;;
    *)
        echo "BLOCKED: metadata capture supports macOS and Linux only" >&2
        exit 3
        ;;
esac

source_status=$(git -C "$repository_root" status \
    --porcelain \
    --untracked-files=all \
    -- \
    . \
    ':(exclude)artifacts/benchmarks/**')
if [ -n "$source_status" ]; then
    dirty=true
else
    dirty=false
fi

git_commit=$(git -C "$repository_root" rev-parse HEAD)
captured_at_utc=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
architecture=$(uname -m)
rustc_description=$(rustc -vV)
cargo_description=$(cargo -V)
manifest_hash=$(sha256_file "$repository_root/benches/manifest.json")
lockfile_hash=$(sha256_file "$repository_root/benches/Cargo.lock")

mkdir -p "$output_directory"
jq -n \
    --arg git_commit "$git_commit" \
    --argjson dirty "$dirty" \
    --arg captured_at_utc "$captured_at_utc" \
    --arg os "$os_description" \
    --arg architecture "$architecture" \
    --arg cpu "$cpu_description" \
    --argjson logical_cpus "$logical_cpus" \
    --argjson memory_bytes "$memory_bytes" \
    --arg rustc "$rustc_description" \
    --arg cargo "$cargo_description" \
    --arg benchmark_manifest_sha256 "$manifest_hash" \
    --arg benchmark_lockfile_sha256 "$lockfile_hash" \
    '{
      git_commit: $git_commit,
      dirty: $dirty,
      captured_at_utc: $captured_at_utc,
      os: $os,
      architecture: $architecture,
      cpu: $cpu,
      logical_cpus: $logical_cpus,
      memory_bytes: $memory_bytes,
      rustc: $rustc,
      cargo: $cargo,
      benchmark_manifest_sha256: $benchmark_manifest_sha256,
      benchmark_lockfile_sha256: $benchmark_lockfile_sha256
    }' > "$temporary"
mv "$temporary" "$output"
trap - EXIT HUP INT TERM
