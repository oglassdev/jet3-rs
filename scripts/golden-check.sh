#!/usr/bin/env bash
# Behavior-preservation check: build <base-rev> and the working tree, run the same
# corpus of database-producing operations with each build, then compare SHA-256 of
# every output file (databases, snapshots, and each command's stdout/stderr/exit).
#
# Every command runs from its side's output root with relative paths, so outputs
# that echo their own path match. Base==head runs show nothing else varies (no
# timestamps, pids or temp paths reach any output), so nothing is normalized.
#
# Not run: row_overflow_candidate (needs-dao-capture); the continue/refuse modes of
# the *_candidate examples (need-dao-sources); inspect/model modes (redundant).
# allocation_candidate currently exits 1 on a read-budget limit; its partial
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
build() { (cd "$1" && CARGO_TARGET_DIR="$2" cargo build --quiet --release -p jet3 -p jet3-cli --bins --examples); }
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
  local cli=$1 ex=$2 id
  mkdir -p cli mut
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

  # Fixtures for the Id-addressed mutation examples.
  local fixed=() rows=() ins=()
  for id in 1 2 3; do fixed+=("[{\"long\": $id}, {\"byte\": $id}, {\"integer\": $id}, {\"long\": $id}, {\"currency\": $id}, {\"single\": $id}, {\"double\": $id}, {\"date_time\": $id}, {\"guid\": [$id,0,0,0,0,0,0,0,0,0,0,0,0,0,0,$id]}, {\"text\": \"$id\"}, {\"text\": \"$(rep "$id" 255)\"}]"); done
  for id in $(seq 12); do rows+=("[{\"long\": $id}, {\"long\": $((id * 7))}, {\"text\": \"$(rep r "$((id * 5))")\"}, {\"binary\": [$id, 1, 2]}, {\"boolean\": $([[ $((id % 2)) -eq 0 ]] && echo true || echo false)}]"); done
  for id in 1 2 3 4 5; do ins+=("[{\"long\": $id}, {\"long\": $id}, {\"text\": \"row $id\"}]"); done
  local pk='"indexes": [{"name": "PrimaryKey", "kind": "primary", "fields": [{"column": "Id"}]}]'
  run cli/create-fixture "$cli" create mut/fixture.mdb --input - <<EOF
{"tables": [
 {"name": "Fixed", $pk, "columns": [{"name": "Id", "type": "long"}, {"name": "B", "type": "byte"}, {"name": "I", "type": "integer"}, {"name": "L", "type": "long"}, {"name": "C", "type": "currency"}, {"name": "S", "type": "single"}, {"name": "D", "type": "double"}, {"name": "Dt", "type": "date_time"}, {"name": "G", "type": "guid"}, {"name": "F1", "type": "fixed_text", "size": 1}, {"name": "F255", "type": "fixed_text", "size": 255}],
  "rows": [$(IFS=,; echo "${fixed[*]}")]},
 {"name": "Rows", $pk, "columns": [{"name": "Id", "type": "long"}, {"name": "Value", "type": "long"}, {"name": "Payload", "type": "text", "size": 80}, {"name": "Bin", "type": "binary", "size": 16}, {"name": "Flag", "type": "boolean"}],
  "rows": [$(IFS=,; echo "${rows[*]}")]},
 {"name": "Ins", $pk, "columns": [{"name": "Id", "type": "long"}, {"name": "Value", "type": "long"}, {"name": "Payload", "type": "text", "size": 80}],
  "rows": [$(IFS=,; echo "${ins[*]}")]}]}
EOF
  local arm col
  while read -r arm col; do
    run "mut/fixed-$arm" "$ex/fixed_field_update_candidate" mut/fixture.mdb "mut/fixed-$arm.mdb" Fixed 2 "$col" "$arm"
  done <<'EOF'
