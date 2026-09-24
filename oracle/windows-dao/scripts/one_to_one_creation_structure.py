#!/usr/bin/env python3
"""Compare fresh one-to-one creation images with complete raw Jet 3 observations."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

import compare_schema_structure as comparison
import schema_edit_structure as raw


def comparable_catalog(table, names):
    rows = []
    for source in table['rows']:
        row = copy.deepcopy(source['values'])
        row['DateCreate'] = row['DateUpdate'] = '<creation time>'
        if row['Name'] in names:
            row.pop('LvProp')
        rows.append(row)
    return sorted(rows, key=comparison.canonical)


def compare(candidate, native, case):
    name = case['name']
    tables = {table['name'] for table in case['request']['tables']}
    left, right = comparison.semantics(candidate, {'name': name, 'steps': []}), comparison.semantics(native, {'name': name, 'steps': []})
    left.pop('MSysObjects')
    right.pop('MSysObjects')
    differences = comparison.differences(left, right)
    comparison.require(not differences, 'raw table/index difference: ' + str(differences[:3]))
    catalog_left = comparable_catalog(candidate['tables']['MSysObjects'], tables)
    catalog_right = comparable_catalog(native['tables']['MSysObjects'], tables)
    comparison.require(catalog_left == catalog_right, 'catalog identity or non-date fields')
    candidate_catalog = {r['values']['Name']: r['values'] for r in candidate['tables']['MSysObjects']['rows']}
    native_catalog = {r['values']['Name']: r['values'] for r in native['tables']['MSysObjects']['rows']}
    for table in tables:
        comparison.require(candidate_catalog[table]['LvProp'] is None and native_catalog[table]['LvProp'] is not None,
                           'expected native creation default properties: ' + table)
        defaults = bytes.fromhex(native_catalog[table]['LvProp'])
        comparison.require(len(defaults) == 67 and hashlib.sha256(defaults).hexdigest() ==
                           '106660b5c99813de4454ee856efb631c2840c560977d03ed67a15e871cee2dc9',
                           'exact native SQL table default properties: ' + table)
    comparison.require(set(candidate['maps']) == set(native['maps']), 'complete map role inventory')
    mode = name.removeprefix('creation-').rsplit('-r', 1)[0]
    child_index = 2 if mode in ('plain', 'required', 'ignore-null', 'descending') else 1
    allowed = {'MSysRelationships/table/owned', 'MSysRelationships/table/available',
               'C/table/owned', 'C/table/available', f'C/index/{child_index}/owned'}
    if mode == 'mixed':
        allowed.add('C/index/2/owned')
    placement = []
    for role, c_map in candidate['maps'].items():
        n_map = native['maps'][role]
        if c_map == n_map:
            continue
        comparison.require(role in allowed, 'unlisted placement: ' + role)
        for key in ('kind', 'length', 'start', 'references'):
            comparison.require(c_map['record'][key] == n_map['record'][key], 'map framing: ' + role + '/' + key)
        comparison.require(len(c_map['members']) == len(n_map['members']), 'map capacity: ' + role)
        placement.append(role)
    return {'name': name, 'status': 'pass', 'placement_roles': placement,
            'native_default_property_tables': sorted(tables), 'normalized_catalog_dates': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--candidate-dir', type=Path, required=True)
    parser.add_argument('--native-dir', type=Path, required=True)
    parser.add_argument('--raw-out', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raw.setup(Path(__file__).resolve().parent)
    args.raw_out.mkdir(exist_ok=True)
    cases = json.loads(args.manifest.read_text())['cases']
    results, failures = [], []
    for case in cases:
        name = case['name']
        try:
            observations = {}
            for kind, path in (('candidate', args.candidate_dir / ('candidate2-' + name + '.mdb')),
                               ('native', args.native_dir / ('input-' + name + '.mdb'))):
                observed = raw.observe(path)
                observations[kind] = observed
                (args.raw_out / (kind + '-' + name + '.json')).write_text(json.dumps(observed, indent=2, sort_keys=True) + '\n')
            request = json.loads((args.manifest.parent / 'suite' / 'creation' / (name + '.json')).read_text())
            results.append(compare(observations['candidate'], observations['native'], {'name': name, 'request': request}))
        except Exception as error:
            failures.append({'name': name, 'error': str(error)})
    report = {'status': 'fail' if failures else 'pass', 'results': results, 'failures': failures}
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'passed': len(results), 'failures': failures}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
