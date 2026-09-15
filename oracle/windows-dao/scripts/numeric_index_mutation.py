#!/usr/bin/env python3
"""Repeatable numeric/multiple-index mutations, native successors and continuation."""
import argparse
import copy
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess

from index_tree_mutation import identity, notes_identity, tables, require, write
import numeric_index_mutation_structure as raw_index
import scalar_index_mutation_rows as raw_rows

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_suffix('.ps1')
MANIFEST = 'numeric-index-mutation.json'
NOTES = [[7, 'n' * 4096], [8, None]]
SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 4, 7: 8, 8: 8, 9: 255}
CASE_NAMES = ('integral', 'wide', 'deep', 'dates', 'binary')


def initial_row(name, id):
    if name == 'integral':
        return [id, id % 7 if id % 11 else None, id % 5 - 2 if id % 13 else None, id % 2 == 0]
    if name == 'wide':
        return [id, id * 10001 if id % 17 else None, id + 0.5 if id % 19 else None, None]
    if name == 'dates':
        dates = [-2.75, -1.25, 0.0, 0.25, 0.5, 1.75, 36526.125, 36527.875]
        return [id, dates[id % 8] if id % 11 else None, 36526 + id / 4 if id % 19 else None, id % 13]
    if name == 'binary':
        widths = [1, 7, 8, 9, 17, 223, 224, 225, 247, 248, 249, 250, 251, 252, 253, 254, 255]
        payload = bytes((offset * 37 + id % 23) % 256 for offset in range(widths[id % len(widths)]))
        return [id, payload.hex() if id % 19 else None, id]
    if id < 3: return [id, None, None]
    if id == 3: return [id, None, 1.5]
    if id == 4: return [id, -100000, None]
    return [id, id, id + 0.5]


def recipe(name):
    field = lambda id, column, value: dict(kind='field', id=id, column=column, value=value)
    replace = lambda id, row: dict(kind='replace', id=id, row=row)
    if name == 'integral':
        types, count, additions, deletions, regrown = [4, 2, 3, 1], 195, range(195, 325), range(195), range(1000, 1195)
        edits = [field(0, 1, 250), field(1, 2, None), replace(2, [2, None, -32768, False]),
                 replace(3, [3, None, None, True]), field(4, 3, False), field(324, 0, 999)]
    elif name == 'wide':
        types, count, additions, deletions, regrown = [4, 5, 7, 6], 80, range(80, 92), [0, 17, 80], [120, 121, 122]
        edits = [field(2, 3, -1.5), replace(3, [3, 30003, 3.5, 2.25]), field(4, 1, None),
                 replace(5, [5, None, None, None]), field(6, 2, None), field(7, 0, 99)]
    elif name in ('dates', 'binary'):
        types = [4, 8, 8, 4] if name == 'dates' else [4, 9, 4]
        count, additions, deletions, regrown = 96, range(96, 220), range(160), range(1000, 1040)
        edits = ([field(1, 1, -1.75), field(2, 2, None), replace(3, [3, None, 36526.75, 3]), field(4, 3, -4)]
                 if name == 'dates' else [field(1, 1, 'ab'), field(2, 1, None), replace(3, [3, 'cd', 3]), field(4, 2, -40)])
        edits.append(field(219, 0, 999))
    else:
        types, count, additions, deletions, regrown = [4, 5, 7], 5673, [5673], [5673], [6000]
        edits = [field(5673, 1, -50000), replace(6, [6, None, None]), field(5, 2, None), field(7, 2, -1.25)]
    insert = lambda id: dict(kind='insert', row=initial_row(name, id))
    index = lambda name, columns, unique=False, primary=False, ignore=False: dict(
        name=name, fields=columns, unique=unique, primary=primary, required=primary, ignore=ignore)
    result = dict(name=name, fields=[[n, t, SIZES[t]] for n, t in zip(['Id', 'A', 'B', 'C'], types)],
                  indexes=[index('ById', [[0, False]], unique=True, primary=True),
                           index('ByPair', [[1, False], [2, True]], unique=name != 'integral', ignore=name == 'integral'),
                           index('ByLast', [[1 if name == 'binary' else 2 if name == 'deep' else 3, True]], unique=name == 'wide', ignore=name == 'wide')],
                  initial_rows=[initial_row(name, id) for id in range(count)], stages=[
                      dict(name='original', operations=[]), dict(name='grown', operations=[insert(id) for id in additions]),
                      dict(name='edited', operations=edits), dict(name='collapsed', operations=[dict(kind='delete', id=id) for id in deletions]),
                      dict(name='regrown', operations=[insert(id) for id in regrown])],
                  native=[insert(9000), field(9000, 0, 9001), dict(kind='delete', id={'integral': 195, 'wide': 120, 'deep': 8, 'dates': 1000, 'binary': 1000}[name])])
    samples = [initial_row(name, id) for id in [0, 1, 2, 3, 4, 5, 6, 7, 80, 120, 195, 324, 500, 999, 1000, 9000, 9001, 1234567, 1234568]]
    if name == 'wide': samples.extend([[0, 0, 0.5, n] for n in [-1.5, 2.25, 4.5]])
    for index in result['indexes']:
        # DAO Seek uses present scalar arguments; nullable entries are checked in full traversal.
        queries = [list(row[c] for c, _ in index['fields']) for row in samples]
        index['queries'] = list({json.dumps(q): q for q in queries if all(v is not None for v in q)}.values())
    return result


