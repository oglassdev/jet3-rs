"""Memo/OLE row lifecycles (EXP-0234/0238/0240).

Four cases (memo, ole, mixed, auto-mixed) insert, edit, empty and reinsert long-value rows;
each phase is compared with a DAO control, released payload pages must be reused, and both
outputs take native writes. The continuation round lets Rust edit the native DAO output.
"""

from __future__ import annotations

import copy
from pathlib import Path
import shutil

import recipes
from registry import common
from registry import multiple_long_values as creation
from registry.common import identity, require

SCRIPT = common.REGISTRY / 'long_value_lifecycle.ps1'
MANIFEST = 'long-value-lifecycle.json'
PHASES = ['initial', 'inserted', 'edited', 'empty', 'reinserted']
LENGTHS = [33, 512, 2036, 2037, 32, None, 1, 4096, 33, 33, 12, 2048]
TAG_QUERIES = [[-44], [-33], [-20], [10], [90], [9000], [9010], [999999]]


def row(case, id, tag, seed, lengths=None):
    lengths = lengths if lengths is not None else [LENGTHS[(seed - 1 + 3 * c) % 12] for c in range(4)]
    values = [id, tag]
    for column, (_, kind, _, _) in enumerate(case['fields'][2:]):
        n = lengths[column]
        if n is None:
            values.append(None)
        elif kind == 12:
            values.append(''.join(chr(65 + (offset + seed + column) % 26) for offset in range(n)))
        else:
            values.append(bytes((offset * 17 + seed * 3 + column) % 256 for offset in range(n)).hex())
    return values


def recipe():
    cases = []
    for name, kinds, generated in [('memo', [12], False), ('ole', [11], False),
                                   ('mixed', [12, 11, 12, 11], False), ('auto-mixed', [12, 11, 12, 11], True)]:
        fields = [['Id', 4, 4, generated], ['Tag', 4, 4, False]] + [[n, t, 0, False] for n, t in zip(creation.NAMES, kinds)]
        indexes = [dict(name='ById', primary=True, unique=True, required=True, ignore=False, fields=[[0, False]],
                        queries=[[0], [1], [9], [13], [101], [9000], [9001], [999999]]),
                   dict(name='ByTag', primary=False, unique=False, required=False, ignore=False, fields=[[1, True]], queries=TAG_QUERIES),
                   dict(name='ByUniqueTag', primary=False, unique=True, required=False, ignore=False, fields=[[1, False]], queries=TAG_QUERIES)]
        case = dict(name=name, fields=fields, indexes=indexes, generated=generated, later=generated, initial_rows=[], long_columns=len(kinds))

        def insert(value):
            return dict(kind='insert', row=value)

        def replace(value):
            return dict(kind='replace', id=value[0], row=value)

        first = 13 if generated else 101
        case['phases'] = [
            dict(name='initial', operations=[]),
            dict(name='inserted', operations=[insert(row(case, id, id * 10, id)) for id in range(1, 13)]),
            dict(name='edited', operations=[dict(kind='delete', id=1), replace(row(case, 9, 90, 91, [1, 32, None, 12])),
                                            replace(row(case, 2, -20, 22, [4096, 2037, 33, 1])), replace(row(case, 6, 60, 66, [33, 32, 2036, 4096]))]),
            dict(name='empty', operations=[dict(kind='delete', id=id) for id in range(2, 13)]),
            dict(name='reinserted', operations=[insert(row(case, first + seed - 1, seed * 10, seed)) for seed in range(1, 13)])]
        case['native'] = [insert(row(case, 25 if generated else 9000, 9000, 900, [2037, 33, 4096, 32])),
                          replace(row(case, first, -33, 33, [33, 4096, None, 1])), dict(kind='delete', id=first + 1)]
        case['continue'] = [insert(row(case, 26 if generated else 9001, 9010, 901, [4096, 2036, 33, 2048])),
                            replace(row(case, first + 2, -44, 44, [12, 2036, 2048, None])), dict(kind='delete', id=first + 3)]
        cases.append(case)
    return cases


