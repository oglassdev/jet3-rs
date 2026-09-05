#!/usr/bin/env python3
"""EXP-0151: one phased DAO-create / Unix-public-update / DAO-read acquisition."""
import argparse
import copy
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
PLAN = ROOT / 'oracle/windows-dao/acquisition/field-update.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/field_update.ps1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return {'size': path.stat().st_size, 'sha256': digest(path)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def verify_inputs():
    plan = json.loads(PLAN.read_text())
    for name, expected in plan['inputs'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Input pin mismatch: ' + name)
    return plan


def preflight():
    plan = verify_inputs()
    if subprocess.check_output(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT) != PLAN.read_bytes():
        raise ValueError('Plan must be committed before acquisition')
    if os.name != 'posix' or plan['replicas'] != 3:
        raise ValueError('Expected Unix producer and three replicas')
    return plan


def normalized(snapshot):
    result = copy.deepcopy(snapshot)
    result['user_tables'].sort(key=lambda table: table['name'])
    for table in result['user_tables']:
        table['rows'].sort()
    return result


def requested(snapshot, plan, arm=None):
    actual = normalized(snapshot)
    if actual['version'] != '3.0' or actual['relations'] != []:
        return False
    if [t['name'] for t in actual['user_tables']] != sorted(t['name'] for t in plan['tables']):
        return False
    if len(actual['queries']) != 1 or actual['queries'][0]['name'] != plan['query']['name'] or not actual['queries'][0]['sql'] or actual['queries'][0]['type'] != 0:
        return False
    for spec in plan['tables']:
        table = next(t for t in actual['user_tables'] if t['name'] == spec['name'])
        if table['attributes'] != 0 or table['indexes'] != []:
            return False
        if [{k: field[k] for k in ('name', 'type', 'size')} for field in table['fields']] != plan['fields']:
            return False
        if any(field['attributes'] & 16 for field in table['fields']):
            return False
        rows = copy.deepcopy(spec['rows'])
        if arm and arm['table'] == spec['name']:
            column = next(i for i, field in enumerate(plan['fields']) if field['name'] == arm['column'])
            matches = [row for row in rows if row[0] == arm['selected_id']]
            if len(matches) != 1:
                return False
            matches[0][column] = arm['replacement']
        if table['rows'] != sorted(rows):
            return False
    return True


def patch_check(original, updated, arm, locator):
    definition, _, records = catalog._discover_catalog(original)
    name = catalog._ordinal(definition, 'Name')
    id_column = catalog._ordinal(definition, 'Id')
    roots = [row['values'][id_column] for row in records if row['values'][name] == arm['table']]
    if len(roots) != 1 or roots[0] != locator['root']:
        raise ValueError('Target catalog binding')
    table = catalog._definition(original, roots[0])
    columns = table['columns']
    ordinal = catalog._ordinal(table, arm['column'])
    id_column = catalog._ordinal(table, 'Id')
    column = columns[ordinal]
    if ordinal != locator['column'] or column['type'] != 'Long' or column['storage'] != 'fixed' or column['size'] != 4:
        raise ValueError('Target field binding')
    pages, _ = catalog._table_pages(original, table)
    rows = catalog._table_rows(original, table, pages)
    selected = [row for row in rows if row['values'][id_column] == arm['selected_id']]
    if len(selected) != 1 or selected[0]['page'] != locator['page'] or selected[0]['row'] != locator['slot'] or not selected[0]['present'][ordinal]:
        raise ValueError('Target row binding')
    image = catalog._page(original, locator['page'], 'target row')
    entry = catalog._row_directory(image, locator['page'])[locator['slot']]
    offset = locator['page'] * catalog.PAGE_BYTES + entry['start'] + 1 + column['fixed_offset']
    expected = bytearray(original)
    expected[offset:offset + 4] = arm['replacement'].to_bytes(4, 'little', signed=True)
    if expected != updated:
        raise ValueError('Bytes outside the requested field changed, or replacement differs')
    return {'offset': offset, 'length': 4, 'before_hex': original[offset:offset + 4].hex(),
            'after_hex': updated[offset:offset + 4].hex(),
            'changed_offsets': [i for i, (a, b) in enumerate(zip(original, updated)) if a != b]}


def phase_entries(phase, name, plan):
    if (phase['document_type'] != 'dao_field_update_phase' or phase['phase'] != name
        or phase['plan_sha256'] != digest(PLAN) or phase['error'] is not None
        or phase['mutation_started'] != (name == 'create')
        or phase['environment']['process_bits'] != 32 or phase['environment']['provider'] != 'DAO.DBEngine.36'):
        raise ValueError('Phase failed or has wrong identity: ' + name)
    roles = ['original'] if name == 'create' else ['original', 'updated']
    expected = {(arm['name'], replica, role) for arm in plan['arms'] for replica in range(1, 4) for role in roles}
    entries = {}
    for item in phase['observations']:
        key = (item['arm'], item['replica'], item['role'])
        if key in entries:
            raise ValueError('Duplicate observation')
        observation = item['observation']
        if observation['status'] != 'pass' or observation['error'] is not None or observation['before'] != observation['after']:
            raise ValueError('Read-only observation failed or mutated')
        entries[key] = observation
    if set(entries) != expected:
        raise ValueError('Incomplete phase observations')
    return entries


def build_report(result, outbox, plan):
    observations, reasons = [], []
    try:
        if (result['document_type'] != 'dao_field_update_result' or result['producer_os'] != 'posix'
            or result['source_revision'] != plan['source_revision']):
            raise ValueError('Unexpected result envelope or public Unix source receipt')
        if result['plan_sha256'] != digest(PLAN) or result['error'] is not None or result['phase'] != 'complete':
            raise ValueError('Coordinated acquisition failed: ' + str(result['error']))
        phases = {}
        for name in ('create', 'observe'):
            path = outbox / (name + '.json')
            if identity(path) != result['phases'][name]:
                raise ValueError('Phase result identity mismatch')
            phases[name] = phase_entries(json.loads(path.read_text(encoding='utf-8-sig')), name, plan)
        updates = {(u['arm'], u['replica']): u for u in result['updates']}
        if len(updates) != len(result['updates']) or set(updates) != {(a['name'], r) for a in plan['arms'] for r in range(1, 4)}:
            raise ValueError('Incomplete Unix updates')
        for arm in plan['arms']:
            for replica in range(1, 4):
                name = arm['name']; update = updates[name, replica]
                before = phases['create'][name, replica, 'original']
                after_original = phases['observe'][name, replica, 'original']
                after = phases['observe'][name, replica, 'updated']
                original = outbox / f'{name}-r{replica}-original.mdb'
                updated = outbox / f'{name}-r{replica}-updated.mdb'
                if (identity(original) != before['after'] or identity(original) != after_original['after']
                    or identity(original) != update['original_before'] or identity(original) != update['original_after']
                    or identity(updated) != after['before'] or identity(updated) != update['updated']):
                    raise ValueError('Image identity chain mismatch')
                for role, observation in [('original', before), ('original', after_original), ('updated', after)]:
                    if observation['file'] != f'{name}-r{replica}-{role}.mdb':
                        raise ValueError('Image filename association mismatch')
                if normalized(before['snapshot']) != normalized(after_original['snapshot']) or not requested(before['snapshot'], plan) or not requested(after['snapshot'], plan, arm):
                    raise ValueError('Original/requested schema or rows differ')
                expected = normalized(before['snapshot'])
                target = next(t for t in expected['user_tables'] if t['name'] == arm['table'])
                field = next(i for i, f in enumerate(plan['fields']) if f['name'] == arm['column'])
                next(row for row in target['rows'] if row[0] == arm['selected_id'])[field] = arm['replacement']
                if normalized(expected) != normalized(after['snapshot']):
                    raise ValueError('Unrelated schema, rows or query SQL differs')
                span = patch_check(original.read_bytes(), updated.read_bytes(), arm, update['locator'])
                observations.append({'arm': name, 'replica': replica, 'original': identity(original),
                                     'updated': identity(updated), 'patch': span, 'snapshot': after['snapshot'],
                                     'requested_change_and_preservation': True})
    except (KeyError, ValueError, TypeError, OSError, catalog.DecodeError) as error:
        reasons.append(str(error))
    return {'document_type': 'dao_field_update_report', 'development_only': True,
            'plan_sha256': digest(PLAN), 'outcome': 'observed_accepted' if not reasons else 'no_outcome',
            'reasons': reasons, 'observations': observations, 'support_matrix_movement': False,
            'compatibility_claim': False}


def analyze(outbox):
    plan = verify_inputs()
    result = json.loads((outbox / 'result.json').read_text())
    report = build_report(result, outbox, plan)
    report['result_sha256'] = digest(outbox / 'result.json')
    (outbox / 'report.json').write_text(canonical(report) + '\n')
    print(report['outcome'])


def dispatch(args):
    plan = preflight()
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}', args.run_id):
        raise ValueError('Invalid run id')
    shared = args.shared_root.resolve(); outbox = shared / 'outbox' / args.run_id
    paths = [outbox] + [shared / part / (args.run_id + '-' + phase) for part in ('inbox', 'outbox') for phase in ('create', 'observe')]
    if any(path.exists() for path in paths):
        raise ValueError('Run id used; never resume or retry')
    subprocess.run(['cargo', 'build', '-p', 'jet3', '--example', 'field_update_candidate'], cwd=ROOT, check=True)
    spec = importlib.util.spec_from_file_location('transport', ROOT / 'scripts/windows-dao-ps.py')
    transport = importlib.util.module_from_spec(spec); spec.loader.exec_module(transport)
    outbox.mkdir(parents=True)
    result = {'document_type': 'dao_field_update_result', 'plan_sha256': digest(PLAN),
              'source_revision': plan['source_revision'],
              'acquisition_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
              'producer_os': os.name, 'phase': 'create', 'phases': {}, 'updates': [], 'error': None}
    try:
        for phase in ('create', 'observe'):
            result['phase'] = phase
            run_id = args.run_id + '-' + phase; inbox = shared / 'inbox' / run_id; inbox.mkdir(parents=True)
            shutil.copyfile(ROOT / SCRIPT, inbox / 'script.ps1'); shutil.copyfile(PLAN, inbox / PLAN.name)
            (inbox / 'phase.txt').write_text(phase)
            if phase == 'observe':
                for path in outbox.glob('*.mdb'): shutil.copyfile(path, inbox / path.name)
            command = ['ssh', '-p', args.port, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'IdentitiesOnly=yes', '-i', args.identity,
                       f'{args.user}@{args.host}', 'powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                       transport.encoded(transport.guest_script(args.remote_shared_root, run_id, 'script.ps1'))]
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
            (outbox / (phase + '-ssh.txt')).write_bytes(completed.stdout + completed.stderr)
            guest = shared / 'outbox' / run_id
            shutil.copyfile(guest / 'result.json', outbox / (phase + '.json'))
            result['phases'][phase] = identity(outbox / (phase + '.json'))
            observations = phase_entries(json.loads((outbox / (phase + '.json')).read_text(encoding='utf-8-sig')), phase, plan)
            if completed.returncode != 0: raise ValueError('Guest phase exited unsuccessfully')
            if phase == 'create':
                for path in guest.glob('*.mdb'): shutil.copyfile(path, outbox / path.name)
                result['phase'] = 'unix_update'
                for arm in plan['arms']:
                    for replica in range(1, 4):
                        prefix = f"{arm['name']}-r{replica}"
                        original = outbox / (prefix + '-original.mdb'); updated = outbox / (prefix + '-updated.mdb')
                        before = identity(original)
                        if before != observations[arm['name'], replica, 'original']['after']: raise ValueError('Original transfer identity')
                        if not requested(observations[arm['name'], replica, 'original']['snapshot'], plan): raise ValueError('Original request mismatch')
                        command = ['cargo', 'run', '--quiet', '-p', 'jet3', '--example', 'field_update_candidate', '--', str(original), str(updated), arm['table'], str(arm['selected_id']), arm['column'], str(arm['replacement'])]
                        done = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=120)
                        (outbox / (prefix + '-unix.txt')).write_bytes(done.stdout + done.stderr)
                        if done.returncode: raise ValueError('Public field update failed: ' + prefix)
                        result['updates'].append({'arm': arm['name'], 'replica': replica, 'original_before': before,
                            'original_after': identity(original), 'updated': identity(updated), 'locator': json.loads(done.stdout)})
                        if identity(original) != before: raise ValueError('Unix update changed original')
                        patch_check(original.read_bytes(), updated.read_bytes(), arm, result['updates'][-1]['locator'])
        result['phase'] = 'complete'
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        (outbox / 'result.json').write_text(canonical(result) + '\n')
    analyze(outbox)


def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('preflight'); report = commands.add_parser('analyze'); report.add_argument('outbox', type=Path)
    run = commands.add_parser('run'); run.add_argument('--run-id', required=True); run.add_argument('--shared-root', type=Path, required=True)
    for name, default in [('host', '127.0.0.1'), ('port', '2222'), ('user', 'jet3runner'), ('identity', str(Path.home() / '.ssh/jet3-dao')), ('remote-shared-root', r'\\host.lan\Data')]:
        run.add_argument('--' + name, default=default)
    args = parser.parse_args()
    if args.command == 'preflight': preflight(); print('Committed inputs match.')
    elif args.command == 'analyze': analyze(args.outbox)
    else: dispatch(args)


if __name__ == '__main__': main()
