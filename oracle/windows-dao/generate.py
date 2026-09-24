"""Suite data too formulaic for literal JSON; a spec names its generator with `generate`."""

from __future__ import annotations

from compare import host

# (name, LANGID, code page) of the six EXP-0309 collations.
LOCALES = [('general', 0x409, 1252), ('nordic', 0x41d, 1252), ('spanish', 0x40a, 1252),
           ('dutch', 0x413, 1252), ('cyrillic', 0x419, 1251), ('greek', 0x408, 1253)]

EXTENDED = 0xc1


def literal(raw: bytes) -> str:
    return "'" + host(raw).replace("'", "''") + "'"


def column(name, kind, **extra):
    return {'name': name, 'type': kind, **extra}


def primary(field='id'):
    return {'name': 'PrimaryKey', 'kind': 'primary', 'fields': [{'column': field}]}


def table(name, columns, indexes=None):
    return {'op': 'table', 'table': {'name': name, 'columns': columns, 'indexes': [primary()] if indexes is None else indexes}}


def sql(text):
    return {'op': 'sql', 'text': text}


def mutate(request, native_sql, expected=0, locate=None):
    step = {'command': 'mutate', 'request': request, 'native': {'sql': native_sql}}
    if expected:
        step['expected_returncode'] = expected
    if locate:
        step['locate'] = locate
    return step


def schema(request, native_sql=None):
    step = {'command': 'schema', 'request': request}
    if native_sql:
        step['native'] = {'sql': native_sql}
    return step


def text(raw: bytes):
    return {'text': list(raw)}


def locale_samples() -> list[bytes]:
    samples = [b'Case', b'CH', b'LL', b' a', bytes([0xc1]), bytes([0xe1]), bytes([0xdf])]
    for width in [1, 3, 127, 128, 129, 254, 255]:
        for prefix, byte in [(100 + width, 0xc1), (400 + width, 0xe1), (700 + width, 0x61)]:
            samples.append((f'{prefix:03}'.encode() + bytes([byte]) * (width - 3))[:width])
    # Prefixes avoid duplicate full keys and keep the expansion-heavy shortened keys.
    return list(dict.fromkeys(b'%02d' % i + value[:253] for i, value in enumerate(samples)))


def locale_input(samples) -> list[dict]:
    ops = [
        table('Items', [column('id', 'long'), column('code', 'text', size=255), column('spare', 'long')]),
        table('P', [column('id', 'long'), column('code', 'text', size=63)]),
        table('C', [column('id', 'long'), column('code', 'text', size=63)]),
        table('Watch', [column('id', 'long'), column('memo', 'memo'), column('ole', 'long_binary')]),
        sql('INSERT INTO Watch (id) VALUES (1)'),
        {'op': 'payload', 'table': 'Watch', 'key': 'id', 'id': 1, 'column': 'memo', 'length': 4200, 'seed': 3},
        {'op': 'payload', 'table': 'Watch', 'key': 'id', 'id': 1, 'column': 'ole', 'length': 2700, 'seed': 7},
        sql('CREATE UNIQUE INDEX ux ON Items (code)'),
        sql('CREATE INDEX dx ON Items (code DESC)'),
        sql('CREATE INDEX cx ON Items (code DESC, id)'),
        sql('CREATE UNIQUE INDEX ux ON P (code)'),
    ]
    ops += [sql(f'INSERT INTO Items (id, code, spare) VALUES ({i},{literal(value)},{i})') for i, value in enumerate(samples, 1)]
    for name in ['P', 'C']:
        ops += [sql(f"INSERT INTO {name} (id, code) VALUES (1,'Case')"),
                sql(f'INSERT INTO {name} (id, code) VALUES (2,{literal(bytes([EXTENDED]))})')]
    ops += [{'op': 'relation', 'name': 'Base', 'parent': 'P', 'child': 'C', 'attributes': 4352, 'pairs': [['code', 'code']]},
            {'op': 'query', 'name': 'SavedSelect', 'sql': 'SELECT id, code FROM Items ORDER BY id'}]
    return ops


