#!/usr/bin/env python3
"""Finite 4/13/14/32-index and ten-component lifecycle comparisons."""
import argparse
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import uuid

from index_tree_mutation import identity, notes_identity, tables, require
import numeric_index_mutation_structure as raw_index
import scalar_index_mutation_rows as raw_rows

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_suffix('.ps1')
MANIFEST = 'index-capacity.json'
NOTES = [[7, 'n' * 4096], [8, None]]
CONFIG = {'indexes4': (4, 3), 'indexes13': (13, 9), 'indexes14': (14, 10), 'indexes32': (32, 10), 'mixed10': (4, 10)}
spec = importlib.util.spec_from_file_location('index_capacity_scalar_engine', Path(__file__).with_name('numeric_index_mutation.py'))
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
key = engine.key
original_inputs = engine.inputs


def initial_row(name, id):
    if name == 'mixed10':
        alphabet = b'aA\xe9\xc9\xc6\xe6\xdf\x8a\x9a'
        text = bytes(alphabet[(n * 7 + id) % len(alphabet)] for n in range(240 + id % 16))
        guid = bytes((n * 37 + id) % 256 for n in range(16))
        return [id, id * 2 + 1 if id % 11 else None, id % 251, id % 30000 - 15000, id * 10001,
                id % 1024 + 0.5, id + 0.25, 36526 + id % 1000 / 4, id % 2 == 0,
                guid.hex() if id % 13 else None, text.hex()]
    width = CONFIG[name][1]
    return [id] + [None if id == 0 or (c == 1 and id % 11 == 0) or (c == width and id % 13 == 0)
                   else id * (c + 1) + c for c in range(1, 11)]


def recipe(name):
    count, width = CONFIG[name]
    types = [4, 4, 2, 3, 5, 6, 7, 8, 1, 15, 10] if name == 'mixed10' else [4] * 11
    fields = [[n, t, engine.SIZES[t]] for n, t in zip(['Id'] + list('ABCDEFGHIJ'), types)]
    indexes = []
    for i in range(count):
        indexes.append(dict(name='ById' if i == 0 else f'K{i:02}', primary=i == 0, unique=i in (0, count - 1), required=i == 0,
                            ignore=i != 0 and (i == count - 1 or i % 2 == 0),
                            fields=[[0, False]] if i == 0 else [[1 + (c + i - 1) % width, bool((i >> (c % 5)) & 1)] for c in range(width)]))
    insert = lambda id: dict(kind='insert', row=initial_row(name, id))
    field = lambda id, c, value: dict(kind='field', id=id, column=c, value=value)
    replacement = initial_row(name, 42); replacement[0] = 3
    result = dict(name=name, fields=fields, indexes=indexes, initial_rows=[initial_row(name, id) for id in range(24)],
                  stages=[dict(name='original', operations=[]),
                          dict(name='edited', operations=[insert(40), field(1, 1, None), dict(kind='replace', id=3, row=replacement), field(7, 0, 99), dict(kind='delete', id=0)]),
                          dict(name='regrown', operations=[dict(kind='delete', id=2), insert(1000), insert(1001)])],
                  native=[insert(9000), field(9000, 0, 9001), dict(kind='delete', id=1000)])
    for index in indexes:
        samples = [initial_row(name, id) for id in (8, 1000, 9000, 1234567, 12345)]
        queries = [[row[c] for c, _ in index['fields']] for row in samples]
        index['queries'] = list({json.dumps(q): q for q in queries if all(v is not None for v in q)}.values())
    return result


