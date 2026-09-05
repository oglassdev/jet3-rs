#!/usr/bin/env python3
"""Pinned local relationship observations; preflight, acquire once, and analyze."""
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
PLAN = ROOT / 'oracle/windows-dao/acquisition/relationship-create.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/relationship_create.ps1'
CHECKPOINTS = ('base', 'first', 'second')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return {'size': path.stat().st_size, 'sha256': digest(path)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def verify_inputs():
    plan = json.loads(PLAN.read_text())
    if plan['experiment_id'] != 'relationship-create' or plan['replicas'] != 3:
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


def leaf_entries(data, root):
    """EXP-0062 leaf boundaries and shared prefix; keys remain uninterpreted."""
    page = catalog._page(data, root, 'relationship index')
    if page[0] != 4 or any(page[8:20]):
        raise catalog.DecodeError('Expected a single leaf with no sibling/child links')
    area = page[248:]
    prefix_length = int.from_bytes(page[20:22], 'little')
    ends = [bit for bit in range(len(area) + 1) if page[22 + bit // 8] & (1 << (bit % 8))]
    used = ends[-1] if ends else 0
    if prefix_length > used or int.from_bytes(page[2:4], 'little') != len(area) - used:
        raise catalog.DecodeError('Leaf prefix/free-space mismatch')
    entries = []
    start = prefix_length
    for end in ends:
        if end <= start:
            raise catalog.DecodeError('Leaf boundary order mismatch')
        raw = area[:prefix_length] + area[start:end]
        if end - start < 4 or len(raw) <= 4:
            raise catalog.DecodeError('Leaf entry lacks a row locator')
        entries.append({'key_hex': raw[:-4].hex(), 'row_page': int.from_bytes(raw[-4:-1], 'big'), 'row': raw[-1]})
        start = end
    return {'root': root, 'prefix_hex': area[:prefix_length].hex(), 'entries': entries}


def observe(data):
    analysis = catalog.analyze_checkpoint(data)
    named = {table['name']: table for table in analysis['tables'].values()}
    result = {'page_count': len(data) // catalog.PAGE_BYTES, 'global_free_pages': analysis['free_pages'],
              'page0_transition_byte': data[1538], 'tables': {}, 'system_rows': {}}
    for name in ('Parent', 'Child', 'MSysRelationships'):
        definition = named[name]['definition']
        physical = [{key: value for key, value in index.items() if key != 'entry_count_offset'}
                    for index in definition['physical_indexes']]
        for index in physical:
            index['map_pages'] = sorted(catalog._locator_pages(data, index['map'], name + ' index'))
        result['tables'][name] = {
            'root': definition['root'], 'logical_indexes': definition['logical_indexes'],
            'physical_indexes': physical, 'row_count': definition['row_count'],
            'maps': {kind: {'locator': locator, 'pages': sorted(catalog._locator_pages(data, locator, name))}
                     for kind, locator in definition['maps'].items()},
            'index_leaves': [leaf_entries(data, index['root']) for index in physical],
        }
    for name in ('MSysObjects', 'MSysACEs', 'MSysRelationships'):
        rows = catalog._generic_rows(analysis['system_rows'][name], analysis)
        if name == 'MSysObjects':
            rows = [{key: row[key] for key in ('Id', 'ParentId', 'Name', 'Type', 'Flags', 'Owner')}
                    for row in rows if row['Type'] == 8]
        elif name == 'MSysACEs':
            ids = {row['Id'] for row in result['system_rows']['MSysObjects']}
            rows = [row for row in rows if row['ObjectId'] in ids]
        result['system_rows'][name] = rows
    return result


def correlate(observation, relations):
    expected_rows = sorted(({
        'szRelationship': relation['name'], 'grbit': 0, 'ccolumn': 1, 'icolumn': 0,
        'szObject': relation['foreign_table'], 'szColumn': relation['fields'][0]['foreign_name'],
        'szReferencedObject': relation['table'], 'szReferencedColumn': relation['fields'][0]['name'],
    } for relation in relations), key=canonical)
    rows = observation['system_rows']
    if sorted(rows['MSysRelationships'], key=canonical) != expected_rows:
        return 'MSysRelationships rows do not match DAO relation fields'
    if sorted(row['Name'] for row in rows['MSysObjects']) != sorted(row['name'] for row in relations):
        return 'Relationship catalog names do not match DAO relations'
    for name, other, side in [('Parent', 'Child', 1), ('Child', 'Parent', 2)]:
        indexes = observation['tables'][name]['logical_indexes']
        related = [index for index in indexes if index['class'] == 2]
        if len(related) != len(relations):
            return 'Reciprocal logical-record count does not match DAO relations'
        for index in related:
            raw = bytes.fromhex(index['raw_hex'])
            if raw[8] != side or int.from_bytes(raw[13:17], 'little') != observation['tables'][other]['root'] or raw[17:19] != b'\0\0':
                return 'Reciprocal side/reference/cascade context does not match DAO relations'
    return None


def build_report(result, outbox):
    if (result['document_type'] != 'dao_relationship_create_result'
            or result['plan_sha256'] != digest(PLAN) or result['development_only'] is not True
            or result['environment']['process_bits'] != 32
            or result['environment']['provider'] != 'DAO.DBEngine.36'):
        raise ValueError('Result identity/environment mismatch')
    observations, reasons = [], []
    expected = [(replica, checkpoint) for replica in range(1, 4) for checkpoint in CHECKPOINTS]
    for position, checkpoint in enumerate(result['checkpoints']):
        pair = (checkpoint['replica'], checkpoint['checkpoint'])
        if position >= len(expected) or pair != expected[position]:
            raise ValueError('Unexpected checkpoint inventory/order')
        filename = f'relationship-r{pair[0]}-{pair[1]}.mdb'
        if checkpoint['file'] != filename or identity(outbox / filename) != checkpoint['after']:
            raise ValueError('Retained checkpoint identity mismatch')
        if checkpoint['before'] != checkpoint['after']:
            reasons.append(f'{pair}: read-only metadata open changed bytes')
        try:
            decoded = observe((outbox / filename).read_bytes())
            relations = sorted(checkpoint['relations'], key=lambda row: row['name'])
            wanted = []
            for name, parent, child in [('ParentChild', 'Id', 'ParentId'), ('AlternateLink', 'Alternate', 'Alternate')][:CHECKPOINTS.index(pair[1])]:
                wanted.append({'name': name, 'table': 'Parent', 'foreign_table': 'Child', 'attributes': 0,
                               'fields': [{'name': parent, 'foreign_name': child}]})
            if relations != sorted(wanted, key=lambda row: row['name']):
                reasons.append(f'{pair}: DAO relationship metadata mismatch')
            disagreement = correlate(decoded, relations)
            if disagreement:
                reasons.append(f'{pair}: {disagreement}')
            decoded['dao_relations'] = relations
            observations.append({'replica': pair[0], 'checkpoint': pair[1], 'observation': decoded})
        except (catalog.DecodeError, KeyError, ValueError) as error:
            reasons.append(f'{pair}: {error}')
    if len(observations) != 9 or result['error'] is not None or result['mutation_started'] is not True:
        reasons.append('Acquisition incomplete or failed: ' + str(result['error']))
    if len(observations) == 9:
        for index in range(3):
            if any(observations[index]['observation'] != observations[index + offset]['observation'] for offset in (3, 6)):
                reasons.append(f'{CHECKPOINTS[index]}: question-bearing values disagree across replicas')
    return {'document_type': 'dao_relationship_create_report', 'development_only': True,
            'plan_sha256': digest(PLAN), 'outcome': 'answered' if not reasons else 'no_outcome',
            'reasons': reasons, 'observations': observations, 'compatibility_claim': False,
            'support_matrix_movement': False}


def analyze(outbox):
    verify_inputs()
    result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
    report = build_report(result, outbox)
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
