"""Multi-column Memo/OLE creation (EXP-0235/0236).

Eight cases create Items with four to eight long-value columns, zero to three Long indexes,
generated Ids and later table order; each map group, external page count and payload
fragment is checked independently and the Rust reader inspects every DAO output. Both the
candidate and a DAO control then take native insert/replace/delete writes.
"""

from __future__ import annotations

import copy
from pathlib import Path

import structure
from registry import common, scalar
from registry.common import identity, require

SCRIPT = common.REGISTRY / 'multiple_long_values.ps1'
MANIFEST = 'multiple-long-value-creation.json'
NAMES = ['Body', 'Blob', 'ExtraMemo', 'ExtraBlob', 'LastMemo', 'LastBlob', 'WideMemo', 'WideBlob']
LENGTHS = [1, 32, 33, 512, 2036, 2037, 2048, 4064, 4096]
NOTES_FIELDS = [['Id', 4, 4, False], ['Body', 12, 0, False]]
TABLES = ['Items', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Notes']
CASES = [('first-mixed', 205, 4, 3, False, False), ('later-generated', 213, 4, 3, True, True),
         ('capacity-six-zero', 12, 6, 0, False, False), ('capacity-six-one', 12, 6, 1, False, True),
         ('capacity-five-two', 12, 5, 2, False, False), ('capacity-five-three', 12, 5, 3, False, True),
         ('map-spill-first', 12, 8, 3, False, False), ('map-spill-later', 12, 8, 2, True, True)]
ENGINE = scalar.Scalar()


def payload(id, column, length=None):
    if length is None:
        if id == 10 or (id > 10 and (id + column) % 7 == 0):
            return None
        length = LENGTHS[(id - 1 + column) % 9] if id <= 9 else 1 + (id + 3 * column) % 17
    if column % 2 == 0:
        return ''.join(chr(65 + (offset + id + column) % 26) for offset in range(length))
    return bytes((offset * 17 + id * 3 + column) % 256 for offset in range(length)).hex()


def row(id, columns):
    return [id, id % 11 - 5 if id % 37 else None] + [payload(id, column) for column in range(columns)]


def recipe():
    result = []
    for name, count, long_columns, index_count, generated, later in CASES:
        fields = [['Id', 4, 4, generated], ['Tag', 4, 4, False]] + [[n, 12 if c % 2 == 0 else 11, 0, False] for c, n in enumerate(NAMES[:long_columns])]
        indexes = [dict(name='ById', primary=True, unique=True, required=True, ignore=False, fields=[[0, False]]),
                   dict(name='ByTag', primary=False, unique=False, required=False, ignore=False, fields=[[1, True]]),
                   dict(name='ByPair', primary=False, unique=True, required=False, ignore=False, fields=[[1, False], [0, True]])][:index_count]
        inserted = [count + 1 if generated else 9000, 99] + [payload(9000, c, [4096, 33, 32, 2037, 1, 512, 2048, 4096][c]) for c in range(long_columns)]
        replaced = row(3, long_columns)
        replaced[1] = -99
        for column, length in enumerate([32, 4096, None, 1]):
            replaced[2 + column] = None if length is None else payload(3, column, length)
        for index in indexes:
            if index['name'] == 'ById':
                index['queries'] = [[0], [1], [2], [3], [count], [inserted[0]], [999999]]
            elif index['name'] == 'ByTag':
                index['queries'] = [[-99], [-5], [0], [5], [99], [999999]]
            else:
                index['queries'] = [[-99, 3], [-2, 3], [-4, 1], [99, inserted[0]], [999999, 1]]
        result.append(dict(name=name, count=count, long_columns=long_columns, generated=generated, later=later,
                           fields=fields, indexes=indexes, initial_rows=[row(id, long_columns) for id in range(1, count + 1)],
                           native=[dict(kind='insert', row=inserted), dict(kind='replace', id=3, row=replaced), dict(kind='delete', id=2)]))
    return result


def cells(case, row, inserting=False):
    """jet3-cli cells for a recipe row; generated Ids are assigned when `inserting`."""
    return ['auto_increment' if case['generated'] and inserting else {'long': row[0]}] + [
        {'memo': value} if field[1] == 12 and value is not None else scalar.cell(value, field[1])
        for value, field in zip(row[1:], case['fields'][1:])]


def create(case, rows):
    """The jet3-cli creation step of Items (with `rows`) and Notes, in the case's table order."""
    columns = [dict(name=name, type='auto_increment' if generated else scalar.KINDS[kind]) for name, kind, _, generated in case['fields']]
    items = dict(name='Items', columns=columns, indexes=[scalar.index_request(case, index) for index in case['indexes']],
                 rows=[cells(case, row, True) for row in rows])
    return dict(command='create', request=dict(tables=[scalar.NOTES_TABLE, items] if case['later'] else [items, scalar.NOTES_TABLE]))


def candidates(spec=None):
    return [{'file': case['name'] + '.mdb', 'steps': [create(case, case['initial_rows'])]} for case in recipe()]


def reader_value(value, kind):
    if value is None or kind not in (2, 3, 4, 12):
        return value
    raw = bytes.fromhex(value)
    return raw.decode('cp1252') if kind == 12 else int.from_bytes(raw, 'little', signed=kind != 2)


def receipt(path: Path) -> dict:
    """Items and Notes as the Rust reader reports them: fields, rows in Id order with locators,
    payloads (Memo text, OLE hex) and long-value references, and index trees."""
    result = {}
    for name, table in common.reader(path)['tables'].items():
        kinds = [c['type'] for c in table['columns']]
        rows = [dict(values=[reader_value(v, k) for v, k in zip(r['values'], kinds)], page=r['page'], slot=r['slot'], references=r['long_values'])
                for r in table['rows']]
        result[name] = dict(fields=[[c['name'], c['type'], c['size'], c['auto_increment']] for c in table['columns']],
                            rows=sorted(rows, key=lambda r: r['values'][0]),
                            indexes=[dict(name=i['name'], entries=i['entries'], nodes=i['nodes'], depth=i['depth']) for i in table['indexes']])
    return result


def expected(case, native=False):
    rows = {r[0]: r.copy() for r in case['initial_rows']}
    counters = ENGINE.counters_for(case, rows)
    if native:
        for operation in case['native']:
            ENGINE.apply(rows, operation, case, counters)
    return rows, counters


def check_receipt(receipt, case, expected_rows):
    require(set(receipt) == {'Items', 'Notes'}, 'Rust table inventory')
    for name, fields, rows in [('Items', case['fields'], sorted(expected_rows.values())), ('Notes', NOTES_FIELDS, common.NOTES)]:
        require(receipt[name]['fields'] == fields, 'Rust schema: ' + name)
        require([r['values'] for r in receipt[name]['rows']] == rows, 'Rust complete payloads and rows: ' + name)
    require(receipt['Notes']['indexes'] == [], 'Unindexed Notes')


def raw_check(data, receipt, case, rows, counters, *, candidate):
    table = common.tables(data, ['Items'])['Items']
    require(table['row_count'] == len(rows), 'Raw row count')
    groups = table['long_value_maps']
    require(sorted(g['column'] for g in groups) == list(range(2, len(case['fields']))), 'One map group per long column')
    data_pages = table['data_pages']
    require(not table['long_value_pages'], 'Table owns only its data pages')
    locators = {(r['page'], r['slot']): r['values'] for r in receipt['Items']['rows']}
    require(len(locators) == len(rows), 'Distinct live row references')
    require(all(page in data_pages for page, _ in locators), 'Logical rows live on owned data pages')
    if candidate:
        decoded = common.table_rows(data, table)
        require({(r['page'], r['row']): r['values'][:2] for r in decoded} == {loc: values[:2] for loc, values in locators.items()},
                'Independent raw numeric values and row locators')
    logical = {i['name']: i['physical_index'] for i in table['logical_indexes']}
    require(set(logical) == {i['name'] for i in case['indexes']} and len(table['physical_indexes']) == len(logical), 'Raw index inventory')
    rust_indexes = {i['name']: i for i in receipt['Items']['indexes']}
    require(set(rust_indexes) == set(logical), 'Rust index inventory')
    all_owned = set(data_pages)
    indexes = {}
    for index in case['indexes']:
        physical = table['physical_indexes'][logical[index['name']]]
        require(physical['keys'] == [dict(column=c, direction=int(not desc)) for c, desc in index['fields']]
                and physical['flags'] == int(index['unique']) + 8 * int(index['required']), 'Raw numeric index shape')
        nodes, entries = structure.tree(data, physical['root'], table['root'], [(4, desc) for _, desc in index['fields']])
        wanted = sorted(ENGINE.key(values, case, index) + common.locator_bytes(page, slot) for (page, slot), values in locators.items())
        require(entries == wanted, 'All raw index keys and locators: ' + index['name'])
        rust = rust_indexes[index['name']]
        require([bytes.fromhex(k) + common.locator_bytes(p, r) for k, p, r in rust['entries']] == entries, 'Rust/raw complete tree records')
        owned = common.map_pages(data, physical['map'])
        reached = {node['page'] for node in nodes}
        require(reached <= owned and not owned & all_owned, 'Independent index/data pages')
        all_owned |= owned
        require(physical['entry_count'] == counters[index['name']], 'Retained distinct insertion counter')
        indexes[index['name']] = dict(depth=max(n['depth'] for n in nodes), pages=sorted(reached), owned=sorted(owned))
        require(rust['depth'] == indexes[index['name']]['depth'] and set(rust['nodes']) == reached, 'Rust/raw tree shape')
    maps = {}
    for group in groups:
        column = group['column']
        owned = common.map_pages(data, group['owned'])
        available = common.map_pages(data, group['available'])
        require(available <= owned and not owned & all_owned, 'Independent long-value page ownership')
        require(all(data[p * common.PAGE] == 1 and data[p * common.PAGE + 4:p * common.PAGE + 8] == b'LVAL' for p in owned), 'LVAL page kind/owner')
        all_owned |= owned
        if candidate:
            first = 2 + len(case['indexes']) + 2 * (column - 2)
            require(group['owned'] == dict(page=table['root'] + 1 + first // 15, row=first % 15)
                    and group['available'] == dict(page=table['root'] + 1 + (first + 1) // 15, row=(first + 1) % 15), 'Candidate map row placement')
            lengths = [len(r[column]) // (2 if column % 2 else 1) for r in rows.values() if r[column] is not None]
            count = sum(0 if n <= 32 else 1 if n <= 2036 else (n + 2031) // 2032 for n in lengths)
            require(len(owned) == count, 'Candidate external page count per column')
        maps[group['column_name']] = dict(owned=sorted(owned), available=sorted(available))
    if candidate and case['count'] > 200:
        require(len(data_pages) > 1 and all(i['depth'] > 1 for i in indexes.values()), 'Mixed data and three numeric leaf boundaries')
    if case['generated']:
        state = int.from_bytes(data[table['root'] * common.PAGE + 16:table['root'] * common.PAGE + 20], 'little', signed=True)
        require(state == max(rows), 'Persisted generated last ID')
    return dict(file_pages=len(data) // common.PAGE, data_pages=data_pages, indexes=indexes, long_value_maps=maps,
                payload_storage=storage_inventory(data, receipt, table, maps))


def storage_inventory(data, receipt, table, maps):
    """Traces EXP-0061 fragment locators and reports native/candidate storage reuse."""
    pages = {}
    for item in receipt['Items']['rows']:
        for reference in item['references']:
            where, seen, length = (reference['page'], reference['slot']), set(), 0
            column = table['columns'][reference['column']]['name']
            while where[0]:
                require(where not in seen, 'Repeated live payload fragment')
                seen.add(where)
                number, slot = where
                require(number in maps[column]['owned'], 'Referenced payload belongs to its column map')
                image = structure.page(data, number, 'LVAL fragment')
                entry = next((e for e in structure.directory(image, number) if e['row'] == slot), None)
                require(entry is not None, 'Payload fragment slot exists')
                raw = image[entry['start']:entry['end']]
                state = pages.setdefault(str(number), dict(column=column, kinds=set(), slots=set(), available=number in maps[column]['available']))
                require(state['column'] == column, 'Independent live payload column')
                require(slot not in state['slots'], 'Each live payload slot is referenced once')
                state['kinds'].add(reference['storage'])
                state['slots'].add(slot)
                if reference['storage'] == 'SinglePage':
                    length += len(raw)
                    break
                require(reference['storage'] == 'Chained' and len(raw) >= 4, 'Known payload storage')
                length += len(raw) - 4
                where = (int.from_bytes(raw[1:4], 'little'), raw[0])
            require(length == reference['length'], 'Complete external storage length')
    for column, mapping in maps.items():
        for number in mapping['owned']:
            image = structure.page(data, number, 'owned LVAL page')
            live, empty_siblings = set(), []
            for entry in structure.directory(image, number):
                slot = entry['row']
                word = int.from_bytes(image[10 + 2 * slot:12 + 2 * slot], 'little')
                if word & 0xe000:
                    require(word & 0xe000 == 0xc000 and entry['start'] == entry['end'], 'Only empty c000 LVAL siblings')
                    empty_siblings.append(dict(slot=slot, word=f'{word:04x}', offset=entry['start']))
                else:
                    require(entry['start'] < entry['end'], 'Nonempty active payload slot')
                    live.add(slot)
            state = pages.setdefault(str(number), dict(column=column, kinds=set(), slots=set(), available=number in mapping['available']))
            require(live == state['slots'], 'All active payload slots have live column references')
            state['empty_siblings'] = empty_siblings
    return {page: dict(value, kinds=sorted(value['kinds']), slots=sorted(value['slots'])) for page, value in pages.items()}


def normalized(capture, case, rows):
    require(capture['status'] == 'pass' and capture['error'] is None, 'DAO capture completed')
    value = copy.deepcopy(capture['snapshot'])
    require(value['version'] == '3.0' and value['tables'] == TABLES and value['queries'] == value['relations'] == [], 'Complete DAO inventory')
    require([t['name'] for t in value['user_tables']] == ['Items', 'Notes'], 'User table inventory')
    for actual, name, fields, expected_rows in zip(value['user_tables'], ['Items', 'Notes'], [case['fields'], NOTES_FIELDS],
                                                   [sorted(rows.values()), common.NOTES]):
        require(actual['name'] == name and actual['attributes'] == 0
                and [[f['name'], f['type'], f['size'], bool(f['attributes'] & 16)] for f in actual['fields']] == fields, 'Complete DAO field schema')
        require(sorted(actual['rows']) == expected_rows, 'Complete DAO payload bytes and nulls: ' + name)
        actual['rows'].sort()
        expected_indexes = [] if name == 'Notes' else [
            dict(name=i['name'], primary=i['primary'], unique=i['unique'], required=i['required'], ignore_nulls=i['ignore'], foreign=False,
                 fields=[dict(name=case['fields'][c][0], attributes=int(d)) for c, d in i['fields']]) for i in case['indexes']]
        actual['indexes'].sort(key=lambda i: i['name'])
        expected_indexes.sort(key=lambda i: i['name'])
        require(actual['indexes'] == expected_indexes, 'Complete DAO index metadata')
    require(set(value['index_reads']) == {i['name'] for i in case['indexes']}, 'DAO index read inventory')
    for index in case['indexes']:
        read = value['index_reads'][index['name']]
        traversal = read['traversal']
        require(sorted(traversal) == sorted(rows.values()), 'Full index traversal multiset')
        keys = [ENGINE.key(r, case, index) for r in traversal]
        require(keys == sorted(keys), 'Numeric traversal order')
        read['traversal'] = sorted(traversal, key=lambda r: (ENGINE.key(r, case, index), r[0]))
        require([s['query'] for s in read['seek']] == index['queries'], 'Seek inventory')
        for seek in read['seek']:
            matches = [r for r in rows.values() if [r[c] for c, _ in index['fields']] == seek['query']]
            require(seek['row'] in matches if matches else seek['row'] is None, 'Seek complete matching row or no-match')
            seek['row'] = sorted(matches)
    return value


def inspect(path: Path, case, rows):
    """Reads an image with the Rust reader and checks its complete receipt."""
    result = receipt(path)
    check_receipt(result, case, rows)
    return result


def prepare(images: Path, revision: str, spec: dict, results: dict) -> None:
    cases, files = recipe(), {}
    for case in cases:
        image = images / (case['name'] + '.mdb')
        rows, counters = expected(case)
        common.validate(image)
        case['layout'] = raw_check(image.read_bytes(), inspect(image, case, rows), case, rows, counters, candidate=True)
        case['notes_pages'] = common.notes_identity(image.read_bytes())
        files[image.name] = identity(image)
    common.write(images / MANIFEST, dict(document_type='multiple_long_value_creation_inputs', source_revision=revision, cases=cases,
                                         files=files, refusals=[]))


def evaluate(images: Path, outbox: Path) -> dict:
    manifest_path = images / MANIFEST
    manifest = common.read(manifest_path)
    report = dict(status='failed', source_revision=manifest['source_revision'], manifest=identity(manifest_path), cases=[], error=None)
    try:
        for name, pin in manifest['files'].items():
            require(identity(images / name) == pin, 'Candidate identity: ' + name)
        result = common.read(outbox / 'result.json')
        report['result'] = identity(outbox / 'result.json')
        common.check_result(result, manifest_path, 'dao_multiple_long_value_creation_result', manifest['source_revision'])
        report['environment'] = result['environment']
        require([c['name'] for c in result['cases']] == [c['name'] for c in manifest['cases']], 'Case inventory')
        for case, capture in zip(manifest['cases'], result['cases']):
            outcome = dict(name=case['name'], status='failed', checkpoints=[], error=None)
            report['cases'].append(outcome)
            try:
                compare_case(outbox, manifest, case, capture, outcome)
                outcome['status'] = 'accepted'
            except Exception as error:
                outcome['error'] = f'{type(error).__name__}: {error}'
        report['refusals'] = manifest['refusals']
        require(all(c['status'] == 'accepted' for c in report['cases']), 'One or more multi-column cases failed')
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    return report


def compare_case(outbox, manifest, case, capture, outcome, check=None):
    """Original and native checkpoints of one case; `check` replaces `raw_check`."""
    check = check or raw_check
    require(capture['status'] == 'pass' and capture['error'] is None, 'Case capture completed: ' + str(capture['error']))
    notes, originals = {}, {}
    for phase in ['original', 'native']:
        require(set(capture[phase]) == {'candidate', 'control'}, 'Paired role inventory')
        rows, counters = expected(case, phase == 'native')
        values, images, layouts = {}, {}, {}
        for role, observation in capture[phase].items():
            path = outbox / observation['file']
            images[role] = identity(path)
            require(observation['before'] == observation['after'] == images[role], 'Closed immutable capture identity')
            if phase == 'original':
                originals[role] = images[role]
                notes[role] = common.notes_identity(path.read_bytes())
                if role == 'candidate':
                    require(images[role] == manifest['files'][case['name'] + '.mdb'] and notes[role] == case['notes_pages'], 'Rust input image identity')
            else:
                mutation = capture['mutations'][role]
                require(mutation['before'] == originals[role] and mutation['after'] == images[role] and mutation['operations'] == case['native'],
                        'Native operation input/result chain')
                require(common.notes_identity(path.read_bytes()) == notes[role], 'Unrelated Notes definition/maps/data/LVAL pages preserved')
            values[role] = normalized(observation, case, rows)
            receipt = inspect(path, case, rows)
            layouts[role] = check(path.read_bytes(), receipt, case, rows, counters, candidate=phase == 'original' and role == 'candidate')
        require(values['candidate'] == values['control'], 'All paired DAO metadata and semantics')
        outcome['checkpoints'].append(dict(phase=phase, rows=len(rows), images=images, layout=layouts, notes_pages=notes))
