"""Definition chains (EXP-0247): Items definitions of exactly 2048/2049, 4088/4089 and
6128/6129 logical bytes, so the table definition fills or just overflows its root and one or
two continuation pages. Candidates must place continuations compactly before index roots;
rows, indexes and long-value maps are checked as in `registry.multiple_long_values`, and
both outputs take native writes in one x86 process per case.
"""

from __future__ import annotations

import json
from pathlib import Path

import structure
from registry import common
from registry import multiple_long_values as lval
from registry.common import identity, require

SCRIPT = common.REGISTRY / 'definition_chains.ps1'
GENERATOR = common.ROOT / 'target/debug/examples/creation_definition_chains_candidate'
MANIFEST = 'creation-definition-chains.json'
DOCUMENT = 'dao_creation_definition_chains_result'
CASES = [('root-full', 2048, 64, 3, 0, False, False, False), ('root-over', 2049, 64, 0, 0, False, False, True),
         ('one-full', 4088, 64, 205, 3, False, False, False), ('one-over', 4089, 64, 17, 3, True, False, True),
         ('two-full', 6128, 96, 3, 3, True, False, False), ('two-over', 6129, 96, 205, 3, True, True, True)]


def payload(id, binary, length=None):
    if length is None:
        length = {1: 32, 2: 33, 3: 2036, 4: 2037, 5: 4096}.get(id)
    if length is None:
        return None
    if binary:
        return bytes((17 * n + id) % 256 for n in range(length)).hex()
    return ''.join(chr(65 + (n + id) % 26) for n in range(length))


def row(id, columns, payloads):
    values = [id, id % 11 - 5] + [None if (id + n) % 7 == 0 else (id + n) % 251 if payloads else id * 1000 + n for n in range(2, columns)]
    if payloads:
        values[-2:] = [payload(id, False), payload(id, True)]
    return values


def recipe():
    cases = []
    for name, length, columns, count, index_count, payloads, generated, later in CASES:
        names = ['Id', 'Tag'] + [f'C{n:04}' for n in range(2, columns)]
        if payloads:
            names[-2:] = ['Body', 'Blob']
        # Column names are padded until the definition reaches exactly `length` bytes.
        base = 45 + 19 * columns + sum(map(len, names)) + (219 if index_count else 0) + (20 if payloads else 0)
        padded = columns - 2 - (2 if payloads else 0)
        for n in range(length - base):
            names[2 + n % padded] += 'x'
        require(max(map(len, names)) <= 64, 'Bounded existing name alphabet/width')
        fields = [[name, 2 if payloads and n > 1 else 4, 1 if payloads and n > 1 else 4, n == 0 and generated] for n, name in enumerate(names)]
        if payloads:
            fields[-2:] = [['Body', 12, 0, False], ['Blob', 11, 0, False]]
        inserted = row(count + 1 if generated else 9000, columns, payloads)
        inserted[1] = 99
        if payloads:
            inserted[-2:] = [payload(9000, False, 4096), payload(9000, True, 33)]
        target = 3 if count else inserted[0]
        replaced = row(target, columns, payloads)
        replaced[1] = -99
        replaced[2] = None
        if payloads:
            replaced[-2:] = [payload(3, False, 32), payload(3, True, 4096)]
        indexes = [dict(name='ById', primary=True, unique=True, required=True, ignore=False, fields=[[0, False]],
                        queries=[[0], [1], [3], [205], [inserted[0]], [999999]]),
                   dict(name='ByTag', primary=False, unique=False, required=False, ignore=False, fields=[[1, True]],
                        queries=[[-99], [-5], [0], [5], [99], [999999]]),
                   dict(name='ByPair', primary=False, unique=True, required=False, ignore=False, fields=[[1, False], [0, True]],
                        queries=[[-99, 3], [-2, 3], [99, inserted[0]], [999999, 1]])][:index_count]
        cases.append(dict(name=name, length=length, count=count, fields=fields, indexes=indexes, generated=generated, later=later,
                          initial_rows=[row(id, columns, payloads) for id in range(1, count + 1)],
                          native=[dict(kind='insert', row=inserted), dict(kind='replace', id=target, row=replaced),
                                  dict(kind='delete', id=2 if count else target)]))
    return cases


