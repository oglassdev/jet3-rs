#!/usr/bin/env python3
"""Compare closed same-input DAO/Rust relationship lifecycles (EXP-0286)."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import zipfile

import relationship_index_checks as checks
from relationship_index_creation import PROVIDER, ident, req
from relationship_index_lifecycle import prefixes, raw_static, system_page_hashes
from relationship_mutation_structure import logical_relation
from larger_graph_lifecycle import tables_with_payload_placement_normalized
import relationship_system_indexes as system_indexes
import system_catalog as catalog

FAMILIES = ('main', 'null-positive', 'self-null', 'self-atomic',
            'self-parent-first', 'self-linked', 'self-anchor', 'self-order')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def capture_inventory(run):
    inbox, outbox = run / 'inbox', run / 'outbox'
    req({p.name for p in inbox.iterdir()} in (
        {'script.ps1', 'inputs.zip'}, {'script.ps1', 'inputs.zip', 'matrix.json'}), 'capture inbox')
    master = read(outbox / 'workers.json')
    workers = master['workers']
    req(master['document_type'] == 'descending_parent_lifecycle_capture_workers', 'capture master')
    req(master['archive'] == ident(inbox / 'inputs.zip')
        and master['script'] == ident(inbox / 'script.ps1'), 'capture inputs')
    expected_files = {'workers.json', 'exit.txt', 'log.txt'}
    for number in range(1, len(workers) + 1):
        expected_files |= {f'worker-{number}-result.json', f'worker-{number}-progress.json'}
    req({p.name for p in outbox.iterdir()} == expected_files, 'complete capture outbox')
    req((outbox / 'exit.txt').read_text().strip() == '0'
        and not (outbox / 'log.txt').read_text().strip(), 'capture exit/log')
    captures = {}
    with zipfile.ZipFile(inbox / 'inputs.zip') as archive:
        matrix_bytes = archive.read('matrix.json')
        matrix = json.loads(matrix_bytes)
        items = matrix['items']
        names = archive.namelist()
        req(len(names) == len(set(names)) and set(names) == {'matrix.json', *(i['file'] for i in items)},
            'exact capture ZIP inventory')
        req(all(Path(name).name == name for name in names), 'flat capture paths')
        req(matrix['status'] == 'prepared_not_dao_verified' and not matrix.get('failures'), 'preparation status')
        req(master['matrix'] == {'size': len(matrix_bytes), 'sha256': hashlib.sha256(matrix_bytes).hexdigest()},
            'captured matrix identity')
        if (inbox / 'matrix.json').exists():
            req((inbox / 'matrix.json').read_bytes() == matrix_bytes, 'submitted matrix')
        for number, worker in enumerate(workers, 1):
            filename = f'worker-{number}-result.json'
            req((worker['worker'], worker['file'], worker['exit_code']) == (number, filename, 0), 'worker order/status')
            path = outbox / filename
            req(ident(path) == worker['identity'], 'worker identity')
            receipt = read(path)
            req(receipt['status'] == 'pass' and receipt['error'] is None
                and receipt['environment'] == PROVIDER and receipt['worker'] == number, 'worker/provider')
            req(all(receipt[k] == master[k] for k in ('matrix', 'archive', 'script')), 'worker input linkage')
            assigned = items[number - 1::len(workers)]
            req([(c['id'], c['file']) for c in receipt['captures']]
                == [(i['id'], i['file']) for i in assigned], 'complete ordered readbacks')
            req(read(outbox / f'worker-{number}-progress.json')
                == {'worker': number, 'completed': len(assigned), 'last_id': assigned[-1]['id']}, 'final progress')
            for item, captured in zip(assigned, receipt['captures'], strict=True):
                data = archive.read(item['file'])
                identity = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                req(identity == item['identity'] == captured['capture']['before'] == captured['capture']['after'],
                    'read-only input identity')
                req(item['id'] not in captures, 'unique capture')
                captures[item['id']] = (item, captured['capture'])
    return captures, {'run': run.name, 'workers': ident(outbox / 'workers.json'),
                      'matrix': master['matrix'], 'archive': master['archive'], 'producer': master['script']}


def native_pair(run, item):
    receipt_path = run / 'outbox' / f"r{item['replica']}-{item['case']}-result.json"
    receipt = read(receipt_path)
    req(ident(receipt_path) == item['receipt'] and receipt['status'] == 'pass'
        and receipt['error'] is None and receipt['environment'] == PROVIDER, 'native receipt')
    recipe = read(run / 'inbox/lifecycle.json')
    req(receipt['matrix'] == ident(run / 'inbox/lifecycle.json'), 'native recipe identity')
    case, = [c for c in recipe['cases'] if c['id'] == item['case']]
    stages = receipt['stages']
    if item['stage'].startswith('ref-'):
        name = item['stage'][4:]
        planned, = [r for r in case['refusals'] if r['name'] == name]
        observed, = [r for r in receipt['refusals'] if r['name'] == name]
        before = stages[-1]['artifact']
        req(observed['source'] == item['source'], 'isolated probe source')
        operations, error = [planned['operation']], observed['error']
        after = observed['artifact']
    else:
        ordinal, = [i for i, s in enumerate(case['stages']) if s['name'] == item['stage']]
        before, after = stages[ordinal]['artifact'], stages[ordinal + 1]['artifact']
        operations, error = case['stages'][ordinal]['operations'], None
        req(stages[ordinal + 1]['name'] == item['stage'], 'native stage name')
    req(operations == item['operations'] and error == item['native_error'], 'same native operations/outcome')
    req(item['accepted'] == (error is None)
        == all(request['exit'] == 0 for request in item['requests']), 'Rust/DAO outcome')
    req(len(item['requests']) == len(operations), 'complete request sequence')
    if error:
        req(len(operations) == 1, 'one isolated refusal')
        code = 3201 if operations[0]['kind'] in ('insert', 'replace') else 3200
        req((error['numbers'] == [code]) or (error['numbers'] == [] and error['hresult'] & 0xffff == code),
            'exact expected native constraint error')
    for record, filename, identity in ((before, item['source_file'], item['source']),
                                       (after, item['native_file'], item['native'])):
        req(record['file'] == filename and ident(run / 'outbox' / filename)
            == identity == record['capture']['before'] == record['capture']['after'], 'native closed image')
    return before['capture'], after['capture']


def specs(snapshot):
    return {'tables': [{'name': checks.n(t), 'fields': [
        {'name': checks.n(f), 'type': f['type']} for f in t['fields']]} for t in snapshot['tables']]}


def expected_rows(raw, operations):
    result = {name: [copy.deepcopy(r['values']) for r in table['rows']]
              for name, table in raw['tables'].items() if not name.startswith('MSys')}
    for op in operations:
        table = raw['tables'][op['table']]
        rows = result[op['table']]
        columns = {c['name']: c for c in table['columns']}
        def encode(name, value):
            return value.encode('cp1252').hex() if value is not None and columns[name]['type'] == 'Memo' else value
        if op['kind'] == 'insert':
            rows.append({name: encode(name, op['values'].get(name)) for name in columns})
            continue
        selected, = [r for r in rows if r['Id'] == op['id']]
        if op['kind'] == 'delete':
            rows.remove(selected)
        elif op['kind'] == 'field':
            selected[op['column']] = encode(op['column'], op['value'])
        else:
            req(op['kind'] == 'replace', 'supported operation')
            selected.update({name: encode(name, value) for name, value in op['values'].items()})
    return result


def refusal_prefixes(before, operation):
    expected = prefixes(before)
    table = before['tables'][operation['table']]
    foreign, generated = set(), set()
    declared = {int.from_bytes(bytes.fromhex(i['raw_hex'])[4:8], 'little')
                for i in table['logical_indexes'] if i['class'] != 2}
    for logical in table['logical_indexes']:
        if logical['class'] == 2:
            relation = logical_relation(logical['raw_hex'])
            ordinal = relation['physical_index']
            if relation['side'] == 2:
                foreign.add(ordinal)
            if ordinal not in declared:
                generated.add(ordinal)
    if operation['kind'] == 'insert':
        req(foreign, 'refused insert foreign tree')
        for index in table['physical_indexes'][:min(foreign)]:
            key = b''.join(checks.component(operation['values'].get(table['columns'][f['column']]['name']),
                                            f['direction'] == 0) for f in index['keys'])
            old = {bytes.fromhex(entry)[:-4] for entry in index['entries_hex']}
            if key not in old:
                expected[operation['table']][index['index']][1] += 1
    else:
        assigned = ({c['name'] for c in table['columns']} if operation['kind'] == 'delete' else
                    {operation['column']} if operation['kind'] == 'field' else set(operation['values']))
        for index in table['physical_indexes']:
            ordinal = index['index']
            if ordinal in foreign | generated and any(table['columns'][f['column']]['name'] in assigned for f in index['keys']):
                first, second = expected[operation['table']][ordinal]
                if first:
                    expected[operation['table']][ordinal] = [first - 1, min(second, first - 1)]
    return expected


def snapshot_with_prefixes(snapshot, raw, expected):
    result = copy.deepcopy(snapshot)
    for table in result['tables']:
        name = checks.n(table)
        definitions = {i['name']: i for i in raw['tables'][name]['logical_indexes']}
        for index in table['indexes']:
            definition = definitions[checks.n(index)]
            ordinal = (logical_relation(definition['raw_hex'])['physical_index'] if definition['class'] == 2
                       else int.from_bytes(bytes.fromhex(definition['raw_hex'])[4:8], 'little'))
            prop, = [p for p in index['properties'] if checks.n(p) == 'DistinctCount']
            req(prop['type'] == 4 and not prop['is_null']
                and prop['value'] == str(raw['tables'][name]['physical_indexes'][ordinal]['second_word']),
                'actual DistinctCount getter/raw agreement')
            prop['value'] = str(expected[name][ordinal][1])
    return result


def requested_nulls(before, operations):
    result = set()
    for op in operations:
        if op['kind'] == 'delete':
            continue
        columns = before['tables'][op['table']]['columns']
        assigned = {op['column']: op['value']} if op['kind'] == 'field' else op['values']
        row_id = op['values']['Id'] if op['kind'] == 'insert' else op['id']
        for column in columns:
            if column['type'] == 'Long' and column['name'] in assigned and assigned[column['name']] is None:
                result.add((op['table'], row_id, column['name']))
    return result


def comparable(raw, nulls):
    result = copy.deepcopy(raw)
    result.pop('identity')
    result.pop('page0_relationship_byte')
    result['tables'] = tables_with_payload_placement_normalized(raw, set(), nulls)
    return result


def check_refusal_bytes(source, native, before, expected):
    old, new = source.read_bytes(), native.read_bytes()
    req(len(old) == len(new), 'refusal file length')
    allowed = {1538}
    analysis = catalog.analyze_checkpoint(old)
    for table in analysis['tables'].values():
        name = table['name']
        for index in table['definition']['physical_indexes']:
            ordinal = index['index']
            if prefixes(before)[name][ordinal] != expected[name][ordinal]:
                offset = index['entry_count_offset']
                allowed.update(range(offset - 4, offset + 4))
    req({i for i, (a, b) in enumerate(zip(old, new)) if a != b} <= allowed,
        'refusal changes only observed counters/header byte')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-root', type=Path, required=True)
    parser.add_argument('--native-root', type=Path, required=True)
    parser.add_argument('--capture-run', type=Path, action='append', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    pins = read(args.prepared_root / 'final-source-pins.json')
    captured, capture_runs = {}, []
    for run in args.capture_run:
        items, receipt = capture_inventory(run)
        req(not captured.keys() & items.keys(), 'disjoint capture runs')
        captured.update(items)
        capture_runs.append(receipt)
    results = []
    seen = set()
    for family in FAMILIES:
        prepared = args.prepared_root / f'lifecycle-{family}-final'
        matrix = read(prepared / 'matrix.json')
        req(matrix['source_revision'] == pins['source_revision'] and matrix['binary'] == pins['binary']
            and matrix['status'] == 'prepared_not_dao_verified' and not matrix['failures'], 'final preparation')
        native_run = args.native_root / 'runs' / matrix['native_run']
        req(matrix['native_matrix'] == ident(native_run / 'inbox/lifecycle.json'), 'native matrix pin')
        for item in matrix['items']:
            label = item['id']
            req(label not in seen, 'unique final case')
            seen.add(label)
            capture_item, rust_capture = captured[label]
            req(all(capture_item[key] == item[key] for key in
                    ('identity', 'source', 'native', 'receipt', 'operations', 'accepted', 'native_error')),
                label + ': captured/final source equivalence')
            req([r['request'] for r in capture_item['requests']] == [r['request'] for r in item['requests']],
                'reproduced request semantics')
            rust_path = prepared / item['file']
            source_path = native_run / 'outbox' / item['source_file']
            native_path = native_run / 'outbox' / item['native_file']
            req(ident(rust_path) == item['identity'], 'final Rust image')
            before_capture, native_capture = native_pair(native_run, item)
            schema = specs(before_capture['snapshot'])
            before = checks.observe_raw(source_path, schema, include_row_bytes=True)
            rust = checks.observe_raw(rust_path, schema, include_row_bytes=True)
            native = checks.observe_raw(native_path, schema, include_row_bytes=True)
            req(raw_static(before) == raw_static(rust) == raw_static(native), label + ': unchanged schema/catalog')
            operations = item['operations'] if item['accepted'] else []
            expected = expected_rows(before, operations)
            for role, raw in [('Rust', rust), ('DAO', native)]:
                actual = {name: [r['values'] for r in raw['tables'][name]['rows']] for name in expected}
                req(actual == expected, label + ': ' + role + ' complete requested rows')
            prior_system = system_page_hashes(source_path)
            prior_indexes = system_indexes.inventory(source_path)
            for path in (rust_path, native_path):
                req(system_page_hashes(path) == prior_system and system_indexes.inventory(path) == prior_indexes,
                    label + ': system storage preserved')
            selected = {(op['table'], op['values']['Id'] if op['kind'] == 'insert' else op['id']) for op in operations}
            for name, table in before['tables'].items():
                for row in table['rows']:
                    if (name, row['values'].get('Id')) not in selected:
                        for raw in (rust, native):
                            req(row in raw['tables'][name]['rows'], label + ': unselected row preserved')
            if item['accepted']:
                expected_counters = prefixes(native)
                req(prefixes(rust) == expected_counters, label + ': successful retained counters')
                nulls = requested_nulls(before, operations)
            else:
                req(rust_path.read_bytes() == source_path.read_bytes(), label + ': Rust refusal byte-exact')
                expected_counters = refusal_prefixes(before, item['operations'][0])
                req(prefixes(native) == expected_counters, label + ': exact native refusal counter effects')
                req(comparable(before, set()) == comparable(native, set()), label + ': native refusal poststate')
                check_refusal_bytes(source_path, native_path, before, expected_counters)
                nulls = set()
            req(comparable(rust, nulls) == comparable(native, nulls), label + ': complete raw state')
            req(snapshot_with_prefixes(rust_capture['snapshot'], rust, expected_counters)
                == snapshot_with_prefixes(native_capture['snapshot'], native, expected_counters),
                label + ': complete actual-property/rows/traversal/Seek snapshot')
            results.append({'id': label, 'family': family, 'accepted': item['accepted'], 'native_error': item['native_error'],
                            'source': item['source'], 'rust': item['identity'], 'native': item['native'],
                            'rust_prefixes': prefixes(rust), 'native_prefixes': prefixes(native),
                            'null_padding_fields': sorted(nulls)})
    req(seen == set(captured) and len(results) == 84, 'complete 84-pair coverage')
    report = {'document_type': 'descending_parent_lifecycle_acceptance', 'status': 'pass',
              'source_revision': pins['source_revision'], 'source_archive': pins['source_archive'],
              'binary': pins['binary'], 'evaluator': ident(Path(__file__)), 'provider': PROVIDER,
              'capture_runs': capture_runs, 'pairs': len(results),
              'successful': sum(r['accepted'] for r in results), 'refused': sum(not r['accepted'] for r in results),
              'results': results}
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('results', 'capture_runs')}))


if __name__ == '__main__':
    main()
