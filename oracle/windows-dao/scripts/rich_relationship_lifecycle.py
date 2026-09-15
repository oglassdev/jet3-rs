#!/usr/bin/env python3
from __future__ import annotations
import argparse,copy,hashlib,json,zipfile
from pathlib import Path
import rich_relationship_structure as creation
import system_catalog as catalog
import relationship_mutation_structure as structure
import numeric_index_mutation_structure as indexes
import allocation_lifecycle_structure as allocation
import relationship_create as relationships
for m in (catalog,indexes.catalog,allocation.catalog):m.MAX_PAGES=8192;m.MAX_ROWS_PER_PAGE=1019;m.MAX_TABLES=64;m.MAX_COLUMNS=255;m.MAX_TEXT=10000
BASE=RUN_DIR=OUT=PLAN=RUN=REPORT=None
ROLES=('candidate','control','native-rust');STAGES=('original','inserted','changed','ordered-deletes')
def req(x,msg):
 if not x:raise ValueError(msg)
def sha(b):return hashlib.sha256(b).hexdigest()
def ident(p):
 b=p.read_bytes();return {'size':len(b),'sha256':sha(b)}
def fields(case,table):return ([{'name':'Id','type':4,'size':4,'attributes':0,'allow_zero_length':False},{'name':'Label','type':10,'size':32,'attributes':0,'allow_zero_length':False}] if table=='Parent' else case['fields'])
def initial(recipe,case):
 return {'Parent':{1:[1,b'one'.hex()],2:[2,b'two'.hex()],3:[3,b'three'.hex()]},'Child':{r[0]:r.copy() for r in recipe['initial_rows']}}
def apply(model,op):
 rows=model[op['table']];kind=op['kind']
 if kind=='insert':rows[op['row'][0]]=op['row'].copy()
 elif kind=='delete':del rows[op['id']]
 elif kind=='replace':del rows[op['id']];rows[op['row'][0]]=op['row'].copy()
 else:rows[op['id']][op['column']]=op['value']
def expected_dict(rows,specs):return [{f['name']:v for f,v in zip(specs,rows[k])} for k in sorted(rows)]
def raw_normal(row):
 return {k:(v.encode('cp1252').hex() if isinstance(v,str) else v.hex() if isinstance(v,bytes) else v) for k,v in row['values'].items()}
def schema_fields(snapshot,case,table,label):
 req(snapshot[table.lower()]['attributes']==0,label+' table attributes')
 specs=fields(case,table);actual=snapshot[table.lower()]['fields'];req(len(actual)==len(specs),label+' field count')
 for got,want in zip(actual,specs):
  attr=17 if want['name']=='Id' and want.get('attributes')==16 else 1 if want['type']==4 else 2
  req((got['name'],got['type'],got['size'],got['attributes'],got['required'],got['allow_zero_length'])==(want['name'],want['type'],want['size'],attr,False,want['allow_zero_length']),label+' field '+want['name'])
  props={p['name']:p for p in got['properties']};req(props['Required']['value']=='False',label+' required '+want['name'])
  if want['type'] in (10,12):req(props['AllowZeroLength']['value']==str(want['allow_zero_length']),label+' allow '+want['name'])
