#!/usr/bin/env python3
"""Separate protocol 1.2 write inventory, generation and hosted evaluation."""
import argparse
import copy
import datetime
import hashlib
import json
import platform
import re
from pathlib import Path
import struct
import subprocess
import uuid

import dao_read_diff as common
import validate_protocol_v1_2 as protocol
from protocol_validation import ValidationError, load_json

ROOT = Path(__file__).resolve().parents[3]
INVENTORY = ROOT / 'oracle/windows-dao/protocol/v1_2/write-scenarios.json'
PLAN = ROOT / 'oracle/windows-dao/acquisition/write-v1_2.plan.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(path=INVENTORY):
    value = load_json(path)
    if value['document_type'] != 'dao_write_scenario_inventory' or value['protocol_version'] != '1.2.0':
        raise ValidationError('Expected separate protocol 1.2 write inventory')
    ids = [s['id'] for s in value['scenarios']]
    if len(set(ids)) != len(ids) or not ids:
        raise ValidationError('Duplicate or empty write inventory')
    for scenario in value['scenarios']:
        if not re.fullmatch(r'DAO-WRITE-[A-Z0-9][A-Z0-9_-]{2,63}', scenario['id']) or scenario['operation'] != {'mode': 'dao_open_rust', 'expected_outcome': 'success', 'error_class': None}:
            raise ValidationError('Wrong write scenario ID or operation mode')
        if set(scenario['required_branches']) - protocol.load_branch_ids():
            raise ValidationError('Unknown required branch')
        if not scenario['coverage']:
            raise ValidationError('Missing declared coverage')
    if not value['deferred_requirements']:
        raise ValidationError('Bounded first write leg must declare its deferrals')
    return value


def expand_rows(table):
    rows = []
    for n in range(table['repeat'] if table['repeat'] is not None else len(table['rows'])):
        row = table['rows'][0 if table['repeat'] is not None else n]
        if len(row) != len(table['columns']):
            raise ValidationError('Recipe row width mismatch')
        expanded = []
        for value in row:
            if isinstance(value, dict):
                if 'sequence' in value: value = value['sequence'] + n
                elif 'repeat_text' in value: value = value['repeat_text'] * value['count']
                elif value == {'auto': True}: value = n + 1
                else: raise ValidationError('Unknown recipe expression')
            expanded.append(value)
        rows.append(expanded)
    return rows


def typed(column, value):
    kind = column['kind']
    if kind == 'Boolean':
        return {'kind': 'boolean', 'value': bool(value)}
    if value is None:
        return {'kind': 'null', 'value': None}
    formats = {'Byte': ('byte', 'B'), 'Integer': ('integer', 'h'), 'Long': ('long', 'i'),
        'AutoIncrement': ('long', 'i'), 'Single': ('single', 'f'), 'Double': ('double', 'd'), 'Currency': ('currency', 'q'), 'DateTime': ('datetime', 'd')}
    if kind in formats:
        name, fmt = formats[kind]
        raw = struct.pack('<' + fmt, value)
        if kind == 'Single': value = struct.unpack('<f', raw)[0]
        elif kind == 'Currency':
            sign = '-' if value < 0 else ''
            value = f'{sign}{abs(value)//10000}.{abs(value)%10000:04d}'
        elif kind == 'DateTime':
            value = (datetime.datetime(1899, 12, 30) + datetime.timedelta(days=value)).isoformat()
        return {'kind': name, 'value': value, 'raw_hex': raw.hex()}
    if kind == 'Guid':
        guid = uuid.UUID(bytes=bytes(value))
        return {'kind': 'guid', 'value': str(guid), 'raw_hex': guid.bytes_le.hex()}
    raw = value.encode('ascii')
    if kind in ('Text', 'Memo'):
        return {'kind': kind.lower(), 'value': value, 'raw_hex': raw.hex(), 'code_page': 1252}
    return {'kind': 'binary' if kind == 'Binary' else 'ole', 'value': raw.hex(), 'raw_hex': raw.hex()}


