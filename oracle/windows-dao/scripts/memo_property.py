#!/usr/bin/env python3
"""EXP-0207 finite Memo property framing discovery; no encoding assumption."""
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
PLAN=ROOT/'oracle/windows-dao/acquisition/memo-property.plan.json'
SCRIPT='oracle/windows-dao/scripts/memo_property.ps1'
identity,canonical=common.identity,common.canonical

def require(condition,message):
    if not condition:raise ValueError(message)

def verify_inputs():
    plan=json.loads(PLAN.read_text())
    for name,sha in plan['inputs'].items():require(identity(ROOT/name)['sha256']==sha,'Input pin mismatch: '+name)
    return plan

def preflight():
    plan=verify_inputs();require(subprocess.check_output(['git','show',f'HEAD:{PLAN.relative_to(ROOT)}'],cwd=ROOT)==PLAN.read_bytes(),'Plan must be committed');return plan

def changed(a,b):
    return [dict(offset=i,before=a[i] if i<len(a) else None,after=b[i] if i<len(b) else None) for i in range(max(len(a),len(b))) if (a[i] if i<len(a) else None)!=(b[i] if i<len(b) else None)]

def snapshot(value,arm,checkpoint):
    value=copy.deepcopy(value)
    require(value['version']=='3.0' and value['tables']==sorted(['MSysACEs','MSysObjects','MSysQueries','MSysRelationships',arm['table']]) and value['relations']==value['queries']==value['indexes']==[] and value['attributes']==value['row_count']==0,'Requested empty schema')
    expected=[dict(name='Id',type=4,size=4)]+[dict(name=n,type=12,size=0) for n in arm['columns']]
    require([{k:f[k] for k in ('name','type','size')} for f in value['fields']]==expected,'Field bindings')
    require([f['allow_zero_length'] for f in value['fields'][1:]]==checkpoint['values'],'Requested AllowZeroLength')
    for field in value['fields']:
        props=field['properties'];require(len({p['name'] for p in props})==len(props),'Duplicate properties');field['properties']=sorted(props,key=lambda p:p['name'])
    return value

def raw(data,arm):
    definition,_,records=catalog._discover_catalog(data);n,i,l=[catalog._ordinal(definition,k) for k in ('Name','Id','LvProp')]
    matches=[r for r in records if r['values'][n]==arm['table']];require(len(matches)==1,'Catalog table binding');record=matches[0];table=catalog._definition(data,record['values'][i]);pages,lval=catalog._table_pages(data,table)
    require(table['row_count']==0 and not catalog._table_rows(data,table,pages) and not table['physical_indexes'] and not table['logical_indexes'],'Raw empty table')
    header=bytes.fromhex(record['values'][l]['long_value_header_hex']);require(len(header)==12,'LvProp header');control=int.from_bytes(header[:4],'little');length=control&0xffffff;flags=control&0xff000000
    require(flags==0x40000000 and length<=2048,'Observed single external property value scope')
    locator=int.from_bytes(header[4:8],'little');page,slot=locator>>8,locator&255;image=catalog._page(data,page,'LvProp');directory=catalog._row_directory(image,page);require(slot<len(directory),'LvProp slot');entry=directory[slot];payload=image[entry['start']:entry['end']];require(len(payload)==length and not entry['hidden'] and not entry['overflow'],'LvProp payload framing')
    catpage=catalog._page(data,record['page'],'catalog');catrow=catalog._row_directory(catpage,record['page'])[record['row']]
    maps={k:sorted(catalog._locator_pages(data,v,k)) for k,v in table['maps'].items()};longmaps=[dict(column=g['column'],owned=sorted(catalog._locator_pages(data,g['owned'],'long owned')),available=sorted(catalog._locator_pages(data,g['available'],'long available'))) for g in table['long_value_maps']]
    require(len(longmaps)==len(arm['columns']),'Long map coverage')
    return dict(root=table['root'],columns=table['columns'],catalog_page=record['page'],catalog_slot=record['row'],catalog_row_hex=catpage[catrow['start']:catrow['end']].hex(),descriptor_hex=header.hex(),payload_hex=payload.hex(),property_page=page,property_slot=slot,property_page_hex=image.hex(),table_maps=maps,long_maps=longmaps,long_value_pages=lval)