def maps_check(data,analysis,named,index_nodes,label):
 maps={'global':creation.map_record(data,{'page':1,'row':0},'global')};claimed={};metadata={0,1};free=set(analysis['free_pages'])
 for name,t in named.items():
  d=t['definition']
  for role,loc in d['maps'].items():maps[f'{name}/table/{role}']=creation.map_record(data,loc,f'{name} {role}')
  for ix in d['physical_indexes']:maps[f'{name}/index/{ix["index"]}']=creation.map_record(data,ix['map'],f'{name} index')
  for g in d['long_value_maps']:
   for role in ('owned','available'):maps[f'{name}/lval/{g["column_name"]}/{role}']=creation.map_record(data,g[role],f'{name} lval')
  owned=set(maps[f'{name}/table/owned']['members']);available=set(maps[f'{name}/table/available']['members']);req(available<=owned and not owned&free,label+' table allocation '+name)
  groups=[(f'{name}/table',owned)]
  for ix in d['physical_indexes']:
   members=set(maps[f'{name}/index/{ix["index"]}']['members']);groups.append((f'{name}/index/{ix["index"]}',members));req(not members&free,label+' index allocation')
   if (name,ix['index']) in index_nodes:req(set(index_nodes[name,ix['index']])<=members,label+' index nodes owned')
  for g in d['long_value_maps']:
   k=f'{name}/lval/{g["column_name"]}';members=set(maps[k+'/owned']['members']);req(set(maps[k+'/available']['members'])<=members and not members&free,label+' lval allocation');groups.append((k,members))
  for role,members in groups:
   for p in members:req(p not in claimed,label+f' page {p} multiply owned');claimed[p]=role
  metadata.update(d['pages'])
 locs=[(v['locator']['page'],v['locator']['row']) for v in maps.values()];refs=[p for v in maps.values() for p in v['record']['references'] if p]
 req(len(locs)==len(set(locs)),label+' distinct map rows');req(len(refs)==len(set(refs)),label+' distinct map reference pages')
 for v in maps.values():metadata.add(v['locator']['page']);metadata.update(p for p in v['record']['references'] if p)
 req(not metadata&free and not metadata&set(claimed),label+' metadata separation')
 req(set(maps['global']['members'])==free,label+' global free map')
 return maps,{'metadata_pages':sorted(metadata),'owned_pages':sorted(claimed),'free_pages':sorted(free)}
def payload_membership(data,named,rrows,allocation_state,label):
 free=set(allocation_state['free_pages']);metadata=set(allocation_state['metadata_pages']);result={}
 for tn in ('Parent','Child'):
  d=named[tn]['definition']
  for g in d['long_value_maps']:
   col=g['column_name'];owned=set(catalog._locator_pages(data,g['owned'],label+' '+tn+' '+col+' owned'));reached=set()
   for row in rrows[tn]:
    desc=row['descriptors'].get(col)
    if desc is None:continue
    local=set();payload,_=structure.payload(data,bytes.fromhex(desc['raw_hex']),owned,local);req(payload==row['values'][col],label+' descriptor payload '+tn+'/'+col);reached.update(local)
    req(all(p in owned and p not in free and p not in metadata for p,_ in local),label+' descriptor ownership '+tn+'/'+col)
   active=set()
   for p in owned:
    image=catalog._page(data,p,label+' payload membership')
    for e in catalog._row_directory(image,p):
     if not e['hidden']:active.add((p,e['row']))
   req(reached==active,label+' complete active payload membership '+tn+'/'+col)
   result[f'{tn}/{col}']={'owned_pages':sorted(owned),'reached_slots':[{'page':p,'row':r} for p,r in sorted(reached)]}
 return result
