#!/usr/bin/env python3
"""EXP-0193 finite multiple-index creation/DAO comparison; no retries."""
import argparse
import ast
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import field_update as common
ROOT=Path(__file__).resolve().parents[3]
PLAN=ROOT/'oracle/windows-dao/acquisition/multiple-index.plan.json'
SCRIPT='oracle/windows-dao/scripts/multiple_index.ps1'
identity,canonical=common.identity,common.canonical
spec=importlib.util.spec_from_file_location('_numeric_catalog',ROOT/'oracle/windows-dao/scripts/system_catalog.py');catalog=importlib.util.module_from_spec(spec);spec.loader.exec_module(catalog)
catalog.MAX_ROWS_PER_PAGE=256

def require(condition,message):
    if not condition:raise ValueError(message)

# Reuse only the pinned pure tree decoder, including both observed branch markers.
source=ROOT/'oracle/windows-dao/scripts/multi_level_index_reanalysis.py'
function=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='tree')
namespace={'catalog':catalog,'require':require};exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
tree=namespace['tree']

def verify_inputs():
    plan=json.loads(PLAN.read_text())
    for name,sha in plan['inputs'].items():require(identity(ROOT/name)['sha256']==sha,'Input pin mismatch: '+name)
    return plan

def component(value,field,descending):
    if value is None:
        require(field['type']!=1,'Boolean null');result=b'\0'
    elif field['type']==1:result=b'\x7f'+(b'\0' if value else b'\xff')
    else:
        raw=bytes.fromhex(value);require(len(raw)==field['size'],'Saved scalar width');payload=bytearray(reversed(raw))
        if field['type'] in (3,4,5):payload[0]^=128
        elif field['type'] in (6,7):
            bits=int.from_bytes(raw,'little');sign=1<<(8*len(raw)-1);exponent=0x7f800000 if len(raw)==4 else 0x7ff0000000000000
            require(bits!=sign and bits&exponent!=exponent,'Excluded floating bits')
            if bits&sign:payload=bytearray(b^255 for b in payload)
            else:payload[0]^=128
        elif field['type']!=2:raise ValueError('Unsupported scalar')
        result=b'\x7f'+payload
    return bytes(b^255 for b in result) if descending else result

def key(row,arm,index):
    return b''.join(component(row[c],arm['fields'][c],d) for c,d in zip(index['columns'],index['directions']))
def included(row,index):return not (index['ignore'] and all(row[c] is None for c in index['columns']))
def sorted_rows(rows):return sorted(rows,key=canonical)

def raw_check(data,arm):
    definition,_,records=catalog._discover_catalog(data);n,i=[catalog._ordinal(definition,k) for k in ('Name','Id')]
    roots=[r['values'][i] for r in records if r['values'][n]=='Rows'];require(len(roots)==1,'Table root')
    table=catalog._definition(data,roots[0]);pages,lval=catalog._table_pages(data,table);rows=catalog._table_rows(data,table,pages)
    require(not lval and len(table['physical_indexes'])==len(table['logical_indexes'])==len(arm['indexes']),'Table/index inventory')
    require([{k:c[k] for k in ('name','type','size')} for c in table['columns']]==[dict(name=f['name'],type=catalog.PHYSICAL_TYPES[f['type']],size=f['size']) for f in arm['fields']],'Raw schema')
    actual=[];bound=[]
    for row in rows:
        page=catalog._page(data,row['page'],'data');entry=catalog._row_directory(page,row['page'])[row['row']];raw=page[entry['start']:entry['end']];values=[]
        for column in table['columns']:
            ordinal=column['ordinal']
            if not row['present'][ordinal]:value=None
            else:
                require(column['storage']=='fixed','Expected fixed scalar');start=1+column['fixed_offset'];value=raw[start:start+column['size']].hex()
            values.append(value)
        actual.append(values);bound.append((values,row['page'].to_bytes(3,'big')+bytes([row['row']])))
    require(sorted_rows(actual)==sorted_rows(arm['rows']) and table['row_count']==len(actual),'Raw row inventory')
    logical={index['name']:index for index in table['logical_indexes']}
    require(set(logical)=={i['name'] for i in arm['indexes']},'Logical index names')
    for ordinal,spec in enumerate(arm['indexes']):
        record=bytes.fromhex(logical[spec['name']]['raw_hex'])
        require(int.from_bytes(record[:4],'little')==int.from_bytes(record[4:8],'little')==ordinal and record[19]==int(spec['primary']),'Logical physical binding/class')
    results=[];owned=set(pages)
    for spec,index in zip(arm['indexes'],table['physical_indexes']):
        expected=sorted(key(values,arm,spec)+locator for values,locator in bound if included(values,spec))
        require(index['flags']==int(spec['unique'])+2*spec['ignore']+8*spec['required'],'Raw index flags')
        require(index['keys']==[dict(column=c,direction=int(not d)) for c,d in zip(spec['columns'],spec['directions'])],'Raw key bindings')
        nodes,entries=tree(data,index['root'],table['root']);require(entries==expected,'Complete raw key/locator inventory')
        require(index['entry_count']==len({e[:-4] for e in entries}),'Distinct full-key count')
        maps=sorted(catalog._locator_pages(data,index['map'],'index map'));require(maps==sorted(n['page'] for n in nodes),'Index ownership map')
        require(not owned.intersection(maps),'Disjoint data/index trees');owned.update(maps)
        results.append(dict(name=spec['name'],index_root=index['root'],entries=len(entries),distinct=index['entry_count'],flags=index['flags'],maps=maps,depth=max(n['depth'] for n in nodes)))
    return dict(root=table['root'],rows=len(rows),indexes=results)

