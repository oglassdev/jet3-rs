"""Creation recipes: rows, indexes, long values and relationships built by one create request."""

from __future__ import annotations

import json
import struct

from . import col, create, index, recipe, table

I32_MIN, I32_MAX = -2**31, 2**31 - 1
I64_MIN, I64_MAX = -2**63, 2**63 - 1


def f32_from_bits(bits):
    return struct.unpack('<f', struct.pack('<I', bits))[0]


def f64_from_bits(bits):
    return struct.unpack('<d', struct.pack('<Q', bits))[0]


F32_MAX = f32_from_bits(0x7F7FFFFF)
F32_MIN_POSITIVE = f32_from_bits(0x00800000)
F64_MAX = f64_from_bits(0x7FEFFFFFFFFFFFFF)
F64_MIN_POSITIVE = f64_from_bits(0x0010000000000000)


def long(value):
    return {'long': value}


def pattern(seed, length):
    return [(i * 37 + seed * 13 + 11) % 256 for i in range(length)]


@recipe
def composite_index():
    """EXP-0126 composite Long index arms."""
    pairs = [[0, 0, 0], [I32_MIN, I32_MAX, 1], [I32_MAX, I32_MIN, 2], [-1, -1, 3], [-1, 0, 4], [-1, 1, 5],
             [0, I32_MIN, 6], [0, I32_MAX, 7], [1, -256, 8], [1, 255, 9], [256, -65536, 10], [-256, 65536, 11],
             [0, 0, 12], [-1, 0, 13]]
    single = [[a, 0, tag] for tag, a in enumerate([I32_MAX, I32_MIN, 0, -1, 1, -256, 255, 256, -65536, 65536])]
    arms = {
        'descending-unique': (['-A'], 'unique', single),
        'ascending-descending-unique': (['A', '-B'], 'unique', pairs[:12]),
        'descending-ascending-ordinary': (['-A', 'B'], 'ordinary', pairs),
    }
    columns = [col('A', 'long'), col('B', 'long'), col('Tag', 'long')]
    return [create(f'composite-{arm}.mdb',
                   table('Rows', columns, [[long(v) for v in row] for row in rows], [index('ByKey', kind, *fields)]))
            for arm, (fields, kind, rows) in arms.items()]


@recipe
def indexed_row():
    """Primary, unique and duplicate-key ordinary Long indexes over 255-byte payload rows."""
    images = []
    for kind in ('primary', 'unique', 'ordinary'):
        rows = [[long(9 - (p % 10 if kind == 'ordinary' else p)), {'text': chr(ord('a') + p) * 255}] for p in range(20)]
        images.append(create(f'indexed-row-{kind}.mdb',
                             table('Rows', [col('Id', 'long'), col('Payload', 'text', size=255)], rows,
                                   [index('ById', kind, 'Id')])))
    return images


@recipe
def initial_long_value():
    """Memo/OLE initial-payload length boundaries."""
    images = []
    for arm, kind in (('memo', 'memo'), ('ole', 'long_binary')):
        rows = []
        for row, length in enumerate([1, 32, 33, 512, 2036, 2037, 2048, 4064, 4096]):
            if kind == 'memo':
                payload = ''.join(chr(ord('A') + (o + row) % 26) for o in range(length))
            else:
                payload = [(o + row) % 256 for o in range(length)]
            rows.append([long(row + 1), {kind: payload}])
        rows.append([long(10), None])
        images.append(create(f'initial-long-value-{arm}.mdb',
                             table('Rows', [col('Id', 'long'), col('Payload', kind)], rows)))
    return images


@recipe
def initial_row():
    rows = [[long(1), {'text': 'one'}], [long(-2), {'text': 'two'}], [None, None]]
    return [create('initial_row.mdb', table('Rows', [col('Id', 'long'), col('Code', 'text', size=8)], rows))]


@recipe
def memo_allow_empty():
    rows = [[long(1), None], [long(2), {'memo': ''}], [long(3), {'memo': 'A'}]]
    return [create(file, table(name, [col('Id', 'long'), col(memo, 'memo', allow_zero_length=True)], rows))
            for file, name, memo in (('short.mdb', 'Rows', 'M'), ('renamed.mdb', 'Ledger7', 'Memo42Long'))]