def locale_cases(locale: str, code_page: int, rows: int) -> list[dict]:
    """The twelve EXP-0310 edits in one locale; names use the locale's extended letter."""
    extended = bytes([EXTENDED]).decode(f'cp{code_page}')
    native_name = host(extended.encode(f'cp{code_page}'))
    index, field, new_table = 'ch' + extended, 'Field' + extended, 'Table' + extended
    changed = b'Changed-' + bytes([EXTENDED]) * 120
    wide = bytes([EXTENDED]) * 255
    duplicate = b'00CASE'
    new_code = b'New' + bytes([EXTENDED])

    def refusal(table_name, count):
        # A native refusal raises the source header marker and the primary index count (EXP-0310).
        return {'native_residue': [{'offset': 1538, 'before': 0, 'after': 1},
                                   {'table': table_name, 'counter': 'entry_count', 'index': 0, 'delta': 1}],
                'getter_residue': [{'table': table_name, 'index': 'PrimaryKey', 'property': 'DistinctCount',
                                    'candidate': count, 'native': count + 1}]}

    cases = [
        {'name': 'rows', 'steps': [
            mutate({'operation': 'update', 'table': 'Items', 'column': 1, 'value': text(changed)},
                   f'UPDATE Items SET code={literal(changed)} WHERE id=1', locate={'table': 'Items', 'id': 1}),
            mutate({'operation': 'insert', 'table': 'Items', 'values': [{'long': 999}, text(wide), None]},
                   f'INSERT INTO Items (id, code) VALUES (999,{literal(wide)})'),
            mutate({'operation': 'delete', 'table': 'Items'}, 'DELETE FROM Items WHERE id=2', locate={'table': 'Items', 'id': 2})],
         'placement_roles': {'Items/index/1/owned': [4, 4], 'Items/index/2/owned': [4, 5],
                             'Items/index/3/owned': [4, 5], 'Items/table/available': [2, 1]}},
        {'name': 'duplicate', 'kind': 'residue', 'dao_error': 3022, 'steps': [
            mutate({'operation': 'insert', 'table': 'Items', 'values': [{'long': 999}, text(duplicate), None]},
                   f'INSERT INTO Items (id,code) VALUES (999,{literal(duplicate)})', expected=1)], **refusal('Items', rows)},
        {'name': 'indexes', 'dates': ['Items'], 'placement_roles': {'Items/index/4/owned': [3, 3]}, 'steps': [
            schema({'operation': 'create_index', 'table': 'Items', 'index': {
                'name': index, 'kind': 'ordinary', 'fields': [{'column': 'spare'}, {'column': 'code', 'direction': 'descending'}]}},
                f'CREATE INDEX [ch{native_name}] ON Items (spare,code DESC)'),
            schema({'operation': 'rename_index', 'table': 'Items', 'index': index, 'name': 'll' + extended})]},
        {'name': 'column', 'dates': ['Items'], 'steps': [
            schema({'operation': 'create_column', 'table': 'Items', 'column': column(field, 'text', size=32)}),
            schema({'operation': 'rename_column', 'table': 'Items', 'column': field, 'name': 'Renamed' + extended}),
            schema({'operation': 'set_column_properties', 'table': 'Items', 'column': 'Renamed' + extended,
                    'description': 'Description ' + extended})]},
        {'name': 'table', 'dates': [new_table, 'Renamed' + extended],
         'placement_roles': {f'Renamed{extended}/index/0/owned': [1, 1], f'Renamed{extended}/index/1/owned': [1, 1],
                             f'Renamed{extended}/table/available': [0, 0]}, 'steps': [
            schema({'operation': 'create_table', 'table': {
                'name': new_table, 'columns': [column('id', 'long'), column(field, 'text', size=32)],
                'indexes': [primary(), {'name': index, 'kind': 'unique', 'fields': [{'column': field}]}]}}),
            schema({'operation': 'rename_table', 'table': new_table, 'name': 'Renamed' + extended})]},
        {'name': 'drop-column', 'dates': ['Items'], 'steps': [
            schema({'operation': 'drop_column', 'table': 'Items', 'column': 'spare'})]},
        # One Rust replacement is two native statements.
        {'name': 'replace-index', 'dates': ['Items'], 'placement_roles': {'Items/index/3/owned': [3, 3]},
         'native_extra': [sql(f'CREATE INDEX [ch{native_name}] ON Items (code)')], 'steps': [
            schema({'operation': 'replace_index', 'table': 'Items', 'index': 'dx',
                    'replacement': {'name': index, 'kind': 'ordinary', 'fields': [{'column': 'code'}]}}, 'DROP INDEX dx ON Items')]},
        {'name': 'relation', 'dates': ['Relation' + extended, 'P', 'C'], 'steps': [
            schema({'operation': 'create_relationship', 'relationship': {
                'name': 'Relation' + extended, 'parent': {'table': 'P', 'column': 'code'}, 'child': {'table': 'C', 'column': 'code'}}})]},
        {'name': 'rename-related', 'dates': ['P', 'Parents' + extended, 'C', 'Base'], 'steps': [
            schema({'operation': 'rename_table', 'table': 'P', 'name': 'Parents' + extended}),
            schema({'operation': 'rename_column', 'table': 'C', 'column': 'code', 'name': field})]},
        {'name': 'cascade-update', 'affected_tables': ['C'], 'steps': [
            mutate({'operation': 'update', 'table': 'P', 'column': 1, 'value': text(new_code)},
                   f'UPDATE P SET code={literal(new_code)} WHERE id=2', locate={'table': 'P', 'id': 2})]},
        {'name': 'cascade-delete', 'affected_tables': ['C'], 'steps': [
            mutate({'operation': 'delete', 'table': 'P'}, 'DELETE FROM P WHERE id=2', locate={'table': 'P', 'id': 2})]},
        {'name': 'orphan', 'kind': 'residue', 'dao_error': 3201, 'steps': [
            mutate({'operation': 'insert', 'table': 'C', 'values': [{'long': 999}, text(b'orphan')]},
                   "INSERT INTO C (id,code) VALUES (999,'orphan')", expected=1)], **refusal('C', 2)},
    ]
    for case in cases:
        case.update(name=f'{locale}-{case["name"]}', input=locale, code_page=code_page)
    return cases


