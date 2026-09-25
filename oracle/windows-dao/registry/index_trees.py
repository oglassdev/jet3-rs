"""Index-tree mutations and DAO successors (EXP-0223/0225).

Five cases (primary, descending, empty, tombstone-empty, deep) grow, reorder, shrink and
collapse a single-Long-key tree; every Rust stage is decoded independently and compared with
a DAO control replaying the same recipe. A continuation round lets Rust insert into
prefix-compressed native DAO trees.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil

import recipes
from registry import common
from registry.common import identity, require

SCRIPT = common.REGISTRY / 'index_trees.ps1'
MANIFEST = 'index-tree-mutation.json'
QUERIES = [-2147483648, -3, -2, -1, 0, 1, 99, 199, 200, 13700, 27799, 27800, 27801, 1000000, 1000001, 1000002, 1234567, 2147483647]
TABLES = ['Items', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Notes']
CASES = ['primary', 'descending', 'empty', 'tombstone-empty', 'deep']


def base_row(id, deep):
    return [id, id * 17 + 3] if deep else [id, 'x' * 80, '11' * 8]


def recipe(name):
    deep = name in ('deep', 'tombstone-empty')
    count = 27800 if name == 'deep' else 3 if name == 'tombstone-empty' else 1 if name == 'empty' else 200
    added = 27800 if deep else -1
    stages = [dict(name='original', operations=[])]
    if name in ('empty', 'tombstone-empty'):
        if name == 'tombstone-empty':
            stages.extend([dict(name='delete-tail', operations=[dict(kind='delete', id=2)]),
                           dict(name='delete-first', operations=[dict(kind='delete', id=0)])])
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
        row = rows.pop(operation['id'])
        row[0] = operation['next_id']
        require(row[0] not in rows, 'Recipe duplicate key change')
        rows[row[0]] = row
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


def items_tree(data):
    table = common.tables(data, ['Items'])['Items']
    physical = table['physical_indexes'][0]
    return common.long_tree(data, physical['root'], table['root'], physical['keys'][0]['direction'] == 0)[0]


def raw_check(data, case, expected, counter, previous=None):
    table = common.tables(data, ['Items'])['Items']
    pages = table['data_pages']
    rows = common.table_rows(data, table)
    require(not table['long_value_pages'] and sorted(r['values'] for r in rows) == sorted(expected.values()), 'Complete raw rows')
    require(table['row_count'] == len(rows), 'Raw table row count')
    require(len(table['physical_indexes']) == len(table['logical_indexes']) == 1, 'Single index')
    physical = table['physical_indexes'][0]
    require(physical['keys'] == [dict(column=0, direction=int(not case['descending']))]
            and physical['flags'] == (1 if case['descending'] else 9), 'Index key definition')
    require(table['logical_indexes'][0]['name'] == 'ById', 'Logical index name')
    nodes, entries = common.long_tree(data, physical['root'], table['root'], case['descending'])
    wanted = [common.long_key(row['values'][0], case['descending']) + common.locator_bytes(row['page'], row['row']) for row in rows]
    require(entries == sorted(wanted), 'Complete raw key/locator entries')
    maps = sorted(common.map_pages(data, physical['map']))
    reached = sorted(node['page'] for node in nodes)
    require(set(reached) <= set(maps) and not set(maps).intersection(pages), 'Independent data and index ownership')
    for page in set(maps) - set(reached):
        image = common.page_bytes(data, page)
        require(previous is not None and image == common.page_bytes(previous, page)
                and image[0] in (3, 4) and int.from_bytes(image[4:8], 'little') == table['root'],
                'Mapped spare index page retains its previous bytes and owner')
    require(physical['entry_count'] == counter, 'Retained index counter increments on insertions')
    depth = max(node['depth'] for node in nodes)
    required_depth = 1 if len(rows) <= 200 else 2 if len(rows) <= 27800 else 3
    require(depth == required_depth, 'Declared 200/201 and 27800/27801 depth boundaries')
    return dict(rows=len(rows), depth=depth, index_pages=reached, reserved_index_pages=sorted(set(maps) - set(reached)),
                data_pages=pages, file_pages=len(data) // common.PAGE)


def cells(row, deep):
    if deep:
        return [{'long': value} for value in row]
    return [{'long': row[0]}, {'text': row[1]}, {'binary': list(bytes.fromhex(row[2]))}]


def step(operation, deep):
    """The jet3-cli mutation for one recipe operation."""
    kind = operation['kind']
    if kind == 'insert':
        return dict(request=dict(operation='insert', table='Items', values=cells(operation['row'], deep)))
    if kind == 'delete':
        request = dict(operation='delete', table='Items')
    elif kind == 'key':
        request = dict(operation='update', table='Items', column=0, value={'long': operation['next_id']})
    else:
        request = dict(operation='replace', table='Items', values=cells(operation['row'], deep))
    return dict(request=request, locate=dict(table='Items', id=operation['id']))


def candidates(spec: dict) -> list[dict]:
    """`<case>-<stage>.mdb` for every stage, each continuing from the previous one."""
    images = []
    for name in CASES:
        case = recipe(name)
        deep = case['deep']
        columns = [('Id', 'long'), ('Value', 'long')] if deep else [('Id', 'long'), ('Text', 'text'), ('Bytes', 'binary')]
        key = dict(column='Id', direction='descending' if case['descending'] else 'ascending')
        items = dict(name='Items', columns=[dict(name=n, type=t, **({} if t == 'long' else {'size': 80})) for n, t in columns],
                     indexes=[dict(name='ById', kind='unique' if case['descending'] else 'primary', fields=[key])],
                     rows=[cells(base_row(id, deep), deep) for id in range(case['initial_count'])])
        notes = dict(name='Notes', columns=[dict(name='Id', type='long'), dict(name='Body', type='memo')],
                     rows=[[{'long': 7}, {'memo': 'n' * 4096}], [{'long': 8}, None]])
        previous = None
        for stage in case['stages']:
            file = f"{name}-{stage['name']}.mdb"
            steps = [step(operation, deep) for operation in stage['operations']]
            if previous is None:
                images.append({'file': file, 'steps': [dict(command='create', request=dict(tables=[items, notes]))]})
            else:
                images.append({'file': file, 'from': previous, 'steps': steps})
            previous = file
    return images


def eof_insert(before: bytes, result: dict) -> bool:
    """Whether an insert's row went to the page appended at the previous end of file."""
    return result['row']['page'] * common.PAGE == len(before)