def expected_schema(scenario):
    tables = []
    sizes = {'Boolean': 1, 'Byte': 1, 'Integer': 2, 'Long': 4, 'AutoIncrement': 4, 'Currency': 8, 'Single': 4, 'Double': 8, 'DateTime': 8, 'Guid': 16, 'Memo': 0, 'LongBinary': 0}
    dao = {'AutoIncrement': 'Long', 'DateTime': 'Date', 'Guid': 'GUID'}
    for table in scenario['tables']:
        columns = [{'name': c['name'], 'ordinal': n, 'dao_type': 'db' + dao.get(c['kind'], c['kind']),
            'size': sizes.get(c['kind'], c.get('size')), 'auto_increment': c['kind'] == 'AutoIncrement',
            'attributes': 17 if c['kind'] == 'AutoIncrement' else 2 if c['kind'] in ('Text', 'Binary', 'Memo', 'LongBinary') else 1, 'properties': {}}
            for n, c in enumerate(table['columns'])]
        indexes = [{'name': index['name'], 'fields': [{'name': columns[field['column']]['name'], 'descending': field['descending']} for field in index['fields']],
            'primary': index['kind'] == 'primary', 'unique': index['kind'] != 'ordinary', 'required': index['kind'] == 'primary', 'properties': {}} for index in table['indexes']]
        rows = [{'values': {column['name']: typed(column, value) for column, value in zip(table['columns'], row)}} for row in expand_rows(table)]
        tables.append({'name': table['name'], 'kind': 'user', 'attributes': 0, 'properties': {}, 'columns': columns, 'indexes': indexes, 'rows': rows})
    relationships = []
    relation = scenario['relationship']
    if relation:
        parent, child = tables[relation['parent_table']], tables[relation['child_table']]
        field = parent['columns'][relation['parent_column']]['name']
        foreign = child['columns'][relation['child_column']]['name']
        relationships.append({'name': relation['name'], 'table': parent['name'], 'foreign_table': child['name'],
            'attributes': 0, 'fields': [{'field': field, 'foreign_field': foreign}], 'properties': {}})
        child['indexes'].append({'name': relation['name'], 'fields': [{'name': foreign, 'descending': False}],
            'primary': False, 'unique': False, 'required': False, 'properties': {}})
    return tables, relationships


def assert_recipe(snapshot, scenario):
    expected = copy.deepcopy(snapshot)
    expected['tables'], expected['relationships'] = expected_schema(scenario)
    expected['database_properties'] = {}
    expected['raw_preservation'] = []
    expected = common.canonicalize_snapshot(expected)
    if common.comparison_document(snapshot) != common.comparison_document(expected):
        raise ValidationError('Snapshot differs from declared creation request')


def validate_write_coverage(receipt, scenarios, snapshot):
    protocol.SCHEMA_SET.validate(receipt)
    ids = [s['id'] for s in scenarios]
    if receipt['scenario_id'] not in ids or [s['id'] for s in receipt['scenarios']] != ids:
        raise ValidationError('Write receipt inventory mismatch')
    branches = receipt['branches']
    if branches != sorted(set(branches)) or set(branches) - protocol.load_branch_ids():
        raise ValidationError('Invalid write receipt branches')
    if receipt['outcome'] != 'success' or receipt['error_class'] is not None or receipt['producer']['kind'] != 'rust':
        raise ValidationError('Write snapshot did not succeed')
    if (receipt['database_sha256'] != snapshot['database_sha256'] or receipt['scenario_id'] != snapshot['scenario_id']
            or receipt['producer'] != snapshot['producer']):
        raise ValidationError('Write receipt snapshot mismatch')
    for declaration, actual in zip(scenarios, receipt['scenarios']):
        missing = sorted(set(declaration['required_branches']) - set(branches))
        forbidden = sorted(set((declaration['boundary'] or {}).get('forbidden_branches', [])) & set(branches))
        if actual != {'id': declaration['id'], 'missing_branches': missing, 'forbidden_observed': forbidden, 'outcome_matches': True, 'satisfied': not missing and not forbidden}:
            raise ValidationError('Write receipt verdict mismatch')
    if not next(s['satisfied'] for s in receipt['scenarios'] if s['id'] == receipt['scenario_id']):
        raise ValidationError('Write coverage unsatisfied')


