"""Creation layouts (EXP-0222/0241/0244/0249/0251): up to 256 created Long tables with zero to
three indexes. Candidates must match DAO controls semantically and follow the exact sequential
page layout; the system catalog, its indexes and the global free map are decoded completely,
and native inserts into both outputs must keep complete rows and indexes.
"""

from __future__ import annotations

import copy
from pathlib import Path

import structure
from registry import common
from registry.common import identity, require

SCRIPT = common.REGISTRY / 'creation_tables.ps1'
MANIFEST = 'creation-tables.json'
INDEXES = [dict(name='ZPrimary', column=0, primary=True, unique=True, descending=False),
           dict(name='ASecond', column=1, primary=False, unique=False, descending=True),
           dict(name='MUnique', column=2, primary=False, unique=True, descending=False)]
# name, tables, columns per table, rows per table, table name width, column/index name width
CASES = [('five-empty', 5, 1, 0, 3, 0), ('six-indexed', 6, 3, 17, 3, 0), ('catalog-short', 40, 1, 0, 3, 0),
         ('catalog-wide', 30, 32, 0, 48, 0), ('catalog-aces', 110, 1, 0, 3, 0), ('catalog-names', 40, 3, 0, 48, 0),
         ('counter-128', 128, 1, 0, 3, 0), ('counter-255', 255, 1, 0, 3, 0), ('counter-256', 256, 1, 0, 3, 0),
         ('names-boundary', 6, 3, 17, 64, 64)]
# One table past the 32639-table catalog counter.
REFUSAL = ('refused', 32640, 1, 0, 3, 0)
SEEKS = [-1000, 0, 2, 16, 1000]
# General-sort primary weights of catalog names: letters and digits only, no secondary nibbles.
LETTERS = bytes.fromhex('60 61 62 64 66 67 68 69 6a 6b 6c 6d 6f 70 72 73 74 75 76 77 78 7a 7b 7c 7d 7e')


def name_key(parent: int, name: str) -> bytes:
    def weight(c):
        if 'a' <= c <= 'z':
            return LETTERS[ord(c) - 97]
        if '0' <= c <= '9':
            return 0x56 + ord(c) - 48
        raise ValueError('Name outside finite letter/digit inventory')
    return common.long_key(parent) + b'\x7f' + bytes(weight(c) for c in name.lower()) + b'\x00'


def expand(name, count, width, rows, name_width, schema_width) -> dict:
    indexes = [dict(index, name=index['name'].ljust(min(schema_width, 63), 'i')) for index in INDEXES]
    tables = [dict(name=f'T{n:02}'.ljust(name_width, 'x'),
                   columns=[f'C{c:02}'.ljust(schema_width, 'c') for c in range(width)],
                   indexes=indexes[:[3, 0, 1, 2, 3, 3][n % 6]] if rows else [],
                   rows=[[r, r % 3, r - 8] for r in range(rows)])
              for n in range(count)]
    return dict(name=name, tables=tables,
                native=[dict(table=t['name'], row=[1000 + c for c in range(len(t['columns']))]) for t in (tables[0], tables[-1])])


def request(arm: dict) -> dict:
    def index(table, i):
        field = dict(column=table['columns'][i['column']], direction='descending' if i['descending'] else 'ascending')
        return dict(name=i['name'], kind='primary' if i['primary'] else 'unique' if i['unique'] else 'ordinary', fields=[field])
    return dict(tables=[dict(name=t['name'], columns=[dict(name=c, type='long') for c in t['columns']],
                             indexes=[index(t, i) for i in t['indexes']], rows=[[{'long': v} for v in r] for r in t['rows']])
                        for t in arm['tables']])


def candidates(spec: dict) -> list[dict]:
    """One `<case>.mdb` per case, and a refused creation past the table counter."""
    images = [{'file': f'{case[0]}.mdb', 'steps': [dict(command='create', request=request(expand(*case)))]} for case in CASES]
    refused = dict(command='create', request=request(expand(*REFUSAL)), refused='TableCountOverflow { count: 32640, maximum: 32639 }')
    return images + [{'file': 'refused.mdb', 'steps': [refused]}]