@recipe
def multi_level_index():
    """Multi-level Long index trees: primary, descending composite, and a SingleLong relationship."""
    columns = [col('Id', 'long'), col('Payload', 'long')]
    primary = [index('ById', 'primary', 'Id')]
    composite_rows = [[long(p // 400), long(p // 800 - 9), long(p)] for p in range(12929)]
    return [
        create('primary.mdb', table('Rows', columns, [[long(27800 - p), long(p)] for p in range(27801)], primary)),
        create('composite.mdb', table('Empty', columns),
               table('Rows', [col('A', 'long'), col('B', 'long'), col('Payload', 'long')], composite_rows,
                     [index('ByKey', 'ordinary', '-B', 'A')])),
        create('relationship.mdb',
               table('Parents', columns, [[long(i), long(i + 100)] for i in range(-100, 101)], primary),
               table('Children', columns, [[long(p % 3 - 1), long(p)] for p in range(27801)]),
               relationship={'name': 'ParentChildren', 'parent': {'table': 'Parents', 'column': 'Id'},
                             'child': {'table': 'Children', 'column': 'Id'}}),
    ]


@recipe
def multi_page_row():
    """509 Long rows over three data pages."""
    return [create('multi_page_row.mdb', table('Rows', [col('Id', 'long')], [[long(v - 254)] for v in range(509)]))]


@recipe
def multiple_index():
    """EXP-0193 multiple populated indexes."""
    images = []
    for mixed in (False, True):
        columns = [col('Id', 'long'), col('Group', 'long'), col('Value', 'currency' if mixed else 'long')]
        indexes = [index('ZPrimary', 'primary', 'Id'), index('AGroup', 'ordinary', *(['-Value', 'Group'] if mixed else ['-Group']))]
        if not mixed:
            indexes.append(index('MMixed', 'unique', '-Group', 'Value'))
        rows = []
        for n in range(30 if mixed else 201):
            value = (None if n % 5 == 0 else {'currency': n % 4 - 2}) if mixed else long(n - 100)
            rows.append([long(n + 1), long(n % 3 - 1), value])
        images.append(create('mixed-null.mdb' if mixed else 'three-long.mdb', table('Rows', columns, rows, indexes)))
    return images


@recipe
def multi_table_row():
    """Mixed-table and empty-first initial rows."""
    id_column = [col('Id', 'long')]
    empty = table('Empty', id_column)
    memo = ''.join(chr(ord('A') + o % 26) for o in range(4096))
    mixed = create('multi-table-row-mixed.mdb',
                   table('Numbers', id_column, [[long(i)] for i in range(-254, 255)]),
                   table('Keys', id_column, [[long(3)], [long(-1)], [long(2)]], [index('ById', 'primary', 'Id')]),
                   table('Notes', [col('Payload', 'memo')], [[{'memo': memo}], [None]]),
                   empty)
    empty_first = create('multi-table-row-empty-first.mdb', empty,
                         table('Binary', [col('Payload', 'long_binary')], [[{'long_binary': [o % 256 for o in range(2048)]}]]))
    return [mixed, empty_first]


@recipe
def nullable_index():
    """Nullable Long index null policies, single and composite."""
    modes = {
        'unique': ('unique', None, False, False, 12),
        'ignore': ('unique', 'ignore_all_null', False, False, 12),
        'required': ('ordinary', 'required', False, False, 12),
        'composite': ('unique', None, True, False, 30000),
        'composite-ignore': ('ordinary', 'ignore_all_null', True, False, 1200),
        'auto': ('unique', None, True, True, 1200),
    }
    images = []
    for mode, (kind, policy, composite, generated, count) in modes.items():
        rows = []
        for n in range(count):
            if policy == 'required':
                a, b = long(n % 3), long(-n)
            elif composite:
                a, b = [(None, None), (None, long(1)), (long(1), None), (long(n), long(-n))][n % 4]
            else:
                a, b = (None if n % 3 == 0 else long(n - 6)), long(-n)
            rows.append([a, 'auto_increment' if generated else b, long(n)])
        extra = {'null_policy': policy} if policy else {}
        columns = [col('A', 'long'), col('B', 'auto_increment' if generated else 'long'), col('Payload', 'long')]
        images.append(create(f'nullable-index-{mode}.mdb', table('Empty', [col('Id', 'long')]),
                             table('Rows', columns, rows, [index('ByKey', kind, *(['A', '-B'] if composite else ['A']), **extra)])))
    return images


@recipe
def numeric_index():
    """EXP-0183 numeric index key encodings."""
    arms = {
        'boolean-asc': (['boolean'], 'asc', 'include', [[{'boolean': True}], [{'boolean': False}]]),
        'byte-desc': (['byte'], 'desc', 'include', [[{'byte': v}] for v in (0, 1, 127, 128, 255)]),
        'integer-asc': (['integer'], 'asc', 'include',
                        [[{'integer': v}] for v in (-32768, -256, -1, 0, 1, 255, 256, 32767)]),
        'currency-desc': (['currency'], 'desc', 'include',
                          [[{'currency': v}] for v in (I64_MIN, -10000, -1, 0, 1, 10000, I64_MAX)]),
        'single-asc': (['single'], 'asc', 'include',
                       [[{'single': v}] for v in (-F32_MAX, -1.0, 0.0, f32_from_bits(1), F32_MIN_POSITIVE, 1.0, F32_MAX)]),
        'double-ignore': (['double'], 'desc', 'ignore_all_null',
                          [[{'double': v}] for v in (-F64_MAX, -1.0, 0.0, f64_from_bits(1), F64_MIN_POSITIVE, 1.0, F64_MAX)]
                          + [[None], [None]]),
        'mixed-include': (['currency', 'double'], 'asc-desc', 'include',
                          [[{'currency': v - 60}, {'double': float(v)}] for v in range(120)]
                          + [[None, None], [None, {'double': 1.0}], [{'currency': 1}, None]] * 2),
        'mixed-required': (['integer', 'single'], 'asc-desc', 'required',
                           [[{'integer': -32768}, {'single': -1.0}], [{'integer': 0}, {'single': 0.0}],
                            [{'integer': 32767}, {'single': 1.0}]]),
    }
    images = []
    for name, (types, directions, policy, rows) in arms.items():
        names = ['A', 'B'][:len(types)]
        columns = [col(n, t) for n, t in zip(names, types)] + [col('Tag', 'long')]
        fields = [('' if d == 'asc' else '-') + n for n, d in zip(names, directions.split('-'))]
        rows = [row + [long(n + 1)] for n, row in enumerate(rows)]
        images.append(create(f'{name}.mdb', table('Rows', columns, rows,
                                                    [index('ByKey', 'unique', *fields, null_policy=policy)])))
    return images


@recipe
def relationship_row():
    """SingleLong relationship with repeated foreign keys across pages."""
    child_rows = [[{'text': chr(ord('a') + p) * 255}, long(1 + p % 3)] for p in range(20)]
    return [create('relationship_row.mdb',
                   table('Accounts7', [col('Code2', 'long'), col('Key1', 'long')],
                         [[long(9), long(1)], [long(8), long(2)], [long(7), long(3)]],
                         [index('Primary9', 'primary', 'Key1')]),
                   table('Events9', [col('Label3', 'text', size=255), col('Account4', 'long')], child_rows),
                   relationship={'name': 'Account7Events9', 'parent': {'table': 'Accounts7', 'column': 'Key1'},
                                 'child': {'table': 'Events9', 'column': 'Account4'}})]


RELATIONSHIP_GRAPH = json.loads('''
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
''')

# Matrix type codes -> (CLI type, cell key); 4/16 is AutoIncrement and 10/1 FixedText.
GRAPH_TYPES = {1: 'boolean', 2: 'byte', 3: 'integer', 4: 'long', 5: 'currency', 6: 'single', 7: 'double',
               8: 'date_time', 9: 'binary', 10: 'text', 11: 'long_binary', 12: 'memo', 15: 'guid'}
GRAPH_CELLS = {'fixed_text': 'text', 'auto_increment': 'long'}


def graph_bytes(value):
    """A graph value's bytes: strings, byte arrays, or repeat/pattern payload recipes."""
    if value is None or isinstance(value, (str, list)):
        return value
    if value['kind'] == 'repeat':
        return [value['byte']] * value['length']
    if value['kind'] == 'pattern':
        return pattern(value['seed'], value['length'])
    raise ValueError(value['kind'])


def graph_column(field):
    kind = GRAPH_TYPES[field['type']]
    if kind == 'long' and field.get('attributes') == 16:
        kind = 'auto_increment'
    if kind == 'text' and field.get('attributes') == 1:
        kind = 'fixed_text'
    extra = {'size': field['size']} if kind in ('binary', 'text', 'fixed_text') else {}
    if field.get('required'):
        extra['required'] = True
    if kind in ('text', 'fixed_text', 'memo') and field.get('allow_zero_length', True):
        extra['allow_zero_length'] = True
    return col(field['name'], kind, **extra)


def graph_cell(column, value):
    kind = column['type']
    if value is None:
        return 'auto_increment' if kind == 'auto_increment' else None
    if kind in ('binary', 'text', 'fixed_text', 'memo', 'long_binary', 'guid'):
        value = graph_bytes(value)
    elif kind == 'single':
        value = struct.unpack('<f', struct.pack('<f', value))[0]
    return {GRAPH_CELLS.get(kind, kind): value}


def graph_index(spec):
    fields = spec.get('fields') or [{'name': spec['field'], 'direction': spec.get('direction')}]
    kind = 'primary' if spec.get('primary') else 'unique' if spec.get('unique') else 'ordinary'
    extra = ({'null_policy': 'ignore_all_null'} if spec.get('ignore_nulls')
             else {'null_policy': 'required'} if spec.get('required') else {})
    return index(spec['name'], kind, *[('-' if f.get('direction') == 'desc' else '') + f['name'] for f in fields], **extra)


def graph_relation(relation):
    attributes = relation.get('attributes', 0)
    pairs = relation.get('fields', [relation])
    return {'name': relation['name'], 'unique': bool(attributes & 1), 'cascade_updates': bool(attributes & 256),
            'cascade_deletes': bool(attributes & 4096),
            'parent': {'table': relation['table'], 'columns': [p['field'] for p in pairs]},
            'child': {'table': relation['foreign_table'], 'columns': [p['foreign_field'] for p in pairs]}}


@recipe
def relationship_graph():
    """Relationship graphs (Graph layout) from the golden-check matrix."""
    images = []
    for case in RELATIONSHIP_GRAPH['graphs']:
        tables = []
        for spec in case['tables']:
            columns = [graph_column(f) for f in spec['fields']]
            rows = [[graph_cell(c, row[c['name']]) for c in columns] for row in spec['rows']]
            indexes = ([graph_index(i) for i in spec['indexes']] if 'indexes' in spec
                       else [index(f'By{spec["name"]}', 'primary', 'Id')])
            tables.append(table(spec['name'], columns, rows, indexes))
        relationships = [graph_relation(r) for r in case['relations']]
        for replica in range(1, RELATIONSHIP_GRAPH.get('replicas', 2) + 1):
            images.append(create(f'{case["name"]}-r{replica}.mdb', *tables, relationships=relationships))
    return images


RICH_RELATIONSHIP = json.loads('''
{"replicas": 1, "arms": [{"name": "mixed",
  "fields": [{"name": "Id", "type": 4}, {"name": "ParentId", "type": 4}, {"name": "Label", "type": 10, "size": 40, "allow_zero_length": true}, {"name": "Note", "type": 12, "allow_zero_length": true}, {"name": "Blob", "type": 11}],
  "parents": [{"Id": 1, "Label": "one"}, {"Id": 2, "Label": "two"}],
  "rows": [{"Id": 1, "ParentId": 1, "Label": {"kind": "ascii"}, "Note": {"kind": "repeat", "byte": 65, "length": 3000}, "Blob": {"kind": "pattern", "seed": 2, "length": 5000}},
           {"Id": 2, "ParentId": 2, "Label": {"kind": "empty"}, "Note": {"kind": "empty"}, "Blob": {"kind": "null"}},
           {"Id": 3, "ParentId": null, "Label": {"kind": "null"}, "Note": {"kind": "ascii"}, "Blob": {"kind": "repeat", "byte": 7, "length": 100}}]}]}
''')


def rich_payload(value, name, row, column):
    kind = value['kind']
    if kind == 'null':
        return None
    if kind == 'empty':
        return []
    if kind == 'repeat':
        return [value['byte']] * value['length']
    if kind == 'pattern':
        return pattern(value['seed'], value['length'])
    if kind == 'ascii':
        return f'label-{row}' if name == 'Label' else f'r{row:02}-c{column:02}'
    raise ValueError(kind)


@recipe
def rich_relationship():
    """SingleLong Parent/Child relationship over Text, Memo and OLE child rows."""
    rich_types = {10: 'text', 11: 'long_binary', 12: 'memo'}
    images = []
    for arm in RICH_RELATIONSHIP['arms']:
        columns, rows = [], [[] for _ in arm['rows']]
        for c, field in enumerate(arm['fields']):
            kind = 'auto_increment' if field['type'] == 4 and field.get('attributes') == 16 else (
                'long' if field['type'] == 4 else rich_types[field['type']])
            extra = {'size': field['size']} if kind == 'text' else {}
            if field.get('allow_zero_length') is True:
                extra['allow_zero_length'] = True
            columns.append(col(field['name'], kind, **extra))
            for r, row in enumerate(arm['rows']):
                value = row[field['name']]
                if kind == 'auto_increment':
                    cell = 'auto_increment'
                elif kind == 'long':
                    cell = None if value is None else long(value)
                else:
                    payload = rich_payload(value, field['name'], r, c)
                    cell = None if payload is None else {kind: payload}
                rows[r].append(cell)
        parent_id = arm['fields'][0]['name']
        child_fk = arm['fields'][1]['name']
        parents = table('Parent', [col('Id', 'long'), col('Label', 'text', size=32)],
                        [[long(p['Id']), {'text': p['Label']}] for p in arm['parents']],
                        [index('ByParent', 'primary', 'Id')])
        child = table('Child', columns, rows, [index('ByChild', 'primary', parent_id)])
        relation = {'name': 'ParentChild', 'parent': {'table': 'Parent', 'column': 'Id'},
                    'child': {'table': 'Child', 'column': child_fk}}
        for replica in range(1, RICH_RELATIONSHIP['replicas'] + 1):
            images.append(create(f'{arm["name"]}-r{replica}.mdb', parents, child, relationship=relation))
    return images


@recipe
def single_leaf_key():
    """EXP-0179 single-leaf index key updates: each original plus a one-field update candidate."""
    images = []
    for name, kind, descending, ids, selected, replacement in (
        ('ascending-primary', 'primary', False, [-10, 0, 10], 0, I32_MIN),
        ('descending-unique', 'unique', True, [I32_MIN, 0, I32_MAX], 0, I32_MAX - 1),
        ('full-leaf', 'primary', False, list(range(200)), 100, -1),
    ):
        columns = [col('Id', 'long'), col('Value', 'long'), col('Payload', 'text', size=8)]
        rows = [[long(i), long(n + 100), {'text': 'payload'}] for n, i in enumerate(ids)]
        images.append(create(f'{name}-original.mdb',
                             table('Items', columns, rows, [index('ByKey', kind, '-Id' if descending else 'Id')])))
        images.append({'file': f'{name}-candidate.mdb', 'from': f'{name}-original.mdb', 'steps': [
            {'command': 'mutate', 'locate': {'table': 'Items', 'id': selected},
             'request': {'operation': 'update', 'table': 'Items', 'column': 0, 'value': long(replacement)}}]})
    return images


@recipe
def autoincrement():
    """AutoIncrement rows (256 unindexed, 10 primary-indexed, or 256 plus a second indexed table)."""
    images = []
    for prefix, large in (('autoincrement', 256), ('autoincrement-validation', 300)):
        columns = [col('Id', 'auto_increment'), col('Tag', 'long')]
        for arm in ('unindexed', 'indexed', 'multi'):
            count = 10 if arm == 'indexed' else large
            rows = [['auto_increment', {'long': tag}] for tag in range(1, count + 1)]
            tables = [table('Rows', columns, rows, [index('PrimaryKey', 'primary', 'Id')] if arm == 'indexed' else [])]
            if arm == 'multi':
                tables.append(table('Later', columns, [['auto_increment', {'long': -1}]], [index('PrimaryKey', 'primary', 'Id')]))
            images.append(create(f'{prefix}-{arm}.mdb', *tables))
    return images
