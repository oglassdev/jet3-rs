#!/usr/bin/env python3
"""Prepare larger-graph CLI mutation stages from retained inputs."""
import argparse,copy,hashlib,json,shutil,subprocess,sys,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--creation',type=Path,required=True);p.add_argument('--native-outbox',type=Path);p.add_argument('--origin',choices=['candidate','native'],required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True);p.add_argument('--cases',nargs='+');a=p.parse_args()
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
 edge=case['relations'][-1];parent=edge['parent'];child=edge['child']
 child_spec=next(t for t in case['tables'] if t['name']==child);parent_spec=next(t for t in case['tables'] if t['name']==parent)
 fk=next(i for i,f in enumerate(child_spec['fields']) if f['name']==edge['child_field']);body=next(i for i,f in enumerate(child_spec['fields']) if f['name']=='Body')
 def new_values(table,label,self_keys=False):
  return [2 if f['name']=='Id' or (self_keys and f['type']==4) else label if f['type']==12 else None for f in table['fields']]
 update=lambda column,value:{'operation':'update','table':child,'id':2,'column':column,'value':value}
 delete=lambda table,id,refused=False:{'operation':'delete','table':table,'id':id,**({'refused':True} if refused else {})}
 orphan=update(fk,99);orphan['refused']=True
 if parent==child:
  return [update(fk,1),update(body,'G'*4096),delete(parent,1,True),delete(child,2),
   {'operation':'insert','table':child,'id':2,'values':new_values(child_spec,'self',True)},delete(child,2),
   {'operation':'insert','table':child,'id':2,'values':new_values(child_spec,'restored')},orphan]
 missing=update(fk,2);missing['refused']=True
 return [update(fk,2),update(body,'G'*4096),delete(parent,2,True),update(fk,None),delete(parent,2),missing,
  {'operation':'insert','table':parent,'id':2,'values':new_values(parent_spec,'restored-parent')},
  {'operation':'replace','table':child,'id':2,'values':new_values(child_spec,'restored-child')}]

a.output.mkdir(exist_ok=False)
creation=json.loads((a.creation/'matrix.json').read_text())
selected=['three-parents-shared-fk','three-table-cycle','fifteen-self-constraints','thirty-one-foreign-columns','long-relation-names'];groups=[]
for origin in [a.origin]:
 for id in (a.cases or selected):
  case=next(c for c in creation['graphs'] if c['id']==id)
  for replica in ([1] if origin=='candidate' else [2]):
   name=f'{origin}-{id}-r{replica}';d=a.output/name;d.mkdir()
   source=a.creation/'candidates'/f'{id}-r{replica}.mdb' if origin=='candidate' else a.native_outbox/f'r{replica}-{id}-native.mdb'
   source_pin=ident(source);shutil.copyfile(source,d/'initial.mdb');current=d/'current.mdb';shutil.copyfile(source,current)
   state={t['name']:copy.deepcopy(t['rows']) for t in case['tables']};events=[]
   for ordinal,op in enumerate(operations(case),1):
    table=next(t for t in case['tables'] if t['name']==op['table']);fields=table['fields'];request={'operation':op['operation'],'table':op['table']}
    if op['operation']!='insert':request['row']=locate(current,op['table'],op['id'])
    if op['operation'] in ('insert','replace'):request['values']=cells(fields,op['values'])
    if op['operation']=='update':
     values=[next(row for row in state[op['table']] if row['Id']==op['id'])[f['name']] for f in fields]
     values[op['column']]=op['value'];request.update(operation='replace',values=cells(fields,values))
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
result={'document_type':'larger_relationship_graph_lifecycle_preparation','source_revision':a.source_revision,'binary':ident(a.binary),'groups':groups,'status':'accepted'}
(a.output/'matrix.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
with zipfile.ZipFile(a.output/'inputs.zip','x',compression=zipfile.ZIP_STORED) as z:
 z.write(a.output/'matrix.json','matrix.json')
 for g in groups:
  z.write(a.output/g['initial'],g['initial'])
  for e in g['events']:z.write(a.output/e['stage'],e['stage'])
print(json.dumps({'groups':len(groups),'stages':sum(len(g['events']) for g in groups),'refusals':sum(e['exit']!=0 for g in groups for e in g['events']),'bundle':ident(a.output/'inputs.zip')}))
