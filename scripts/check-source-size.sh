#!/usr/bin/env sh
set -eu

find crates/jet3/src -type f -name '*.rs' -exec sh -c '
    status=0
    for source_file do
        line_count=$(wc -l < "$source_file")
        if [ "$line_count" -gt 800 ]; then
            echo "$source_file has $line_count lines; maximum is 800" >&2
            status=1
        fi
    done
    exit "$status"
' sh {} +