def prepare(images: Path, revision: str, spec: dict, results: dict) -> None:
    cases = [recipe(name) for name in CASES]
    files = {}
    for case in cases:
        original_notes = None
        previous = None
        for stage, expected in expected_stages(case):
            image = images / f"{case['name']}-{stage['name']}.mdb"
            data = image.read_bytes()
            raw_check(data, case, expected, stage['counter'], previous)
            common.validate(image)
            notes = common.notes_identity(data)
            if original_notes is None:
                original_notes = notes
            require(notes == original_notes, 'Notes definition, maps, data and LVAL pages unchanged: ' + image.stem)
            if stage['name'] == 'split' and not case['deep']:
                require(eof_insert(previous, results[image.name][0]), 'Combined data-EOF and index-node append')
            previous = data
            files[image.name] = identity(image)
        case['notes_pages'] = original_notes
    common.write(images / MANIFEST, dict(document_type='index_tree_mutation_inputs', round='mutations', source_revision=revision,
                                         cases=cases, files=files, queries=QUERIES))


def sidecar(outbox, reference):
    path = outbox / reference['file']
    require(identity(path) == {key: reference[key] for key in ('size', 'sha256')}, 'Sidecar identity: ' + path.name)
    return json.loads(path.read_text())


def normalized(outbox, capture, case, rows):
    require(capture['status'] == 'pass' and capture['error'] is None, 'DAO capture completed')
    require(capture['before'] == capture['after'] == identity(outbox / capture['file']), 'Read-only capture identity')
    value = copy.deepcopy(capture['snapshot'])
    require(value['version'] == '3.0' and value['tables'] == TABLES and value['queries'] == value['relations'] == [], 'Complete database inventory')
    require([t['name'] for t in value['user_tables']] == ['Items', 'Notes'], 'User table inventory')
    items, notes = value['user_tables']
    wanted_fields = [('Id', 4, 4), ('Value', 4, 4)] if case['deep'] else [('Id', 4, 4), ('Text', 10, 80), ('Bytes', 9, 80)]
    for actual, columns in [(items, wanted_fields), (notes, [('Id', 4, 4), ('Body', 12, 0)])]:
        require(actual['attributes'] == 0 and [(c['name'], c['type'], c['size']) for c in actual['fields']] == columns, 'Complete field schema')
    require(notes['indexes'] == [], 'Unindexed Notes sentinel')
    require(items['indexes'] == [dict(name='ById', primary=not case['descending'], unique=True, required=not case['descending'],
                                      ignore_nulls=False, foreign=False, fields=[dict(name='Id', attributes=int(case['descending']))])],
            'Complete index schema')
    item_rows = sidecar(outbox, items.pop('rows'))
    note_rows = sidecar(outbox, notes.pop('rows'))
    require(sorted(item_rows) == sorted(rows.values()) and sorted(note_rows) == common.NOTES,
            'Complete DAO rows and Memo sentinel content')
    traversal = sidecar(outbox, value.pop('traversal'))
    require(traversal == sorted(rows.values(), key=lambda row: row[0], reverse=case['descending']), 'Complete directed index traversal')
    require([seek['query'] for seek in value['seek']] == QUERIES, 'Seek query inventory')
    for seek in value['seek']:
        require(seek['row'] == rows.get(seek['query']), 'Seek exact row or absent result')
    # Content was checked in full; metadata and Seek rows remain for the paired comparison.
    return value


