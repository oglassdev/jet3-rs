#!/usr/bin/env python3
"""Describe native property-presence policies and compare both replicas."""
import argparse,copy,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--run-id',required=True);p.add_argument('--report',type=Path,required=True);args=p.parse_args()
sys.path.insert(0,str(args.repo/'oracle/windows-dao/scripts'))
import required_column_discovery as d
import column_property_checks as props
import relationship_system_indexes as systems
require=d.require;ident=d.identity;catalog=d.catalog

def property_payload(data):
 definition,pages,_=catalog._discover_catalog(data);name,ordinal=(catalog._ordinal(definition,n) for n in ('Name','LvProp'))
 for page in pages:
  image=catalog._page(data,page,'catalog properties')
  for entry in catalog._row_directory(image,page):
   if entry['hidden']:continue
   fields=props.row_layout(image[entry['start']:entry['end']],definition['columns'])
   if fields[name][1]!=b'Rows':continue
   if not fields[ordinal][0]:return {'payload_hex':None,'descriptor':None,'model':None}
   payload,descriptor=props.property_payload(data,'Rows')
   return {'payload_hex':payload.hex(),'descriptor':descriptor,'model':d.dictionary_and_blocks(payload)}
 raise ValueError('Rows catalog entry missing')

def inspect(path,capture,case,expected):
 require(ident(path)==capture['before']==capture['after'],'closed unchanged capture/'+path.name)
 snap=capture['snapshot'];require(snap['version']=='3.0' and snap['relations']==[] and len(snap['tables'])==1,'database inventory')
 table=snap['tables'][0];require(table['name']['value']=='Rows' and table['rows']==expected,'complete DAO values/'+path.name)
 f=table['fields'];require(len(f)==2 and f[0]['name']['value']=='Id' and f[0]['type']==4,'Id schema')
 require((f[1]['name']['value'],f[1]['type'],f[1]['size'],f[1]['attributes'],f[1]['required'],f[1]['allow_zero_length'])==('Payload',case['type'],case['size'],case['attributes'],case['required'],case['allow_zero_length']),'requested/default DAO column properties')
 require(set(table['index_reads'])=={'PrimaryKey'},'primary inventory');read=table['index_reads']['PrimaryKey'];require(read['error'] is None and read['field']=='Id' and read['traversal']==expected,'complete primary traversal')
 byid={r['Id']:r for r in expected};require([s['query'] for s in read['seek']]==list(byid)+[2147483000],'Seek inventory');require(all(s['row']==byid.get(s['query']) and s['no_match']==(s['query'] not in byid) for s in read['seek']),'complete Seek values')
 data=path.read_bytes();analysis=catalog.analyze_checkpoint(data);named={t['name']:t for t in analysis['tables'].values()};require(set(named)=={'Rows',*systems.SYSTEM_INDEXES},'raw table inventory')
 definition=named['Rows']['definition'];rows=d.raw_rows.rows(data,named['Rows']);require([{'Id':r['values']['Id'],'Payload':d.api_from_raw(r['values']['Payload'],case)} for r in rows]==expected,'complete raw values')
 require(len(definition['physical_indexes'])==1,'one physical primary');idx=definition['physical_indexes'][0];require(idx['keys']==[{'column':0,'direction':1}] and idx['flags']==9,'raw primary schema');nodes,entries=d.indexes.tree(data,idx['root'],definition['root'],[(4,False)]);require(entries==sorted(d.long_key(r['values']['Id'],r['locator']) for r in rows),'complete primary keys and locators');require(idx['prefix_hex']=='00000000' and idx['entry_count']==len(rows),'exact primary counters')
 return {'identity':ident(path),'rows':rows,'definition':definition,'properties':property_payload(data),'index':{'nodes':nodes,'entries_hex':[e.hex() for e in entries]},'system_indexes':systems.inventory(path),'free_pages':analysis['free_pages']}

root=args.root;run=root/'runs'/args.run_id;inbox,outbox=run/'inbox',run/'outbox';matrix=json.loads((root/'matrix.json').read_text());cases=matrix['cases'];require(len(cases)==18 and matrix['replicas']==2,'matrix scope')
require({p.name for p in inbox.iterdir()}=={'matrix.json','script.ps1','inputs.zip'},'inbox inventory');require(ident(inbox/'matrix.json')==ident(root/'matrix.json') and ident(inbox/'script.ps1')==ident(root/'column_property_presence.ps1'),'submitted inputs');require(ident(inbox/'inputs.zip')==ident(root/'inputs.zip'),'submitted variant ZIP')
expected_files={'workers.json','exit.txt','log.txt'}
for c in cases:
 for replica in (1,2):
  stem=f"r{replica}-{c['id']}";expected_files.update({stem+'-result.json',stem+'-initial.mdb'});expected_files.update(stem+f'-{i:02}.mdb' for i in range(1,8))
