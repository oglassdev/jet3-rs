#!/usr/bin/env python3
"""Prepare paired relationship-index CLI mutation stages from retained inputs."""
import argparse,copy,hashlib,json,shutil,subprocess,sys,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[3]);p.add_argument('--binary',type=Path,required=True);p.add_argument('--creation',type=Path,required=True);p.add_argument('--discovery',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True);a=p.parse_args()
sys.path.insert(0,str(a.repo/'oracle/windows-dao/scripts'))
import system_catalog as catalog
import relationship_mutation_structure as structure
catalog.MAX_PAGES=8192;catalog.MAX_TABLES=64

def ident(p):
 b=p.read_bytes();return {'size':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def cells(fields,values):
 return [None if value is None else {'long':value} if f['type']==4 else {{10:'text',12:'memo'}[f['type']]:list(value.encode('cp1252'))} for f,value in zip(fields,values)]
def locate(path,table,id):
 data=path.read_bytes();ts=catalog.analyze_checkpoint(data)['tables'];t=next(t for t in ts.values() if t['name']==table)
 hits=[r['locator'] for r in structure.rows(data,t,primary_column='Id') if r['values']['Id']==id];assert len(hits)==1,(table,id,hits);return {'page':hits[0]['page'],'slot':hits[0]['row']}
def operations(case):
 parent=next(t for t in case['tables'] if t['name']=='Parent');vals={'Id':90,'ParentKey':90,'Other':190,'Label':'new'}
 return [
 {'operation':'insert','table':'Parent','id':90,'values':[vals[f['name']] for f in parent['fields']]},
 {'operation':'insert','table':'Child','id':90,'values':[90,90,'new']},
 {'operation':'update','table':'Child','id':10,'column':1,'value':90},
 {'operation':'replace','table':'Child','id':10,'values':[10,90,'G'*4096]},
 {'operation':'update','table':'Child','id':10,'column':1,'value':1},
 {'operation':'delete','table':'Child','id':90},
 {'operation':'delete','table':'Parent','id':90},
 {'operation':'update','table':'Child','id':10,'column':1,'value':99,'refused':True},
 {'operation':'delete','table':'Parent','id':1,'refused':True},
 ]

a.output.mkdir(exist_ok=False)
creation=json.loads((a.creation/'matrix.json').read_text());native={}
for name,run in [('matrix.json','20260916T024836Z-relationship-index-r1'),('supplement-matrix.json','20260916T030504Z-relationship-index-supp-r1')]:
 for case in json.loads((a.discovery/name).read_text())['cases']:native[case['id']]=(case,a.discovery/'runs'/run/'outbox')
selected=['nullable-parent-two-nulls','child-fk-two-aliases'];groups=[]
for origin in ['candidate','native']:
 for id in selected:
  case=next(c for c in creation['graphs'] if c['id']==id) if origin=='candidate' else native[id][0]
  for replica in [1,2]:
   name=f'{origin}-{id}-r{replica}';d=a.output/name;d.mkdir()
   source=a.creation/'candidates'/f'{id}-r{replica}.mdb' if origin=='candidate' else native[id][1]/f'r{replica}-{id}.mdb'
   source_pin=ident(source);shutil.copyfile(source,d/'initial.mdb');current=d/'current.mdb';shutil.copyfile(source,current)
   state={t['name']:copy.deepcopy(t['rows']) for t in case['tables']};events=[]
   for ordinal,op in enumerate(operations(case),1):
    table=next(t for t in case['tables'] if t['name']==op['table']);fields=table['fields'];request={'operation':op['operation'],'table':op['table']}
    if op['operation']!='insert':request['row']=locate(current,op['table'],op['id'])
    if op['operation'] in ('insert','replace'):request['values']=cells(fields,op['values'])
    if op['operation']=='update':request.update(column=op['column'],value=cells([fields[op['column']]],[op['value']])[0])
    stem=f'{ordinal:02d}-{op["operation"]}';rq=d/(stem+'.request.json');rq.write_text(json.dumps(request,sort_keys=True,indent=2)+'\n');before=ident(current)
    run=subprocess.run([str(a.binary),'mutate',str(current),'--input',str(rq)],capture_output=True,text=True);(d/(stem+'.stdout')).write_text(run.stdout);(d/(stem+'.stderr')).write_text(run.stderr)
    refused=op.get('refused',False);assert (run.returncode!=0)==refused,(name,op,run.stderr)
    if refused:assert ident(current)==before
    else:
     rows=state[op['table']]
     if op['operation']=='insert':rows.append(dict(zip([f['name'] for f in fields],op['values'])))
     elif op['operation']=='delete':rows[:]=[r for r in rows if r['Id']!=op['id']]
     else:
      row=next(r for r in rows if r['Id']==op['id'])
      if op['operation']=='replace':row.update(dict(zip([f['name'] for f in fields],op['values'])))
      else:row[fields[op['column']]['name']]=op['value']
    stage=d/(stem+'.mdb');shutil.copyfile(current,stage)
    check=subprocess.run([str(a.binary),'validate',str(stage)],capture_output=True,text=True);(d/(stem+'.validate.stdout')).write_text(check.stdout);(d/(stem+'.validate.stderr')).write_text(check.stderr);assert check.returncode==0,(name,stem,check.stderr)
    events.append({'ordinal':ordinal,'operation':op,'request':request,'exit':run.returncode,'before':before,'stage':f'{name}/{stage.name}','image':ident(stage),'expected_rows':copy.deepcopy(state)})
   assert ident(source)==source_pin
   groups.append({'id':name,'case':case,'origin':origin,'replica':replica,'source':str(source),'source_identity':source_pin,'initial':name+'/initial.mdb','events':events})
result={'document_type':'relationship_index_lifecycle_preparation','source_revision':a.source_revision,'binary':ident(a.binary),'groups':groups,'status':'accepted'}
(a.output/'matrix.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
with zipfile.ZipFile(a.output/'inputs.zip','x',compression=zipfile.ZIP_STORED) as z:
 z.write(a.output/'matrix.json','matrix.json')
 for g in groups:
  z.write(a.output/g['initial'],g['initial'])
  for e in g['events']:z.write(a.output/e['stage'],e['stage'])
print(json.dumps({'groups':len(groups),'stages':sum(len(g['events']) for g in groups),'refusals':sum(e['exit']!=0 for g in groups for e in g['events']),'bundle':ident(a.output/'inputs.zip')}))
