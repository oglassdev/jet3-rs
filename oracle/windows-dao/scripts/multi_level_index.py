#!/usr/bin/env python3
"""Pinned finite multi-level Long index validation; one acquisition, no retries."""
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
import multi_level_index_structure as structure

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/multi-level-index.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/multi_level_index.ps1'
ARMS = ('primary', 'composite', 'relationship')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return {'size': path.stat().st_size, 'sha256': digest(path)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def verify_inputs(plan):
    for path, expected in plan['inputs'].items():
        if digest(ROOT / path) != expected:
            raise ValueError(f'Input pin mismatch: {path}')


def rows_for(arm, name):
    if name == 'Empty':
        return []
    if arm == 'primary':
        return [[27800 - position, position] for position in range(27801)]
    if arm == 'composite':
        return [[position // 400, position // 800 - 9, position] for position in range(12929)]
    if name == 'Parents':
        return [[value, value + 100] for value in range(-100, 101)]
    return [[position % 3 - 1, position] for position in range(27801)]


def order_key(row, table):
    names = [field['name'] for field in table['fields']]
    return tuple(row[names.index(field['name'])] * (-1 if field['descending'] else 1)
                 for field in table['indexes'][0]['fields'])


def query_key(row, table):
    names = [field['name'] for field in table['fields']]
    return tuple(row[names.index(field['name'])] for field in table['indexes'][0]['fields'])


def expected_snapshot(arm, tables):
    users = []
    for table in tables:
        rows = rows_for(arm, table['name'])
        value = {key: table[key] for key in ('name', 'fields', 'indexes')}
        value['rows'] = rows
        if table['indexes']:
            by_key = {query_key(row, table): row for row in rows}
            value['traversal'] = sorted(rows, key=lambda row: order_key(row, table))
            value['seek'] = [{'query': query, 'row': by_key.get(tuple(query))} for query in table['queries']]
        users.append(value)
    relations = []
    if arm == 'relationship':
        relations = [{'name': 'ParentChildren', 'table': 'Parents', 'foreign_table': 'Children', 'attributes': 0,
                      'fields': [{'name': 'Id', 'foreign_name': 'Id'}]}]
    return {'version': '3.0', 'tables': sorted(['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships'] + [table['name'] for table in tables]),
            'user_tables': users, 'relations': relations}


def normalize(snapshot, tables):
    result = json.loads(json.dumps(snapshot))
    specifications = {table['name']: table for table in tables}
    valid = True
    for table in result.get('user_tables', []):
        spec = specifications[table['name']]
        rows = table['rows']
        valid &= all(len(row) == len(spec['fields']) and all(type(value) is int for value in row) for row in rows)
        row_set = {tuple(row) for row in rows}
        table['rows'] = sorted(rows)
        if spec['indexes']:
            traversal = table['traversal']
            ordered = sorted(traversal) == sorted(rows) and [order_key(row, spec) for row in traversal] == sorted(order_key(row, spec) for row in rows)
            valid &= ordered
            if ordered:
                table['traversal'] = sorted(traversal)
            existing = {query_key(row, spec) for row in rows}
            seeks = table['seek']
            seek_ok = [item['query'] for item in seeks] == spec['queries']
            for item in seeks:
                row, query = item['row'], tuple(item['query'])
                seek_ok &= ((row is None and query not in existing) or
                            (row is not None and tuple(row) in row_set and query_key(row, spec) == query))
            valid &= seek_ok
            if seek_ok:
                table['seek'] = [{'query': item['query'], 'found': item['row'] is not None} for item in seeks]
    return valid, result


def preflight(candidate_directory):
    plan = json.loads(PLAN.read_text())
    if plan['replicas'] != 3 or list(plan['arms']) != list(ARMS):
        raise ValueError('Unexpected experiment plan')
    verify_inputs(plan)
    for arm in ARMS:
        path = candidate_directory / f'{arm}.mdb'
        if identity(path) != plan['candidates'][arm]:
            raise ValueError(f'Candidate identity mismatch: {arm}')
        structure.observe(path.read_bytes(), plan['arms'][arm],
                          {table['name']: rows_for(arm, table['name']) for table in plan['arms'][arm]}, True)
    committed = subprocess.run(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT,
                               check=True, capture_output=True).stdout
    if committed != PLAN.read_bytes():
        raise ValueError('Plan must be committed before acquisition')
    return plan


def analyze(outbox):
    plan = json.loads(PLAN.read_text())
    verify_inputs(plan)
    result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
    if (result['document_type'] != 'dao_multi_level_index_result' or result['plan_sha256'] != digest(PLAN)
            or result['development_only'] is not True or result['environment']['process_bits'] != 32
            or result['environment']['provider'] != 'DAO.DBEngine.36'):
        raise ValueError('Result identity/environment mismatch')
    expected_inventory = [(arm, replica) for arm in ARMS for replica in range(1, 4)]
    actual_inventory = [(item['arm'], item['replica']) for item in result['replicas']]
    if actual_inventory != expected_inventory[:len(actual_inventory)]:
        raise ValueError('Unexpected replica inventory')
    groups, decoded = {arm: [] for arm in ARMS}, []
    controls_ok, unchanged = True, True
    for item in result['replicas']:
        arm, replica = item['arm'], item['replica']
        tables = plan['arms'][arm]
        expected = normalize(expected_snapshot(arm, tables), tables)[1]
        roles = {}
        for role in ('control', 'candidate'):
            observation = item[role]
            path = outbox / f'{arm}-{role}-r{replica}.mdb'
            if identity(path) != observation['after']:
                raise ValueError('Retained identity mismatch: ' + path.name)
            if role == 'candidate' and observation['before'] != plan['candidates'][arm]:
                raise ValueError('Candidate did not start with pinned identity')
            unchanged &= observation['before'] == observation['after']
            details, reason = None, None
            try:
                valid, normalized = normalize(observation['snapshot'], tables)
                passed = valid and normalized == expected and observation['status'] == 'pass' and observation['endpoint'] == 'complete' and observation['error'] is None
                details = structure.observe(path.read_bytes(), tables, {table['name']: rows_for(arm, table['name']) for table in tables}, role == 'candidate')
            except (ValueError, KeyError, TypeError, IndexError) as error:
                passed, normalized, reason = False, observation['snapshot'], str(error)
            semantic_hash = hashlib.sha256(canonical(normalized).encode()).hexdigest()
            roles[role] = (passed, semantic_hash, observation['status'], observation['endpoint'], observation['error'], reason)
            decoded.append(dict(arm=arm, replica=replica, role=role, passed=passed, semantic_sha256=semantic_hash, reason=reason, tables=details))
        controls_ok &= roles['control'][0]
        groups[arm].append(roles['candidate'])
    outcomes = {arm: 'no_outcome' for arm in ARMS}
    if (result['mutation_started'] is True and result['error'] is None and unchanged and controls_ok and actual_inventory == expected_inventory):
        for arm, observations in groups.items():
            if all(value == observations[0] for value in observations):
                outcomes[arm] = 'observed_accepted' if observations[0][0] else 'not_observed_accepted'
    report = dict(document_type='dao_multi_level_index_report', development_only=True, compatibility_claim=False,
                  support_movement=False, plan_sha256=digest(PLAN), result_sha256=digest(outbox / 'result.json'),
                  outcomes=outcomes, replicas=len(result['replicas']), candidates=plan['candidates'], structures=decoded)
    path = outbox / 'report.json'
    path.write_text(canonical(report) + '\n')
    print(path)
    print(canonical(outcomes))
    return report


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
    shutil.copyfile(PLAN, inbox / 'multi-level-index.plan.json')
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
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=3600)
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