def normalized(snapshot, arm):
    result = copy.deepcopy(snapshot)
    require(result['version'] == '3.0' and result['relations'] == [] and result['queries'] == [], 'Database version, relations and queries')
    require(result['inventory'] == sorted(common.SYSTEM_TABLES + [t['name'] for t in arm['tables']]), 'Complete table inventory')
    require([t['name'] for t in result['tables']] == [t['name'] for t in arm['tables']], 'Requested tables')
    for actual, expected in zip(result['tables'], arm['tables']):
        require(actual['attributes'] == 0, 'Table attributes')
        require([{k: c[k] for k in ('name', 'type', 'size', 'attributes')} for c in actual['columns']] ==
                [dict(name=n, type=4, size=4, attributes=1) for n in expected['columns']], 'Column schema')
        wanted = [dict(name=i['name'], primary=i['primary'], unique=i['unique'], required=i['primary'], ignore_nulls=False, foreign=False,
                       fields=[dict(name=expected['columns'][i['column']], attributes=int(i['descending']))])
                  for i in expected['indexes']]
        actual['indexes'].sort(key=lambda i: i['name'])
        require(actual['indexes'] == sorted(wanted, key=lambda i: i['name']), 'Complete index schema')
        require(sorted(actual['rows']) == sorted(expected['rows']), 'Complete table rows')
        actual['rows'].sort()
        require(set(actual['traversals']) == set(actual['seeks']) == {i['name'] for i in expected['indexes']}, 'Index traversal/Seek inventory')
        for index in expected['indexes']:
            name, column = index['name'], index['column']
            traversal = actual['traversals'][name]
            require(sorted(traversal) == sorted(expected['rows']), 'Complete indexed rows')
            keys = [r[column] for r in traversal]
            require(keys == sorted(keys, reverse=index['descending']), 'Directed index order')
            require([s['query'] for s in actual['seeks'][name]] == SEEKS, 'Seek queries')
            for seek in actual['seeks'][name]:
                matches = [r for r in expected['rows'] if r[column] == seek['query']]
                require(seek['row'] in matches if matches else seek['row'] is None, 'Seek row')
                # DAO may choose any row for a duplicate key; complete traversal is checked above.
                seek['row'] = min(matches) if matches else None
            actual['traversals'][name] = sorted(traversal)
    return result


def native_arm(arm):
    result = copy.deepcopy(arm)
    for operation in arm['native']:
        next(t for t in result['tables'] if t['name'] == operation['table'])['rows'].append(operation['row'])
    return result


def system_catalog(data: bytes) -> dict:
    """MSysObjects (root 2) and MSysACEs (root 3): rows through overflow links and payloads,
    every index against keys rebuilt from the rows, and page ownership (EXP-0241)."""
    found = structure.tables(data)
    output = {}
    for root, name in [(2, 'MSysObjects'), (3, 'MSysACEs')]:
        table = found[name]
        definition = table['definition']
        require(table['root'] == root and not table['long_value_pages'], 'System table owns only data pages')
        pages = table['data_pages']
        available = sorted(common.map_pages(data, definition['maps']['available']))
        require(set(available) <= set(pages), 'Available subset of owned data')
        rows = structure.rows(data, table)
        require(len(rows) == definition['row_count'], 'Declared system row count')
        columns = [c['name'] for c in definition['columns']]
        item = dict(rows=rows, owned=pages, available=available, indexes=[])
        for physical in definition['physical_indexes']:
            name_shape = root == 2 and len(physical['keys']) == 2
            fields = [(4, False), (10, False)] if name_shape else [(4, False)]
            nodes, entries = structure.tree(data, physical['root'], root, fields)
            if name_shape:
                keys = [name_key(r['values'][columns[1]], r['values'][columns[2]]) for r in rows]
            else:
                keys = [common.long_key(r['values'][columns[0]]) for r in rows]
            wanted = sorted(k + common.locator_bytes(r['locator']['page'], r['locator']['row']) for k, r in zip(keys, rows))
            require(entries == wanted, 'Complete system index key/locator multiset')
            owned = sorted(common.map_pages(data, physical['map']))
            require({n['page'] for n in nodes} <= set(owned), 'All index nodes owned')
            distinct = len({e[:-4] for e in entries})
            require(physical['entry_count'] == distinct, 'Initial distinct-key counter')
            item['indexes'].append(dict(root=physical['root'], owned=owned, nodes=nodes, distinct=distinct))
        output[name] = item
    return output


