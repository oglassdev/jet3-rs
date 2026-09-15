#!/usr/bin/env python3
"""Complete overflow mutation comparisons from retained native EXP-0262 inputs."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess

import wide_row_lifecycle as wide
from index_tree_mutation import identity, notes_identity, require, write

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_suffix('.ps1')
MANIFEST = 'row-overflow-lifecycle.json'
CASES = ('ordinary', 'payloads', 'payloads-native')
SOURCE_PINS = {
    'ordinary': dict(size=65536, sha256='0151ee9bcfd6c3e2a99960fc6b9da165cdaa04a23cbd479ab56e519cb455e93c'),
    'payloads': dict(size=90112, sha256='8ece5079aa93d43283c262ec27aa4d5fbd360a3771c9b60725a3c40a86445a4b'),
    'payloads-native': dict(size=90112, sha256='f0fcaca0b39552a83729f81400675f316acfa8b975a7c6d45094ced13b4e5894'),
}
engine = wide.engine
original_apply = engine.apply
original_inputs = engine.inputs
catalog = wide.structure.catalog


def payload(length, id, column, salt=0):
    alphabet = b'aAezZ\xe9\xc9\xc6\xe6\xdf\x8a\x9a'
    return bytes(alphabet[(n * 7 + id * 13 + column * 31 + salt * 5) % len(alphabet)] if column in (1, 3)
                 else (n * 37 + id * 13 + column * 31 + salt * 17) % 256 for n in range(length)).hex()


def row(name, id, width=12):
    result = [id, payload(width, id, 1), payload(width, id, 2)]
    if name != 'ordinary': result += [payload(80, id, 3), payload(80, id, 4)]
    return result


def apply(rows, operation, case, counters):
    require(operation['kind'] in ('short', 'insert', 'delete', 'field', 'replace'), 'Known operation')
    if operation['kind'] == 'short':
        values = rows[operation['id']].copy()
        values[1:3] = [operation['text'], operation['binary']]
        operation = dict(kind='replace', id=operation['id'], row=values)
    original_apply(rows, operation, case, counters)


def recipe(name):
    selected = [0, 27, 55, 71] if name == 'ordinary' else [0, 15, 31, 71]
    renamed = selected[2] + 900
    short = lambda id, width, salt=0: dict(kind='short', id=id,
        text=payload(width, id - 900 if id >= 900 else id, 1, salt),
        binary=payload(width, id - 900 if id >= 900 else id, 2, salt))
    insert = lambda id, width: dict(kind='insert', row=row(name, id, width))
    delete = lambda id: dict(kind='delete', id=id)
    indexes = [dict(name='ById', fields=[[0, False]], primary=True, unique=True, required=True, ignore=False)]
    if name != 'ordinary':
        indexes.append(dict(name='ByText', fields=[[1, False]], primary=False, unique=False, required=False, ignore=False))
    initial = [row(name, id, 180 if name == 'payloads-native' and id in selected else 12) for id in range(72)]
    stages = [dict(name='original', operations=[]),
              dict(name='grown', operations=[short(id, 180) for id in selected]),
              dict(name='equal', operations=[short(id, 180, 1) for id in selected[:2]]),
              dict(name='keyed', operations=[dict(kind='field', id=selected[2], column=0, value=renamed)]),
              dict(name='shrunken', operations=[short(id, 1, 2) for id in selected[:2]]),
              dict(name='filled', operations=[insert(id, 12) for id in range(1000, 1013)]),
              dict(name='relocated', operations=[short(id, 255, 3) for id in [*selected[:2], renamed, selected[3]]]),
              dict(name='shared', operations=[insert(id, 150) for id in range(2000, 2008)]),
              dict(name='deleted', operations=[delete(id) for id in [*selected[:2], renamed, selected[3]]]),
              dict(name='released', operations=[delete(id) for id in [*range(1000, 1013), *range(2000, 2008)]]),
              dict(name='reinserted', operations=[insert(id, 255) for id in range(3000, 3008)])]
    native = [insert(9000, 180), dict(kind='field', id=9000, column=0, value=9001), delete(3000)]
    samples = initial + [row(name, id, width) for ids, width in [(range(1000,1013),12), (range(2000,2008),150),
               (range(3000,3008),255), ([9000,9001,renamed,7777],180)] for id in ids]
    for stage in stages:
        for op in stage['operations']:
            if op['kind'] == 'short':
                value = row(name, op['id']); value[1:3] = [op['text'], op['binary']]; samples.append(value)
    for index in indexes:
        index['queries'] = list({json.dumps([r[c] for c,_ in index['fields']]): [r[c] for c,_ in index['fields']] for r in samples}.values())
    return dict(name=name, selected=selected, fixed_fields=[],
        fields=[['Id',4,4],['V000',10,255],['V001',9,255]] + ([['Body',12,0],['Blob',11,0]] if name != 'ordinary' else []),
        indexes=indexes, initial_rows=initial, stages=stages, native=native, source_file=name+'-source.mdb')


def refusal_check(directory, notes):
    receipts = json.loads((directory / 'refusals.json').read_text())
    require([r['name'] for r in receipts] == ['duplicate-insert', 'duplicate-replace', 'resource'], 'Refusal inventory')
    original = (directory / 'payloads-grown.mdb').read_bytes()
    for receipt in receipts:
        require((directory / f"refusal-{receipt['name']}-before.mdb").read_bytes() ==
                (directory / f"refusal-{receipt['name']}-after.mdb").read_bytes() == original and
                receipt['preserved'], 'Whole-image refusal preservation')
        expected = ('Open(Header(Read(ResourceLimitExceeded { kind: TotalWorkUnits, requested: 1, maximum: 0 })))'
                    if receipt['name'] == 'resource' else 'Unsupported("duplicate unique key")')
        require(receipt['error'] == expected, 'Declared refusal reason')
        require(notes_identity(original) == notes, 'Refusal Notes preservation')
    return receipts


def inputs():
    files = [Path(__file__), SCRIPT, ROOT / 'crates/jet3/examples/row_overflow_candidate.rs']
    return original_inputs() | {str(p.relative_to(ROOT)): identity(p) for p in files}


def prepare(directory, revision):
    cases = {}
    for name in CASES:
        case = recipe(name)
        source = directory / case['source_file']
        require(identity(source) == SOURCE_PINS[name], 'Pinned EXP-0262 native source: ' + name)
        table = wide.base.tables(source.read_bytes())['Items']
        case['initial_counters'] = {logical['name']: table['physical_indexes'][logical['physical_index']]['entry_count'] for logical in table['logical_indexes']}
        cases[name] = case
    engine.recipe = lambda name: copy.deepcopy(cases[name])
    manifest = engine.prepare(directory, revision)
    for helper in ('numeric_index_mutation.ps1', 'field_update.ps1'):
        shutil.copy2(SCRIPT.with_name(helper), directory / helper)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('candidates', type=Path); p.add_argument('revision')
    p = sub.add_parser('evaluate'); p.add_argument('candidates', type=Path); p.add_argument('outbox', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        revision = subprocess.check_output(['git','rev-parse',args.revision], cwd=ROOT, text=True).strip()
        prepare(args.candidates, revision)
    else:
        engine.aggregate(args.outbox)
        return int(engine.evaluate(args.candidates, args.outbox)['status'] != 'accepted')


engine.CASE_NAMES = CASES
engine.SCRIPT = SCRIPT
engine.MANIFEST = MANIFEST
engine.apply = apply
engine.counters_for = lambda case, rows: case['initial_counters'].copy()
engine.raw_check = wide.raw_check
engine.refusal_check = refusal_check
engine.inputs = inputs
if __name__ == '__main__': raise SystemExit(main())