def assert_indexes(observations, snapshot):
    expected = [(table, index) for table in snapshot['tables'] for index in table['indexes']]
    if [(x['table'], x['index']) for x in observations] != [(t['name'], i['name']) for t, i in expected]:
        raise ValidationError('Incomplete index observation inventory')
    for observation, (table, index) in zip(observations, expected):
        rows = [row['values'] for row in table['rows']]
        key = lambda row: tuple(row[field['name']]['value'] for field in index['fields'])
        directed = lambda row: tuple(v * (-1 if f['descending'] else 1) for v, f in zip(key(row), index['fields']))
        canonical = lambda row: common.canonical_bytes(row)
        traversal = observation['rows']
        if sorted(map(canonical, traversal)) != sorted(map(canonical, rows)) or list(map(directed, traversal)) != sorted(map(directed, rows)):
            raise ValidationError('Index traversal differs from complete requested rows')
        queries = [tuple(s['query']) for s in observation['seeks']]
        if len(queries) != len(set(queries)) or set(queries) != set(map(key, rows)):
            raise ValidationError('Seek inventory incomplete')
        for seek in observation['seeks']:
            if seek['row'] not in rows or key(seek['row']) != tuple(seek['query']):
                raise ValidationError('Seek returned wrong full row')


def bind_snapshot(snapshot, scenario_id, revision, kind, identity):
    if (snapshot['scenario_id'] != scenario_id or snapshot['producer'] != {'kind': kind, 'source_revision': revision}
            or snapshot['database_sha256'] != identity):
        raise ValidationError('Snapshot scenario, source or database identity mismatch')


def prepare(out, generator, reader, revision):
    scenarios = inventory()['scenarios']
    out.mkdir(parents=True, exist_ok=False)
    manifest = {'document_type': 'dao_write_preparation', 'protocol_version': '1.2.0', 'source_revision': revision, 'producer_os': platform.system(), 'inventory_sha256': digest(INVENTORY), 'scenarios': []}
    try:
        for scenario in scenarios:
            root = out / scenario['id']; root.mkdir()
            entry = {'scenario_id': scenario['id'], 'status': 'failed', 'error': None}
            manifest['scenarios'].append(entry)
            try:
                for label, command in [('generate', [str(generator), scenario['id'], str(root / 'database.mdb')]),
                    ('snapshot', [str(reader), 'snapshot', str(root / 'database.mdb'), '--scenario', scenario['id'], '--out', str(root / 'rust'), '--source-revision', revision])]:
                    run = subprocess.run(command, capture_output=True, text=True, timeout=120)
                    (root / (label + '.stdout.log')).write_text(run.stdout)
                    (root / (label + '.stderr.log')).write_text(run.stderr)
                    if run.returncode: raise ValidationError(label + ' failed')
                    if label == 'generate':
                        entry['database_sha256'] = digest(root / 'database.mdb')
                    elif digest(root / 'database.mdb') != entry['database_sha256']:
                        raise ValidationError('Initial reader changed generated database')
                snapshot = load_json(root / 'rust/snapshot.json')
                bind_snapshot(snapshot, scenario['id'], revision, 'rust', entry['database_sha256'])
                assert_recipe(snapshot, scenario)
                validate_write_coverage(load_json(root / 'rust/coverage.json'), scenarios, snapshot)
                entry['status'] = 'prepared'
            except Exception as error:
                entry['error'] = str(error)
                raise
    finally:
        common.write_canonical(out / 'preparation.json', manifest)


def resnapshot(out, reader, revision):
    prepared = load_json(out / 'preparation.json')
    if prepared['source_revision'] != revision or prepared['inventory_sha256'] != digest(INVENTORY):
        raise ValidationError('Downloaded preparation identity mismatch')
    for entry in prepared['scenarios']:
        if entry['scenario_id'] not in [s['id'] for s in inventory()['scenarios']]:
            raise ValidationError('Unknown downloaded scenario')
        root = out / entry['scenario_id']
        if entry['status'] != 'prepared' or digest(root / 'database.mdb') != entry['database_sha256']:
            raise ValidationError('Downloaded database identity mismatch')
        run = subprocess.run([str(reader), 'snapshot', str(root / 'database.mdb'), '--scenario', entry['scenario_id'],
            '--out', str(root / 'rust'), '--source-revision', revision], capture_output=True, text=True, timeout=120)
        (root / 'windows-snapshot.stdout.log').write_text(run.stdout)
        (root / 'windows-snapshot.stderr.log').write_text(run.stderr)
        if run.returncode: raise ValidationError('Windows Rust snapshot failed')
    common.write_canonical(out / 'reader.json', {'source_revision': revision, 'reader_os': platform.system()})


