"""Practical Items/Notes lifecycle against native DAO (EXP-0229/0242).

Rust loads, replaces, deletes and reinserts Items rows (Text, Currency, Boolean) around a Notes
Memo sentinel; each stage is decoded independently and compared with a DAO control. Four
refusals must leave their source bytes unchanged.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from registry import common
from registry.common import identity, require

SCRIPT = common.REGISTRY / 'practical_lifecycle.ps1'
MANIFEST = 'practical-lifecycle.json'
QUERIES = list(range(-1, 261))
NOTES = common.NOTES
TABLES = ['Items', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Notes']
SCHEMA = {'Items': [['Id', 4, 4], ['Name', 10, 80], ['Price', 5, 8], ['Active', 1, 1]],
          'Notes': [['Id', 4, 4], ['Body', 12, 0]]}
REPLACEMENTS = [0, 1, 20, 21, 99, 199, 201, 219]
DELETIONS = [0, 1, 20, 21, 99, 100, 199, 219]


def item(id, replaced=False):
    if replaced:
        return [id, f'renamed-{id:04}' if id % 2 == 0 else f'edit-{id:04}-' + 'y' * 70,
                7654321 + id if id % 3 == 0 else None, id % 2 != 0]
    return [id, f'item-{id:04}-' + 'x' * 70, id * 10003 - 123456 if id % 3 else None, id % 2 == 0]


def recipe():
    insert = lambda id: dict(kind='insert', row=item(id))
    return [dict(name='lifecycle', stages=[
        dict(name='empty', operations=[]),
        dict(name='loaded', operations=[insert(id) for id in range(220)]),
        dict(name='replaced', operations=[dict(kind='replace', id=id, row=item(id, True)) for id in REPLACEMENTS]),
        dict(name='deleted', operations=[dict(kind='delete', id=id) for id in DELETIONS]),
        dict(name='reinserted', operations=[insert(id) for id in range(220, 260)]),
    ]), dict(name='reuse', stages=[
        dict(name='loaded', operations=[insert(id) for id in range(3)]),
        dict(name='empty', operations=[dict(kind='delete', id=id) for id in [2, 0, 1]]),
        dict(name='reinserted', operations=[insert(42)]),
    ])]


def expected_stages(case):
    rows = {}
    for stage in case['stages']:
        for operation in stage['operations']:
            if operation['kind'] == 'delete':
                del rows[operation['id']]
            else:
                row = operation['row']
                require((row[0] in rows) == (operation['kind'] == 'replace'), 'Recipe key precondition')
                rows[row[0]] = row
        yield stage, copy.deepcopy(rows)


def semantics(rows):
    return dict(items=sorted(rows.values()), notes=NOTES, schema=SCHEMA,
                traversal=sorted(rows.values()), seek=[dict(query=q, row=rows.get(q)) for q in QUERIES])


def raw_check(data, expected, receipt):
    table = common.tables(data, ['Items'])['Items']
    pages, long_values = table['data_pages'], table['long_value_pages']
    rows = common.table_rows(data, table)
    decoded = []
    for row in rows:
        value = row['values'].copy()
        if value[2] is not None:
            raw = bytes.fromhex(value[2]['raw_hex'])
            require(len(raw) == 8, 'Currency width')
            value[2] = int.from_bytes(raw, 'little', signed=True)
        decoded.append(value)
    require(not long_values and sorted(decoded) == sorted(expected.values()), 'Complete raw Items rows')
    require(table['row_count'] == len(expected), 'Declared live row count')
    physical = table['physical_indexes']
    require(len(physical) == len(table['logical_indexes']) == 1 and physical[0]['flags'] == 9 and
            physical[0]['keys'] == [dict(column=0, direction=1)] and table['logical_indexes'][0]['name'] == 'ById', 'Raw primary schema')
    nodes, entries = common.long_tree(data, physical[0]['root'], table['root'])
    wanted = [common.long_key(row['values'][0]) + common.locator_bytes(row['page'], row['row']) for row in rows]
    require(entries == sorted(wanted), 'Full raw index keys and row references')
    layout = dict(pages=len(data) // common.PAGE, data_pages=sorted(pages), index_pages=[n['page'] for n in nodes], depth=max(n['depth'] for n in nodes))
    require(layout == receipt['layout'], 'Rust and raw traversal layout')
    require(semantics(expected) == {k: receipt[k] for k in semantics(expected)}, 'Rust full reader/traversal/lookup receipt')
    return layout


def refusal_check(directory, files, notes):
    receipts = json.loads((directory / 'refusals.json').read_text())
    names = ['duplicate', 'wrong-value', 'malformed-source', 'resource']
    require([r['name'] for r in receipts] == names, 'Refusal inventory')
    original = (directory / 'lifecycle-loaded.mdb').read_bytes()
    for receipt in receipts:
        name = receipt['name']
        before = directory / f'refusal-{name}-before.mdb'
        after = directory / f'refusal-{name}-after.mdb'
        require(receipt['expected_error'] and receipt['preserved'] and receipt['error'] and
                before.read_bytes() == after.read_bytes(), 'Refusal error and exact source preservation: ' + name)
        expected = bytearray(original)
        if name == 'malformed-source':
            root = common.tables(original, ['Items'])['Items']['physical_indexes'][0]['root']
            expected[root * common.PAGE + 4:root * common.PAGE + 8] = b'\0' * 4
        require(before.read_bytes() == expected, 'Declared refusal source: ' + name)
        require(common.notes_identity(after.read_bytes()) == notes, 'Refusal Notes preservation')
        for path in (before, after):
            files[path.name] = identity(path)
        receipt.update(before=identity(before), after=identity(after))
    files['refusals.json'] = identity(directory / 'refusals.json')
    return receipts


def prepare(candidates: Path, revision: str, spec: dict, stdout: str) -> None:
    cases, files = recipe(), {}
    for case in cases:
        original_notes = None
        for stage, expected in expected_stages(case):
            stem = f"{case['name']}-{stage['name']}"
            path, snapshot = candidates / (stem + '.mdb'), candidates / (stem + '.snapshot.json')
            receipt = json.loads(snapshot.read_text())
            layout = raw_check(path.read_bytes(), expected, receipt)
            notes = common.notes_identity(path.read_bytes())
            if original_notes is None:
                original_notes = notes
                first_layout = layout
            require(notes == original_notes, 'Notes definition/maps/data/LVAL page hashes unchanged: ' + stem)
            if stem == 'lifecycle-loaded':
                require(len(layout['data_pages']) > 1 and layout['depth'] == 2, 'Crossed data and leaf boundaries')
            if stem == 'reuse-reinserted':
                require(layout['pages'] == first_layout['pages'] and layout['data_pages'] == first_layout['data_pages'], 'Released target page reuse without EOF growth')
            for file in (path, snapshot):
                files[file.name] = identity(file)
        case['notes_pages'] = original_notes
    refusals = refusal_check(candidates, files, cases[0]['notes_pages'])
    common.write(candidates / MANIFEST, dict(document_type='practical_lifecycle_inputs', source_revision=revision, cases=cases,
                                             files=files, queries=QUERIES, refusals=refusals))


def normalized(capture, expected):
    require(capture['status'] == 'pass' and capture['error'] is None, 'DAO capture completed')
    value = copy.deepcopy(capture['snapshot'])
    require(value['version'] == '3.0' and value['tables'] == TABLES
            and value['queries'] == value['relations'] == [], 'Complete database inventory')
    require([t['name'] for t in value['user_tables']] == ['Items', 'Notes'], 'User table inventory')
    items, notes = value['user_tables']
    for table in value['user_tables']:
        require(table['attributes'] == 0 and [[f['name'], f['type'], f['size']] for f in table['fields']] == SCHEMA[table['name']], 'Full column schema')
    require(items['fields'][2]['required'] is False, 'Price is nullable')
    require(notes['indexes'] == [] and items['indexes'] == [dict(name='ById', primary=True, unique=True, required=True,
            ignore_nulls=False, foreign=False, fields=[dict(name='Id', attributes=0)])], 'Full index schema')
    require(sorted(items['rows']) == sorted(expected.values()) and sorted(notes['rows']) == NOTES, 'Full DAO rows and sentinel')
    require(value['traversal'] == sorted(expected.values()) and value['seek'] == semantics(expected)['seek'], 'Complete DAO traversal and finite Seek inventory')
    items['rows'].sort(); notes['rows'].sort()
    return value


def evaluate(candidates: Path, outbox: Path) -> dict:
    manifest_path = candidates / MANIFEST
    manifest = common.read(manifest_path)
    report = dict(status='failed', source_revision=manifest['source_revision'], manifest=identity(manifest_path), cases=[], error=None)
    try:
        report['result'] = identity(outbox / 'result.json')
        for name, pin in manifest['files'].items():
            require(identity(candidates / name) == pin, 'Candidate input identity: ' + name)
        result = common.read(outbox / 'result.json')
        common.check_result(result, manifest_path, 'dao_practical_lifecycle_result', manifest['source_revision'])
        report['environment'] = result['environment']
        require([c['name'] for c in result['cases']] == [c['name'] for c in manifest['cases']], 'Case inventory')
        for case, observation in zip(manifest['cases'], result['cases']):
            outcome = dict(name=case['name'], status='failed', checkpoints=[], error=None)
            report['cases'].append(outcome)
            try:
                require(observation['status'] == 'pass' and observation['error'] is None, 'Case completed')
                require([s['name'] for s in observation['stages']] == [s['name'] for s in case['stages']], 'Checkpoint inventory')
                created = outbox / observation['created']['file']
                chain = identity(created)
                require(chain == observation['created']['image'], 'Retained native creation image')
                control_notes = common.notes_identity(created.read_bytes())
                for (stage, expected), capture in zip(expected_stages(case), observation['stages']):
                    stem = f"{case['name']}-{stage['name']}"
                    require(capture['operations'] == stage['operations'] and set(capture['roles']) == {'candidate', 'control'}, 'Operations and paired roles')
                    mutation = capture['control_mutation']
                    require(mutation['before'] == chain and mutation['count'] == len(stage['operations']), 'Native stage mutation chain')
                    normalized_roles = {}; images = {}
                    for role, record in capture['roles'].items():
                        path = outbox / record['file']; images[role] = identity(path)
                        require(record['before'] == record['after'] == images[role], 'Read-only capture and retained image identity')
                        normalized_roles[role] = normalized(record, expected)
                        notes = common.notes_identity(path.read_bytes())
                        if role == 'candidate':
                            require(images[role] == manifest['files'][stem + '.mdb'] and notes == case['notes_pages'], 'Rust input and Notes preservation')
                        else:
                            require(notes == control_notes, 'Native control Notes pages unchanged')
                    require(mutation['after'] == images['control'], 'Native stage output identity')
                    chain = images['control']
                    require(normalized_roles['candidate'] == normalized_roles['control'], 'All paired DAO metadata and contents')
                    receipt = json.loads((candidates / (stem + '.snapshot.json')).read_text())
                    raw = raw_check((outbox / capture['roles']['candidate']['file']).read_bytes(), expected, receipt)
                    outcome['checkpoints'].append(dict(name=stage['name'], images=images, rows=len(expected), layout=raw,
                                                       notes_pages=case['notes_pages'], control_notes_pages=control_notes))
                outcome['status'] = 'accepted'
            except Exception as error:
                outcome['error'] = str(error)
        refusal_files = {}
        report['refusals'] = refusal_check(candidates, refusal_files, manifest['cases'][0]['notes_pages'])
        require(report['refusals'] == manifest['refusals'], 'Refusal receipts unchanged')
        for name, pin in refusal_files.items():
            require(identity(outbox / name) == pin, 'Retained refusal artifact: ' + name)
        require(all(c['status'] == 'accepted' for c in report['cases']), 'One or more lifecycle cases failed')
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = str(error)
    return report
