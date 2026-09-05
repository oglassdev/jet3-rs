#!/usr/bin/env python3
"""EXP-0199 finite empty Memo/OLE discovery; stable provider negatives are observations."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import field_update as common
import system_catalog as catalog
ROOT=Path(__file__).resolve().parents[3]
PLAN=ROOT/'oracle/windows-dao/acquisition/empty-long-values.plan.json'
SCRIPT='oracle/windows-dao/scripts/empty_long_values.ps1'
identity,canonical=common.identity,common.canonical

def require(condition,message):
    if not condition:raise ValueError(message)

def verify_inputs():
    plan=json.loads(PLAN.read_text())
    for name,sha in plan['inputs'].items():require(identity(ROOT/name)['sha256']==sha,'Input pin mismatch: '+name)
    return plan

def preflight():
    plan=verify_inputs();require(subprocess.check_output(['git','show',f'HEAD:{PLAN.relative_to(ROOT)}'],cwd=ROOT)==PLAN.read_bytes(),'Plan must be committed');return plan

def operations(values):
    require(len(values)==3,'Attempt inventory')
    result=[]
    for item,(ident,state) in zip(values,[(1,'null'),(2,'empty'),(3,'one')]):
        require(item['id']==ident and item['state']==state and isinstance(item['accepted'],bool),'Attempt binding')
        error=item['error']
        if item['accepted']:require(error is None,'Accepted operation error')
        else:
            require(state=='empty' and isinstance(error,dict) and error['endpoint'] in ('empty/assign','empty/update') and error['numbers'] and all(isinstance(n,int) and n>0 for n in error['numbers']),'Unexpected mutation failure')
        result.append(dict(id=ident,state=state,accepted=item['accepted'],error=None if error is None else {k:error[k] for k in ('endpoint','type','hresult','numbers')}))
    return result

def snapshot(value,arm,ops):
    value=copy.deepcopy(value)
    require(value['version']=='3.0' and value['tables']==['MSysACEs','MSysObjects','MSysQueries','MSysRelationships','Rows'] and value['relations']==value['queries']==value['indexes']==[] and value['attributes']==0,'Database inventory')
    require([{k:f[k] for k in ('name','type','size')} for f in value['fields']]==[dict(name='Id',type=4,size=4),dict(name='Payload',type=arm['type'],size=0)],'Field schema')
    if arm['type']==12:
        require(isinstance(value['fields'][1]['allow_zero_length'],bool),'Memo property capture')
        if arm['allow_zero_length']:require(value['fields'][1]['allow_zero_length'] is True,'Requested AllowZeroLength')
    for field in value['fields']:
        properties=field['properties'];require(len({p['name'] for p in properties})==len(properties),'Duplicate field property')
        require(all(isinstance(p['name'],str) and isinstance(p['type'],int) and isinstance(p['is_null'],bool) and ((p['value'] is None)==p['is_null']) for p in properties),'Property capture')
        field['properties']=sorted(properties,key=lambda p:p['name'])
    rows=sorted(value['rows'],key=lambda r:r['id']);require([r['id'] for r in rows]==[op['id'] for op in ops if op['accepted']],'Saved row inventory')
    for row in rows:
        require(isinstance(row['is_null'],bool) and (row['payload'] is None)==row['is_null'] and isinstance(row['field_size'],int) and row['field_size']>=0,'Payload state')
        if row['id']==1:require(row['is_null'],'Null control differs')
        elif row['id']==3:require(row['payload']==('A' if arm['type']==12 else '41'),'One-byte control differs')
        else:require(row['payload'] in (None,''),'Empty attempt returned nonempty payload')
    value['rows']=rows;return value

def raw_observation(data,arm,saved):
    definition,_,records=catalog._discover_catalog(data);n,i=[catalog._ordinal(definition,k) for k in ('Name','Id')]
    roots=[r['values'][i] for r in records if r['values'][n]=='Rows'];require(len(roots)==1,'Raw table binding')
    table=catalog._definition(data,roots[0]);pages,lval=catalog._table_pages(data,table);rows=catalog._table_rows(data,table,pages)
    require(not table['physical_indexes'] and not table['logical_indexes'],'Unexpected raw indexes')
    require([(c['name'],c['type']) for c in table['columns']]==[('Id','Long'),('Payload','Memo' if arm['type']==12 else 'LongBinary')],'Raw column schema')
    require(table['row_count']==len(rows)==len(saved),'Raw count')
    observed=[]
    for row in rows:
        require(row['present'][0] and row['values'][0] in [s['id'] for s in saved],'Raw Id binding')
        wanted=next(s for s in saved if s['id']==row['values'][0]);require(row['present'][1]==(not wanted['is_null']),'Null mask/readback correlation')
        image=catalog._page(data,row['page'],'data');slot=catalog._row_directory(image,row['page'])[row['row']];raw=image[slot['start']:slot['end']]
        # This two-column small-row layout was already validated by _decode_row.
        require(len(raw)<=255 and raw[-2]==1,'Small single-variable row scope')
        start,end=raw[-3],raw[-4];field=raw[start:end]
        descriptor=None
        if len(field)>=12:
            control=int.from_bytes(field[:4],'little')
            descriptor=dict(header_hex=field[:12].hex(),declared_length=control&0xffffff,flags=control&0xff000000,inline_payload_hex=field[12:].hex() if control&0xff000000==0x80000000 else None)
        observed.append(dict(id=wanted['id'],page=row['page'],slot=row['row'],row_hex=raw.hex(),presence_mask_hex=raw[-1:].hex(),present=row['present'][1],field_start=start,field_end=end,field_hex=field.hex(),descriptor=descriptor,dao=wanted))
    require(sorted(r['id'] for r in observed)==[s['id'] for s in saved],'Unique raw row inventory')
    maps=[]
    for group in table['long_value_maps']:
        maps.append(dict(column=group['column'],owned=sorted(catalog._locator_pages(data,group['owned'],'owned')),available=sorted(catalog._locator_pages(data,group['available'],'available'))))
    require(len(maps)==1 and maps[0]['column']==1,'Long-column map coverage')
    return dict(root=table['root'],rows=sorted(observed,key=lambda r:r['id']),table_maps={k:sorted(catalog._locator_pages(data,v,k)) for k,v in table['maps'].items()},long_value_maps=maps,long_value_pages=lval)

def build_report(result,outbox,plan):
    observations=[];reasons=[]
    try:
        require(result['document_type']=='dao_empty_long_values_result' and result['plan_sha256']==identity(PLAN)['sha256'] and result['error'] is None and result['retention_failures']==[] and result['mutation_started'] is True and result['environment']==dict(process_bits=32,provider='DAO.DBEngine.36'),'Acquisition failure')
        cases={(c['arm'],c['replica']):c for c in result['cases']};require(len(cases)==len(result['cases']) and set(cases)=={(a['name'],r) for a in plan['arms'] for r in range(1,4)},'Case inventory')
        for arm in plan['arms']:
            signatures=[]
            for replica in range(1,4):
                case=cases[arm['name'],replica];ops=operations(case['operations']);capture=case['capture'];path=outbox/f"{arm['name']}-r{replica}.mdb"
                require(capture['file']==path.name and capture['status']=='pass' and capture['error'] is None and capture['before']==capture['after']==identity(path),'Unchanged reopened identity')
                saved=snapshot(capture['snapshot'],arm,ops);raw=raw_observation(path.read_bytes(),arm,saved['rows'])
                signatures.append(dict(operations=ops,snapshot=saved,raw=raw));observations.append(dict(arm=arm['name'],replica=replica,identity=identity(path),operations=case['operations'],snapshot=saved,raw=raw))
            require(all(s==signatures[0] for s in signatures),'Replicas disagree: '+arm['name'])
    except (ValueError,KeyError,TypeError,OSError,catalog.DecodeError) as error:reasons.append(str(error))
    return dict(document_type='dao_empty_long_values_report',plan_sha256=identity(PLAN)['sha256'],outcome='answered' if not reasons else 'no_outcome',reasons=reasons,observations=observations,development_only=True,compatibility_claim=False,support_matrix_movement=False)

def analyze(outbox):
    plan=verify_inputs();report=build_report(json.loads((outbox/'result.json').read_text(encoding='utf-8-sig')),outbox,plan);report['result_sha256']=identity(outbox/'result.json')['sha256'];(outbox/'report.json').write_text(canonical(report)+'\n');print(report['outcome'])

def dispatch(args):
    preflight();require(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id),'Run id')
    shared=args.shared_root.resolve();inbox=shared/'inbox'/args.run_id;outbox=shared/'outbox'/args.run_id;require(not inbox.exists() and not outbox.exists(),'Run used; no retry');inbox.mkdir(parents=True)
    shutil.copyfile(ROOT/SCRIPT,inbox/'script.ps1');shutil.copyfile(PLAN,inbox/PLAN.name)
    spec=importlib.util.spec_from_file_location('transport',ROOT/'scripts/windows-dao-ps.py');transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
    command=['ssh','-p',args.port,'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','IdentitiesOnly=yes','-i',args.identity,f'{args.user}@{args.host}','powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',transport.encoded(transport.guest_script(args.remote_shared_root,args.run_id,'script.ps1'))]
    done=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=300);outbox.mkdir(exist_ok=True);(outbox/'ssh.txt').write_bytes(done.stdout+done.stderr)
    require((outbox/'result.json').exists(),'Missing result; no retry');analyze(outbox);require(done.returncode==0,'Guest failed; no retry')

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True);sub.add_parser('preflight');p=sub.add_parser('analyze');p.add_argument('outbox',type=Path)
    p=sub.add_parser('run');p.add_argument('--run-id',required=True);p.add_argument('--shared-root',type=Path,required=True)
    for name,default in [('host','127.0.0.1'),('port','2222'),('user','jet3runner'),('identity',str(Path.home()/'.ssh/jet3-dao')),('remote-shared-root',r'\\host.lan\Data')]:p.add_argument('--'+name,default=default)
    args=parser.parse_args()
    if args.command=='preflight':preflight();print('Committed inputs match.')
    elif args.command=='analyze':analyze(args.outbox)
    else:dispatch(args)
if __name__=='__main__':main()