def index_entries(data, rows, index, owner, root):
    nodes, entries = common.long_tree(data, root, owner, index['descending'])
    wanted = sorted(common.long_key(row['values'][index['column']], index['descending']) + common.locator_bytes(row['page'], row['row'])
                    for row in rows)
    return nodes, entries, wanted


def raw_layout(data: bytes, arm: dict) -> dict:
    """The exact sequential creation layout of a candidate (EXP-0222/0244/0249/0251)."""
    require(len(data) % common.PAGE == 0, 'Whole pages')
    system = system_catalog(data)
    objects = system['MSysObjects']['rows']
    require(len(objects) == 8 + len(arm['tables']), 'Raw catalog count')
    roots = {r['values']['Name']: r['values']['Id'] for r in objects}
    require(int.from_bytes(data[1538:1540], 'little') == 0x0100 + 2 * len(arm['tables']), 'EXP-0249 creation counter word')
    definitions = common.tables(data, [t['name'] for t in arm['tables']])
    next_root = 20
    observations = []
    for position, spec in enumerate(arm['tables']):
        require(roots[spec['name']] == next_root, 'Sequential definition root')
        table = definitions[spec['name']]
        require(table['root'] == next_root, 'Sequential definition root')
        require([dict(name=c['name'], type=c['type'], size=c['size']) for c in table['columns']] ==
                [dict(name=n, type='Long', size=4) for n in spec['columns']], 'Raw columns')
        require(len(table['physical_indexes']) == len(table['logical_indexes']) == len(spec['indexes']), 'Raw index count')
        pages = table['data_pages']
        rows = common.table_rows(data, table)
        require(not table['long_value_pages'] and sorted(r['values'] for r in rows) == sorted(spec['rows']), 'Raw rows')
        require(table['row_count'] == len(rows), 'Stored row count')
        require(len(pages) == int(bool(rows)), 'Finite single data-page layout')
        expected_logical = sorted((i['name'], n, int(i['primary'])) for n, i in enumerate(spec['indexes']))
        require([(i['name'], i['physical_index'], i['class']) for i in table['logical_indexes']] == expected_logical,
                'Logical name order and physical binding')
        for ordinal, (index, physical) in enumerate(zip(spec['indexes'], table['physical_indexes'])):
            root = next_root + 2 + int(position == 0) + ordinal
            require(physical['root'] == root and physical['map'] == dict(page=next_root + 1, row=2 + ordinal), 'Index placement')
            require(physical['flags'] == int(index['unique']) + 8 * int(index['primary']) and
                    physical['keys'] == [dict(column=index['column'], direction=int(not index['descending']))], 'Raw index definition')
            nodes, entries, wanted = index_entries(data, rows, index, next_root, root)
            require(entries == wanted and len(nodes) == 1, 'Complete index key/locator entries')
            require(physical['entry_count'] == len({row['values'][index['column']] for row in rows}), 'Distinct keys')
            require(sorted(common.map_pages(data, physical['map'])) == [root], 'Independent index map')
        observations.append(dict(name=spec['name'], root=next_root, columns=len(spec['columns']), indexes=len(spec['indexes']), rows=len(rows)))
        next_root += 2 + int(position == 0) + len(spec['indexes']) + len(pages)
    pages = len(data) // common.PAGE
    extra = {p for table in system.values() for p in table['owned'] if p >= next_root}
    extra.update(p for table in system.values() for index in table['indexes'] for p in index['owned'] if p >= next_root)
    require(extra == set(range(next_root, pages)), 'Complete appended catalog page inventory')
    record, members = structure.map_record(data, dict(page=1, row=0), 'global')
    require(not members and record['outside_eof'] == [[pages, 1024]], 'Exact global free page inventory')
    catalogs = {name: dict(owned=t['owned'], available=t['available'], rows=len(t['rows']),
                           indexes=[dict(root=i['root'], owned=i['owned'], nodes=len(i['nodes']), distinct=i['distinct']) for i in t['indexes']])
                for name, t in system.items()}
    return dict(pages=pages, tables=observations, catalogs=catalogs)