def locale_continuation(locale: str, code_page: int) -> dict:
    """Three native writes on both the Rust and the native `rows` output (EXP-0310)."""
    value = 'Continued' + chr(EXTENDED)
    return {
        'name': f'{locale}-continued', 'of': f'{locale}-rows', 'code_page': code_page,
        'steps': [sql(f"UPDATE Items SET code='{value}' WHERE id=999"),
                  sql("INSERT INTO Items (id,code,spare) VALUES (1000,'NativeNext',1000)"),
                  sql('DELETE FROM Items WHERE id=3')],
        'touched': [{'operation': 'update', 'table': 'Items', 'id': 999}, {'operation': 'delete', 'table': 'Items', 'id': 3}],
        'rows': {'table': 'Items', 'key': 'id', 'update': {'999': {'code': value}},
                 'insert': [{'id': 1000, 'code': 'NativeNext', 'spare': 1000}], 'delete': [3]},
    }


def locale_updates() -> dict:
    samples = locale_samples()
    inputs, cases, continuations = {}, [], []
    for locale, language, code_page in LOCALES:
        inputs[locale] = {'locale': f';LANGID=0x{language:04x};CP={code_page};COUNTRY=0', 'ops': locale_input(samples)}
        cases += locale_cases(locale, code_page, len(samples))
        continuations.append(locale_continuation(locale, code_page))
    return {'inputs': inputs, 'cases': cases, 'continuations': continuations}


# --- Text properties (EXP-0299/0300) --------------------------------------------------

KINDS = [
    ("boolean", {}, "Yes", "Is Not Null"),
    ("byte", {}, "1", ">=0"),
    ("integer", {}, "2", ">=0"),
    ("long", {}, "3", ">=0"),
    ("auto_increment", {}, None, None),
    ("currency", {}, "4.5", ">=0"),
    ("single", {}, "1.5", ">=0"),
    ("double", {}, "2.5", ">=0"),
    ("date_time", {}, "#1/2/2000#", "Is Not Null"),
    ("guid", {}, "1", None),
    ("text", {"size": 20}, '"abc"', '<>"x"'),
    ("fixed_text", {"size": 10}, '"xy"', '<>"x"'),
    ("binary", {"size": 20}, "1", None),
    ("memo", {}, '"memo"', "Is Not Null"),
    ("long_binary", {}, "1", None),
]


