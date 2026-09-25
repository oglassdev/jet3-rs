"""Mutation recipes: public row writes on created fixtures and retained native captures."""

from __future__ import annotations

from pathlib import Path

import structure

from . import ROW_OVERFLOW_CAPTURES, col, create, delete, index, insert, recipe, replace, table, update

DUPLICATE = 'Unsupported("duplicate unique key")'
RESOURCE = {'refused': 'Open(Header(Read(ResourceLimitExceeded { kind: TotalWorkUnits, requested: 1, maximum: 0 })))', 'limits': {'work-units': 0}}


def text(data: bytes):
    return data.decode('ascii') if data.isascii() else list(data)


# --- Mutation fixtures -----------------------------------------------------------------------

PK = [index('PrimaryKey', 'primary', 'Id')]


def fixture():
    fixed = [[{'long': i}, {'byte': i}, {'integer': i}, {'long': i}, {'currency': i}, {'single': i}, {'double': i},
              {'date_time': i}, {'guid': [i] + [0] * 14 + [i]}, {'text': str(i)}, {'text': str(i) * 255}] for i in (1, 2, 3)]
    rows = [[{'long': i}, {'long': i * 7}, {'text': 'r' * (i * 5)}, {'binary': [i, 1, 2]}, {'boolean': i % 2 == 0}]
            for i in range(1, 13)]
    ins = [[{'long': i}, {'long': i}, {'text': f'row {i}'}] for i in range(1, 6)]
    return create(
        'fixture.mdb',
        table('Fixed', [col('Id', 'long'), col('B', 'byte'), col('I', 'integer'), col('L', 'long'), col('C', 'currency'),
                        col('S', 'single'), col('D', 'double'), col('Dt', 'date_time'), col('G', 'guid'),
                        col('F1', 'fixed_text', size=1), col('F255', 'fixed_text', size=255)], fixed, PK),
        table('Rows', [col('Id', 'long'), col('Value', 'long'), col('Payload', 'text', size=80),
                       col('Bin', 'binary', size=16), col('Flag', 'boolean')], rows, PK),
        table('Ins', [col('Id', 'long'), col('Value', 'long'), col('Payload', 'text', size=80)], ins, PK))


def derived(file, *steps):
    return {'file': file, 'from': 'fixture.mdb', 'steps': list(steps)}


@recipe
def fixed_field_update():
    """fixed_field_update_candidate: one fixed-width field of Fixed Id 2 per arm."""
    guid = [0, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff]
    arms = [('byte', 1, {'byte': 255}), ('integer', 2, {'integer': -2**15}), ('long-control', 3, {'long': -2**31}),
            ('currency', 4, {'currency': -2**63}), ('single', 5, {'single': -0.0}), ('double', 6, {'double': -0.0}),
            ('date', 7, {'date_time': -1.25}), ('guid', 8, {'guid': guid}),
            ('fixed-text-1', 9, {'text': [0xe9]}), ('fixed-text-255', 10, {'text': [0xe9] * 255})]
    return [fixture()] + [derived(f'fixed-{arm}.mdb', update('Fixed', 2, column, value)) for arm, column, value in arms]


@recipe
def row_update():
    """row_update_candidate: complete replacement of one Rows row per profile."""
    profiles = [
        ('grow-first', 1, [{'long': 1}, None, {'text': 'g' * 60}, {'binary': [1, 35, 69, 103, 137, 171, 205, 239]}, {'boolean': False}]),
        ('shrink-middle', 2, [{'long': 2}, {'long': -42}, None, {'binary': [1, 2]}, {'boolean': True}]),
        ('null-later', 12, [{'long': 12}, {'long': -2**31}, {'text': 'restored'}, {'binary': [0, 255, 1, 2]}, {'boolean': True}]),
        ('tombstone', 3, [{'long': 3}, None, None, None, {'boolean': False}]),
    ]
    return [fixture()] + [derived(f'row-update-{name}.mdb', replace('Rows', id, values)) for name, id, values in profiles]


@recipe
def field_update():
    """field_update_candidate: Rows Id 4 Value = 424242."""
    return [fixture(), derived('field-update.mdb', update('Rows', 4, 1, {'long': 424242}))]


