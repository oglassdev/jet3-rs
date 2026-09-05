#!/usr/bin/env python3
"""Preregistered relationship candidate DAO experiment: preflight, dispatch once, analyze.

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
PLAN = ROOT / 'oracle/windows-dao/acquisition/parameterized-relationships.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/parameterized_relationships.ps1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return {'size': path.stat().st_size, 'sha256': digest(path)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def verify_inputs():
    plan = json.loads(PLAN.read_text())
    if plan['experiment_id'] != 'parameterized-relationships' or plan['replicas'] != 3:
        raise ValueError('Unexpected experiment plan')
    for name, expected in plan['inputs'].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f'Input pin mismatch: {name}')
    return plan


def preflight(candidate):
    plan = verify_inputs()
    for arm in plan['arms']:
        if identity(candidate / arm['filename']) != arm['candidate']:
            raise ValueError('Candidate identity mismatch: ' + arm['name'])
    committed = subprocess.run(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT,
                               check=True, capture_output=True).stdout
    if committed != PLAN.read_bytes():
        raise ValueError('Plan must be committed before acquisition')
    return plan


def normalized(snapshot):
    result = json.loads(canonical(snapshot))
    if 'schema' in result:
        for table in result['schema'].values():
            table['indexes'] = sorted(table['indexes'], key=lambda index: index['name'])
    if 'relations' in result:
        result['relations'] = sorted(result['relations'], key=lambda relation: relation['name'])
    return result


def control_matches(snapshot, expected):
    if any(snapshot.get(key) != expected[key] for key in ('version', 'tables', 'relations')):
        return False
    schema = snapshot.get('schema', {})
    if set(schema) != set(expected['columns']):
        return False
    for name, columns in expected['columns'].items():
        table = schema[name]
        fields = [{key: field[key] for key in ('name', 'type', 'size')} for field in table['fields']]
        if table['attributes'] != 0 or fields != columns or table['rows'] != []:
            return False
    # DAO exposes all other index metadata as the differential control.
    parent = {index['name']: index for index in schema[expected['parent']]['indexes']}
    for name, column, primary in expected['parent_indexes']:
        index = parent.get(name)
        if not index or index['primary'] is not primary or index['unique'] is not True or index['fields'] != [{'name': column, 'attributes': 0}]:
            return False
    return True


def arm_report(result, outbox, plan, arm):
    if (result['document_type'] != 'dao_parameterized_relationships_result'
            or result['plan_sha256'] != digest(PLAN) or result['development_only'] is not True
            or result['environment']['process_bits'] != 32
            or result['environment']['provider'] != 'DAO.DBEngine.36'):
        raise ValueError('Result identity/environment mismatch')
    observations, reasons = [], []
    for number, replica in enumerate(result['replicas'], 1):
        if replica['replica'] != number or number > 3:
            raise ValueError('Unexpected replica inventory')
        pair = {}
        for role in ('control', 'candidate'):
            observation = replica[role]
            if identity(outbox / f"{arm['name']}-{role}-r{number}.mdb") != observation['after']:
                raise ValueError('Retained image identity mismatch')
            if role == 'candidate' and observation['before'] != arm['candidate']:
                raise ValueError('Candidate starting identity mismatch')
            if observation['before'] != observation['after']:
                reasons.append(f'{role} replica {number}: read-only bytes changed')
            pair[role] = {key: observation[key] for key in ('status', 'endpoint', 'error')}
            pair[role]['snapshot'] = normalized(observation['snapshot'])
        control = pair['control']
        if (control['status'] != 'pass' or control['endpoint'] != 'complete'
                or not control_matches(control['snapshot'], arm['expected_control'])):
            reasons.append(f'Control replica {number} did not satisfy declared schema/relation/rows')
        observations.append(pair)
    if len(observations) != 3 or result['error'] is not None or result['mutation_started'] is not True:
        reasons.append('Acquisition incomplete or failed: ' + str(result['error']))
    if observations and any(pair != observations[0] for pair in observations):
        reasons.append('Replicas disagree on endpoint observations')
    outcome = 'no_outcome'
    if not reasons:
        outcome = 'observed_accepted' if observations[0]['candidate'] == observations[0]['control'] else 'not_observed_accepted'
    return {'document_type': 'dao_parameterized_relationships_report', 'development_only': True,
            'plan_sha256': digest(PLAN), 'candidate': arm['candidate'], 'outcome': outcome,
            'reasons': reasons, 'observations': observations, 'compatibility_claim': False,
            'support_matrix_movement': False}


def build_report(result, outbox, plan):
    expected = [(arm['name'], replica) for arm in plan['arms'] for replica in range(1, 4)]
    actual = [(entry['arm'], entry['replica']) for entry in result['replicas']]
    if actual != expected[:len(actual)]:
        raise ValueError('Unexpected arm/replica inventory')
    reports = {}
    for arm in plan['arms']:
        subset = dict(result, replicas=[entry for entry in result['replicas'] if entry['arm'] == arm['name']])
        reports[arm['name']] = arm_report(subset, outbox, plan, arm)
    outcomes = [report['outcome'] for report in reports.values()]
    outcome = ('no_outcome' if 'no_outcome' in outcomes else
               'not_observed_accepted' if 'not_observed_accepted' in outcomes else 'observed_accepted')
    return {'document_type': 'dao_parameterized_relationships_matrix_report',
            'development_only': True, 'plan_sha256': digest(PLAN), 'outcome': outcome,
            'arms': reports, 'compatibility_claim': False, 'support_matrix_movement': False}


def analyze(outbox):
    plan = verify_inputs()
    result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
    report = build_report(result, outbox, plan)
    report['result_sha256'] = digest(outbox / 'result.json')
    output = outbox / 'report.json'
    output.write_text(canonical(report) + '\n')
    print(output)
    print(report['outcome'])


def dispatch(args):
    plan = preflight(args.candidate)
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}', args.run_id):
        raise ValueError('Invalid run id')
    shared = args.shared_root.resolve()
    inbox, outbox = (shared / part / args.run_id for part in ('inbox', 'outbox'))
    if inbox.exists() or outbox.exists():
        raise ValueError('Run id already used; never redispatch a scientific run')
    inbox.mkdir(parents=True)
    shutil.copyfile(ROOT / SCRIPT, inbox / 'script.ps1')
    shutil.copyfile(PLAN, inbox / PLAN.name)
    for arm in plan['arms']:
        shutil.copyfile(args.candidate / arm['filename'], inbox / arm['filename'])
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
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('preflight')
    check.add_argument('candidate', type=Path)
    report = commands.add_parser('analyze')
    report.add_argument('outbox', type=Path)
    run = commands.add_parser('run')
    run.add_argument('candidate', type=Path)
    run.add_argument('--run-id', required=True)
    run.add_argument('--shared-root', type=Path, required=True)
    for name, default in [('host', '127.0.0.1'), ('port', '2222'), ('user', 'jet3runner'),
                          ('identity', str(Path.home() / '.ssh/jet3-dao')),
                          ('remote-shared-root', r'\\host.lan\Data')]:
        run.add_argument('--' + name, default=os.environ.get('JET3_WINDOWS_' + name.upper().replace('-', '_'), default))
    args = parser.parse_args()
    if args.command == 'preflight':
        preflight(args.candidate)
        print('Pinned inputs and committed plan match.')
    elif args.command == 'analyze':
        analyze(args.outbox)
    else:
        dispatch(args)


if __name__ == '__main__':
    main()
