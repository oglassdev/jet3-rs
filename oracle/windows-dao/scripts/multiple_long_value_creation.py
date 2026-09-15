#!/usr/bin/env python3
"""Repeatable multi-column Memo/OLE creation and native writability comparison."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from index_tree_mutation import identity, notes_identity, tables, require, write
from numeric_index_mutation import key, counters_for, apply
import numeric_index_mutation_structure as raw_index

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_suffix('.ps1')
EXAMPLE = ROOT / 'crates/jet3/examples/multiple_long_value_creation_candidate.rs'
GENERATOR = ROOT / 'target/debug/examples/multiple_long_value_creation_candidate'
MANIFEST = 'multiple-long-value-creation.json'
NAMES = ['Body', 'Blob', 'ExtraMemo', 'ExtraBlob', 'LastMemo', 'LastBlob']
LENGTHS = [1, 32, 33, 512, 2036, 2037, 2048, 4064, 4096]
NOTES = [[7, 'n' * 4096], [8, None]]


def payload(id, column, length=None):
    if length is None:
        if id == 10 or (id > 10 and (id + column) % 7 == 0): return None
        length = LENGTHS[(id - 1 + column) % 9] if id <= 9 else 1 + (id + 3 * column) % 17
    if column % 2 == 0:
        return ''.join(chr(65 + (offset + id + column) % 26) for offset in range(length))
    return bytes((offset * 17 + id * 3 + column) % 256 for offset in range(length)).hex()


def row(id, columns):
    return [id, id % 11 - 5 if id % 37 else None] + [payload(id, column) for column in range(columns)]


def recipe():
    result = []
    for name, count, long_columns, index_count, generated, later in [
        ('first-mixed', 205, 4, 3, False, False),
        ('later-generated', 213, 4, 3, True, True),
        ('capacity-six-zero', 12, 6, 0, False, False),
        ('capacity-six-one', 12, 6, 1, False, True),
        ('capacity-five-two', 12, 5, 2, False, False),
        ('capacity-five-three', 12, 5, 3, False, True),
    ]:
        fields = [['Id', 4, 4, generated], ['Tag', 4, 4, False]] + [[name, 12 if c % 2 == 0 else 11, 0, False] for c, name in enumerate(NAMES[:long_columns])]
        indexes = [dict(name='ById', primary=True, unique=True, required=True, ignore=False, fields=[[0, False]]),
                   dict(name='ByTag', primary=False, unique=False, required=False, ignore=False, fields=[[1, True]]),
                   dict(name='ByPair', primary=False, unique=True, required=False, ignore=False, fields=[[1, False], [0, True]])][:index_count]
        inserted = [count + 1 if generated else 9000, 99] + [payload(9000, c, [4096, 33, 32, 2037, 1, 512][c]) for c in range(long_columns)]
        replaced = row(3, long_columns); replaced[1] = -99
        for column, length in enumerate([32, 4096, None, 1]):
            replaced[2 + column] = None if length is None else payload(3, column, length)
        for index in indexes:
            index['queries'] = ([[0], [1], [2], [3], [count], [inserted[0]], [999999]] if index['name'] == 'ById'
                                else [[-99], [-5], [0], [5], [99], [999999]] if index['name'] == 'ByTag'
                                else [[-99, 3], [-2, 3], [-4, 1], [99, inserted[0]], [999999, 1]])
        result.append(dict(name=name, count=count, long_columns=long_columns, generated=generated, later=later,
                           fields=fields, indexes=indexes, initial_rows=[row(id, long_columns) for id in range(1, count + 1)],
                           native=[dict(kind='insert', row=inserted), dict(kind='replace', id=3, row=replaced), dict(kind='delete', id=2)]))
    return result


def expected(case, native=False):
    rows = {r[0]: r.copy() for r in case['initial_rows']}
    counters = counters_for(case, rows)
    if native:
        for operation in case['native']: apply(rows, operation, case, counters)
    return rows, counters


def check_receipt(receipt, case, expected_rows):
    require(set(receipt) == {'Items', 'Notes'}, 'Rust table inventory')
    for name, fields, rows in [('Items', case['fields'], sorted(expected_rows.values())), ('Notes', [['Id', 4, 4, False], ['Body', 12, 0, False]], NOTES)]:
        require(receipt[name]['fields'] == fields, 'Rust schema: ' + name)
        require([r['values'] for r in receipt[name]['rows']] == rows, 'Rust complete payloads and rows: ' + name)
    require(receipt['Notes']['indexes'] == [], 'Unindexed Notes')


def raw_check(data, receipt, case, rows, counters, *, candidate):
    catalog = raw_index.catalog
    table = tables(data)['Items']
    require(table['row_count'] == len(rows), 'Raw row count')
    groups = table['long_value_maps']
    require(sorted(g['column'] for g in groups) == list(range(2, len(case['fields']))), 'One map group per long column')
    data_pages, misplaced_lval = catalog._table_pages(data, table)
    require(not misplaced_lval, 'Table owns only its data pages')
    locators = {(r['page'], r['slot']): r['values'] for r in receipt['Items']['rows']}
    require(len(locators) == len(rows), 'Distinct live row references')
    require(all(page in data_pages for page, _ in locators), 'Logical rows live on owned data pages')
    if candidate:
        decoded = catalog._table_rows(data, table, data_pages)
        require({(r['page'], r['row']): r['values'][:2] for r in decoded} == {loc: row[:2] for loc, row in locators.items()}, 'Independent raw numeric values and row locators')
    logical = {i['name']: i['physical_index'] for i in table['logical_indexes']}
    require(set(logical) == {i['name'] for i in case['indexes']} and len(table['physical_indexes']) == len(logical), 'Raw index inventory')
    rust_indexes = {i['name']: i for i in receipt['Items']['indexes']}
    require(set(rust_indexes) == set(logical), 'Rust index inventory')
    all_owned = set(data_pages); indexes = {}
    for index in case['indexes']:
        physical = table['physical_indexes'][logical[index['name']]]
        require(physical['keys'] == [dict(column=c, direction=int(not desc)) for c, desc in index['fields']] and
                physical['flags'] == int(index['unique']) + 8 * int(index['required']), 'Raw numeric index shape')
        nodes, entries = raw_index.tree(data, physical['root'], table['root'], [(4, desc) for _, desc in index['fields']])
        wanted = sorted(key(row, case, index) + page.to_bytes(3, 'big') + bytes([slot]) for (page, slot), row in locators.items())
        require(entries == wanted, 'All raw index keys and locators: ' + index['name'])
        rust = rust_indexes[index['name']]
        require([bytes.fromhex(k) + p.to_bytes(3, 'big') + bytes([r]) for k, p, r in rust['entries']] == entries, 'Rust/raw complete tree records')
        owned = set(catalog._locator_pages(data, physical['map'], 'index ownership'))
        reached = {node['page'] for node in nodes}
        require(reached <= owned and not owned & all_owned, 'Independent index/data pages')
        all_owned |= owned
        require(physical['entry_count'] == counters[index['name']], 'Retained distinct insertion counter')
        indexes[index['name']] = dict(depth=max(n['depth'] for n in nodes), pages=sorted(reached), owned=sorted(owned))
        require(rust['depth'] == indexes[index['name']]['depth'] and set(rust['nodes']) == reached, 'Rust/raw tree shape')
    maps = {}
    for group in groups:
        column = group['column']; owned = set(catalog._locator_pages(data, group['owned'], 'LVAL ownership'))
        available = set(catalog._locator_pages(data, group['available'], 'LVAL availability'))
        require(available <= owned and not owned & all_owned, 'Independent long-value page ownership')
        require(all(data[p * 2048] == 1 and data[p * 2048 + 4:p * 2048 + 8] == b'LVAL' for p in owned), 'LVAL page kind/owner')
        all_owned |= owned
        if candidate:
            position = column - 2; first = 2 + len(case['indexes']) + 2 * position
            require(group['owned'] == dict(page=table['root'] + 1, row=first) and group['available'] == dict(page=table['root'] + 1, row=first + 1), 'Candidate map row placement')
            lengths = [len(r[column]) // (2 if column % 2 else 1) for r in rows.values() if r[column] is not None]
            count = sum(0 if n <= 32 else 1 if n <= 2036 else (n + 2031) // 2032 for n in lengths)
            require(len(owned) == count, 'Candidate external page count per column')
        maps[group['column_name']] = dict(owned=sorted(owned), available=sorted(available))
    if candidate and case['count'] > 200:
        require(len(data_pages) > 1 and all(i['depth'] > 1 for i in indexes.values()), 'Mixed data and three numeric leaf boundaries')
    if case['generated']:
        state = int.from_bytes(data[table['root'] * 2048 + 16:table['root'] * 2048 + 20], 'little', signed=True)
        require(state == max(rows), 'Persisted generated last ID')
    return dict(file_pages=len(data) // 2048, data_pages=data_pages, indexes=indexes, long_value_maps=maps,
                payload_storage=storage_inventory(data, receipt, table, maps))


def storage_inventory(data, receipt, table, maps):
    """Trace EXP-0061 fragment locators and report native/candidate storage reuse."""
    pages = {}
    for row in receipt['Items']['rows']:
        for reference in row['references']:
            locator = (reference['page'], reference['slot']); seen = set(); length = 0
            column = table['columns'][reference['column']]['name']
            while locator[0]:
                require(locator not in seen, 'Repeated live payload fragment')
                seen.add(locator); number, slot = locator
                require(number in maps[column]['owned'], 'Referenced payload belongs to its column map')
                page = raw_index.catalog._page(data, number, 'LVAL fragment')
                entries = raw_index.catalog._row_directory(page, number)
                entry = next((e for e in entries if e['row'] == slot), None)
                require(entry is not None, 'Payload fragment slot exists')
                raw = page[entry['start']:entry['end']]
                state = pages.setdefault(str(number), dict(column=column, kinds=set(), slots=set(), available=number in maps[column]['available']))
                require(state['column'] == column, 'Independent live payload column')
                state['kinds'].add(reference['storage']); state['slots'].add(slot)
                if reference['storage'] == 'SinglePage':
                    length += len(raw); break
                require(reference['storage'] == 'Chained' and len(raw) >= 4, 'Known payload storage')
                length += len(raw) - 4
                locator = (int.from_bytes(raw[1:4], 'little'), raw[0])
            require(length == reference['length'], 'Complete external storage length')
    return {page: dict(value, kinds=sorted(value['kinds']), slots=sorted(value['slots'])) for page, value in pages.items()}


def prepare(candidates: Path, revision: str):
    cases = recipe(); files = {}
    for case in cases:
        image, snapshot = [candidates / (case['name'] + suffix) for suffix in ('.mdb', '.snapshot.json')]
        receipt = json.loads(snapshot.read_text()); rows, counters = expected(case)
        check_receipt(receipt, case, rows)
        case['layout'] = raw_check(image.read_bytes(), receipt, case, rows, counters, candidate=True)
        case['notes_pages'] = notes_identity(image.read_bytes())
        for path in (image, snapshot): files[path.name] = identity(path)
    refusals = json.loads((candidates / 'refusals.json').read_text())
    require(refusals == [dict(case=c['name'], error='PageFull', destination_absent=True) for c in cases if c['name'].startswith('capacity')], 'Map capacity refusal inventory')
    require(not list(candidates.glob('*-overflow.mdb')), 'Refused destinations absent')
    files['refusals.json'] = identity(candidates / 'refusals.json')
    sources = [Path(__file__), SCRIPT, EXAMPLE, Path(raw_index.__file__), Path(raw_index.catalog.__file__),
               Path(__file__).with_name('numeric_index_mutation.py'), Path(__file__).with_name('index_tree_mutation.py'),
               Path(__file__).with_name('index_tree_mutation_structure.py'), Path(__file__).with_name('multi_level_index_structure.py'), Path(__file__).with_name('field_update.ps1')]
    manifest = dict(document_type='multiple_long_value_creation_inputs', source_revision=revision, cases=cases, files=files,
                    refusals=refusals, generator_binary=identity(GENERATOR), inputs={str(p.relative_to(ROOT)): identity(p) for p in sources})
    write(candidates / MANIFEST, manifest)
    shutil.copy2(SCRIPT, candidates / SCRIPT.name)
    return manifest


def normalized(capture, case, rows):
    require(capture['status'] == 'pass' and capture['error'] is None, 'DAO capture completed')
    value = copy.deepcopy(capture['snapshot'])
    require(value['version'] == '3.0' and value['tables'] == ['Items', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Notes'] and
            value['queries'] == value['relations'] == [], 'Complete DAO inventory')
    require([t['name'] for t in value['user_tables']] == ['Items', 'Notes'], 'User table inventory')
    for actual, name, fields, expected_rows in zip(value['user_tables'], ['Items', 'Notes'], [case['fields'], [['Id', 4, 4, False], ['Body', 12, 0, False]]], [sorted(rows.values()), NOTES]):
        require(actual['name'] == name and actual['attributes'] == 0 and [[f['name'], f['type'], f['size'], bool(f['attributes'] & 16)] for f in actual['fields']] == fields, 'Complete DAO field schema')
        require(sorted(actual['rows']) == expected_rows, 'Complete DAO payload bytes and nulls: ' + name)
        actual['rows'].sort()
        expected_indexes = [] if name == 'Notes' else [dict(name=i['name'], primary=i['primary'], unique=i['unique'], required=i['required'],
            ignore_nulls=i['ignore'], foreign=False, fields=[dict(name=case['fields'][c][0], attributes=int(d)) for c, d in i['fields']]) for i in case['indexes']]
        actual['indexes'].sort(key=lambda i: i['name']); expected_indexes.sort(key=lambda i: i['name'])
        require(actual['indexes'] == expected_indexes, 'Complete DAO index metadata')
    require(set(value['index_reads']) == {i['name'] for i in case['indexes']}, 'DAO index read inventory')
    for index in case['indexes']:
        read = value['index_reads'][index['name']]; traversal = read['traversal']
        require(sorted(traversal) == sorted(rows.values()), 'Full index traversal multiset')
        keys = [key(r, case, index) for r in traversal]
        require(keys == sorted(keys), 'Numeric traversal order')
        read['traversal'] = sorted(traversal, key=lambda r: (key(r, case, index), r[0]))
        require([s['query'] for s in read['seek']] == index['queries'], 'Seek inventory')
        for seek in read['seek']:
            matches = [r for r in rows.values() if [r[c] for c, _ in index['fields']] == seek['query']]
            require(seek['row'] in matches if matches else seek['row'] is None, 'Seek complete matching row or no-match')
            seek['row'] = sorted(matches)
    return value


def retain_report(outbox, report):
    path = outbox / 'multiple-long-value-creation-report.json'; number = 1
    while path.exists():
        if json.loads(path.read_text()) == report: return path
        number += 1; path = outbox / f'multiple-long-value-creation-report-{number}.json'
    write(path, report); return path


def evaluate(candidates: Path, outbox: Path):
    manifest_path = candidates / MANIFEST; manifest = json.loads(manifest_path.read_text())
    report = dict(document_type='dao_multiple_long_value_creation_report', status='failed', source_revision=manifest['source_revision'],
                  manifest=identity(manifest_path), result=identity(outbox / 'result.json'), cases=[], error=None)
    try:
        for name, pin in manifest['inputs'].items(): require(identity(ROOT / name) == pin, 'Source identity: ' + name)
        for name, pin in manifest['files'].items(): require(identity(candidates / name) == pin, 'Candidate identity: ' + name)
        require(identity(GENERATOR) == manifest['generator_binary'], 'Reader binary identity')
        result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
        require(result['document_type'] == 'dao_multiple_long_value_creation_result' and result['source_revision'] == manifest['source_revision'] and
                result['manifest_sha256'] == report['manifest']['sha256'], 'Producer source and inputs')
        require(result['error'] is None and result['retention_failures'] == [], 'Producer/retention completed')
        report['environment'] = result['environment']
        require(result['environment']['process_bits'] == 32 and result['environment']['provider'] == 'DAO.DBEngine.36' and result['environment']['provider_version'] == '3.6', 'Actual DAO provider')
        require([c['name'] for c in result['cases']] == [c['name'] for c in manifest['cases']], 'Case inventory')
        for case, capture in zip(manifest['cases'], result['cases']):
            outcome = dict(name=case['name'], status='failed', checkpoints=[], error=None); report['cases'].append(outcome)
            try:
                require(capture['status'] == 'pass' and capture['error'] is None, 'Case capture completed: ' + str(capture['error']))
                notes = {}; originals = {}
                for phase in ['original', 'native']:
                    require(set(capture[phase]) == {'candidate', 'control'}, 'Paired role inventory')
                    rows, counters = expected(case, phase == 'native'); values = {}; images = {}; layouts = {}
                    for role, observation in capture[phase].items():
                        path = outbox / observation['file']; images[role] = identity(path)
                        require(observation['before'] == observation['after'] == images[role], 'Closed immutable capture identity')
                        if phase == 'original':
                            originals[role] = images[role]; notes[role] = notes_identity(path.read_bytes())
                            if role == 'candidate': require(images[role] == manifest['files'][case['name'] + '.mdb'] and notes[role] == case['notes_pages'], 'Rust input image identity')
                        else:
                            mutation = capture['mutations'][role]
                            require(mutation['before'] == originals[role] and mutation['after'] == images[role] and mutation['operations'] == case['native'], 'Native operation input/result chain')
                            require(notes_identity(path.read_bytes()) == notes[role], 'Unrelated Notes definition/maps/data/LVAL pages preserved')
                        values[role] = normalized(observation, case, rows)
                        snapshot = outbox / (path.stem + '.rust.json')
                        execution = subprocess.run([str(GENERATOR), 'inspect', str(path), str(snapshot)], capture_output=True, text=True)
                        write(outbox / (path.stem + '.rust-command.json'), dict(args=['inspect', path.name, snapshot.name], returncode=execution.returncode, stdout=execution.stdout, stderr=execution.stderr, generator=manifest['generator_binary']))
                        require(execution.returncode == 0, 'Rust reader of DAO capture: ' + execution.stderr)
                        receipt = json.loads(snapshot.read_text()); check_receipt(receipt, case, rows)
                        layouts[role] = raw_check(path.read_bytes(), receipt, case, rows, counters, candidate=phase == 'original' and role == 'candidate')
                    require(values['candidate'] == values['control'], 'All paired DAO metadata and semantics')
                    outcome['checkpoints'].append(dict(phase=phase, rows=len(rows), images=images, layout=layouts, notes_pages=notes))
                outcome['status'] = 'accepted'
            except Exception as error: outcome['error'] = str(error)
        require(identity(outbox / 'refusals.json') == manifest['files']['refusals.json'], 'Retained capacity refusals')
        report['refusals'] = manifest['refusals']
        require(all(c['status'] == 'accepted' for c in report['cases']), 'One or more multi-column cases failed')
        report['status'] = 'accepted'
    except Exception as error: report['error'] = str(error)
    print(retain_report(outbox, report)); return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('candidates', type=Path); p.add_argument('revision')
    p = sub.add_parser('evaluate'); p.add_argument('candidates', type=Path); p.add_argument('outbox', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare': prepare(args.candidates, args.revision)
    else: raise SystemExit(evaluate(args.candidates, args.outbox)['status'] != 'accepted')
