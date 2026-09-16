#!/usr/bin/env python3
"""Prepare same-input Rust mutations for closed native presence observations."""
import argparse, hashlib, json, shutil, subprocess, sys, zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--native-outbox',type=Path,required=True);p.add_argument('--native-matrix',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True);a=p.parse_args()
sys.path.insert(0,str(a.repo/'oracle/windows-dao/scripts'))
import required_column_discovery as d
import required_column_acceptance as acceptance
import column_property_checks as props
import relationship_system_indexes as systems
from column_property_presence_allocation import check_empty_insert
catalog=d.catalog

def identity(path):
 data=path.read_bytes();return dict(size=len(data),sha256=hashlib.sha256(data).hexdigest())
def write(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True,default=acceptance.compact_json)+'\n')
def property_payload(data):
 definition,pages,_=catalog._discover_catalog(data);name,ordinal=(catalog._ordinal(definition,n) for n in ('Name','LvProp'))
 for page in pages:
  image=catalog._page(data,page,'catalog properties')
  for entry in catalog._row_directory(image,page):
   if entry['hidden']:continue
   fields=props.row_layout(image[entry['start']:entry['end']],definition['columns'])
   if fields[name][1]!=b'Rows':continue
   if not fields[ordinal][0]:return None
   return props.property_payload(data,'Rows')
 raise ValueError('Rows missing')
def raw(path):
 data=path.read_bytes();analysis=catalog.analyze_checkpoint(data);table=next(t for t in analysis['tables'].values() if t['name']=='Rows');definition=table['definition'];rows=d.raw_rows.rows(data,table)
 index=definition['physical_indexes'][0];nodes,entries=d.indexes.tree(data,index['root'],definition['root'],[(4,False)])
 assert entries==sorted(d.long_key(row['values']['Id'],row['locator']) for row in rows)
 assert index['entry_count']==len(rows) and index['prefix_hex']=='00000000'
 return dict(rows=rows,definition=definition,entries_hex=[e.hex() for e in entries],properties=property_payload(data),systems=systems.inventory(path),allocation=acceptance.allocation_observation(data,analysis,[node['page'] for node in nodes],systems.inventory(path)))
def execute(args,stem):
 r=subprocess.run([str(a.binary),*map(str,args)],capture_output=True,text=True);Path(str(stem)+'.stdout').write_text(r.stdout);Path(str(stem)+'.stderr').write_text(r.stderr);return r

matrix=json.loads((a.native_matrix).read_text());receipts={}
for case in matrix['cases']:
 for replica in (1,2):
  stem=f"r{replica}-{case['id']}";path=a.native_outbox/(stem+'-result.json');receipt=json.loads(path.read_text());assert receipt['status']=='pass' and len(receipt['stages'])==7;receipts[stem]=receipt
assert len(receipts)==36
a.output.mkdir(exist_ok=False);items=[];failures=[]
for case in matrix['cases']:
 for replica in (1,2):
  stem=f"r{replica}-{case['id']}";receipt=receipts[stem];previous=receipt['initial']
  for ordinal,stage in enumerate(receipt['stages'],1):
   label=f'{stem}-{ordinal:02}';source=a.native_outbox/previous['file'];native=a.native_outbox/stage['stage']['file'];target=a.output/(label+'.mdb');shutil.copyfile(source,target)
   assert identity(source)==previous['capture']['before']==previous['capture']['after']
   assert identity(native)==stage['stage']['capture']['before']==stage['stage']['capture']['after']
   op=stage['operation'];state=op['state'];value=d.value_for(case,state);cell=None if value is None else {('memo' if case['name']=='Memo' else 'text'):bytes.fromhex(value).decode('cp1252')}
   before=raw(source)
   if op['kind']=='insert':request=dict(operation='insert',table='Rows',values=[{'long':op['id']},cell])
   else:
    hits=[r['locator'] for r in before['rows'] if r['values']['Id']==op['id']];assert len(hits)==1;locator=dict(page=hits[0]['page'],slot=hits[0]['row'])
    request=dict(operation='replace',table='Rows',row=locator,values=[{'long':op['id']},cell])
   rq=a.output/(label+'.request.json');write(rq,request);mutation=execute(['mutate',target,'--input',rq],a.output/(label+'.mutate'))
   errors=[] if stage['error'] is None else stage['error']['numbers'];accepted=stage['accepted'];actual=mutation.returncode==0
   if actual!=accepted:failures.append(dict(id=label,kind='outcome',native_accepted=accepted,native_errors=errors,rust_stderr=mutation.stderr))
   if not actual:assert identity(target)==identity(source)
   validation=execute(['validate',target],a.output/(label+'.validate'))
   if validation.returncode:failures.append(dict(id=label,kind='validation',stderr=validation.stderr))
   rust,control=raw(target),raw(native);column=before['definition']['columns'][1]
   try:
    if case['id'].endswith('-absent') and ordinal==1:
     check_empty_insert(source,target,native)
    else:
     assert rust['entries_hex']==control['entries_hex'],'complete physical keys and locators'
     assert [acceptance.comparable_row(row,column,row['values']['Id']==op['id']) for row in rust['rows']]==[acceptance.comparable_row(row,column,row['values']['Id']==op['id']) for row in control['rows']],'row bytes outside assigned null padding/payload placement'
     assert rust['definition']==control['definition'],'complete definitions'
     for key in ('properties','systems'):assert rust[key]==control[key]==before[key],key
     for key in ('maps','free_pages','global','system_page_hashes'):assert rust['allocation'][key]==control['allocation'][key],key
   except AssertionError as e:failures.append(dict(id=label,kind='raw_comparison',detail=str(e)))
   items.append(dict(id=label,case=case,replica=replica,ordinal=ordinal,operation=op,request=request,source_file=previous['file'],source_identity=identity(source),native_file=stage['stage']['file'],native_identity=identity(native),receipt=identity(a.native_outbox/(stem+'-result.json')),file=target.name,identity=identity(target),accepted=accepted,native_errors=errors,rust_exit=mutation.returncode,rust_stderr=mutation.stderr))
   previous=stage['stage']
result=dict(document_type='column_property_presence_acceptance_inputs',status='prepared_not_dao_verified' if not failures else 'failed',source_revision=a.source_revision,binary=identity(a.binary),native_run=a.native_outbox.name,native_matrix=identity(a.native_matrix),items=items,failures=failures)
write(a.output/'matrix.json',result)
with zipfile.ZipFile(a.output/'presence-candidates.zip','x',compression=zipfile.ZIP_STORED) as z:
 z.write(a.output/'matrix.json','matrix.json')
 for item in items:z.write(a.output/item['file'],item['file'])
print(json.dumps(dict(status=result['status'],cases=len(items),failures=failures,bundle=identity(a.output/'presence-candidates.zip'))))
if failures:raise SystemExit(1)
