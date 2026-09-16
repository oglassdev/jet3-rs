#!/usr/bin/env python3
"""Create Required-column candidates and independent mutation checkpoints."""
import argparse,copy,hashlib,json,shutil,subprocess,sys,uuid,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--source-revision',required=True);p.add_argument('--native-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
sys.path.insert(0,str(a.repo/'oracle/windows-dao/scripts'))
import system_catalog as catalog
import relationship_mutation_structure as raw_rows
catalog.MAX_PAGES=8192;catalog.MAX_TABLES=64

def identity(path):
 b=path.read_bytes();return {'size':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def write(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def kind(case):return {'AutoIncrement':'auto_increment','DateTime':'date_time','FixedText':'fixed_text','LongBinary':'long_binary'}.get(case['name'],case['name'].lower())
def cell(case,state):
 name=case['name'];t=kind(case)
 if state=='null':return None
 if name=='AutoIncrement':return 'auto_increment'
 zero=state=='special';changed=state=='changed'
 if name=='Boolean':v=not(zero or changed)
 elif name=='Byte':v=0 if zero else 24 if changed else 23
 elif name=='Integer':v=0 if zero else -124 if changed else -123
 elif name=='Long':v=0 if zero else 124 if changed else 123
 elif name=='Currency':v=0 if zero else 123457 if changed else 123456
 elif name in ('Single','Double'):v=0.0 if zero else -2.5 if changed else 1.25
 elif name=='DateTime':v=0.0 if zero else 45001.5 if changed else 45000.25
 elif name=='Guid':v=list(uuid.UUID(int=0).bytes if zero else uuid.UUID('12345678-1234-5678-9abc-def012345679' if changed else '12345678-1234-5678-9abc-def012345678').bytes)
 elif name=='Text':v='' if zero else 'changed' if changed else 'text'
 elif name=='FixedText':t='text';v='    ' if zero else 'WXYZ' if changed else 'ABCD'
 elif name=='Memo':v='' if zero else 'M'*4096 if changed else 'memo'
 elif name in ('Binary','LongBinary'):v=[] if zero else ([1,0,66,254]*1024 if name=='LongBinary' else [1,0,66,254]) if changed else [0,65,255]
 else:raise ValueError(name)
 return {t:v}
def expect(case,operation):
 if operation['state']=='null':
  if case['name']=='Boolean':return True,None
  return (False,3314) if case['required'] else (True,None)
 if operation['state']=='special':
  if case['name'] in ('Text','Memo') and not case['allow_zero_length']:return False,3315
  if case['name'] in ('Binary','LongBinary') and case['required']:return False,3314
 return True,None
def locate(path):
 data=path.read_bytes();table=next(t for t in catalog.analyze_checkpoint(data)['tables'].values() if t['name']=='Rows')
 rows=raw_rows.rows(data,table);hits=[r['locator'] for r in rows if r['values']['Id']==1];assert len(hits)==1
 return {'page':hits[0]['page'],'slot':hits[0]['row']}
def run(args,stem):
 result=subprocess.run([str(a.binary),*map(str,args)],capture_output=True,text=True)
 Path(str(stem)+'.stdout').write_text(result.stdout);Path(str(stem)+'.stderr').write_text(result.stderr)
 return result
def validate(path,stem):
 result=run(['validate',path],stem);assert result.returncode==0,(str(path),result.stderr)
 return json.loads(result.stdout)

a.output.mkdir(exist_ok=False);native=a.native_root/'runs/20260916T050547Z-required-columns-r1/outbox'
cases=json.loads((a.native_root/'matrix.json').read_text())['cases'];creations=[];mutations=[];refusals=[]
for case in cases:
 d=a.output/case['id'];d.mkdir();column={'name':'Payload','type':kind(case),'required':case['required'],'allow_zero_length':case['allow_zero_length']}
 if case['name'] in ('Text','FixedText','Binary'):column['size']=case['size']
 schema={'tables':[{'name':'Rows','columns':[{'name':'Id','type':'long'},column], 'indexes':[{'name':'PrimaryKey','kind':'primary','fields':[{'column':'Id'}]}], 'rows':[]}]}
 for populated in (False,True):
  label='populated' if populated else 'empty';request=copy.deepcopy(schema)
  if populated:request['tables'][0]['rows']=[[{'long':1},cell(case,'value')]]
  requestpath=d/(label+'.request.json');write(requestpath,request);candidate=d/(label+'.mdb')
  result=run(['create',candidate,'--input',requestpath],d/(label+'.create'));assert result.returncode==0,(case['id'],label,result.stderr)
  validate(candidate,d/(label+'.validation'))
  source=native/f"r1-{case['id']}-{'01' if populated else 'initial'}.mdb";control=d/(label+'-native.mdb');shutil.copyfile(source,control)
  creations.append({'id':case['id']+'-'+label,'case':case,'populated':populated,'request':request,'candidate':str(candidate.relative_to(a.output)),'candidate_identity':identity(candidate),'control':str(control.relative_to(a.output)),'control_identity':identity(control),'native_source':str(source),'native_receipt':identity(native/f"r1-{case['id']}-result.json")})
 # Explicit initial-null refusal (or normalized Boolean false) is checked locally;
 # the equivalent native null behavior is retained in EXP-0283.
 request=copy.deepcopy(schema);request['tables'][0]['rows']=[[{'long':2},None]];rq=d/'initial-null.request.json';write(rq,request);dest=d/'initial-null.mdb';result=run(['create',dest,'--input',rq],d/'initial-null.create')
 accepted=(case['name']=='Boolean' or (not case['required'] and case['name']!='AutoIncrement'))
 if case['name']=='AutoIncrement':accepted=False
 assert (result.returncode==0)==accepted,(case['id'],result.stderr)
 assert dest.exists()==accepted
 refusals.append({'case':case['id'],'expected_accepted':accepted,'exit':result.returncode,'published':dest.exists(),'request':request,'stderr':result.stderr})
 if case['name']=='AutoIncrement':continue
 for origin in ('candidate','native'):
  source=d/('populated.mdb' if origin=='candidate' else 'populated-native.mdb');source_id=identity(source)
  ops=[{'kind':'insert','id':2,'state':'null'},{'kind':'update','id':1,'state':'null'}]
  if origin=='candidate':ops.extend([{'kind':'insert','id':4,'state':'special'},{'kind':'update','id':1,'state':'changed'}])
  for ordinal,op in enumerate(ops,1):
   stem=f"{origin}-{ordinal:02}-{op['kind']}-{op['state']}";path=d/(stem+'.mdb');shutil.copyfile(source,path)
   request={'operation':'insert' if op['kind']=='insert' else 'replace','table':'Rows','values':[{'long':op['id']},cell(case,op['state'])]}
   if op['kind']=='update':request['row']=locate(path)
   rq=d/(stem+'.request.json');write(rq,request);result=run(['mutate',path,'--input',rq],d/(stem+'.mutation'));accepted,code=expect(case,op)
   assert (result.returncode==0)==accepted,(case['id'],origin,op,result.stderr)
   if not accepted:assert identity(path)==source_id
   validate(path,d/(stem+'.validation'))
   mutations.append({'id':case['id']+'-'+stem,'case':case,'origin':origin,'operation':op,'request':request,'expected_accepted':accepted,'expected_native_error':code,'source':str(source.relative_to(a.output)),'source_identity':source_id,'stage':str(path.relative_to(a.output)),'stage_identity':identity(path),'exit':result.returncode,'stderr':result.stderr})
  assert identity(source)==source_id
result={'document_type':'required_column_acceptance_preparation','source_revision':a.source_revision,'binary':identity(a.binary),'native_manifest':identity(a.native_root/'MANIFEST.json'),'creations':creations,'mutations':mutations,'initial_null_checks':refusals}
write(a.output/'matrix.json',result)
files={item[role] for item in creations for role in ('candidate','control')}|{item[role] for item in mutations for role in ('source','stage')}
with zipfile.ZipFile(a.output/'inputs.zip','x',compression=zipfile.ZIP_STORED) as z:
 z.write(a.output/'matrix.json','matrix.json')
 for file in sorted(files):z.write(a.output/file,file)
print(json.dumps({'creations':len(creations),'mutations':len(mutations),'refusals':sum(not m['expected_accepted'] for m in mutations),'bundle':identity(a.output/'inputs.zip')}))
