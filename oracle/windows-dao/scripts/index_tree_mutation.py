#!/usr/bin/env python3
"""Prepare/evaluate the finite EXP-0223 index-tree mutation and DAO successor suite."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import index_tree_mutation_structure as raw_index

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'oracle/windows-dao/scripts/index_tree_mutation.ps1'
QUERIES = [-2147483648, -3, -2, -1, 0, 1, 99, 199, 200, 13700, 27799, 27800, 27801, 1000000, 1000001, 1000002, 1234567, 2147483647]
NOTES = [[7, 'n' * 4096], [8, None]]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(path):
    data = path.read_bytes()
    return dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')


def base_row(id, deep):
    return [id, id * 17 + 3] if deep else [id, 'x' * 80, '11' * 8]


def recipe(name):
    deep = name in ('deep', 'tombstone-empty')
    count = 27800 if name == 'deep' else 3 if name == 'tombstone-empty' else 1 if name == 'empty' else 200
    added = 27800 if deep else -1
    stages = [dict(name='original', operations=[])]
    if name in ('empty', 'tombstone-empty'):
        if name == 'tombstone-empty':
            stages.extend([dict(name='delete-tail', operations=[dict(kind='delete', id=2)]), dict(name='delete-first', operations=[dict(kind='delete', id=0)])])
        stages.append(dict(name='empty', operations=[dict(kind='delete', id=1 if name == 'tombstone-empty' else 0)]))
    else:
        stages.extend([
            dict(name='split', operations=[dict(kind='insert', row=base_row(added, deep))]),
            dict(name='reordered', operations=[dict(kind='key', id=added, next_id=1000000)]),
        ])
        if not deep:
            stages.extend([
                dict(name='grown', operations=[dict(kind='row', id=1000000, row=[1000000, 'g' * 80, 'ab' * 60])]),
                dict(name='shrunk', operations=[dict(kind='row', id=1000000, row=[1000000, 's', '01'])]),
            ])
        stages.append(dict(name='collapsed', operations=[dict(kind='delete', id=1000000)]))
    stages.append(dict(name='regrown', operations=[dict(kind='insert', row=base_row(-2, deep))]))
    counter = count
    for stage in stages:
        counter += sum(op['kind'] == 'insert' for op in stage['operations'])
        stage['counter'] = counter
        stage['capture'] = name != 'deep' or stage['name'] in ('original', 'split', 'regrown')
    return dict(name=name, deep=deep, descending=name == 'descending', initial_count=count, stages=stages,
                native=[dict(kind='insert', row=base_row(1000001, deep)),
                        dict(kind='key', id=1000001, next_id=1000002),
                        dict(kind='delete', id=-2 if name in ('empty', 'tombstone-empty') else 0)])


def apply(rows, operation):
    kind = operation['kind']
    if kind == 'insert':
        require(operation['row'][0] not in rows, 'Recipe duplicate insertion')
        rows[operation['row'][0]] = operation['row'].copy()
    elif kind == 'delete':
        del rows[operation['id']]
    elif kind == 'key':
        row = rows.pop(operation['id']); row[0] = operation['next_id']
        require(row[0] not in rows, 'Recipe duplicate key change'); rows[row[0]] = row
    elif kind == 'row':
        del rows[operation['id']]
        require(operation['row'][0] not in rows, 'Recipe duplicate row change')
        rows[operation['row'][0]] = operation['row'].copy()
    else:
        raise ValueError('Unknown operation')


def expected_stages(case):
    rows = {id: base_row(id, case['deep']) for id in range(case['initial_count'])}
    for stage in case['stages']:
        for operation in stage['operations']:
            apply(rows, operation)
        yield stage, copy.deepcopy(rows)


def tables(data):
    catalog = raw_index.catalog
    catalog.MAX_ROWS_PER_PAGE = 256
    definition, _, records = catalog._discover_catalog(data)
    name, id = [catalog._ordinal(definition, column) for column in ('Name', 'Id')]
    roots = {r['values'][name]: r['values'][id] for r in records}
    return {name: catalog._definition(data, roots[name]) for name in ('Items', 'Notes')}


def notes_identity(data):
    catalog = raw_index.catalog
    notes = tables(data)['Notes']
    pages = set(notes['pages'])
    for locator in [*notes['maps'].values(), *(locator for group in notes['long_value_maps'] for locator in (group['owned'], group['available']))]:
        pages.add(locator['page'])
        pages.update(catalog._locator_pages(data, locator, 'Notes allocation'))
    return {str(page): hashlib.sha256(data[page * 2048:(page + 1) * 2048]).hexdigest() for page in sorted(pages)}


def raw_check(data, case, expected, layout, counter, previous=None):
    catalog = raw_index.catalog
    table = tables(data)['Items']
    pages, long_values = catalog._table_pages(data, table)
    rows = catalog._table_rows(data, table, pages)
    require(not long_values and sorted(r['values'] for r in rows) == sorted(expected.values()), 'Complete raw rows')
    require(table['row_count'] == len(rows), 'Raw table row count')
    require(len(table['physical_indexes']) == len(table['logical_indexes']) == 1, 'Single index')
    physical = table['physical_indexes'][0]
    require(physical['keys'] == [dict(column=0, direction=int(not case['descending']))] and physical['flags'] == (1 if case['descending'] else 9), 'Index key definition')
    require(table['logical_indexes'][0]['name'] == 'ById', 'Logical index name')
    nodes, entries = raw_index.tree(data, physical['root'], table['root'])
    wanted = []
    for row in rows:
        key = b'\x7f' + ((row['values'][0] & 0xffffffff) ^ 0x80000000).to_bytes(4, 'big')
        if case['descending']:
            key = bytes(b ^ 255 for b in key)
        wanted.append(key + row['page'].to_bytes(3, 'big') + bytes([row['row']]))
    require(entries == sorted(wanted), 'Complete raw key/locator entries')
    maps = sorted(catalog._locator_pages(data, physical['map'], 'index ownership'))
    reached = sorted(node['page'] for node in nodes)
    require(set(reached) <= set(maps) and not set(maps).intersection(pages), 'Independent data and index ownership')
    for page in set(maps) - set(reached):
        image = data[page * 2048:(page + 1) * 2048]
        require(previous is not None and image == previous[page * 2048:(page + 1) * 2048] and
                image[0] in (3, 4) and int.from_bytes(image[4:8], 'little') == table['root'],
                'Mapped spare index page retains its previous bytes and owner')
    require(physical['entry_count'] == counter, 'Retained index counter increments on insertions')
    depth = max(node['depth'] for node in nodes)
    require(layout['rows'] == len(rows) and layout['depth'] == depth and sorted(layout['nodes']) == reached and layout['pages'] * 2048 == len(data), 'Rust/raw layout receipt')
    required_depth = 1 if len(rows) <= 200 else 2 if len(rows) <= 27800 else 3
    require(depth == required_depth, 'Declared 200/201 and 27800/27801 depth boundaries')
    return dict(rows=len(rows), depth=depth, index_pages=reached, reserved_index_pages=sorted(set(maps) - set(reached)), data_pages=pages, file_pages=len(data) // 2048)


def prepare(candidates: Path, revision: str):
    directory = candidates
    cases = [recipe(name) for name in ('primary', 'descending', 'empty', 'tombstone-empty', 'deep')]
    files = {}
    for case in cases:
        original_notes = None
        previous = None
        for stage, expected in expected_stages(case):
            stem = f"{case['name']}-{stage['name']}"
            image, row_path, layout_path = [directory / (stem + suffix) for suffix in ('.mdb', '.rows.json', '.layout.json')]
            rows = json.loads(row_path.read_text()); layout = json.loads(layout_path.read_text())
            require(rows == sorted(expected.values()), 'Rust reader rows versus independent operation recipe: ' + stem)
            raw_check(image.read_bytes(), case, expected, layout, stage['counter'], previous)
            previous = image.read_bytes()
            notes = notes_identity(image.read_bytes())
            if original_notes is None:
                original_notes = notes
            require(notes == original_notes, 'Notes definition, maps, data and LVAL pages unchanged: ' + stem)
            if stage['name'] == 'split' and not case['deep']:
                require(layout['data_eof_insert'] is True, 'Combined data-EOF and index-node append')
            for path in (image, row_path, layout_path):
                files[path.name] = identity(path)
        case['notes_pages'] = original_notes
    manifest = dict(document_type='index_tree_mutation_inputs', round='mutations', source_revision=revision,
                    producer=identity(SCRIPT), generator=identity(ROOT / 'crates/jet3/examples/index_tree_mutation_candidate.rs'),
                    analyzer=identity(Path(__file__)), structure=identity(Path(raw_index.__file__)), cases=cases, files=files, queries=QUERIES)
    write(directory / 'index-tree-mutation.json', manifest)
    shutil.copy2(SCRIPT, directory / SCRIPT.name)


def sidecar(outbox, reference):
    path = outbox / reference['file']
    require(identity(path) == {key: reference[key] for key in ('size', 'sha256')}, 'Sidecar identity: ' + path.name)
    return json.loads(path.read_text())


def normalized(outbox, capture, case, rows):
    require(capture['status'] == 'pass' and capture['error'] is None, 'DAO capture completed')
    require(capture['before'] == capture['after'] == identity(outbox / capture['file']), 'Read-only capture identity')
    value = copy.deepcopy(capture['snapshot'])
    require(value['version'] == '3.0' and value['tables'] == ['Items', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Notes'] and value['queries'] == value['relations'] == [], 'Complete database inventory')
    require([t['name'] for t in value['user_tables']] == ['Items', 'Notes'], 'User table inventory')
    items, notes = value['user_tables']
    wanted_fields = [('Id', 4, 4), ('Value', 4, 4)] if case['deep'] else [('Id', 4, 4), ('Text', 10, 80), ('Bytes', 9, 80)]
    for actual, columns in [(items, wanted_fields), (notes, [('Id', 4, 4), ('Body', 12, 0)])]:
        require(actual['attributes'] == 0 and [(c['name'], c['type'], c['size']) for c in actual['fields']] == columns, 'Complete field schema')
    require(notes['indexes'] == [], 'Unindexed Notes sentinel')
    require(items['indexes'] == [dict(name='ById', primary=not case['descending'], unique=True, required=not case['descending'], ignore_nulls=False, foreign=False, fields=[dict(name='Id', attributes=int(case['descending']))])], 'Complete index schema')
    item_rows = sidecar(outbox, items.pop('rows'))
    note_rows = sidecar(outbox, notes.pop('rows'))
    require(sorted(item_rows) == sorted(rows.values()) and sorted(note_rows) == NOTES, 'Complete DAO rows and Memo sentinel content')
    traversal = sidecar(outbox, value.pop('traversal'))
    require(traversal == sorted(rows.values(), key=lambda row: row[0], reverse=case['descending']), 'Complete directed index traversal')
    require([seek['query'] for seek in value['seek']] == QUERIES, 'Seek query inventory')
    for seek in value['seek']:
        require(seek['row'] == rows.get(seek['query']), 'Seek exact row or absent result')
    # Content was checked in full; retain metadata and semantic Seek rows for exact paired comparison.
    return value


def evaluate(candidates: Path, outbox: Path):
    directory = candidates
    manifest_path = directory / 'index-tree-mutation.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['round'] == 'continuation':
        return evaluate_continue(directory, outbox)
    result = json.loads((outbox / 'result.json').read_text())
    report = dict(document_type='dao_index_tree_mutation_report', status='failed', source_revision=manifest['source_revision'],
                  manifest=identity(manifest_path), result=identity(outbox / 'result.json'), cases=[], error=None)
    try:
        require(manifest['analyzer'] == identity(Path(__file__)), 'Analyzer identity')
        require(manifest['structure'] == identity(Path(raw_index.__file__)), 'Tree decoder identity')
        require(result['document_type'] == 'dao_index_tree_mutation_result' and result['source_revision'] == manifest['source_revision'] and result['manifest_sha256'] == identity(manifest_path)['sha256'], 'Result inputs and source')
        require(result['error'] is None and result['retention_failures'] == [], 'Producer and artifact retention completed')
        require(result['environment']['process_bits'] == 32 and result['environment']['provider'] == 'DAO.DBEngine.36' and result['environment']['provider_version'] == '3.6' and len(result['environment']['dll']['sha256']) == 64, 'Actual DAO provider environment')
        observed = {case['name']: case for case in result['cases']}
        require(len(observed) == len(result['cases']) and set(observed) == {case['name'] for case in manifest['cases']}, 'Case inventory')
        for case in manifest['cases']:
            outcome = dict(name=case['name'], status='failed', checkpoints=[], error=None)
            report['cases'].append(outcome)
            try:
                case_result = observed[case['name']]
                require(case_result['status'] == 'pass' and case_result['error'] is None, 'Case completed')
                require([p['name'] for p in case_result['stages']] == [p['name'] for p in case['stages']], 'Checkpoint inventory')
                original_control_notes = None
                previous = None
                for (stage, expected), observation in zip(expected_stages(case), case_result['stages']):
                    stem = f"{case['name']}-{stage['name']}"
                    require(observation['operations'] == stage['operations'], 'Control operation recipe')
                    require(set(observation['roles']) == {'candidate', 'control'}, 'Role inventory')
                    snapshots = {}; images = {}
                    for role, record in observation['roles'].items():
                        path = outbox / record['file']; images[role] = identity(path)
                        require(images[role] == record['image'], 'Retained checkpoint image')
                        if role == 'candidate':
                            require(images[role] == manifest['files'][stem + '.mdb'], 'Rust checkpoint input identity')
                            require(notes_identity(path.read_bytes()) == case['notes_pages'], 'Rust Notes page preservation')
                        elif original_control_notes is None:
                            original_control_notes = notes_identity(path.read_bytes())
                        else:
                            require(notes_identity(path.read_bytes()) == original_control_notes, 'Control Notes page preservation')
                        if stage['capture']:
                            snapshots[role] = normalized(outbox, record['capture'], case, expected)
                        else:
                            require(record['capture'] is None, 'Declared finite capture scope')
                    if stage['capture']:
                        require(snapshots['candidate'] == snapshots['control'], 'Complete paired DAO semantics')
                    layout_path = directory / (stem + '.layout.json')
                    require(identity(layout_path) == manifest['files'][layout_path.name], 'Layout input identity')
                    raw = raw_check((outbox / observation['roles']['candidate']['file']).read_bytes(), case, expected, json.loads(layout_path.read_text()), stage['counter'], previous)
                    previous = (outbox / observation['roles']['candidate']['file']).read_bytes()
                    outcome['checkpoints'].append(dict(name=stage['name'], images=images, raw=raw, dao_compared=stage['capture']))
                final_rows = expected
                for operation in case['native']:
                    apply(final_rows, operation)
                require(set(case_result['native']) == {'candidate', 'control'}, 'Native successor role inventory')
                native_snapshots = {}
                for role, native in case_result['native'].items():
                    require([o['request'] for o in native['operations']] == case['native'] and all(o['status'] == 'pass' for o in native['operations']), 'Native insert/key-update/delete completed')
                    chain = outcome['checkpoints'][-1]['images'][role]
                    for operation in native['operations']:
                        require(operation['before'] == chain, 'Native mutation image chain')
                        require(identity(outbox / operation['file']) == operation['after'], 'Retained native operation image')
                        chain = operation['after']
                    capture = native['capture']
                    require(capture['before'] == chain, 'Native final capture input')
                    native_snapshots[role] = normalized(outbox, capture, case, final_rows)
                    expected_notes = case['notes_pages'] if role == 'candidate' else original_control_notes
                    require(notes_identity((outbox / capture['file']).read_bytes()) == expected_notes, 'Native successor Notes page preservation')
                require(native_snapshots['candidate'] == native_snapshots['control'], 'Complete native successor paired semantics')
                outcome.update(status='accepted', native_rows=len(final_rows), notes_payload_sha256=hashlib.sha256(b'n' * 4096).hexdigest())
            except Exception as error:
                outcome['error'] = str(error)
        require(all(case['status'] == 'accepted' for case in report['cases']), 'One or more cases failed')
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        path = outbox / 'index-tree-mutation-report.json'; suffix = 1
        while path.exists():
            if json.loads(path.read_text()) == report:
                break
            suffix += 1; path = outbox / f'index-tree-mutation-report-{suffix}.json'
        else:
            write(path, report)
        print(path)
    return report


def prepare_continue(directory, first_outbox, output, generator, revision):
    first = json.loads((directory / 'index-tree-mutation.json').read_text())
    result = json.loads((first_outbox / 'result.json').read_text())
    require(result['manifest_sha256'] == identity(directory / 'index-tree-mutation.json')['sha256'], 'Continuation source run')
    output.mkdir(parents=True, exist_ok=False)
    cases = []; files = {}; outcomes = []
    try:
        for name in ('primary', 'descending', 'deep'):
            case = next(c for c in first['cases'] if c['name'] == name)
            observed = next(c for c in result['cases'] if c['name'] == name)
            require(observed['status'] == 'pass', 'Source native case completed')
            capture = observed['native']['control']['capture']
            source = first_outbox / capture['file']
            expected = list(expected_stages(case))[-1][1]
            for operation in case['native']:
                apply(expected, operation)
            normalized(first_outbox, capture, case, expected)
            input_nodes, _ = raw_index.tree(source.read_bytes(), tables(source.read_bytes())['Items']['physical_indexes'][0]['root'], tables(source.read_bytes())['Items']['root'])
            compressed = [node['page'] for node in input_nodes if node['prefix']]
            require(compressed, 'Source contains prefix-compressed index nodes')
            stale = sum(node['stale_separators'] for node in input_nodes)
            require(name != 'descending' or stale > 0, 'Descending source retains a separator after native deletion')
            operation = dict(kind='insert', row=base_row(1234567, case['deep']))
            apply(expected, operation)
            child = output / name
            done = subprocess.run([str(generator), 'continue', str(source), str(child), name], capture_output=True, text=True)
            (output / (name + '.stdout.log')).write_text(done.stdout)
            (output / (name + '.stderr.log')).write_text(done.stderr)
            outcomes.append(dict(name=name, returncode=done.returncode, source=identity(source)))
            require(done.returncode == 0, 'Rust continuation failed: ' + name)
            source_name = name + '-continuation-source.mdb'
            shutil.copy2(source, output / source_name)
            for suffix in ('.mdb', '.rows.json', '.layout.json'):
                file = name + '-continued' + suffix
                shutil.copy2(child / file, output / file)
                files[file] = identity(output / file)
            files[source_name] = identity(output / source_name)
            require(json.loads((output / (name + '-continued.rows.json')).read_text()) == sorted(expected.values()), 'Continuation Rust reader rows')
            counter = tables(source.read_bytes())['Items']['physical_indexes'][0]['entry_count'] + 1
            layout = json.loads((output / (name + '-continued.layout.json')).read_text())
            raw_check((output / (name + '-continued.mdb')).read_bytes(), case, expected, layout, counter, source.read_bytes())
            notes = notes_identity(source.read_bytes())
            require(notes_identity((output / (name + '-continued.mdb')).read_bytes()) == notes, 'Continuation Notes bytes')
            cases.append(dict(name=name, deep=case['deep'], descending=case['descending'], source_file=source_name,
                              candidate_file=name + '-continued.mdb', operation=operation, expected=sorted(expected.values()),
                              counter=counter, notes_pages=notes, compressed_source_pages=compressed, retained_source_separators=stale))
        manifest = dict(document_type='index_tree_mutation_inputs', round='continuation', source_revision=revision,
                        producer=identity(SCRIPT), analyzer=identity(Path(__file__)), structure=identity(Path(raw_index.__file__)),
                        generator=identity(ROOT / 'crates/jet3/examples/index_tree_mutation_candidate.rs'),
                        parent_manifest=identity(directory / 'index-tree-mutation.json'), parent_result=identity(first_outbox / 'result.json'),
                        cases=cases, files=files, queries=QUERIES)
        write(output / 'index-tree-mutation.json', manifest)
        shutil.copy2(SCRIPT, output / SCRIPT.name)
    finally:
        write(output / 'continuation-preparation.json', outcomes)


def evaluate_continue(directory, outbox):
    manifest_path = directory / 'index-tree-mutation.json'
    manifest = json.loads(manifest_path.read_text())
    result = json.loads((outbox / 'result.json').read_text())
    report = dict(document_type='dao_index_tree_mutation_continuation_report', status='failed', cases=[], error=None,
                  source_revision=manifest['source_revision'], manifest=identity(manifest_path), result=identity(outbox / 'result.json'))
    try:
        require(manifest['analyzer'] == identity(Path(__file__)), 'Continuation analyzer identity')
        require(manifest['structure'] == identity(Path(raw_index.__file__)), 'Continuation tree decoder identity')
        require(result['document_type'] == 'dao_index_tree_mutation_result' and result['round'] == 'continuation' and
                result['source_revision'] == manifest['source_revision'] and result['manifest_sha256'] == identity(manifest_path)['sha256'], 'Continuation source')
        require(result['error'] is None and result['retention_failures'] == [], 'Continuation producer complete')
        require(result['environment']['process_bits'] == 32 and result['environment']['provider_version'] == '3.6' and len(result['environment']['dll']['sha256']) == 64, 'Continuation provider')
        require([c['name'] for c in result['cases']] == [c['name'] for c in manifest['cases']], 'Continuation case inventory')
        for case, observed in zip(manifest['cases'], result['cases']):
            require(observed['status'] == 'pass' and observed['error'] is None and set(observed['roles']) == {'candidate', 'control'}, 'Continuation case complete')
            expected = {row[0]: row for row in case['expected']}
            source = directory / case['source_file']; require(identity(source) == manifest['files'][source.name], 'Compressed source identity')
            previous = source.read_bytes()
            source_table = tables(previous)['Items']
            source_nodes, _ = raw_index.tree(previous, source_table['physical_indexes'][0]['root'], source_table['root'])
            require([n['page'] for n in source_nodes if n['prefix']] == case['compressed_source_pages'] and case['compressed_source_pages'], 'Compressed source inventory')
            require(sum(n['stale_separators'] for n in source_nodes) == case['retained_source_separators'], 'Retained source separator inventory')
            require(observed['operation']['request'] == case['operation'] and observed['operation']['status'] == 'pass' and observed['operation']['before'] == identity(source), 'DAO equivalent continuation insert')
            normalized_roles = {}; images = {}
            for role, capture in observed['roles'].items():
                normalized_roles[role] = normalized(outbox, capture, case, expected)
                path = outbox / capture['file']; images[role] = identity(path)
                require(notes_identity(path.read_bytes()) == case['notes_pages'], 'Continuation unrelated Notes preservation')
            require(images['candidate'] == manifest['files'][case['candidate_file']] and images['control'] == observed['operation']['after'], 'Continuation output identities')
            require(normalized_roles['candidate'] == normalized_roles['control'], 'Complete continuation paired DAO semantics')
            layout = directory / (case['name'] + '-continued.layout.json')
            require(identity(layout) == manifest['files'][layout.name], 'Continuation layout identity')
            raw = raw_check((outbox / observed['roles']['candidate']['file']).read_bytes(), case, expected, json.loads(layout.read_text()), case['counter'], previous)
            report['cases'].append(dict(name=case['name'], images=images, raw=raw, compressed_source_pages=case['compressed_source_pages'], retained_source_separators=case['retained_source_separators']))
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        path = outbox / 'index-tree-mutation-continuation-report.json'; suffix = 1
        while path.exists():
            if json.loads(path.read_text()) == report:
                break
            suffix += 1; path = outbox / f'index-tree-mutation-continuation-report-{suffix}.json'
        else:
            write(path, report)
        print(path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('directory', type=Path); p.add_argument('revision')
    p = sub.add_parser('evaluate'); p.add_argument('directory', type=Path); p.add_argument('outbox', type=Path)
    p = sub.add_parser('prepare-continue'); p.add_argument('directory', type=Path); p.add_argument('first_outbox', type=Path); p.add_argument('output', type=Path); p.add_argument('generator', type=Path); p.add_argument('revision')
    args = parser.parse_args()
    if args.command == 'prepare':
        revision = subprocess.check_output(['git', 'rev-parse', args.revision], cwd=ROOT, text=True).strip()
        prepare(args.directory, revision)
    elif args.command == 'prepare-continue':
        revision = subprocess.check_output(['git', 'rev-parse', args.revision], cwd=ROOT, text=True).strip()
        prepare_continue(args.directory, args.first_outbox, args.output, args.generator, revision)
    elif json.loads((args.directory / 'index-tree-mutation.json').read_text())['round'] == 'continuation':
        evaluate_continue(args.directory, args.outbox)
    else:
        evaluate(args.directory, args.outbox)


if __name__ == '__main__':
    main()