def native_contents(data: bytes, arm: dict) -> None:
    """Complete rows, indexes and ownership of the tables after native DAO inserts."""
    system_catalog(data)
    definitions = common.tables(data, [t['name'] for t in arm['tables']])
    for spec in arm['tables']:
        table = definitions[spec['name']]
        require([c['name'] for c in table['columns']] == spec['columns'], 'Native full column names')
        rows = common.table_rows(data, table)
        require(not table['long_value_pages'] and table['row_count'] == len(rows)
                and sorted(r['values'] for r in rows) == sorted(spec['rows']), 'Native complete rows')
        logical = {i['name']: i['physical_index'] for i in table['logical_indexes']}
        require(set(logical) == {i['name'] for i in spec['indexes']}, 'Native full index inventory')
        owned = set(table['data_pages'])
        for index in spec['indexes']:
            physical = table['physical_indexes'][logical[index['name']]]
            nodes, entries, wanted = index_entries(data, rows, index, table['root'], physical['root'])
            require(entries == wanted, 'Native complete index key/locator records')
            mapped = common.map_pages(data, physical['map'])
            require({n['page'] for n in nodes} <= mapped and not owned & mapped, 'Native distinct data/index ownership')
            owned |= mapped


def prepare(images: Path, revision: str, spec: dict, results: dict) -> None:
    arms = [dict(expand(*case), image=identity(images / f'{case[0]}.mdb')) for case in CASES]
    refusal, = results['refused.mdb']
    require(not (images / 'refused.mdb').exists(), 'Capacity refusal publishes nothing')
    for case in arms:
        raw_layout((images / (case['name'] + '.mdb')).read_bytes(), case)
    common.write(images / MANIFEST, dict(document_type='creation_tables_inputs', source_revision=revision,
                                         refusals=[dict(name='creation-counter', tables=REFUSAL[1], error=refusal['refused'])], arms=arms,
                                         files={a['name'] + '.mdb': a['image'] for a in arms}))


def evaluate(images: Path, outbox: Path) -> dict:
    manifest_path = images / MANIFEST
    manifest = common.read(manifest_path)
    report = dict(status='failed', source_revision=manifest['source_revision'], manifest=identity(manifest_path), observations=[], error=None)
    try:
        result = common.read(outbox / 'result.json')
        report['result'] = identity(outbox / 'result.json')
        common.check_result(result, manifest_path, 'dao_creation_tables_result', manifest['source_revision'])
        pairs = {(p['arm'], p['replica']): p for p in result['pairs']}
        require(len(pairs) == len(result['pairs']) and set(pairs) == {(a['name'], r) for a in manifest['arms'] for r in (1, 2)}, 'Complete pair inventory')
        for arm in manifest['arms']:
            for replica in (1, 2):
                report['observations'].append(compare_pair(outbox, arm, replica, pairs[arm['name'], replica]))
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    return report


def compare_pair(outbox, arm, replica, pair):
    require(set(pair['captures']) == {'candidate', 'control'}, 'Role inventory')
    snapshots, images, counters = {}, {}, {}
    for role in ('candidate', 'control'):
        path = outbox / f"{arm['name']}-r{replica}-{role}.mdb"
        capture = pair['captures'][role]
        require(capture['file'] == path.name and capture['status'] == 'pass' and capture['error'] is None, 'DAO capture complete')
        images[role] = identity(path)
        require(capture['before'] == capture['after'] == images[role], 'DAO read retained identical bytes')
        snapshots[role] = normalized(capture['snapshot'], arm)
        counters[role] = int.from_bytes(path.read_bytes()[1538:1540], 'little')
    require(snapshots['candidate'] == snapshots['control'], 'Complete DAO semantic comparison')
    require(images['candidate'] == arm['image'], 'Candidate unchanged from generated input')
    raw = raw_layout((outbox / pair['captures']['candidate']['file']).read_bytes(), arm)
    expected = native_arm(arm)
    native_snapshots, native_images = {}, {}
    require(set(pair['native']) == {'candidate', 'control'}, 'Native successor roles')
    for role in ('candidate', 'control'):
        capture = pair['native'][role]
        path = outbox / f"{arm['name']}-r{replica}-native-{role}.mdb"
        require(capture['file'] == path.name and capture['status'] == 'pass' and capture['error'] is None, 'Native successor capture')
        native_images[role] = identity(path)
        require(capture['before'] == capture['after'] == native_images[role], 'Immutable native successor capture')
        native_snapshots[role] = normalized(capture['snapshot'], expected)
        native_contents(path.read_bytes(), expected)
    require(native_snapshots['candidate'] == native_snapshots['control'], 'Native successor complete semantics')
    return dict(arm=arm['name'], replica=replica, images=images, counters=counters, layout=raw, native=native_images)