def expected(snapshot,arm):
    value=copy.deepcopy(snapshot)
    require(value['version']=='3.0' and value['tables']==['MSysACEs','MSysObjects','MSysQueries','MSysRelationships','Rows'] and value['relations']==[] and value['queries']==[] and value['attributes']==0,'Requested inventory')
    require([{k:f[k] for k in ('name','type','size','attributes')} for f in value['fields']]==[dict(f,attributes=1) for f in arm['fields']],'Requested field schema')
    indexes=[dict(name=i['name'],primary=i['primary'],unique=i['unique'],required=i['required'],ignore_nulls=i['ignore'],foreign=False,fields=[dict(name=arm['fields'][c]['name'],attributes=int(d)) for c,d in zip(i['columns'],i['directions'])]) for i in arm['indexes']]
    require(sorted(value['indexes'],key=lambda i:i['name'])==sorted(indexes,key=lambda i:i['name']) and sorted_rows(value['rows'])==sorted_rows(arm['rows']),'Requested index/rows')
    require(set(value['traversals'])==set(value['seeks'])=={i['name'] for i in arm['indexes']},'Index observation inventory')
    for index in arm['indexes']:
        name=index['name'];rows=[r for r in arm['rows'] if included(r,index)];traversal=value['traversals'][name]
        require(sorted_rows(traversal)==sorted_rows(rows),'Complete traversal rows')
        require([key(r,arm,index) for r in traversal]==sorted(key(r,arm,index) for r in rows),'Directed traversal')
        require(len(value['seeks'][name])==len(index['queries']),'Seek inventory')
        for obs,query in zip(value['seeks'][name],index['queries']):
            matches=[r for r in rows if [r[c] for c in index['columns']]==query]
            require(obs['query']==query and (obs['row'] in matches if matches else obs['row'] is None),'Full-key Seek')
            # Duplicate-key Seek may return any matching row, after full traversal coverage.
            obs['row']=sorted_rows(matches)[0] if matches else None
        value['traversals'][name]=sorted(traversal,key=lambda r:(key(r,arm,index),canonical(r)))
    value['rows']=sorted_rows(value['rows']);value['indexes']=sorted(value['indexes'],key=lambda i:i['name'])
    return value