def build_report(result,outbox,plan):
    observations=[];reasons=[]
    try:
        require(result['document_type']=='dao_memo_property_result' and result['plan_sha256']==identity(PLAN)['sha256'] and result['error'] is None and result['retention_failures']==[] and result['mutation_started'] is True and result['environment']==dict(process_bits=32,provider='DAO.DBEngine.36'),'Acquisition failure')
        cases={(c['arm'],c['replica']):c for c in result['cases']};require(len(cases)==len(result['cases']) and set(cases)=={(a['name'],r) for a in plan['arms'] for r in range(1,4)},'Case inventory')
        for arm in plan['arms']:
            signatures=[]
            for replica in range(1,4):
                case=cases[arm['name'],replica];require([c['checkpoint'] for c in case['captures']]==[c['name'] for c in arm['checkpoints']],'Checkpoint inventory')
                require(case['operations']==[dict(checkpoint=c['name'],column=arm['columns'][c['target']],value=c['values'][c['target']],status='complete') for c in arm['checkpoints'] if c['target'] is not None],'Setter inventory')
                prior=None;prior_payload=None;signature=[]
                for checkpoint,item in zip(arm['checkpoints'],case['captures']):
                    capture=item['capture'];path=outbox/f"{arm['name']}-r{replica}-{checkpoint['name']}.mdb";require(capture['file']==path.name and capture['status']=='pass' and capture['error'] is None and capture['before']==capture['after']==identity(path),'Capture identity')
                    saved=snapshot(capture['snapshot'],arm,checkpoint);data=path.read_bytes();observed=raw(data,arm);payload=bytes.fromhex(observed['payload_hex']);delta=[] if prior is None else changed(prior,data);payload_delta=[] if prior_payload is None else changed(prior_payload,payload)
                    signature.append(dict(snapshot=saved,columns=observed['columns'],payload_hex=payload.hex(),payload_changes=payload_delta))
                    observations.append(dict(arm=arm['name'],replica=replica,checkpoint=checkpoint['name'],identity=identity(path),snapshot=saved,raw=observed,changes=delta,payload_changes=payload_delta));prior=data;prior_payload=payload
                # Physical locators and timestamps remain observations, not replica invariants.
                signatures.append(signature)
            require(all(s==signatures[0] for s in signatures),'Question-bearing replicas disagree: '+arm['name'])
    except (ValueError,KeyError,TypeError,OSError,catalog.DecodeError) as error:reasons.append(str(error))
    return dict(document_type='dao_memo_property_report',plan_sha256=identity(PLAN)['sha256'],outcome='answered' if not reasons else 'no_outcome',reasons=reasons,observations=observations,development_only=True,compatibility_claim=False,support_matrix_movement=False)

def analyze(outbox):
    plan=verify_inputs();report=build_report(json.loads((outbox/'result.json').read_text(encoding='utf-8-sig')),outbox,plan);report['result_sha256']=identity(outbox/'result.json')['sha256'];(outbox/'report.json').write_text(canonical(report)+'\n');print(report['outcome'])

def dispatch(args):
    preflight();require(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id),'Run id')
    shared=args.shared_root.resolve();inbox=shared/'inbox'/args.run_id;outbox=shared/'outbox'/args.run_id;require(not inbox.exists() and not outbox.exists(),'Run used; no retry');inbox.mkdir(parents=True)
    shutil.copyfile(ROOT/SCRIPT,inbox/'script.ps1');shutil.copyfile(ROOT/'oracle/windows-dao/scripts/empty_long_values.ps1',inbox/'empty_long_values.ps1');shutil.copyfile(PLAN,inbox/PLAN.name)
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
