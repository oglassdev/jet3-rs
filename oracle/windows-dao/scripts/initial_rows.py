#!/usr/bin/env python3
"""Preregistered initial-row DAO experiment: preflight, dispatch once, analyze.

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
PLAN = ROOT / 'oracle/windows-dao/acquisition/initial-rows.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/initial_rows.ps1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return {'size': path.stat().st_size, 'sha256': digest(path)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def preflight(candidate):
    plan = json.loads(PLAN.read_text())
    if plan['experiment_id'] != 'initial-rows' or plan['replicas'] != 3:
        raise ValueError('Unexpected experiment plan')
    for name, expected in plan['inputs'].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f'Input pin mismatch: {name}')
    if identity(candidate) != plan['candidate']:
        raise ValueError('Candidate identity mismatch')
    # The exact plan must already exist in a commit before acquisition.
    committed = subprocess.run(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT,
                               check=True, capture_output=True).stdout
    if committed != PLAN.read_bytes():
        raise ValueError('Plan must be committed before acquisition')
    return plan


def normalized(snapshot):
    result = dict(snapshot)
    if 'rows' in result:
        result['rows'] = sorted(result['rows'], key=canonical)
    return result


def analyze(outbox):
    plan = json.loads(PLAN.read_text())
    result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
    if (result['document_type'] != 'dao_initial_rows_result'
            or result['plan_sha256'] != digest(PLAN)
            or result['development_only'] is not True
            or result['environment']['process_bits'] != 32):
        raise ValueError('Result identity/environment mismatch')
    expected = normalized(plan['expected_snapshot'])
    replicas = result['replicas']
    outcome = 'no_outcome'
    observations = []
    controls_ok = True
    for number, replica in enumerate(replicas, 1):
        if replica['replica'] != number or number > 3:
            raise ValueError('Unexpected replica inventory')
        for role in ('candidate', 'control'):
            observation = replica[role]
            retained = outbox / f'{role}-r{number}.mdb'
            if identity(retained) != observation['after']:
                raise ValueError(f'Retained identity mismatch: {retained.name}')
            if role == 'candidate' and observation['before'] != plan['candidate']:
                raise ValueError('Candidate did not start with pinned identity')
            if observation['before'] != observation['after']:
                controls_ok = False
        control = replica['control']
        controls_ok &= (control['status'] == 'pass' and control['endpoint'] == 'complete'
                        and normalized(control['snapshot']) == expected)
        candidate = dict(replica['candidate'])
        candidate.pop('before')
        candidate.pop('after')
        candidate['snapshot'] = normalized(candidate['snapshot'])
        observations.append(candidate)
    if (result['mutation_started'] is True and result['error'] is None
            and len(replicas) == 3 and controls_ok
            and all(item == observations[0] for item in observations)):
        accepted = (observations[0]['status'] == 'pass'
                    and observations[0]['endpoint'] == 'complete'
                    and observations[0]['snapshot'] == expected)
        outcome = 'observed_accepted' if accepted else 'not_observed_accepted'
    report = {'document_type': 'dao_initial_rows_report', 'development_only': True,
              'plan_sha256': digest(PLAN), 'result_sha256': digest(outbox / 'result.json'),
              'outcome': outcome, 'replicas': len(replicas), 'candidate': plan['candidate']}
    path = outbox / 'report.json'
    path.write_text(canonical(report) + '\n')
    print(path)
    print(canonical(report))


def dispatch(args):
    preflight(args.candidate)
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}', args.run_id):
        raise ValueError('Invalid run id')
    shared = args.shared_root.resolve()
    inbox, outbox = (shared / part / args.run_id for part in ('inbox', 'outbox'))
    if inbox.exists() or outbox.exists():
        raise ValueError('Run id already used; never redispatch a scientific run')
    inbox.mkdir(parents=True)
    shutil.copyfile(ROOT / SCRIPT, inbox / 'script.ps1')
    shutil.copyfile(PLAN, inbox / PLAN.name)
    shutil.copyfile(args.candidate, inbox / 'initial-rows.mdb')
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
