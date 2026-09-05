#!/usr/bin/env python3
"""Finite public indexed insertion/deletion validation; EXP-0215, no retries."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import field_update as common
import multi_level_index_structure as structure
ROOT=Path(__file__).resolve().parents[3]
PLAN=ROOT/'oracle/windows-dao/acquisition/indexed-row-candidate.plan.json'
SCRIPT='oracle/windows-dao/scripts/indexed_row_candidate.ps1'
identity,canonical=common.identity,common.canonical
catalog=structure.catalog

def require(value,message):
    if not value:raise ValueError(message)

def verify_inputs():
    plan=json.loads(PLAN.read_text())
    for name,sha in plan['inputs'].items():require(identity(ROOT/name)['sha256']==sha,'Input pin mismatch: '+name)
    return plan

def rows_for(arm,role):
    rows=copy.deepcopy(arm['rows'])
    if role not in ('original','control-original'):
        if arm['kind']=='insert':rows.append(arm['insert'])
        else:rows=[r for r in rows if r[0] not in arm['delete']]
    if role.endswith('-next'):rows.append(arm['follow'])
    return sorted(rows)

def definition(data):
    d,_,objects=catalog._discover_catalog(data);n,i=[catalog._ordinal(d,k) for k in ('Name','Id')]
    roots=[r['values'][i] for r in objects if r['values'][n]=='Items'];require(len(roots)==1,'Items root')
    table=catalog._definition(data,roots[0]);pages,lval=catalog._table_pages(data,table);rows=catalog._table_rows(data,table,pages)
    require(not lval and len(table['physical_indexes'])==len(table['logical_indexes'])==1,'One scalar index')
    return table,rows

def raw_check(data,arm,rows,candidate=False):
    table,physical_rows=definition(data);physical=table['physical_indexes'][0]
    require(physical['flags']==(9 if arm['primary'] else 1) and physical['keys']==[dict(column=0,direction=int(not arm['descending']))],'Physical key metadata')
    spec=dict(name='Items',fields=[dict(name=n) for n in ('Id','Value')],indexes=[dict(fields=[dict(name='Id',descending=arm['descending'])])],candidate_depth=1)
    result=structure.observe(data,[spec],{'Items':rows},candidate)
    result[0]['row_locators']=[dict(values=r['values'],page=r['page'],slot=r['row']) for r in physical_rows]
    return result

def patch_check(before,after,arm,receipt):
    raw_check(before,arm,arm['rows'],True);raw_check(after,arm,rows_for(arm,'candidate'),True)
    expected=bytearray(before);actions=receipt['actions'];require(len(actions)==(1 if arm['kind']=='insert' else len(arm['delete'])),'Action inventory')
    for step,action in enumerate(actions):
        table,rows=definition(bytes(expected));require(table['root']==receipt['root'],'Receipt root')
        page,slot=action['page'],action['slot'];offset=page*2048;image=bytes(expected[offset:offset+2048]);directory=catalog._row_directory(image,page)
        require(image[:2]==b'\x01\x01' and int.from_bytes(image[4:8],'little')==table['root'] and directory,'Data page owner/kind')
        lowest=directory[-1]['start'];free=lowest-10-2*len(directory)
        require(int.from_bytes(image[2:4],'little')==free,'Data free bytes')
        if arm['kind']=='insert':
            require(action['kind']=='insert' and slot==len(directory) and slot<256,'Appended physical slot')
            row=bytes([2])+b''.join(v.to_bytes(4,'little',signed=True) for v in arm['insert'])+b'\x03'
            start=lowest-len(row);require(start>=10+2*(slot+1),'Row capacity')
            expected[offset+start:offset+lowest]=row;expected[offset+10+slot*2:offset+12+slot*2]=start.to_bytes(2,'little')
            expected[offset+8:offset+10]=(slot+1).to_bytes(2,'little');new_free=free-len(row)-2;count=len(rows)+1
        else:
            require(action['kind']=='delete' and slot<len(directory),'Deleted slot')
            selected=[r for r in rows if r['values'][0]==arm['delete'][step]]
            require(len(selected)==1 and (selected[0]['page'],selected[0]['row'])==(page,slot),'Deleted Id/locator')
            target=directory[slot];start,end=target['start'],target['end'];length=end-start;require(length>0,'Live deletion')
            expected[offset+lowest+length:offset+end]=image[lowest:start]
            for n in range(slot,len(directory)):
                old=int.from_bytes(image[10+2*n:12+2*n],'little');word=(end|0xc000) if n==slot else ((old&0xf000)|((old&0x0fff)+length))
                expected[offset+10+2*n:offset+12+2*n]=word.to_bytes(2,'little')
            new_free=free+length;count=len(rows)-1
        expected[offset+2:offset+4]=new_free.to_bytes(2,'little');root=table['root']*2048
        expected[root+12:root+16]=count.to_bytes(4,'little');expected[root+47:root+51]=count.to_bytes(4,'little')
        _,new_rows=definition(bytes(expected));index=table['physical_indexes'][0]['root'];leaf=bytes(expected[index*2048:(index+1)*2048])
        require(leaf[:2]==b'\x04\x01' and leaf[8:22]==bytes(14),'Candidate uncompressed root leaf')
        records=sorted(structure.key_bytes(r['values'],[dict(name='Id',descending=arm['descending'])],['Id','Value'])+r['page'].to_bytes(3,'big')+bytes([r['row']]) for r in new_rows)
        require(len(records)==count<=200,'Unique leaf capacity');bitmap=bytearray(226)
        for n in range(1,count+1):bitmap[n*9//8]|=1<<(n*9%8)
        base=index*2048;expected[base+2:base+4]=(1800-count*9).to_bytes(2,'little');expected[base+22:base+248]=bitmap;expected[base+248:base+248+count*9]=b''.join(records)
    require(bytes(expected)==after,'Exact three-page patches, slack/maps/page0 and unrelated preservation')
    require(receipt['refusal_preserved'] is True and receipt['public_refusal']==('capacity' if arm['name']=='capacity' else 'duplicate'),'Public duplicate/capacity refusal')
    return dict(actions=actions,refusal=receipt['public_refusal'],changed_offsets=[i for i,(a,b) in enumerate(zip(before,after)) if a!=b],page0_unchanged=before[:2048]==after[:2048])

def expected(snapshot,arm,role):
    value=common.normalized(snapshot);rows=rows_for(arm,role)
    require(value['version']=='3.0' and value['relations']==value['queries']==[] and value['tables']==['Items','MSysACEs','MSysObjects','MSysQueries','MSysRelationships'] and len(value['user_tables'])==1,'Complete table inventory')
    table=value['user_tables'][0];fields=[dict(name=n,type=4,size=4,attributes=1) for n in ('Id','Value')]
    index=dict(name='ByKey',primary=arm['primary'],unique=True,required=arm['primary'],foreign=False,ignore_nulls=False,fields=[dict(name='Id',attributes=int(arm['descending']))])
    require(table['name']=='Items' and table['attributes']==0 and [{k:f[k] for k in ('name','type','size','attributes')} for f in table['fields']]==fields and table['indexes']==[index] and table['rows']==rows,'Exact schema/index/full rows')
    require(value['traversal']==sorted(rows,reverse=arm['descending']) and value['seek']==[dict(query=q,row=next((r for r in rows if r[0]==q),None)) for q in arm['queries']],'Full directed traversal/present and missing Seek')
    return value

def build_report(result,outbox,plan):
    observations=[];reasons=[]
    try:
        require(result['document_type']=='dao_indexed_row_result' and result['plan_sha256']==identity(PLAN)['sha256'] and result['source_revision']==plan['source_revision'] and result['error'] is None and result['retention_failures']==[] and result['mutation_started'] is True and result['environment']==dict(process_bits=32,provider='DAO.DBEngine.36'),'Acquisition/source failure')
        pairs={(p['arm'],p['replica']):p for p in result['pairs']};require(len(pairs)==len(result['pairs']) and set(pairs)=={(a['name'],r) for a in plan['arms'] for r in range(1,4)},'Complete pairs')
        for arm in plan['arms']:
            for replica in range(1,4):
                pair=pairs[arm['name'],replica];roles=['original','candidate','control-original','control','candidate-next','control-next','candidate-duplicate','control-duplicate'];snapshots={};images={};raw={};duplicate_counts={}
                require(set(pair['captures'])==set(roles) and set(pair['operations'])=={'control','candidate-next','control-next','candidate-duplicate','control-duplicate'},'Complete capture/operation inventory')
                for role in roles:
                    c=pair['captures'][role];path=outbox/f"{arm['name']}-r{replica}-{role}.mdb"
                    require(c['file']==path.name and c['status']=='pass' and c['error'] is None and c['before']==c['after']==identity(path),'Unchanged retained image');images[role]=identity(path)
                    if role in ('original','candidate'):require(images[role]==plan['images'][arm['name']+'-'+role+'.mdb'],'Pinned public image')
                    snapshots[role]=expected(c['snapshot'],arm,role)
                    if role.endswith('-duplicate'):
                        t,rs=definition(path.read_bytes());duplicate_counts[role]=dict(table_count=t['row_count'],distinct_count=t['physical_indexes'][0]['entry_count'],live_rows=len(rs))
                    else:raw[role]=raw_check(path.read_bytes(),arm,rows_for(arm,role),role in ('original','candidate'))
                for role in ('control','candidate-next','control-next'):require(pair['operations'][role]==dict(status='complete'),'Completed mutation')
                for role in ('candidate-duplicate','control-duplicate'):
                    p=pair['operations'][role];require(p['accepted'] is False and p['error'] is not None and 3022 in p['numbers'],'Duplicate native rejection')
                require(pair['operations']['candidate-duplicate']['numbers']==pair['operations']['control-duplicate']['numbers'],'Matched native duplicate errors')
                for a,b in [('original','control-original'),('candidate','control'),('candidate-next','control-next'),('candidate-duplicate','control-duplicate'),('candidate','candidate-duplicate')]:require(snapshots[a]==snapshots[b],'Full control or rejected post-state semantics')
                metadata=[]
                for snapshot in snapshots.values():
                    value=copy.deepcopy(snapshot);value.pop('traversal');value.pop('seek');value['user_tables'][0].pop('rows');metadata.append(value)
                require(all(v==metadata[0] for v in metadata),'Unrelated metadata changed')
                patch=patch_check((outbox/f"{arm['name']}-r{replica}-original.mdb").read_bytes(),(outbox/f"{arm['name']}-r{replica}-candidate.mdb").read_bytes(),arm,plan['receipts'][arm['name']])
                observations.append(dict(arm=arm['name'],replica=replica,identities=images,raw=raw,patch=patch,duplicate_counts=duplicate_counts,operations=pair['operations']))
    except (ValueError,KeyError,TypeError,OSError,catalog.DecodeError) as e:reasons.append(str(e))
    return dict(document_type='dao_indexed_row_report',outcome='no_outcome' if reasons else 'observed_accepted',plan_sha256=identity(PLAN)['sha256'],reasons=reasons,observations=observations,development_only=True,compatibility_claim=False,support_matrix_movement=False)

def preflight(images):
    plan=verify_inputs();require(subprocess.check_output(['git','show',f'HEAD:{PLAN.relative_to(ROOT)}'],cwd=ROOT)==PLAN.read_bytes(),'Plan not committed')
    for name,pin in plan['images'].items():require(identity(images/name)==pin,'Candidate pin: '+name)
    for a in plan['arms']:patch_check((images/(a['name']+'-original.mdb')).read_bytes(),(images/(a['name']+'-candidate.mdb')).read_bytes(),a,plan['receipts'][a['name']])
    return plan

def analyze(outbox):
    plan=verify_inputs();report=build_report(json.loads((outbox/'result.json').read_text(encoding='utf-8-sig')),outbox,plan);report['result_sha256']=identity(outbox/'result.json')['sha256'];(outbox/'report.json').write_text(canonical(report)+'\n');print(report['outcome'])

def dispatch(args):
    plan=preflight(args.images);require(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id),'Run id');shared=args.shared_root.resolve();inbox=shared/'inbox'/args.run_id;outbox=shared/'outbox'/args.run_id;require(not inbox.exists() and not outbox.exists(),'Used run; no retry');inbox.mkdir(parents=True)
    for name in plan['images']:shutil.copyfile(args.images/name,inbox/name)
    shutil.copyfile(ROOT/SCRIPT,inbox/'script.ps1');shutil.copyfile(ROOT/'oracle/windows-dao/scripts/field_update.ps1',inbox/'field_update.ps1');shutil.copyfile(PLAN,inbox/PLAN.name)
    spec=importlib.util.spec_from_file_location('transport',ROOT/'scripts/windows-dao-ps.py');transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
    command=['ssh','-p',args.port,'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','IdentitiesOnly=yes','-i',args.identity,f'{args.user}@{args.host}','powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',transport.encoded(transport.guest_script(args.remote_shared_root,args.run_id,'script.ps1'))]
    done=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=900);outbox.mkdir(exist_ok=True);(outbox/'ssh.txt').write_bytes(done.stdout+done.stderr)
    require((outbox/'result.json').exists(),'Missing result; no retry');analyze(outbox);require(done.returncode==0,'Guest failed; no retry')

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('preflight');c.add_argument('--images',type=Path,required=True);c=sub.add_parser('analyze');c.add_argument('outbox',type=Path)
    c=sub.add_parser('run');c.add_argument('--images',type=Path,required=True);c.add_argument('--run-id',required=True);c.add_argument('--shared-root',type=Path,required=True)
    for n,d in [('host','127.0.0.1'),('port','2222'),('user','jet3runner'),('identity',str(Path.home()/'.ssh/jet3-dao')),('remote-shared-root',r'\\host.lan\Data')]:c.add_argument('--'+n,default=d)
    a=p.parse_args()
    if a.command=='preflight':preflight(a.images);print('Committed inputs/images match.')
    elif a.command=='analyze':analyze(a.outbox)
    else:dispatch(a)
if __name__=='__main__':main()
