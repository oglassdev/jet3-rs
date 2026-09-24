#!/usr/bin/env python3
"""Verify exact DAO refusal residue before comparing one-to-one readbacks."""
import argparse
import copy
import json
from pathlib import Path

from compare_schema_candidates import mask_dates, sha256


# Offsets and before/after bytes are specific to the fixed one_to_one.py suite.
RESIDUE = {
    'duplicate-child': {1538: (0, 1), 49199: (3, 4)},
    'orphan-child': {1538: (0, 1), 49199: (3, 4)},
    'child-duplicate-update': {1538: (0, 1), 49203: (3, 2), 49207: (3, 2)},
    'parent-referenced-update': {},
    'parent-referenced-delete': {},
    'create-duplicate': {1538: (0, 2), 4108: (14, 15), 4143: (14, 15),
                         4151: (14, 15), 6156: (28, 30), 6191: (14, 15)},
    'composite-duplicate': {1538: (0, 1), 51247: (4, 5)},
}
GETTER_RESIDUE = {
    'duplicate-child': ('C', 'PrimaryKey', 3, 4),
    'orphan-child': ('C', 'PrimaryKey', 3, 4),
    'child-duplicate-update': ('C', 'One', 3, 2),
    'composite-duplicate': ('C', 'PrimaryKey', 4, 5),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def flattened(items):
    while len(items) == 1 and isinstance(items[0], list):
        items = items[0]
    return items


def get_distinct_count(observation, table_name, index_name):
    table, = [table for table in flattened(observation['tables']) if table['name'] == table_name]
    index, = [index for index in flattened(table['indexes']) if index['name'] == index_name]
    prop, = [prop for prop in flattened(index['properties']) if prop['name'] == 'DistinctCount']
    return prop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--readback', type=Path, required=True)
    parser.add_argument('--comparison', type=Path, required=True)
    parser.add_argument('--candidate-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text())['cases']
    observed = {item['file']: item for item in json.loads(args.readback.read_text())['files']}
    previous = {item['name']: item for item in json.loads(args.comparison.read_text())['cases']}
    expected_files = {f'{kind}-{case["name"]}.mdb' for case in cases for kind in ('candidate', 'native')}
    require(set(observed) == expected_files and set(previous) == {case['name'] for case in cases},
            'readback or comparison inventory mismatch')
    results = []
    for case in cases:
        name = case['name']
        label = name.rsplit('-r', 1)[0]
        result = {'name': name, 'status': 'pass', 'verified_residue': [], 'masked_getter': None}
        try:
            before = Path(case['input']).read_bytes()
            candidate = (args.candidate_dir / f'candidate-{name}.mdb').read_bytes()
            native = (args.candidate_dir / f'native-{name}.mdb').read_bytes()
            for kind, data in (('candidate', candidate), ('native', native)):
                require(observed[f'{kind}-{name}.mdb']['identity'] ==
                        {'size': len(data), 'sha256': sha256(data)}, 'readback identity: ' + kind)
            left = mask_dates(copy.deepcopy(observed[f'candidate-{name}.mdb']), set(case.get('normalize_table_dates', [])))
            right = mask_dates(copy.deepcopy(observed[f'native-{name}.mdb']), set(case.get('normalize_table_dates', [])))
            for item in (left, right):
                item.pop('file')
                item.pop('identity')
            if label in RESIDUE:
                require(case['kind'] == 'residue' and before == candidate, 'Rust refusal changed input')
                actual = {offset: (a, b) for offset, (a, b) in enumerate(zip(before, native)) if a != b}
                require(len(before) == len(native) and actual == RESIDUE[label], 'native refusal residue')
                result['verified_residue'] = [{'offset': i, 'before': a, 'after': b} for i, (a, b) in actual.items()]
            if label in GETTER_RESIDUE:
                table, index, candidate_count, native_count = GETTER_RESIDUE[label]
                a = get_distinct_count(left, table, index)
                b = get_distinct_count(right, table, index)
                require(a['value'] == candidate_count and b['value'] == native_count,
                        'DAO distinct-count getter residue')
                b['value'] = a['value']
                result['masked_getter'] = {'table': table, 'index': index, 'candidate': candidate_count,
                                           'native': native_count}
            require(left == right, 'readback differs after verified residue')
            require(previous[name]['passed'] == (label not in GETTER_RESIDUE),
                    'original comparison result changed')
            require(previous[name]['identity_equal'], 'original comparison identity')
        except Exception as error:
            result['status'] = 'fail'
            result['error'] = str(error)
        results.append(result)
    report = {'status': 'pass' if all(x['status'] == 'pass' for x in results) else 'fail', 'results': results}
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'passed': sum(x['status'] == 'pass' for x in results),
                      'failures': [x for x in results if x['status'] == 'fail']}, indent=2))
    if report['status'] != 'pass':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
