#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, sys, zipfile
from pathlib import Path
HERE=Path(__file__).resolve().parent

import larger_graph_checks as structure
import relationship_system_indexes as system_index_inventory
from relationship_mutation_structure import logical_relation
PROVIDER={'ansi':1252,'bits':32,'culture':'en-US','dll_sha256':'4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac','dll_version':'03.60.9765.0','os':'Microsoft Windows NT 10.0.20348.0','provider':'DAO.DBEngine.36','version':'3.6'}
def req(ok,msg):
 if not ok: raise AssertionError(msg)
def ident(path):
 b=path.read_bytes();return {'size':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def pseudo(capture,graph,replica):
 return {'status':'pass','error':None,'table_error':None,'capture':capture,'replica':replica,'environment':{},'relations':[{'name':r['name'],'error':None} for r in graph['relations']]}
def roots(raw): return {t['root']:n for n,t in raw['tables'].items()}
def logical_semantics(raw,name):
 table=raw['tables'][name];byroot=roots(raw);out=[]
 for item in table['logical_indexes']:
  encoded=bytes.fromhex(item['raw_hex'])
  if item['class']==2:
   r=logical_relation(item['raw_hex']);p=table['physical_indexes'][r['physical_index']]
   out.append({'name':item['name'],'class':2,'selector':r['selector'],'side':r['side'],'relation_ordinal':r['relation_ordinal'],'context':r['context_hex'],'related_table':byroot[r['related_root']],'selected_flags':p['flags'],'selected_keys':p['keys']})
  else:
   selector=int.from_bytes(encoded[4:8],'little');p=table['physical_indexes'][selector]
   out.append({'name':item['name'],'class':item['class'],'selected_flags':p['flags'],'selected_keys':p['keys']})
 return out
def validate_counters(raw,graph,role):
 specs={t['name']:t for t in graph['tables']}
 for name,spec in specs.items():
  table=raw['tables'][name]
  for p in table['physical_indexes']:
   entries=[bytes.fromhex(x) for x in p['entries_hex']]
   req(p['second_word']==len({x[:-4] for x in entries}),f'{graph["id"]}/{role}/{name}/{p["index"]} distinct counter')
   if role=='candidate': req(p['first_word']==0,f'{graph["id"]}/{name}/{p["index"]} candidate first word')
   elif p['first_word']:
    req(name in {r['child'] for r in graph['relations']} and p['first_word']==len(spec['rows']),f'{graph["id"]}/{name}/{p["index"]} native first word')
 if role=='native':
  for rel in graph['relations']:
   child=raw['tables'][rel['child']]
   rs=[logical_relation(i['raw_hex']) for i in child['logical_indexes'] if i['class']==2 and i['name']==rel['name']]
   req(len(rs)==1,f'{graph["id"]}/{rel["name"]} named record')
   selected=child['physical_indexes'][rs[0]['physical_index']]
   declared=next(t for t in graph['tables'] if t['name']==rel['child'])['indexes']
   reusable=any(i['field']==rel['child_field'] and i['direction']=='asc' and not any(i[k] for k in ('primary','unique','required','ignore_nulls')) for i in declared)
   expected=0 if reusable else len(specs[rel['child']]['rows'])
   req(selected['first_word']==expected,f'{graph["id"]}/{rel["name"]} native selected prefix history')
def inbox_for(outbox):
 p=outbox.parent/'inbox'
 return p if p.is_dir() else outbox.parent.parent/'inbox'/outbox.name
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--evidence-root',type=Path,required=True);ap.add_argument('matrix',type=Path);ap.add_argument('bundle',type=Path);ap.add_argument('outbox',type=Path);ap.add_argument('report',type=Path);a=ap.parse_args()
 matrix=json.loads(a.matrix.read_text());graphs={g['id']:g for g in matrix['graphs']};pairs=[(r,g['id']) for g in matrix['graphs'] for r in range(1,matrix['replicas']+1)]
 pins=json.loads((a.evidence_root/'source-pins.json').read_text());prep=json.loads((a.evidence_root/'preparation.json').read_text())
 req(pins['source_revision']=='e56791a8da1d022c48db303c817841c92d137688' and pins['creation_bundle']==ident(a.bundle) and pins['creation_matrix']==ident(a.matrix),'source/input pins')
 req(prep['status']=='accepted' and prep['exit']==0 and len(prep['files'])==20 and all(x['matches_reference'] for x in prep['files']),'preparation receipt')
 allowed={'workers.json','exit.txt','log.txt'}|{f'r{r}-{c}{s}' for r,c in pairs for s in ('-result.json','-candidate.mdb','-native.mdb')}
 actual={p.name for p in a.outbox.iterdir() if p.is_file()};req(actual==allowed,f'outbox inventory missing={sorted(allowed-actual)} extra={sorted(actual-allowed)}')
 req((a.outbox/'exit.txt').read_text().strip()=='0' and not (a.outbox/'log.txt').read_text(encoding='utf-8-sig').strip(),'wrapper exit/log')
 inbox=inbox_for(a.outbox);req({p.name for p in inbox.iterdir() if p.is_file()}=={'script.ps1','matrix.json','inputs.zip'},'inbox inventory')
 req((inbox/'script.ps1').read_bytes()==(HERE/'larger_graph_creation.ps1').read_bytes() and (inbox/'matrix.json').read_bytes()==a.matrix.read_bytes() and (inbox/'inputs.zip').read_bytes()==a.bundle.read_bytes(),'submitted bytes')
 workers=json.loads((a.outbox/'workers.json').read_text(encoding='utf-8-sig'));req(workers['matrix']==ident(a.matrix) and workers['bundle']==ident(a.bundle) and len(workers['workers'])==len(pairs),'workers inventory')
 prepared={x['name']:{'size':x['size'],'sha256':x['sha256']} for x in prep['files']};obs=[]
 with zipfile.ZipFile(a.bundle) as z:
  req(z.read('matrix.json')==a.matrix.read_bytes(),'bundle matrix')
  expected={'matrix.json'}|{f'candidates/{graphs[c]["name"]}-r{r}.mdb' for r,c in pairs};names=[n for n in z.namelist() if not n.endswith('/')]
  req(len(names)==len(set(names)) and set(names)==expected,'ZIP inventory')
  for pos,(replica,case) in enumerate(pairs):
   graph=graphs[case];rp=a.outbox/f'r{replica}-{case}-result.json';w=workers['workers'][pos]
   req((w['replica'],w['case'],w['exit_code'],w['result'])==(replica,case,0,ident(rp)),f'{case}/r{replica} worker linkage')
   result=json.loads(rp.read_text(encoding='utf-8-sig'));req(result['status']=='pass' and result['error'] is None and result['case']==case and result['replica']==replica and result['environment']==PROVIDER and result['matrix']==ident(a.matrix) and result['bundle']==ident(a.bundle),f'{case}/r{replica} result/provider')
   entry=f'candidates/{graph["name"]}-r{replica}.mdb';payload=z.read(entry);source={'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()};req(source==prepared[Path(entry).name]==result['candidate']['source'],f'{case}/r{replica} candidate pin')
   cp=a.outbox/result['candidate']['file'];np=a.outbox/result['native']['file'];req(result['candidate']['capture']['before']==result['candidate']['capture']['after']==ident(cp)==source,f'{case}/r{replica} candidate identity')
   req(result['native']['creation_error'] is None and len(result['native']['relations'])==len(graph['relations']) and all(x['error'] is None for x in result['native']['relations']),f'{case}/r{replica} native creation')
   req(result['native']['capture']['before']==result['native']['capture']['after']==ident(np),f'{case}/r{replica} native identity')
   candidate=structure.evaluate_case(graph,pseudo(result['candidate']['capture'],graph,replica),cp);native=structure.evaluate_case(graph,pseudo(result['native']['capture'],graph,replica),np)
   csi=system_index_inventory.inventory(cp);nsi=system_index_inventory.inventory(np)
   req(structure.normalized_snapshot(candidate['snapshot'])==structure.normalized_snapshot(native['snapshot']),f'{case}/r{replica} DAO snapshot')
   for table in graph['tables']:
    name=table['name'];req(candidate['raw']['tables'][name]['columns']==native['raw']['tables'][name]['columns'],f'{case}/{name} columns')
    req(logical_semantics(candidate['raw'],name)==logical_semantics(native['raw'],name),f'{case}/{name} logical semantics')
    req([x['values'] for x in candidate['raw']['tables'][name]['rows']]==[x['values'] for x in native['raw']['tables'][name]['rows']],f'{case}/{name} raw values')
   for key in ('relationship_rows','relationship_objects','relationship_aces'): req(candidate['raw'][key]==native['raw'][key],f'{case}/{key}')
   validate_counters(candidate['raw'],graph,'candidate');validate_counters(native['raw'],graph,'native')
   obs.append({'case':case,'replica':replica,'environment':result['environment'],'candidate_source':source,'candidate':candidate,'native':native,'candidate_system_indexes':csi,'native_system_indexes':nsi})
 for case in graphs:
  pair=[x for x in obs if x['case']==case];req(len(pair)==2,f'{case} replicas')
  req(structure.normalized_snapshot(pair[0]['candidate']['snapshot'])==structure.normalized_snapshot(pair[1]['candidate']['snapshot']),f'{case} candidate replica snapshot')
  req(structure.normalized_snapshot(pair[0]['native']['snapshot'])==structure.normalized_snapshot(pair[1]['native']['snapshot']),f'{case} native replica snapshot')
 report={'document_type':'larger_relationship_graph_creation_acceptance_report','outcome':'accepted','source_revision':pins['source_revision'],'source_archive':pins['source_archive'],'candidate_binary':pins['binary'],'provider':PROVIDER,'matrix':ident(a.matrix),'bundle':ident(a.bundle),'producer':ident(HERE/'larger_graph_creation.ps1'),'evaluator':ident(Path(__file__)),'pairs':len(obs),'captures':2*len(obs),'observations':obs}
 a.report.write_text(json.dumps(report,sort_keys=True,separators=(',',':'))+'\n');print(a.report);print('accepted',len(obs),'pairs')
if __name__=='__main__':main()
