#!/usr/bin/env python3
"""Pinned finite scalar/null index observations; no key transform is assumed."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

import system_catalog as catalog
from relationship_create import leaf_entries

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/scalar-index-layout.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/scalar_index_layout.ps1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return {'size': path.stat().st_size, 'sha256': digest(path)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def verify_inputs():
    plan = json.loads(PLAN.read_text())
    if plan['experiment_id'] != 'scalar-index-layout' or plan['replicas'] != 3:
        raise ValueError('Unexpected plan')
    for name, expected in plan['inputs'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Input pin mismatch: ' + name)
    return plan


def preflight():
    plan = verify_inputs()
    committed = subprocess.run(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT,
                               check=True, capture_output=True).stdout
    if committed != PLAN.read_bytes():
        raise ValueError('Plan must be committed before acquisition')
    return plan


def physical(value, field):
    """Normalize existing row decoding to physical value bytes, never index bytes."""
    kind = field['type']
    if value is None or kind == 1:
        return value
    if kind in (2, 3, 4):
        return value.to_bytes(field['size'], 'little', signed=kind != 2).hex()
    if kind == 8:
        return struct.pack('<d', value).hex()
    return value if kind == 9 else value['raw_hex']


def semantic_key(row, arm):
    values = []
    for value, field, direction in zip(row['values'], arm['fields'], arm['directions']):
        if field['type'] == 1:
            number = int(value)
        else:
            raw = bytes.fromhex(value)
            if field['type'] in (6, 7, 8):
                number = struct.unpack('<f' if field['type'] == 6 else '<d', raw)[0]
            elif field['type'] == 9:
                number = int.from_bytes(raw, 'big')
            else:
                number = int.from_bytes(raw, 'little', signed=field['type'] != 2)
        values.append(-number if direction else number)
    return tuple(values)


def observe(data, arm):
    definition, _, objects = catalog._discover_catalog(data)
    names, kinds, ids = [catalog._ordinal(definition, name) for name in ('Name', 'Type', 'Id')]
    roots = [row['values'][ids] for row in objects
             if row['values'][names] == 'Rows' and row['values'][kinds] == 1]
    if len(roots) != 1:
        raise catalog.DecodeError('Expected one Rows table')
    definition = catalog._definition(data, roots[0])
    if len(definition['physical_indexes']) != 1 or len(definition['logical_indexes']) != 1:
        raise catalog.DecodeError('Expected one physical/logical index')
    pages, long_values = catalog._table_pages(data, definition)
    if long_values:
        raise catalog.DecodeError('Unexpected long values')
    rows = catalog._table_rows(data, definition, pages)
    fields = arm['fields'] + [{'name': 'Tag', 'type': 4, 'size': 4}]
    expected_types = {1: 'Boolean', 2: 'Byte', 3: 'Integer', 4: 'Long', 5: 'Currency',
                      6: 'Single', 7: 'Double', 8: 'Date', 9: 'Binary'}
    if [(c['name'], c['type'], c['size']) for c in definition['columns']] != [
            (f['name'], expected_types[f['type']], f['size']) for f in fields]:
        raise catalog.DecodeError('Decoded schema differs')
    by_locator = {(row['page'], row['row']): {'tag': row['values'][-1],
                  'values': [physical(value, field) for value, field in zip(row['values'], arm['fields'])]}
                  for row in rows}
    index = definition['physical_indexes'][0]
    leaf = leaf_entries(data, index['root'])
    raw = catalog._page(data, index['root'], 'scalar leaf')
    if int.from_bytes(raw[4:8], 'little') != definition['root']:
        raise catalog.DecodeError('Index owner differs')
    bindings, seen = [], set()
    for entry in leaf['entries']:
        locator = (entry['row_page'], entry['row'])
        if locator not in by_locator or locator in seen:
            raise catalog.DecodeError('Missing or repeated index row locator')
        seen.add(locator)
        bindings.append({**entry, **by_locator[locator]})
    omitted = [row for locator, row in by_locator.items() if locator not in seen]
    if any(None not in row['values'] for row in omitted):
        raise catalog.DecodeError('Index omitted a non-null row')
    return {'definition_root': definition['root'], 'row_count': definition['row_count'],
            'physical_index': index, 'logical_indexes': definition['logical_indexes'],
            'data_pages': pages, 'index_pages': sorted(catalog._locator_pages(data, index['map'], 'index')),
            'rows': sorted(by_locator.values(), key=lambda row: row['tag']), 'bindings': bindings,
            'omitted_rows': sorted(omitted, key=lambda row: row['tag']), 'prefix_hex': leaf['prefix_hex']}


def correlate(entry, decoded, arm):
    snapshot = entry['snapshot']
    fields = arm['fields'] + [{'name': 'Tag', 'type': 4, 'size': 4}]
    expected_index = {'name': 'ByKey', 'primary': False, 'unique': arm['unique'],
                      'required': arm['required'], 'ignore_nulls': arm['ignore_nulls'],
                      'fields': [{'name': f['name'], 'descending': d, 'attributes': int(d)}
                                 for f, d in zip(arm['fields'], arm['directions'])]}
    if (snapshot['version'] != '3.0' or snapshot['fields'] != fields
            or snapshot['indexes'] != [expected_index]
            or snapshot['tables'] != ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Rows']):
        raise ValueError('DAO schema differs from declared control')
    operations = entry['operations']
    if [op['tag'] for op in operations] != list(range(1, len(arm['rows']) + 1)):
        raise ValueError('Incomplete insertion attempts')
    if any(op['status'] not in ('updated', 'rejected') or op['endpoint'] != 'update' for op in operations):
        raise ValueError('Unexpected operation result')
    accepted = [op['tag'] for op in operations if op['status'] == 'updated']
    rows = sorted(snapshot['rows'], key=lambda row: row['tag'])
    if ([row['tag'] for row in rows] != accepted or rows != decoded['rows']
            or decoded['row_count'] != len(rows)):
        raise ValueError('Saved rows do not match accepted attempts/decoded rows')
    traversal = snapshot['traversal']
    bound = [{'tag': b['tag'], 'values': b['values']} for b in decoded['bindings']]
    if sorted(traversal, key=lambda row: row['tag']) != sorted(bound, key=lambda row: row['tag']):
        raise ValueError('DAO traversal differs from raw key bindings')
    # Equal full values may have different tag order; null ordering is a question.
    if [row['values'] for row in traversal] != [row['values'] for row in bound]:
        raise ValueError('DAO traversal value order differs from raw leaf')
    if arm['family'] == 'scalar' and [semantic_key(row, arm) for row in traversal] != sorted(
            semantic_key(row, arm) for row in traversal):
        raise ValueError('Scalar traversal is not in declared direction')
    return all(row['values'] == arm['rows'][row['tag'] - 1] for row in rows)


def comparable(observation):
    decoded = observation['decoded']
    return {'operations': [{k: op[k] for k in ('tag', 'status', 'endpoint', 'native_codes', 'hresult')}
                           for op in observation['operations']],
            'snapshot': {k: v for k, v in observation['snapshot'].items() if k not in ('rows', 'traversal')},
            'rows': decoded['rows'], 'bindings': sorted((b['tag'], b['values'], b['key_hex']) for b in decoded['bindings']),
            'omitted_rows': decoded['omitted_rows'], 'keys': decoded['physical_index']['keys'],
            'flags': decoded['physical_index']['flags'], 'entry_count': decoded['physical_index']['entry_count'],
            'requested_payloads_match': observation['requested_payloads_match']}


def build_report(result, outbox, plan):
    if (result['document_type'] != 'dao_scalar_index_layout_result' or result['plan_sha256'] != digest(PLAN)
            or result['development_only'] is not True or result['environment']['process_bits'] != 32
            or result['environment']['provider'] != 'DAO.DBEngine.36'):
        raise ValueError('Result binding/environment mismatch')
    expected = [(arm['name'], replica) for arm in plan['arms'] for replica in range(1, 4)]
    actual = [(e['arm'], e['replica']) for e in result['replicas']]
    if actual != expected[:len(actual)]:
        raise ValueError('Unexpected replica inventory')
    arms = {arm['name']: arm for arm in plan['arms']}
    observations, reasons = [], []
    for entry in result['replicas']:
        label = f"{entry['arm']}-r{entry['replica']}"
        if entry['file'] != label + '.mdb' or identity(outbox / entry['file']) != entry['after']:
            raise ValueError('Retained image identity mismatch')
        if entry['before'] != entry['after']:
            reasons.append(label + ': read-only bytes changed')
        try:
            if entry['status'] != 'pass' or entry['error'] is not None:
                raise ValueError('DAO capture failed: ' + str(entry['error']))
            decoded = observe((outbox / entry['file']).read_bytes(), arms[entry['arm']])
            matching = correlate(entry, decoded, arms[entry['arm']])
            observations.append({**{k: entry[k] for k in ('arm', 'replica', 'operations', 'snapshot')},
                                 'decoded': decoded, 'requested_payloads_match': matching})
        except (catalog.DecodeError, KeyError, ValueError) as error:
            reasons.append(label + ': ' + str(error))
    if len(observations) != len(expected) or result['error'] is not None or result['mutation_started'] is not True:
        reasons.append('Acquisition incomplete or failed: ' + str(result['error']))
    for arm in arms:
        group = [comparable(o) for o in observations if o['arm'] == arm]
        if group and any(o != group[0] for o in group):
            reasons.append(arm + ': question-bearing replica disagreement')
    return {'document_type': 'dao_scalar_index_layout_report', 'development_only': True,
            'plan_sha256': digest(PLAN), 'outcome': 'answered' if not reasons else 'no_outcome',
            'reasons': reasons, 'observations': observations, 'compatibility_claim': False,
            'support_matrix_movement': False}


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