@recipe
def row_insert():
    """row_insert_candidate: Ins (100, 7, 'hello')."""
    return [fixture(), derived('row-insert.mdb', insert('Ins', [{'long': 100}, {'long': 7}, {'text': 'hello'}]))]


@recipe
def row_delete():
    """row_delete_candidate: delete Rows Id 5."""
    return [fixture(), derived('row-delete.mdb', delete('Rows', 5))]


@recipe
def row_delete_compaction():
    """row_delete_compaction: delete Rows Ids 6, 7, 8, 9, 1 in order on one copy."""
    return [fixture(), derived('row-delete-compaction.mdb', *(delete('Rows', id) for id in (6, 7, 8, 9, 1)))]


@recipe
def relationship_mutation():
    """relationship_mutation_candidate: the golden-check recipe.json stages and refusals on rel.mdb."""
    rel = create(
        'rel.mdb',
        table('Parent', [col('Id', 'long'), col('Label', 'text', size=32)],
              [[{'long': 1}, {'text': 'p1'}], [{'long': 2}, {'text': 'p2'}]], PK),
        table('Child', [col('Id', 'long'), col('ParentId', 'long'), col('Note', 'memo'), col('Blob', 'long_binary')],
              [[{'long': 1}, {'long': 1}, {'memo': 'n1'}, {'long_binary': [1, 2]}], [{'long': 2}, {'long': 1}, None, None],
               [{'long': 3}, {'long': 2}, {'memo': 'q' * 2500}, None]], PK),
        relationships=[{'name': 'ParentChild', 'cascade_deletes': True,
                        'parent': {'table': 'Parent', 'column': 'Id'}, 'child': {'table': 'Child', 'column': 'ParentId'}}])
    stages = [
        ('insert', [insert('Parent', [{'long': 3}, {'text': 'p3'}]),
                    insert('Child', [{'long': 10}, {'long': 3}, {'memo': 'n' * 3000}, {'long_binary': [0xab] * 5000}])]),
        ('field', [update('Child', 10, 2, {'memo': 'hi'}), update('Child', 1, 3, {'long_binary': [0xcd] * 4000})]),
        ('replace', [replace('Child', 2, [{'long': 2}, {'long': 3}, None, None])]),
        ('delete', [delete('Parent', 1), delete('Child', 10)]),
    ]
    images, previous = [rel], 'rel.mdb'
    for name, steps in stages:
        images.append({'file': f'{name}.mdb', 'from': previous, 'steps': steps})
        previous = f'{name}.mdb'
    refusals = [
        ('orphan', insert('Child', [{'long': 99}, {'long': 42}, None, None],
                          refused='RelationshipConstraint { parent: PageNumber(20), child: PageNumber(25), value: 42 }')),
        ('duplicate', insert('Parent', [{'long': 1}, {'text': 'x'}], refused=DUPLICATE)),
        ('limited', insert('Parent', [{'long': 50}, {'text': 'x'}], **RESOURCE)),
    ]
    images += [{'file': f'refusal-{name}.mdb', 'from': 'rel.mdb', 'steps': [step]} for name, step in refusals]
    return images


# --- Wide-row lifecycle models (empty_value, fixed_text_index, row_overflow) ------------------
# A row is a list of (kind, value) scalars: null, long, text, memo, ole, binary.

NULL = ('null', None)
ALPHABET = b'aAezZ\xe9\xc9\xc6\xe6\xdf\x8a\x9a'
NOTES = table('Notes', [col('Id', 'long'), col('Body', 'memo')], [[{'long': 7}, {'memo': 'n' * 4096}], [{'long': 8}, None]])


def payload(length, id, column, is_text):
    if is_text:
        return bytes(ALPHABET[(n * 7 + id * 13 + column * 31) % len(ALPHABET)] for n in range(length))
    return bytes((n * 37 + id * 13 + column * 31) % 256 for n in range(length))


def cell(scalar):
    kind, value = scalar
    if kind == 'null':
        return None
    if kind == 'long':
        return {'long': value}
    return {{'text': 'text', 'memo': 'memo', 'ole': 'long_binary', 'binary': 'binary'}[kind]:
            text(value) if kind in ('text', 'memo') else list(value)}


def cells(row):
    return [cell(s) for s in row]