def kinds_table(name, *, default=False, rule=False, description=False):
    columns = [column("Id", "long")]
    for kind, extra, dv, vr in KINDS:
        spec = column("F_" + kind, kind, **extra)
        if default and dv is not None:
            spec["default_value"] = dv
        if rule and vr is not None:
            spec["validation_rule"] = vr
            spec["validation_text"] = "bad " + kind
        if description:
            spec["description"] = "About " + kind
        if len(spec) > 2 + len(extra) or not (default or rule):
            columns.append(spec)
    return {"name": name, "columns": columns, "indexes": [primary("Id")]}


def index_matrix(required, allow):
    variants = [
        ("UniqueInclude", "unique", "include"),
        ("UniqueIgnore", "unique", "ignore_all_null"),
        ("UniqueRequired", "unique", "required"),
        ("OrdinaryInclude", "ordinary", "include"),
        ("OrdinaryIgnore", "ordinary", "ignore_all_null"),
        ("OrdinaryRequired", "ordinary", "required"),
        ("PrimaryKey", "primary", "required"),
    ]
    tables = []
    for name, kind, policy in variants:
        key = column("K", "text", size=20, required=required, allow_zero_length=allow)
        index = {"name": "By" + name, "kind": kind, "null_policy": policy, "fields": [{"column": "K"}]}
        indexes = [index] if kind == "primary" else [primary("Id"), index]
        values = ["b", "a"]
        if allow:
            values.append("")
        if kind == "ordinary":
            values.append("a")
        if not required and policy != "required":
            values += [None, None] if kind == "unique" else [None]
        rows = [[{"long": n + 1}, None if v is None else {"text": v}] for n, v in enumerate(values)]
        tables.append({"name": "K" + name, "columns": [column("Id", "long"), key], "indexes": indexes, "rows": rows})
    return {"tables": tables}


def long_text(prefix, length):
    return (prefix * length)[:length]


