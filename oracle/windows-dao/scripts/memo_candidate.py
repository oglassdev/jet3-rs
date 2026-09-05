#!/usr/bin/env python3
"""EXP-0209 finite explicit empty Memo candidate validation."""
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
PLAN=ROOT/'oracle/windows-dao/acquisition/memo-candidate.plan.json'
SCRIPT='oracle/windows-dao/scripts/memo_candidate.ps1'
identity,canonical=common.identity,common.canonical

def require(condition,message):
    if not condition:raise ValueError(message)

def verify_inputs():
    plan=json.loads(PLAN.read_text())
    for name,sha in plan['inputs'].items():require(identity(ROOT/name)['sha256']==sha,'Input pin mismatch: '+name)
    return plan

def preflight(images):
    plan=verify_inputs();require(subprocess.check_output(['git','show',f'HEAD:{PLAN.relative_to(ROOT)}'],cwd=ROOT)==PLAN.read_bytes(),'Plan must be committed')
    for arm in plan['arms']:
        path=images/(arm['name']+'.mdb');require(identity(path)==plan['images'][path.name],'Candidate pin');raw_check(path.read_bytes(),arm,arm['rows'])
    return plan

def expected_rows(arm,role,plan):
    rows=copy.deepcopy(arm['rows'])
    for request in plan['continuations']:
        if role.endswith('-'+request['name']):rows.append(request['row'])
    return rows

def snapshot(value,arm,rows):
    value=copy.deepcopy(value)
    require(value['version']=='3.0' and value['tables']==sorted(['MSysACEs','MSysObjects','MSysQueries','MSysRelationships',arm['table']]) and value['relations']==value['queries']==value['indexes']==[] and value['attributes']==0,'Requested inventory')
    require([{k:f[k] for k in ('name','type','size','attributes')} for f in value['fields']]==[dict(name='Id',type=4,size=4,attributes=1),dict(name=arm['memo'],type=12,size=0,attributes=2)] and value['fields'][1]['allow_zero_length'] is True,'Requested schema/option')
    for field in value['fields']:
        properties=field['properties'];require(len({p['name'] for p in properties})==len(properties),'Duplicate property');field['properties']=sorted(properties,key=lambda p:p['name'])
    value['rows']=sorted(value['rows'],key=lambda r:r['id'])
    require(value['rows']==[dict(id=i,is_null=v is None,payload=v,field_size=0 if v is None else len(v)*2) for i,v in rows],'Complete Memo rows/IsNull/FieldSize')
    return value

