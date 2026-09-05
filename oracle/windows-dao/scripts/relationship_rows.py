#!/usr/bin/env python3
"""Preregistered populated relationship and integrity DAO experiment: preflight, dispatch once, analyze.

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
PLAN = ROOT / 'oracle/windows-dao/acquisition/relationship-rows.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/relationship_rows.ps1'


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


ARMS = ('populated',)
PROBES = ('valid_child', 'orphan_child', 'duplicate_parent')


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


def expected_snapshot(arm, probe=None):
    parent = [{'code2': 9 - position, 'key1': position + 1} for position in range(3)]
    child = [{'label3': chr(97 + position) * 255, 'account4': 1 + position % 3} for position in range(20)]
    if probe == 'valid_child':
        child.append({'label3': 'valid', 'account4': 2})
    tables = []
    for name, rows, key, fields, index in [
        ('Accounts7', parent, 'key1', [{'name': 'Code2', 'type': 4, 'size': 4}, {'name': 'Key1', 'type': 4, 'size': 4}], 'Primary9'),
        ('Events9', child, 'account4', [{'name': 'Label3', 'type': 10, 'size': 255}, {'name': 'Account4', 'type': 4, 'size': 4}], 'Account7Events9')]:
        primary = name == 'Accounts7'
        tables.append({'name': name, 'fields': fields,
                       'indexes': [{'name': index, 'primary': primary, 'unique': primary,
                                    'required': primary, 'foreign': not primary, 'ignore_nulls': False,
                                    'fields': [{'name': 'Key1' if primary else 'Account4', 'descending': False, 'attributes': 0}]}],
                       'rows': rows, 'traversal': [dict(row) for row in sorted(rows, key=lambda row: row[key])],
                       'seek': [{'query': value, 'row': dict(next(row for row in rows if row[key] == value))} for value in (1, 2, 3)]})
    return {'version': '3.0', 'tables': ['Accounts7', 'Events9', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships'],
            'user_tables': tables, 'relations': [{'name': 'Account7Events9', 'table': 'Accounts7', 'foreign_table': 'Events9',
                                               'attributes': 0, 'fields': [{'name': 'Key1', 'foreign_name': 'Account4'}]}]}


def normalize(snapshot):
    result = json.loads(json.dumps(snapshot))
    valid = True
    for table in result.get('user_tables', []):
        key = {'Accounts7': 'key1', 'Events9': 'account4'}.get(table['name'])
        rows = table.get('rows', [])
        traversal = table.get('traversal', [])
        keys = [row.get(key) for row in traversal]
        ordered = (all(type(value) is int for value in keys) and keys == sorted(keys)
                   and sorted(traversal, key=canonical) == sorted(rows, key=canonical))
        seeks = table.get('seek', [])
        seek_ok = ([item['query'] for item in seeks] == [1, 2, 3]
                   and all(item['row'] in rows and item['row'][key] == item['query'] for item in seeks))
        valid &= ordered and seek_ok
        if 'rows' in table:
            table['rows'] = sorted(rows, key=canonical)
        if ordered and 'traversal' in table:
            table['traversal'] = sorted(traversal, key=canonical)
        if seek_ok:
            table['seek'] = [1, 2, 3]
    return valid, result


def semantics(plan, arm, observation, probe=None):
    valid, snapshot = normalize(observation['snapshot'])
    passed = (valid and observation['status'] == 'pass' and observation['endpoint'] == 'complete'
              and observation['error'] is None and snapshot == normalize(expected_snapshot(arm, probe))[1])
    return passed, {**{key: observation[key] for key in ('status', 'endpoint', 'error')}, 'snapshot': snapshot}


def probe_semantics(plan, probe, observation):
    passed, snapshot = semantics(plan, 'populated', observation['observation'], probe)
    operation = observation['operation']
    expected = 'updated' if probe == 'valid_child' else 'rejected'
    passed &= operation['status'] == expected and operation['endpoint'] == 'update'
    normalized = {key: operation[key] for key in ('status', 'endpoint', 'native_codes', 'hresult')}
    return passed, {'operation': normalized, 'observation': snapshot}


def analyze(outbox):
    plan = json.loads(PLAN.read_text())
    verify_inputs(plan)
    result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
    if (result['document_type'] != 'dao_relationship_rows_result'
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
    expected_probes = [(replica, probe) for replica in range(1, 4) for probe in PROBES]
    actual_probes = [(item['replica'], item['probe']) for item in result['probes']]
    if actual_probes != expected_probes[:len(actual_probes)]:
        raise ValueError('Unexpected integrity probe inventory')
    grouped_probes = {probe: [] for probe in PROBES}
    probe_codes = {probe: {'candidate': [], 'control': []} for probe in PROBES}
    probe_controls_ok = True
    for item in result['probes']:
        probe, replica = item['probe'], item['replica']
        for role in ('candidate', 'control'):
            observed = item[role]
            base = next(value for value in result['replicas'] if value['replica'] == replica)[role]
            path = outbox / f'populated-{role}-{probe}-r{replica}.mdb'
            if observed['before'] != base['after'] or identity(path) != observed['observation']['after']:
                raise ValueError('Integrity copy identity mismatch')
            if observed['observation']['before'] != observed['observation']['after']:
                raise ValueError('Read-only post-probe observation changed image')
            probe_codes[probe][role].append(observed['operation']['native_codes'])
        control = probe_semantics(plan, probe, item['control'])
        candidate = probe_semantics(plan, probe, item['candidate'])
        probe_controls_ok &= control[0]
        # Native rejection identities are observed and compared to matched controls,
        # never guessed numeric acquisition gates.
        same_error = (candidate[1]['operation']['native_codes'] == control[1]['operation']['native_codes']
                      and candidate[1]['operation']['hresult'] == control[1]['operation']['hresult'])
        grouped_probes[probe].append((candidate[0] and same_error, candidate[1]))
    integrity = {probe: 'no_outcome' for probe in PROBES}
    if (result['error'] is None and actual_probes == expected_probes and controls_ok
            and actual_inventory == expected_inventory and unchanged and probe_controls_ok
            and outcomes['populated'] == 'observed_accepted'):
        for probe, observations in grouped_probes.items():
            if all(item == observations[0] for item in observations):
                integrity[probe] = 'observed_accepted' if observations[0][0] else 'not_observed_accepted'
    report = {'document_type': 'dao_relationship_rows_report', 'development_only': True,
              'compatibility_claim': False, 'support_movement': False,
              'plan_sha256': digest(PLAN), 'result_sha256': digest(outbox / 'result.json'),
              'outcomes': outcomes, 'integrity': integrity, 'probe_codes': probe_codes,
              'replicas': len(result['replicas']), 'probes': len(result['probes']), 'candidates': plan['candidates']}
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
    shutil.copyfile(PLAN, inbox / 'relationship-rows.plan.json')
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
