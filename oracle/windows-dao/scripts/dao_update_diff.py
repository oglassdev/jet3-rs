#!/usr/bin/env python3
"""Finite hosted before/after field-update comparison using protocol 1.2 snapshots."""
import argparse
import copy
import platform
import subprocess
from pathlib import Path

import dao_write_diff as write
from protocol_validation import ValidationError, load_json

common = write.common
ROOT = write.ROOT
INVENTORY = ROOT / 'oracle/windows-dao/protocol/v1_2/update-scenarios.json'
PLAN = ROOT / 'oracle/windows-dao/acquisition/update-v1_2.plan.json'
ROLES = ('before', 'after')


def inventory():
    value = load_json(INVENTORY)
    if value['document_type'] != 'dao_update_scenario_inventory' or value['protocol_version'] != '1.2.0':
        raise ValidationError('Wrong update inventory')
    if [s['id'] for s in value['scenarios']] != ['DAO-UPDATE-FIRST-FIELD', 'DAO-UPDATE-LATER-ROW', 'DAO-UPDATE-LATER-TABLE']:
        raise ValidationError('Wrong finite update membership')
    for scenario in value['scenarios']:
        if scenario['operation'] != {'mode': 'dao_open_rust_update', 'expected_outcome': 'success', 'error_class': None}:
            raise ValidationError('Wrong update operation')
        if set(scenario['required_branches']) - write.protocol.load_branch_ids() or not scenario['coverage']:
            raise ValidationError('Invalid declared coverage')
        recipe(scenario, 'after')
    if not value['deferred_requirements']:
        raise ValidationError('Missing update limitations')
    return value


def recipe(scenario, role):
    expected = copy.deepcopy(scenario)
    request = scenario['request']
    table, = [t for t in expected['tables'] if t['name'] == request['table']]
    column, = [n for n, c in enumerate(table['columns']) if c['name'] == request['column'] and c['kind'] == 'Long']
    table['rows'] = write.expand_rows(table); table['repeat'] = None
    if table['rows'][request['row_index']][column] != request['before']:
        raise ValidationError('Requested old value differs from creation recipe')
    if role == 'after': table['rows'][request['row_index']][column] = request['after']
    elif role != 'before': raise ValidationError('Unknown image role')
    return expected


def command(root, label, args):
    run = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=120)
    (root / (label + '.stdout.log')).write_text(run.stdout)
    (root / (label + '.stderr.log')).write_text(run.stderr)
    if run.returncode: raise ValidationError(label + ' failed')
    return run.stdout


def check_preservation(root, scenario, receipt):
    request = scenario['request']
    if ({key: receipt.get(key) for key in request} != request or receipt.get('scenario_id') != scenario['id']
            or receipt.get('preserved') is not True or receipt.get('length') != 4):
        raise ValidationError('Independent field verification is not bound to the declared request')
    before, after = [(root / role / 'database.mdb').read_bytes() for role in ROLES]
    offset = receipt['offset']
    if type(offset) is not int or offset < 0 or offset + 4 > len(before) or len(before) != len(after):
        raise ValidationError('Invalid independently verified field range')
    if (before[offset:offset+4] != request['before'].to_bytes(4, 'little', signed=True)
            or after[offset:offset+4] != request['after'].to_bytes(4, 'little', signed=True)
            or before[:offset] != after[:offset] or before[offset+4:] != after[offset+4:]):
        raise ValidationError('Requested bytes or unrelated byte preservation mismatch')
    for role in ROLES:
        if receipt[role + '_sha256'] != write.digest(root / role / 'database.mdb'):
            raise ValidationError('Verifier image identity mismatch')


def verify(root, scenario, checker, label):
    import json
    receipt = json.loads(command(root, label, [checker, 'verify', scenario['id'], root]))
    check_preservation(root, scenario, receipt)
    return receipt


def snapshot(root, scenario, reader, revision, scenarios):
    for role in ROLES:
        directory = root / role; identity = write.digest(directory / 'database.mdb')
        command(directory, 'snapshot', [reader, 'snapshot', directory / 'database.mdb', '--scenario', scenario['id'],
            '--out', directory / 'rust', '--source-revision', revision])
        if write.digest(directory / 'database.mdb') != identity: raise ValidationError('Reader changed image')
        value = load_json(directory / 'rust/snapshot.json')
        write.bind_snapshot(value, scenario['id'], revision, 'rust', identity)
        write.assert_recipe(value, recipe(scenario, role))
        write.validate_write_coverage(load_json(directory / 'rust/coverage.json'), scenarios, value)


def preparation(out, revision):
    prepared = load_json(out / 'preparation.json')
    if (prepared['source_revision'] != revision or prepared['inventory_sha256'] != write.digest(INVENTORY)
            or prepared['producer_os'] != 'Linux'
            or [s['scenario_id'] for s in prepared['scenarios']] != [s['id'] for s in inventory()['scenarios']]
            or any(s['status'] != 'prepared' for s in prepared['scenarios'])):
        raise ValidationError('Incomplete or mismatched preparation')
    return prepared