# Refused inserts after the `inserted` phase and the jet3-cli error text each must report.
REFUSALS = {'duplicate-later-index': 'duplicate unique key', 'caller-header': 'caller-supplied long-value header',
            'empty-payload': 'ZeroLengthNotAllowed', 'chain-budget': 'ChainDepth'}


def refusal_kinds(case):
    return ['duplicate-later-index', 'caller-header' if case['name'] == 'ole' else 'empty-payload', 'chain-budget']


def step(case, operation):
    """The jet3-cli mutation for one recipe operation; generated Ids are assigned on insert."""
    kind = operation['kind']
    locate = dict(locate=dict(table='Items', id=operation['id'])) if kind != 'insert' else {}
    if kind == 'delete':
        return dict(request=dict(operation='delete', table='Items'), **locate)
    return dict(request=dict(operation=kind, table='Items', values=creation.cells(case, operation['row'], kind == 'insert')), **locate)


def refusal(case, kind):
    """Row 999 with 8192-byte payloads: a duplicate ByUniqueTag, a raw long-value header, or a chain
    past a depth limit of 2; or with empty payloads."""
    values = creation.cells(case, row(case, 999, 90 if kind == 'duplicate-later-index' else 9990, 999,
                                      [0 if kind == 'empty-payload' else 8192] * 4), True)
    if kind == 'caller-header':
        values[2] = {'long_value': [0] * 12}
    limits = dict(limits={'chain-depth': 2}) if kind == 'chain-budget' else {}
    return dict(request=dict(operation='insert', table='Items', values=values), refused=REFUSALS[kind], **limits)


def candidates(spec=None):
    """`<case>-<phase>.mdb` for every phase, each continuing from the previous one, and
    `<case>-refused-<kind>.mdb` refusals taken from `<case>-inserted.mdb`."""
    images = []
    for case in recipe():
        previous = None
        for phase in case['phases']:
            file = f'{case["name"]}-{phase["name"]}.mdb'
            steps = [step(case, operation) for operation in phase['operations']]
            if previous is None:
                images.append({'file': file, 'steps': [creation.create(case, []), *steps]})
            else:
                images.append({'file': file, 'from': previous, 'steps': steps})
            previous = file
            if phase['name'] == 'inserted':
                images += [{'file': f'{case["name"]}-refused-{kind}.mdb', 'from': file, 'steps': [refusal(case, kind)]}
                           for kind in refusal_kinds(case)]
    return images


def states(case):
    rows = {}
    counters = creation.ENGINE.counters_for(case, rows)
    auto = 0
    result = {}
    for phase in case['phases'] + [dict(name='native', operations=case['native']), dict(name='continued', operations=case['continue'])]:
        for operation in phase['operations']:
            creation.ENGINE.apply(rows, operation, case, counters)
            if operation['kind'] == 'insert':
                auto += 1
        result[phase['name']] = (copy.deepcopy(rows), counters.copy(), auto)
    return result


def raw_check(data, receipt, case, expected):
    rows, counters, auto = expected
    creation.check_receipt(receipt, case, rows)
    layout = creation.raw_check(data, receipt, dict(case, generated=False), rows, counters, candidate=False)
    table = common.tables(data, ['Items'])['Items']
    if case['generated']:
        offset = table['root'] * common.PAGE + 16
        require(int.from_bytes(data[offset:offset + 4], 'little') == auto, 'Retained generated ID state')
        layout['auto_state'] = auto
    free = common.global_free(data)
    owned = {p for mapping in layout['long_value_maps'].values() for p in mapping['owned']}
    require(not owned & free, 'No owned payload page globally free')
    for page in layout['payload_storage'].values():
        require(len(page['kinds']) == 1, 'Separate live single/chained storage pools')
        if page['kinds'] == ['Chained']:
            require(not page['available'], 'Chained pages unavailable')
    layout['globally_free'] = sorted(free)
    return layout


