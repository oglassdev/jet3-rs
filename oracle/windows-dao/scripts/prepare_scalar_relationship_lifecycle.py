#!/usr/bin/env python3
"""Apply closed scalar DAO operations to their exact native predecessors."""
import argparse
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import uuid
import zipfile

import system_catalog as catalog
from relationship_mutation_structure import rows as raw_rows
from prepare_scalar_relationship_creation import identity, write

TYPES = {1: 'boolean', 2: 'byte', 3: 'integer', 4: 'long', 5: 'currency',
         6: 'single', 7: 'double', 8: 'date_time', 9: 'binary', 10: 'text',
         11: 'long_binary', 12: 'memo', 15: 'guid'}


def cell(field, value, captured=False):
    if value is None:
        return None
    kind = field['type']
    if kind in (9, 10, 11, 12):
        value = bytes.fromhex(value)
        if kind == 10 and field['attributes'] & 1:
            value = value.ljust(field['size'], b' ')
        value = list(value)
    elif kind == 15:
        value = list(uuid.UUID(value).bytes)
    elif kind in (6, 7, 8) and (captured or isinstance(value, dict)):
        if isinstance(value, dict):
            assert set(value) == {'ieee_le'}
            value = value['ieee_le']
        value, = struct.unpack('<f' if kind == 6 else '<d', bytes.fromhex(value))
    return {TYPES[kind]: value}


def request(path, case, operation, snapshot):
    table, = [table for table in case['tables'] if table['name'] == operation['table']]
    fields = table['fields']
    result = {'operation': operation['kind'], 'table': table['name']}
    if operation['kind'] == 'insert':
        result['values'] = [cell(field, value) for field, value in zip(fields, operation['row'], strict=True)]
        return result
    data = path.read_bytes()
    raw_table, = [table for table in catalog.analyze_checkpoint(data)['tables'].values()
                  if table['name'] == operation['table']]
    row, = [row for row in raw_rows(data, raw_table) if row['values']['Id'] == operation['id']]
    result['row'] = {'page': row['locator']['page'], 'slot': row['locator']['row']}
    if operation['kind'] == 'delete':
        return result
    if operation['kind'] == 'replace':
        assert operation['row'][0] == operation['id']
        result['values'] = [cell(field, value) for field, value in zip(fields, operation['row'], strict=True)]
        return result
    assert operation['kind'] == 'field'
    ordinal, field = next((ordinal, field) for ordinal, field in enumerate(fields)
                           if field['name'] == operation['column'])
    captured_table, = [table for table in snapshot['tables'] if table['name']['value'] == operation['table']]
    captured, = [row for row in captured_table['rows'] if row['Id'] == operation['id']]
    fixed = field['type'] not in (1, 9, 10, 11, 12) or (field['type'] == 10 and field['attributes'] & 1)
    if fixed and captured[field['name']] is not None and operation['value'] is not None:
        result.update(operation='update', column=ordinal, value=cell(field, operation['value']))
    else:
        values = [cell(field, captured[field['name']], captured=True) for field in fields]
        values[ordinal] = cell(field, operation['value'])
        result.update(operation='replace', values=values)
    return result