def component(value, kind, descending):
    if value is None:
        require(kind != 1, 'Boolean null is outside admitted input')
        result = b'\0'
    elif kind == 9:
        raw = bytes.fromhex(value)
        require(0 < len(raw) <= 255, 'Present Binary model length')
        mask = 255 if descending else 0
        result = bytearray([127 ^ mask])
        for offset in range(0, len(raw), 8):
            chunk = raw[offset:offset + 8]
            result.extend(b ^ mask for b in chunk + bytes(8 - len(chunk)))
            result.append(9 if offset + 8 < len(raw) else len(chunk) ^ mask)
        return bytes(result)
    elif kind == 1:
        require(type(value) is bool, 'Boolean model type')
        result = b'\x7f' + bytes([0 if value else 255])
    else:
        size = SIZES[kind]
        if kind in (6, 7, 8):
            raw = struct.pack('>f' if kind == 6 else '>d', value)
            bits = int.from_bytes(raw, 'big'); sign = 1 << (size * 8 - 1)
            require(math.isfinite(value), 'Excluded floating value')
            bits = (bits ^ ((1 << (size * 8)) - 1)) if bits & sign else bits ^ sign
        else:
            require(type(value) is int, 'Integer/scaled Currency model type')
            bits = int.from_bytes(value.to_bytes(size, 'big', signed=kind != 2), 'big')
            if kind != 2: bits ^= 1 << (size * 8 - 1)
        result = b'\x7f' + bits.to_bytes(size, 'big')
    return bytes(b ^ 255 for b in result) if descending else result


def key(row, case, index):
    if index['ignore'] and all(row[c] is None for c, _ in index['fields']): return None
    encoded = b''.join(component(row[c], case['fields'][c][1], desc) for c, desc in index['fields'])
    if len(encoded) > 255:
        state = 0
        for byte in encoded[253:]:
            for _ in range(8): state = ((state << 1) ^ (0x8005 if state & 0x8000 else 0)) & 65535
            state ^= byte
        encoded = encoded[:253] + state.to_bytes(2, 'little')
    return encoded


def counters_for(case, rows):
    return {index['name']: len({key(row, case, index) for row in rows.values()} - {None}) for index in case['indexes']}


def apply(rows, operation, case, counters):
    kind = operation['kind']
    if kind == 'insert':
        row = operation['row'].copy()
        require(row[0] not in rows, 'Recipe duplicate Id')
        for index in case['indexes']:
            value = key(row, case, index)
            if value is not None and all(key(old, case, index) != value for old in rows.values()):
                counters[index['name']] += 1
        rows[row[0]] = row
    elif kind == 'delete': del rows[operation['id']]
    else:
        old = rows.pop(operation['id'])
        row = operation['row'].copy() if kind == 'replace' else old
        if kind == 'field': row[operation['column']] = operation['value']
        require(row[0] not in rows, 'Recipe replacement Id')
        rows[row[0]] = row
    for index in case['indexes']:
        if not index['unique']: continue
        present = [key(row, case, index) for row in rows.values() if all(row[c] is not None for c, _ in index['fields'])]
        require(len(present) == len(set(present)), 'Recipe unique-key collision')