def creations():
    specs = {
        "c01-kinds-defaults": {"tables": [kinds_table("Kinds", default=True)]},
        "c02-kinds-rules": {"tables": [kinds_table("Kinds", rule=True)]},
        "c03-kinds-descriptions": {"tables": [kinds_table("Kinds", description=True)]},
        "c04-table-rule": {"tables": [{
            "name": "Ruled",
            "columns": [column("Id", "long"), column("A", "long", required=True, default_value="1"),
                        column("B", "text", size=20, validation_rule='<>"x"')],
            "indexes": [primary("Id")],
            "validation_rule": "[A]>0 Or [B] Is Null", "validation_text": "table says no"}]},
        "c05-table-text-only": {"tables": [{
            "name": "Messages", "columns": [column("Id", "long"), column("Name", "text", size=30)],
            "indexes": [primary("Id")], "validation_text": "only a message"}]},
        "c06-required-azl-mix": {"tables": [{
            "name": "Mixed",
            "columns": [column("Id", "long"),
                        column("T1", "text", size=20, required=True, allow_zero_length=True, default_value='""'),
                        column("T2", "text", size=20, required=True, validation_rule='<>"no"'),
                        column("M1", "memo", allow_zero_length=True, validation_text="memo message"),
                        column("M2", "memo", required=True, default_value='"m"', description="memo two"),
                        column("F1", "fixed_text", size=4, required=True, default_value='"abcd"')],
            "indexes": [primary("Id")]}]},
        "c07-accents": {"tables": [{
            "name": "Accents",
            "columns": [column("Id", "long"),
                        column("Name", "text", size=20, default_value='"café"', validation_rule='<>"é"',
                               validation_text="Valeur €", description="Clé ÿ")],
            "indexes": [primary("Id")], "validation_text": "Tableau €"}]},
        "c08-chained": {"tables": [{
            "name": "Big",
            "columns": [column("Id", "long"),
                        column("Body", "memo", default_value='"' + long_text("x", 1000) + '"',
                               validation_text=long_text("v", 1500), description=long_text("d", 2000))],
            "indexes": [primary("Id")],
            "rows": [[{"long": 1}, {"memo": "first"}], [{"long": 2}, None]]}]},
        "c09-two-tables": {"tables": [
            {"name": "Plain",
             "columns": [column("Id", "long"), column("Amount", "long", default_value="5"),
                         column("Tag", "guid"), column("Label", "text", size=20, description="label")],
             "indexes": [primary("Id")],
             "rows": [[{"long": 1}, {"long": 10}, None, {"text": "one"}], [{"long": 2}, None, None, None]]},
            {"name": "Checked",
             "columns": [column("Id", "long"), column("Qty", "long", validation_rule=">=0", validation_text="no negatives")],
             "indexes": [primary("Id")], "validation_rule": "[Qty]<1000", "validation_text": "too many"}]},
        "c10-index-plain": index_matrix(False, False),
        "c11-index-azl": index_matrix(False, True),
        "c12-index-required": index_matrix(True, False),
        "c13-index-required-azl": index_matrix(True, True),
        "c14-composite-index": {"tables": [{
            "name": "Pairs",
            "columns": [column("Id", "long"), column("A", "text", size=10, required=True, validation_text="need A"),
                        column("B", "long", default_value="0")],
            "indexes": [primary("Id"), {"name": "ByPair", "kind": "unique", "null_policy": "ignore_all_null",
                                    "fields": [{"column": "A"}, {"column": "B", "direction": "descending"}]}],
            "rows": [[{"long": 1}, {"text": "a"}, {"long": 2}], [{"long": 2}, {"text": "a"}, None],
                     [{"long": 3}, {"text": "b"}, {"long": 1}]]}]},
        "c15-fixed-text": {"tables": [{
            "name": "Fixed",
            "columns": [column("Id", "long"), column("F", "fixed_text", size=4, required=True, allow_zero_length=True,
                                                     default_value='"abcd"', validation_rule='Like "????"', validation_text="four")],
            "indexes": [primary("Id")]}]},
        "c16-kinds-everything": {"tables": [kinds_table("Kinds", default=True, rule=True, description=True)]},
        "c17-later-table": {"tables": [
            {"name": "First", "columns": [column("Id", "long"), column("Name", "text", size=10)], "indexes": [primary("Id")]},
            {"name": "Second", "columns": [column("Id", "long"), column("Note", "memo", description=long_text("n", 1800))],
             "indexes": [primary("Id")]},
            {"name": "Third", "columns": [column("Id", "long"), column("Score", "double", validation_rule="Between 0 And 1")],
             "indexes": [primary("Id")], "validation_rule": "[Score]<>0.5", "validation_text": long_text("t", 1200)}]},
        "c18-memo-rules": {"tables": [{
            "name": "Notes",
            "columns": [column("Id", "long"), column("Body", "memo", required=True, validation_rule="Is Not Null",
                                                     validation_text="body needed", default_value='"-"')],
            "indexes": [primary("Id")]}]},
        "c19-relationship": {"tables": [
            {"name": "Parent", "columns": [column("Id", "long"), column("Name", "text", size=20, default_value='"p"')],
             "indexes": [primary("Id")]},
            {"name": "Child", "columns": [column("Id", "long"), column("ParentId", "long", validation_rule=">0", description="parent")],
             "indexes": [primary("Id")]}],
            "relationships": [{"name": "ParentChild", "parent": {"table": "Parent", "column": "Id"},
                               "child": {"table": "Child", "column": "ParentId"}}]},
        "c20-descending-primary": {"tables": [{
            "name": "Codes",
            "columns": [column("Id", "long", description="key"), column("Code", "text", size=8, required=True,
                                                                          validation_text="code message")],
            "indexes": [{"name": "PrimaryKey", "kind": "primary", "fields": [{"column": "Id", "direction": "descending"}]},
                        {"name": "ByCode", "kind": "unique", "null_policy": "required", "fields": [{"column": "Code"}]}],
            "rows": [[{"long": 1}, {"text": "x"}], [{"long": 2}, {"text": "y"}]]}]},
        # EXP-0300: LvProp payloads of 1,776 bytes stay single-page; 1,777 chain.
        "c21-property-boundary": {"tables": [
            {"name": name, "columns": [column("Id", "long"), column("Note", "memo", description=long_text("d", size))],
             "indexes": [primary("Id")]} for name, size in (("Single", 1661), ("Chained", 1662))]},
    }
    return specs


