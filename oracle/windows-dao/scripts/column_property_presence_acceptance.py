#!/usr/bin/env python3
"""Complete DAO comparison of same-input Rust property-presence mutations."""
import argparse,copy,json,sys,tempfile,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--native-run',type=Path,required=True);p.add_argument('--capture-run',type=Path,required=True);p.add_argument('--producer',type=Path,required=True);p.add_argument('--report',type=Path,required=True);args=p.parse_args()
sys.path.insert(0,str(args.repo/'oracle/windows-dao/scripts'))
import required_column_acceptance as a
import required_column_discovery as d
import column_property_checks as props
require=d.require;identity=d.identity;catalog=d.catalog

def property_observation(data,case):
 expected={}
 if case['other_field_property']:expected['Id']={'Required':False}
 if case['set_required']:expected.setdefault('Payload',{})['Required']=case['required']
 if case['set_allow_zero_length']:expected.setdefault('Payload',{})['AllowZeroLength']=case['allow_zero_length']
 definition,pages,_=catalog._discover_catalog(data);name,ordinal=(catalog._ordinal(definition,n) for n in ('Name','LvProp'))
 for page in pages:
  image=catalog._page(data,page,'catalog properties')
  for entry in catalog._row_directory(image,page):
   if entry['hidden']:continue
   fields=props.row_layout(image[entry['start']:entry['end']],definition['columns'])
   if fields[name][1]!=b'Rows':continue
   if not fields[ordinal][0]:
    require(expected=={},'expected complete absence');return dict(payload_hex=None,descriptor=None,model=None)
   payload,descriptor=props.property_payload(data,'Rows');model=d.dictionary_and_blocks(payload);observed={}
   for block in model['blocks']:
    require(block['name'] not in observed,'unique property owner');values={}
    for record in block['records']:
     raw=bytes.fromhex(record['raw_hex']);require(raw[2:4]==b'\1\1' and raw[5:8]==b'\0\1\0' and raw[8] in (0,255),'exact Boolean record')
     require(record['name'] not in values,'unique field property');values[record['name']]=record['value']
    observed[block['name']]=values
   require(observed==expected,'exact partial property model')
   require(model['dictionary']==[name for name in ('Required','AllowZeroLength') if any(name in values for values in expected.values())],'exact partial dictionary')
   return dict(payload_hex=payload.hex(),descriptor=descriptor,model=model)
 raise ValueError('Rows catalog entry missing')

def outcome(case,op):
 if op['state'] in ('null','omit') and case['required']:return False,[3314]
 if op['state']=='empty' and case['name']!='FixedText' and case['set_allow_zero_length'] and not case['allow_zero_length']:return False,[3315]
 return True,[]