def reuse_check(layouts, receipts, *, candidate=True):
    before = layouts['inserted']['payload_storage']
    after = layouts['edited']['payload_storage']
    deleted = next(r for r in receipts['inserted']['Items']['rows'] if r['values'][0] == 1)
    shared = [str(r['page']) for r in deleted['references'] if r['storage'] == 'SinglePage' and len(before[str(r['page'])]['slots']) > 1]
    require(shared and any(p in after and after[p]['slots'] for p in shared), 'Partial delete on a still-live shared single-value page')
    released = {p for m in layouts['edited']['long_value_maps'].values() for p in m['owned']}
    require(released and all(not m['owned'] and not m['available'] for m in layouts['empty']['long_value_maps'].values()),
            'Full clear releases all payload ownership')
    require(released <= set(layouts['empty']['globally_free']), 'Released payload pages globally free')
    reused = released & {p for m in layouts['reinserted']['long_value_maps'].values() for p in m['owned']}
    growth = layouts['reinserted']['file_pages'] - layouts['edited']['file_pages']
    if candidate:
        require(reused and growth <= 0, 'Reinsert reuses released pages without EOF growth')
    return dict(shared_partial_delete_pages=shared, released_payload_pages=sorted(released), reused_payload_pages=sorted(reused), file_growth_pages=growth)


def write_manifest(images, revision, cases, mode, **extra):
    files = {p.name: identity(p) for p in sorted(images.iterdir()) if p.is_file() and p.name != MANIFEST}
    common.write(images / MANIFEST, dict(document_type='long_value_lifecycle_inputs', source_revision=revision, mode=mode,
                                         cases=cases, files=files, **extra))


def prepare(images: Path, revision: str, spec: dict, results: dict) -> None:
    cases = recipe()
    for case in cases:
        expected = states(case)
        layouts, receipts, notes = {}, {}, None
        for phase in PHASES:
            image = images / f'{case["name"]}-{phase}.mdb'
            common.validate(image)
            receipt = creation.receipt(image)
            layouts[phase] = raw_check(image.read_bytes(), receipt, case, expected[phase])
            receipts[phase] = receipt
            actual = common.notes_identity(image.read_bytes())
            if notes is None:
                notes = actual
            require(actual == notes, 'All Notes-owned bytes preserved')
        case['layout'] = layouts
        case['reuse'] = reuse_check(layouts, receipts)
        case['notes_pages'] = notes
    refusals = []
    for case in cases:
        for kind in refusal_kinds(case):
            file = f'{case["name"]}-refused-{kind}.mdb'
            error = results[file][0]['refused']
            require(REFUSALS[kind] in error, 'Structured refusal: ' + kind)
            require(identity(images / file) == identity(images / f'{case["name"]}-inserted.mdb'), 'Refusal byte preservation')
            refusals.append(dict(case=case['name'], kind=kind, file=file, error=error, bytes_unchanged=True))
    write_manifest(images, revision, cases, 'lifecycle', refusals=refusals)


def prepare_continue(images: Path, outbox: Path, continued: Path, revision: str) -> None:
    parent = common.read(images / MANIFEST)
    require(parent['mode'] == 'lifecycle', 'Continuation parent mode')
    report = outbox.with_suffix('.comparison.json')
    first = common.read(report)
    require(first['status'] == 'accepted' and first['manifest'] == identity(images / MANIFEST)
            and first['result'] == identity(outbox / 'result.json'), 'Accepted retained first-round comparison')
    result = common.read(outbox / 'result.json')
    continued.mkdir(parents=True)
    cases = copy.deepcopy(parent['cases'])
    for case, outcome in zip(cases, result['cases']):
        require(case['name'] == outcome['name'], 'Parent case order')
        observation = outcome['phases'][-1]['captures']['control']
        source = outbox / observation['file']
        require(identity(source) == observation['after'], 'Native source image identity')
        local = continued / f'{case["name"]}-native-source.mdb'
        shutil.copy2(source, local)
        image = continued / f'{case["name"]}-continued.mdb'
        steps = [step(case, operation) for operation in case['continue']]
        recipes.build([{'file': image.name, 'from': local.name, 'steps': steps}], continued, continued.parent / 'requests')
        common.validate(image)
        receipt = creation.receipt(image)
        case['continued_layout'] = raw_check(image.read_bytes(), receipt, case, states(case)['continued'])
        case['source_notes'] = common.notes_identity(local.read_bytes())
        require(common.notes_identity(image.read_bytes()) == case['source_notes'], 'Rust native-input preserves Notes')
        case['source'] = identity(local)
    write_manifest(continued, revision, cases, 'continuation', parent_manifest=identity(images / MANIFEST),
                   parent_report=identity(report), parent_result=identity(outbox / 'result.json'))


