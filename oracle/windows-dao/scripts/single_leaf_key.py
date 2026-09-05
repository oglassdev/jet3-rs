#!/usr/bin/env python3
"""EXP-0179: one finite root-leaf public update/DAO comparison, no retries."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import field_update as common
ROOT=Path(__file__).resolve().parents[3]
PLAN=ROOT/'oracle/windows-dao/acquisition/single-leaf-key.plan.json'
SCRIPT='oracle/windows-dao/scripts/single_leaf_key.ps1'
identity,canonical=common.identity,common.canonical
spec=importlib.util.spec_from_file_location('key_catalog',ROOT/'oracle/windows-dao/scripts/system_catalog.py')
catalog=importlib.util.module_from_spec(spec);spec.loader.exec_module(catalog)
catalog.MAX_ROWS_PER_PAGE=256

def verify_inputs():
    plan=json.loads(PLAN.read_text())
    for name,sha in plan['inputs'].items():
        if identity(ROOT/name)['sha256']!=sha:raise ValueError('Input pin mismatch: '+name)
    return plan

def preflight(images):
    plan=verify_inputs()
    if subprocess.check_output(['git','show',f'HEAD:{PLAN.relative_to(ROOT)}'],cwd=ROOT)!=PLAN.read_bytes():raise ValueError('Plan not committed')
    for name,ident in plan['images'].items():
        if identity(images/name)!=ident:raise ValueError('Image pin mismatch: '+name)
    for arm in plan['arms']:patch_check((images/(arm['name']+'-original.mdb')).read_bytes(),(images/(arm['name']+'-candidate.mdb')).read_bytes(),arm,plan['receipts'][arm['name']])
    return plan

def key(value,descending):
    raw=b'\x7f'+((value&0xffffffff)^0x80000000).to_bytes(4,'big')
    return bytes(v^255 for v in raw) if descending else raw

def patch_check(before,after,arm,receipt):
    definition,_,objects=catalog._discover_catalog(before)
    name,ident=[catalog._ordinal(definition,n) for n in ('Name','Id')]
    roots=[o['values'][ident] for o in objects if o['values'][name]=='Items']
    if len(roots)!=1 or roots[0]!=receipt['root']:raise ValueError('Target root binding')
    table=catalog._definition(before,roots[0]);pages,lval=catalog._table_pages(before,table);rows=catalog._table_rows(before,table,pages)
    if lval or len(table['physical_indexes'])!=1 or len(table['logical_indexes'])!=1 or table['row_count']!=len(rows):raise ValueError('Table/index shape')
    if sorted(r['values'] for r in rows)!=sorted(arm['rows']):raise ValueError('Raw requested rows')
    index=table['physical_indexes'][0];root=index['root'];page=catalog._page(before,root,'leaf')
    if root!=receipt['index'] or index['keys']!=[{'column':0,'direction':int(not arm['descending'])}] or index['flags']!=(9 if arm['primary'] else 1) or index['entry_count']!=len(rows):raise ValueError('Index metadata/count')
    if page[:2]!=b'\x04\x01' or page[4:8]!=roots[0].to_bytes(4,'little') or page[8:22]!=bytes(14):raise ValueError('Isolated uncompressed leaf')
    boundaries=[i*8+b for i,v in enumerate(page[22:248]) for b in range(8) if v&(1<<b)]
    if boundaries!=list(range(9,9*len(rows)+1,9)) or int.from_bytes(page[2:4],'little')!=1800-9*len(rows):raise ValueError('Fixed leaf framing/free')
    records=[];new_records=[];target=[]
    for row in rows:
        values=row['values'];locator=row['page'].to_bytes(3,'big')+bytes([row['row']]);records.append(key(values[0],arm['descending'])+locator)
        new_id=arm['replacement'] if values[0]==arm['selected'] else values[0]
        new_records.append(key(new_id,arm['descending'])+locator)
        if values[0]==arm['selected']:target.append(row)
    if len(target)!=1 or receipt['column']!=0 or (target[0]['page'],target[0]['row'])!=(receipt['page'],receipt['slot']):raise ValueError('Target locator')
    if len({r[:5] for r in new_records})!=len(rows):raise ValueError('Duplicate requested key')
    length=9*len(rows);start=root*2048+248
    if before[start:start+length]!=b''.join(sorted(records)):raise ValueError('Complete leaf key/row correlation')
    raw=catalog._page(before,receipt['page'],'row');entry=catalog._row_directory(raw,receipt['page'])[receipt['slot']]
    offset=receipt['page']*2048+entry['start']+1+table['columns'][0]['fixed_offset']
    expected=bytearray(before);expected[offset:offset+4]=arm['replacement'].to_bytes(4,'little',signed=True);expected[start:start+length]=b''.join(sorted(new_records))
    if expected!=after:raise ValueError('Exact field/leaf patch and unrelated preservation')
    return dict(field_offset=offset,field_length=4,index_page=root,index_offset=start,index_length=length,row_count=len(rows),distinct_count=index['entry_count'],page0_unchanged=before[:2048]==after[:2048],changed_offsets=[i for i,(a,b) in enumerate(zip(before,after)) if a!=b])

def expected(snapshot,arm,role):
    result=common.normalized(snapshot);rows=copy.deepcopy(arm['rows'])
    if role!='original':
        for row in rows:
            if row[0]==arm['selected']:row[0]=arm['replacement']
    if role.endswith('-next'):rows.append(arm['follow'])
    if result['version']!='3.0' or result['queries']!=[] or result['relations']!=[] or result['tables']!=['Items','MSysACEs','MSysObjects','MSysQueries','MSysRelationships'] or len(result['user_tables'])!=1:raise ValueError('Requested inventory')
    table=result['user_tables'][0]
    fields=[dict(name='Id',type=4,size=4,attributes=1),dict(name='Value',type=4,size=4,attributes=1),dict(name='Payload',type=10,size=8,attributes=2)]
    index=dict(name='ByKey',primary=arm['primary'],unique=True,required=arm['primary'],foreign=False,ignore_nulls=False,fields=[dict(name='Id',attributes=int(arm['descending']))])
    if table['name']!='Items' or table['attributes']!=0 or [{k:f[k] for k in ('name','type','size','attributes')} for f in table['fields']]!=fields or table['indexes']!=[index] or table['rows']!=sorted(rows):raise ValueError('Requested schema/rows/index')
    traversal=sorted(rows,key=lambda r:r[0],reverse=arm['descending'])
    seeks=[dict(query=q,row=next((r for r in rows if r[0]==q),None)) for q in arm['queries']]
    if result['traversal']!=traversal or result['seek']!=seeks:raise ValueError('Complete traversal/Seek')
    return result

def build_report(result,outbox,plan):
    observations=[];reasons=[]
    try:
        if result['document_type']!='dao_single_leaf_key_result' or result['plan_sha256']!=identity(PLAN)['sha256'] or result['source_revision']!=plan['source_revision'] or result['error'] is not None or result['retention_failures'] or result['mutation_started'] is not True or result['environment']['process_bits']!=32 or result['environment']['provider']!='DAO.DBEngine.36':raise ValueError('Acquisition/receipt failure')
        pairs={(p['arm'],p['replica']):p for p in result['pairs']};wanted={(a['name'],r) for a in plan['arms'] for r in range(1,4)}
        if len(pairs)!=len(result['pairs']) or set(pairs)!=wanted:raise ValueError('Incomplete pair inventory')
        for arm in plan['arms']:
            for replica in range(1,4):
                pair=pairs[arm['name'],replica];roles=['original','candidate','control','candidate-next','control-next'];snapshots={};images={}
                if set(pair['captures'])!=set(roles) or set(pair['operations'])!={'control','candidate-next','control-next'}:raise ValueError('Incomplete capture/operation')
                if pair['operations']['control']!=dict(status='complete',duplicate=None):raise ValueError('DAO key update failed')
                for role in roles:
                    obs=pair['captures'][role];name=f"{arm['name']}-r{replica}-{role}.mdb";path=outbox/name
                    if obs['file']!=name or obs['status']!='pass' or obs['error'] is not None or obs['before']!=obs['after'] or identity(path)!=obs['after']:raise ValueError('Capture identity/failure')
                    images[role]=identity(path)
                    if role in ('original','candidate') and images[role]!=plan['images'][arm['name']+'-'+role+'.mdb']:raise ValueError('Pinned public image changed')
                    snapshots[role]=expected(obs['snapshot'],arm,role)
                for role in ('candidate-next','control-next'):
                    op=pair['operations'][role];dup=op['duplicate']
                    if op['status']!='complete' or dup['accepted'] is not False or dup['error'] is None or 3022 not in dup['numbers']:raise ValueError('Duplicate probe differs')
                if snapshots['candidate']!=snapshots['control'] or snapshots['candidate-next']!=snapshots['control-next']:raise ValueError('DAO control differs')
                metadata=[]
                for value in snapshots.values():
                    value=copy.deepcopy(value);value.pop('traversal');value.pop('seek');value['user_tables'][0].pop('rows');metadata.append(value)
                if any(v!=metadata[0] for v in metadata):raise ValueError('Unrelated metadata differs')
                patch=patch_check((outbox/f"{arm['name']}-r{replica}-original.mdb").read_bytes(),(outbox/f"{arm['name']}-r{replica}-candidate.mdb").read_bytes(),arm,plan['receipts'][arm['name']])
                observations.append(dict(arm=arm['name'],replica=replica,identities=images,patch=patch,operations=pair['operations']))
    except (ValueError,KeyError,TypeError,OSError,catalog.DecodeError) as error:reasons.append(str(error))
    return dict(document_type='dao_single_leaf_key_report',plan_sha256=identity(PLAN)['sha256'],outcome='observed_accepted' if not reasons else 'no_outcome',reasons=reasons,observations=observations,development_only=True,compatibility_claim=False,support_matrix_movement=False)

def analyze(outbox):
    plan=verify_inputs();report=build_report(json.loads((outbox/'result.json').read_text(encoding='utf-8-sig')),outbox,plan);report['result_sha256']=identity(outbox/'result.json')['sha256'];(outbox/'report.json').write_text(canonical(report)+'\n');print(report['outcome'])

def dispatch(args):
    plan=preflight(args.images)
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id):raise ValueError('Invalid run id')
    shared=args.shared_root.resolve();inbox=shared/'inbox'/args.run_id;outbox=shared/'outbox'/args.run_id
    if inbox.exists() or outbox.exists():raise ValueError('Run used; no retry')
    inbox.mkdir(parents=True)
    for name in plan['images']:shutil.copyfile(args.images/name,inbox/name)
    for name in (SCRIPT,'oracle/windows-dao/scripts/field_update.ps1'):shutil.copyfile(ROOT/name,inbox/Path(name).name)
    shutil.copyfile(inbox/Path(SCRIPT).name,inbox/'script.ps1');shutil.copyfile(PLAN,inbox/PLAN.name)
    spec=importlib.util.spec_from_file_location('transport',ROOT/'scripts/windows-dao-ps.py');transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
    command=['ssh','-p',args.port,'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','IdentitiesOnly=yes','-i',args.identity,f'{args.user}@{args.host}','powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',transport.encoded(transport.guest_script(args.remote_shared_root,args.run_id,'script.ps1'))]
    done=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=300)
    outbox.mkdir(exist_ok=True);(outbox/'ssh.txt').write_bytes(done.stdout+done.stderr)
    if (outbox/'result.json').exists():analyze(outbox)
    if done.returncode:raise ValueError('Guest failed; no retry')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('preflight');p.add_argument('--images',type=Path,required=True)
    p=sub.add_parser('analyze');p.add_argument('outbox',type=Path)
    p=sub.add_parser('run');p.add_argument('--images',type=Path,required=True);p.add_argument('--run-id',required=True);p.add_argument('--shared-root',type=Path,required=True)
    for name,default in [('host','127.0.0.1'),('port','2222'),('user','jet3runner'),('identity',str(Path.home()/'.ssh/jet3-dao')),('remote-shared-root',r'\\host.lan\Data')]:p.add_argument('--'+name,default=default)
    args=parser.parse_args()
    if args.command=='preflight':preflight(args.images);print('Committed inputs/images match.')
    elif args.command=='analyze':analyze(args.outbox)
    else:dispatch(args)
