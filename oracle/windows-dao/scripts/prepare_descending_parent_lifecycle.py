#!/usr/bin/env python3
"""Apply each closed DAO lifecycle stage to its exact native predecessor."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import system_catalog as catalog
from relationship_mutation_structure import rows as raw_rows


def identity(path):
    data = path.read_bytes()
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def cell(column, value):
    if value is None:
        return None
    if column['type'] == 'Long':
        return {'long': value}
    if isinstance(value, str):
        value = value.encode('cp1252')
    return {{'Text': 'text', 'Memo': 'memo'}[column['type']]: list(value)}


def request(path, operation):
    data = path.read_bytes()
    table = next(t for t in catalog.analyze_checkpoint(data)['tables'].values()
                 if t['name'] == operation['table'])
    columns = table['definition']['columns']
    assert operation['kind'] in ('insert', 'field', 'replace', 'delete')
    result = {'operation': operation['kind'], 'table': operation['table']}
    if operation['kind'] == 'insert':
        result['values'] = [cell(c, operation['values'].get(c['name'])) for c in columns]
        return result
    row, = [r for r in raw_rows(data, table, primary_column='Id')
            if r['values']['Id'] == operation['id']]
    result['row'] = {'page': row['locator']['page'], 'slot': row['locator']['row']}
    if operation['kind'] == 'replace':
        assert set(operation['values']) <= {c['name'] for c in columns}
        values = row['values'] | operation['values']
        result['values'] = [cell(c, values[c['name']]) for c in columns]
    if operation['kind'] == 'field':
        column, = [c for c in columns if c['name'] == operation['column']]
        if row['values'][column['name']] is None or operation['value'] is None:
            # Null transitions use the documented full-row API.
            values = row['values'] | {column['name']: operation['value']}
            result.update(operation='replace', values=[cell(c, values[c['name']]) for c in columns])
        else:
            result.update(operation='update', column=column['ordinal'], value=cell(column, operation['value']))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native-run', type=Path, required=True)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-revision', required=True)
    args = p.parse_args()
    outbox = args.native_run / 'outbox'
    matrix_path = args.native_run / 'inbox/lifecycle.json'
    matrix = json.loads(matrix_path.read_text())
    args.output.mkdir(exist_ok=False)
    items, failures = [], []
    for case in matrix['cases']:
        for replica in range(1, matrix['replicas'] + 1):
            stem = f"r{replica}-{case['id']}"
            receipt_path = outbox / (stem + '-result.json')
            receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
            assert receipt['status'] == 'pass' and receipt['error'] is None
            assert receipt['matrix'] == identity(matrix_path)
            assert receipt['case'] == case['id'] and receipt['replica'] == replica
            assert len(receipt['stages']) == len(case['stages']) + 1
            previous = receipt['stages'][0]['artifact']
            comparisons = []
            for planned, native in zip(case['stages'], receipt['stages'][1:], strict=True):
                assert planned['name'] == native['name']
                comparisons.append((planned['name'], planned['operations'], previous, native['artifact'], None))
                previous = native['artifact']
            for planned, native in zip(case['refusals'], receipt['refusals'], strict=True):
                assert planned['name'] == native['name'] and native['source'] == previous['capture']['before']
                comparisons.append(('ref-' + planned['name'], [planned['operation']], previous, native['artifact'], native['error']))
            for label, operations, source_record, native_record, error in comparisons:
                name = stem + '-' + label
                source = outbox / source_record['file']
                native_path = outbox / native_record['file']
                assert identity(source) == source_record['capture']['before'] == source_record['capture']['after']
                assert identity(native_path) == native_record['capture']['before'] == native_record['capture']['after']
                target = args.output / (name + '.mdb')
                shutil.copyfile(source, target)
                requests = []
                for ordinal, operation in enumerate(operations):
                    wanted = request(target, operation)
                    rq = args.output / f'{name}-{ordinal}.request.json'
                    write(rq, wanted)
                    result = subprocess.run([str(args.binary), 'mutate', str(target), '--input', str(rq)], capture_output=True, text=True)
                    (args.output / f'{name}-{ordinal}.stdout').write_text(result.stdout)
                    (args.output / f'{name}-{ordinal}.stderr').write_text(result.stderr)
                    requests.append({'request': wanted, 'exit': result.returncode, 'stderr': result.stderr})
                    if result.returncode:
                        break
                accepted = all(r['exit'] == 0 for r in requests) and len(requests) == len(operations)
                if accepted != (error is None):
                    failures.append({'id': name, 'kind': 'outcome', 'native_error': error, 'requests': requests})
                if not accepted:
                    assert identity(target) == identity(source), name
                validation = subprocess.run([str(args.binary), 'validate', str(target)], capture_output=True, text=True)
                (args.output / (name + '.validate.stdout')).write_text(validation.stdout)
                (args.output / (name + '.validate.stderr')).write_text(validation.stderr)
                if validation.returncode:
                    failures.append({'id': name, 'kind': 'validation', 'stderr': validation.stderr})
                items.append({'id': name, 'case': case['id'], 'replica': replica, 'stage': label,
                              'operations': operations, 'requests': requests, 'accepted': accepted,
                              'native_error': error, 'source_file': source.name, 'source': identity(source),
                              'native_file': native_path.name, 'native': identity(native_path),
                              'file': target.name, 'identity': identity(target), 'receipt': identity(receipt_path)})
    result = {'document_type': 'descending_parent_same_input_preparation', 'source_revision': args.source_revision,
              'binary': identity(args.binary), 'native_matrix': identity(matrix_path),
              'native_run': args.native_run.name, 'items': items, 'failures': failures,
              'status': 'prepared_not_dao_verified' if not failures else 'failed'}
    write(args.output / 'matrix.json', result)
    with zipfile.ZipFile(args.output / 'inputs.zip', 'x', compression=zipfile.ZIP_STORED) as archive:
        archive.write(args.output / 'matrix.json', 'matrix.json')
        for item in items:
            archive.write(args.output / item['file'], item['file'])
    print(json.dumps({'status': result['status'], 'stages': len(items), 'failures': failures}))
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