def normalize(model):
    for id, row in model.items():
        model[id] = [NULL if s == ('ole', b'') else s for s in row]


def lifecycle(model, operations, normalized=False, direct_text=False):
    """jet3-cli steps for `operations` on Items, applied to `model`.

    Field edits update in place when both values are Long (or, with `direct_text`, when a Text
    value replaces column 1); other field edits replace the whole row."""
    steps = []
    for operation in operations:
        kind = operation[0]
        if kind == 'insert':
            row = operation[1]
            steps.append(insert('Items', cells(row)))
            model[row[0][1]] = row
        elif kind == 'delete':
            steps.append(delete('Items', operation[1]))
            del model[operation[1]]
        elif kind == 'replace':
            old, row = operation[1:]
            steps.append(replace('Items', old, cells(row)))
            del model[old]
            model[row[0][1]] = row
        else:
            old, column, value = operation[1:]
            row = list(model[old])
            previous, row[column] = row[column], value
            if direct_text and column == 1 and value[0] == 'text':
                steps.append(update('Items', old, column, cell(value)))
            elif previous[0] != 'long' or value[0] != 'long':
                steps.append(replace('Items', old, cells(row)))
            else:
                steps.append(update('Items', old, column, cell(value)))
            del model[old]
            model[row[0][1]] = row
        if normalized:
            normalize(model)
    return steps


def phases(case, model, stages, **options):
    """`{case}-{phase}.mdb` images, each continuing the previous phase."""
    images, previous = [], f'{case}-original.mdb'
    for phase, operations in stages:
        images.append({'file': f'{case}-{phase}.mdb', 'from': previous, 'steps': lifecycle(model, operations, **options)})
        previous = f'{case}-{phase}.mdb'
    return images


def refusal_images(source, refusals):
    images = []
    for name, step in refusals:
        images.append({'file': f'refusal-{name}-before.mdb', 'from': source})
        images.append({'file': f'refusal-{name}-after.mdb', 'from': source, 'steps': [step]})
    return images


@recipe
def empty_value():
    """empty_value_candidate: Text/Memo empty-value and OLE-null lifecycles."""
    images = []
    for case in ('mixed-first', 'mixed-later', 'property-chain', 'unique-text'):
        chain = case == 'property-chain'

        def row(id):
            if id % 13 == 0:
                code = NULL
            elif id == 1 or (case != 'unique-text' and id % 7 == 1):
                code = ('text', b'')
            else:
                code = ('text', f'{id:08}'.encode())
            n = id % 5
            body = NULL if n == 0 else ('memo', payload([0, 0, 1, 33, 4096][n], id, 3, True))
            blob = NULL if n == 0 else ('ole', payload([0, 0, 1, 33, 4096][n], id, 6, False))
            result = [('long', id), code, ('text', b' ' * 8), body, ('text', b'kept'), ('memo', payload(32, id, 5, True)),
                      blob, ('long', id % 5), ('memo', b'')]
            if chain:
                result += [('text', b'' if i % 2 == 0 else b'value') for i in range(26)]
            return result

        columns = [col('Id', 'long'), col('Code', 'text', size=8, allow_zero_length=True), col('Fixed', 'fixed_text', size=8),
                   col('Body Memo', 'memo', allow_zero_length=True), col('OtherText', 'text', size=8), col('OtherMemo', 'memo'),
                   col('Blob', 'long_binary'), col('Group', 'long'), col('Extra_Memo', 'memo', allow_zero_length=True)]
        if chain:
            columns += [col(f'Field{i:02}_' + 'x' * 56, 'text', size=8, **({'allow_zero_length': True} if i % 2 == 0 else {}))
                        for i in range(26)]
        model = {id: row(id) for id in range(48)}
        items = table('Items', columns, [cells(model[id]) for id in sorted(model)],
                      [index('ById', 'primary', 'Id'), index('ByCode', 'unique' if case == 'unique-text' else 'ordinary', 'Code'),
                       index('ByPair', 'ordinary', '-Code', 'Group')])
        images.append(create(f'{case}-original.mdb', *((NOTES, items) if case == 'mixed-later' else (items, NOTES))))
        normalize(model)

        changed = row(4)
        changed[1], changed[3], changed[6] = NULL, ('memo', b''), ('ole', b'')
        edited = [('field', 1, 1, ('text', b'changed ')), ('field', 2, 1, ('text', b'')),
                  ('field', 3, 3, ('memo', payload(4096, 3, 3, True))), ('replace', 4, changed),
                  ('field', 7, 0, ('long', 700)), ('delete', 5)]
        edited += [('insert', row(id)) for id in range(1000, 1008)]
        regrown = [('delete', id) for id in range(20, 40)] + [('insert', row(id)) for id in range(2000, 2020)]
        images += phases(case, model, [('edited', edited), ('regrown', regrown)], normalized=True)

        def with_value(row, column, value):
            row[column] = value
            return row

        refusals = {
            'mixed-later': [
                ('disabled-text-insert', insert('Items', cells(with_value(row(9999), 4, ('text', b''))),
                                                 refused='Encoding(ZeroLengthNotAllowed { ordinal: 4, physical_type: Text })')),
                ('disabled-memo-row', replace('Items', 4, cells(with_value(row(4), 5, ('memo', b''))),
                                               refused='Encoding(ZeroLengthNotAllowed { ordinal: 5, physical_type: Memo })')),
                ('resource', insert('Items', cells(row(9999)), **RESOURCE)),
            ],
            'unique-text': [('unique-empty', insert('Items', cells(with_value(row(9999), 1, ('text', b''))), refused=DUPLICATE))],
        }.get(case, [])
        images += refusal_images(f'{case}-original.mdb', refusals)
    return images


