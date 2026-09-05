#!/usr/bin/env python3
"""EXP-0181: phased public row insertion and DAO continuation, no retries."""
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
import field_update as common
import row_delete_layout as layout

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/eof-insert-candidate.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/eof_insert_candidate.ps1'
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
    if arm['delete_id'] is not None: target['rows'] = [r for r in target['rows'] if r[0] != arm['delete_id']]
    if role != 'original': target['rows'].append(arm['insert'])
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


def patch_check(before, after, arm, receipt):
    table, rows, maps = shape(before, arm)
    page = len(before) // 2048
    if len(before)%2048 or len(after)!=len(before)+2048 or receipt!=dict(root=table['root'],page=page,slot=0):
        raise ValueError('Exactly one EOF page/slot required')
    if arm['name']=='empty-table' and rows: raise ValueError('Expected empty source table')
    if arm['name']!='empty-table' and not rows: raise ValueError('Expected populated source table')
    ident,value,text=arm['insert'];payload=text.encode('ascii')
    if len(payload)>246: raise ValueError('Finite narrow Text scope')
    encoded=bytes([3])+ident.to_bytes(4,'little',signed=True)+value.to_bytes(4,'little',signed=True)+payload+bytes([9+len(payload),9,1,7])
    for owned in maps['owned']:
        image=layout.catalog._page(before,owned,'original data')
        entries=layout.catalog._row_directory(image,owned)
        if not entries or any(e['hidden'] or e['overflow'] for e in entries): raise ValueError('Natural ordinary populated page required')
        free=entries[-1]['start']-10-2*len(entries)
        if int.from_bytes(image[2:4],'little')!=free: raise ValueError('Inconsistent original free field')
        if owned in maps['available'] and free>=len(encoded)+2: raise ValueError('Existing page already fits this row')
    expected=bytearray(before);changes=[]
    def map_bit(locator, role, was_set, set_value):
        raw=layout.catalog._locator_row(before,locator,role)
        if len(raw)<6 or raw[0]!=0: raise ValueError('Inline map required')
        relative=page-int.from_bytes(raw[1:5],'little')
        if not 0<=relative<8*(len(raw)-5): raise ValueError('EOF beyond existing inline map capacity')
        entry=layout.catalog._row_directory(layout.catalog._page(before,locator['page'],role),locator['page'])[locator['row']]
        offset=locator['page']*2048+entry['start']+5+relative//8;mask=1<<(relative%8)
        if bool(before[offset]&mask)!=was_set: raise ValueError('Original map bit mismatch')
        expected[offset]=(expected[offset]|mask) if set_value else (expected[offset]&~mask)
        changes.append(dict(role=role,offset=offset,mask=mask,before=bool(was_set),after=bool(set_value)))
    map_bit(table['maps']['owned'],'owned',False,True)
    map_bit(table['maps']['available'],'available',False,True)
    map_bit(dict(page=1,row=0),'global_free',True,False)
    root=table['root']*2048
    expected[root+12:root+16]=(len(rows)+1).to_bytes(4,'little')
    image=bytearray(2048);image[:2]=bytes([1,1]);image[2:4]=(2036-len(encoded)).to_bytes(2,'little')
    image[4:8]=table['root'].to_bytes(4,'little');image[8:10]=bytes([1,0]);image[10:12]=(2048-len(encoded)).to_bytes(2,'little');image[-len(encoded):]=encoded
    expected.extend(image)
    if expected!=after: raise ValueError('Exact append/map/count or unrelated original-prefix preservation')
    after_table,after_rows,after_maps=shape(after,arm)
    if after_maps!={k:sorted(set(v)|{page}) for k,v in maps.items()} or sorted(r['values'] for r in after_rows)!=sorted([r['values'] for r in rows]+[arm['insert']]):
        raise ValueError('Post-append rows/maps')
    return dict(locator=receipt,row_count=after_table['row_count'],maps=after_maps,map_changes=changes,new_page_sha256=hashlib.sha256(image).hexdigest(),inserted_payload_hex=encoded.hex(),page0_unchanged=before[:2048]==after[:2048])


def entries(document, phase, plan):
    if (document['document_type']!='dao_eof_insert_candidate_phase' or document['phase']!=phase
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
                if operations[a['name'],r,role]!=dict(operation='insert' if phase=='create' else 'continue',status='complete'): raise ValueError('Operation failed')
    return result


def build_report(result, outbox, plan):
    observations=[]; reasons=[]
    try:
        if (result['document_type']!='dao_eof_insert_candidate_result' or result['producer_os']!='posix' or result['source_revision']!=plan['source_revision'] or result['plan_sha256']!=identity(PLAN)['sha256'] or result['phase']!='complete' or result['error'] is not None): raise ValueError('Coordinated phase/source failure')
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
                baseline=copy.deepcopy(snapshots['original']); target=next(t for t in baseline['user_tables'] if t['name']==arm['table']); target['rows'].append(arm['insert'])
                if common.normalized(baseline)!=snapshots['rust']: raise ValueError('Unrelated metadata/SQL changed')
                target['rows'].append(plan['insert']); baseline=common.normalized(baseline)
                if baseline!=snapshots['rust-next']: raise ValueError('Follow-on metadata/SQL changed')
                observations.append(dict(arm=arm['name'],replica=replica,identities=images,patch=patch,snapshot=snapshots['rust-next']))
    except (ValueError,KeyError,TypeError,OSError,layout.catalog.DecodeError) as error: reasons.append(str(error))
    return dict(document_type='dao_eof_insert_candidate_report',plan_sha256=identity(PLAN)['sha256'],outcome='observed_accepted' if not reasons else 'no_outcome',reasons=reasons,observations=observations,development_only=True,compatibility_claim=False,support_matrix_movement=False)


def analyze(outbox):
    plan=verify_inputs(); report=build_report(json.loads((outbox/'result.json').read_text()),outbox,plan)
    report['result_sha256']=identity(outbox/'result.json')['sha256']; (outbox/'report.json').write_text(canonical(report)+'\n'); print(report['outcome'])


def dispatch(args):
    plan=preflight()
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id): raise ValueError('Invalid run id')
    shared=args.shared_root.resolve(); outbox=shared/'outbox'/args.run_id
    paths=[outbox]+[shared/part/(args.run_id+'-'+phase) for part in ('inbox','outbox') for phase in ('create','observe')]
    if any(p.exists() for p in paths): raise ValueError('Run used; no retry/resume')
    subprocess.run(['cargo','build','-p','jet3','--example','row_insert_candidate'],cwd=ROOT,check=True)
    spec=importlib.util.spec_from_file_location('transport',ROOT/'scripts/windows-dao-ps.py'); transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
    outbox.mkdir(parents=True)
    result=dict(document_type='dao_eof_insert_candidate_result',producer_os=os.name,source_revision=plan['source_revision'],acquisition_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),plan_sha256=identity(PLAN)['sha256'],phase='create',error=None,phases={},updates=[])
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
                result['phase']='unix_insert'
                for arm in plan['arms']:
                    for replica in range(1,4):
                        prefix=f"{arm['name']}-r{replica}"; original=outbox/(prefix+'-original.mdb');rust=outbox/(prefix+'-rust.mdb');before=identity(original)
                        command=['cargo','run','--quiet','-p','jet3','--example','row_insert_candidate','--',str(original),str(rust),arm['table'],*[str(v) for v in arm['insert']]]
                        done=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=120);(outbox/(prefix+'-unix.txt')).write_bytes(done.stdout+done.stderr)
                        if done.returncode: raise ValueError('Public insert failed: '+prefix)
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