def raw_check(data,arm,expected):
    definition,_,records=catalog._discover_catalog(data);n,i,l=[catalog._ordinal(definition,k) for k in ('Name','Id','LvProp')]
    matches=[r for r in records if r['values'][n]==arm['table']];require(len(matches)==1,'Catalog root');record=matches[0];table=catalog._definition(data,record['values'][i]);pages,lval=catalog._table_pages(data,table);rows=catalog._table_rows(data,table,pages)
    require(table['row_count']==len(rows)==len(expected) and not table['physical_indexes'] and not table['logical_indexes'] and not lval,'Raw row/index/LVAL inventory')
    require([(c['name'],c['type'],c['size']) for c in table['columns']]==[('Id','Long',4),(arm['memo'],'Memo',0)],'Raw field bindings')
    observed=[]
    for row in rows:
        ident=row['values'][0];wanted=[v for i,v in expected if i==ident];require(len(wanted)==1,'Id binding');wanted=wanted[0]
        require(row['present']==[True,wanted is not None],'Presence binding')
        page=catalog._page(data,row['page'],'data');entry=catalog._row_directory(page,row['page'])[row['row']];raw=page[entry['start']:entry['end']];require(len(raw)<=255 and raw[-2]==1,'Small variable row')
        field=raw[raw[-3]:raw[-4]];payload=b'' if wanted is None else wanted.encode('ascii')
        encoded=b'' if wanted is None else (0x80000000|len(payload)).to_bytes(4,'little')+bytes(8)+payload
        require(field==encoded,'Inline Memo descriptor/payload');observed.append(dict(id=ident,page=row['page'],slot=row['row'],field_hex=field.hex()))
    require(sorted(o['id'] for o in observed)==[i for i,v in expected],'Complete raw row bindings')
    header=bytes.fromhex(record['values'][l]['long_value_header_hex']);require(len(header)==12 and header[8:]==bytes(4) and int.from_bytes(header[:4],'little')==0x40000000+len(bytes.fromhex(arm['property_payload_hex'])),'Property header')
    locator=int.from_bytes(header[4:8],'little');page,slot=locator>>8,locator&255;image=catalog._page(data,page,'LvProp');directory=catalog._row_directory(image,page);require(slot<len(directory),'Property slot');entry=directory[slot];payload=image[entry['start']:entry['end']];require(not entry['hidden'] and not entry['overflow'] and payload.hex()==arm['property_payload_hex'],'Exact named property payload')
    group,=[g for g in definition['long_value_maps'] if g['column']==14];owned=catalog._locator_pages(data,group['owned'],'catalog property owned');available=catalog._locator_pages(data,group['available'],'catalog property available')
    require(page in owned and page in available and page not in catalog._map_pages(catalog._locator_row(data,dict(page=1,row=0),'global free'),len(data)//2048,'global free',bounded=False),'Catalog property map membership')
    group,=table['long_value_maps'];require(group['column']==1 and not catalog._locator_pages(data,group['owned'],'Memo owned') and not catalog._locator_pages(data,group['available'],'Memo available'),'Inline Memo maps')
    maps={k:sorted(catalog._locator_pages(data,v,k)) for k,v in table['maps'].items()};require(set(maps['owned'])=={r['page'] for r in rows} and set(maps['available'])==set(maps['owned']),'Data map inventory')
    return dict(root=table['root'],rows=sorted(observed,key=lambda r:r['id']),maps=maps,property_header_hex=header.hex(),property_payload_hex=payload.hex(),property_page=page,property_slot=slot,catalog_property_maps=dict(owned=sorted(owned),available=sorted(available)))

def build_report(result,outbox,plan):
    observations=[];reasons=[]
    try:
        require(result['document_type']=='dao_memo_candidate_result' and result['plan_sha256']==identity(PLAN)['sha256'] and result['source_revision']==plan['source_revision'] and result['error'] is None and result['retention_failures']==[] and result['mutation_started'] is True and result['environment']==dict(process_bits=32,provider='DAO.DBEngine.36'),'Acquisition/source failure')
        pairs={(p['arm'],p['replica']):p for p in result['pairs']};require(len(pairs)==len(result['pairs']) and set(pairs)=={(a['name'],r) for a in plan['arms'] for r in range(1,4)},'Pair inventory')
        for arm in plan['arms']:
            for replica in range(1,4):
                pair=pairs[arm['name'],replica];operations={role+'-'+p['name']:dict(status='complete',row=p['row']) for role in ('candidate','control') for p in plan['continuations']};require(pair['operations']==operations and set(pair['captures'])==set(operations)|{'candidate','control'},'Complete capture/continuation inventory')
                snapshots={};raw={};images={}
                for role,capture in pair['captures'].items():
                    path=outbox/f"{arm['name']}-r{replica}-{role}.mdb";require(capture['file']==path.name and capture['status']=='pass' and capture['error'] is None and capture['before']==capture['after']==identity(path),'Unchanged capture identity');images[role]=identity(path)
                    if role=='candidate':require(images[role]==plan['images'][arm['name']+'.mdb'],'Pinned candidate')
                    rows=expected_rows(arm,role,plan);snapshots[role]=snapshot(capture['snapshot'],arm,rows);raw[role]=raw_check(path.read_bytes(),arm,rows)
                for suffix in ['',*['-'+p['name'] for p in plan['continuations']]]:require(snapshots['candidate'+suffix]==snapshots['control'+suffix],'Control semantics differ')
                for role,value in snapshots.items():
                    metadata=copy.deepcopy(value);metadata.pop('rows');baseline=copy.deepcopy(snapshots['candidate']);baseline.pop('rows');require(metadata==baseline,'Continuation metadata changed')
                observations.append(dict(arm=arm['name'],replica=replica,identities=images,raw=raw,operations=pair['operations'],snapshots=snapshots))
    except (ValueError,KeyError,TypeError,OSError,catalog.DecodeError) as error:reasons.append(str(error))
    return dict(document_type='dao_memo_candidate_report',plan_sha256=identity(PLAN)['sha256'],outcome='observed_accepted' if not reasons else 'no_outcome',reasons=reasons,observations=observations,development_only=True,compatibility_claim=False,support_matrix_movement=False)

def analyze(outbox):
    plan=verify_inputs();report=build_report(json.loads((outbox/'result.json').read_text(encoding='utf-8-sig')),outbox,plan);report['result_sha256']=identity(outbox/'result.json')['sha256'];(outbox/'report.json').write_text(canonical(report)+'\n');print(report['outcome'])

def dispatch(args):
    plan=preflight(args.images);require(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id),'Run id')
    shared=args.shared_root.resolve();inbox=shared/'inbox'/args.run_id;outbox=shared/'outbox'/args.run_id;require(not inbox.exists() and not outbox.exists(),'Run used; no retry');inbox.mkdir(parents=True)
    shutil.copyfile(ROOT/SCRIPT,inbox/'script.ps1');shutil.copyfile(PLAN,inbox/PLAN.name);shutil.copyfile(ROOT/'oracle/windows-dao/scripts/empty_long_values.ps1',inbox/'empty_long_values.ps1')
    for name in plan['images']:shutil.copyfile(args.images/name,inbox/name)
    spec=importlib.util.spec_from_file_location('transport',ROOT/'scripts/windows-dao-ps.py');transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
    command=['ssh','-p',args.port,'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','IdentitiesOnly=yes','-i',args.identity,f'{args.user}@{args.host}','powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',transport.encoded(transport.guest_script(args.remote_shared_root,args.run_id,'script.ps1'))]
    done=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=600);outbox.mkdir(exist_ok=True);(outbox/'ssh.txt').write_bytes(done.stdout+done.stderr)
    require((outbox/'result.json').exists(),'Missing result; no retry');analyze(outbox);require(done.returncode==0,'Guest failed; no retry')

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True);p=sub.add_parser('preflight');p.add_argument('--images',type=Path,required=True);p=sub.add_parser('analyze');p.add_argument('outbox',type=Path)
    p=sub.add_parser('run');p.add_argument('--images',type=Path,required=True);p.add_argument('--run-id',required=True);p.add_argument('--shared-root',type=Path,required=True)
    for name,default in [('host','127.0.0.1'),('port','2222'),('user','jet3runner'),('identity',str(Path.home()/'.ssh/jet3-dao')),('remote-shared-root',r'\\host.lan\Data')]:p.add_argument('--'+name,default=default)
    args=parser.parse_args()
    if args.command=='preflight':preflight(args.images);print('Committed inputs/images match.')
    elif args.command=='analyze':analyze(args.outbox)
    else:dispatch(args)
if __name__=='__main__':main()
