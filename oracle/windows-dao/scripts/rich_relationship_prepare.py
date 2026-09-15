#!/usr/bin/env python3
"""Prepare Rust creations and mutations of both Rust and retained DAO inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

from rich_relationship_recipe import recipe


def identity(path):
    data = path.read_bytes()
    return dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')


def prepare(matrix, native, output, creation, mutation, revision):
    plan = json.loads(matrix.read_text())
    output.mkdir()
    shutil.copyfile(matrix, output / 'matrix.json')
    shutil.copyfile(Path(__file__).with_name('rich_relationship_lifecycle.ps1'),
                    output / 'acceptance.ps1')
    inputs = output / 'inputs'
    subprocess.run([str(creation), str(matrix), str(inputs)], check=True)
    (inputs / 'native').mkdir()
    (output / 'local').mkdir()
    sources = {}
    for case in plan['arms']:
        recipe_path = output / f"recipe-{case['name']}.json"
        write(recipe_path, recipe(case))
        for replica in range(1, plan['replicas'] + 1):
            stem = f"{case['name']}-r{replica}"
            retained = native / f'{stem}.mdb'
            receipt = json.loads((native / f'{stem}.json').read_text())
            if receipt['status'] != 'pass' or receipt['identity'] != identity(retained):
                raise ValueError(f'{stem}: retained DAO source identity')
            if receipt['matrix_sha256'] != identity(matrix)['sha256']:
                raise ValueError(f'{stem}: native creation matrix differs')
            shutil.copyfile(retained, inputs / 'native' / retained.name)
            for role, source in [('candidate', inputs / retained.name),
                                 ('native-rust', inputs / 'native' / retained.name)]:
                target = output / 'local' / f'{role}-{stem}'
                subprocess.run([str(mutation), str(source), str(recipe_path), str(target)],
                               check=True)
                sources[f'{stem}/{role}'] = identity(source)
    write(output / 'build-identity.json', dict(head=revision))
    write(output / 'sources.json', sources)
    with zipfile.ZipFile(output / 'acceptance-input.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        paths = [output / 'matrix.json', *output.glob('recipe-*.json'),
                 *inputs.rglob('*.mdb'), *(output / 'local').rglob('*.mdb')]
        for path in sorted(paths):
            archive.write(path, path.relative_to(output))
    print(output / 'acceptance-input.zip')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix', type=Path)
    parser.add_argument('native_outbox', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('creation_generator', type=Path)
    parser.add_argument('mutation_generator', type=Path)
    parser.add_argument('revision')
    args = parser.parse_args()
    prepare(args.matrix.resolve(), args.native_outbox.resolve(), args.output.resolve(),
            args.creation_generator.resolve(), args.mutation_generator.resolve(), args.revision)


if __name__ == '__main__':
    main()
