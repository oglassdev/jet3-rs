"""Complete schema, key, storage and submitted-input checks for graph creation."""
import hashlib
import json
import zipfile

import allocation_lifecycle_structure as allocation
import catalog_pages_native as catalog_indexes
import multiple_relationship_storage as payload_storage
import system_catalog as catalog
import text_index_collation as text_keys

PROVIDER = {
    'provider': 'DAO.DBEngine.36', 'version': '3.6', 'bits': 32,
    'os': 'Microsoft Windows NT 10.0.20348.0', 'culture': 'en-US',
    'dll_version': '03.60.9765.0',
    'dll_sha256': '4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac',
}
SYSTEM_INDEXES = {
    'MSysObjects': [(0, 1, [(1, 'ParentId', 'Long', 1), (2, 'Name', 'Text', 1)]),
                    (1, 1, [(0, 'Id', 'Long', 1)])],
    'MSysACEs': [(0, 8, [(0, 'ObjectId', 'Long', 1)])],
    'MSysQueries': [(0, 1, [(0, 'ObjectId', 'Long', 1), (1, 'Attribute', 'Byte', 1),
                           (2, 'Order', 'Binary', 1)])],
    'MSysRelationships': [(0, 2, [(0, 'szRelationship', 'Text', 1)]),
                          (1, 2, [(4, 'szObject', 'Text', 1)]),
                          (2, 2, [(6, 'szReferencedObject', 'Text', 1)])],
}


def require(ok, detail):
    if not ok:
        raise ValueError(detail)


def identity(data):
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def run_inputs(run, matrix, producer, plan, progress_logs=False):
    require((run / 'inbox/script.ps1').read_bytes() == producer.read_bytes(), 'executed producer')
    require((run / 'outbox/exit.txt').read_text().strip() == '0', 'native run exit')
    require(not (run / 'outbox/log.txt').read_text().strip(), 'native run log')
    stems = {f"{g['name']}-r{rep}" for g in plan['graphs'] for rep in range(1, plan['replicas'] + 1)}
    require(len(stems) == len(plan['graphs']) * plan['replicas'], 'unique matrix case inventory')
    with zipfile.ZipFile(run / 'inbox/inputs.zip') as bundle:
        names = [n for n in bundle.namelist() if not n.endswith('/')]
        require(len(names) == len(set(names)), 'unique ZIP members')
        require(set(names) == {'matrix.json'} | {f'candidates/{stem}.mdb' for stem in stems}, 'submitted ZIP inventory')
        require(bundle.read('matrix.json') == matrix.read_bytes(), 'submitted matrix bytes')
        inputs = {stem: identity(bundle.read(f'candidates/{stem}.mdb')) for stem in stems}
    expected = {'exit.txt', 'log.txt'} | {f'{stem}.json' for stem in stems}
    expected |= {f'{stem}-{role}.mdb' for stem in stems for role in ('candidate', 'control')}
    if progress_logs:
        expected |= {'progress-master.txt'} | {f'progress-{stem}.txt' for stem in stems}
    require({p.name for p in (run / 'outbox').iterdir()} == expected, 'complete native output inventory')
    return inputs


def flat(items):
    while len(items) == 1 and isinstance(items[0], list):
        items = items[0]
    return items


def lean(value):
    if isinstance(value, dict):
        return {k: lean(v) for k, v in value.items() if k != 'properties'}
    if isinstance(value, list):
        return [lean(v) for v in value]
    return value


def schema(snapshot, case, label):
    require(snapshot['version'] == '3.0', label + ' version')
    expected = [dict(name=r['name'], table=r['table'], foreign_table=r['foreign_table'], attributes=0,
                     fields=[dict(name=r['field'], foreign_name=r['foreign_field'])]) for r in case['relations']]
    require(sorted(lean(snapshot['relations']), key=lambda x: x['name']) == sorted(expected, key=lambda x: x['name']), label + ' relationship schema')
    require([t['name'] for t in snapshot['user_tables']] == [t['name'] for t in case['tables']], label + ' ordered table inventory')
    for table, spec in zip(snapshot['user_tables'], case['tables']):
        require(table['attributes'] == 0, label + ' table attributes')
        fields = [dict(name=f['name'], type=f['type'], size=f['size'], ordinal=n,
                       attributes=(1 if f['type'] == 4 else 2) | f.get('attributes', 0), required=False,
                       allow_zero_length=f.get('allow_zero_length', f['type'] in (10, 12))) for n, f in enumerate(spec['fields'])]
        require(flat(lean(table['fields'])) == fields, label + ' field schema/' + spec['name'])
        specs = [('By' + spec['name'], 'Id', True)]
        specs += [(r['name'], r['foreign_field'], False) for r in case['relations'] if r['foreign_table'] == spec['name']]
        indexes = [dict(name=n, primary=primary, unique=primary, required=primary, foreign=not primary,
                        ignore_nulls=False, fields=[dict(name=column, attributes=0)]) for n, column, primary in specs]
        require(sorted(flat(lean(table['indexes'])), key=lambda x: x['name']) == sorted(indexes, key=lambda x: x['name']), label + ' index schema/' + spec['name'])
        require(set(table['index_reads']) == {n for n, _, _ in specs}, label + ' index reads inventory')


