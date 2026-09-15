#!/usr/bin/env python3
"""Prepare saved-query inputs and check their preservation during row mutation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import rich_relationship_structure as raw
from rich_relationship_recipe import recipe
from rich_relationship_prepare import identity, write


def require(condition, message):
    if not condition:
        raise ValueError(message)


def system_storage_hashes(data, named, label):
    result = {}
    for name in ('MSysQueries', 'MSysObjects'):
        table = named[name]
        definition = table['definition']
        pages = set(definition['pages']) | set(table['data_pages'])
        locators = list(definition['maps'].values())
        locators.extend(index['map'] for index in definition['physical_indexes'])
        for group in definition['long_value_maps']:
            locators.extend((group['owned'], group['available']))
        for locator in locators:
            record = raw.map_record(data, locator, label + ' ' + name + ' map')
            pages.add(locator['page'])
            pages.update(page for page in record['record']['references'] if page)
            pages.update(record['members'])
        result[name] = dict(pages=sorted(pages), hashes={
            str(page): hashlib.sha256(raw.catalog._page(data, page, label + ' ' + name)).hexdigest()
            for page in sorted(pages)
        })
    return result


def preserved(observation, baseline, label):
    for key in ('queries', 'system_storage'):
        require(observation[key] == baseline[key], label + ' preserves ' + key)


def verify_seed(base, plan):
    seeded = base / 'seeded'
    names = {f"{case['name']}-r{replica}"
             for case in plan['arms'] for replica in range(1, plan['replicas'] + 1)}
    expected = {'seed-report.json', 'exit.txt', 'log.txt'} | {
        f'{name}-query-source.{suffix}' for name in names for suffix in ('json', 'mdb')}
    require({path.name for path in seeded.iterdir()} == expected, 'seed file inventory')
    require((seeded / 'exit.txt').read_text().strip() == '0', 'seed worker exit')
    require((seeded / 'log.txt').read_text() == '', 'seed worker log')
    report = json.loads((seeded / 'seed-report.json').read_text())
    require(report['status'] == 'pass', 'seed worker status')
    require(len(report['cases']) == len(names)
            and {case['case'] for case in report['cases']} == names, 'seed case inventory')
    provider = dict(culture='en-US', provider_version='03.60.9765.0',
                    provider_sha256='4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac',
                    os='Microsoft Windows NT 10.0.20348.0')
    require(all(report['environment'].get(key) == value for key, value in provider.items()),
            'seed provider environment')
    for receipt in report['cases']:
        stem = receipt['case']
        require(receipt['version'] == '3.0', stem + ' seed format version')
        require(receipt == json.loads((seeded / f'{stem}-query-source.json').read_text()),
                stem + ' complete seed receipt')
        require(receipt['after'] == identity(seeded / f'{stem}-query-source.mdb'),
                stem + ' seed image identity')


def verify_source(base, stem, stages, native):
    seed = json.loads((base / 'seeded' / f'{stem}-query-source.json').read_text())
    source = identity(base / 'inputs/native' / f'{stem}.mdb')
    baseline = stages['original']['control']
    require(seed['case'] == stem and seed['version'] == '3.0', stem + ' seed case/version')
    require(seed['after'] == source, stem + ' seeded source identity')
    require(baseline['queries'] == [seed['queries']], stem + ' complete seeded QueryDef metadata')
    require([query['name'] for query in seed['queries']] == [
        'Q Aggregate Count', 'Q Parameter', 'Q Simple Select', 'Q_Join_Child_Parent',
    ], stem + ' query inventory')
    require(all(query['type'] == 0 and query['returns_records'] for query in seed['queries']),
            stem + ' query types')
    parameters = [(p['name'], p['type'], p['direction'])
                  for query in seed['queries'] for p in query['parameters']]
    require(parameters == [('[pParent]', 4, 1)], stem + ' parameter inventory')
    for role, original in stages['original'].items():
        require(original['identity'] == source, stem + ' queries precede ' + role)
        for observation in [stage[role] for stage in stages.values()] + [native[role]]:
            preserved(observation, baseline, stem + '/' + role)


def prepare(matrix, seeded, output, generator, revision):
    plan = json.loads(matrix.read_text())
    output.mkdir()
    shutil.copyfile(matrix, output / 'matrix.json')
    shutil.copytree(seeded, output / 'seeded')
    shutil.copyfile(Path(__file__).with_name('query_preservation_lifecycle.ps1'),
                    output / 'acceptance.ps1')
    (output / 'inputs/native').mkdir(parents=True)
    (output / 'local').mkdir()
    for case in plan['arms']:
        recipe_path = output / f"recipe-{case['name']}.json"
        write(recipe_path, recipe(case))
        for replica in range(1, plan['replicas'] + 1):
            stem = f"{case['name']}-r{replica}"
            source = seeded / f'{stem}-query-source.mdb'
            receipt = json.loads((seeded / f'{stem}-query-source.json').read_text())
            require(receipt['after'] == identity(source), stem + ' seed identity')
            for directory in ('inputs', 'inputs/native'):
                shutil.copyfile(source, output / directory / f'{stem}.mdb')
            target = output / 'local' / f'candidate-{stem}'
            subprocess.run([str(generator), str(source), str(recipe_path), str(target)], check=True)
            # Both labels use the same native input and Rust mutations in this suite.
            shutil.copytree(target, output / 'local' / f'native-rust-{stem}')
    write(output / 'build-identity.json', dict(head=revision))
    with zipfile.ZipFile(output / 'acceptance-input.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        paths = [output / 'matrix.json', *output.glob('recipe-*.json')]
        for directory in ('inputs', 'local'):
            paths.extend(path for path in (output / directory).rglob('*') if path.is_file())
        for path in sorted(paths):
            archive.write(path, path.relative_to(output))
    print(output / 'acceptance-input.zip')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix', type=Path)
    parser.add_argument('seeded', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('generator', type=Path)
    parser.add_argument('revision')
    args = parser.parse_args()
    prepare(args.matrix.resolve(), args.seeded.resolve(), args.output.resolve(),
            args.generator.resolve(), args.revision)
