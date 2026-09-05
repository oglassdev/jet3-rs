#!/usr/bin/env python3
"""Pinned local Long index-key observations; preflight, acquire once, and analyze."""
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
from relationship_create import leaf_entries

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/long-key-layout.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/long_key_layout.ps1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return {'size': path.stat().st_size, 'sha256': digest(path)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def verify_inputs():
    plan = json.loads(PLAN.read_text())
    if plan['experiment_id'] != 'long-key-layout' or plan['replicas'] != 3:
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


def hypothesized_key(values, arm):
    """Test hypothesis only: marked, sign-flipped BE Long components; descending complement."""
    key = b''
    for field in arm['fields']:
        value = values[['A', 'B', 'Tag'].index(field['name'])]
        component = b'\x7f' + (value ^ -(1 << 31)).to_bytes(4, 'big', signed=True)
        key += bytes(byte ^ 255 for byte in component) if field['descending'] else component
    return key.hex()


def observe(data, arm):
    # The named user root is derived through the typed catalog decoder.
    catalog_definition, _, objects = catalog._discover_catalog(data)
    name = catalog._ordinal(catalog_definition, 'Name')
    kind = catalog._ordinal(catalog_definition, 'Type')
    ident = catalog._ordinal(catalog_definition, 'Id')
    roots = [row['values'][ident] for row in objects
             if row['values'][name] == 'Rows' and row['values'][kind] == 1]
    if len(roots) != 1:
        raise catalog.DecodeError('Expected one Rows table')
    definition = catalog._definition(data, roots[0])
    if ([(column['name'], column['type'], column['size']) for column in definition['columns']]
            != [(name, 'Long', 4) for name in ('A', 'B', 'Tag')]
            or len(definition['physical_indexes']) != 1 or len(definition['logical_indexes']) != 1):
        raise catalog.DecodeError('Unexpected physical schema/index inventory')
    pages, long_values = catalog._table_pages(data, definition)
    if long_values:
        raise catalog.DecodeError('Unexpected long-value pages')
    rows = catalog._table_rows(data, definition, pages)
    if sorted(row['values'] for row in rows) != sorted(arm['rows']) or definition['row_count'] != len(rows):
        raise catalog.DecodeError('Decoded rows disagree with planned inputs')
    index = definition['physical_indexes'][0]
    leaf = leaf_entries(data, index['root'])
    raw_page = catalog._page(data, index['root'], 'Long key leaf')
    if int.from_bytes(raw_page[4:8], 'little') != definition['root']:
        raise catalog.DecodeError('Index owner mismatch')
    bindings = bind_rows(rows, leaf['entries'], arm)
    return {'definition_root': definition['root'], 'row_count': definition['row_count'],
            'physical_index': index, 'logical_indexes': definition['logical_indexes'],
            'data_pages': pages, 'index_pages': sorted(catalog._locator_pages(data, index['map'], 'Long index')),
            'prefix_hex': leaf['prefix_hex'], 'bindings': bindings,
            'hypothesis_matches': all(row['key_hex'] == row['hypothesis_key_hex'] for row in bindings)}


def bind_rows(rows, entries, arm):
    by_locator = {(row['page'], row['row']): row['values'] for row in rows}
    bindings, seen = [], set()
    for entry in entries:
        locator = (entry['row_page'], entry['row'])
        if locator not in by_locator or locator in seen:
            raise catalog.DecodeError('Index row locator missing or repeated')
        seen.add(locator)
        values = by_locator[locator]
        bindings.append(dict(entry, values=values, hypothesis_key_hex=hypothesized_key(values, arm)))
    if seen != set(by_locator):
        raise catalog.DecodeError('Index does not cover every row')
    return bindings


def key_values(row, arm):
    return tuple(row[['A', 'B', 'Tag'].index(field['name'])] * (-1 if field['descending'] else 1)
                 for field in arm['fields'])


def correlate(snapshot, decoded, arm):
    expected_fields = [{'name': name, 'type': 4, 'size': 4} for name in ('A', 'B', 'Tag')]
    indexes = snapshot.get('indexes', [])
    if (snapshot.get('version') != '3.0' or snapshot.get('fields') != expected_fields
            or snapshot.get('tables') != ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Rows']
            or len(indexes) != 1):
        return False
    index = indexes[0]
    if (index['name'] != 'ByKey' or index['primary'] is not False
            or index['unique'] != arm['unique'] or index['fields'] != arm['fields']):
        return False
    rows, traversal = snapshot.get('rows', []), snapshot.get('traversal', [])
    if sorted(rows) != sorted(arm['rows']) or sorted(traversal) != sorted(rows):
        return False
    wanted_order = sorted(key_values(row, arm) for row in rows)
    # Equal full keys may appear in either payload order in DAO traversal.
    return ([key_values(row, arm) for row in traversal] == wanted_order
            and [key_values(entry['values'], arm) for entry in decoded['bindings']] == wanted_order)


def comparable(observation):
    decoded = observation['decoded']
    # Page/slot identities are retained, but allocator locations and equal-key
    # payload order are not the question-bearing comparison across replicas.
    return {'snapshot': dict(observation['snapshot'],
                rows=sorted(observation['snapshot']['rows']),
                traversal=sorted(observation['snapshot']['traversal'])),
            'bindings': sorted([(entry['values'], entry['key_hex']) for entry in decoded['bindings']]),
            'entry_count': decoded['physical_index']['entry_count'],
            'flags': decoded['physical_index']['flags'], 'keys': decoded['physical_index']['keys'],
            'hypothesis_matches': decoded['hypothesis_matches']}


def build_report(result, outbox, plan):
    if (result['document_type'] != 'dao_long_key_layout_result'
            or result['plan_sha256'] != digest(PLAN) or result['development_only'] is not True
            or result['environment']['process_bits'] != 32
            or result['environment']['provider'] != 'DAO.DBEngine.36'):
        raise ValueError('Result identity/environment mismatch')
    expected = [(arm['name'], replica) for arm in plan['arms'] for replica in range(1, 4)]
    actual = [(entry['arm'], entry['replica']) for entry in result['replicas']]
    if actual != expected[:len(actual)]:
        raise ValueError('Unexpected arm/replica inventory')
    observations, reasons = [], []
    arms = {arm['name']: arm for arm in plan['arms']}
    for entry in result['replicas']:
        label = f"{entry['arm']}-r{entry['replica']}"
        if entry['file'] != label + '.mdb' or identity(outbox / entry['file']) != entry['after']:
            raise ValueError('Retained image identity mismatch')
        if entry['before'] != entry['after']:
            reasons.append(label + ': read-only bytes changed')
        try:
            if entry['status'] != 'pass' or entry['endpoint'] != 'complete' or entry['error'] is not None:
                raise ValueError('DAO observation did not complete: ' + str(entry['error']))
            decoded = observe((outbox / entry['file']).read_bytes(), arms[entry['arm']])
            if not correlate(entry['snapshot'], decoded, arms[entry['arm']]):
                raise ValueError('DAO/decoded row or metadata correlation failed')
            observations.append({'arm': entry['arm'], 'replica': entry['replica'],
                                 'snapshot': entry['snapshot'], 'decoded': decoded})
        except (catalog.DecodeError, KeyError, ValueError) as error:
            reasons.append(label + ': ' + str(error))
    if len(observations) != len(expected) or result['error'] is not None or result['mutation_started'] is not True:
        reasons.append('Acquisition incomplete or failed: ' + str(result['error']))
    for arm in arms:
        group = [comparable(item) for item in observations if item['arm'] == arm]
        if group and any(item != group[0] for item in group):
            reasons.append(arm + ': question-bearing observations differ across replicas')
    return {'document_type': 'dao_long_key_layout_report', 'development_only': True,
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