require({p.name for p in outbox.iterdir()}==expected_files,'exact outbox');require((outbox/'exit.txt').read_text()=='0\n' and (outbox/'log.txt').read_bytes()==b'','guest success')
workers=json.loads((outbox/'workers.json').read_text());require(workers['document_type']=='column_property_presence_workers' and workers['matrix']==ident(root/'matrix.json'),'worker header');require(len(workers['workers'])==36 and {(w['case'],w['replica']) for w in workers['workers']}=={(c['id'],r) for c in cases for r in (1,2)} and all(w['exit_code']==0 for w in workers['workers']),'successful worker inventory')
results=[];environment=None
for case in cases:
 replicas=[];snapshots=[]
 for replica in (1,2):
  stem=f"r{replica}-{case['id']}";path=outbox/(stem+'-result.json');receipt=json.loads(path.read_text());require(receipt['document_type']=='column_property_presence_discovery' and receipt['case']==case and receipt['replica']==replica and receipt['status']=='pass' and receipt['error'] is None and receipt['schema_error'] is None,'successful case receipt')
  require(receipt['matrix']==ident(root/'matrix.json') and receipt['script']==ident(root/'column_property_presence.ps1'),'receipt input linkage')
  env=receipt['environment'];require(env['provider']=='DAO.DBEngine.36' and env['version']=='3.6' and env['bits']==32 and env['culture']=='en-US' and env['ansi']==1252 and env['dll_sha256']=='4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac','provider');environment=env if environment is None else environment;require(env==environment,'same provider')
  require(receipt['initial']['file']==stem+'-initial.mdb','initial filename');initial=inspect(outbox/receipt['initial']['file'],receipt['initial']['capture'],case,[]);require(initial['identity']==receipt['input']==case['inputs'][str(replica)]['identity'],'prepared variant remains unchanged at first capture');rows={};observations=[];before=initial['identity'];snaps=[d.normalized_snapshot(receipt['initial']['capture']['snapshot'])]
  operations=[('insert',1,'value'),('insert',2,'null'),('insert',3,'omit'),('insert',4,'empty'),('update',1,'empty'),('update',1,'null'),('update',1,'value')];require(len(receipt['stages'])==7,'seven operations')
  for ordinal,(stage,(kind,key,state)) in enumerate(zip(receipt['stages'],operations),1):
   require(stage['operation']==dict(kind=kind,id=key,state=state),'exact operation');errors=[] if stage['error'] is None else stage['error']['numbers']
   if state=='value' or (state in ('null','omit') and not case['required']) or (state=='empty' and (case['name']=='FixedText' or case['allow_zero_length'])):require(stage['accepted'] and errors==[],'known successful control')
   elif state in ('null','omit') and case['required']:require(not stage['accepted'] and errors==[3314],'required refusal control')
   elif state=='empty' and case['set_allow_zero_length']:require(not stage['accepted'] and errors==[3315],'explicit disabled empty control')
   else:require((stage['accepted'] and errors==[]) or (not stage['accepted'] and errors==[3315]),'missing property outcome class')
   if stage['accepted']:rows[key]=d.value_for(case,state)
   expected=[{'Id':key,'Payload':rows[key]} for key in sorted(rows)];saved=stage['stage'];require(saved['file']==stem+f'-{ordinal:02}.mdb','stage filename');raw=inspect(outbox/saved['file'],saved['capture'],case,expected)
   require(raw['properties']==initial['properties'],'unchanged property payload/descriptor/model');require(raw['system_indexes']==initial['system_indexes'],'unchanged system index keys')
   if not stage['accepted']:require(raw['identity']==before,'whole-file refusal preservation')
   observations.append({'operation':stage['operation'],'accepted':stage['accepted'],'errors':errors,'raw':raw});before=raw['identity'];snaps.append(d.normalized_snapshot(saved['capture']['snapshot']))
  replicas.append({'replica':replica,'receipt':ident(path),'initial':initial,'operations':observations});snapshots.append(snaps)
 require(snapshots[0]==snapshots[1],'complete replica DAO snapshots after timestamp normalization')
 require(replicas[0]['initial']['properties']==replicas[1]['initial']['properties'],'replica initial property models')
 require([(s['accepted'],s['errors']) for s in replicas[0]['operations']]==[(s['accepted'],s['errors']) for s in replicas[1]['operations']],'replica outcomes')
 results.append({'case':case,'replicas':replicas})
report=dict(document_type='column_property_presence_evaluation',status='pass',scope='native_observations_on_controlled_property_variants',run_id=args.run_id,cases=results,provider=environment,matrix=ident(root/'matrix.json'),producer=ident(root/'column_property_presence.ps1'),evaluator=ident(Path(__file__)),checkpoints=288,operations=252)
def encode_bytes(value):
 if isinstance(value,bytes):return {'bytes_hex':value.hex()}
 raise TypeError(type(value).__name__)
args.report.write_text(json.dumps(report,indent=2,sort_keys=True,default=encode_bytes)+'\n');print(json.dumps({'status':'pass','checkpoints':288,'operations':252,'report':ident(args.report)}))
