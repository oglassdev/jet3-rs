#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;
import larger_graph_checks as structure
import larger_graph_negative_indexes as relaxed
PROVIDER={'ansi':1252,'bits':32,'culture':'en-US','dll_sha256':'4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac','dll_version':'03.60.9765.0','os':'Microsoft Windows NT 10.0.20348.0','provider':'DAO.DBEngine.36','version':'3.6'}
def req(x,m):
 if not x: raise AssertionError(m)
def ident(p):
 b=p.read_bytes();return {'size':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def norm_error(e):return None if e is None else {k:e.get(k) for k in ('numbers','hresult','message','type')}
def inbox_for(outbox):
 p=outbox.parent/'inbox';return p if p.is_dir() else outbox.parent.parent/'inbox'/outbox.name
def main():
 ap=argparse.ArgumentParser();ap.add_argument('matrix',type=Path);ap.add_argument('outbox',type=Path);ap.add_argument('report',type=Path);a=ap.parse_args();m=json.loads(a.matrix.read_text());cases={c['id']:c for c in m['cases']};expected=[(r,c['id']) for c in m['cases'] for r in range(1,m['replicas']+1)]
 allowed={'workers.json','exit.txt','log.txt'}|{f'r{r}-{c}-result.json' for r,c in expected}|{f'r{r}-{c}-native.mdb' for r,c in expected};actual={p.name for p in a.outbox.iterdir() if p.is_file()};req(actual==allowed,f'outbox inventory {sorted(allowed-actual)} {sorted(actual-allowed)}');req((a.outbox/'exit.txt').read_text().strip()=='0' and not (a.outbox/'log.txt').read_text(encoding='utf-8-sig').strip(),'wrapper')
 inbox=inbox_for(a.outbox);req({p.name for p in inbox.iterdir() if p.is_file()}=={'script.ps1','native-negative-matrix.json'},'inbox inventory');req((inbox/'script.ps1').read_bytes()==(HERE/'larger_graph_negative.ps1').read_bytes() and (inbox/'native-negative-matrix.json').read_bytes()==a.matrix.read_bytes(),'submitted bytes')
 workers=json.loads((a.outbox/'workers.json').read_text(encoding='utf-8-sig'));req(workers['matrix']==ident(a.matrix) and len(workers['workers'])==len(expected),'workers')
 # Preserve full key/locator checking but record, rather than accept, the native failed-write prefix mismatch.
 structure.system_index_inventory.inventory=relaxed.inventory
 obs=[]
 for pos,(replica,caseid) in enumerate(expected):
  rp=a.outbox/f'r{replica}-{caseid}-result.json';w=workers['workers'][pos];req((w['replica'],w['case'],w['exit_code'],w['result'])==(replica,caseid,0,ident(rp)),'worker link')
  r=json.loads(rp.read_text(encoding='utf-8-sig'));req(r['status']=='pass' and r['error'] is None and r['table_error'] is None and r['environment']==PROVIDER and r['matrix']==ident(a.matrix),'receipt')
  case=cases[caseid];limit=15 if caseid=='sixteen-self-constraints-refusal' else 31;req(len(r['relations'])==limit+1,'attempt inventory')
  for i,x in enumerate(r['relations']):
   if i<limit:req(x['error'] is None and (x['before_count'],x['after_count'])==(i,i+1),f'{caseid} accepted relation {i}')
   else:req(x['before_count']==x['after_count']==limit and x['error'] is not None and 3626 in x['error']['numbers'],f'{caseid} terminal 3626')
  p=a.outbox/r['file'];req(r['capture']['before']==r['capture']['after']==ident(p),'capture identity')
  evaluated=structure.evaluate_case(case,r,p);si=relaxed.inventory(p)
  for name,item in si.items():
   if name=='MSysObjects':req(item['definition_row_count']==item['live_row_count']+1 and all(x['second_word']==x['expected_second_word']+1 for x in item['indexes']),'MSysObjects failed-write mismatch')
   elif name=='MSysACEs':req(item['definition_row_count']==item['live_row_count']+2 and all(x['second_word']==x['expected_second_word']+1 for x in item['indexes']),'MSysACEs failed-write mismatch')
   else:req(item['definition_row_count']==item['live_row_count'] and all(x['prefix_matches_live_keys'] for x in item['indexes']),f'{name} intact')
  obs.append({'case':caseid,'replica':replica,'file':ident(p),'environment':r['environment'],'relation_outcomes':[{'name':x['name'],'before_count':x['before_count'],'after_count':x['after_count'],'error':norm_error(x['error'])} for x in r['relations']],'snapshot':r['capture']['snapshot'],'raw':evaluated['raw'],'system_indexes':si})
 for caseid in cases:
  pair=[x for x in obs if x['case']==caseid];req(len(pair)==2,'replicas');req(structure.normalized_snapshot(pair[0]['snapshot'])==structure.normalized_snapshot(pair[1]['snapshot']),'snapshot replication');req(pair[0]['relation_outcomes']==pair[1]['relation_outcomes'],'outcome replication')
  req(structure.normalized_raw_tables(pair[0]['raw']['tables'])==structure.normalized_raw_tables(pair[1]['raw']['tables']),'raw table replication')
  for key in ('page_count','page0_relationship_byte','free_pages','relationship_rows','relationship_objects','relationship_aces','relationship_system_indexes','maps'):
   req(pair[0]['raw'][key]==pair[1]['raw'][key],f'{caseid} raw replica {key}')
  req(pair[0]['system_indexes']==pair[1]['system_indexes'],f'{caseid} system index replication')
 report={'document_type':'larger_relationship_graph_negative_capacity_report','outcome':'native_refused_with_retained_integrity_failure','matrix':ident(a.matrix),'producer':ident(HERE/'larger_graph_negative.ps1'),'evaluator':ident(Path(__file__)),'provider':PROVIDER,'cases':len(cases),'captures':len(obs),'observations':obs};a.report.write_text(json.dumps(report,sort_keys=True,separators=(',',':'))+'\n');print(a.report);print('recorded',len(obs),'native refusal captures')
if __name__=='__main__':main()