def props(table, name, **changes):
    return schema({"operation": "set_column_properties", "table": table, "column": name, **changes})


def table_props(table, **changes):
    return schema({"operation": "set_table_properties", "table": table, **changes})


ALL_LABEL = {"validation_rule": "Is Not Null", "validation_text": "label needed", "default_value": '"lbl"',
             "description": "The label"}


def edits():
    """Paired edits: DAO applies the same requests to the same retained inputs."""
    cases = [
        ("e01-field-all", "baseline", [props("Target", "Label", **ALL_LABEL)], ["Target"]),
        ("e02-field-change-clear", "baseline", [
            props("Target", "Label", **ALL_LABEL),
            props("Target", "Label", validation_rule='Like "L*"', validation_text=None, description=None)], ["Target"]),
        ("e03-table-rule", "baseline", [table_props("Target", validation_rule="[Id]>0", validation_text="positive id")], ["Target"]),
        ("e04-table-rule-clear-one", "baseline", [
            table_props("Target", validation_rule="[Id]>0", validation_text="positive id"),
            table_props("Target", validation_rule=None)], ["Target"]),
        ("e05-column-after-table-block", "baseline", [
            table_props("Target", validation_rule="[Id]>0", validation_text="positive id"),
            schema({"operation": "create_column", "table": "Target", "column": column(
                "Extra", "text", size=30, required=True, validation_rule='<>"x"', validation_text="no x",
                default_value='"e"', description="extra")})], ["Target"]),
        ("e06-autoincrement-properties", "baseline", [
            schema({"operation": "create_column", "table": "Target", "column": column("Serial", "auto_increment")}),
            props("Target", "Serial", validation_rule=">0", default_value="1", description="serial")], ["Target"]),
        ("e07-rename-referenced", "baseline", [
            table_props("Target", validation_rule="[Label] Is Not Null"),
            schema({"operation": "rename_column", "table": "Target", "column": "Label", "name": "Caption"})], ["Target"]),
        ("e08-drop-referenced", "baseline", [
            table_props("Target", validation_rule="[Label] Is Not Null"),
            schema({"operation": "drop_column", "table": "Target", "column": "Label"})], ["Target"]),
        ("e09-create-table", "baseline", [schema({"operation": "create_table", "table": {
            "name": "Props", "columns": [column("Id", "long"), column("Name", "text", size=20, validation_rule='<>"x"',
                                                                     default_value='"n"', description="name"),
                                         column("Qty", "long", default_value="0")],
            "indexes": [primary("Id")], "validation_rule": "[Qty]>=0", "validation_text": "qty"}})], ["Props"]),
        ("e10-options-after-text", "baseline", [
            props("Target", "Label", default_value='"d"'),
            schema({"operation": "set_column_options", "table": "Target", "column": "Label", "required": True,
                    "allow_zero_length": False})], ["Target"]),
        ("e11-refuse-ole-rule", "baseline", [
            dict(props("Sentinel", "Blob", validation_rule="Is Not Null"), expected_returncode=1)], []),
        ("e12-refuse-guid-rule", "n-c09", [
            dict(props("Plain", "Tag", validation_text="guid"), expected_returncode=1)], []),
        ("e13-explicit-null-default", "n-c09", [
            {"command": "mutate", "request": {"operation": "insert", "table": "Plain",
                                               "values": [{"long": 3}, None, None, None]}}], ["Plain"]),
        ("e14-native-rule-edits", "n-c09", [
            table_props("Checked", validation_rule=None, validation_text="changed"),
            props("Checked", "Qty", validation_rule="Between 0 And 9", description="quantity")], ["Checked"]),
        ("e15-native-chained", "n-c08", [
            props("Big", "Body", validation_text="short", description=long_text("e", 2040))], ["Big"]),
        ("e16-rust-rule-edits", "r-c09", [
            table_props("Checked", validation_rule=None, validation_text="changed"),
            props("Checked", "Qty", validation_rule="Between 0 And 9", description="quantity")], ["Checked"]),
        ("e17-rust-chained", "r-c08", [
            props("Big", "Body", validation_text="short", description=long_text("e", 2040))], ["Big"]),
        ("e18-replace-ordinary-unique-required", "n-c13", [schema({
            "operation": "replace_index", "table": "KUniqueInclude", "index": "ByUniqueInclude",
            "replacement": {"name": "ByKey", "kind": "unique", "null_policy": "required", "fields": [{"column": "K"}]}})],
            ["KUniqueInclude"]),
        ("e19-replace-unique-ordinary-ignore", "n-c11", [schema({
            "operation": "replace_index", "table": "KUniqueInclude", "index": "ByUniqueInclude",
            "replacement": {"name": "ByKey", "kind": "ordinary", "null_policy": "ignore_all_null",
                            "fields": [{"column": "K", "direction": "descending"}]}})], ["KUniqueInclude"]),
        ("e20-replace-required-unique-include", "n-c12", [schema({
            "operation": "replace_index", "table": "KUniqueRequired", "index": "ByUniqueRequired",
            "replacement": {"name": "ByKey", "kind": "unique", "fields": [{"column": "K"}]}})], ["KUniqueRequired"]),
        ("e21-replace-ignore-primary", "n-c10", [schema({
            "operation": "replace_index", "table": "KUniqueIgnore", "index": "PrimaryKey",
            "replacement": {"name": "PrimaryKey", "kind": "primary", "fields": [{"column": "Id", "direction": "descending"}]}})],
            ["KUniqueIgnore"]),
        ("e22-refuse-unique-duplicates", "n-c11", [dict(schema({
            "operation": "create_index", "table": "KOrdinaryInclude",
            "index": {"name": "Unique", "kind": "unique", "fields": [{"column": "K"}]}}), expected_returncode=1)], []),
        ("e24-medium-property-single", "baseline", [props("Target", "Label", description=long_text("s", 1600))],
         ["Target"]),
        ("e25-medium-property-chained", "baseline", [props("Target", "Label", description=long_text("m", 1601))],
         ["Target"]),
        ("e26-create-column-chained", "baseline", [schema({"operation": "create_column", "table": "Target",
                                                            "column": column("Extra", "memo", description=long_text("x", 1566))})],
         ["Target"]),
        ("e27-create-table-chained", "baseline", [schema({"operation": "create_table", "table": {
            "name": "Props2", "columns": [column("Id", "long"), column("Note", "memo", description=long_text("d", 1662))],
            "indexes": [primary("Id")]}})], ["Props2"]),
        ("e28-rename-shrinks-property", "baseline", [
            props("Target", "Label", description=long_text("m", 1601)),
            schema({"operation": "rename_column", "table": "Target", "column": "Label", "name": "Lab"})], ["Target"]),
        ("e23-refuse-required-null", "n-c10", [dict(schema({
            "operation": "create_index", "table": "KOrdinaryInclude",
            "index": {"name": "Needed", "kind": "ordinary", "null_policy": "required", "fields": [{"column": "K"}]}}),
            expected_returncode=1)], []),
    ]
    # EXP-0297: a newly Required column retains old nulls, which validation reports.
    invalid = {"e05-column-after-table-block"}
    # Independently allocated pages; the raw evaluator still checks their framing and capacity.
    lvprop = ["MSysObjects/lval/LvProp/owned"]
    placements = {
        "e09-create-table": ["Props/table/owned", "Props/table/available", "Props/index/0/owned"],
        "e15-native-chained": lvprop,
        "e17-rust-chained": lvprop,
        "e24-medium-property-single": lvprop,
        "e25-medium-property-chained": lvprop,
        "e26-create-column-chained": lvprop + ["Target/lval/Extra/owned", "Target/lval/Extra/available"],
        "e27-create-table-chained": lvprop + ["Props2/table/owned", "Props2/table/available", "Props2/index/0/owned",
                                              "Props2/lval/Note/owned", "Props2/lval/Note/available"],
        "e28-rename-shrinks-property": lvprop + ["MSysObjects/lval/LvProp/available"],
        "e18-replace-ordinary-unique-required": ["KUniqueInclude/index/1/owned"],
        "e19-replace-unique-ordinary-ignore": ["KUniqueInclude/index/1/owned"],
        "e20-replace-required-unique-include": ["KUniqueRequired/index/1/owned"],
        "e21-replace-ignore-primary": ["KUniqueIgnore/index/1/owned"],
    }
    return [{"name": name, "input": source, "steps": steps, "normalize_table_dates": dates,
             "validation_returncode": int(name in invalid), "placement_roles": placements.get(name, [])}
            for name, source, steps, dates in cases]


