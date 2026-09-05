#!/usr/bin/env python3
"""EXP-0175: distinct retained fixed-field candidate validation, no retry."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import types
import fixed_field_successor as original

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/fixed-field-reuse.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/fixed_field_reuse.ps1'
identity, digest, canonical = original.identity, original.digest, original.canonical

# Private diagnostic interpretation only; original decoder and MDB bytes stay frozen.
spec = importlib.util.spec_from_file_location('reuse_catalog', ROOT / 'oracle/windows-dao/scripts/system_catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)
base_decode = catalog._decode_row

def decode_row(raw, columns, what):
    fixed, variables = catalog._row_layout(columns)
    if variables == 1 and fixed >= 256:
        trailer = len(raw) - (len(columns) + 7) // 8 - 4
        if not 256 <= fixed <= trailer < 512 or raw[trailer + 2] != 0:
            raise catalog.DecodeError('Unsupported wide fixed-prefix shape')
        # EXP-0172: both boundaries are in block one with a recorded zero jump.
        decoded = bytearray(raw)
        decoded[trailer + 2] = 3
        return base_decode(bytes(decoded), columns, what)
    return base_decode(raw, columns, what)

catalog._decode_row = decode_row
patch_globals = dict(original.patch_check.__globals__, catalog=catalog)
patch_check = types.FunctionType(original.patch_check.__code__, patch_globals)


def verify_inputs():
    plan = json.loads(PLAN.read_text())
    for name, sha in plan['inputs'].items():
        if digest(ROOT / name) != sha: raise ValueError('Input pin mismatch: ' + name)
    source = Path(plan['retained_root'])
    for name, ident in plan['retained'].items():
        if identity(source / name) != ident: raise ValueError('Retained identity mismatch: ' + name)
    return plan


def preflight():
    plan = verify_inputs()
    if os.name != 'posix' or subprocess.check_output(['git','show',f'HEAD:{PLAN.relative_to(ROOT)}'],cwd=ROOT) != PLAN.read_bytes():
        raise ValueError('Expected committed Unix plan')
    return plan


def source_updates(plan):
    source = Path(plan['retained_root'])
    result = json.loads((source/'result.json').read_text())
    if result['phase'] != 'unix_update' or result['plan_sha256'] != plan['original_plan_sha256'] or result['source_revision'] != plan['original_source_revision']:
        raise ValueError('Original failed-run identity')
    updates = {(u['arm'],u['replica']):u for u in result['updates']}
    wanted = {(a['name'],r) for a in plan['arms'] if a['name'] != 'fixed-text-255' for r in range(1,4)}
    if len(updates) != 27 or set(updates) != wanted: raise ValueError('Original 27-update inventory')
    return updates


def capture_entries(document, plan):
    if (document['document_type'] != 'dao_fixed_field_reuse_phase' or document['plan_sha256'] != digest(PLAN)
        or document['error'] is not None or document['retention_failures'] or document['mutation_started'] is not False
        or document['environment']['process_bits'] != 32 or document['environment']['provider'] != 'DAO.DBEngine.36'):
        raise ValueError('Read-only phase failure/environment')
    entries = {}
    wanted = {(a['name'],r,role) for a in plan['arms'] for r in range(1,4) for role in ('original','updated')}
    for item in document['observations']:
        key = item['arm'],item['replica'],item['role']; obs = item['observation']
        if key in entries or key not in wanted or obs['file'] != f'{key[0]}-r{key[1]}-{key[2]}.mdb' or obs['status'] != 'pass' or obs['error'] is not None or obs['before'] != obs['after']:
            raise ValueError('Observation identity or failure')
        entries[key] = obs
    if set(entries) != wanted: raise ValueError('Incomplete 60-capture inventory')
    return entries


def build_report(result, outbox, plan):
    observations, reasons = [], []
    try:
        if (result['document_type'] != 'dao_fixed_field_reuse_result' or result['plan_sha256'] != digest(PLAN)
            or result['source_revision'] != plan['source_revision'] or result['producer_os'] != 'posix'
            or result['error'] is not None or result['phase'] != 'complete'):
            raise ValueError('Coordinated acquisition failed: ' + str(result.get('error')))
        if identity(outbox/'observe.json') != result['observe']: raise ValueError('Capture receipt identity')
        entries = capture_entries(json.loads((outbox/'observe.json').read_text(encoding='utf-8-sig')),plan)
        source = Path(plan['retained_root']); old = source_updates(plan)
        create = json.loads((source/'create.json').read_text(encoding='utf-8-sig'))
        if create['plan_sha256'] != plan['original_plan_sha256'] or create['error'] is not None: raise ValueError('Original creation receipt')
        originals = {(o['arm'],o['replica']):o['observation'] for o in create['observations']}
        updates = {(u['arm'],u['replica']):u for u in result['updates']}
        wanted = {(a['name'],r) for a in plan['arms'] for r in range(1,4)}
        if len(updates) != 30 or set(updates) != wanted or set(originals) != wanted: raise ValueError('Update/original inventory')
        for arm in plan['arms']:
            for replica in range(1,4):
                key = arm['name'],replica; prefix=f'{key[0]}-r{replica}'; update=updates[key]
                new = arm['name']=='fixed-text-255'
                revision = plan['source_revision'] if new else plan['original_source_revision']
                if update['source_revision'] != revision or update['origin'] != ('new_public_update' if new else 'retained_update'):
                    raise ValueError('Per-case source binding')
                if not new and {k:v for k,v in update.items() if k not in ('source_revision','origin')} != old[key]:
                    raise ValueError('Frozen update receipt changed')
                before = entries[key+('original',)]; after=entries[key+('updated',)]
                original_path=outbox/f'{prefix}-original.mdb'; updated_path=outbox/f'{prefix}-updated.mdb'
                if (identity(original_path) != plan['retained'][original_path.name]
                    or identity(original_path) != before['after'] or identity(original_path) != originals[key]['after']
                    or identity(original_path) != update['original_before'] or identity(original_path) != update['original_after']
                    or identity(updated_path) != after['after'] or identity(updated_path) != update['updated']):
                    raise ValueError('Complete image identity chain')
                if not new and identity(updated_path) != plan['retained'][updated_path.name]: raise ValueError('Retained updated image changed')
                if original.normalized(before['snapshot']) != original.normalized(originals[key]['snapshot']): raise ValueError('Original snapshot changed')
                if not original.requested(before['snapshot'],plan,arm) or not original.requested(after['snapshot'],plan,arm,True): raise ValueError('Requested schema/payload bits')
                expected=original.normalized(before['snapshot']); table=next(t for t in expected['user_tables'] if t['name']==arm['table'])
                ordinal=next(i for i,f in enumerate(table['fields']) if f['name']==arm['column'])
                selected=[r for r in table['rows'] if int.from_bytes(bytes.fromhex(r[0]),'little',signed=True)==arm['selected_id']]
                if len(selected)!=1: raise ValueError('Selected row is not unique')
                selected[0][ordinal]=arm['replacement_hex']
                if original.normalized(expected)!=original.normalized(after['snapshot']): raise ValueError('Unrelated schema/rows/query differs')
                span=patch_check(original_path.read_bytes(),updated_path.read_bytes(),arm,update['locator'])
                observations.append(dict(arm=arm['name'],replica=replica,source_revision=revision,origin=update['origin'],original=identity(original_path),updated=identity(updated_path),patch=span,snapshot=after['snapshot']))
    except (ValueError,KeyError,TypeError,OSError,catalog.DecodeError) as error: reasons.append(str(error))
    return dict(document_type='dao_fixed_field_reuse_report',plan_sha256=digest(PLAN),outcome='observed_accepted' if not reasons else 'no_outcome',observations=observations,reasons=reasons,development_only=True,compatibility_claim=False,support_matrix_movement=False,original_outcome='no_outcome')


def analyze(outbox):
    plan=verify_inputs()
    if outbox.resolve().is_relative_to(Path(plan['retained_root']).resolve()): raise ValueError('Output inside original retained tree')
    report=build_report(json.loads((outbox/'result.json').read_text()),outbox,plan)
    report['result_sha256']=digest(outbox/'result.json');(outbox/'report.json').write_text(canonical(report)+'\n');print(report['outcome'])


def dispatch(args):
    plan=preflight()
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id): raise ValueError('Invalid run id')
    shared=args.shared_root.resolve();outbox=shared/'outbox'/args.run_id;run_id=args.run_id+'-observe';inbox=shared/'inbox'/run_id;guest=shared/'outbox'/run_id
    for path in (outbox,inbox,guest):
        if path.exists() or path.resolve().is_relative_to(Path(plan['retained_root']).resolve()): raise ValueError('Used or original output; no retry/resume')
    subprocess.run(['cargo','build','-p','jet3','--example','fixed_field_update_candidate'],cwd=ROOT,check=True)
    spec=importlib.util.spec_from_file_location('transport',ROOT/'scripts/windows-dao-ps.py');transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
    outbox.mkdir(parents=True)
    result=dict(document_type='dao_fixed_field_reuse_result',plan_sha256=digest(PLAN),source_revision=plan['source_revision'],acquisition_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),producer_os=os.name,phase='unix_update',error=None,updates=[])
    try:
        for name in plan['retained']:
            if name.endswith('.mdb'): shutil.copyfile(Path(plan['retained_root'])/name,outbox/name)
        old=source_updates(plan)
        for arm in plan['arms']:
            for replica in range(1,4):
                key=arm['name'],replica;prefix=f'{key[0]}-r{replica}';before_path=outbox/f'{prefix}-original.mdb';after_path=outbox/f'{prefix}-updated.mdb'
                if key in old:
                    update=dict(old[key],source_revision=plan['original_source_revision'],origin='retained_update')
                else:
                    before=identity(before_path)
                    command=['cargo','run','--quiet','-p','jet3','--example','fixed_field_update_candidate','--',str(before_path),str(after_path),arm['table'],str(arm['selected_id']),arm['column'],arm['name']]
                    done=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=120);(outbox/f'{prefix}-unix.txt').write_bytes(done.stdout+done.stderr)
                    if done.returncode: raise ValueError('Public update failed: '+prefix)
                    update=dict(arm=arm['name'],replica=replica,original_before=before,original_after=identity(before_path),updated=identity(after_path),locator=json.loads(done.stdout),source_revision=plan['source_revision'],origin='new_public_update')
                result['updates'].append(update)
                if identity(before_path)!=update['original_before'] or identity(before_path)!=update['original_after'] or identity(after_path)!=update['updated']: raise ValueError('Unix transfer/source identity')
                patch_check(before_path.read_bytes(),after_path.read_bytes(),arm,update['locator'])
        verify_inputs();result['phase']='observe';inbox.mkdir(parents=True)
        for name in (SCRIPT,'oracle/windows-dao/scripts/fixed_field_successor.ps1'):
            shutil.copyfile(ROOT/name,inbox/Path(name).name)
        shutil.copyfile(inbox/Path(SCRIPT).name,inbox/'script.ps1');shutil.copyfile(PLAN,inbox/PLAN.name)
        for path in outbox.glob('*.mdb'): shutil.copyfile(path,inbox/path.name)
        command=['ssh','-p',args.port,'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','IdentitiesOnly=yes','-i',args.identity,f'{args.user}@{args.host}','powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',transport.encoded(transport.guest_script(args.remote_shared_root,run_id,'script.ps1'))]
        done=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=300);(outbox/'observe-ssh.txt').write_bytes(done.stdout+done.stderr)
        shutil.copyfile(guest/'result.json',outbox/'observe.json');result['observe']=identity(outbox/'observe.json')
        entries=capture_entries(json.loads((outbox/'observe.json').read_text(encoding='utf-8-sig')),plan)
        if done.returncode: raise ValueError('Guest capture failed')
        for obs in entries.values():
            if identity(guest/obs['file'])!=obs['after'] or identity(outbox/obs['file'])!=obs['after']: raise ValueError('Retained guest image identity')
        verify_inputs();result['phase']='complete'
    except (OSError,ValueError,subprocess.SubprocessError) as error: result['error']=type(error).__name__+': '+str(error)
    finally: (outbox/'result.json').write_text(canonical(result)+'\n')
    analyze(outbox)


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('preflight');report=commands.add_parser('analyze');report.add_argument('outbox',type=Path)
    run=commands.add_parser('run');run.add_argument('--run-id',required=True);run.add_argument('--shared-root',type=Path,required=True)
    for name,default in [('host','127.0.0.1'),('port','2222'),('user','jet3runner'),('identity',str(Path.home()/'.ssh/jet3-dao')),('remote-shared-root',r'\\host.lan\Data')]:run.add_argument('--'+name,default=default)
    args=parser.parse_args()
    if args.command=='preflight':preflight();print('Committed input and retained pins match.')
    elif args.command=='analyze':analyze(args.outbox)
    else:dispatch(args)

if __name__=='__main__':main()
