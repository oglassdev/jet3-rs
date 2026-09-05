#!/usr/bin/env python3
"""Preregistered multi-table initial-row DAO experiment: preflight, dispatch once, analyze.

Uses only the low-level SSH transport helpers from windows-dao-ps.py. Its
ad-hoc discovery entry point is never used. Local results do not move the
hosted support matrix.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/multi-table-rows.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/multi_table_rows.ps1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return {'size': path.stat().st_size, 'sha256': digest(path)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def verify_inputs(plan):
    for name, expected in plan['inputs'].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f'Input pin mismatch: {name}')


ARMS = ('mixed', 'empty-first')


def preflight(candidate_directory):
    plan = json.loads(PLAN.read_text())
    if plan['replicas'] != 3 or list(plan['candidates']) != list(ARMS):
        raise ValueError('Unexpected experiment plan')
    verify_inputs(plan)
    for arm in ARMS:
        if identity(candidate_directory / f'{arm}.mdb') != plan['candidates'][arm]:
            raise ValueError(f'Candidate identity mismatch: {arm}')
    # The exact plan must already exist in a commit before acquisition.
    committed = subprocess.run(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT,
                               check=True, capture_output=True).stdout
    if committed != PLAN.read_bytes():
        raise ValueError('Plan must be committed before acquisition')
    return plan


def expected_snapshot(arm):
    id_field = [{'name': 'Id', 'type': 4, 'size': 4}]
    tables = [{'name': 'Empty', 'fields': id_field, 'indexes': [], 'rows': []}]
    if arm == 'mixed':
        keys = [{'id': key} for key in (-1, 2, 3)]
        tables = [
            {'name': 'Numbers', 'fields': id_field, 'indexes': [],
             'rows': [{'id': number} for number in range(-254, 255)]},
            {'name': 'Keys', 'fields': id_field,
             'indexes': [{'name': 'ById', 'primary': True, 'unique': True, 'required': True,
                          'foreign': False, 'ignore_nulls': False,
                          'fields': [{'name': 'Id', 'descending': False, 'attributes': 0}]}],
             'rows': keys, 'traversal': [dict(row) for row in keys],
             'seek': [{'query': key, 'row': {'id': key}} for key in (-1, 2, 3)]},
            {'name': 'Notes', 'fields': [{'name': 'Payload', 'type': 12, 'size': 0}],
             'indexes': [], 'rows': [{'payload': ''.join(chr(65 + offset % 26) for offset in range(4096))},
                                    {'payload': None}]},
            *tables]
    else:
        tables.append({'name': 'Binary', 'fields': [{'name': 'Payload', 'type': 11, 'size': 0}],
                       'indexes': [], 'rows': [{'payload': bytes(offset % 256 for offset in range(2048)).hex()}]})
    return {'version': '3.0', 'tables': sorted(['MSysACEs', 'MSysObjects', 'MSysQueries',
            'MSysRelationships'] + [table['name'] for table in tables]), 'user_tables': tables}


def normalize(snapshot):
    result = json.loads(json.dumps(snapshot))
    for table in result.get('user_tables', []):
        if 'rows' in table:
            table['rows'] = sorted(table['rows'], key=canonical)
    return result


def semantics(plan, arm, observation):
    snapshot = normalize(observation['snapshot'])
    passed = (observation['status'] == 'pass' and observation['endpoint'] == 'complete'
              and observation['error'] is None and snapshot == normalize(expected_snapshot(arm)))
    return passed, {**{key: observation[key] for key in ('status', 'endpoint', 'error')},
                    'snapshot': snapshot}


def analyze(outbox):
    plan = json.loads(PLAN.read_text())
    verify_inputs(plan)
    result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
    if (result['document_type'] != 'dao_multi_table_rows_result'
            or result['plan_sha256'] != digest(PLAN)
            or result['development_only'] is not True
            or result['environment']['process_bits'] != 32):
        raise ValueError('Result identity/environment mismatch')
    expected_inventory = [(arm, replica) for arm in ARMS for replica in range(1, 4)]
    actual_inventory = [(item['arm'], item['replica']) for item in result['replicas']]
    if actual_inventory != expected_inventory[:len(actual_inventory)]:
        raise ValueError('Unexpected replica inventory')
    grouped = {arm: [] for arm in ARMS}
    unchanged = True
    controls_ok = True
    for item in result['replicas']:
        arm, replica = item['arm'], item['replica']
        for role in ('candidate', 'control'):
            observation = item[role]
            retained = outbox / f'{arm}-{role}-r{replica}.mdb'
            if identity(retained) != observation['after']:
                raise ValueError(f'Retained identity mismatch: {retained.name}')
            if role == 'candidate' and observation['before'] != plan['candidates'][arm]:
                raise ValueError('Candidate did not start with pinned identity')
            unchanged &= observation['before'] == observation['after']
        controls_ok &= semantics(plan, arm, item['control'])[0]
        grouped[arm].append(semantics(plan, arm, item['candidate']))
    outcomes = {arm: 'no_outcome' for arm in ARMS}
    if (result['mutation_started'] is True and result['error'] is None
            and actual_inventory == expected_inventory and controls_ok and unchanged):
        for arm, observations in grouped.items():
            if all(item == observations[0] for item in observations):
                outcomes[arm] = 'observed_accepted' if observations[0][0] else 'not_observed_accepted'
    report = {'document_type': 'dao_multi_table_rows_report', 'development_only': True,
              'compatibility_claim': False, 'support_movement': False,
              'plan_sha256': digest(PLAN), 'result_sha256': digest(outbox / 'result.json'),
              'outcomes': outcomes, 'replicas': len(result['replicas']), 'candidates': plan['candidates']}
    path = outbox / 'report.json'
    path.write_text(canonical(report) + '\n')
    print(path)
    print(canonical(report))


def dispatch(args):
    preflight(args.candidate_directory)
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}', args.run_id):
        raise ValueError('Invalid run id')
    shared = args.shared_root.resolve()
    inbox, outbox = (shared / part / args.run_id for part in ('inbox', 'outbox'))
    if inbox.exists() or outbox.exists():
        raise ValueError('Run id already used; never redispatch a scientific run')
    inbox.mkdir(parents=True)
    shutil.copyfile(ROOT / SCRIPT, inbox / 'script.ps1')
    shutil.copyfile(PLAN, inbox / 'multi-table-rows.plan.json')
    for arm in ARMS:
        shutil.copyfile(args.candidate_directory / f'{arm}.mdb', inbox / f'{arm}.mdb')
    spec = importlib.util.spec_from_file_location('transport', ROOT / 'scripts/windows-dao-ps.py')
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    command = ['ssh', '-p', args.port, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
               '-o', 'IdentitiesOnly=yes', '-i', args.identity, f'{args.user}@{args.host}',
               'powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
               transport.encoded(transport.guest_script(args.remote_shared_root, args.run_id, 'script.ps1'))]
    print(f'Dispatching one run {args.run_id}; no automatic retries.', flush=True)
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
    if (outbox / 'log.txt').exists():
        print(transport.read_log(outbox / 'log.txt'))
    if not (outbox / 'result.json').exists():
        raise RuntimeError(f'No scientific result (SSH exit {completed.returncode}); inspect run, do not retry')
    analyze(outbox)


def main():
    global PLAN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=PLAN)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('preflight')
    check.add_argument('candidate_directory', type=Path)
    report = commands.add_parser('analyze')
    report.add_argument('outbox', type=Path)
    run = commands.add_parser('run')
    run.add_argument('candidate_directory', type=Path)
    run.add_argument('--run-id', required=True)
    run.add_argument('--shared-root', type=Path, required=True)
    for name, default in [('host', '127.0.0.1'), ('port', '2222'), ('user', 'jet3runner'),
                          ('identity', str(Path.home() / '.ssh/jet3-dao')),
                          ('remote-shared-root', r'\\host.lan\Data')]:
        run.add_argument('--' + name, default=os.environ.get('JET3_WINDOWS_' + name.upper().replace('-', '_'), default))
    args = parser.parse_args()
    PLAN = args.plan.resolve()
    if args.command == 'preflight':
        preflight(args.candidate_directory)
        print('Pinned inputs and committed plan match.')
    elif args.command == 'analyze':
        analyze(args.outbox)
    else:
        dispatch(args)


if __name__ == '__main__':
    main()