def observe(path,capture,case,model,label,role):
 data=path.read_bytes();req(capture['before']==capture['after']==ident(path),label+' identity');snap=capture['snapshot'];req(set(snap['tables'])=={'Parent','Child','MSysObjects','MSysACEs','MSysQueries','MSysRelationships'},label+' tables')
 req(snap['relations']==[{'name':'ParentChild','table':'Parent','foreign_table':'Child','attributes':0,'fields':[{'name':'Id','foreign_name':'ParentId'}]}],label+' relation DAO')
 schema_fields(snap,case,'Parent',label);schema_fields(snap,case,'Child',label)
 expected={t:expected_dict(model[t],fields(case,t)) for t in ('Parent','Child')}
 req({r['Id']:r for r in snap['parent']['rows']}=={r['Id']:r for r in expected['Parent']},label+' DAO Parent rows');req({r['Id']:r for r in snap['child']['rows']}=={r['Id']:r for r in expected['Child']},label+' DAO Child rows')
 expect_indexes={'parent':[('ByParent',True,True,True,False,False,[{'name':'Id','attributes':0}])],'child':[('ByChild',True,True,True,False,False,[{'name':'Id','attributes':0}]),('ParentChild',False,False,False,True,False,[{'name':'ParentId','attributes':0}])]}
 for key in ('parent','child'):
  got=snap[key]['indexes'];req([(x['name'],x['primary'],x['unique'],x['required'],x['foreign'],x['ignore_nulls'],x['fields']) for x in got]==expect_indexes[key],label+' DAO indexes '+key)
 analysis=catalog.analyze_checkpoint(data);named={t['name']:t for t in analysis['tables'].values()};req(set(('Parent','Child','MSysRelationships'))<=set(named),label+' raw tables')
 rrows={t:structure.rows(data,named[t]) for t in ('Parent','Child')};req([raw_normal(r) for r in rrows['Parent']]==expected['Parent'],label+' raw Parent rows');req([raw_normal(r) for r in rrows['Child']]==expected['Child'],label+' raw Child rows')
 physical={};nodeset={}
 for tn,defs in [('Parent',[('ByParent','Id',0)]),('Child',[('ByChild','Id',0),('ParentChild','ParentId',1)])]:
  t=named[tn];req(len(t['definition']['physical_indexes'])==len(defs),label+' physical count '+tn);byloc={(r['locator']['page'],r['locator']['row']):r['values']['Id'] for r in rrows[tn]};items=[]
  for ixname,col,ordinal in defs:
   ix=t['definition']['physical_indexes'][ordinal];req(ix['keys']==[{'column':next(c['ordinal'] for c in t['definition']['columns'] if c['name']==col),'direction':1}],label+' physical key '+ixname)
   nodes,entries=indexes.tree(data,ix['root'],t['definition']['root'],[(4,False)]);wanted=sorted(structure.long_key(r['values'][col],r['locator']) for r in rrows[tn]);req(entries==wanted,label+' key inventory '+ixname);nodeset[tn,ordinal]=[n['page'] for n in nodes]
   order=[byloc[(int.from_bytes(e[-4:-1],'big'),e[-1])] for e in entries];dao=snap[tn.lower()]['index_reads'][ixname];req([r['Id'] for r in dao['traversal']]==order,label+' traversal order '+ixname)
   complete={r['Id']:r for r in snap[tn.lower()]['rows']};req(all(r==complete[r['Id']] for r in dao['traversal']),label+' complete traversal fields '+ixname)
   expected_queries=sorted({r['values'][col] for r in rrows[tn] if r['values'][col] is not None})+[999999];req([s['query'] for s in dao['seek']]==expected_queries,label+' complete Seek inventory '+ixname)
   for s in dao['seek']:
    matches=[r for r in rrows[tn] if r['values'][col]==s['query']];req((s['row'] is None)==(not matches),label+' seek presence '+ixname)
    if s['row'] is not None:req(s['row']==complete[s['row']['Id']] and s['row']['Id'] in {r['values']['Id'] for r in matches},label+' seek full row '+ixname)
   items.append({'name':ixname,'root':ix['root'],'flags':ix['flags'],'first_word':int.from_bytes(bytes.fromhex(ix['prefix_hex']),'little'),'second_word':ix['entry_count'],'nodes':nodes,'entries_hex':[e.hex() for e in entries]})
  physical[tn]=items
 req([x['flags'] for x in physical['Parent']]==[9] and [x['flags'] for x in physical['Child']]==[9,0],label+' physical flags')
 relrows=catalog._generic_rows(analysis['system_rows']['MSysRelationships'],analysis);req(relrows==[{'szRelationship':'ParentChild','grbit':0,'ccolumn':1,'icolumn':0,'szObject':'Child','szColumn':'ParentId','szReferencedObject':'Parent','szReferencedColumn':'Id'}],label+' relation catalog')
 relrec={}
 for tn,side,other in [('Parent',1,'Child'),('Child',2,'Parent')]:
  es=[x for x in named[tn]['definition']['logical_indexes'] if x['class']==2];req(len(es)==1,label+' reciprocal '+tn);rr=structure.logical_relation(es[0]['raw_hex']);rr['name']=es[0]['name'];req(rr['side']==side and rr['related_root']==named[other]['definition']['root'] and rr['context_hex']=='0000',label+' relation record '+tn);relrec[tn]=rr
 req(relrec['Parent']['selector']==relrec['Child']['relation_ordinal'] and relrec['Parent']['relation_ordinal']==relrec['Child']['selector'] and relrec['Parent']['physical_index']==0 and relrec['Child']['physical_index']==1,label+' relation reciprocity')
 req(relrec['Parent']['name']=='.rB' and relrec['Child']['name']=='ParentChild' and relrec['Parent']['selector']==relrec['Parent']['relation_ordinal']==1,label+' relation names/selectors')
 sr=named['MSysRelationships'];rawsr=analysis['system_rows']['MSysRelationships']['rows'];req(len(rawsr)==1,label+' one relation row');sysidx=[]
 for ix in sr['definition']['physical_indexes']:
  leaf=relationships.leaf_entries(data,ix['root']);req(len(leaf['entries'])==1,label+' system relation key');e=leaf['entries'][0];req((e['row_page'],e['row'])==(rawsr[0]['page'],rawsr[0]['row']),label+' system relation key locator');sysidx.append({'index':ix['index'],'flags':ix['flags'],'first_word':int.from_bytes(bytes.fromhex(ix['prefix_hex']),'little'),'second_word':ix['entry_count'],'entry':e})
 props={}
 for tn in ('Parent','Child'):
  p=creation.find_properties(data,tn);specs=fields(case,tn);has_option=any(f['allow_zero_length'] for f in specs)
  if p is None:req(role=='candidate' and not has_option,label+' missing properties '+tn);props[tn]=None
  else:
   wanted=creation.expected_properties(specs);req(p['payload']==wanted,label+' property bytes '+tn);props[tn]={'length':len(p['payload']),'sha256':sha(p['payload']),'descriptor':p['descriptor'],'fragments':p['fragments']}
 maps,allocation_state=maps_check(data,analysis,named,nodeset,label);payload_maps=payload_membership(data,named,rrows,allocation_state,label)
 child_map_count=sum(k.startswith('Child/') for k in maps);req(child_map_count==(20 if case['name']=='boundary' else 8),label+' child map count')
 req(len(named['Child']['definition']['long_value_maps'])==(8 if case['name']=='boundary' else 2),label+' LVAL inventory')
 req(len(named['Child']['definition']['pages'])==(2 if case['name']=='boundary' else 1),label+' definition chain')
 if case['name']=='boundary':req(props['Child'] is not None and len(props['Child']['fragments'])==2,label+' property chain')
 parent_pages=set(named['Parent']['definition']['pages']+named['Parent']['data_pages']);parent_pages.update(n['page'] for n in physical['Parent'][0]['nodes'])
 return {'file':path.name,'identity':ident(path),'page_count':len(data)//2048,'page0_counter':data[1538],'rows':expected,'raw_rows':{t:[{'locator':r['locator'],'storage':r['storage'],'raw_hex':r['raw_hex'],'descriptors':r['descriptors']} for r in rrows[t]] for t in rrows},
  'dao_schema':{t:{k:snap[t][k] for k in ('name','attributes','fields','indexes')} for t in ('parent','child')},'raw_columns':{t:named[t]['definition']['columns'] for t in ('Parent','Child')},'definition_pages':{t:named[t]['definition']['pages'] for t in ('Parent','Child')},'indexes':physical,'relation_records':relrec,'relationship_system_indexes':sysidx,'properties':props,
  'maps':maps,'allocation_state':allocation_state,'payload_membership':payload_maps,'free_pages':analysis['free_pages'],'parent_page_hashes':{str(p):sha(catalog._page(data,p,'parent')) for p in sorted(parent_pages)}}