@recipe
def fixed_text_index():
    """fixed_text_index_candidate: Fixed Text index creation, field/full-row changes and refusals."""
    images = []
    for case, width in (('fixed1', 1), ('fixed8', 8), ('fixed32', 32), ('fixed255', 255)):
        def code(seed):
            if case == 'fixed8':
                return {1: b'a       ', 100: b'c       ', 101: b'd       '}.get(seed, f'{seed:08}'.encode())
            if seed % 11 == 0:
                return (b'A' if (seed // 11) % 2 == 0 else b'a') + b' ' * (width - 1)
            return payload(width, seed, 1, True)

        def row(id):
            return [('long', id), NULL if id % 13 == 0 else ('text', code(id)), NULL if id % 7 == 0 else ('long', id % 5),
                    ('memo', payload(80, id, 3, True)), ('ole', payload(80, id, 4, False))]

        columns = [col('Id', 'long'), col('Code', 'fixed_text', size=width), col('Group', 'long'), col('Body', 'memo'),
                   col('Blob', 'long_binary')]
        indexes = [index('ById', 'primary', 'Id'), index('ByCode', 'unique' if case == 'fixed8' else 'ordinary', 'Code'),
                   index('ByCodeDesc', 'ordinary', '-Code', null_policy='ignore_all_null'),
                   index('ByPair', 'ordinary', '-Code', 'Group')]
        model = {id: row(id) for id in range(96)}
        images.append(create(f'{case}-original.mdb', table('Items', columns, [cells(model[id]) for id in sorted(model)], indexes), NOTES))

        clear, present = row(2), row(13)
        clear[1], present[1] = NULL, ('text', code(101))
        edited = [('field', 1, 1, ('text', code(100))), ('replace', 2, clear), ('replace', 13, present),
                  ('field', 7, 0, ('long', 700)), ('delete', 3)]
        edited += [('insert', row(id)) for id in range(1000, 1012)]
        regrown = [('delete', id) for id in range(20, 60)] + [('insert', row(id)) for id in range(2000, 2040)]
        images += phases(case, model, [('edited', edited), ('regrown', regrown)], direct_text=True)

        if case == 'fixed8':
            upper = {'text': 'A       '}
            duplicate_row, duplicate_insert = cells(row(2)), cells(row(9999))
            duplicate_row[1] = duplicate_insert[1] = upper
            images += refusal_images('fixed8-original.mdb', [
                ('width', update('Items', 1, 1, {'text': 'x' * (width - 1)}, refused=f'Encoding(InvalidWidth {{ ordinal: 1, physical_type: Text, expected: {width}, actual: {width - 1} }})')),
                ('duplicate-insert', insert('Items', duplicate_insert, refused=DUPLICATE)),
                ('duplicate-field', update('Items', 2, 1, upper, refused=DUPLICATE)),
                ('duplicate-row', replace('Items', 2, duplicate_row, refused=DUPLICATE)),
                ('resource', insert('Items', cells(row(9999)), **RESOURCE)),
            ])
    return images


def captured_model(path):
    """Items rows of a retained capture as model scalars, keyed by Id."""
    data = path.read_bytes()
    items = structure.tables(data)['Items']
    columns = items['definition']['columns']
    kinds = {'Long': 'long', 'Text': 'text', 'Binary': 'binary', 'Memo': 'memo', 'LongBinary': 'ole'}
    model = {}
    for stored in structure.rows(data, items):
        row = []
        for column in columns:
            value, kind = stored['values'][column['name']], kinds[column['type']]
            if value is None:
                row.append(NULL)
            elif kind == 'long':
                row.append(('long', value))
            elif kind == 'text':
                row.append(('text', value.encode('cp1252') if isinstance(value, str) else bytes.fromhex(value['raw_hex'])))
            else:
                row.append((kind, bytes.fromhex(value)))
        model[row[0][1]] = row
    return model


def row_overflow():
    """row_overflow_candidate: public mutations of the retained native row-overflow captures."""
    images = []
    for case, mixed, native in (('ordinary', False, False), ('payloads', True, False), ('payloads-native', True, True)):
        source = Path(ROW_OVERFLOW_CAPTURES) / f'{"payloads" if mixed else "ordinary"}-r1-{"grown" if native else "original"}.mdb'
        selected = [0, 15, 31, 71] if mixed else [0, 27, 55, 71]
        renamed = selected[2] + 900

        def row(id, width):
            result = [('long', id), ('text', payload(width, id, 1, True)), ('binary', payload(width, id, 2, False))]
            if mixed:
                result += [('memo', payload(80, id, 3, True)), ('ole', payload(80, id, 4, False))]
            return result

        def replacements(ids, width, salt):
            operations = []
            for id in ids:
                seed = id - 900 if id >= 900 else id
                short = list(model[id])
                short[1] = ('text', bytes(ALPHABET[(n * 7 + seed * 13 + 31 + salt * 5) % len(ALPHABET)] for n in range(width)))
                short[2] = ('binary', bytes((n * 37 + seed * 13 + 62 + salt * 17) % 256 for n in range(width)))
                operations.append(('replace', id, short))
            return operations

        model = captured_model(source)
        images += [{'file': f'{case}-source.mdb', 'from': source}, {'file': f'{case}-original.mdb', 'from': source}]
        previous = f'{case}-original.mdb'
        stages = [
            ('grown', lambda: replacements(selected, 180, 0)),
            ('equal', lambda: replacements(selected[:2], 180, 1)),
            ('keyed', lambda: [('field', selected[2], 0, ('long', renamed))]),
            ('shrunken', lambda: replacements(selected[:2], 1, 2)),
            ('filled', lambda: [('insert', row(id, 12)) for id in range(1000, 1013)]),
            ('relocated', lambda: replacements([selected[0], selected[1], renamed, selected[3]], 255, 3)),
            ('shared', lambda: [('insert', row(id, 150)) for id in range(2000, 2008)]),
            ('deleted', lambda: [('delete', id) for id in (selected[0], selected[1], renamed, selected[3])]),
            ('released', lambda: [('delete', id) for id in [*range(1000, 1013), *range(2000, 2008)]]),
            ('reinserted', lambda: [('insert', row(id, 255)) for id in range(3000, 3008)]),
        ]
        for phase, operations in stages:
            images.append({'file': f'{case}-{phase}.mdb', 'from': previous, 'steps': lifecycle(model, operations())})
            previous = f'{case}-{phase}.mdb'
            if case == 'payloads' and phase == 'grown':
                images += refusal_images('payloads-grown.mdb', [
                    ('duplicate-insert', insert('Items', cells(row(0, 255)), refused=DUPLICATE)),
                    ('duplicate-replace', replace('Items', selected[1], cells(row(0, 255)), refused=DUPLICATE)),
                    ('resource', insert('Items', cells(row(9999, 255)), **RESOURCE)),
                ])
    return images


if ROW_OVERFLOW_CAPTURES:
    recipe(row_overflow)
