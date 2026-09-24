"""Plans and expected-value models of the storage lifecycle suites (EXP-0304, EXP-0311).

A plan is a list of cases. Each case builds one native database (the `Items` table under
test, a `Watch` table with payloads, saved queries of every form and opaque custom
properties), then applies named stages of `jet3-cli mutate`-shaped operations to both the
native image (through DAO) and a Rust candidate derived from the native original.
"""

from __future__ import annotations

import copy
import hashlib

QUERIES = [
    ('Q Select', 'SELECT Id, Text1 FROM Items ORDER BY Id;', 0),
    ('Q Parameter', 'PARAMETERS [pId] Long, [pText] Text(255); SELECT Id FROM Items WHERE Id=[pId] OR Text1=[pText];', 0),
    ('Q Aggregate', 'SELECT Text1, Count(*) AS N FROM Items GROUP BY Text1;', 0),
    ('Q Join', 'SELECT Items.Id, Watch.Text1 FROM Items LEFT JOIN Watch ON Items.Id=Watch.Id;', 0),
    ('Q Union', 'SELECT Id FROM Items UNION SELECT Id FROM Watch;', 128),
    ('Q Crosstab', 'TRANSFORM Count(Id) AS N SELECT Text1 FROM Items GROUP BY Text1 PIVOT (Id Mod 3);', 16),
    ('Q Update', "UPDATE Items SET Text1='never executed' WHERE Id=-1;", 48),
    ('Q Append', 'INSERT INTO Watch (Id, Text1) SELECT Id, Text1 FROM Items WHERE Id=-1;', 64),
    ('Q Delete', 'DELETE FROM Items WHERE Id=-1;', 32),
    ('Q MakeTable', 'SELECT Id, Text1 INTO NeverExecuted FROM Items;', 80),
    ('Q DDL', 'CREATE TABLE NeverExecutedDDL (Id LONG);', 96),
]

WATCH_COLUMNS = [
    {'name': 'Id', 'type': 'long'},
    {'name': 'Text1', 'type': 'text', 'size': 255},
    {'name': 'Memo', 'type': 'memo'},
    {'name': 'Blob', 'type': 'long_binary'},
]


# --- Rows and operations -------------------------------------------------------------

def columns(kind):
    result = [{'name': 'Id', 'type': 'auto_increment' if kind == 'sparse' else 'long'},
              {'name': 'Text1', 'type': 'text', 'size': 255, 'required': True}]
    result += [{'name': f'Text{i}', 'type': 'text', 'size': 255} for i in range(2, 7)]
    if kind != 'wide':
        result += [{'name': name, 'type': typ} for name, typ in
                   (('Memo1', 'memo'), ('Blob1', 'long_binary'), ('Memo2', 'memo'), ('Blob2', 'long_binary'))]
    return result


def row(kind, ident, width=20, lengths=(40, 80, 160, 2100)):
    """Positional jet3-cli cells; a `None` length leaves that payload column Null."""
    result = [{'long': ident}]
    result += [{'text': (f'{ident:04d}-{i}-' + chr(65 + i) * width)[:width]} for i in range(1, 7)]
    if kind != 'wide':
        for i, (typ, length) in enumerate(zip(('memo', 'long_binary', 'memo', 'long_binary'), lengths)):
            if length is None:
                result.append(None)
            elif typ == 'memo':
                result.append({typ: ''.join(chr(65 + (j * 7 + ident + i) % 26) for j in range(length))})
            else:
                result.append({typ: [(j * 13 + ident + i) % 256 for j in range(length)]})
    return result


def tiny_row(ident, flipped=False):
    return [{'byte': ident}] + [{'boolean': bool(ident & (1 << bit)) != flipped} for bit in range(6)]


def operation(kind, ident=None, values=None):
    value = {'operation': kind, 'table': 'Items'}
    if ident is not None:
        value['id'] = ident
    if values is not None:
        value['values'] = values
    return value


# --- storage-preservation (EXP-0303..0306) ---------------------------------------------

def preservation_stages(kind):
    deleted = list(range(3, 49, 3))
    kept = [i for i in range(1, 49) if i not in deleted]
    return [
        {'name': 'holes', 'operations': [operation('delete', i) for i in deleted]},
        {'name': 'refilled', 'operations': [operation('insert', values=row(kind, 100 + i, 12)) for i in deleted]},
        {'name': 'grown', 'operations': [operation('replace', i, row(kind, i, 230, (2100, 4200, 6000, 8000)))
                                         for i in (1, 16, 31, 46)]},
        {'name': 'regrown', 'operations': [operation('replace', i, row(kind, i, 255, (8000, 6000, 4200, 2100)))
                                           for i in (1, 16, 31, 46)]},
        {'name': 'shrunken', 'operations': [operation('replace', i, row(kind, i, 2, (None, 12, 40, None)))
                                            for i in (1, 16, 31, 46)]},
        {'name': 'released', 'operations': [operation('delete', i) for i in [*kept, *(100 + i for i in deleted)]]},
        {'name': 'reused', 'operations': [operation('insert', values=row(kind, 200 + i, 24, (80, 160, 2100, 40)))
                                          for i in range(1, 49)]},
    ]