def evaluate_checked(out):
    scenarios = inventory()['scenarios']; prepared = load_json(out / 'preparation.json'); manifest = load_json(out / 'dao-manifest.raw.json')
    ids = [s['id'] for s in scenarios]
    reader = load_json(out / 'reader.json')
    if prepared['producer_os'] != 'Linux' or reader != {'source_revision': prepared['source_revision'], 'reader_os': 'Windows'}:
        raise ValidationError('Producer/reader source or platform receipt mismatch')
    if manifest['source_revision'] != prepared['source_revision']:
        raise ValidationError('Producer source revision mismatch')
    if prepared['inventory_sha256'] != digest(INVENTORY) or manifest['inventory_sha256'] != digest(INVENTORY):
        raise ValidationError('Inventory identity mismatch')
    if [s['scenario_id'] for s in prepared['scenarios']] != ids or [s['scenario_id'] for s in manifest['scenarios']] != ids:
        raise ValidationError('Incomplete write acquisition')
    comparisons = []
    for scenario, initial, observation in zip(scenarios, prepared['scenarios'], manifest['scenarios']):
        root = out / scenario['id']; identity = digest(root / 'database.mdb')
        if initial['status'] != 'prepared' or observation['status'] != 'pass' or observation['error'] is not None:
            raise ValidationError('Write generation or DAO observation failed')
        if not identity == initial['database_sha256'] == observation['before'] == observation['after']:
            raise ValidationError('Read-only database identity changed')
        rust = load_json(root / 'rust/snapshot.json'); dao = common.canonicalize_snapshot(load_json(root / 'dao-snapshot.raw.json'))
        bind_snapshot(rust, scenario['id'], prepared['source_revision'], 'rust', identity)
        bind_snapshot(dao, scenario['id'], prepared['source_revision'], 'dao', identity)
        validate_write_coverage(load_json(root / 'rust/coverage.json'), scenarios, rust)
        assert_recipe(rust, scenario); assert_recipe(dao, scenario)
        assert_indexes(load_json(root / 'dao-indexes.raw.json'), dao)
        comparison = common.compare_snapshots(dao, rust); comparison['document_type'] = 'dao_write_comparison'; comparison['mode'] = 'dao_open_rust'
        common.write_canonical(root / 'dao-snapshot.json', dao); common.write_canonical(root / 'comparison.json', comparison)
        comparisons.append(comparison)
    report = {'document_type': 'dao_write_report', 'protocol_version': '1.2.0', 'outcome': 'matched', 'mode': 'dao_open_rust',
        'comparisons': comparisons, 'deferred_requirements': inventory()['deferred_requirements'], 'support_matrix_movement': False}
    common.write_canonical(out / 'report.json', report)


def evaluate(out):
    try:
        evaluate_checked(out)
    except Exception as error:
        common.write_canonical(out / 'report.json', {'document_type': 'dao_write_report', 'protocol_version': '1.2.0',
            'outcome': 'no_outcome', 'mode': 'dao_open_rust', 'error': str(error), 'support_matrix_movement': False})
        raise


def validate_plan(expected):
    if digest(PLAN) != expected: raise ValidationError('Plan digest mismatch')
    plan = load_json(PLAN)
    if plan['mode'] != 'dao_open_rust': raise ValidationError('Wrong plan mode')
    for name, sha in plan['inputs'].items():
        if digest(ROOT / name) != sha: raise ValidationError('Plan input mismatch: ' + name)
    inventory()


def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inventory')
    plan = commands.add_parser('plan'); plan.add_argument('sha256')
    prep = commands.add_parser('prepare'); prep.add_argument('out', type=Path); prep.add_argument('generator', type=Path); prep.add_argument('reader', type=Path); prep.add_argument('revision')
    snap = commands.add_parser('snapshot'); snap.add_argument('out', type=Path); snap.add_argument('reader', type=Path); snap.add_argument('revision')
    check = commands.add_parser('evaluate'); check.add_argument('out', type=Path)
    args = parser.parse_args()
    if args.command == 'inventory': inventory()
    elif args.command == 'plan': validate_plan(args.sha256)
    elif args.command == 'prepare': prepare(args.out, args.generator, args.reader, args.revision)
    elif args.command == 'snapshot': resnapshot(args.out, args.reader, args.revision)
    else: evaluate(args.out)


if __name__ == '__main__': main()