def byte_diffs(before,after):
 b=before.read_bytes();a=after.read_bytes();req(len(a)==len(b),'refusal size changed');offsets=[i for i,(x,y) in enumerate(zip(b,a)) if x!=y];return {'count':len(offsets),'pages':sorted({i//2048 for i in offsets}),'bytes':[{'offset':i,'before':b[i],'after':a[i]} for i in offsets]}
def validate_inventory():
 archive={}
 archive['matrix.json']=BASE/'matrix.json'
 for arm in ('plain','rich','boundary'):archive[f'recipe-{arm}.json']=BASE/f'recipe-{arm}.json'
 for arm in ('plain','rich','boundary'):
  for rep in (1,2):
   stem=f'{arm}-r{rep}';archive[f'inputs/{stem}.mdb']=BASE/'inputs'/f'{stem}.mdb';archive[f'inputs/native/{stem}.mdb']=BASE/'inputs/native'/f'{stem}.mdb'
   recipe=json.loads((BASE/f'recipe-{arm}.json').read_text())
   for role in ('candidate','native-rust'):
    directory=BASE/'local'/f'{role}-{stem}'
    expected={*[f'{s}.mdb' for s in STAGES],*[f'{s}.snapshot.json' for s in STAGES],*[f"refusal-{r['name']}.mdb" for r in recipe['refusals']],'refusals.json'}
    req({p.name for p in directory.iterdir()}==expected,'local output inventory '+role+'/'+stem)
    for stage in STAGES:archive[f'local/{role}-{stem}/{stage}.mdb']=directory/f'{stage}.mdb'
    for refusal in recipe['refusals']:archive[f"local/{role}-{stem}/refusal-{refusal['name']}.mdb"]=directory/f"refusal-{refusal['name']}.mdb"
 with zipfile.ZipFile(BASE/'acceptance-input.zip') as z:
  req(z.testzip() is None,'input archive CRC');req(set(z.namelist())==set(archive),'input archive inventory')
  for name,path in archive.items():req(z.read(name)==path.read_bytes(),'input archive bytes '+name)
 expected_out={'exit.txt','log.txt'}
 for arm in ('plain','rich','boundary'):
  recipe=json.loads((BASE/f'recipe-{arm}.json').read_text())
  for rep in (1,2):
   stem=f'{arm}-r{rep}';expected_out.add(stem+'.json')
   for stage in STAGES:
    for role in ROLES:expected_out.add(f'{stem}-{stage}-{role}.mdb')
   for refusal in recipe['refusals']:
    for role in ('candidate','control'):expected_out.add(f"{stem}-refusal-{refusal['name']}-{role}.mdb")
   for role in ROLES:expected_out.add(f'{stem}-native-next-{role}.mdb')
 req({p.name for p in OUT.iterdir()}==expected_out,'retained outbox inventory');req((OUT/'exit.txt').read_text().strip()=='0' and (OUT/'log.txt').read_bytes()==b'','retained process outcome')
 req({p.name for p in (RUN_DIR/'inbox').iterdir()}=={'script.ps1','acceptance-input.zip'},'retained inbox inventory')
 req((RUN_DIR/'inbox/script.ps1').read_bytes()==(BASE/'acceptance.ps1').read_bytes() and (RUN_DIR/'inbox/acceptance-input.zip').read_bytes()==(BASE/'acceptance-input.zip').read_bytes(),'retained inbox bytes')
 return {'archive_entries':len(archive),'local_files':144,'outbox_files':len(expected_out)}
def assert_prefix_history(per,native,label):
 parent=[(0,3),(0,4),(0,4),(0,4)];primary=[(0,5),(0,7),(0,7),(0,7)]
 foreign={'candidate':[(0,4)]*4,'control':[(5,4),(5,4),(3,3),(1,1)],'native-rust':[(5,4),(5,4),(3,3),(1,1)]}
 for role in ROLES:
  for i,stage in enumerate(STAGES):
   obs=per[stage][role];req((obs['indexes']['Parent'][0]['first_word'],obs['indexes']['Parent'][0]['second_word'])==parent[i],label+' Parent prefix '+role+'/'+stage);req((obs['indexes']['Child'][0]['first_word'],obs['indexes']['Child'][0]['second_word'])==primary[i],label+' primary prefix '+role+'/'+stage);req((obs['indexes']['Child'][1]['first_word'],obs['indexes']['Child'][1]['second_word'])==foreign[role][i],label+' foreign prefix '+role+'/'+stage)
  obs=native[role];req((obs['indexes']['Parent'][0]['first_word'],obs['indexes']['Parent'][0]['second_word'])==(0,4),label+' Parent prefix '+role+'/native');req((obs['indexes']['Child'][0]['first_word'],obs['indexes']['Child'][0]['second_word'])==(0,7),label+' primary prefix '+role+'/native');req((obs['indexes']['Child'][1]['first_word'],obs['indexes']['Child'][1]['second_word'])==((0,4) if role=='candidate' else (0,0)),label+' foreign prefix '+role+'/native')
def main():
 inventory=validate_inventory();observations=[];refusals=[];errors=[]
 for case in PLAN['arms']:
  recipe=json.loads((BASE/f"recipe-{case['name']}.json").read_text())
  for rep in (1,2):
   stem=f"{case['name']}-r{rep}";receipt=json.loads((OUT/f'{stem}.json').read_text());model=initial(recipe,case);per={}
   try:
    req(receipt['status']=='pass' and receipt['error'] is None,stem+' worker')
    req((receipt['case'],receipt['replica'])==(case['name'],rep),stem+' worker identity')
    environment=receipt['environment']
    provider={'provider':'DAO.DBEngine.36','version':'3.6','bits':32,'culture':'en-US','dll_version':'03.60.9765.0','dll_sha256':'4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac'}
    req(all(environment.get(k)==v for k,v in provider.items()),stem+' recorded provider')
    req(receipt['matrix_sha256']==ident(BASE/'matrix.json')['sha256'] and receipt['recipe_sha256']==ident(BASE/f"recipe-{case['name']}.json")['sha256'],stem+' input pins')
    req(len(receipt['stages'])==len(recipe['stages'])==4 and [x['name'] for x in receipt['stages']]==list(STAGES),stem+' exact stage inventory')
    req(len(receipt['refusals'])==len(recipe['refusals'])==3 and len(receipt['native'])==3,stem+' refusal/native inventory lengths')
    for stage,record in zip(recipe['stages'],receipt['stages']):
     req(stage['name']==record['name'],stem+' stage order')
     req(set(record['roles'])==set(ROLES),stem+' stage role inventory '+stage['name'])
     if stage['operations']:
      req(len(record['control_operations'])==len(stage['operations']) and [x['request'] for x in record['control_operations']]==stage['operations'] and all(x['status']=='success' for x in record['control_operations']),stem+' control operation inventory '+stage['name'])
     else:req(record['control_operations'] is None,stem+' original operation inventory')
     for op in stage['operations']:apply(model,op)
     per[stage['name']]={}
     for role in ROLES:
      cap=record['roles'][role];path=OUT/cap['file']
      source=BASE/'inputs'/f'{stem}.mdb' if role=='candidate' else BASE/'inputs/native'/f'{stem}.mdb'
      if stage['name']=='original':req(cap['before']==ident(source),stem+' original source '+role)
      if role!='control':req(cap['before']==ident(BASE/'local'/f'{role}-{stem}'/f"{stage['name']}.mdb"),stem+' Rust stage source '+role)
      per[stage['name']][role]=observe(path,cap,case,model,f'{stem}/{stage["name"]}/{role}',role)
     for table in ('Parent','Child'):
      req(per[stage['name']]['candidate']['raw_columns'][table]==per[stage['name']]['control']['raw_columns'][table]==per[stage['name']]['native-rust']['raw_columns'][table],stem+' cross-role raw columns '+stage['name']+'/'+table)
     req(per[stage['name']]['candidate']['relation_records']==per[stage['name']]['control']['relation_records']==per[stage['name']]['native-rust']['relation_records'],stem+' cross-role relation records '+stage['name'])
    for role in ROLES:req(per['inserted'][role]['parent_page_hashes']==per['changed'][role]['parent_page_hashes'],stem+' child-only stage changed parent pages '+role)
    nmodel=copy.deepcopy(model)
    for op in recipe['native']:apply(nmodel,op)
    native={}
    req([x['role'] for x in receipt['native']]==list(ROLES),stem+' native role inventory')
    for rec in receipt['native']:
     role=rec['role'];native[role]=observe(OUT/rec['capture']['file'],rec['capture'],case,nmodel,f'{stem}/native/{role}',role)
     req(len(rec['operations'])==len(recipe['native']) and [x['request'] for x in rec['operations']]==recipe['native'] and [x['status'] for x in rec['operations']]==['success']*len(recipe['native']),stem+' native ops '+role)
    assert_prefix_history(per,native,stem)
    for role in ROLES:
     for observed in [per[stage][role] for stage in STAGES]+[native[role]]:
      req(observed['dao_schema']==per['original']['control']['dao_schema'],stem+' complete DAO schema '+role)
    for role in ROLES:
     original=per['original'][role]
     for stage in STAGES[1:]:
      current=per[stage][role];req(current['raw_columns']==original['raw_columns'] and current['relation_records']==original['relation_records'] and current['relationship_system_indexes']==original['relationship_system_indexes'] and current['properties']==original['properties'],stem+' metadata stability '+role+'/'+stage)
     req(native[role]['raw_columns']==original['raw_columns'] and native[role]['relation_records']==original['relation_records'] and native[role]['relationship_system_indexes']==original['relationship_system_indexes'] and native[role]['properties']==original['properties'],stem+' metadata stability '+role+'/native')
    # Rust refusals are required to preserve the complete input image.
    for role,source in [('candidate',BASE/'inputs'/f'{stem}.mdb'),('native-rust',BASE/'inputs/native'/f'{stem}.mdb')]:
     d=BASE/'local'/f'{role}-{stem}';rr=json.loads((d/'refusals.json').read_text());req([x['name'] for x in rr]==[x['name'] for x in recipe['refusals']] and all(x['preserved'] for x in rr),stem+' Rust refusals '+role)
     for request in recipe['refusals']:req((d/f"refusal-{request['name']}.mdb").read_bytes()==source.read_bytes(),stem+' Rust refusal bytes '+role)
    for request,rec in zip(recipe['refusals'],receipt['refusals']):
     req((request['name'],request['number'])==(rec['name'],rec['expected_number']),stem+' refusal inventory')
     req(set(rec['roles'])=={'candidate','control'},stem+' refusal role inventory '+request['name'])
     for role,source in [('candidate',BASE/'inputs'/f'{stem}.mdb'),('control',BASE/'inputs/native'/f'{stem}.mdb')]:
      item=rec['roles'][role];op=item['operation'];req(op['status']=='rejected' and op['error']['numbers']==[request['number']],stem+' refusal error '+role);cap=item['capture'];obs=observe(OUT/cap['file'],cap,case,initial(recipe,case),f'{stem}/refusal/{request["name"]}/{role}',role)
      req(op['request']==request['operation'],stem+' refusal request')
      req(op['before']==ident(source) and op['after']==obs['identity'],stem+' refusal identities')
      baseline=per['original'][role]
      for key in ('dao_schema','raw_columns','relation_records','relationship_system_indexes','properties','maps','allocation_state','free_pages','raw_rows','payload_membership'):
       req(obs[key]==baseline[key],stem+' refusal preserves '+role+'/'+key)
      for table in ('Parent','Child'):
       req([x['entries_hex'] for x in obs['indexes'][table]]==[x['entries_hex'] for x in baseline['indexes'][table]],stem+' refusal keys '+role+'/'+table)
      expected_primary=(0,6) if request['name']=='orphan-insert' else (0,5);expected_foreign=({'candidate':(0,4),'control':(5,4)}[role] if request['name']!='orphan-update' else {'candidate':(0,4),'control':(4,4)}[role])
      req((obs['indexes']['Parent'][0]['first_word'],obs['indexes']['Parent'][0]['second_word'])==(0,3),stem+' refusal Parent prefix');req((obs['indexes']['Child'][0]['first_word'],obs['indexes']['Child'][0]['second_word'])==expected_primary,stem+' refusal primary prefix');req((obs['indexes']['Child'][1]['first_word'],obs['indexes']['Child'][1]['second_word'])==expected_foreign,stem+' refusal foreign prefix')
      refusals.append({'case':case['name'],'replica':rep,'name':request['name'],'role':role,'identity':obs['identity'],'page0_counter':obs['page0_counter'],'prefix_words':{t:[[i['first_word'],i['second_word']] for i in obs['indexes'][t]] for t in ('Parent','Child')},'diff':byte_diffs(source,OUT/cap['file'])})
    observations.append({'case':case['name'],'replica':rep,'stages':per,'native':native})
   except Exception as e:errors.append({'case':case['name'],'replica':rep,'error':f'{type(e).__name__}: {e}'})
 report={'document_type':'rich_relationship_creation_lifecycle_acceptance','run_id':RUN,'source_revision':json.loads((BASE/'build-identity.json').read_text())['head'],'inventory':inventory,'status':'accepted' if not errors and len(observations)==6 and len(refusals)==36 else 'rejected','errors':errors,'observations':observations,'refusals':refusals}
 with REPORT.open('x') as output:output.write(json.dumps(report,sort_keys=True,separators=(',',':'))+'\n')
 print(json.dumps({'status':report['status'],'errors':errors,'observations':len(observations),'refusals':len(refusals)},indent=2));return 0 if report['status']=='accepted' else 1
if __name__=='__main__':
 parser=argparse.ArgumentParser(description='Compare rich relationship creation and lifecycle captures with their complete expected model.')
 parser.add_argument('directory',type=Path)
 parser.add_argument('retained_run',type=Path,help='Directory containing the retained inbox and outbox')
 parser.add_argument('report',type=Path,help='New report path; existing reports are preserved')
 args=parser.parse_args()
 BASE=args.directory.resolve();RUN_DIR=args.retained_run.resolve();OUT=RUN_DIR/'outbox';RUN=RUN_DIR.name;REPORT=args.report.resolve()
 PLAN=json.loads((BASE/'matrix.json').read_text())
 raise SystemExit(main())