def preservation():
    cases = []
    for kind in ('wide', 'payloads', 'sparse'):
        for replica in (1, 2):
            cases.append({
                'name': f'{kind}-r{replica}', 'kind': kind, 'columns': columns(kind),
                'initial': [row(kind, i) for i in range(1, 49)], 'stages': preservation_stages(kind),
                'continuation': [operation('replace', 201, row(kind, 201, 200, (4200, 40, None, 6000))),
                                 operation('delete', 202), operation('insert', values=row(kind, 901))],
            })
    return cases


def refusals(case):
    """Constraint refusals Rust must reject whole-file and DAO must refuse natively."""
    required = row(case['kind'], 1)
    required[1] = None
    return [
        {'name': 'duplicate', 'command': 'mutate', 'error': 3022,
         'request': operation('insert', values=case['initial'][0])},
        {'name': 'required', 'command': 'mutate', 'error': 3314,
         'request': operation('replace', 1, required)},
        {'name': 'relationship', 'command': 'schema', 'error': 3201,
         'request': {'operation': 'create_relationship', 'relationship': {
             'name': 'Rejected', 'parent': {'table': 'Watch', 'column': 'Id'},
             'child': {'table': 'Items', 'column': 'Id'}}}},
    ]


# --- storage-churn (EXP-0311) --------------------------------------------------------

def churn_case(kind, replica):
    if kind == 'tiny':
        case_columns = [{'name': 'Id', 'type': 'byte'}]
        case_columns += [{'name': f'Text{i}', 'type': 'boolean'} for i in range(1, 7)]
        initial = [tiny_row(i) for i in range(256)]
        stages = [{'name': 'holes', 'operations': [operation('delete', i) for i in range(1, 32)]},
                  {'name': 'refilled', 'operations': [operation('insert', values=tiny_row(i, True)) for i in range(1, 32)]}]
        current = list(range(256))
        reused = [tiny_row(i) for i in current]
        continuation = [operation('replace', 0, tiny_row(0, True)),
                        operation('delete', 255), operation('insert', values=tiny_row(255, True))]
    else:
        case_columns = columns(kind)
        initial = [row(kind, i, 1) for i in range(1, 49)]
        current, stages = list(range(2, 49)), []
        for cycle in range(1, 7):
            stages.append({'name': f'holes-{cycle}', 'operations': [operation('delete', i) for i in current]})
            current = list(range(cycle * 1000 + 1, cycle * 1000 + 48))
            stages.append({'name': f'refilled-{cycle}',
                           'operations': [operation('insert', values=row(kind, i, 1)) for i in current]})
        current = [1, *current]
        reused = [row(kind, i, 1) for i in range(7001, 7049)]
        continuation = [operation('replace', 7001, row(kind, 7001, 255, (8000, 6000, 4200, 2100))),
                        operation('delete', 7002), operation('insert', values=row(kind, 9001, 1))]
    stages += [
        {'name': 'released', 'operations': [operation('delete', i) for i in current]},
        {'name': 'reused', 'operations': [operation('insert', values=values) for values in reused]},
    ]
    return {'name': f'{kind}-churn-r{replica}', 'kind': kind, 'columns': case_columns, 'initial': initial,
            'stages': stages, 'continuation': continuation}


def churn():
    return [churn_case(kind, replica) for kind in ('wide', 'payloads', 'tiny') for replica in (1, 2)]


PLANS = {'preservation': preservation, 'churn': churn}


# --- Native DAO operations -----------------------------------------------------------

def native_op(op):
    """A jet3-cli mutation as a Native.ps1 op (table-type recordset, Seek on PrimaryKey)."""
    result = {'op': op['operation'], 'table': op['table']}
    if 'id' in op:
        result['id'] = op['id']
    if 'values' in op:
        result['values'] = op['values']
    return result


def storage_table(name, table_columns):
    """Primary key on Id, a secondary ByText index and opaque table/field properties."""
    table_columns = copy.deepcopy(table_columns)
    for column in table_columns:
        if column['name'] == 'Text1':
            column['properties'] = {'OpaqueFieldTag': f'retained field {name}'}
    return {'op': 'table', 'table': {
        'name': name, 'columns': table_columns,
        'indexes': [{'name': 'PrimaryKey', 'kind': 'primary', 'fields': [{'column': 'Id'}]},
                    {'name': 'ByText', 'kind': 'ordinary', 'fields': [{'column': 'Text1'}]}],
        'properties': {'OpaqueTableTag': f'retained {name}'}}}


