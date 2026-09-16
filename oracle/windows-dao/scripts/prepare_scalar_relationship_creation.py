#!/usr/bin/env python3
"""Replay closed scalar relationship creation recipes through the Rust example."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
import zipfile


def identity(path):
    data = path.read_bytes()
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def graph(case):
    tables = []
    for table in case['tables']:
        rows = []
        for row in table['rows']:
            values = {}
            for field, value in zip(table['fields'], row, strict=True):
                if value is not None:
                    if field['type'] in (9, 10, 11, 12):
                        value = list(bytes.fromhex(value))
                    elif field['type'] == 15:
                        value = list(uuid.UUID(value).bytes)
                values[field['name']] = value
            rows.append(values)
        tables.append(table | {'rows': rows})
    relation = case['relation']
    return {'id': case['id'], 'name': case['id'], 'tables': tables, 'relations': [{
        'name': relation['name'], 'table': relation['parent'], 'field': relation['parent_field'],
        'foreign_table': relation['child'], 'foreign_field': relation['child_field'],
    }]}


def run(binary, matrix, output, prefix):
    result = subprocess.run([str(binary), str(matrix), str(output)], capture_output=True, text=True)
    prefix.with_suffix('.stdout').write_text(result.stdout)
    prefix.with_suffix('.stderr').write_text(result.stderr)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix', type=Path, required=True)
    parser.add_argument('--discovery-report', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text())
    discovery = json.loads(args.discovery_report.read_text())
    assert discovery['status'] == 'accepted' and discovery['matrix'] == identity(args.matrix)
    args.output.mkdir(parents=True, exist_ok=False)
    accepted, refused = [], []
    for case in matrix['cases']:
        observations = [item for item in discovery['results'] if item['case'] == case['id']]
        assert len(observations) == 2 and {item['replica'] for item in observations} == {1, 2}
        assert len({item['accepted'] for item in observations}) == 1
        (accepted if observations[0]['accepted'] else refused).append(graph(case))
    wanted = {'replicas': 2, 'graphs': accepted}
    matrix_path = args.output / 'matrix.json'
    write(matrix_path, wanted)
    candidates = args.output / 'candidates'
    result = run(args.binary, matrix_path, candidates, args.output / 'creation')
    assert result.returncode == 0, result.stderr
    expected = {f"{case['name']}-r{replica}.mdb" for case in accepted for replica in (1, 2)}
    assert {path.name for path in candidates.iterdir()} == expected
    refusals = []
    for case in refused:
        for replica in (1, 2):
            name = f"refused-{case['id']}-r{replica}"
            request = args.output / (name + '.json')
            write(request, {'replicas': 1, 'graphs': [case]})
            destination = args.output / name
            result = run(args.binary, request, destination, args.output / name)
            assert result.returncode != 0 and 'UnsupportedRelationship' in result.stderr
            assert destination.is_dir() and not list(destination.iterdir())
            refusals.append({'case': case['id'], 'replica': replica, 'request': identity(request),
                             'exit': result.returncode, 'stderr': result.stderr, 'no_output': True})
    files = [{'name': path.name, **identity(path)} for path in sorted(candidates.iterdir())]
    preparation = {'document_type': 'scalar_relationship_creation_preparation',
                   'source_revision': args.source_revision, 'binary': identity(args.binary),
                   'discovery_matrix': identity(args.matrix), 'discovery_report': identity(args.discovery_report),
                   'matrix': identity(matrix_path), 'files': files, 'refusals': refusals,
                   'status': 'prepared_not_dao_verified'}
    write(args.output / 'preparation.json', preparation)
    with zipfile.ZipFile(args.output / 'inputs.zip', 'x', compression=zipfile.ZIP_STORED) as archive:
        archive.write(matrix_path, 'matrix.json')
        for item in files:
            archive.write(candidates / item['name'], 'candidates/' + item['name'])
    print(json.dumps({'status': preparation['status'], 'images': len(files), 'refusals': len(refusals)}))


if __name__ == '__main__':
    main()