def build_report(result,outbox,plan):
    observations=[];reasons=[]
    try:
        require(result['document_type']=='dao_multiple_index_result' and result['plan_sha256']==identity(PLAN)['sha256'] and result['source_revision']==plan['source_revision'] and result['error'] is None and result['retention_failures']==[] and result['mutation_started'] is True and result['environment']['process_bits']==32 and result['environment']['provider']=='DAO.DBEngine.36','Acquisition/source failure')
        pairs={(p['arm'],p['replica']):p for p in result['pairs']};require(len(pairs)==len(result['pairs']) and set(pairs)=={(a['name'],r) for a in plan['arms'] for r in range(1,4)},'Pair inventory')
        for arm in plan['arms']:
            for replica in range(1,4):
                pair=pairs[arm['name'],replica];probe_roles={role+'-'+p['name'] for role in ('candidate','control') for p in arm['probes']};roles=['candidate','control',*sorted(probe_roles)];snapshots={};images={};raw={}
                require(set(pair['captures'])==set(roles) and set(pair['probes'])==probe_roles,'Complete capture/probe inventory')
                for role in roles:
                    obs=pair['captures'][role];path=outbox/f"{arm['name']}-r{replica}-{role}.mdb"
                    require(obs['file']==path.name and obs['status']=='pass' and obs['error'] is None and obs['before']==obs['after']==identity(path),'Unchanged capture identity')
                    images[role]=identity(path)
                    if role=='candidate':require(images[role]==plan['images'][arm['name']+'.mdb'],'Pinned candidate identity')
                    snapshots[role]=expected(obs['snapshot'],arm)
                    raw[role]=raw_check(path.read_bytes(),arm)
                require(all(v==snapshots['candidate'] for v in snapshots.values()),'Full control/probe snapshot differs')
                for probe in arm['probes']:
                    for role in ('candidate','control'):
                        op=pair['probes'][role+'-'+probe['name']];require(op['accepted'] is False and op['error'] is not None and probe['number'] in op['numbers'],'Probe rejection differs')
                observations.append(dict(arm=arm['name'],replica=replica,identities=images,raw=raw,probes=pair['probes']))
    except (ValueError,KeyError,TypeError,OSError,catalog.DecodeError) as error:reasons.append(str(error))
    return dict(document_type='dao_multiple_index_report',plan_sha256=identity(PLAN)['sha256'],outcome='observed_accepted' if not reasons else 'no_outcome',observations=observations,reasons=reasons,development_only=True,compatibility_claim=False,support_matrix_movement=False)

def preflight(images):
    plan=verify_inputs();require(subprocess.check_output(['git','show',f'HEAD:{PLAN.relative_to(ROOT)}'],cwd=ROOT)==PLAN.read_bytes(),'Plan not committed')
    for arm in plan['arms']:
        path=images/(arm['name']+'.mdb');require(identity(path)==plan['images'][path.name],'Image pin mismatch');raw_check(path.read_bytes(),arm)
    return plan

def analyze(outbox):
    plan=verify_inputs();report=build_report(json.loads((outbox/'result.json').read_text(encoding='utf-8-sig')),outbox,plan);report['result_sha256']=identity(outbox/'result.json')['sha256'];(outbox/'report.json').write_text(canonical(report)+'\n');print(report['outcome'])

def dispatch(args):
    plan=preflight(args.images);require(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id),'Run id')
    shared=args.shared_root.resolve();inbox=shared/'inbox'/args.run_id;outbox=shared/'outbox'/args.run_id;require(not inbox.exists() and not outbox.exists(),'Run used; no retry');inbox.mkdir(parents=True)
    for name in plan['images']:shutil.copyfile(args.images/name,inbox/name)
    for name in (SCRIPT,'oracle/windows-dao/scripts/fixed_field_successor.ps1'):shutil.copyfile(ROOT/name,inbox/Path(name).name)
    shutil.copyfile(inbox/Path(SCRIPT).name,inbox/'script.ps1');shutil.copyfile(PLAN,inbox/PLAN.name)
    spec=importlib.util.spec_from_file_location('transport',ROOT/'scripts/windows-dao-ps.py');transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
    command=['ssh','-p',args.port,'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','IdentitiesOnly=yes','-i',args.identity,f'{args.user}@{args.host}','powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',transport.encoded(transport.guest_script(args.remote_shared_root,args.run_id,'script.ps1'))]
    done=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=600);outbox.mkdir(exist_ok=True);(outbox/'ssh.txt').write_bytes(done.stdout+done.stderr)
    require((outbox/'result.json').exists(),'Missing result; no retry');analyze(outbox);require(done.returncode==0,'Guest failed; no retry')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('preflight');p.add_argument('--images',type=Path,required=True)
    p=sub.add_parser('analyze');p.add_argument('outbox',type=Path)
    p=sub.add_parser('run');p.add_argument('--images',type=Path,required=True);p.add_argument('--run-id',required=True);p.add_argument('--shared-root',type=Path,required=True)
    for name,default in [('host','127.0.0.1'),('port','2222'),('user','jet3runner'),('identity',str(Path.home()/'.ssh/jet3-dao')),('remote-shared-root',r'\\host.lan\Data')]:p.add_argument('--'+name,default=default)
    args=parser.parse_args()
    if args.command=='preflight':preflight(args.images);print('Committed input/image pins match.')
    elif args.command=='analyze':analyze(args.outbox)
    else:dispatch(args)
