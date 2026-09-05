#!/usr/bin/env python3
"""EXP-0187: phased public slot-preserving deletion and DAO continuation, no retries."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import field_update as common
import row_delete_layout as layout

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/row-delete-compaction.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/row_delete_compaction.ps1'
identity, canonical = common.identity, common.canonical


def verify_inputs():
    plan = json.loads(PLAN.read_text())
    for name, sha in plan['inputs'].items():
        if identity(ROOT / name)['sha256'] != sha: raise ValueError('Input pin mismatch: ' + name)
    return plan


def preflight():
    plan = verify_inputs()
    if os.name != 'posix' or subprocess.check_output(['git','show',f'HEAD:{PLAN.relative_to(ROOT)}'],cwd=ROOT) != PLAN.read_bytes():
        raise ValueError('Expected committed Unix plan')
    return plan


def expected(snapshot, arm, role, plan):
    value = common.normalized(snapshot)
    tables = copy.deepcopy(arm['tables'])
    target = next(t for t in tables if t['name'] == arm['table'])
    if role != 'original': target['rows'] = [r for r in target['rows'] if r[0] not in arm['selected_ids']]
    if role.endswith('-next'): target['rows'].append(plan['insert'])
    if (value['version'] != '3.0' or value['relations'] != []
        or value['tables'] != sorted(['MSysACEs','MSysObjects','MSysQueries','MSysRelationships']+[t['name'] for t in tables])
        or [t['name'] for t in value['user_tables']] != sorted(t['name'] for t in tables)
        or len(value['queries']) != 1 or value['queries'][0]['name'] != plan['query']['name']
        or value['queries'][0]['type'] != 0 or not value['queries'][0]['sql']):
        raise ValueError('Requested database inventory')
    for spec in tables:
        table = next(t for t in value['user_tables'] if t['name'] == spec['name'])
        if (table['attributes'] != 0 or table['indexes'] != [] or table['rows'] != sorted(spec['rows'])
            or [{k:f[k] for k in ('name','type','size')} for f in table['fields']] != plan['fields']
            or [f['attributes'] for f in table['fields']] != [1,1,2]):
            raise ValueError('Requested table schema/rows')
    return value


def shape(data, arm):
    catalog = layout.catalog
    definition, _, records = catalog._discover_catalog(data)
    name, ident = [catalog._ordinal(definition, n) for n in ('Name','Id')]
    roots = [r['values'][ident] for r in records if r['values'][name] == arm['table']]
    if len(roots) != 1: raise ValueError('Target table binding')
    table = catalog._definition(data, roots[0]); pages, lval = catalog._table_pages(data, table)
    if lval or table['physical_indexes']: raise ValueError('Unsupported source shape')
    rows = catalog._table_rows(data, table, pages)
    if table['row_count'] != len(rows): raise ValueError('Raw row count')
    return table, rows, {k:sorted(catalog._locator_pages(data,v,k)) for k,v in table['maps'].items()}


def patch_check(before, after, arm, receipts):
    initial_table, initial_rows, initial_maps = shape(before, arm)
    if len(receipts)!=len(arm['selected_ids']): raise ValueError('Step receipt inventory')
    working=bytearray(before); steps=[]
    for selected,receipt in zip(arm['selected_ids'],receipts):
        table,rows,maps=shape(working,arm)
        matches=[r for r in rows if r['values'][0]==selected]
        if len(matches)!=1: raise ValueError('Delete Id not unique')
        row=matches[0];page,slot=row['page'],row['row']
        if receipt!=dict(root=table['root'],page=page,slot=slot): raise ValueError('Step locator binding')
        raw=layout.catalog._page(working,page,'target'); entries=layout.catalog._row_directory(raw,page)
        tomb=lambda e:e['hidden'] and e['overflow'] and e['start']==e['end']
        if any((e['hidden'] or e['overflow'] or e['start']==e['end']) and not tomb(e) for e in entries): raise ValueError('Unsupported source slot')
        if sum(not tomb(e) for e in entries)<2 or tomb(entries[slot]): raise ValueError('Sole/empty source')
        if page not in maps['available'] or int.from_bytes(raw[2:4],'little')!=entries[-1]['start']-10-2*len(entries): raise ValueError('Source free/map metadata')
        prior=bytes(working);end=2048;base=page*2048
        # Repack surviving complete row byte strings in stable slot order; retain slack.
        for e in entries:
            if e['row']==slot or tomb(e): word=end|0xc000
            else:
                payload=raw[e['start']:e['end']];start=end-len(payload)
                working[base+start:base+end]=payload;end=start;word=start
            offset=base+10+2*e['row'];working[offset:offset+2]=word.to_bytes(2,'little')
        working[base+2:base+4]=(end-10-2*len(entries)).to_bytes(2,'little')
        root=table['root']*2048;working[root+12:root+16]=(len(rows)-1).to_bytes(4,'little')
        steps.append(dict(selected_id=selected,locator=receipt,deleted_payload_hex=raw[entries[slot]['start']:entries[slot]['end']].hex(),changes=layout.changed_ranges(prior,working)))
    if working!=after: raise ValueError('Exact compaction/offset/free/count/slack/page0 preservation')
    table,rows,maps=shape(after,arm)
    survivors=[r for r in initial_rows if r['values'][0] not in arm['selected_ids']]
    bindings=lambda rows:sorted((r['page'],r['row'],r['values']) for r in rows)
    if maps!=initial_maps or bindings(rows)!=bindings(survivors): raise ValueError('Stable row locators/values/maps')
    return dict(steps=steps,row_count=table['row_count'],maps=maps,changes=layout.changed_ranges(before,after),page0_unchanged=before[:2048]==after[:2048])


def entries(document, phase, plan):
    if (document['document_type']!='dao_row_delete_compaction_phase' or document['phase']!=phase
        or document['plan_sha256']!=identity(PLAN)['sha256'] or document['error'] is not None
        or document['retention_failures'] or document['mutation_started'] is not True
        or document['environment']!={'process_bits':32,'provider':'DAO.DBEngine.36'}):
        raise ValueError('Failed/incomplete phase')
    roles=['original','control'] if phase=='create' else ['original','control','rust','control-next','rust-next']
    wanted={(a['name'],r,role) for a in plan['arms'] for r in range(1,4) for role in roles}
    result={}
    for item in document['observations']:
        key=(item['arm'],item['replica'],item['role']); obs=item['observation']
        if key in result or key not in wanted or obs['file']!=f'{key[0]}-r{key[1]}-{key[2]}.mdb' or obs['status']!='pass' or obs['error'] is not None or obs['before']!=obs['after']:
            raise ValueError('Observation binding/failure')
        result[key]=obs
    if set(result)!=wanted: raise ValueError('Missing capture')
    op_roles=['control'] if phase=='create' else ['control-next','rust-next']
    operations={(o['arm'],o['replica'],o['role']):o['result'] for o in document['operations']}
    if len(operations)!=len(document['operations']) or set(operations)!={(a['name'],r,role) for a in plan['arms'] for r in range(1,4) for role in op_roles}: raise ValueError('Missing operation')
    for a in plan['arms']:
        for r in range(1,4):
            for role in op_roles:
                if operations[a['name'],r,role]!=dict(operation='delete' if phase=='create' else 'insert',status='complete',selected_ids=a['selected_ids']): raise ValueError('Operation failed')
    return result


def build_report(result, outbox, plan):
    observations=[]; reasons=[]
    try:
        if (result['document_type']!='dao_row_delete_compaction_result' or result['producer_os']!='posix' or result['source_revision']!=plan['source_revision'] or result['plan_sha256']!=identity(PLAN)['sha256'] or result['phase']!='complete' or result['error'] is not None): raise ValueError('Coordinated phase/source failure')
        phases={}
        for phase in ('create','observe'):
            path=outbox/(phase+'.json')
            if identity(path)!=result['phases'][phase]: raise ValueError('Phase identity')
            phases[phase]=entries(json.loads(path.read_text(encoding='utf-8-sig')),phase,plan)
        updates={(u['arm'],u['replica']):u for u in result['updates']}
        if len(updates)!=len(result['updates']) or set(updates)!={(a['name'],r) for a in plan['arms'] for r in range(1,4)}: raise ValueError('Update inventory')
        for arm in plan['arms']:
            for replica in range(1,4):
                prefix=f"{arm['name']}-r{replica}"; snapshots={}; images={}
                for role in ('original','control','rust','control-next','rust-next'):
                    item=phases['observe'][arm['name'],replica,role]; path=outbox/f'{prefix}-{role}.mdb'
                    if identity(path)!=item['after']: raise ValueError('Retained identity')
                    images[role]=identity(path); snapshots[role]=expected(item['snapshot'],arm,role,plan)
                    if role in ('original','control') and item!=phases['create'][arm['name'],replica,role]: raise ValueError('Original/control changed between phases')
                update=updates[arm['name'],replica]
                if update['original_before']!=images['original'] or update['original_after']!=images['original'] or update['rust']!=images['rust']: raise ValueError('Unix identity chain')
                before=(outbox/f'{prefix}-original.mdb').read_bytes(); rust=(outbox/f'{prefix}-rust.mdb').read_bytes()
                patch=patch_check(before,rust,arm,update['locator'])
                if snapshots['rust']!=snapshots['control'] or snapshots['rust-next']!=snapshots['control-next']: raise ValueError('DAO control differs')
                baseline=copy.deepcopy(snapshots['original']); target=next(t for t in baseline['user_tables'] if t['name']==arm['table']); target['rows']=[r for r in target['rows'] if r[0] not in arm['selected_ids']]
                if baseline!=snapshots['rust']: raise ValueError('Unrelated metadata/SQL changed')
                target['rows'].append(plan['insert']); baseline=common.normalized(baseline)
                if baseline!=snapshots['rust-next']: raise ValueError('Follow-on metadata/SQL changed')
                control_maps=shape((outbox/f'{prefix}-control.mdb').read_bytes(),arm)[2]
                if control_maps!=patch['maps']: raise ValueError('Control map membership differs')
                observations.append(dict(arm=arm['name'],replica=replica,identities=images,patch=patch,snapshot=snapshots['rust-next']))
    except (ValueError,KeyError,TypeError,OSError,layout.catalog.DecodeError) as error: reasons.append(str(error))
    return dict(document_type='dao_row_delete_compaction_report',plan_sha256=identity(PLAN)['sha256'],outcome='observed_accepted' if not reasons else 'no_outcome',reasons=reasons,observations=observations,development_only=True,compatibility_claim=False,support_matrix_movement=False)


def analyze(outbox):
    plan=verify_inputs(); report=build_report(json.loads((outbox/'result.json').read_text()),outbox,plan)
    report['result_sha256']=identity(outbox/'result.json')['sha256']; (outbox/'report.json').write_text(canonical(report)+'\n'); print(report['outcome'])


def dispatch(args):
    plan=preflight()
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id): raise ValueError('Invalid run id')
    shared=args.shared_root.resolve(); outbox=shared/'outbox'/args.run_id
    paths=[outbox]+[shared/part/(args.run_id+'-'+phase) for part in ('inbox','outbox') for phase in ('create','observe')]
    if any(p.exists() for p in paths): raise ValueError('Run used; no retry/resume')
    subprocess.run(['cargo','build','-p','jet3','--example','row_delete_compaction'],cwd=ROOT,check=True)
    spec=importlib.util.spec_from_file_location('transport',ROOT/'scripts/windows-dao-ps.py'); transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
    outbox.mkdir(parents=True)
    result=dict(document_type='dao_row_delete_compaction_result',producer_os=os.name,source_revision=plan['source_revision'],acquisition_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),plan_sha256=identity(PLAN)['sha256'],phase='create',error=None,phases={},updates=[])
    try:
        for phase in ('create','observe'):
            result['phase']=phase; run_id=args.run_id+'-'+phase; inbox=shared/'inbox'/run_id;inbox.mkdir(parents=True)
            shutil.copyfile(ROOT/SCRIPT,inbox/'script.ps1');shutil.copyfile(ROOT/'oracle/windows-dao/scripts/field_update.ps1',inbox/'field_update.ps1');shutil.copyfile(PLAN,inbox/PLAN.name);(inbox/'phase.txt').write_text(phase)
            if phase=='observe':
                for path in outbox.glob('*.mdb'): shutil.copyfile(path,inbox/path.name)
            command=['ssh','-p',args.port,'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','IdentitiesOnly=yes','-i',args.identity,f'{args.user}@{args.host}','powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',transport.encoded(transport.guest_script(args.remote_shared_root,run_id,'script.ps1'))]
            done=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=300)
            (outbox/(phase+'-ssh.txt')).write_bytes(done.stdout+done.stderr);guest=shared/'outbox'/run_id
            shutil.copyfile(guest/'result.json',outbox/(phase+'.json'));result['phases'][phase]=identity(outbox/(phase+'.json'))
            for path in guest.glob('*.mdb'):
                destination=outbox/path.name
                if destination.exists() and identity(destination)!=identity(path): raise ValueError('Transferred original/control/Rust image changed')
                if not destination.exists(): shutil.copyfile(path,destination)
            observed=entries(json.loads((outbox/(phase+'.json')).read_text(encoding='utf-8-sig')),phase,plan)
            if done.returncode: raise ValueError('Guest failed')
            for arm in plan['arms']:
                for replica in range(1,4):
                    for role in ('original','control'):
                        item=observed[arm['name'],replica,role];expected(item['snapshot'],arm,role,plan)
                        if identity(outbox/item['file'])!=item['after']: raise ValueError('Transferred image identity')
            if phase=='create':
                result['phase']='unix_delete'
                for arm in plan['arms']:
                    for replica in range(1,4):
                        prefix=f"{arm['name']}-r{replica}"; original=outbox/(prefix+'-original.mdb');rust=outbox/(prefix+'-rust.mdb');before=identity(original)
                        command=['cargo','run','--quiet','-p','jet3','--example','row_delete_compaction','--',str(original),str(rust),arm['table'],*[str(v) for v in arm['selected_ids']]]
                        done=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=120);(outbox/(prefix+'-unix.txt')).write_bytes(done.stdout+done.stderr)
                        if done.returncode: raise ValueError('Public delete failed: '+prefix)
                        receipt=json.loads(done.stdout);patch_check(original.read_bytes(),rust.read_bytes(),arm,receipt)
                        result['updates'].append(dict(arm=arm['name'],replica=replica,original_before=before,original_after=identity(original),rust=identity(rust),locator=receipt))
                        if identity(original)!=before: raise ValueError('Unix original changed')
        result['phase']='complete'
    except (OSError,ValueError,subprocess.SubprocessError) as error: result['error']=type(error).__name__+': '+str(error)
    finally: (outbox/'result.json').write_text(canonical(result)+'\n')
    analyze(outbox)


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('preflight');report=commands.add_parser('analyze');report.add_argument('outbox',type=Path)
    run=commands.add_parser('run');run.add_argument('--run-id',required=True);run.add_argument('--shared-root',type=Path,required=True)
    for name,default in [('host','127.0.0.1'),('port','2222'),('user','jet3runner'),('identity',str(Path.home()/'.ssh/jet3-dao')),('remote-shared-root',r'\\host.lan\Data')]:run.add_argument('--'+name,default=default)
    args=parser.parse_args()
    if args.command=='preflight':preflight();print('Committed inputs match.')
    elif args.command=='analyze':analyze(args.outbox)
    else:dispatch(args)


if __name__=='__main__':main()
