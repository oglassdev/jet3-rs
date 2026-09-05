#!/usr/bin/env python3
"""EXP-0157: bounded deletion layout observations, not a deletion writer."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

import system_catalog as catalog

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/row-delete-layout.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/row_delete_layout.ps1'


def identity(path):
    return {'size': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def verify_inputs():
    plan = json.loads(PLAN.read_text())
    for name, expected in plan['inputs'].items():
        if identity(ROOT / name)['sha256'] != expected:
            raise ValueError('Input pin mismatch: ' + name)
    return plan


def preflight():
    plan = verify_inputs()
    if subprocess.check_output(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT) != PLAN.read_bytes():
        raise ValueError('Plan must be committed before acquisition')
    return plan


def expected_rows(arm, checkpoint, plan):
    rows = copy.deepcopy(arm['rows'])
    if checkpoint != 'before': rows = [row for row in rows if row[0] != arm['delete_id']]
    if checkpoint == 'inserted': rows.append(plan['insert'])
    return sorted(rows)


def check_snapshot(snapshot, rows):
    expected = {'version': '3.0', 'tables': ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Rows'],
        'relations': [], 'queries': [], 'attributes': 0, 'indexes': [],
        'fields': [{'name': name, 'type': 4, 'size': 4, 'attributes': 1} for name in ('Id', 'Value')], 'rows': rows}
    snapshot = copy.deepcopy(snapshot); snapshot['rows'].sort()
    if snapshot != expected: raise ValueError('DAO schema or requested rows differ')


def data_page(data, page):
    image = catalog._page(data, page, 'retained row page')
    entries = None
    if image[0] == 1:
        entries = catalog._row_directory(image, page)
        for entry in entries:
            offset = 10 + 2 * entry['row']
            entry['raw_word'] = int.from_bytes(image[offset:offset + 2], 'little')
            entry['raw_hex'] = image[entry['start']:entry['end']].hex()
    return {'page': page, 'hex': image.hex(), 'directory': entries}


def observe(data, wanted):
    analysis = catalog.analyze_checkpoint(data)
    tables = [table for table in analysis['tables'].values() if table['name'] == 'Rows']
    if len(tables) != 1: raise ValueError('Rows table not unique')
    table = tables[0]; definition = table['definition']
    rows = catalog._table_rows(data, definition, table['data_pages'])
    if definition['row_count'] != len(wanted) or sorted(row['values'] for row in rows) != wanted or any(not all(row['present']) for row in rows):
        raise ValueError('Raw rows/count disagree with requested DAO rows')
    if [(c['name'], c['type'], c['size']) for c in definition['columns']] != [('Id', 'Long', 4), ('Value', 'Long', 4)]:
        raise ValueError('Raw column schema mismatch')
    return {'definition_root': definition['root'], 'row_count': definition['row_count'], 'rows': rows,
            'definition_pages': [{'page': p, 'hex': catalog._page(data, p, 'TDEF').hex()} for p in definition['pages']],
            'header_hex': data[:catalog.PAGE_BYTES].hex(), 'page_count': len(data) // catalog.PAGE_BYTES,
            'maps': {role: {'locator': locator, 'hex': catalog._locator_row(data, locator, role).hex(),
                           'pages': sorted(catalog._locator_pages(data, locator, role))} for role, locator in definition['maps'].items()},
            'data_pages': [data_page(data, p) for p in table['data_pages']],
            'global_free_pages': analysis['free_pages']}


def changed_ranges(before, after):
    ranges, start = [], None
    for offset in range(max(len(before), len(after)) + 1):
        different = offset < max(len(before), len(after)) and before[offset:offset + 1] != after[offset:offset + 1]
        if different and start is None: start = offset
        if not different and start is not None:
            ranges.append({'offset': start, 'end': offset, 'before_hex': before[start:offset].hex(), 'after_hex': after[start:offset].hex()})
            start = None
    return ranges


def transition(before_data, after_data, before, after):
    pages = sorted({p['page'] for p in before['data_pages'] + after['data_pages']})
    tracked = []
    for page in pages:
        states = []
        for data, observation in [(before_data, before), (after_data, after)]:
            states.append({'image': data_page(data, page) if page < observation['page_count'] else None,
                           'owned': page in observation['maps']['owned']['pages'],
                           'available': page in observation['maps']['available']['pages'],
                           'globally_free': page in observation['global_free_pages']})
        tracked.append({'page': page, 'before': states[0], 'after': states[1]})
    changes = changed_ranges(before_data, after_data)
    return {'row_count_before': before['row_count'], 'row_count_after': after['row_count'],
            'page_count_before': before['page_count'], 'page_count_after': after['page_count'],
            'changed_ranges': changes,
            'changed_pages': sorted({p for change in changes for p in range(change['offset'] // catalog.PAGE_BYTES, (change['end'] - 1) // catalog.PAGE_BYTES + 1)}),
            'tracked_data_pages': tracked,
            'global_free_added': sorted(set(after['global_free_pages']) - set(before['global_free_pages'])),
            'global_free_removed': sorted(set(before['global_free_pages']) - set(after['global_free_pages']))}


def question_signature(checkpoints, transitions):
    # Absolute allocator addresses and opaque catalog/timestamp differences are retained diagnostics.
    signature = {'checkpoints': [], 'transitions': []}
    for name in ('before', 'deleted', 'inserted'):
        obs = checkpoints[name]
        signature['checkpoints'].append({'name': name, 'row_count': obs['row_count'],
            'rows': [{k: row[k] for k in ('row', 'present', 'values')} for row in obs['rows']]})
    for movement in transitions:
        states = []
        for tracked in movement['tracked_data_pages']:
            pair = []
            for role in ('before', 'after'):
                state = tracked[role]; image = state['image']
                raw = bytes.fromhex(image['hex']) if image else None
                pair.append({'owned': state['owned'], 'available': state['available'], 'globally_free': state['globally_free'],
                             'page_bytes_without_owner': (raw[:4] + raw[8:]).hex() if raw else None,
                             'directory': image['directory'] if image else None})
            states.append(pair)
        signature['transitions'].append({'tracked': states,
            'row_delta': movement['row_count_after'] - movement['row_count_before'],
            'page_delta': movement['page_count_after'] - movement['page_count_before'],
            'global_added_count': len(movement['global_free_added']), 'global_removed_count': len(movement['global_free_removed'])})
    return signature


def build_report(result, outbox, plan):
    observations, reasons, signatures = [], [], {}
    expected = {(a['name'], replica, checkpoint) for a in plan['arms'] for replica in range(1, 4) for checkpoint in plan['checkpoints']}
    entries, decoded, images = {}, {}, {}
    try:
        if (result['document_type'] != 'dao_row_delete_layout_result' or result['plan_sha256'] != identity(PLAN)['sha256']
            or result['mutation_started'] is not True or result['error'] is not None
            or result['environment']['process_bits'] != 32 or result['environment']['provider'] != 'DAO.DBEngine.36'):
            raise ValueError('Acquisition incomplete or failed: ' + str(result['error']))
        for capture in result['captures']:
            key = (capture['arm'], capture['replica'], capture['checkpoint'])
            if key in entries or key not in expected: raise ValueError('Unexpected/duplicate checkpoint')
            arm = next(a for a in plan['arms'] if a['name'] == capture['arm']); observation = capture['observation']
            filename = f'{key[0]}-r{key[1]}-{key[2]}.mdb'
            if capture['file'] != filename or observation['status'] != 'pass' or observation['error'] is not None:
                raise ValueError('Failed/misassociated checkpoint')
            path = outbox / filename
            if observation['before'] != observation['after'] or identity(path) != observation['after']:
                raise ValueError('Retained checkpoint identity changed')
            wanted = expected_rows(arm, capture['checkpoint'], plan); check_snapshot(observation['snapshot'], wanted)
            image = path.read_bytes(); raw = observe(image, wanted)
            entries[key] = capture; decoded[key] = raw; images[key] = image
        if set(entries) != expected: raise ValueError('Missing checkpoint')
        for arm in plan['arms']:
            groups = []
            for replica in range(1, 4):
                checks = {name: decoded[arm['name'], replica, name] for name in plan['checkpoints']}
                movements = [transition(images[arm['name'], replica, first], images[arm['name'], replica, last], checks[first], checks[last])
                             for first, last in [('before', 'deleted'), ('deleted', 'inserted')]]
                signature = question_signature(checks, movements); groups.append(signature)
                observations.append({'arm': arm['name'], 'replica': replica, 'checkpoints': checks, 'transitions': movements,
                    'identities': {name: entries[arm['name'], replica, name]['observation']['after'] for name in plan['checkpoints']},
                    'deleted_has_zero_length_c000': any(entry['raw_word'] & 0xe000 == 0xc000 and entry['start'] == entry['end']
                        for tracked in movements[0]['tracked_data_pages'] if tracked['after']['image'] and tracked['after']['image']['directory']
                        for entry in tracked['after']['image']['directory'])})
            signatures[arm['name']] = all(group == groups[0] for group in groups)
            if not signatures[arm['name']]: reasons.append(arm['name'] + ': question-bearing replica disagreement')
    except (ValueError, KeyError, TypeError, OSError) as error:
        reasons.append(str(error))
    return {'document_type': 'dao_row_delete_layout_report', 'plan_sha256': identity(PLAN)['sha256'],
            'development_only': True, 'outcome': 'answered' if not reasons else 'no_outcome', 'reasons': reasons,
            'observations': observations, 'replica_agreement': signatures, 'compatibility_claim': False, 'support_matrix_movement': False}


def analyze(outbox):
    plan = verify_inputs(); result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
    report = build_report(result, outbox, plan); report['result_sha256'] = identity(outbox / 'result.json')['sha256']
    (outbox / 'report.json').write_text(canonical(report) + '\n'); print(report['outcome'])


def dispatch(args):
    preflight()
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,32}', args.run_id): raise ValueError('Invalid run id')
    inbox, outbox = (args.shared_root.resolve() / part / args.run_id for part in ('inbox', 'outbox'))
    if inbox.exists() or outbox.exists(): raise ValueError('Run id used; never retry or resume')
    inbox.mkdir(parents=True); shutil.copyfile(ROOT / SCRIPT, inbox / 'script.ps1'); shutil.copyfile(PLAN, inbox / PLAN.name)
    spec = importlib.util.spec_from_file_location('transport', ROOT / 'scripts/windows-dao-ps.py')
    transport = importlib.util.module_from_spec(spec); spec.loader.exec_module(transport)
    command = ['ssh', '-p', args.port, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'IdentitiesOnly=yes', '-i', args.identity,
               f'{args.user}@{args.host}', 'powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
               transport.encoded(transport.guest_script(args.remote_shared_root, args.run_id, 'script.ps1'))]
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
    if not (outbox / 'result.json').exists(): raise RuntimeError(f'No result (SSH {completed.returncode}); inspect retained run, never retry')
    analyze(outbox)


def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('preflight'); report = commands.add_parser('analyze'); report.add_argument('outbox', type=Path)
    run = commands.add_parser('run'); run.add_argument('--run-id', required=True); run.add_argument('--shared-root', type=Path, required=True)
    for name, default in [('host', '127.0.0.1'), ('port', '2222'), ('user', 'jet3runner'), ('identity', str(Path.home() / '.ssh/jet3-dao')), ('remote-shared-root', r'\\host.lan\Data')]: run.add_argument('--' + name, default=default)
    args = parser.parse_args()
    if args.command == 'preflight': preflight(); print('Committed inputs match.')
    elif args.command == 'analyze': analyze(args.outbox)
    else: dispatch(args)


if __name__ == '__main__': main()
