#!/usr/bin/env python3
"""Pinned local AutoIncrement state observations; preflight, acquire once, and analyze."""
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

import system_catalog as catalog

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/autoincrement-layout.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/autoincrement_layout.ps1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return {'size': path.stat().st_size, 'sha256': digest(path)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def verify_inputs():
    plan = json.loads(PLAN.read_text())
    if plan['experiment_id'] != 'autoincrement-layout' or plan['replicas'] != 3:
        raise ValueError('Unexpected experiment plan')
    for name, expected in plan['inputs'].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f'Input pin mismatch: {name}')
    return plan


def preflight():
    plan = verify_inputs()
    committed = subprocess.run(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT,
                               check=True, capture_output=True).stdout
    if committed != PLAN.read_bytes():
        raise ValueError('Plan must be committed before acquisition')
    return plan


CHECKPOINTS = ('empty', 'one', 'n255', 'n256', 'deleted', 'next')
ARMS = ('auto', 'long')


def expected_tags(checkpoint):
    if checkpoint == 'empty':
        return []
    if checkpoint == 'one':
        return [1]
    if checkpoint == 'n255' or checkpoint == 'deleted':
        return list(range(1, 256))
    if checkpoint == 'n256':
        return list(range(1, 257))
    return list(range(1, 256)) + [257]


def observe(data):
    analysis = catalog.analyze_checkpoint(data)
    named = {table['name']: table for table in analysis['tables'].values()}
    table = named['Rows']
    definition = table['definition']
    rows = catalog._table_rows(data, definition, table['data_pages'])
    header = catalog._page(data, definition['root'], 'Rows definition')
    pages = {'header': {'page': 0, 'hex': data[:catalog.PAGE_BYTES].hex()},
             'user_definition': {'page': definition['root'], 'hex': header.hex()}}
    objects = named['MSysObjects']
    for label, numbers in [('catalog_definition', objects['definition']['pages']), ('catalog_data', objects['data_pages'])]:
        for ordinal, page in enumerate(numbers):
            pages[f'{label}:{ordinal}'] = {'page': page, 'hex': catalog._page(data, page, label).hex()}
    return {'definition_root': definition['root'], 'row_count': definition['row_count'],
            'columns': definition['columns'], 'rows': rows,
            'state_header_hex': header[12:35].hex(),
            'candidate_state_hex': header[16:20].hex(),
            'candidate_state_i32': int.from_bytes(header[16:20], 'little', signed=True),
            'maps': {kind: {'locator': locator, 'pages': sorted(catalog._locator_pages(data, locator, kind))}
                     for kind, locator in definition['maps'].items()},
            'data_pages': table['data_pages'], 'page_count': len(data) // catalog.PAGE_BYTES,
            'global_free_pages': analysis['free_pages'], 'pages': pages}


def changed_ranges(before, after):
    result = []
    for role in sorted(set(before['pages']) | set(after['pages'])):
        old, new = before['pages'].get(role), after['pages'].get(role)
        left, right = bytes.fromhex(old['hex']) if old else b'', bytes.fromhex(new['hex']) if new else b''
        offsets = [offset for offset in range(max(len(left), len(right)))
                   if left[offset:offset + 1] != right[offset:offset + 1]]
        for start, end in catalog._coalesce(offsets):
            result.append({'role': role, 'page_before': old['page'] if old else None,
                'page_after': new['page'] if new else None, 'start': start, 'end': end,
                'before_hex': left[start:end].hex(), 'after_hex': right[start:end].hex()})
    return result


def correlate(snapshot, decoded, arm, checkpoint):
    fields = snapshot.get('fields', [])
    if (snapshot.get('version') != '3.0' or snapshot.get('table_attributes') != 0
            or snapshot.get('tables') != ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Rows']
            or snapshot.get('index_count') != 0 or len(fields) != 2):
        return False
    for ordinal, name in enumerate(('Id', 'Tag')):
        field = fields[ordinal]
        if (field['name'] != name or field['type'] != 4 or field['size'] != 4
                or bool(field['attributes'] & 16) != (arm == 'auto' and ordinal == 0)):
            return False
    rows = snapshot.get('rows', [])
    if (sorted(rows) != sorted(row['values'] for row in decoded['rows'])
            or sorted(row[1] for row in rows) != expected_tags(checkpoint)
            or decoded['row_count'] != len(rows)):
        return False
    if any(type(value) is not int or not -(1 << 31) <= value < (1 << 31) for row in rows for value in row):
        return False
    return arm == 'auto' or all(row[0] == row[1] for row in rows)


def comparable(entry):
    decoded = entry['decoded']
    snapshot = dict(entry['snapshot'], rows=sorted(entry['snapshot']['rows']))
    return {'snapshot': snapshot, 'state_header_hex': decoded['state_header_hex'],
            'columns': decoded['columns'], 'row_count': decoded['row_count']}


