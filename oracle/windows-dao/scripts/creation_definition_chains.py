#!/usr/bin/env python3
"""Exact TDEF chain boundaries, independent typed rows, and native writability."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from index_tree_mutation import identity, require, write
from creation_definition_chains_structure import notes_identity, tables
from numeric_index_mutation import key
import numeric_index_mutation_structure as raw_index
import multiple_long_value_creation as lval

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_suffix('.ps1')
EXAMPLE = ROOT / 'crates/jet3/examples/creation_definition_chains_candidate.rs'
GENERATOR = ROOT / 'target/debug/examples/creation_definition_chains_candidate'
MANIFEST = 'creation-definition-chains.json'


def payload(id, binary, length=None):
    if length is None:
        length = {1: 32, 2: 33, 3: 2036, 4: 2037, 5: 4096}.get(id)
    if length is None: return None
    if binary: return bytes((17 * n + id) % 256 for n in range(length)).hex()
    return ''.join(chr(65 + (n + id) % 26) for n in range(length))


def row(id, columns, payloads):
    values = [id, id % 11 - 5] + [None if (id + n) % 7 == 0 else (id + n) % 251 if payloads else id * 1000 + n for n in range(2, columns)]
    if payloads: values[-2:] = [payload(id, False), payload(id, True)]
    return values


def recipe():
    cases = []
    for name, length, columns, count, index_count, payloads, generated, later in [
        ('root-full', 2048, 64, 3, 0, False, False, False),
        ('root-over', 2049, 64, 0, 0, False, False, True),
        ('one-full', 4088, 64, 205, 3, False, False, False),
        ('one-over', 4089, 64, 17, 3, True, False, True),
        ('two-full', 6128, 96, 3, 3, True, False, False),
        ('two-over', 6129, 96, 205, 3, True, True, True),
    ]:
        names = ['Id', 'Tag'] + [f'C{n:04}' for n in range(2, columns)]
        if payloads: names[-2:] = ['Body', 'Blob']
        base = 45 + 19 * columns + sum(map(len, names)) + (219 if index_count else 0) + (20 if payloads else 0)
        padded = columns - 2 - (2 if payloads else 0)
        for n in range(length - base): names[2 + n % padded] += 'x'
        require(max(map(len, names)) <= 64, 'Bounded existing name alphabet/width')
        fields = [[name, 2 if payloads and n > 1 else 4, 1 if payloads and n > 1 else 4, n == 0 and generated] for n, name in enumerate(names)]
        if payloads: fields[-2:] = [['Body', 12, 0, False], ['Blob', 11, 0, False]]
        inserted = row(count + 1 if generated else 9000, columns, payloads)
        inserted[1] = 99
        if payloads: inserted[-2:] = [payload(9000, False, 4096), payload(9000, True, 33)]
        target = 3 if count else inserted[0]
        replaced = row(target, columns, payloads); replaced[1] = -99; replaced[2] = None
        if payloads: replaced[-2:] = [payload(3, False, 32), payload(3, True, 4096)]
        indexes = [dict(name='ById', primary=True, unique=True, required=True, ignore=False, fields=[[0, False]], queries=[[0], [1], [3], [205], [inserted[0]], [999999]]),
                   dict(name='ByTag', primary=False, unique=False, required=False, ignore=False, fields=[[1, True]], queries=[[-99], [-5], [0], [5], [99], [999999]]),
                   dict(name='ByPair', primary=False, unique=True, required=False, ignore=False, fields=[[1, False], [0, True]], queries=[[-99, 3], [-2, 3], [99, inserted[0]], [999999, 1]])][:index_count]
        cases.append(dict(name=name, length=length, count=count, fields=fields, indexes=indexes, generated=generated, later=later,
                          initial_rows=[row(id, columns, payloads) for id in range(1, count + 1)],
                          native=[dict(kind='insert', row=inserted), dict(kind='replace', id=target, row=replaced), dict(kind='delete', id=2 if count else target)]))
    return cases


def raw_check(data, receipt, case, rows, counters, *, candidate):
    catalog = raw_index.catalog
    table = tables(data)['Items']
    require(table['row_count'] == len(rows), 'Raw row count')
    require(table['logical_length'] == case['length'], 'Exact logical definition length')
    chain = table['pages']
    require(len(chain) == 1 + (0 if case['length'] < 2048 else 1 + (case['length'] - 2048) // 2040), 'Definition chain geometry')
    require(len(set(chain)) == len(chain), 'Distinct definition pages')
    if candidate:
        first = table['root'] + 2 + int(not case['later'])
        require(chain == [table['root']] + list(range(first, first + len(chain) - 1)), 'Compact candidate continuation placement')
        for n, physical in enumerate(table['physical_indexes']):
            require(physical['root'] == first + len(chain) - 1 + n, 'Index roots follow complete definition')
    groups = table['long_value_maps']
    require(sorted(g['column'] for g in groups) == [n for n, f in enumerate(case['fields']) if f[1] in (11, 12)], 'One map group per long column')
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
    require(not set(chain) & set(data_pages), 'Definition and row pages are separate')
    all_owned = set(data_pages) | set(chain); indexes = {}
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
            position = [n for n, f in enumerate(case['fields']) if f[1] in (11, 12)].index(column); first = 2 + len(case['indexes']) + 2 * position
            require(group['owned'] == dict(page=table['root'] + 1, row=first) and group['available'] == dict(page=table['root'] + 1, row=first + 1), 'Candidate map row placement')
            lengths = [len(r[column]) // (2 if case['fields'][column][1] == 11 else 1) for r in rows.values() if r[column] is not None]
            count = sum(0 if n <= 32 else 1 if n <= 2036 else (n + 2031) // 2032 for n in lengths)
            require(len(owned) == count, 'Candidate external page count per column')
        maps[group['column_name']] = dict(owned=sorted(owned), available=sorted(available))
    if candidate and case['count'] > 200:
        require(len(data_pages) > 1 and all(i['depth'] > 1 for i in indexes.values()), 'Mixed data and three numeric leaf boundaries')
    if case['generated']:
        state = int.from_bytes(data[table['root'] * 2048 + 16:table['root'] * 2048 + 20], 'little', signed=True)
        require(state == max(rows), 'Persisted generated last ID')
    return dict(definition_pages=chain, logical_length=table['logical_length'], file_pages=len(data) // 2048, data_pages=data_pages, indexes=indexes, long_value_maps=maps,
                payload_storage=lval.storage_inventory(data, receipt, table, maps))


def prepare(candidates: Path, revision: str):
    cases = recipe(); files = {}
    for case in cases:
        image, snapshot = [candidates / (case['name'] + suffix) for suffix in ('.mdb', '.snapshot.json')]
        receipt = json.loads(snapshot.read_text()); rows, counters = lval.expected(case)
        lval.check_receipt(receipt, case, rows)
        case['layout'] = raw_check(image.read_bytes(), receipt, case, rows, counters, candidate=True)
        case['notes_pages'] = notes_identity(image.read_bytes())
        for path in (image, snapshot): files[path.name] = identity(path)
    sources = [Path(__file__), SCRIPT, EXAMPLE, EXAMPLE.parent / 'creation_definition_chains_support/snapshot.rs', Path(raw_index.__file__), Path(raw_index.catalog.__file__), Path(lval.__file__), Path(lval.__file__).with_suffix('.ps1'), Path(__file__).with_name('creation_definition_chains_structure.py')]
    sources += [Path(__file__).with_name(name) for name in ['numeric_index_mutation.py', 'index_tree_mutation.py', 'index_tree_mutation_structure.py', 'multi_level_index_structure.py', 'field_update.ps1']]
    manifest = dict(document_type='creation_definition_chains_inputs', source_revision=revision, cases=cases, files=files,
                    generator_binary=identity(GENERATOR), inputs={str(p.relative_to(ROOT)): identity(p) for p in sources})
    write(candidates / MANIFEST, manifest)
    for source in [SCRIPT, Path(lval.__file__).with_suffix('.ps1'), Path(__file__).with_name('field_update.ps1')]: shutil.copy2(source, candidates / source.name)
    return manifest


def retain_report(outbox, report):
    path = outbox / 'creation-definition-chains-report.json'; n = 1
    while path.exists():
        if json.loads(path.read_text()) == report: return path
        n += 1; path = outbox / f'creation-definition-chains-report-{n}.json'
    write(path, report); return path


def evaluate(candidates: Path, outbox: Path):
    manifest_path = candidates / MANIFEST; manifest = json.loads(manifest_path.read_text())
    report = dict(document_type='dao_creation_definition_chains_report', status='failed', source_revision=manifest['source_revision'],
                  manifest=identity(manifest_path), result=identity(outbox / 'result.json'), cases=[], error=None)
    try:
        for name, pin in manifest['inputs'].items(): require(identity(ROOT / name) == pin, 'Source identity: ' + name)
        for name, pin in manifest['files'].items(): require(identity(candidates / name) == pin, 'Input identity: ' + name)
        require(identity(GENERATOR) == manifest['generator_binary'], 'Reader binary identity')
        result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
        require(result['document_type'] == 'dao_creation_definition_chains_result' and result['source_revision'] == manifest['source_revision'] and result['manifest_sha256'] == report['manifest']['sha256'], 'Producer source/input identity')
        report['environment'] = result['environment']
        require(result['error'] is None and result['retention_failures'] == [], 'Producer/retention completed')
        require(result['environment']['process_bits'] == 32 and result['environment']['provider'] == 'DAO.DBEngine.36' and result['environment']['provider_version'] == '3.6', 'Actual provider')
        require([c['name'] for c in result['cases']] == [c['name'] for c in manifest['cases']], 'Case inventory')
        require([w['case'] for w in result['workers']] == [c['name'] for c in manifest['cases']], 'Native worker inventory')
        for worker, capture in zip(result['workers'], result['cases']):
            path = outbox / worker['file']
            require(worker['exit_code'] == 0 and identity(path) == worker['image'], 'Native worker completion/identity')
            value = json.loads(path.read_text())
            require(value['source_revision'] == manifest['source_revision'] and value['manifest_sha256'] == report['manifest']['sha256'] and
                    value['error'] is None and value['retention_failures'] == [] and value['environment'] == result['environment'] and value['cases'] == [capture], 'Worker source/provider/capture identity')
        for case, capture in zip(manifest['cases'], result['cases']):
            outcome = dict(name=case['name'], status='failed', checkpoints=[], error=None); report['cases'].append(outcome)
            try:
                require(capture['status'] == 'pass' and capture['error'] is None, 'Native capture completed: ' + str(capture['error']))
                notes = {}; originals = {}
                for phase in ['original', 'native']:
                    require(set(capture[phase]) == {'candidate', 'control'}, 'Paired inventory')
                    rows, counters = lval.expected(case, phase == 'native'); values = {}; images = {}; layouts = {}
                    for role, observation in capture[phase].items():
                        path = outbox / observation['file']; images[role] = identity(path)
                        require(observation['before'] == observation['after'] == images[role], 'Immutable read capture')
                        if phase == 'original':
                            originals[role] = images[role]; notes[role] = notes_identity(path.read_bytes())
                            if role == 'candidate': require(images[role] == manifest['files'][case['name'] + '.mdb'] and notes[role] == case['notes_pages'], 'Candidate generated image')
                        else:
                            mutation = capture['mutations'][role]
                            require(mutation['before'] == originals[role] and mutation['after'] == images[role] and mutation['operations'] == case['native'], 'Native input/result chain')
                            require(notes_identity(path.read_bytes()) == notes[role], 'Unrelated Notes metadata/data/payload pages preserved')
                        values[role] = lval.normalized(observation, case, rows)
                        snapshot = outbox / (path.stem + '.rust.json')
                        execution = subprocess.run([str(GENERATOR), 'inspect', str(path), str(snapshot)], capture_output=True, text=True)
                        write(outbox / (path.stem + '.rust-command.json'), dict(args=['inspect', path.name, snapshot.name], returncode=execution.returncode, stdout=execution.stdout, stderr=execution.stderr, generator=manifest['generator_binary']))
                        require(execution.returncode == 0, 'Rust retained-image read: ' + execution.stderr)
                        receipt = json.loads(snapshot.read_text()); lval.check_receipt(receipt, case, rows)
                        layouts[role] = raw_check(path.read_bytes(), receipt, case, rows, counters, candidate=phase == 'original' and role == 'candidate')
                    require(values['candidate'] == values['control'], 'Complete DAO schema/row/traversal/Seek comparison')
                    outcome['checkpoints'].append(dict(phase=phase, rows=len(rows), images=images, layout=layouts, notes_pages=notes))
                outcome['status'] = 'accepted'
            except Exception as error: outcome['error'] = str(error)
        require(all(c['status'] == 'accepted' for c in report['cases']), 'One or more definition-chain cases failed')
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