def expected_stages(case):
    rows = {row[0]: row.copy() for row in case['initial_rows']}
    counters = counters_for(case, rows)
    for stage in case['stages']:
        for operation in stage['operations']: apply(rows, operation, case, counters)
        yield stage, copy.deepcopy(rows), counters.copy()


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
    require(not lval and sorted(r['values'] for r in rows) == sorted(expected.values()), 'Complete raw scalar rows')
    require(table['row_count'] == len(expected), 'Declared live row count')
    require([[c['name'], c['type'], c['size']] for c in table['columns']] == [[name, catalog.PHYSICAL_TYPES[kind], size] for name, kind, size in case['fields']], 'Raw field schema')
    require(len(table['physical_indexes']) == len(table['logical_indexes']) == 3, 'Three physical/logical indexes')
    logical = {index['name']: index['physical_index'] for index in table['logical_indexes']}
    require(set(logical) == {i['name'] for i in case['indexes']} and len(set(logical.values())) == 3, 'Distinct named indexes')
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
    return dict(indexes=results, data_pages=sorted(pages), file_pages=len(data) // 2048, locators=locators)


def refusal_check(directory, notes):
    receipts = json.loads((directory / 'refusals.json').read_text())
    require([r['name'] for r in receipts] == ['later-insert', 'later-replace'], 'Later-index refusal inventory')
    source = (directory / 'wide-edited.mdb').read_bytes()
    for receipt in receipts:
        before = directory / f"refusal-{receipt['name']}-before.mdb"; after = directory / f"refusal-{receipt['name']}-after.mdb"
        require(receipt['preserved'] and receipt['error'] == 'Unsupported("duplicate unique key")' and
                before.read_bytes() == after.read_bytes() == source, 'Third-index duplicate error and byte preservation')
        require(notes_identity(after.read_bytes()) == notes, 'Refused operation Notes preservation')
    return receipts


def inputs():
    paths = [Path(__file__), SCRIPT, Path(raw_index.__file__), Path(raw_rows.__file__), ROOT / 'crates/jet3/examples/numeric_index_mutation_candidate.rs',
             ROOT / 'crates/jet3/examples/numeric_index_mutation_support/mod.rs',
             Path(__file__).with_name('index_tree_mutation.py'), Path(__file__).with_name('index_tree_mutation_structure.py'),
             Path(__file__).with_name('multi_level_index_structure.py'), Path(raw_index.catalog.__file__), Path(__file__).with_name('field_update.ps1')]
    return {str(p.relative_to(ROOT)): identity(p) for p in paths}


def prepare(candidates: Path, revision: str):
    cases = [recipe(name) for name in CASE_NAMES]
    for case in cases:
        previous = None; baseline_notes = None; original_layout = None
        for stage, expected, counters in expected_stages(case):
            stem = f"{case['name']}-{stage['name']}"; data = (candidates / (stem + '.mdb')).read_bytes()
            receipt = json.loads((candidates / (stem + '.snapshot.json')).read_text())
            layout = raw_check(data, case, expected, counters, receipt, previous if len(stage['operations']) == 1 else None)
            if baseline_notes is None: baseline_notes = notes_identity(data); original_layout = layout
            require(notes_identity(data) == baseline_notes, 'All Notes metadata/data/LVAL page hashes: ' + stem)
            if stem == 'integral-edited': require(layout['indexes']['ByPair']['counter'] == 47 and layout['indexes']['ByPair']['distinct'] == 49, 'Edits increase ordinary distinct keys above retained counter')
            if stem == 'wide-edited': require(layout['indexes']['ByLast']['counter'] == 0 and layout['indexes']['ByLast']['distinct'] == 2, 'Edits create keys above zero retained counter')
            if stem == 'integral-grown': require(all(i['depth'] == 2 for i in layout['indexes'].values()), 'All three integral indexes cross leaves')
            if stem == 'wide-grown': require(layout['indexes']['ByPair']['depth'] == 2, 'Wide composite leaf growth')
            if stem == 'deep-original': require(layout['indexes']['ByPair']['depth'] == 2, 'Variable-width depth2 boundary')
            if stem == 'deep-grown': require(layout['indexes']['ByPair']['depth'] == 3, 'Variable-width depth3 transition')
            if stem == 'integral-regrown':
                new = [p for id, p, _ in layout['locators'] if id >= 1000]
                retained = [p for id, p, _ in layout['locators'] if id < 1000]
                require(min(new) < max(retained) and min(new) == min(original_layout['data_pages']), 'Higher IDs reuse released lower data page')
            stage['counters'] = counters; previous = data
        case['notes_pages'] = baseline_notes
    refusals = refusal_check(candidates, cases[1]['notes_pages'])
    manifest = dict(document_type='numeric_index_mutation_inputs', round='mutations', source_revision=revision, cases=cases,
                    files={p.name: identity(p) for p in sorted(candidates.iterdir()) if p.suffix in ('.mdb', '.json') and p.name != MANIFEST}, inputs=inputs(), refusals=refusals)
    write(candidates / MANIFEST, manifest); shutil.copy2(SCRIPT, candidates / SCRIPT.name)
    return manifest


def normalized(capture, case, rows):
    require(capture['status'] == 'pass' and capture['error'] is None, 'DAO capture completed')
    value = copy.deepcopy(capture['snapshot'])
    require(value['version'] == '3.0' and value['tables'] == ['Items', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Notes'] and value['queries'] == value['relations'] == [], 'Complete DAO database inventory')
    require([t['name'] for t in value['user_tables']] == ['Items', 'Notes'], 'DAO user inventory')
    items, notes = value['user_tables']
    for table, fields in [(items, case['fields']), (notes, [['Id', 4, 4], ['Body', 12, 0]])]:
        require(table['attributes'] == 0 and [[f['name'], f['type'], f['size']] for f in table['fields']] == fields, 'Full DAO scalar schema')
    require(sorted(items['rows']) == sorted(rows.values()) and sorted(notes['rows']) == NOTES, 'Complete DAO rows and Memo content')
    expected_indexes = [dict(name=i['name'], primary=i['primary'], unique=i['unique'], required=i['required'], ignore_nulls=i['ignore'], foreign=False,
                             fields=[dict(name=case['fields'][c][0], attributes=int(d)) for c, d in i['fields']]) for i in case['indexes']]
    require(sorted(items['indexes'], key=lambda i: i['name']) == sorted(expected_indexes, key=lambda i: i['name']) and notes['indexes'] == [], 'Full DAO index schema')
    require(set(value['index_reads']) == {i['name'] for i in case['indexes']}, 'Complete DAO index traversal inventory')
    for index in case['indexes']:
        actual = value['index_reads'][index['name']]; selected = [r for r in rows.values() if key(r, case, index) is not None]
        require(sorted(actual['traversal']) == sorted(selected) and
                [key(r, case, index) for r in actual['traversal']] == sorted(key(r, case, index) for r in selected), 'Complete directed DAO traversal')
        require([s['query'] for s in actual['seek']] == index['queries'], 'Finite full-key Seek inventory')
        for seek in actual['seek']:
            query_row = [None] * len(case['fields'])
            for (column, _), query_value in zip(index['fields'], seek['query']): query_row[column] = query_value
            wanted_key = key(query_row, case, index)
            matches = [r for r in selected if key(r, case, index) == wanted_key]
            require(seek['row'] in matches if matches else seek['row'] is None, 'Seek returns complete matching row or absence')
            # Equal-key ties can select different physical rows in independently allocated files.
            seek['matches'] = sorted(matches); del seek['row']
        actual['traversal'].sort(key=lambda r: (key(r, case, index), r[0]))
    items['rows'].sort(); notes['rows'].sort(); items['indexes'].sort(key=lambda i: i['name'])
    return value


def retained_capture(outbox, capture, case, rows, counters, notes, receipt=None, previous=None):
    path = outbox / capture['file']; data = path.read_bytes(); image = identity(path)
    require(capture['before'] == capture['after'] == image, 'Read-only capture and retained image identity')
    require(notes_identity(data) == notes, 'Notes-owned pages preserved')
    normalized_snapshot = normalized(capture, case, rows)
    layout = raw_check(data, case, rows, counters, receipt, previous)
    return normalized_snapshot, dict(image=image, layout=layout), data


def retain_report(outbox, report):
    path = outbox / 'numeric-index-mutation-report.json'; suffix = 1
    while path.exists() and json.loads(path.read_text()) != report:
        suffix += 1; path = outbox / f'numeric-index-mutation-report-{suffix}.json'
    write(path, report); print(path)


def evaluate(candidates: Path, outbox: Path):
    manifest_path = candidates / MANIFEST; manifest = json.loads(manifest_path.read_text())
    result_path = outbox / 'result.json'; result = json.loads(result_path.read_text(encoding='utf-8-sig'))
    report = dict(document_type='dao_numeric_index_mutation_report', status='failed', round=manifest['round'], source_revision=manifest['source_revision'],
                  manifest=identity(manifest_path), result=identity(result_path), cases=[], error=None)
    try:
        require(manifest['inputs'] == inputs(), 'Current harness inputs')
        for name, pin in manifest['files'].items(): require(identity(candidates / name) == identity(outbox / name) == pin, 'Retained input identity: ' + name)
        require(result['document_type'] == 'dao_numeric_index_mutation_result' and result['manifest_sha256'] == report['manifest']['sha256'] and result['source_revision'] == manifest['source_revision'] and result['round'] == manifest['round'], 'Producer inputs and revision')
        require(result['error'] is None and result['retention_failures'] == [], 'Producer and retention completed')
        report['environment'] = result['environment']
        require(result['environment']['process_bits'] == 32 and result['environment']['provider'] == 'DAO.DBEngine.36' and result['environment']['provider_version'] == '3.6' and len(result['environment']['dll']['sha256']) == 64, 'Actual loaded DAO provider')
        require([c['name'] for c in result['cases']] == [c['name'] for c in manifest['cases']], 'Case inventory')
        for case, observed in zip(manifest['cases'], result['cases']):
            outcome = dict(name=case['name'], status='failed', checkpoints=[], error=None); report['cases'].append(outcome)
            try:
                require(observed['status'] == 'pass' and observed['error'] is None, 'Case completed')
                if manifest['round'] == 'continuation':
                    rows = {r[0]: r for r in case['expected']}; pairs = {}; checkpoints = {}
                    require(observed['operation']['count'] == len(case['operations']) and observed['operation']['before'] == manifest['files'][case['source_file']], 'Continuation operation input')
                    for role in ('candidate', 'control'):
                        receipt = json.loads((candidates / f"{case['name']}-continued.snapshot.json").read_text()) if role == 'candidate' else None
                        pairs[role], checkpoints[role], _ = retained_capture(outbox, observed['roles'][role], case, rows, case['counters'], case['notes_pages'], receipt)
                    require(checkpoints['candidate']['image'] == manifest['files'][case['candidate_file']] and checkpoints['control']['image'] == observed['operation']['after'], 'Continuation output identities')
                    require(pairs['candidate'] == pairs['control'], 'Paired continuation schema/rows/traversal/seeks')
                    outcome['checkpoints'].append(dict(name='continued', roles=checkpoints, compressed_source=case['compressed_source']))
                else:
                    require([s['name'] for s in observed['stages']] == [s['name'] for s in case['stages']], 'Checkpoint inventory')
                    created = outbox / observed['created']['file']; chain = identity(created)
                    require(chain == observed['created']['image'], 'Native creation image')
                    native_notes = notes_identity(created.read_bytes()); previous = None
                    for (stage, rows, counters), capture in zip(expected_stages(case), observed['stages']):
                        require(capture['operations'] == stage['operations'] and capture['mutation']['before'] == chain and capture['mutation']['count'] == len(stage['operations']), 'Native operation chain')
                        pairs = {}; checkpoints = {}; stem = f"{case['name']}-{stage['name']}"
                        for role in ('candidate', 'control'):
                            receipt = json.loads((candidates / (stem + '.snapshot.json')).read_text()) if role == 'candidate' else None
                            pairs[role], checkpoints[role], data = retained_capture(outbox, capture['roles'][role], case, rows, counters, case['notes_pages'] if role == 'candidate' else native_notes, receipt, previous if role == 'candidate' and len(stage['operations']) == 1 else None)
                            if role == 'candidate': previous = data
                        require(checkpoints['candidate']['image'] == manifest['files'][stem + '.mdb'] and checkpoints['control']['image'] == capture['mutation']['after'], 'Stage output identities')
                        require(pairs['candidate'] == pairs['control'], 'Paired full DAO metadata and contents')
                        chain = checkpoints['control']['image']; outcome['checkpoints'].append(dict(name=stage['name'], roles=checkpoints))
                    for operation in case['native']: apply(rows, operation, case, counters)
                    native_pairs = {}; native_details = {}
                    for role in ('candidate', 'control'):
                        native = observed['native'][role]
                        require(native['mutation']['count'] == len(case['native']) and native['mutation']['before'] == checkpoints[role]['image'], 'Native successor source')
                        native_pairs[role], native_details[role], _ = retained_capture(outbox, native['capture'], case, rows, counters, case['notes_pages'] if role == 'candidate' else native_notes)
                        require(native['mutation']['after'] == native_details[role]['image'], 'Native successor output')
                    require(native_pairs['candidate'] == native_pairs['control'], 'Native follow-up writes on both outputs')
                    outcome['native'] = native_details
                outcome['status'] = 'accepted'
            except Exception as error: outcome['error'] = str(error)
        if manifest['round'] == 'mutations':
            require(refusal_check(candidates, manifest['cases'][1]['notes_pages']) == manifest['refusals'], 'Refusal receipts')
            report['refusals'] = manifest['refusals']
        require(all(c['status'] == 'accepted' for c in report['cases']), 'One or more numeric mutation cases failed')
        report['status'] = 'accepted'
    except Exception as error: report['error'] = str(error)
    finally: retain_report(outbox, report)
    return report


def prepare_continue(candidates, first_outbox, output, generator, revision):
    first = json.loads((candidates / MANIFEST).read_text()); result = json.loads((first_outbox / 'result.json').read_text(encoding='utf-8-sig'))
    require(result['manifest_sha256'] == identity(candidates / MANIFEST)['sha256'], 'Continuation parent run')
    output.mkdir(parents=True, exist_ok=False); cases = []; receipts = []
    try:
        for name in CASE_NAMES:
            case = copy.deepcopy(next(c for c in first['cases'] if c['name'] == name)); observed = next(c for c in result['cases'] if c['name'] == name)
            capture = observed['native']['control']['capture']; source = first_outbox / capture['file']
            _, rows, counters = list(expected_stages(case))[-1]
            for operation in case['native']: apply(rows, operation, case, counters)
            normalized(capture, case, rows)
            require(identity(source) == capture['before'] == capture['after'], 'Native continuation source identity')
            source_layout = raw_check(source.read_bytes(), case, rows, counters)
            compressed = {name: index['compressed'] for name, index in source_layout['indexes'].items() if index['compressed']}
            if name in ('integral', 'deep'):
                require(any(index != 'ById' for index in compressed), 'Native source contains prefix-compressed non-Long/composite nodes')
            operations = [dict(kind='insert', row=initial_row(name, 1234567)), dict(kind='field', id=1234567, column=0, value=1234568), dict(kind='delete', id=9001)]
            for operation in operations: apply(rows, operation, case, counters)
            child = output / name
            done = subprocess.run([str(generator), 'continue', str(source), str(child), name], capture_output=True, text=True)
            (output / (name + '.stdout.log')).write_text(done.stdout); (output / (name + '.stderr.log')).write_text(done.stderr)
            receipts.append(dict(name=name, source=identity(source), returncode=done.returncode))
            require(done.returncode == 0, 'Rust native-input continuation: ' + name)
            source_name = name + '-continuation-source.mdb'; shutil.copy2(source, output / source_name)
            for suffix in ('.mdb', '.snapshot.json'): shutil.copy2(child / (name + '-continued' + suffix), output / (name + '-continued' + suffix))
            continued = (output / (name + '-continued.mdb')).read_bytes(); receipt = json.loads((output / (name + '-continued.snapshot.json')).read_text())
            raw_check(continued, case, rows, counters, receipt)
            notes = notes_identity(source.read_bytes()); require(notes_identity(continued) == notes, 'Continuation Notes-owned bytes')
            case.update(source_file=source_name, candidate_file=name + '-continued.mdb', expected=sorted(rows.values()), counters=counters,
                        operations=operations, notes_pages=notes, compressed_source=compressed)
            cases.append(case)
        manifest = dict(document_type='numeric_index_mutation_inputs', round='continuation', source_revision=revision, cases=cases, inputs=inputs(),
                        files={p.name: identity(p) for p in output.iterdir() if p.suffix in ('.mdb', '.json')},
                        parent_manifest=identity(candidates / MANIFEST), parent_result=identity(first_outbox / 'result.json'))
        write(output / MANIFEST, manifest); shutil.copy2(SCRIPT, output / SCRIPT.name)
    finally: write(output / 'continuation-preparation.json', receipts)


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('candidates', type=Path); p.add_argument('revision')
    p = sub.add_parser('evaluate'); p.add_argument('candidates', type=Path); p.add_argument('outbox', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare': prepare(args.candidates, subprocess.check_output(['git', 'rev-parse', args.revision], cwd=ROOT, text=True).strip())
    else: return int(evaluate(args.candidates, args.outbox)['status'] != 'accepted')


if __name__ == '__main__': raise SystemExit(main())