def raw_check(data, receipt, case, rows, counters, *, candidate):
    table = common.tables(data, ['Items'])['Items']
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
    long_columns = [n for n, f in enumerate(case['fields']) if f[1] in (11, 12)]
    groups = table['long_value_maps']
    require(sorted(g['column'] for g in groups) == long_columns, 'One map group per long column')
    data_pages = table['data_pages']
    require(not table['long_value_pages'], 'Table owns only its data pages')
    locators = {(r['page'], r['slot']): r['values'] for r in receipt['Items']['rows']}
    require(len(locators) == len(rows), 'Distinct live row references')
    require(all(page in data_pages for page, _ in locators), 'Logical rows live on owned data pages')
    if candidate:
        decoded = common.table_rows(data, table, wide=True)
        require({(r['page'], r['row']): r['values'][:2] for r in decoded} == {loc: values[:2] for loc, values in locators.items()},
                'Independent raw numeric values and row locators')
    logical = {i['name']: i['physical_index'] for i in table['logical_indexes']}
    require(set(logical) == {i['name'] for i in case['indexes']} and len(table['physical_indexes']) == len(logical), 'Raw index inventory')
    rust_indexes = {i['name']: i for i in receipt['Items']['indexes']}
    require(set(rust_indexes) == set(logical), 'Rust index inventory')
    require(not set(chain) & set(data_pages), 'Definition and row pages are separate')
    all_owned = set(data_pages) | set(chain)
    indexes = {}
    for index in case['indexes']:
        physical = table['physical_indexes'][logical[index['name']]]
        require(physical['keys'] == [dict(column=c, direction=int(not desc)) for c, desc in index['fields']]
                and physical['flags'] == int(index['unique']) + 8 * int(index['required']), 'Raw numeric index shape')
        nodes, entries = structure.tree(data, physical['root'], table['root'], [(4, desc) for _, desc in index['fields']])
        wanted = sorted(lval.ENGINE.key(values, case, index) + common.locator_bytes(page, slot) for (page, slot), values in locators.items())
        require(entries == wanted, 'All raw index keys and locators: ' + index['name'])
        rust = rust_indexes[index['name']]
        require([bytes.fromhex(k) + common.locator_bytes(p, r) for k, p, r in rust['entries']] == entries, 'Rust/raw complete tree records')
        owned = common.map_pages(data, physical['map'])
        reached = {node['page'] for node in nodes}
        require(reached <= owned and not owned & all_owned, 'Independent index/data pages')
        all_owned |= owned
        require(physical['entry_count'] == counters[index['name']], 'Retained distinct insertion counter')
        indexes[index['name']] = dict(depth=max(n['depth'] for n in nodes), pages=sorted(reached), owned=sorted(owned))
        require(rust['depth'] == indexes[index['name']]['depth'] and set(rust['nodes']) == reached, 'Rust/raw tree shape')
    maps = {}
    for group in groups:
        column = group['column']
        owned = common.map_pages(data, group['owned'])
        available = common.map_pages(data, group['available'])
        require(available <= owned and not owned & all_owned, 'Independent long-value page ownership')
        require(all(data[p * common.PAGE] == 1 and data[p * common.PAGE + 4:p * common.PAGE + 8] == b'LVAL' for p in owned), 'LVAL page kind/owner')
        all_owned |= owned
        if candidate:
            first = 2 + len(case['indexes']) + 2 * long_columns.index(column)
            require(group['owned'] == dict(page=table['root'] + 1, row=first) and group['available'] == dict(page=table['root'] + 1, row=first + 1),
                    'Candidate map row placement')
            lengths = [len(r[column]) // (2 if case['fields'][column][1] == 11 else 1) for r in rows.values() if r[column] is not None]
            count = sum(0 if n <= 32 else 1 if n <= 2036 else (n + 2031) // 2032 for n in lengths)
            require(len(owned) == count, 'Candidate external page count per column')
        maps[group['column_name']] = dict(owned=sorted(owned), available=sorted(available))
    if candidate and case['count'] > 200:
        require(len(data_pages) > 1 and all(i['depth'] > 1 for i in indexes.values()), 'Mixed data and three numeric leaf boundaries')
    if case['generated']:
        state = int.from_bytes(data[table['root'] * common.PAGE + 16:table['root'] * common.PAGE + 20], 'little', signed=True)
        require(state == max(rows), 'Persisted generated last ID')
    return dict(definition_pages=chain, logical_length=table['logical_length'], file_pages=len(data) // common.PAGE, data_pages=data_pages,
                indexes=indexes, long_value_maps=maps, payload_storage=lval.storage_inventory(data, receipt, table, maps))


def prepare(images: Path, revision: str, spec: dict, stdout: str) -> None:
    cases, files = recipe(), {}
    for case in cases:
        image, snapshot = [images / (case['name'] + suffix) for suffix in ('.mdb', '.snapshot.json')]
        receipt = json.loads(snapshot.read_text())
        rows, counters = lval.expected(case)
        lval.check_receipt(receipt, case, rows)
        case['layout'] = raw_check(image.read_bytes(), receipt, case, rows, counters, candidate=True)
        case['notes_pages'] = common.notes_identity(image.read_bytes())
        for path in (image, snapshot):
            files[path.name] = identity(path)
    common.write(images / MANIFEST, dict(document_type='creation_definition_chains_inputs', source_revision=revision, cases=cases, files=files))


def aggregate(outbox: Path) -> dict:
    return common.aggregate(outbox, [c[0] for c in CASES], DOCUMENT)


def evaluate(images: Path, outbox: Path) -> dict:
    manifest_path = images / MANIFEST
    manifest = common.read(manifest_path)
    report = dict(status='failed', source_revision=manifest['source_revision'], manifest=identity(manifest_path), cases=[], error=None)
    try:
        for name, pin in manifest['files'].items():
            require(identity(images / name) == pin, 'Input identity: ' + name)
        result = common.read(outbox / 'result.json')
        report['result'] = identity(outbox / 'result.json')
        common.check_result(result, manifest_path, DOCUMENT, manifest['source_revision'])
        report['environment'] = result['environment']
        require([c['name'] for c in result['cases']] == [c['name'] for c in manifest['cases']], 'Case inventory')
        for case, capture in zip(manifest['cases'], result['cases']):
            outcome = dict(name=case['name'], status='failed', checkpoints=[], error=None)
            report['cases'].append(outcome)
            try:
                lval.compare_case(outbox, manifest, case, capture, outcome, raw_check, GENERATOR)
                outcome['status'] = 'accepted'
            except Exception as error:
                outcome['error'] = f'{type(error).__name__}: {error}'
        require(all(c['status'] == 'accepted' for c in report['cases']), 'One or more definition-chain cases failed')
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    return report
