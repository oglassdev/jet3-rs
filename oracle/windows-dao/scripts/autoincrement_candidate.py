#!/usr/bin/env python3
"""Preregistered descending/composite candidate DAO experiment: preflight, dispatch once, analyze.

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
PLAN = ROOT / 'oracle/windows-dao/acquisition/autoincrement-candidate.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/autoincrement_candidate.ps1'


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


ARMS = ('unindexed', 'indexed', 'multi')


def preflight(candidate_directory):
    plan = json.loads(PLAN.read_text())
    if plan['experiment_id'] != 'autoincrement-candidate' or plan['replicas'] != 3 or list(plan['candidates']) != list(ARMS):
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


def decoder():
    spec = importlib.util.spec_from_file_location('auto_candidate_catalog', ROOT / 'oracle/windows-dao/scripts/system_catalog.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.MAX_ROWS_PER_PAGE != 64:
        raise ValueError('Unexpected original decoder bound')
    module.MAX_ROWS_PER_PAGE = 256
    return module


def expected_rows(table, post):
    rows = [[n, n] for n in range(1, table['count'] + 1)]
    if table['name'] == 'Later':
        rows = [[1, -1]]
    if post:
        rows.append([table['count'] + 1, 1001])
    return rows


def semantics(observation, tables, post, path):
    if observation['status'] != 'pass' or observation['endpoint'] != 'complete' or observation['error'] is not None:
        return False, {'status': observation['status'], 'error': observation['error']}
    snapshot = observation['snapshot']
    expected = {'version': '3.0', 'tables': sorted(['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships'] + [t['name'] for t in tables]), 'user_tables': []}
    for table in tables:
        rows = expected_rows(table, post)
        indexes = []
        if table['indexed']:
            indexes = [{'name': 'PrimaryKey', 'primary': True, 'unique': True, 'required': True,
                'foreign': False, 'ignore_nulls': False, 'fields': [{'name': 'Id', 'attributes': 0, 'descending': False}]}]
        expected['user_tables'].append({'name': table['name'], 'attributes': 0,
            'fields': [{'name': 'Id', 'type': 4, 'size': 4, 'attributes': 17}, {'name': 'Tag', 'type': 4, 'size': 4, 'attributes': 1}],
            'indexes': indexes, 'rows': rows,
            'traversal': rows if table['indexed'] else [],
            'seek': [{'query': row[0], 'row': row} for row in rows] if table['indexed'] else []})
    normalized = json.loads(json.dumps(snapshot))
    for table in normalized.get('user_tables', []):
        table['rows'] = sorted(table['rows'])
    module = decoder()
    try:
        data = path.read_bytes()
        decoded = module.analyze_checkpoint(data)
        named = {t['name']: t for t in decoded['tables'].values()}
        states = []
        good = normalized == expected
        for table in tables:
            definition = named[table['name']]['definition']
            root = definition['root']
            raw = module._page(data, root, 'user TDEF')
            state = int.from_bytes(raw[16:20], 'little', signed=True)
            rows = module._table_rows(data, definition, named[table['name']]['data_pages'])
            wanted = expected_rows(table, post)
            good &= (state == table['count'] + int(post) and definition['row_count'] == len(wanted)
                     and sorted(row['values'] for row in rows) == wanted)
            states.append({'name': table['name'], 'last_generated': state, 'row_count': definition['row_count']})
        return good, {'snapshot': normalized, 'states': states}
    except (module.DecodeError, KeyError, ValueError) as error:
        return False, {'snapshot': normalized, 'decode_error': str(error)}


def build_report(result, outbox, plan):
    if (result['document_type'] != 'dao_autoincrement_candidate_result'
            or result['plan_sha256'] != digest(PLAN) or result['development_only'] is not True
            or result['environment']['process_bits'] != 32 or result['environment']['provider'] != 'DAO.DBEngine.36'):
        raise ValueError('Result identity/environment mismatch')
    expected = [(arm, replica) for arm in ARMS for replica in range(1, 4)]
    actual = [(item['arm'], item['replica']) for item in result['replicas']]
    if actual != expected[:len(actual)]:
        raise ValueError('Unexpected replica inventory')
    compared, grouped = [], {arm: [] for arm in ARMS}
    unchanged, controls_ok = True, True
    for item in result['replicas']:
        arm, replica = item['arm'], item['replica']
        roles = {}
        for role in ('control', 'candidate'):
            stages = {}
            for post in (False, True):
                stage = 'post' if post else 'initial'
                observation = item[role][stage]
                path = outbox / f'{arm}-{role}-r{replica}-{stage}.mdb'
                if identity(path) != observation['after']:
                    raise ValueError('Retained image identity mismatch')
                unchanged &= observation['before'] == observation['after']
                if not post and role == 'candidate' and observation['before'] != plan['candidates'][arm]:
                    raise ValueError('Initial candidate pin mismatch')
                stages[stage] = semantics(observation, plan['arms'][arm], post, path)
            if item[role]['copy_before'] != item[role]['initial']['after']:
                raise ValueError('Writable copy did not start from observed source')
            insertion = item[role]['insert']
            inserted = insertion['status'] == 'pass' and insertion['error'] is None and insertion['ids'] == [t['count'] + 1 for t in plan['arms'][arm]]
            roles[role] = {'initial': stages['initial'], 'post': stages['post'], 'insert': insertion,
                           'passed': inserted and all(s[0] for s in stages.values())}
        controls_ok &= roles['control']['passed']
        grouped[arm].append(roles)
        compared.append({'arm': arm, 'replica': replica, **roles})
    outcomes = dict.fromkeys(ARMS, 'no_outcome')
    if actual == expected and result['error'] is None and result['mutation_started'] is True and unchanged and controls_ok:
        for arm, observations in grouped.items():
            if all(item == observations[0] for item in observations):
                pair = observations[0]
                outcomes[arm] = 'observed_accepted' if pair['candidate']['passed'] and pair['control'] == pair['candidate'] else 'not_observed_accepted'
    return {'document_type': 'dao_autoincrement_candidate_report', 'plan_sha256': digest(PLAN),
        'development_only': True, 'compatibility_claim': False, 'support_movement': False,
        'outcomes': outcomes, 'observations': compared, 'unchanged': unchanged, 'controls_ok': controls_ok,
        'acquisition_error': result['error'], 'mutation_started': result['mutation_started']}


def analyze(outbox):
    plan = json.loads(PLAN.read_text())
    verify_inputs(plan)
    result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
    report = build_report(result, outbox, plan)
    report['result_sha256'] = digest(outbox / 'result.json')
    path = outbox / 'report.json'
    path.write_text(canonical(report) + '\n')
    print(path)
    print(canonical(report['outcomes']))


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
    shutil.copyfile(PLAN, inbox / 'autoincrement-candidate.plan.json')
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
