#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if '--repo' in sys.argv:
    _repo = Path(sys.argv[sys.argv.index('--repo') + 1]).resolve()
    REPO_SCRIPTS = _repo / 'oracle' / 'windows-dao' / 'scripts'
else:
    REPO_SCRIPTS = HERE
sys.path.insert(0, str(REPO_SCRIPTS))

import system_catalog as catalog
import numeric_index_mutation_structure as index_tree
import larger_graph_negative_indexes as relationship_system_indexes
from relationship_mutation_structure import logical_relation, map_info, rows as decode_rows

KIND = {
    'Boolean': 1, 'Byte': 2, 'Integer': 3, 'Long': 4, 'Currency': 5,
    'Single': 6, 'Double': 7, 'Date': 8, 'Binary': 9, 'Text': 10,
    'FixedText': 10, 'GUID': 15,
}


def req(ok, message):
    if not ok:
        raise AssertionError(message)


def identity(path):
    raw = path.read_bytes()
    return {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def pname(value):
    return value['name']['value']


def normalized_snapshot(value):
    value = copy.deepcopy(value)
    for table in value['tables']:
        for prop in table['properties']:
            if pname(prop) in ('DateCreated', 'LastUpdated'):
                prop['value'] = '<timestamp>'
    return value


def compact_error(error):
    if error is None:
        return None
    return {key: error.get(key) for key in ('message', 'numbers', 'hresult', 'type')}


def raw_observation(path, accepted):
    data = path.read_bytes()
    analysis = catalog.analyze_checkpoint(data)
    named = {item['name']: item for item in analysis['tables'].values()}
    req({'Parent', 'Child', 'MSysObjects', 'MSysACEs', 'MSysQueries', 'MSysRelationships'} <= set(named), f'{path.name} tables')
    result = {
        'identity': identity(path), 'page_count': len(data) // 2048,
        'page0_relationship_byte': data[1538], 'free_pages': analysis['free_pages'],
        'tables': {}, 'maps': {},
        'system_indexes': relationship_system_indexes.inventory(path),
    }
    decoded_by_table = {}
    for name, table in named.items():
        definition = table['definition']
        primary = 'Id' if name in ('Parent', 'Child') else None
        decoded = decode_rows(data, table, primary_column=primary) if primary else []
        decoded_by_table[name] = decoded
        physical = []
        for index in definition['physical_indexes']:
            fields = []
            for key in index['keys']:
                column = definition['columns'][key['column']]
                req(column['type'] in KIND, f'{path.name} unknown indexed type {column["type"]}')
                fields.append((KIND[column['type']], key['direction'] == 0))
            nodes, entries = index_tree.tree(data, index['root'], definition['root'], fields)
            members = sorted(catalog._locator_pages(data, index['map'], f'{name} index {index["index"]}'))
            req({node['page'] for node in nodes} <= set(members), f'{path.name} {name} index ownership')
            if decoded:
                row_locators = {(row['locator']['page'], row['locator']['row']) for row in decoded}
                entry_locators = {(int.from_bytes(entry[-4:-1], 'big'), entry[-1]) for entry in entries}
                req(entry_locators == row_locators, f'{path.name} {name} index {index["index"]} locator coverage')
            physical.append({
                'index': index['index'], 'root': index['root'], 'flags': index['flags'],
                'keys': index['keys'], 'map': index['map'], 'members': members,
                'first_word': int.from_bytes(bytes.fromhex(index['prefix_hex']), 'little'),
                'second_word': index['entry_count'], 'nodes': nodes,
                'entries_hex': [entry.hex() for entry in entries],
            })
        result['tables'][name] = {
            'root': definition['root'], 'columns': definition['columns'],
            'logical_indexes': definition['logical_indexes'], 'physical_indexes': physical,
            'maps': definition['maps'], 'long_value_maps': definition['long_value_maps'],
            'rows': [{
                'locator': row['locator'], 'storage': row['storage'],
                'values': {key: value.hex() if isinstance(value, bytes) else value for key, value in row['values'].items()},
                'raw_hex': row['raw_hex'], 'descriptors': row['descriptors'],
            } for row in decoded],
        }
    relationship_rows = catalog._generic_rows(analysis['system_rows']['MSysRelationships'], analysis)
    objects = catalog._generic_rows(analysis['system_rows']['MSysObjects'], analysis)
    aces = catalog._generic_rows(analysis['system_rows']['MSysACEs'], analysis)
    result['relationship_rows'] = relationship_rows
    result['relationship_objects'] = [{key: row[key] for key in ('Id', 'ParentId', 'Name', 'Type', 'Flags', 'Owner')}
                                      for row in objects if row['Type'] == 8]
    object_ids = {row['Id'] for row in result['relationship_objects']}
    result['relationship_aces'] = [row for row in aces if row['ObjectId'] in object_ids]
    if accepted:
        req(len(relationship_rows) == 1 and len(result['relationship_objects']) == 1 and len(result['relationship_aces']) == 2,
            f'{path.name} relationship catalog cardinality')
        parent = result['tables']['Parent']; child = result['tables']['Child']
        child_records = [logical_relation(item['raw_hex']) | {'name': item['name']}
                         for item in child['logical_indexes'] if item['class'] == 2 and item['name'] == 'ParentChild']
        req(len(child_records) == 1, f'{path.name} child relationship record')
        foreign = child_records[0]
        parent_records = [logical_relation(item['raw_hex']) | {'name': item['name']}
                          for item in parent['logical_indexes'] if item['class'] == 2 and item['name'].startswith('.r')]
        reciprocal = [item for item in parent_records
                      if item['related_root'] == child['root']
                      and item['selector'] == foreign['relation_ordinal']
                      and item['relation_ordinal'] == foreign['selector']]
        req(len(reciprocal) == 1, f'{path.name} parent reciprocal record')
        parent_record = reciprocal[0]
        req(parent_record['side'] == 1 and foreign['side'] == 2 and parent_record['context_hex'] == foreign['context_hex'] == '0000',
            f'{path.name} relationship record roles')
        result['reciprocal'] = {'parent': parent_record, 'child': foreign}
    else:
        req(not relationship_rows and not result['relationship_objects'] and not result['relationship_aces'],
            f'{path.name} rejected relationship residue')
    maps = {'global': map_info(data, {'page': 1, 'row': 0}, 'global')}
    for name, table in named.items():
        definition = table['definition']
        for role, locator in definition['maps'].items():
            maps[f'{name}/table/{role}'] = map_info(data, locator, f'{name}-{role}')
        for index in definition['physical_indexes']:
            maps[f'{name}/index/{index["index"]}'] = map_info(data, index['map'], f'{name}-index')
        for group in definition['long_value_maps']:
            for role in ('owned', 'available'):
                maps[f'{name}/lval/{group["column"]}/{role}'] = map_info(data, group[role], f'{name}-lval')
    locators = [(record['locator']['page'], record['locator']['row']) for record, _ in maps.values()]
    references = [page for record, _ in maps.values() for page in record['references'] if page]
    req(len(locators) == len(set(locators)) and len(references) == len(set(references)), f'{path.name} map record uniqueness')
    free = set(analysis['free_pages']); claimed = {}; metadata = {0, 1}
    for name, table in named.items():
        definition = table['definition']; metadata.update(definition['pages'])
        groups = [(f'{name}/table', set(maps[f'{name}/table/owned'][1]))]
        req(set(maps[f'{name}/table/available'][1]) <= groups[0][1], f'{path.name} available table pages')
        for index in definition['physical_indexes']:
            groups.append((f'{name}/index/{index["index"]}', set(maps[f'{name}/index/{index["index"]}'][1])))
        for group in definition['long_value_maps']:
            key = f'{name}/lval/{group["column"]}'
            owned = set(maps[key + '/owned'][1])
            req(set(maps[key + '/available'][1]) <= owned, f'{path.name} available LVAL pages')
            groups.append((key, owned))
        for role, members in groups:
            req(not members & free, f'{path.name} live/free overlap {role}')
            for page in members:
                req(page not in claimed, f'{path.name} duplicate owner page {page}')
                claimed[page] = role
    for record, _ in maps.values():
        metadata.add(record['locator']['page']); metadata.update(page for page in record['references'] if page)
    req(not metadata & free and not metadata & set(claimed), f'{path.name} metadata separation')
    result['maps'] = {key: {'record': value[0], 'members': value[1]} for key, value in maps.items()}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--run-id', default='20260916T103125Z-scalar-rel-create-r2')
    parser.add_argument('--report', type=Path, default=Path(__file__).resolve().parent / 'CREATION_REPORT.json')
    parser.add_argument('--matrix', type=Path)
    parser.add_argument('--producer', type=Path)
    args = parser.parse_args()
    matrix_path = args.matrix or args.root / 'matrix.json'; producer = args.producer or args.root / 'scalar_relationship_creation.ps1'
    matrix = json.loads(matrix_path.read_text())
    run = args.root / 'runs' / args.run_id; outbox = run / 'outbox'; inbox = run / 'inbox'
    req(identity(inbox / matrix_path.name) == identity(matrix_path), 'submitted matrix identity')
    req(identity(inbox / 'script.ps1') == identity(producer), 'submitted producer identity')
    req((run / 'run-id.txt').read_text().strip() == args.run_id, 'run ID')
    workers = json.loads((outbox / 'workers.json').read_text())
    req(all(item['exit_code'] == 0 for item in workers['workers']), 'worker exits')
    req(workers['matrix'] == identity(matrix_path) and workers['script'] == identity(producer), 'worker input identities')
    results = []
    by_id = {}
    environments = []
    for path in sorted(outbox.glob('worker-*-result.json')):
        worker = json.loads(path.read_text())
        req(worker['status'] == 'pass' and worker['error'] is None, f'{path.name} status')
        req(worker['matrix'] == identity(matrix_path) and worker['script'] == identity(producer), f'{path.name} inputs')
        environments.append(worker['environment'])
        for item in worker['items']:
            req(item['producer_error'] is None, f'{item["id"]} producer')
            req(item['capture']['before'] == item['capture']['after'] == item['artifact'], f'{item["id"]} read identity')
            mdb = outbox / f'{item["id"]}.mdb'; req(item['artifact'] == identity(mdb), f'{item["id"]} artifact')
            for table in item['capture']['snapshot']['tables']:
                for read in table['index_reads'].values():
                    req(read['error'] is None, f'{item["id"]} index read')
                    req(all((entry['row'] is None) == entry['no_match'] for entry in read['seek']), f'{item["id"]} Seek presence')
            by_id[item['id']] = (item, mdb)
    req(len(by_id) == len(matrix['items']), 'exact item count')
    req(all(environment == environments[0] for environment in environments), 'provider environment equality')
    expected_ids = {item['id'] for item in matrix['items']}; req(set(by_id) == expected_ids, 'exact output item inventory')
    expected_files = {f'{item}.mdb' for item in expected_ids} | {f'worker-{n}-{suffix}.json' for n in range(1, 5) for suffix in ('progress', 'result')} | {'workers.json', 'exit.txt', 'log.txt'}
    req((outbox / 'exit.txt').read_text().strip() == '0', 'wrapper exit')
    req({path.name for path in outbox.iterdir()} == expected_files, 'exact outbox inventory')
    cases = {case['id']: case for case in matrix['cases']}
    accepted_ids = []
    for spec in matrix['items']:
        item, mdb = by_id[spec['id']]; accepted = item['relation_error'] is None
        expected = spec['case'] not in {'byte-integer','integer-long','single-double','currency-double','date-double','guid-binary16','boolean-byte'}
        req(accepted == expected, f'{spec["id"]} relationship eligibility')
        if not accepted:
            req(compact_error(item['relation_error']) == {'message': 'Relationship must be on the same number of fields with the same data types.', 'numbers': [3368], 'hresult': -2146824920, 'type': 'System.Runtime.InteropServices.COMException'}, f'{spec["id"]} error')
        snapshot = item['capture']['snapshot']
        req(len(snapshot['tables']) == 2 and {pname(t) for t in snapshot['tables']} == {'Parent','Child'}, f'{spec["id"]} table inventory')
        req(len(snapshot['relations']) == int(accepted), f'{spec["id"]} DAO relation inventory')
        results.append({'id': spec['id'], 'case': spec['case'], 'replica': spec['replica'], 'accepted': accepted,
                        'error': compact_error(item['relation_error']), 'artifact': item['artifact'],
                        'snapshot': snapshot, 'raw': raw_observation(mdb, accepted)})
        if accepted: accepted_ids.append(spec['id'])
    # Replicas must agree completely after normalizing only native timestamps and layout identities.
    for case_id in cases:
        pair = sorted((item for item in results if item['case'] == case_id), key=lambda item: item['replica'])
        req(len(pair) == 2, f'{case_id} replicas')
        req(normalized_snapshot(pair[0]['snapshot']) == normalized_snapshot(pair[1]['snapshot']), f'{case_id} semantic replica equality')
        req(pair[0]['accepted'] == pair[1]['accepted'] and pair[0]['error'] == pair[1]['error'], f'{case_id} outcome replica equality')
    report = {
        'document_type': 'scalar_relationship_creation_discovery_report', 'status': 'accepted',
        'run_id': args.run_id, 'matrix': identity(matrix_path), 'producer': identity(producer),
        'provider_environment': environments[0], 'cases': len(cases), 'captures': len(results),
        'accepted_captures': len(accepted_ids), 'rejected_captures': len(results) - len(accepted_ids),
        'results': results,
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({key: report[key] for key in ('status','run_id','cases','captures','accepted_captures','rejected_captures')}, indent=2))


if __name__ == '__main__':
    main()