byte B
integer I
long-control L
currency C
single S
double D
date Dt
guid G
fixed-text-1 F1
fixed-text-255 F255
EOF
  local profile
  while read -r profile id; do
    run "mut/row-update-$profile" "$ex/row_update_candidate" mut/fixture.mdb "mut/row-update-$profile.mdb" Rows "$id" "$profile"
  done <<<$'grow-first 1\nshrink-middle 2\nnull-later 12\ntombstone 3'
  run mut/field-update "$ex/field_update_candidate" mut/fixture.mdb mut/field-update.mdb Rows 4 Value 424242
  run mut/row-insert "$ex/row_insert_candidate" mut/fixture.mdb mut/row-insert.mdb Ins 100 7 hello
  run mut/row-delete "$ex/row_delete_candidate" mut/fixture.mdb mut/row-delete.mdb Rows 5
  run mut/row-delete-compaction "$ex/row_delete_compaction" mut/fixture.mdb mut/row-delete-compaction.mdb Rows 6 7 8 9 1

  run cli/create-rel "$cli" create mut/rel.mdb --input - <<EOF
{"tables": [
 {"name": "Parent", $pk, "columns": [{"name": "Id", "type": "long"}, {"name": "Label", "type": "text", "size": 32}],
  "rows": [[{"long": 1}, {"text": "p1"}], [{"long": 2}, {"text": "p2"}]]},
 {"name": "Child", $pk, "columns": [{"name": "Id", "type": "long"}, {"name": "ParentId", "type": "long"}, {"name": "Note", "type": "memo"}, {"name": "Blob", "type": "long_binary"}],
  "rows": [[{"long": 1}, {"long": 1}, {"memo": "n1"}, {"long_binary": [1, 2]}], [{"long": 2}, {"long": 1}, null, null], [{"long": 3}, {"long": 2}, {"memo": "$(rep q 2500)"}, null]]}],
 "relationships": [{"name": "ParentChild", "cascade_deletes": true, "parent": {"table": "Parent", "column": "Id"}, "child": {"table": "Child", "column": "ParentId"}}]}
EOF
  cat >mut/recipe.json <<EOF
{"stages": [
 {"name": "insert", "operations": [{"table": "Parent", "kind": "insert", "row": [3, "7033"]}, {"table": "Child", "kind": "insert", "row": [10, 3, "$(rep 6e 3000)", "$(rep ab 5000)"]}]},
 {"name": "field", "operations": [{"table": "Child", "kind": "field", "id": 10, "column": 2, "value": "6869"}, {"table": "Child", "kind": "field", "id": 1, "column": 3, "value": "$(rep cd 4000)"}]},
 {"name": "replace", "operations": [{"table": "Child", "kind": "replace", "id": 2, "row": [2, 3, null, null]}]},
 {"name": "delete", "operations": [{"table": "Parent", "kind": "delete", "id": 1}, {"table": "Child", "kind": "delete", "id": 10}]}],
 "refusals": [
  {"name": "orphan", "operation": {"table": "Child", "kind": "insert", "row": [99, 42, null, null]}},
  {"name": "duplicate", "operation": {"table": "Parent", "kind": "insert", "row": [1, "78"]}},
  {"name": "limited", "operation": {"table": "Parent", "kind": "insert", "row": [50, "78"], "limited": true}}]}
EOF
  run mut/relationship-mutation "$ex/relationship_mutation_candidate" mut/rel.mdb mut/recipe.json mut/relationship-mutation
}