def evaluate(images: Path, outbox: Path) -> dict:
    manifest_path = images / MANIFEST
    manifest = common.read(manifest_path)
    continuation = manifest['round'] == 'continuation'
    report = dict(status='failed', round=manifest['round'], source_revision=manifest['source_revision'],
                  manifest=identity(manifest_path), cases=[], error=None)
    try:
        result = common.read(outbox / 'result.json')
        report['result'] = identity(outbox / 'result.json')
        common.check_result(result, manifest_path, 'dao_index_tree_mutation_result', manifest['source_revision'])
        require(result['round'] == manifest['round'], 'Result round')
        require([c['name'] for c in result['cases']] == [c['name'] for c in manifest['cases']], 'Case inventory')
        for case, observed in zip(manifest['cases'], result['cases']):
            outcome = dict(name=case['name'], status='failed', error=None)
            report['cases'].append(outcome)
            try:
                outcome.update((continuation_case if continuation else mutation_case)(images, outbox, manifest, case, observed))
                outcome['status'] = 'accepted'
            except Exception as error:
                outcome['error'] = str(error)
        require(all(case['status'] == 'accepted' for case in report['cases']), 'One or more cases failed')
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = str(error)
    return report


def mutation_case(images, outbox, manifest, case, observed):
    require(observed['status'] == 'pass' and observed['error'] is None, 'Case completed')
    require([p['name'] for p in observed['stages']] == [p['name'] for p in case['stages']], 'Checkpoint inventory')
    control_notes = None
    previous = None
    checkpoints = []
    for (stage, expected), observation in zip(expected_stages(case), observed['stages']):
        stem = f"{case['name']}-{stage['name']}"
        require(observation['operations'] == stage['operations'], 'Control operation recipe')
        require(set(observation['roles']) == {'candidate', 'control'}, 'Role inventory')
        snapshots, images_seen = {}, {}
        for role, record in observation['roles'].items():
            path = outbox / record['file']
            images_seen[role] = identity(path)
            require(images_seen[role] == record['image'], 'Retained checkpoint image')
            if role == 'candidate':
                require(images_seen[role] == manifest['files'][stem + '.mdb'], 'Rust checkpoint input identity')
                require(common.notes_identity(path.read_bytes()) == case['notes_pages'], 'Rust Notes page preservation')
            elif control_notes is None:
                control_notes = common.notes_identity(path.read_bytes())
            else:
                require(common.notes_identity(path.read_bytes()) == control_notes, 'Control Notes page preservation')
            if stage['capture']:
                snapshots[role] = normalized(outbox, record['capture'], case, expected)
            else:
                require(record['capture'] is None, 'Declared finite capture scope')
        if stage['capture']:
            require(snapshots['candidate'] == snapshots['control'], 'Complete paired DAO semantics')
        candidate = (outbox / observation['roles']['candidate']['file']).read_bytes()
        raw = raw_check(candidate, case, expected, stage['counter'], previous)
        previous = candidate
        checkpoints.append(dict(name=stage['name'], images=images_seen, raw=raw, dao_compared=stage['capture']))
    final_rows = expected
    for operation in case['native']:
        apply(final_rows, operation)
    require(set(observed['native']) == {'candidate', 'control'}, 'Native successor role inventory')
    native_snapshots = {}
    for role, native in observed['native'].items():
        require([o['request'] for o in native['operations']] == case['native'] and all(o['status'] == 'pass' for o in native['operations']),
                'Native insert/key-update/delete completed')
        chain = checkpoints[-1]['images'][role]
        for operation in native['operations']:
            require(operation['before'] == chain, 'Native mutation image chain')
            require(identity(outbox / operation['file']) == operation['after'], 'Retained native operation image')
            chain = operation['after']
        capture = native['capture']
        require(capture['before'] == chain, 'Native final capture input')
        native_snapshots[role] = normalized(outbox, capture, case, final_rows)
        expected_notes = case['notes_pages'] if role == 'candidate' else control_notes
        require(common.notes_identity((outbox / capture['file']).read_bytes()) == expected_notes, 'Native successor Notes page preservation')
    require(native_snapshots['candidate'] == native_snapshots['control'], 'Complete native successor paired semantics')
    return dict(checkpoints=checkpoints, native_rows=len(final_rows), notes_payload_sha256=hashlib.sha256(b'n' * 4096).hexdigest())