run=args.capture_run;inbox=run/'inbox';outbox=run/'outbox';native=args.native_run/'outbox';archive=inbox/'presence-candidates.zip'
require({p.name for p in inbox.iterdir()}=={'script.ps1','presence-candidates.zip'},'complete inbox')
require(identity(inbox/'script.ps1')==identity(args.producer),'exact capture producer')
require({p.name for p in outbox.iterdir()}=={'workers.json','exit.txt','log.txt','worker-1-result.json','worker-2-result.json','worker-1-progress.json','worker-2-progress.json'},'complete capture outbox')
require((outbox/'exit.txt').read_text()=='0\n' and (outbox/'log.txt').read_bytes()==b'','successful guest exit')
workers=json.loads((outbox/'workers.json').read_text());require(workers['document_type']=='column_property_presence_capture_workers' and workers['script']==identity(args.producer) and workers['archive']==identity(archive),'master identity')
with tempfile.TemporaryDirectory(prefix='jet3-property-presence-') as temp:
 inputs=Path(temp)
 with zipfile.ZipFile(archive) as z:
  names=z.namelist();require(len(names)==len(set(names)) and all(Path(n).name==n for n in names),'unique flat ZIP paths');matrix=json.loads(z.read('matrix.json'));items=matrix['items'];require(len(items)==252 and matrix['status']=='prepared_not_dao_verified' and matrix['failures']==[],'complete successful preparation');require(set(names)=={'matrix.json',*(i['file'] for i in items)},'ZIP inventory');z.extractall(inputs)
 require(workers['matrix']==identity(inputs/'matrix.json'),'matrix identity');require(matrix['native_run']==args.native_run.name,'native run identity')
 require([(w['worker'],w['file'],w['exit_code']) for w in workers['workers']]==[(n,f'worker-{n}-result.json',0) for n in (1,2)],'successful two workers')
 captures={};environment=None
 for worker in workers['workers']:
  path=outbox/worker['file'];require(identity(path)==worker['identity'],'worker report identity');receipt=json.loads(path.read_text());number=worker['worker'];require(receipt['document_type']=='column_property_presence_captures' and receipt['worker']==number and receipt['status']=='pass' and receipt['error'] is None,'complete worker report');require(all(receipt[k]==workers[k] for k in ('matrix','archive','script')),'worker input linkage')
  environment=receipt['environment'] if environment is None else environment;require(receipt['environment']==environment,'same worker environment')
  expected=items[number-1::2];require([(c['id'],c['file']) for c in receipt['captures']]==[(i['id'],i['file']) for i in expected],'complete ordered captures');require(json.loads((outbox/f'worker-{number}-progress.json').read_text())==dict(worker=number,completed=len(expected),last_id=expected[-1]['id']),'final progress')
  for capture in receipt['captures']:require(capture['id'] not in captures,'unique capture');captures[capture['id']]=capture['capture']
 require(environment['provider']=='DAO.DBEngine.36' and environment['version']=='3.6' and environment['bits']==32 and environment['culture']=='en-US' and environment['ansi']==1252 and environment['dll_sha256']=='4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac','provider')
 results=[];seen=set();initials={}
 for item in items:
  case=item['case'];replica=item['replica'];ordinal=item['ordinal'];stem=f"r{replica}-{case['id']}";label=f'{stem}-{ordinal:02}';require(item['id']==label and label not in seen,'unique expected identity');seen.add(label)
  receiptpath=native/(stem+'-result.json');require(identity(receiptpath)==item['receipt'],'native receipt identity');receipt=json.loads(receiptpath.read_text());require(receipt['case']==case and receipt['replica']==replica and receipt['status']=='pass' and receipt['error'] is None and receipt['environment']==environment,'native complete receipt and environment')
  stage=receipt['stages'][ordinal-1];previous=receipt['initial'] if ordinal==1 else receipt['stages'][ordinal-2]['stage'];op=stage['operation'];accepted,errors=outcome(case,op)
  require(item['operation']==op and item['accepted']==stage['accepted']==accepted and item['native_errors']==([] if stage['error'] is None else stage['error']['numbers'])==errors and (item['rust_exit']==0)==accepted,'independent exact operation outcome')
  require(item['source_file']==previous['file'] and item['source_identity']==identity(native/previous['file'])==previous['capture']['before']==previous['capture']['after'],'same input')
  require(item['native_file']==stage['stage']['file'] and item['native_identity']==identity(native/item['native_file']),'native output identity');require(identity(inputs/item['file'])==item['identity'],'Rust output identity')
  expected=d.expected_api_rows(case,receipt['stages'])
  before_rows=[] if ordinal==1 else expected[ordinal-2];after_rows=expected[ordinal-1]
  before=a.observe(native/previous['file'],previous['capture'],case,before_rows,property_reader=property_observation)
  pair={}
  for role,path,capture in [('rust',inputs/item['file'],captures[label]),('native',native/item['native_file'],stage['stage']['capture'])]:
   value=a.observe(path,capture,case,after_rows,property_reader=property_observation)
   for key in ('schema','definition_hashes','properties','system_indexes','system_page_hashes'):require(value[key]==before[key],label+': preserved '+key)
   if not accepted:require(value['identity']==before['identity'],label+': whole-file refusal preservation')
   old={row['values']['Id']:row for row in before['rows']}
   for row in value['rows']:
    if row['values']['Id']!=op['id']:require(row==old[row['values']['Id']],label+': unselected row exact')
   pair[role]=value
  require(d.normalized_snapshot(captures[label]['snapshot'])==d.normalized_snapshot(stage['stage']['capture']['snapshot']),label+': complete DAO snapshot equality')
  column=before['schema']['columns'][1]
  require([a.comparable_row(row,column,row['values']['Id']==op['id']) for row in pair['rust']['rows']]==[a.comparable_row(row,column,row['values']['Id']==op['id']) for row in pair['native']['rows']],label+': complete row structure outside assigned null padding/payload placement')
  for key in ('index','maps','free_pages','global'):require(pair['rust'][key]==pair['native'][key],label+': exact '+key)
  results.append(dict(id=label,accepted=accepted,native_errors=errors,request=item['request'],before=before,**pair))
 require(len(seen)==252 and len({(i['case']['id'],i['replica']) for i in items})==36,'full 18-schema two-replica scope')
 report=dict(document_type='column_property_presence_acceptance_evaluation',status='pass',source_revision=matrix['source_revision'],binary=matrix['binary'],native_run=args.native_run.name,capture_run=args.capture_run.name,producer=identity(args.producer),evaluator=identity(Path(__file__)),matrix=identity(inputs/'matrix.json'),archive=identity(archive),provider=environment,mutation_pairs=252,accepted=sum(r['accepted'] for r in results),refused=sum(not r['accepted'] for r in results),results=results)
 args.report.write_text(json.dumps(report,indent=2,sort_keys=True,default=a.compact_json)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='results'}))