def rust_only():
    """Rust refusals where DAO would evaluate or silently drop the request; inputs must stay exact."""
    return [
        ("x01-insert-ruled-table", "n-c09", {"command": "mutate", "request": {
            "operation": "insert", "table": "Checked", "values": [{"long": 1}, {"long": 5}]}}),
        ("x02-insert-field-rule", "r-c09", {"command": "mutate", "request": {
            "operation": "insert", "table": "Checked", "values": [{"long": 1}, {"long": 5}]}}),
        ("x03-nordic-edit", "nordic", props("T", "Name", description="no")),
        ("x04-nordic-insert", "nordic", {"command": "mutate", "request": {
            "operation": "insert", "table": "T", "values": [{"text": "z"}]}}),
        ("x05-autoincrement-default", "baseline", schema({"operation": "create_column", "table": "Target",
                                                           "column": column("Serial", "auto_increment", default_value="1")})),
        ("x06-empty-set", "baseline", props("Target", "Label", validation_text="")),
        # DAO accepts up to 4,000 bytes (EXP-0299); Rust limits each value to 2,048.
        ("x07-over-limit-field", "baseline", props("Target", "Label", description=long_text("o", 2049))),
        ("x08-over-limit-table", "r-c09", table_props("Checked", validation_text=long_text("t", 2049))),
    ]