def relationship_error(case, operation, snapshot):
    relation = case['relation']
    parent, = [table for table in case['tables'] if table['name'] == relation['parent']]
    ordinal, field = next((ordinal, field) for ordinal, field in enumerate(parent['fields'])
                          if field['name'] == relation['parent_field'])
    if operation['table'] == parent['name'] and operation['kind'] != 'insert':
        captured, = [table for table in snapshot['tables'] if table['name']['value'] == parent['name']]
        row, = [row for row in captured['rows'] if row['Id'] == operation['id']]
        old = row[field['name']]
        if operation['kind'] == 'delete':
            removed = True
        elif operation['kind'] == 'replace':
            removed = operation['row'][ordinal] is not None
        else:
            removed = operation['column'] == field['name'] and operation['value'] is not None
        if field['type'] != 1 and old is None and removed:
            return 'NullRelationshipConstraint'
    return 'RelationshipConstraint' if field['type'] == 4 else 'ScalarRelationshipConstraint'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-run', type=Path, required=True)
    parser.add_argument('--native-matrix', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--item', action='append', help='Select a closed lineage; omit to prepare all lineages.')
    args = parser.parse_args()
    outbox = args.native_run / 'outbox'
    assert (outbox / 'exit.txt').read_text().strip() == '0'
    matrix = json.loads(args.native_matrix.read_text())
    cases = {case['id']: case for case in matrix['cases']}
    planned = {item['id']: item for item in matrix['items']}
    workers_path = outbox / 'workers.json'
    workers = json.loads(workers_path.read_text(encoding='utf-8-sig'))
    assert workers['document_type'] == 'scalar_relationship_lifecycle_workers'
    assert workers['matrix'] == identity(args.native_matrix)
    assert workers['script'] == identity(args.native_run / 'inbox' / 'script.ps1')
    assert sorted(worker['worker'] for worker in workers['workers']) == [1, 2, 3, 4]
    records = {}
    environments = []
    for worker in workers['workers']:
        assert worker['file'] == f"worker-{worker['worker']}-result.json"
        receipt = outbox / worker['file']
        assert worker['exit_code'] == 0 and worker['identity'] == identity(receipt)
        result = json.loads(receipt.read_text(encoding='utf-8-sig'))
        assert result['status'] == 'pass' and result['error'] is None
        assert result['document_type'] == 'scalar_relationship_lifecycle_discovery'
        assert result['worker'] == worker['worker'] and result['script'] == workers['script']
        assert result['matrix'] == identity(args.native_matrix)
        environments.append(result['environment'])
        for record in result['items']:
            assert record['id'] not in records and record['producer_error'] is None
            records[record['id']] = (record, receipt)
    assert set(records) == set(planned)
    assert environments[0] is not None and all(value == environments[0] for value in environments)
    selected = set(args.item) if args.item is not None else set(planned)
    assert selected and selected <= set(planned)
    args.output.mkdir(parents=True, exist_ok=False)
    items, failures = [], []
    for item_id, planned_item in planned.items():
        if item_id not in selected:
            continue
        record, receipt = records[item_id]
        assert record['case'] == planned_item['case'] and record['replica'] == planned_item['replica']
        assert record['source'] == planned_item['source']
        predecessor = outbox / (item_id + '--initial.mdb')
        before = record['initial']['capture']
        assert identity(predecessor) == record['initial']['artifact'] == before['before'] == before['after']
        for event_index, (event, wanted) in enumerate(zip(record['events'], planned_item['events'], strict=True)):
            assert all(event[key] == wanted[key] for key in ('name', 'operation', 'isolated', 'expected_error'))
            assert event['before'] == identity(predecessor)
            expected_error = wanted['expected_error']
            if expected_error is None:
                assert event['error'] is None, 'unexpected native refusal'
            elif expected_error != 'observe':
                assert event['error'] is not None and expected_error in event['error']['numbers'], 'native refusal differs from plan'
            terminal_refusal = event['error'] is not None and not event['isolated']
            if terminal_refusal:
                assert expected_error == 'observe' and event_index + 1 == len(record['events']), 'failed native continuation'
            name = item_id + '--' + event['name']
            native = outbox / (name + '.mdb')
            assert identity(native) == event['artifact'] == event['after'] == event['capture']['before'] == event['capture']['after']
            target = args.output / (name + '.mdb')
            shutil.copyfile(predecessor, target)
            wanted_request = request(target, cases[record['case']], event['operation'], before['snapshot'])
            request_path = args.output / (name + '.request.json')
            write(request_path, wanted_request)
            applied = subprocess.run([str(args.binary), 'mutate', str(target), '--input', str(request_path)], capture_output=True, text=True)
            (args.output / (name + '.stdout')).write_text(applied.stdout)
            (args.output / (name + '.stderr')).write_text(applied.stderr)
            accepted = applied.returncode == 0
            if accepted != (event['error'] is None):
                failures.append({'id': name, 'kind': 'outcome', 'native_error': event['error'], 'stderr': applied.stderr})
            if not accepted:
                assert identity(target) == identity(predecessor), 'Rust refusal changed input'
                if event['error'] is not None:
                    numbers = set(event['error']['numbers'])
                    if numbers & {3200, 3201}:
                        variant = relationship_error(cases[record['case']], event['operation'], before['snapshot'])
                        matched_error = re.search(r'\b' + variant + r' \{', applied.stderr) is not None
                    elif 3022 in numbers:
                        matched_error = 'duplicate unique key' in applied.stderr
                    else:
                        matched_error = False
                    if not matched_error:
                        failures.append({'id': name, 'kind': 'refusal_type',
                                         'native_error': event['error'], 'stderr': applied.stderr})
            validated = subprocess.run([str(args.binary), 'validate', str(target)], capture_output=True, text=True)
            (args.output / (name + '.validate.stdout')).write_text(validated.stdout)
            (args.output / (name + '.validate.stderr')).write_text(validated.stderr)
            if validated.returncode:
                failures.append({'id': name, 'kind': 'validation', 'stderr': validated.stderr})
            items.append({'id': name, 'case': record['case'], 'replica': record['replica'], 'stage': event['name'],
                          'operation': event['operation'], 'request': wanted_request, 'request_identity': identity(request_path),
                          'exit': applied.returncode, 'accepted': accepted, 'native_error': event['error'],
                          'source_file': predecessor.name, 'source': identity(predecessor),
                          'native_file': native.name, 'native': identity(native), 'receipt': identity(receipt),
                          'file': target.name, 'identity': identity(target)})
            if terminal_refusal:
                assert event['continuation_identity'] == identity(native)
                continue
            if not event['isolated']:
                predecessor, before = native, event['capture']
            assert event['continuation_identity'] == identity(predecessor)
    result = {'document_type': 'scalar_relationship_same_input_preparation', 'source_revision': args.source_revision,
              'binary': identity(args.binary), 'native_matrix': identity(args.native_matrix), 'native_run': args.native_run.name,
              'native_workers': identity(workers_path), 'native_script': workers['script'],
              'provider_environment': environments[0],
              'selected_lineages': sorted(selected), 'unselected_lineages': sorted(set(planned) - selected),
              'items': items, 'failures': failures, 'status': 'prepared_not_dao_verified' if not failures else 'failed'}
    write(args.output / 'matrix.json', result)
    with zipfile.ZipFile(args.output / 'inputs.zip', 'x', compression=zipfile.ZIP_STORED) as archive:
        archive.write(args.output / 'matrix.json', 'matrix.json')
        for item in items:
            archive.write(args.output / item['file'], item['file'])
    print(json.dumps({'status': result['status'], 'stages': len(items), 'failures': len(failures)}))
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
