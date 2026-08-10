#!/usr/bin/env sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
output="$repository_root/artifacts/benchmarks/metadata-smoke-$$.json"

cleanup() {
    rm -f "$output"
}
trap cleanup EXIT HUP INT TERM

source_status() {
    git -C "$repository_root" status \
        --porcelain \
        --untracked-files=all \
        -- \
        . \
        ':(exclude)artifacts/benchmarks/**'
}

before=$(source_status)
"$repository_root/benches/scripts/capture_metadata.sh" "$output"
after=$(source_status)

if [ "$before" != "$after" ]; then
    echo "metadata output changed filtered source status" >&2
    exit 1
fi

if [ -n "$before" ]; then
    expected_dirty=true
else
    expected_dirty=false
fi
jq -e --argjson expected "$expected_dirty" '.dirty == $expected' "$output" >/dev/null
jq -e '.suite_digest_sha256 | test("^[0-9a-f]{64}$")' "$output" >/dev/null