def text_properties() -> dict:
    """Rust creations against two native replicas; paired and Rust-only edits on retained inputs."""
    creation_list = [{'name': name, 'request': request, 'checks': ['lvprop'], 'dates': [t['name'] for t in request['tables']]}
                     for name, request in creations().items()]
    inputs = {
        'baseline': {'external': '20260923-schema-expressions-acceptance/runs/acceptance-r5/stage/baseline.mdb',
                     'sha256': 'e8c8e06a602f3a0ac50d3402ecd5dd20c9d44c05150bd16a4b368ad732d5a762'},
        'nordic': {'external': '20260922-schema-expressions-discovery/runs/20260923T002000Z-textprops-rest2/outbox/j-nordic-table-r1.mdb',
                   'sha256': 'fc545cfc88e2a8dea33e07c8f7002c4ad72039f06c43b5acd2145936088414db'},
    }
    for name in creations():
        number = name.split('-')[0]
        inputs['n-' + number] = {'from': f'native-{name}-r1.mdb'}
        inputs['r-' + number] = {'from': f'candidate-{name}.mdb'}
    refusals = {'e11-refuse-ole-rule': 3313, 'e12-refuse-guid-rule': 3313, 'e22-refuse-unique-duplicates': 3022,
                'e23-refuse-required-null': 3058}
    cases = []
    for case in edits():
        case = dict(case, dates=case.pop('normalize_table_dates'))
        if not case['placement_roles']:
            del case['placement_roles']
        if not case['validation_returncode']:
            del case['validation_returncode']
        if case['name'] in refusals:
            case.update(kind='refused', dao_error=refusals[case['name']])
        cases.append(case)
    for name, source, step in rust_only():
        if source == 'nordic':
            # EXP-0300 recorded Rust refusals; Rust edits Nordic databases since EXP-0310, so
            # these now pair with DAO. Raw Nordic keys need a native key inventory (EXP-0309).
            cases.append({'name': name, 'input': source, 'steps': [step], 'dates': ['T'], 'skip_structure': True})
            continue
        cases.append({'name': name, 'input': source, 'kind': 'rust-only', 'native': False, 'steps': [dict(step, expected_returncode=1)]})
    used = {case['input'] for case in cases}
    return {'inputs': {k: v for k, v in inputs.items() if k in used}, 'creations': creation_list, 'cases': cases}
