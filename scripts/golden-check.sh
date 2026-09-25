#!/usr/bin/env bash
# Behavior-preservation check: build jet3-cli at <base-rev> and in the working tree,
# run the working tree's corpus of database-producing requests with each build, then
# compare SHA-256 of every output file (databases, request results, and each
# command's stdout/stderr/exit).
#
# The corpus is scripts/golden_corpus.py (every DAO registry suite's candidates and
# the recipes of archived DAO candidates in scripts/golden_recipes) plus the CLI
# requests below. <base-rev> must accept the corpus requests (resource limit flags
# and raw long values, jet3-cli from #380 part 6 on).
#
# Every command runs from its side's output root with relative paths, so outputs
# that echo their own path match. Base==head runs show nothing else varies (no
# timestamps, pids or temp paths reach any output), so nothing is normalized.
#
# Not run: row-overflow unless JET3_ROW_OVERFLOW_CAPTURES names its retained DAO
# captures; suite continuations and refusal inputs derived from DAO outputs. The
# allocation-lifecycle suite currently stops on a read-budget limit; its partial
# outputs and error are still compared.
set -euo pipefail
[[ $# -eq 1 ]] || { echo "usage: $0 <base-rev>" >&2; exit 2; }
root=$(git rev-parse --show-toplevel)
base=$(git -C "$root" rev-parse --verify "$1^{commit}")
# Prefer tmpfs: the corpus publishes atomically thousands of times and fsync on disk is slow.
tmp=$(mktemp -d -p /dev/shm 2>/dev/null || mktemp -d)
cleanup() { git -C "$root" worktree remove --force "$tmp/src" >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT
start=$SECONDS

git -C "$root" worktree add --quiet --detach "$tmp/src" "$base"
build() { (cd "$1" && CARGO_TARGET_DIR="$2" cargo build --quiet --release -p jet3-cli); }
echo "building base $(git -C "$root" rev-parse --short "$base") and working tree..."
build "$tmp/src" "$root/target/golden-base"
build "$root" "$root/target"

# run NAME CMD...: record stdout, stderr and exit status of CMD under log/NAME.*
run() {
  local name=$1 rc=0; shift
  mkdir -p "log/$(dirname "$name")"
  "$@" >"log/$name.out" 2>"log/$name.err" || rc=$?
  echo "$rc" >"log/$name.exit"
}
rep() { printf "$1%.0s" $(seq "$2"); }  # rep TEXT N: TEXT repeated N times

cli_corpus() {
  local cli=$1
  mkdir -p cli
  run cli/create-types "$cli" create cli/types.mdb --input - <<EOF
{"tables": [
 {"name": "Parent", "columns": [
   {"name": "Id", "type": "long", "required": true},
   {"name": "Name", "type": "text", "size": 50, "required": true, "description": "Display name"},
   {"name": "Code", "type": "fixed_text", "size": 4, "allow_zero_length": true},
   {"name": "Price", "type": "currency", "default_value": "0"},
   {"name": "Rate", "type": "double"}, {"name": "Score", "type": "single"},
   {"name": "Qty", "type": "integer"}, {"name": "Tiny", "type": "byte"},
   {"name": "Seen", "type": "date_time"}, {"name": "Flag", "type": "boolean"},
   {"name": "Uid", "type": "guid"}, {"name": "Notes", "type": "memo", "allow_zero_length": true},
   {"name": "Blob", "type": "long_binary"}, {"name": "Raw", "type": "binary", "size": 8}],
  "indexes": [
   {"name": "PrimaryKey", "kind": "primary", "fields": [{"column": "Id"}]},
   {"name": "ByName", "kind": "unique", "fields": [{"column": "Name"}], "null_policy": "ignore_all_null"},
   {"name": "BySeen", "kind": "ordinary", "fields": [{"column": "Seen", "direction": "descending"}, {"column": "Qty"}]}],
  "rows": [
   [{"long": 1}, {"text": "alpha"}, {"text": "AB  "}, {"currency": 123450}, {"double": 2.5}, {"single": -1.25}, {"integer": 7}, {"byte": 255}, {"date_time": 36526.5}, {"boolean": true}, {"guid": [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]}, {"memo": "$(rep m 5000)"}, {"long_binary": [0,1,2,3]}, {"binary": [9,8,7]}],
   [{"long": 2}, {"text": "beta"}, null, null, null, null, {"integer": 0}, null, {"date_time": -1.5}, {"boolean": false}, null, {"memo": [233,232]}, null, null],
   [{"long": 3}, {"text": "gamma"}, {"text": "WXYZ"}, {"currency": -1}, {"double": 1e300}, {"single": 0.0}, {"integer": 32767}, {"byte": 0}, null, {"boolean": false}, null, null, null, {"binary": []}]]},
 {"name": "Child", "columns": [
   {"name": "Id", "type": "auto_increment"}, {"name": "ParentId", "type": "long"}, {"name": "Body", "type": "text", "size": 100}],
  "indexes": [
   {"name": "PrimaryKey", "kind": "primary", "fields": [{"column": "Id"}]},
   {"name": "ByParent", "kind": "ordinary", "fields": [{"column": "ParentId"}]}],
  "rows": [["auto_increment", {"long": 1}, {"text": "c1"}], ["auto_increment", {"long": 1}, {"text": "c2"}], ["auto_increment", {"long": 2}, null], ["auto_increment", null, {"text": "orphan"}]]}],
 "relationships": [{"name": "ParentChild", "cascade_deletes": true, "cascade_updates": true,
   "parent": {"table": "Parent", "column": "Id"}, "child": {"table": "Child", "column": "ParentId"}}]}
EOF
  # Schema edits, applied in order to one copy; every intermediate state is kept.
  cp cli/types.mdb cli/schema.mdb 2>/dev/null || true
  local n=0 edit
  while IFS= read -r edit; do
    n=$((n + 1))
    run "cli/schema-$n" "$cli" schema cli/schema.mdb --input - <<<"$edit"
    cp cli/schema.mdb "cli/schema-$n.mdb" 2>/dev/null || true
  done <<'EOF'
{"operation": "set_table_properties", "table": "Parent", "validation_rule": "[Qty] >= 0", "validation_text": "Qty must be non-negative"}
{"operation": "set_column_properties", "table": "Parent", "column": "Qty", "validation_rule": ">= 0", "description": "Quantity", "default_value": "1"}
{"operation": "set_column_options", "table": "Child", "column": "Body", "allow_zero_length": true}
{"operation": "set_column_options", "table": "Parent", "column": "Qty", "required": true}
{"operation": "create_column", "table": "Parent", "column": {"name": "Extra", "type": "text", "size": 20}}
{"operation": "rename_column", "table": "Parent", "column": "Extra", "name": "Extra2"}
{"operation": "create_index", "table": "Parent", "index": {"name": "ByTiny", "kind": "ordinary", "fields": [{"column": "Tiny"}]}}
{"operation": "rename_index", "table": "Parent", "index": "ByTiny", "name": "ByTinyValue"}
{"operation": "replace_index", "table": "Parent", "index": "ByTinyValue", "replacement": {"name": "ByTinyValue", "kind": "ordinary", "fields": [{"column": "Tiny", "direction": "descending"}]}}
{"operation": "create_table", "table": {"name": "Extra", "columns": [{"name": "Id", "type": "long"}, {"name": "ParentId", "type": "long"}, {"name": "Note", "type": "memo"}], "indexes": [{"name": "PrimaryKey", "kind": "primary", "fields": [{"column": "Id"}]}]}}
{"operation": "create_relationship", "relationship": {"name": "ExtraParent", "enforce": false, "join": "left", "parent": {"table": "Parent", "column": "Id"}, "child": {"table": "Extra", "column": "ParentId"}}}
{"operation": "replace_relationship", "name": "ParentChild", "relationship": {"name": "ParentChild", "parent": {"table": "Parent", "column": "Id"}, "child": {"table": "Child", "column": "ParentId"}}}
{"operation": "drop_relationship", "name": "ExtraParent"}
{"operation": "drop_column", "table": "Parent", "column": "Raw"}
{"operation": "drop_column", "table": "Parent", "column": "Id"}
{"operation": "drop_index", "table": "Parent", "index": "BySeen"}
{"operation": "rename_table", "table": "Extra", "name": "Extra3"}
{"operation": "drop_table", "table": "Extra3"}
{"operation": "set_column_options", "table": "Child", "column": "Body", "required": true}
EOF
  # Row mutations; update/replace/delete target the locator an earlier insert reported.
  cp cli/types.mdb cli/mutate.mdb 2>/dev/null || true
  mut() { run "cli/mutate-$1" "$cli" mutate cli/mutate.mdb --input - <<<"$2"; cp cli/mutate.mdb "cli/mutate-$1.mdb" 2>/dev/null || true; }
  loc() { jq -c .row "log/cli/mutate-$1.out" 2>/dev/null || echo null; }
  mut 01-insert '{"operation": "insert", "table": "Parent", "values": [{"long": 10}, {"text": "delta"}, {"text": "DDDD"}, {"currency": 5}, {"double": -0.0}, {"single": 3.5}, {"integer": -2}, {"byte": 1}, {"date_time": 2.25}, {"boolean": true}, null, {"memo": "'"$(rep n 3000)"'"}, {"long_binary": [5,6]}, {"binary": [1]}]}'
  mut 02-insert-child '{"operation": "insert", "table": "Child", "values": ["auto_increment", {"long": 10}, {"text": "child of 10"}]}'
  mut 03-update '{"operation": "update", "table": "Parent", "row": '"$(loc 01-insert)"', "column": 1, "value": {"text": "renamed"}}'
  mut 04-replace '{"operation": "replace", "table": "Parent", "row": '"$(loc 01-insert)"', "values": [{"long": 10}, {"text": "replaced"}, null, null, null, null, {"integer": 1}, null, null, {"boolean": false}, null, null, null, null]}'
  mut 05-cascade-update '{"operation": "update", "table": "Parent", "row": '"$(loc 01-insert)"', "column": 0, "value": {"long": 11}}'
  mut 06-orphan '{"operation": "insert", "table": "Child", "values": ["auto_increment", {"long": 99}, null]}'
  mut 07-duplicate '{"operation": "insert", "table": "Parent", "values": [{"long": 12}, {"text": "alpha"}, null, null, null, null, null, null, null, {"boolean": false}, null, null, null, null]}'
  mut 08-update-child '{"operation": "update", "table": "Child", "row": '"$(loc 02-insert-child)"', "column": 2, "value": null}'
  mut 09-cascade-delete '{"operation": "delete", "table": "Parent", "row": '"$(loc 01-insert)"'}'

}

corpus() { # BIN_DIR OUT_DIR
  local cli=$1/jet3-cli
  mkdir -p "$2" && cd "$2"
  run corpus python3 "$root/scripts/golden_corpus.py" --cli "$cli" --out gen &
  cli_corpus "$cli" &
  wait
  # Read-path coverage: validate every produced database.
  local f
  find gen cli -name '*.mdb' | LC_ALL=C sort | while read -r f; do run "validate/$f" "$cli" validate "$f"; done
}

echo "running corpus..."
(corpus "$root/target/golden-base/release" "$tmp/base") &
(corpus "$root/target/release" "$tmp/head") &
wait

sums() { (cd "$1" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | awk '{print $2, $1}'); }
LC_ALL=C join -a1 -a2 -e - -o 0,1.2,2.2 <(sums "$tmp/base") <(sums "$tmp/head") | awk -v secs=$((SECONDS - start)) '
  { s = $2 == $3 ? "identical" : $2 == "-" ? "only-head" : $3 == "-" ? "only-base" : "differs"; n[s]++
    if (s != "identical") printf "%-10s %s\n", s, $1
    if ($1 !~ /^\.\/log\//) { if ($2 != "-") b++; if ($3 != "-") h++ } }
  END { printf "\n%d files: %d identical, %d differ, %d only-base, %d only-head (%d base / %d head non-log outputs, %ds)\n",
          NR, n["identical"], n["differs"], n["only-base"], n["only-head"], b, h, secs
        exit (NR == n["identical"] && b > 0 && h > 0) ? 0 : 1 }'
