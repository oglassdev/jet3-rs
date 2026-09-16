#!/usr/bin/env python3
"""Reproduce graph-creation candidates and a local DAO input bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile


def identity(path):
    data = path.read_bytes()
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True,
                        help='Built jet3 relationship_graph_candidate example')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-revision', required=True, help='Source used to build the candidate binary')
    parser.add_argument('--reference', type=Path,
                        help='Optional retained candidate directory for exact-byte comparison')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    matrix = args.output / 'matrix.json'
    shutil.copy2(args.matrix, matrix)
    plan = json.loads(matrix.read_text())
    expected = {f"{case['name']}-r{replica}.mdb"
                for case in plan['graphs'] for replica in range(1, plan['replicas'] + 1)}
    if len(expected) != len(plan['graphs']) * plan['replicas']:
        raise ValueError('Duplicate case names')
    candidates = args.output / 'candidates'
    run = subprocess.run([str(args.binary.resolve()), str(matrix.resolve()), str(candidates.resolve())],
                         capture_output=True, text=True)
    (args.output / 'candidate.stdout').write_text(run.stdout)
    (args.output / 'candidate.stderr').write_text(run.stderr)
    result = {'source_revision': args.source_revision, 'matrix_source_revision': plan['source_revision'], 'matrix': identity(matrix),
              'binary': identity(args.binary), 'exit': run.returncode, 'files': [], 'status': 'rejected'}
    if run.returncode == 0:
        if {p.name for p in candidates.iterdir()} != expected:
            raise ValueError('Generated candidate inventory differs from the matrix')
        for name in sorted(expected):
            item = {'name': name, **identity(candidates / name)}
            if args.reference:
                item['matches_reference'] = (candidates / name).read_bytes() == (args.reference / name).read_bytes()
            result['files'].append(item)
        if all(item.get('matches_reference', True) for item in result['files']):
            bundle = args.output / 'inputs.zip'
            with zipfile.ZipFile(bundle, 'x', compression=zipfile.ZIP_STORED) as archive:
                archive.write(matrix, 'matrix.json')
                for name in sorted(expected):
                    archive.write(candidates / name, 'candidates/' + name)
            result['bundle'] = identity(bundle)
            result['status'] = 'accepted'
    (args.output / 'preparation.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': result['status'], 'files': len(result['files']), 'output': str(args.output)}))
    return 0 if result['status'] == 'accepted' else 1


if __name__ == '__main__':
    raise SystemExit(main())