def prepare(out, checker, reader, revision):
    scenarios = inventory()['scenarios']; out.mkdir(parents=True, exist_ok=False)
    prepared = {'document_type': 'dao_update_preparation', 'protocol_version': '1.2.0', 'source_revision': revision,
        'producer_os': platform.system(), 'inventory_sha256': write.digest(INVENTORY), 'scenarios': []}
    try:
        for scenario in scenarios:
            root = out / scenario['id']; root.mkdir()
            entry = {'scenario_id': scenario['id'], 'status': 'failed'}; prepared['scenarios'].append(entry)
            command(root, 'generate', [checker, 'generate', scenario['id'], root])
            entry['verification'] = verify(root, scenario, checker, 'verify')
            snapshot(root, scenario, reader, revision, scenarios)
            entry['status'] = 'prepared'
    finally: common.write_canonical(out / 'preparation.json', prepared)


def resnapshot(out, checker, reader, revision):
    prepared = preparation(out, revision); scenarios = inventory()['scenarios']; receipts = []
    for scenario, entry in zip(scenarios, prepared['scenarios']):
        root = out / scenario['id']
        receipt = verify(root, scenario, checker, 'windows-verify')
        if receipt != entry['verification']: raise ValidationError('Independent Windows reader disagrees with generation verification')
        snapshot(root, scenario, reader, revision, scenarios)
        receipts.append(receipt)
    common.write_canonical(out / 'reader.json', {'source_revision': revision, 'reader_os': platform.system(), 'verifications': receipts})


def evaluate_checked(out):
    scenarios = inventory()['scenarios']; initial = load_json(out / 'preparation.json')
    prepared = preparation(out, initial['source_revision']); revision = prepared['source_revision']
    reader = load_json(out / 'reader.json'); manifest = load_json(out / 'dao-manifest.raw.json')
    if reader != {'source_revision': revision, 'reader_os': 'Windows', 'verifications': [s['verification'] for s in prepared['scenarios']]}:
        raise ValidationError('Independent Windows verification incomplete or mismatched')
    if (manifest['source_revision'] != revision or manifest['inventory_sha256'] != write.digest(INVENTORY)
            or [(s['scenario_id'], s['role']) for s in manifest['scenarios']] != [(s['id'], r) for s in scenarios for r in ROLES]):
        raise ValidationError('Incomplete DAO acquisition or identity mismatch')
    comparisons = []
    for n, (scenario, entry) in enumerate(zip(scenarios, prepared['scenarios'])):
        root = out / scenario['id']; check_preservation(root, scenario, entry['verification'])
        for r, role in enumerate(ROLES):
            directory = root / role; observed = manifest['scenarios'][2*n+r]
            identity = write.digest(directory / 'database.mdb')
            if observed['status'] != 'pass' or observed['error'] is not None or not identity == observed['before'] == observed['after']:
                raise ValidationError('DAO observation failed or image changed')
            rust = load_json(directory / 'rust/snapshot.json')
            dao = common.canonicalize_snapshot(load_json(directory / 'dao-snapshot.raw.json'))
            for kind, value in [('rust', rust), ('dao', dao)]:
                write.bind_snapshot(value, scenario['id'], revision, kind, identity)
                write.assert_recipe(value, recipe(scenario, role))
            write.validate_write_coverage(load_json(directory / 'rust/coverage.json'), scenarios, rust)
            comparison = common.compare_snapshots(dao, rust)
            if comparison['matched'] is not True: raise ValidationError('Snapshot mismatch')
            comparison['role'] = role; comparisons.append(comparison)
            common.write_canonical(directory / 'dao-snapshot.json', dao)
    return {'document_type': 'dao_update_report', 'protocol_version': '1.2.0', 'outcome': 'matched',
        'mode': 'dao_open_rust_update', 'comparisons': comparisons, 'verifications': reader['verifications'],
        'deferred_requirements': inventory()['deferred_requirements'], 'support_matrix_movement': False}


def evaluate(out):
    try: report = evaluate_checked(out)
    except Exception as error:
        common.write_canonical(out / 'report.json', {'document_type': 'dao_update_report', 'outcome': 'no_outcome',
            'error': str(error), 'support_matrix_movement': False})
        raise
    common.write_canonical(out / 'report.json', report)


def validate_plan(expected):
    if write.digest(PLAN) != expected: raise ValidationError('Plan digest mismatch')
    plan = load_json(PLAN)
    if plan['mode'] != 'dao_open_rust_update': raise ValidationError('Wrong plan mode')
    for name, sha in plan['inputs'].items():
        if write.digest(ROOT / name) != sha: raise ValidationError('Plan input mismatch: ' + name)
    inventory()


def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inventory')
    plan = commands.add_parser('plan'); plan.add_argument('sha256')
    for name in ('prepare', 'snapshot'):
        p = commands.add_parser(name); p.add_argument('out', type=Path); p.add_argument('checker', type=Path); p.add_argument('reader', type=Path); p.add_argument('revision')
    p = commands.add_parser('evaluate'); p.add_argument('out', type=Path)
    args = parser.parse_args()
    if args.command == 'inventory': inventory()
    elif args.command == 'plan': validate_plan(args.sha256)
    elif args.command == 'prepare': prepare(args.out, args.checker, args.reader, args.revision)
    elif args.command == 'snapshot': resnapshot(args.out, args.checker, args.reader, args.revision)
    else: evaluate(args.out)


if __name__ == '__main__': main()