def build_report(result, outbox, plan):
    if (result['document_type'] != 'dao_autoincrement_layout_result'
            or result['plan_sha256'] != digest(PLAN) or result['development_only'] is not True
            or result['environment']['process_bits'] != 32
            or result['environment']['provider'] != 'DAO.DBEngine.36'):
        raise ValueError('Result identity/environment mismatch')
    expected = [(replica, arm, checkpoint) for replica in range(1, 4) for arm in ARMS for checkpoint in CHECKPOINTS]
    actual = [(entry['replica'], entry['arm'], entry['checkpoint']) for entry in result['checkpoints']]
    if actual != expected[:len(actual)]:
        raise ValueError('Unexpected checkpoint inventory')
    observations, reasons, previous = [], [], {}
    for entry in result['checkpoints']:
        replica, arm, checkpoint = entry['replica'], entry['arm'], entry['checkpoint']
        label = f'{arm}-r{replica}-{checkpoint}'
        if entry['file'] != label + '.mdb' or identity(outbox / entry['file']) != entry['after']:
            raise ValueError('Retained image identity mismatch')
        if entry['before'] != entry['after']:
            reasons.append(label + ': read-only bytes changed')
        try:
            if entry['status'] != 'pass' or entry['error'] is not None:
                raise ValueError('DAO observation failed: ' + str(entry['error']))
            decoded = observe((outbox / entry['file']).read_bytes())
            if not correlate(entry['snapshot'], decoded, arm, checkpoint):
                raise ValueError('DAO/decoded schema or row correlation failed')
            prior = previous.get((replica, arm))
            if prior:
                old_ids = {row['values'][1]: row['values'][0] for row in prior[0]['rows']}
                if any(row['values'][1] in old_ids and row['values'][0] != old_ids[row['values'][1]] for row in decoded['rows']):
                    raise ValueError('Surviving row ID changed across checkpoints')
            item = {'replica': replica, 'arm': arm, 'checkpoint': checkpoint,
                    'snapshot': entry['snapshot'], 'decoded': decoded,
                    'previous_checkpoint': prior[1] if prior else None,
                    'changed_ranges': changed_ranges(prior[0], decoded) if prior else []}
            previous[(replica, arm)] = (decoded, checkpoint)
            observations.append(item)
        except (catalog.DecodeError, KeyError, ValueError) as error:
            reasons.append(label + ': ' + str(error))
    if len(observations) != len(expected) or result['error'] is not None or result['mutation_started'] is not True:
        reasons.append('Acquisition incomplete or failed: ' + str(result['error']))
    for arm in ARMS:
        for checkpoint in CHECKPOINTS:
            group = [comparable(item) for item in observations if item['arm'] == arm and item['checkpoint'] == checkpoint]
            if group and any(item != group[0] for item in group):
                reasons.append(f'{arm}/{checkpoint}: question-bearing values differ across replicas')
    hypotheses = []
    if len(observations) == len(expected):
        for replica in range(1, 4):
            auto = {item['checkpoint']: item['decoded'] for item in observations if item['replica'] == replica and item['arm'] == 'auto'}
            long = {item['checkpoint']: item['decoded'] for item in observations if item['replica'] == replica and item['arm'] == 'long'}
            latest = {checkpoint: max((row['values'] for row in auto[checkpoint]['rows']), key=lambda row: row[1], default=[0, 0])[0] for checkpoint in CHECKPOINTS}
            latest['deleted'] = latest['n256']
            hypotheses.append({'replica': replica,
                'state_matches_last_generated': all(auto[c]['candidate_state_i32'] == latest[c] for c in CHECKPOINTS),
                'ordinary_state_stays_zero': all(long[c]['candidate_state_i32'] == 0 for c in CHECKPOINTS),
                'generated_ids_match_tags': all(row['values'][0] == row['values'][1] for c in CHECKPOINTS for row in auto[c]['rows']),
                'next_after_delete': next(row['values'][0] for row in auto['next']['rows'] if row['values'][1] == 257)})
    return {'document_type': 'dao_autoincrement_layout_report', 'development_only': True,
            'plan_sha256': digest(PLAN), 'outcome': 'answered' if not reasons else 'no_outcome',
            'reasons': reasons, 'observations': observations, 'hypotheses': hypotheses,
            'compatibility_claim': False, 'support_matrix_movement': False}


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
    preflight()
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}', args.run_id):
        raise ValueError('Invalid run id')
    inbox, outbox = (args.shared_root.resolve() / part / args.run_id for part in ('inbox', 'outbox'))
    if inbox.exists() or outbox.exists():
        raise ValueError('Run id already used; never redispatch a scientific run')
    inbox.mkdir(parents=True)
    shutil.copyfile(ROOT / SCRIPT, inbox / 'script.ps1')
    shutil.copyfile(PLAN, inbox / PLAN.name)
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
    commands.add_parser('preflight')
    report = commands.add_parser('analyze')
    report.add_argument('outbox', type=Path)
    run = commands.add_parser('run')
    run.add_argument('--run-id', required=True)
    run.add_argument('--shared-root', type=Path, required=True)
    for name, default in [('host', '127.0.0.1'), ('port', '2222'), ('user', 'jet3runner'),
                          ('identity', str(Path.home() / '.ssh/jet3-dao')),
                          ('remote-shared-root', r'\\host.lan\Data')]:
        run.add_argument('--' + name, default=os.environ.get('JET3_WINDOWS_' + name.upper().replace('-', '_'), default))
    args = parser.parse_args()
    if args.command == 'preflight':
        preflight()
        print('Pinned inputs and committed plan match.')
    elif args.command == 'analyze':
        analyze(args.outbox)
    else:
        dispatch(args)


if __name__ == '__main__':
    main()