def prepare_continue(images: Path, first_outbox: Path, output: Path, revision: str) -> None:
    first = common.read(images / MANIFEST)
    result = common.read(first_outbox / 'result.json')
    require(result['manifest_sha256'] == identity(images / MANIFEST)['sha256'], 'Continuation source run')
    output.mkdir(parents=True, exist_ok=False)
    cases, files, outcomes = [], {}, []
    try:
        for name in ('primary', 'descending', 'deep'):
            case = next(c for c in first['cases'] if c['name'] == name)
            observed = next(c for c in result['cases'] if c['name'] == name)
            require(observed['status'] == 'pass', 'Source native case completed')
            capture = observed['native']['control']['capture']
            source = first_outbox / capture['file']
            source_data = source.read_bytes()
            expected = list(expected_stages(case))[-1][1]
            for operation in case['native']:
                apply(expected, operation)
            normalized(first_outbox, capture, case, expected)
            input_nodes = items_tree(source_data)
            compressed = [node['page'] for node in input_nodes if node['prefix']]
            require(compressed, 'Source contains prefix-compressed index nodes')
            stale = sum(node['stale_separators'] for node in input_nodes)
            require(name != 'descending' or stale > 0, 'Descending source retains a separator after native deletion')
            operation = dict(kind='insert', row=base_row(1234567, case['deep']))
            apply(expected, operation)
            source_name = name + '-continuation-source.mdb'
            shutil.copy2(source, output / source_name)
            file = name + '-continued.mdb'
            image = {'file': file, 'from': source_name, 'steps': [step(operation, case['deep'])]}
            inserted, = recipes.build([image], output, output.parent / 'requests')[file]
            outcomes.append(dict(name=name, source=identity(source), row=inserted['row']))
            files[file] = identity(output / file)
            files[source_name] = identity(output / source_name)
            common.validate(output / file)
            counter = common.tables(source_data, ['Items'])['Items']['physical_indexes'][0]['entry_count'] + 1
            raw_check((output / file).read_bytes(), case, expected, counter, source_data)
            notes = common.notes_identity(source_data)
            require(common.notes_identity((output / (name + '-continued.mdb')).read_bytes()) == notes, 'Continuation Notes bytes')
            cases.append(dict(name=name, deep=case['deep'], descending=case['descending'], source_file=source_name,
                              candidate_file=name + '-continued.mdb', operation=operation, expected=sorted(expected.values()),
                              counter=counter, notes_pages=notes, compressed_source_pages=compressed, retained_source_separators=stale))
        common.write(output / MANIFEST, dict(document_type='index_tree_mutation_inputs', round='continuation', source_revision=revision,
                                             parent_manifest=identity(images / MANIFEST), parent_result=identity(first_outbox / 'result.json'),
                                             cases=cases, files=files, queries=QUERIES))
    finally:
        common.write(output / 'continuation-preparation.json', outcomes)


def continuation_case(images, outbox, manifest, case, observed):
    require(observed['status'] == 'pass' and observed['error'] is None and set(observed['roles']) == {'candidate', 'control'},
            'Continuation case complete')
    expected = {row[0]: row for row in case['expected']}
    source = images / case['source_file']
    require(identity(source) == manifest['files'][source.name], 'Compressed source identity')
    previous = source.read_bytes()
    source_nodes = items_tree(previous)
    require([n['page'] for n in source_nodes if n['prefix']] == case['compressed_source_pages'] and case['compressed_source_pages'],
            'Compressed source inventory')
    require(sum(n['stale_separators'] for n in source_nodes) == case['retained_source_separators'], 'Retained source separator inventory')
    require(observed['operation']['request'] == case['operation'] and observed['operation']['status'] == 'pass'
            and observed['operation']['before'] == identity(source), 'DAO equivalent continuation insert')
    snapshots, images_seen = {}, {}
    for role, capture in observed['roles'].items():
        snapshots[role] = normalized(outbox, capture, case, expected)
        path = outbox / capture['file']
        images_seen[role] = identity(path)
        require(common.notes_identity(path.read_bytes()) == case['notes_pages'], 'Continuation unrelated Notes preservation')
    require(images_seen['candidate'] == manifest['files'][case['candidate_file']] and images_seen['control'] == observed['operation']['after'],
            'Continuation output identities')
    require(snapshots['candidate'] == snapshots['control'], 'Complete continuation paired DAO semantics')
    raw = raw_check((outbox / observed['roles']['candidate']['file']).read_bytes(), case, expected, case['counter'], previous)
    return dict(images=images_seen, raw=raw, compressed_source_pages=case['compressed_source_pages'],
                retained_source_separators=case['retained_source_separators'])