def index_reads(snapshot, label):
    for table in snapshot['user_tables']:
        rows = {r['Id']: r for r in table['rows']}
        require(len(rows) == len(table['rows']), label + ' unique row IDs')
        for name, index in table['index_reads'].items():
            require(len(index['traversal']) == len(rows), label + ' complete traversal/' + name)
            require({r['Id'] for r in index['traversal']} == set(rows), label + ' traversal row inventory/' + name)
            for row in index['traversal']:
                require(row == rows[row['Id']], label + ' traversal values/' + name)
            wanted_queries = sorted({r[index['field']] for r in rows.values() if r[index['field']] is not None}) + [999999]
            require([s['query'] for s in index['seek']] == wanted_queries, label + ' query inventory/' + name)
            for seek in index['seek']:
                matches = [r for r in rows.values() if r[index['field']] == seek['query']]
                require((seek['row'] is None) == (not matches), label + ' Seek presence/' + name)
                if matches:
                    require(seek['row'] in matches, label + ' Seek values/' + name)


def prefixes(observation, model, role, label):
    for name, indexes in observation['physical_indexes'].items():
        for index in indexes:
            distinct = len({r[index['column']] for r in model[name]})
            first = len(model[name]) if role == 'control' and index['ordinal'] != 0 else 0
            require((index['first_word'], index['second_word']) == (first, distinct), label + '/' + role + ' creation index prefixes/' + name)
    expected = []
    for pair in observation['pairs']:
        parent = pair['spec']['table']
        child = pair['spec']['foreign_table']
        primary = observation['physical_indexes'][parent][pair['parent']['physical_index']]
        require(primary['column'] == pair['spec']['field'] and primary['flags'] == 9,
                label + ' referenced primary index')
        expected.extend([(parent, pair['parent']['raw_hex']), (child, pair['child']['raw_hex'])])
    actual = [(table, record['raw_hex']) for table, records in observation['logical_relationships'].items() for record in records]
    require(len(expected) == len(set(expected)) and sorted(actual) == sorted(expected), label + ' complete reciprocal inventory')


def properties(decoded, fields, label):
    require(set(decoded['dictionary']) == {'Required', 'AllowZeroLength'}, label + ' property dictionary inventory')
    expected = {f['name']: dict(Required=False, **({'AllowZeroLength': f.get('allow_zero_length', True)} if f['type'] in (10, 12) else {})) for f in fields if f.get('attributes') != 16}
    require(len(decoded['blocks']) == len(expected), label + ' property block count')
    require({b['field']: b['properties'] for b in decoded['blocks']} == expected, label + ' named property inventory')


def system_key(value):
    if isinstance(value, str):
        return text_keys.component(value.encode('cp1252').hex(), False)
    require(isinstance(value, int), 'system key type')
    return catalog_indexes.long_key(value)


def record_width(record, fields):
    offset = 0
    for kind in fields:
        require(record[offset] == 127, 'present system key component')
        offset = record.index(0, offset + 1) + 1 if kind == 'Text' else offset + 5
    return offset + 4


def storage(path, analysis, observation):
    data = path.read_bytes()
    payload_count = payload_storage.check(path)
    named = {t['name']: t for t in analysis['tables'].values()}
    for name, indexes in observation['physical_indexes'].items():
        table = named[name]['definition']
        for index in indexes:
            owned = set(allocation.map_record(data, table['physical_indexes'][index['ordinal']]['map'], name)[1])
            require(set(index['nodes']) <= owned, 'user index node ownership/' + name)
    catalog_indexes.index.record_width = record_width
    system = {}
    require(set(analysis['system_rows']) == set(SYSTEM_INDEXES), 'complete system row inventory')
    for name, expected_schema in SYSTEM_INDEXES.items():
        definition = named[name]['definition']
        rows = analysis['system_rows'][name]['rows']
        schema = [(index['index'], index['flags'],
                   [(field['column'], definition['columns'][field['column']]['name'],
                     definition['columns'][field['column']]['type'], field['direction'])
                    for field in index['keys']]) for index in definition['physical_indexes']]
        require(schema == expected_schema, 'complete system index schema/' + name)
        if name == 'MSysQueries':
            require(not rows, 'new database has no stored queries')
        system[name] = []
        for index in definition['physical_indexes']:
            columns = [f['column'] for f in index['keys']]
            require(all(f['direction'] == 1 for f in index['keys']), 'system key direction')
            kinds = [definition['columns'][n]['type'] for n in columns]
            nodes, entries = catalog_indexes.index.tree(data, index['root'], definition['root'], kinds)
            expected = sorted(b''.join(system_key(row['values'][n]) for n in columns) + row['page'].to_bytes(3, 'big') + bytes([row['row']]) for row in rows)
            require(entries == expected, 'complete system keys and locators/' + name)
            require(int.from_bytes(bytes.fromhex(index['prefix_hex']), 'little') == 0
                    and index['entry_count'] == len({entry[:-4] for entry in entries}),
                    'creation system index prefixes/' + name)
            owned = set(allocation.map_record(data, index['map'], name)[1])
            require({n['page'] for n in nodes} <= owned, 'system index node ownership/' + name)
            system[name].append({'ordinal': index['index'], 'first_word': int.from_bytes(bytes.fromhex(index['prefix_hex']), 'little'), 'second_word': index['entry_count'], 'entries': [e.hex() for e in entries]})
    return {'payload_descriptors': payload_count, 'system_indexes': system}