corpus() { # BIN_DIR OUT_DIR
  local ex=$1/examples cli=$1/jet3-cli g m
  mkdir -p "$2/gen" && cd "$2"
  for g in allocation creation_definition_chains creation_tables empty_value fixed_text_index \
    index_capacity indexed_boundary indexed_row_mutation index_tree_mutation long_value_lifecycle \
    memo_allow_empty multi_level_index multiple_index multiple_long_value_creation numeric_index \
    numeric_index_mutation practical_lifecycle single_leaf_key wide_row; do
    run "gen/$g" "$ex/${g}_candidate" "gen/$g" &
  done
  for m in unindexed indexed multi; do
    run "gen/autoincrement-$m" "$ex/autoincrement_candidate" "gen/autoincrement-$m.mdb" "$m" &
    run "gen/autoincrement-validation-$m" "$ex/autoincrement_validation_candidate" "gen/autoincrement-validation-$m.mdb" "$m" &
  done
  for m in descending-unique ascending-descending-unique descending-ascending-ordinary; do
    run "gen/composite-$m" "$ex/composite_index_candidate" "gen/composite-$m.mdb" "$m" &
  done
  for m in primary unique ordinary; do run "gen/indexed-row-$m" "$ex/indexed_row_candidate" "gen/indexed-row-$m.mdb" "$m" & done
  for m in memo ole; do run "gen/initial-long-value-$m" "$ex/initial_long_value_candidate" "gen/initial-long-value-$m.mdb" "$m" & done
  for m in mixed empty-first; do run "gen/multi-table-row-$m" "$ex/multi_table_row_candidate" "gen/multi-table-row-$m.mdb" "$m" & done
  for m in unique ignore required composite composite-ignore auto; do
    run "gen/nullable-index-$m" "$ex/nullable_index_candidate" "$m" "gen/nullable-index-$m.mdb" &
  done
  for g in initial_row multi_page_row relationship_row; do run "gen/$g" "$ex/${g}_candidate" "gen/$g.mdb" & done
  run gen/relationship-graph "$ex/relationship_graph_candidate" /dev/stdin gen/relationship-graph <<'EOF' &
{"replicas": 1, "graphs": [
 {"name": "cycle", "tables": [
   {"name": "Alpha", "fields": [{"name": "Id", "type": 4}, {"name": "Fk", "type": 4}, {"name": "Body", "type": 12}], "rows": [{"Id": 1, "Fk": 1, "Body": "a1"}, {"Id": 2, "Fk": null, "Body": null}]},
   {"name": "Bravo", "fields": [{"name": "Id", "type": 4}, {"name": "Fk", "type": 4}, {"name": "Body", "type": 12}], "rows": [{"Id": 1, "Fk": 1, "Body": "b1"}, {"Id": 2, "Fk": null, "Body": ""}]}],
  "relations": [{"name": "R00", "table": "Alpha", "field": "Id", "foreign_table": "Bravo", "foreign_field": "Fk"},
                {"name": "R01", "table": "Bravo", "field": "Id", "foreign_table": "Alpha", "foreign_field": "Fk", "attributes": 4352}]},
 {"name": "typed", "tables": [
   {"name": "Parent", "fields": [{"name": "Id", "type": 4}, {"name": "Code", "type": 10, "attributes": 1, "size": 3}, {"name": "Name", "type": 10, "size": 20, "required": true},
      {"name": "Amount", "type": 5}, {"name": "When", "type": 8}, {"name": "Blob", "type": 11}, {"name": "Uid", "type": 15}, {"name": "Bin", "type": 9, "size": 4},
      {"name": "Flag", "type": 1}, {"name": "B", "type": 2}, {"name": "I", "type": 3}, {"name": "S", "type": 6}, {"name": "D", "type": 7}],
    "indexes": [{"name": "PrimaryKey", "field": "Id", "primary": true}, {"name": "ByName", "field": "Name", "unique": true, "ignore_nulls": true},
      {"name": "ByCodeWhen", "fields": [{"name": "Code"}, {"name": "When", "direction": "desc"}]}],
    "rows": [{"Id": 1, "Code": "abc", "Name": "one", "Amount": 12345, "When": 1.5, "Blob": {"kind": "pattern", "seed": 3, "length": 6000}, "Uid": {"kind": "pattern", "seed": 1, "length": 16}, "Bin": [1, 2], "Flag": true, "B": 9, "I": -9, "S": 0.5, "D": -2.75},
             {"Id": 2, "Code": null, "Name": "two", "Amount": null, "When": null, "Blob": null, "Uid": null, "Bin": null, "Flag": false, "B": null, "I": null, "S": null, "D": null}]},
   {"name": "Child", "fields": [{"name": "Id", "type": 4, "attributes": 16}, {"name": "ParentId", "type": 4}, {"name": "Note", "type": 12}],
    "rows": [{"Id": null, "ParentId": 1, "Note": {"kind": "repeat", "byte": 66, "length": 3000}}, {"Id": null, "ParentId": 2, "Note": "x"}]}],
  "relations": [{"name": "ParentChild", "table": "Parent", "field": "Id", "foreign_table": "Child", "foreign_field": "ParentId", "attributes": 4096}]}]}
EOF
  run gen/rich-relationship "$ex/rich_relationship_candidate" /dev/stdin gen/rich-relationship <<'EOF' &
{"replicas": 1, "arms": [{"name": "mixed",
  "fields": [{"name": "Id", "type": 4}, {"name": "ParentId", "type": 4}, {"name": "Label", "type": 10, "size": 40, "allow_zero_length": true}, {"name": "Note", "type": 12, "allow_zero_length": true}, {"name": "Blob", "type": 11}],
  "parents": [{"Id": 1, "Label": "one"}, {"Id": 2, "Label": "two"}],
  "rows": [{"Id": 1, "ParentId": 1, "Label": {"kind": "ascii"}, "Note": {"kind": "repeat", "byte": 65, "length": 3000}, "Blob": {"kind": "pattern", "seed": 2, "length": 5000}},
           {"Id": 2, "ParentId": 2, "Label": {"kind": "empty"}, "Note": {"kind": "empty"}, "Blob": {"kind": "null"}},
           {"Id": 3, "ParentId": null, "Label": {"kind": "null"}, "Note": {"kind": "ascii"}, "Blob": {"kind": "repeat", "byte": 7, "length": 100}}]}]}
EOF
  cli_corpus "$cli" "$ex" &
  wait
  # Read-path coverage: validate every produced database.
  local f
  find gen cli mut -name '*.mdb' | LC_ALL=C sort | while read -r f; do run "validate/$f" "$cli" validate "$f"; done
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