def raw_check(data, case, expected, counters, receipt=None, previous=None):
    catalog = raw_index.catalog
    table = tables(data)['Items']; pages, lval = catalog._table_pages(data, table)
    rows = raw_rows.table_rows(data, table, pages)
    for row in rows:
        for n, (_, kind, _) in enumerate(case['fields']):
            value = row['values'][n]
            if value is not None and kind in (5, 6, 7):
                raw = bytes.fromhex(value['raw_hex'])
                row['values'][n] = int.from_bytes(raw, 'little', signed=True) if kind == 5 else struct.unpack('<f' if kind == 6 else '<d', raw)[0]
            elif value is not None and kind == 10:
                row['values'][n] = value.encode('cp1252').hex()
            elif value is not None and kind == 15:
                row['values'][n] = uuid.UUID(bytes_le=bytes.fromhex(value['raw_hex'])).hex
    require(not lval and sorted(r['values'] for r in rows) == sorted(expected.values()), 'Complete raw scalar rows')
    require(table['row_count'] == len(expected), 'Declared live row count')
    require([[c['name'], c['type'], c['size']] for c in table['columns']] == [[name, catalog.PHYSICAL_TYPES[kind], size] for name, kind, size in case['fields']], 'Raw field schema')
    require(len(table['physical_indexes']) == len(table['logical_indexes']) == len(case['indexes']), 'Complete physical/logical index inventory')
    logical = {index['name']: index['physical_index'] for index in table['logical_indexes']}
    require(set(logical) == {i['name'] for i in case['indexes']} and len(set(logical.values())) == len(case['indexes']), 'Distinct named indexes')
    map_slots = [tuple(physical['map'][k] for k in ('page', 'row')) for physical in table['physical_indexes']]
    require(len(set(map_slots)) == len(map_slots), 'Distinct index map slots')
    all_owned = set(pages); results = {}; rust_indexes = {i['name']: i for i in receipt['indexes']} if receipt else {}
    for index in case['indexes']:
        physical = table['physical_indexes'][logical[index['name']]]
        require(physical['keys'] == [dict(column=c, direction=int(not d)) for c, d in index['fields']] and
                physical['flags'] == int(index['unique']) + 2 * int(index['ignore']) + 8 * int(index['required']), 'Raw index schema: ' + index['name'])
        nodes, entries = raw_index.tree(data, physical['root'], table['root'], [(case['fields'][c][1], d) for c, d in index['fields']])
        wanted = sorted(k + r['page'].to_bytes(3, 'big') + bytes([r['row']]) for r in rows if (k := key(r['values'], case, index)) is not None)
        require(entries == wanted, 'Full sorted key+locator records: ' + index['name'])
        owned = set(catalog._locator_pages(data, physical['map'], 'numeric index map'))
        reached = {node['page'] for node in nodes}
        require(reached <= owned and not owned.intersection(all_owned), 'Independent data/index maps')
        all_owned.update(owned)
        for page in owned - reached:
            image = data[page * 2048:(page + 1) * 2048]
            require(image[0] in (3, 4) and int.from_bytes(image[4:8], 'little') == table['root'], 'Reserved node owner')
            if previous is not None:
                require(image == previous[page * 2048:(page + 1) * 2048], 'Reserved node bytes preserved')
        require(physical['entry_count'] == counters[index['name']], 'EXP-0230 insertion-only absent-key counter: ' + index['name'])
        layout = dict(depth=max(n['depth'] for n in nodes), nodes=sorted(reached), counter=physical['entry_count'],
                      entries=len(entries), distinct=len({e[:-4] for e in entries}), compressed=[n['page'] for n in nodes if n['prefix']],
                      stale_separators=sum(n['stale_separators'] for n in nodes), reserved=sorted(owned - reached))
        results[index['name']] = layout
        if receipt:
            actual = rust_indexes[index['name']]
            require(actual['depth'] == layout['depth'] and sorted(actual['nodes']) == layout['nodes'] and
                    actual['entries'] == [[e[:-4].hex(), int.from_bytes(e[-4:-1], 'big'), e[-1]] for e in entries], 'Rust/raw complete index receipt')
    locators = sorted([r['values'][0], r['page'], r['row']] for r in rows)
    if receipt:
        require(receipt['items'] == sorted(expected.values()) and receipt['notes'] == NOTES and
                receipt['schema'] == dict(Items=case['fields'], Notes=[['Id', 4, 4], ['Body', 12, 0]]) and
                receipt['locators'] == locators and receipt['pages'] * 2048 == len(data) and receipt['data_pages'] == sorted(pages), 'Rust complete reader and schema receipt')
    return dict(map_slots=map_slots, indexes=results, data_pages=sorted(pages), file_pages=len(data) // 2048, locators=locators)


def refusal_check(directory, notes):
    receipts = json.loads((directory / 'refusals.json').read_text())
    require([r['name'] for r in receipts] == ['later-insert', 'later-replace'], 'Late-index refusal inventory')
    source = (directory / 'indexes13-edited.mdb').read_bytes()
    for receipt in receipts:
        before = directory / f"refusal-{receipt['name']}-before.mdb"
        after = directory / f"refusal-{receipt['name']}-after.mdb"
        require(receipt['preserved'] and receipt['error'] == 'Unsupported("duplicate unique key")' and
                before.read_bytes() == after.read_bytes() == source, 'Thirteenth-index duplicate error and whole-image preservation')
        require(notes_identity(after.read_bytes()) == notes, 'Refusal preserves Notes-owned pages')
    return receipts


def inputs():
    paths = [Path(__file__), SCRIPT, ROOT / 'crates/jet3/examples/index_capacity_candidate.rs',
             ROOT / 'crates/jet3/examples/index_capacity_support/io.rs', Path(engine.__file__).with_suffix('.ps1')]
    return original_inputs() | {str(p.relative_to(ROOT)): identity(p) for p in paths}


def prepare(candidates, revision):
    manifest = engine.prepare(candidates, revision)
    # Stage the shared producer explicitly; the generic VM wrapper uploads all input files.
    import shutil
    shutil.copy2(Path(engine.__file__).with_suffix('.ps1'), candidates / 'numeric_index_mutation.ps1')
    return manifest


def prepare_continue(candidates, outbox, output, generator, revision):
    engine.prepare_continue(candidates, outbox, output, generator, revision)
    import shutil
    shutil.copy2(Path(engine.__file__).with_suffix('.ps1'), output / 'numeric_index_mutation.ps1')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('candidates', type=Path); p.add_argument('revision')
    p = sub.add_parser('evaluate'); p.add_argument('candidates', type=Path); p.add_argument('outbox', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.candidates, subprocess.check_output(['git', 'rev-parse', args.revision], cwd=ROOT, text=True).strip())
    else:
        return int(evaluate(args.candidates, args.outbox)['status'] != 'accepted')


engine.CASE_NAMES = tuple(CONFIG)
engine.SCRIPT = SCRIPT
engine.MANIFEST = MANIFEST
engine.recipe = recipe
engine.initial_row = initial_row
engine.raw_check = raw_check
engine.refusal_check = refusal_check
engine.inputs = inputs
aggregate = engine.aggregate
evaluate = engine.evaluate

if __name__ == '__main__':
    raise SystemExit(main())