def evaluate(images: Path, outbox: Path) -> dict:
    path = images / MANIFEST
    manifest = common.read(path)
    report = dict(status='failed', source_revision=manifest['source_revision'], mode=manifest['mode'], manifest=identity(path), cases=[], error=None)
    try:
        for name, pin in manifest['files'].items():
            require(identity(images / name) == pin, 'Input identity: ' + name)
        result = common.read(outbox / 'result.json')
        report['result'] = identity(outbox / 'result.json')
        common.check_result(result, path, 'dao_long_value_lifecycle_result', manifest['source_revision'])
        report['environment'] = result['environment']
        require([c['name'] for c in result['cases']] == [c['name'] for c in manifest['cases']], 'Case inventory')
        for case, outcome in zip(manifest['cases'], result['cases']):
            checked = dict(name=case['name'], status='failed', checkpoints=[], error=None)
            report['cases'].append(checked)
            try:
                compare_case(outbox, manifest, case, outcome, checked)
                checked['status'] = 'accepted'
            except Exception as error:
                checked['error'] = f'{type(error).__name__}: {error}'
        require(all(c['status'] == 'accepted' for c in report['cases']), 'One or more cases failed')
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    return report


def compare_case(outbox, manifest, case, outcome, checked):
    require(outcome['status'] == 'pass' and outcome['error'] is None, 'Case completed: ' + str(outcome['error']))
    continuation = manifest['mode'] == 'continuation'
    names = ['continued'] if continuation else PHASES + ['native']
    require([p['name'] for p in outcome['phases']] == names, 'Checkpoint inventory')
    notes, previous = {}, {}
    layouts, receipts = {'candidate': {}, 'control': {}}, {'candidate': {}, 'control': {}}
    expected = states(case)
    for phase in outcome['phases']:
        name = phase['name']
        rows = expected[name][0]
        compared, images, current = {}, {}, {}
        require(set(phase['captures']) == {'candidate', 'control'}, 'Paired roles')
        mutation_roles = {'candidate', 'control'} if name == 'native' else set() if name == 'initial' else {'control'}
        require(set(phase['mutations']) == mutation_roles, 'Native mutation receipt inventory')
        for role, observation in phase['captures'].items():
            image = outbox / observation['file']
            data = image.read_bytes()
            images[role] = identity(image)
            require(observation['before'] == observation['after'] == images[role], 'Closed capture identity')
            if role == 'candidate' and name != 'native':
                require(images[role] == manifest['files'][f'{case["name"]}-{name}.mdb'], 'Candidate checkpoint identity')
            if role in phase['mutations']:
                mutation = phase['mutations'][role]
                if continuation:
                    operations = case['continue']
                elif name == 'native':
                    operations = case['native']
                else:
                    operations = next(p['operations'] for p in case['phases'] if p['name'] == name)
                before = case['source'] if continuation else previous[role]
                require(mutation['operations'] == operations and mutation['before'] == before and mutation['after'] == images[role],
                        'Native operations and input/result identities')
            actual_notes = common.notes_identity(data)
            if role not in notes:
                notes[role] = case['source_notes'] if continuation else case['notes_pages'] if role == 'candidate' else actual_notes
            require(actual_notes == notes[role], 'All unrelated Notes metadata/data/LVAL page hashes')
            receipt = creation.receipt(image)
            receipts[role][name] = receipt
            current[role] = raw_check(data, receipt, case, expected[name])
            layouts[role][name] = current[role]
            compared[role] = creation.normalized(observation, case, rows)
            previous[role] = images[role]
        require(compared['candidate'] == compared['control'], 'Full paired schema/rows/payloads/index traversal/Seek')
        checked['checkpoints'].append(dict(name=name, status='accepted', rows=len(rows), files=images, layout=current))
    if not continuation:
        checked['reuse'] = {role: reuse_check(layouts[role], receipts[role], candidate=role == 'candidate') for role in ['candidate', 'control']}
    checked['notes_pages'] = notes
