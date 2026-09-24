#!/usr/bin/env python3
"""Prepare and compare the finite EXP-0222/0241 creation-layout suite."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import dao_common

CATALOG = dao_common.load_catalog('_numeric_catalog', MAX_ROWS_PER_PAGE=256)

ROOT = Path(__file__).resolve().parents[3]
PRODUCER = ROOT / 'oracle/windows-dao/scripts/creation_tables.ps1'
SYSTEM_TABLES = ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(path):
    data = path.read_bytes()
    return dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def prepare(candidates, revision):
    specs = [
        dict(name='ZPrimary', column=0, primary=True, unique=True, descending=False),
        dict(name='ASecond', column=1, primary=False, unique=False, descending=True),
        dict(name='MUnique', column=2, primary=False, unique=True, descending=False),
    ]
    arms = []
    for line in (candidates / 'cases.tsv').read_text().splitlines():
        name, count, width, rows, name_width, schema_width = line.split('\t')
        count, width, rows, name_width, schema_width = map(int, (count, width, rows, name_width, schema_width))
        arm_indexes = [dict(index, name=index['name'].ljust(min(schema_width, 63), 'i')) for index in specs]
        tables = [dict(name=f'T{n:02}'.ljust(name_width, 'x'),
                       columns=[f'C{c:02}'.ljust(schema_width, 'c') for c in range(width)],
                       indexes=arm_indexes[:[3, 0, 1, 2, 3, 3][n % 6]] if rows else [],
                       rows=[[r, r % 3, r - 8] for r in range(rows)])
                  for n in range(count)]
        arms.append(dict(name=name, image=identity(candidates / (name + '.mdb')), tables=tables,
                         native=[dict(table=t['name'], row=[1000 + c for c in range(len(t['columns']))]) for t in (tables[0], tables[-1])]))
    require([(a['name'], len(a['tables'])) for a in arms] ==
            [('five-empty', 5), ('six-indexed', 6), ('catalog-short', 40), ('catalog-wide', 30), ('catalog-aces', 110), ('catalog-names', 40), ('counter-128', 128), ('counter-255', 255), ('counter-256', 256), ('names-boundary', 6)],
            'Candidate arm inventory and catalog capacities')
    require((candidates / 'refusals.tsv').read_text() ==
            'creation-counter\t32640\tTableCountOverflow\n', 'Capacity refusals')
    manifest = dict(document_type='creation_tables_inputs', source_revision=revision,
                    producer_sha256=identity(PRODUCER)['sha256'],
                    analyzer_sha256=identity(Path(__file__))['sha256'],
                    generator_sha256=identity(ROOT / 'crates/jet3/examples/creation_tables_candidate.rs')['sha256'],
                    refusals=identity(candidates / 'refusals.tsv'), arms=arms)
    for arm in arms:
        raw_layout((candidates / (arm['name'] + '.mdb')).read_bytes(), arm)
    write(candidates / 'creation-tables.json', manifest)
    shutil.copy2(PRODUCER, candidates / PRODUCER.name)


def normalized(snapshot, arm):
    result = copy.deepcopy(snapshot)
    require(result['version'] == '3.0' and result['relations'] == [] and result['queries'] == [],
            'Database version, relations and queries')
    require(result['inventory'] == sorted(SYSTEM_TABLES + [t['name'] for t in arm['tables']]), 'Complete table inventory')
    require([t['name'] for t in result['tables']] == [t['name'] for t in arm['tables']], 'Requested tables')
    for actual, expected in zip(result['tables'], arm['tables']):
        require(actual['attributes'] == 0, 'Table attributes')
        require([{k: c[k] for k in ('name', 'type', 'size', 'attributes')} for c in actual['columns']] ==
                [dict(name=n, type=4, size=4, attributes=1) for n in expected['columns']], 'Column schema')
        wanted_indexes = [dict(name=i['name'], primary=i['primary'], unique=i['unique'],
                              required=i['primary'], ignore_nulls=False, foreign=False,
                              fields=[dict(name=expected['columns'][i['column']], attributes=int(i['descending']))])
                          for i in expected['indexes']]
        actual['indexes'].sort(key=lambda i: i['name'])
        require(actual['indexes'] == sorted(wanted_indexes, key=lambda i: i['name']), 'Complete index schema')
        require(sorted(actual['rows']) == sorted(expected['rows']), 'Complete table rows')
        actual['rows'].sort()
        require(set(actual['traversals']) == set(actual['seeks']) == {i['name'] for i in expected['indexes']}, 'Index traversal/Seek inventory')
        for index in expected['indexes']:
            name, column = index['name'], index['column']
            traversal = actual['traversals'][name]
            require(sorted(traversal) == sorted(expected['rows']), 'Complete indexed rows')
            keys = [r[column] for r in traversal]
            require(keys == sorted(keys, reverse=index['descending']), 'Directed index order')
            require([s['query'] for s in actual['seeks'][name]] == [-1000, 0, 2, 16, 1000], 'Seek queries')
            for seek in actual['seeks'][name]:
                matches = [r for r in expected['rows'] if r[column] == seek['query']]
                require(seek['row'] in matches if matches else seek['row'] is None, 'Seek row')
                # DAO may choose any row for a duplicate key; complete traversal is checked above.
                seek['row'] = min(matches) if matches else None
            actual['traversals'][name] = sorted(traversal)
    return result


def long_key(value, descending):
    encoded = b'\x7f' + ((value & 0xffffffff) ^ 0x80000000).to_bytes(4, 'big')
    return bytes(b ^ 255 for b in encoded) if descending else encoded


def native_arm(arm):
    result = copy.deepcopy(arm)
    for operation in arm['native']:
        next(t for t in result['tables'] if t['name'] == operation['table'])['rows'].append(operation['row'])
    return result


def native_contents(data, arm):
    catalog = CATALOG
    from catalog_pages_native import inspect
    system = inspect(data)
    objects = system['MSysObjects']
    name_slot, id_slot = [catalog._ordinal(objects['definition'], n) for n in ('Name', 'Id')]
    roots = {r['values'][name_slot]: r['values'][id_slot] for r in objects['rows']}
    for spec in arm['tables']:
        table = catalog._definition(data, roots[spec['name']])
        require([c['name'] for c in table['columns']] == spec['columns'], 'Native full column names')
        pages, lval = catalog._table_pages(data, table)
        rows = catalog._table_rows(data, table, pages)
        require(not lval and table['row_count'] == len(rows) and sorted(r['values'] for r in rows) == sorted(spec['rows']), 'Native complete rows')
        logical = {i['name']: i['physical_index'] for i in table['logical_indexes']}
        require(set(logical) == {i['name'] for i in spec['indexes']}, 'Native full index inventory')
        owned = set(pages)
        for index in spec['indexes']:
            physical = table['physical_indexes'][logical[index['name']]]
            nodes, entries = dao_common.index_tree(catalog, data, physical['root'], table['root'])
            wanted = sorted(long_key(row['values'][index['column']], index['descending']) +
                            row['page'].to_bytes(3, 'big') + bytes([row['row']]) for row in rows)
            require(entries == wanted, 'Native complete index key/locator records')
            mapped = set(catalog._locator_pages(data, physical['map'], 'native index map'))
            require({n['page'] for n in nodes} <= mapped and not owned.intersection(mapped), 'Native distinct data/index ownership')
            owned.update(mapped)


def raw_layout(data, arm):
    catalog = CATALOG
    definition, _, records = catalog._discover_catalog(data)
    name_slot, id_slot = [catalog._ordinal(definition, n) for n in ('Name', 'Id')]
    roots = {r['values'][name_slot]: r['values'][id_slot] for r in records}
    require(len(records) == 8 + len(arm['tables']), 'Raw catalog count')
    require(int.from_bytes(data[1538:1540], 'little') == 0x0100 + 2 * len(arm['tables']), 'EXP-0249 creation counter word')
    next_root = 20
    observations = []
    for position, spec in enumerate(arm['tables']):
        require(roots[spec['name']] == next_root, 'Sequential definition root')
        table = catalog._definition(data, next_root)
        require([dict(name=c['name'], type=c['type'], size=c['size']) for c in table['columns']] ==
                [dict(name=n, type='Long', size=4) for n in spec['columns']], 'Raw columns')
        require(len(table['physical_indexes']) == len(table['logical_indexes']) == len(spec['indexes']), 'Raw index count')
        pages, long_values = catalog._table_pages(data, table)
        rows = catalog._table_rows(data, table, pages)
        require(not long_values and sorted(r['values'] for r in rows) == sorted(spec['rows']), 'Raw rows')
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
            nodes, entries = dao_common.index_tree(catalog, data, root, next_root)
            wanted = sorted(long_key(row['values'][index['column']], index['descending']) +
                            row['page'].to_bytes(3, 'big') + bytes([row['row']]) for row in rows)
            require(entries == wanted and len(nodes) == 1, 'Complete index key/locator entries')
            require(physical['entry_count'] == len({row['values'][index['column']] for row in rows}), 'Distinct keys')
            require(sorted(catalog._locator_pages(data, physical['map'], 'creation index map')) == [root], 'Independent index map')
        observations.append(dict(name=spec['name'], root=next_root, columns=len(spec['columns']), indexes=len(spec['indexes']), rows=len(rows)))
        next_root += 2 + int(position == 0) + len(spec['indexes']) + len(pages)
    from catalog_pages_native import inspect as inspect_catalog_pages
    system = inspect_catalog_pages(data)
    extra = {p for table in system.values() for p in table['owned'] if p >= next_root}
    extra.update(p for table in system.values() for index in table['indexes'] for p in index['owned'] if p >= next_root)
    require(extra == set(range(next_root, len(data) // 2048)), 'Complete appended catalog page inventory')
    require(len(data) % 2048 == 0, 'Whole pages')
    global_row = catalog._locator_row(data, dict(page=1, row=0), 'global free map')
    global_free = set(catalog._map_pages(global_row, 1024, 'global free map', bounded=True))
    require(global_free == set(range(len(data) // 2048, 1024)), 'Exact global free page inventory')
    return dict(pages=len(data) // 2048, tables=observations, catalogs=system)


def evaluate(candidates, outbox):
    manifest_path = candidates / 'creation-tables.json'
    manifest = json.loads(manifest_path.read_text())
    result = json.loads((outbox / 'result.json').read_text())
    report = dict(document_type='dao_creation_tables_report', status='failed', source_revision=manifest['source_revision'],
                  manifest=identity(manifest_path), result=identity(outbox / 'result.json'), observations=[], error=None)
    try:
        require(manifest['analyzer_sha256'] == identity(Path(__file__))['sha256'], 'Analyzer identity')
        require(result['document_type'] == 'dao_creation_tables_result' and result['manifest_sha256'] == identity(manifest_path)['sha256'] and
                result['source_revision'] == manifest['source_revision'], 'Result source and input identity')
        require(result['error'] is None and result['retention_failures'] == [], 'Producer completed and retained outputs')
        require(result['environment']['process_bits'] == 32 and result['environment']['provider'] == 'DAO.DBEngine.36', 'Provider environment')
        pairs = {(p['arm'], p['replica']): p for p in result['pairs']}
        require(len(pairs) == len(result['pairs']) and set(pairs) == {(a['name'], r) for a in manifest['arms'] for r in (1, 2)}, 'Complete pair inventory')
        for arm in manifest['arms']:
            for replica in (1, 2):
                pair = pairs[arm['name'], replica]
                require(set(pair['captures']) == {'candidate', 'control'}, 'Role inventory')
                snapshots = {}; images = {}; counters = {}
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
                expected_native = native_arm(arm); native_snapshots = {}; native_images = {}
                require(set(pair['native']) == {'candidate', 'control'}, 'Native successor roles')
                for role in ('candidate', 'control'):
                    capture = pair['native'][role]
                    path = outbox / f"{arm['name']}-r{replica}-native-{role}.mdb"
                    require(capture['file'] == path.name and capture['status'] == 'pass' and capture['error'] is None, 'Native successor capture')
                    native_images[role] = identity(path)
                    require(capture['before'] == capture['after'] == native_images[role], 'Immutable native successor capture')
                    native_snapshots[role] = normalized(capture['snapshot'], expected_native)
                    native_contents(path.read_bytes(), expected_native)
                require(native_snapshots['candidate'] == native_snapshots['control'], 'Native successor complete semantics')
                report['observations'].append(dict(arm=arm['name'], replica=replica, images=images, counters=counters, layout=raw, native=native_images))
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        path = outbox / 'creation-tables-report.json'
        suffix = 1
        while path.exists():
            if json.loads(path.read_text()) == report:
                break
            suffix += 1
            path = outbox / f'creation-tables-report-{suffix}.json'
        else:
            write(path, report)
        print(path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('candidates', type=Path); p.add_argument('revision')
    p = sub.add_parser('evaluate'); p.add_argument('candidates', type=Path); p.add_argument('outbox', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        revision = subprocess.check_output(['git', 'rev-parse', args.revision], cwd=ROOT, text=True).strip()
        prepare(args.candidates, revision)
    else:
        evaluate(args.candidates, args.outbox)


if __name__ == '__main__':
    main()