def native_input(case):
    """Native.ps1 input: the original database, then one checkpoint per stage."""
    ops = [
        storage_table('Items', case['columns']),
        storage_table('Watch', WATCH_COLUMNS),
        {'op': 'sql', 'text': "INSERT INTO Watch (Id,Text1) VALUES (1,'sentinel')"},
        {'op': 'payload', 'table': 'Watch', 'key': 'Id', 'id': 1, 'column': 'Memo', 'length': 9000, 'seed': 7},
        {'op': 'payload', 'table': 'Watch', 'key': 'Id', 'id': 1, 'column': 'Blob', 'length': 6000, 'seed': 11},
    ]
    ops += [native_op(operation('insert', values=values)) for values in case['initial']]
    if case['kind'] == 'sparse':
        # Leaves both a fixed and a variable storage-ID gap before the lifecycle.
        ops += [
            {'op': 'request', 'command': 'schema', 'request': {
                'operation': 'create_column', 'table': 'Items', 'column': {'name': 'RetiredFixed', 'type': 'long'}}},
            {'op': 'request', 'command': 'schema', 'request': {
                'operation': 'create_column', 'table': 'Items',
                'column': {'name': 'RetiredVariable', 'type': 'text', 'size': 20}}},
            {'op': 'sql', 'text': "UPDATE Items SET RetiredFixed=42, RetiredVariable='old bytes'"},
            {'op': 'request', 'command': 'schema',
             'request': {'operation': 'drop_column', 'table': 'Items', 'column': 'RetiredFixed'}},
            {'op': 'request', 'command': 'schema',
             'request': {'operation': 'drop_column', 'table': 'Items', 'column': 'RetiredVariable'}},
        ]
    for name, sql, _ in QUERIES:
        ops += [{'op': 'query', 'name': name, 'sql': sql},
                {'op': 'property', 'target': 'query', 'query': name, 'name': 'OpaqueQueryTag', 'value': f'retained {name}'}]
    ops += [{'op': 'property', 'target': 'database', 'name': 'OpaqueDatabaseTag', 'value': 'database sentinel'},
            {'op': 'checkpoint', 'file': f'native-{case["name"]}-original.mdb'}]
    for stage in case['stages']:
        ops += [native_op(op) for op in stage['operations']]
        ops.append({'op': 'checkpoint', 'file': f'native-{case["name"]}-{stage["name"]}.mdb'})
    return {'name': case['name'], 'file': f'native-{case["name"]}-final.mdb', 'locale': 'general', 'ops': ops}


def failure_edits(case):
    """Each refusal on the native original, directly and inside a rolled-back transaction."""
    edits = []
    for refusal in refusals(case):
        request = refusal['request']
        refused = {'op': 'request', 'command': 'schema', 'request': request} if refusal['command'] == 'schema' \
            else native_op(request)
        for suffix, transaction in (('', False), ('-rollback', True)):
            steps = [refused]
            if transaction and refusal['name'] != 'relationship':
                steps = [{'op': 'sql', 'text': "UPDATE Items SET Text1='rolled back' WHERE Id=1"}, refused]
            label = f'{case["name"]}-{refusal["name"]}{suffix}'
            edits.append({'name': 'failure-' + label, 'source': f'native-{case["name"]}-original.mdb',
                          'file': f'failure-{label}.mdb', 'steps': steps, 'transaction': transaction})
    return edits


# --- Expected values -----------------------------------------------------------------

def model_row(case_columns, cells, dao=False):
    """Expected raw values (payloads as hex) or DAO values (Observe.ps1 Norm)."""
    result = {}
    for column, cell in zip(case_columns, cells, strict=True):
        value = None if cell is None else next(iter(cell.values()))
        typ = column['type']
        if value is not None and typ in ('memo', 'long_binary'):
            data = value.encode('cp1252') if typ == 'memo' else bytes(value)
            if not dao:
                value = data.hex()
            elif typ == 'long_binary':
                value = {'kind': 'bytes', 'length': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                if len(data) <= 255:
                    value['hex'] = data.hex()
            elif len(value) > 1024:
                value = {'kind': 'text', 'length': len(value), 'sha256': hashlib.sha256(value.encode('utf-8')).hexdigest()}
        result[column['name']] = value
    return result


def apply_model(rows, operations):
    """`rows` maps Id to cells; applies inserts, replacements and deletions."""
    for op in operations:
        if op['operation'] == 'delete':
            del rows[op['id']]
            continue
        ident = next(iter(op['values'][0].values()))
        if op['operation'] == 'replace':
            del rows[op['id']]
        rows[ident] = op['values']
